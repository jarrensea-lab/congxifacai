# 易淘金账户真值、自选股与行情桥接实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**日期：** 2026-07-26

**状态：** 待执行

**对应设计：** `docs/superpowers/specs/2026-07-26-profit-evidence-yitaojin-execution-loop-design.md`

**本计划范围：** 第一阶段，只实现收益反馈真值修复、易淘金账户读取、自选股安全同步、有限行情校验和调度接线；可信历史回测、策略影子晋级和真实成交归因另立计划。

## 目标

把广发易淘金 Mac 客户端变成项目的“账户事实源 + 自选股投影层 + 有限行情校验端”，同时保持以下硬边界：

1. 不自动下单、撤单、清仓、转账。
2. 不保存或填写密码、验证码、完整账号、手机号、股东账号。
3. 不修改易淘金内部数据库，不逆向私有交易接口。
4. 账户、候选池或界面结构不可信时失败关闭。
5. 自选股只删除系统自己添加、已连续两次确认退出且不再持仓的股票。
6. 所有真实 UI 写操作默认禁用；必须先通过脱敏测试、`dry-run`、只新增三次和人工验收。

## 架构

采用两层桥接：

- Swift CLI 使用 macOS Accessibility API 读取和操作 `/Applications/GF-Trader.app` 的正常界面；命令采用 JSON stdin/stdout，内部使用显式命令白名单和禁止交易区域规则。
- Python 服务负责领域规则、账户快照校验、候选池映射、所有权账本、审计、行情新鲜度判断和 APScheduler 调度。

Swift 层不理解策略，不读取项目文件；Python 层不依赖屏幕坐标，不输入敏感凭证。所有运行状态保存在 `CONGXI_STATE_DIR`，默认位于：

```text
~/Library/Application Support/congxicai-v7/
```

## 关键数据流

```text
易淘金可访问性树
  -> Swift 安全桥
  -> Python 结构化账户快照
  -> 账户指纹/完整性/差异校验
  -> 临时 broker snapshot
  -> portfolio_store 事务锁
  -> 项目持仓真值

Candidate Pool(executable/watching) + broker holdings
  -> 纯自选股规划器
  -> manual_protected / managed_codes 所有权账本
  -> dry-run 差异
  -> 只新增三次
  -> 双确认受控移除
  -> 易淘金自选股

holdings + executable + watching
  -> 易淘金有限行情
  -> 新鲜度与多源偏差检查
  -> 报告/生命周期的行情可信度门
```

## 运行时文件

以下文件全部位于运行状态目录，不进入 Git：

```text
yitaojin/account_snapshot.json
yitaojin/account_fingerprint_salt
yitaojin/account_sync_audit.jsonl
yitaojin/watchlist_state.json
yitaojin/watchlist_sync_audit.jsonl
yitaojin/quote_snapshot.json
bin/yitaojin-bridge
```

状态环境变量：

```text
CONGXI_YITAOJIN_ENABLED=false
CONGXI_YITAOJIN_WRITE_ENABLED=false
CONGXI_YITAOJIN_BRIDGE_PATH=${CONGXI_STATE_DIR}/bin/yitaojin-bridge
CONGXI_YITAOJIN_ACCOUNT_SNAPSHOT_PATH=${CONGXI_STATE_DIR}/yitaojin/account_snapshot.json
CONGXI_YITAOJIN_ACCOUNT_FINGERPRINT_SALT_PATH=${CONGXI_STATE_DIR}/yitaojin/account_fingerprint_salt
CONGXI_YITAOJIN_WATCHLIST_STATE_PATH=${CONGXI_STATE_DIR}/yitaojin/watchlist_state.json
CONGXI_YITAOJIN_QUOTE_SNAPSHOT_PATH=${CONGXI_STATE_DIR}/yitaojin/quote_snapshot.json
CONGXI_YITAOJIN_QUOTE_MAX_AGE_SECONDS=90
CONGXI_YITAOJIN_ACTION_QUOTE_MAX_AGE_SECONDS=30
CONGXI_YITAOJIN_MAX_PRICE_DIVERGENCE_PCT=0.5
```

默认值必须保持两个 enable 开关均为 `false`。不能因为安装脚本运行成功就自动开启。

## 公共 Python 接口

在 `backend/app/integrations/yitaojin/` 下提供：

```python
class BridgeCommand(StrEnum):
    PROBE = "probe"
    READ_ACCOUNT = "read_account"
    READ_WATCHLIST = "read_watchlist"
    READ_QUOTES = "read_quotes"
    ADD_WATCHLIST = "add_watchlist"
    REMOVE_WATCHLIST = "remove_watchlist"


@dataclass(frozen=True)
class BrokerPosition:
    code: str
    name: str
    shares: int
    available_shares: int
    average_cost: Decimal
    current_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal


@dataclass(frozen=True)
class AccountSnapshot:
    captured_at: datetime
    account_fingerprint: str
    total_assets: Decimal
    available_cash: Decimal
    frozen_cash: Decimal
    positions: tuple[BrokerPosition, ...]
    source: str = "yitaojin_ui"


@dataclass(frozen=True)
class QuoteSnapshot:
    code: str
    captured_at: datetime
    market_time: datetime
    price: Decimal
    change_pct: Decimal | None
    volume: Decimal | None
    amount: Decimal | None
    high: Decimal | None
    low: Decimal | None
    previous_close: Decimal | None
    status: str


@dataclass(frozen=True)
class WatchlistPlan:
    add: tuple[str, ...]
    remove: tuple[str, ...]
    keep: tuple[str, ...]
    blocked: tuple[str, ...]
    reasons: tuple[str, ...]
    desired_codes: tuple[str, ...]
    current_codes: tuple[str, ...]
    managed_codes: tuple[str, ...]
    manual_protected_codes: tuple[str, ...]
```

关键函数：

```python
def build_desired_codes(
    pool_payload: Mapping[str, object],
    positions: Sequence[BrokerPosition],
) -> set[str]: ...


def plan_watchlist_sync(
    *,
    desired_codes: set[str],
    current_codes: set[str],
    held_codes: set[str],
    state: WatchlistState,
    allow_removals: bool,
    source_valid: bool,
) -> WatchlistPlan: ...


class YitaojinBridge:
    def run(
        self,
        command: BridgeCommand,
        payload: Mapping[str, object] | None = None,
        *,
        timeout: float = 15.0,
    ) -> Mapping[str, object]: ...


class YitaojinSyncService:
    def read_account(self) -> AccountReadResult: ...
    def sync_account(self, *, apply: bool = False) -> AccountSyncResult: ...
    def read_quotes(self, codes: Sequence[str]) -> QuoteReadResult: ...
    def plan_watchlist(self) -> WatchlistPlan: ...
    def sync_watchlist(self, *, apply: bool = False) -> WatchlistSyncResult: ...
```

账户指纹规则：

- Python 首次 bootstrap 时使用 `secrets.token_bytes(32)` 生成本地随机盐，文件权限固定为 `0600`。
- Python 把 base64 盐作为 `read_account` 的一次性请求参数传入 Swift。
- Swift 在进程内对“稳定账户材料 + 盐”计算 SHA-256，只返回 `sha256:<digest>`。
- 原始账户材料不能出现在 stdout、stderr、日志、fixture、状态文件或审计中。
- 盐丢失后必须重新人工绑定；不能用完整账号恢复。

## 状态约束

`watchlist_state.json` 使用版本化结构：

```json
{
  "schema_version": 1,
  "account_fingerprint": "sha256:...",
  "manual_protected_codes": ["000001"],
  "managed_codes": ["600000"],
  "pending_removal_counts": {"600000": 1},
  "successful_apply_count": 0,
  "last_success_at": null,
  "last_snapshot_hash": null
}
```

规则：

- 首次 bootstrap 时，当前自选股全部写入 `manual_protected_codes`。
- 后续发现的非 `managed_codes` 股票也自动保护，视为用户手工添加。
- `managed_codes` 只在单股添加后再次读取并确认存在时写入。
- 前三次成功应用周期只新增，不移除。
- 移除必须同时满足：由系统管理、已不在目标集合、当前不持仓、连续两次确认、所有输入可信。
- 本轮任何新增失败，则整轮跳过移除阶段。
- 单股移除失败时保留 `managed_codes`，供下次重试。
- 重复执行相同输入必须幂等。

---

## Task 0：建立隔离实施环境并锁定基线

**目的：** 避开当前工作区中已有的历史行情接入改动，确保新功能的提交只包含本计划文件。

**文件：**

- 不修改代码文件。
- 创建独立 worktree，分支建议：`codex/yitaojin-safe-bridge`。

**步骤：**

- [ ] 读取并遵循 `superpowers:using-git-worktrees`。
- [ ] 从当前分支已提交的 `08c1e0b` 或其后仅含计划文档的提交创建隔离 worktree。
- [ ] 验证原工作区的九个脏文件没有出现在新分支 diff 中。
- [ ] 记录基线测试结果，不把与本计划无关的既有失败当成本轮修改。

**命令：**

```bash
git status --short
git log -3 --oneline
git worktree list
./.venv/bin/python -m pytest \
  backend/tests/test_runtime_database_paths.py \
  backend/tests/test_runtime_regressions.py -q
```

**验收：**

- 新 worktree `git status --short` 为空。
- 原工作区的历史数据改动保持原样。
- 基线测试输出被记录到实施日志。

---

## Task 1：修复复盘收益失败时写入假零值的问题

**目的：** 在接入新账户真值之前，先阻止现有复盘任务把行情抓取异常伪装成 0% 收益并标记完成。

**文件：**

- 修改：`backend/app/engine/debate_tracker.py`
- 修改：`backend/app/main.py`
- 新建：`backend/tests/test_debate_tracker.py`

### 1.1 先写失败测试

- [ ] 测试行情成功时正确计算多股票平均 5 日和 20 日收益。
- [ ] 测试任一批次行情失败时返回“未知”，不写 `return_5d`、`return_20d`、`result_filled_at` 和 `direction_correct`。
- [ ] 测试所有必需区间都成功后才把记录计入 `filled`。
- [ ] 测试异步方法在已有事件循环中执行，不创建嵌套事件循环。

测试骨架：

```python
@pytest.mark.asyncio
async def test_fill_pending_keeps_result_unfilled_when_market_fetch_fails(
    monkeypatch,
    db_session,
):
    async def fail_fetch_batch(self, codes):
        raise RuntimeError("quote unavailable")

    monkeypatch.setattr(TencentDataSource, "fetch_batch", fail_fetch_batch)

    filled = await DebateTracker.fill_pending(db_session)
    db_session.refresh(record)

    assert filled == 0
    assert record.return_5d is None
    assert record.return_20d is None
    assert record.direction_correct is None
    assert record.result_filled_at is None
```

运行并确认红灯：

```bash
./.venv/bin/python -m pytest backend/tests/test_debate_tracker.py -q
```

### 1.2 最小实现

- [ ] 将 `DebateTracker.fill_pending(db)` 改为 `async def`。
- [ ] 将 `_fetch_avg_return` 改为异步，并返回 `float | None`。
- [ ] 删除 `new_event_loop()` 和 `run_until_complete()`。
- [ ] 不在异常时返回 `0.0`；异常记录日志后返回 `None`。
- [ ] 只有 5 日和 20 日两个收益都不是 `None` 时才更新数据库字段。
- [ ] 修改 `backend/app/main.py::_run_review_with_status()` 为 `await DebateTracker.fill_pending(db)`。
- [ ] 确认调用点没有遗漏。

核心实现形状：

```python
@staticmethod
async def _fetch_avg_return(stock_codes: list[str]) -> float | None:
    try:
        quotes = await TencentDataSource().fetch_batch(stock_codes)
    except Exception:
        logger.exception("获取推荐标的收益失败")
        return None
    returns = [quote.change_pct for quote in quotes if quote.change_pct is not None]
    return sum(returns) / len(returns) if returns else None
```

### 1.3 验证与提交

```bash
./.venv/bin/python -m pytest \
  backend/tests/test_debate_tracker.py \
  backend/tests/test_runtime_regressions.py -q
./.venv/bin/ruff check \
  backend/app/engine/debate_tracker.py \
  backend/app/main.py \
  backend/tests/test_debate_tracker.py
git add backend/app/engine/debate_tracker.py backend/app/main.py backend/tests/test_debate_tracker.py
git commit -m "fix: preserve unknown debate returns on quote failure"
```

**验收：**

- 不再创建嵌套 event loop。
- 行情失败不会产生假 0 收益或假完成状态。
- 成功路径仍能完成复盘填充。

---

## Task 2：建立易淘金运行路径和强类型数据契约

**目的：** 先固定本地状态目录、结构化快照和错误分类，让后续 UI 解析失败不能污染项目真值。

**文件：**

- 修改：`backend/app/config.py`
- 新建：`backend/app/integrations/__init__.py`
- 新建：`backend/app/integrations/yitaojin/__init__.py`
- 新建：`backend/app/integrations/yitaojin/models.py`
- 新建：`backend/tests/test_yitaojin_models.py`
- 修改：`backend/tests/test_runtime_database_paths.py`

### 2.1 先写路径和解析测试

- [ ] 默认路径全部落在 `~/Library/Application Support/congxicai-v7/yitaojin/`。
- [ ] `CONGXI_STATE_DIR` 和各单独路径环境变量可覆盖默认值。
- [ ] `Decimal` 解析拒绝空字符串、`--`、`NaN` 和无限值。
- [ ] 股票代码只接受规范化的 6 位 A 股代码。
- [ ] 持仓数量为负、资产字段缺失、重复持仓代码时拒绝快照。
- [ ] 序列化输出不包含完整账户标识，只包含不可逆指纹。

运行红灯：

```bash
./.venv/bin/python -m pytest \
  backend/tests/test_yitaojin_models.py \
  backend/tests/test_runtime_database_paths.py -q
```

### 2.2 实现配置和模型

- [ ] 在 `backend/app/config.py` 增加 `RuntimeYitaojinPaths`。
- [ ] 路径解析只读取环境变量，不创建目录；目录创建由写入服务负责。
- [ ] `models.py` 实现前述 `BrokerPosition`、`AccountSnapshot`、`QuoteSnapshot`、`WatchlistPlan`。
- [ ] 增加显式异常：

```python
class YitaojinError(RuntimeError): ...
class BridgeUnavailableError(YitaojinError): ...
class AccessibilityPermissionError(YitaojinError): ...
class AppNotLoggedInError(YitaojinError): ...
class AccountMismatchError(YitaojinError): ...
class SnapshotValidationError(YitaojinError): ...
class UnsafeUiTargetError(YitaojinError): ...
```

- [ ] 配置模型包含 `account_fingerprint_salt` 路径；盐由账户服务创建，不由配置模块创建。
- [ ] 账户指纹由 Swift 使用 Python 传入的一次性盐在进程内计算；Python 只接收摘要，审计中不保存原字段或盐。
- [ ] 提供 `from_bridge_payload()`，所有外部字段先校验再构造模型。

路径接口：

```python
@dataclass(frozen=True)
class RuntimeYitaojinPaths:
    bridge: Path
    account_snapshot: Path
    account_fingerprint_salt: Path
    account_audit: Path
    watchlist_state: Path
    watchlist_audit: Path
    quote_snapshot: Path


def resolve_runtime_yitaojin_paths() -> RuntimeYitaojinPaths: ...
```

### 2.3 验证与提交

```bash
./.venv/bin/python -m pytest \
  backend/tests/test_yitaojin_models.py \
  backend/tests/test_runtime_database_paths.py -q
./.venv/bin/ruff check backend/app/config.py backend/app/integrations backend/tests/test_yitaojin_models.py
git add \
  backend/app/config.py \
  backend/app/integrations \
  backend/tests/test_yitaojin_models.py \
  backend/tests/test_runtime_database_paths.py
git commit -m "feat: define yitaojin runtime contracts"
```

**验收：**

- 所有路径可注入临时目录测试。
- 非法或不完整 UI 数据无法构造账户快照。
- 敏感账户字段不进入持久化模型。

---

## Task 3：实现纯自选股规划器和所有权状态账本

**目的：** 不接触真实 App，先证明候选映射、手工保护、持仓优先、三次只新增和双确认移除正确。

**文件：**

- 新建：`backend/app/integrations/yitaojin/planner.py`
- 新建：`backend/app/integrations/yitaojin/state.py`
- 新建：`backend/tests/test_yitaojin_planner.py`
- 新建：`backend/tests/test_yitaojin_state.py`

### 3.1 先写规划器矩阵测试

- [ ] `build_desired_codes` 只接纳 `executable` 和 `watching`，排除 `research_reference`、`removed`、`expired`、`cooldown_after_loss`。
- [ ] 所有真实持仓无条件进入目标集合。
- [ ] 当前自选中非系统管理的股票进入 `manual_protected_codes`。
- [ ] 手工保护与系统候选重合时仍保持手工保护。
- [ ] 前三次成功应用只产生 `add`，不产生 `remove`。
- [ ] 系统管理股票第一次退出只累加计数，不移除。
- [ ] 第二次连续确认退出且不持仓时才进入 `remove`。
- [ ] 股票重新进入池或成为持仓时清空移除计数。
- [ ] 输入无效时 `add` 和 `remove` 均为空，原因进入 `blocked/reasons`。
- [ ] 候选池新鲜度按交易日判断：盘前允许上一已完成交易日的 20:30 产物；收盘后必须是当日产物；中间没有遗漏已完成交易日。
- [ ] 相同输入重复规划结果一致。

新鲜度函数固定为：

```python
def validate_candidate_pool_freshness(
    updated_at: datetime,
    *,
    now: datetime,
) -> PoolFreshness:
    """15:30 前要求不早于上一交易日，15:30 后要求不早于当日；休市日沿用上一交易日。"""
```

该函数使用 `app.utils.trading_calendar.is_trading_day/prev_trading_day`，并以
`Asia/Shanghai` 解释时间；未来时间戳和无法解析的时间戳直接判为无效。

参数化示例：

```python
@pytest.mark.parametrize(
    ("status", "included"),
    [
        ("executable", True),
        ("watching", True),
        ("research_reference", False),
        ("removed", False),
        ("expired", False),
        ("cooldown_after_loss", False),
    ],
)
def test_build_desired_codes_uses_production_statuses_only(status, included):
    ...
```

### 3.2 先写状态存储测试

- [ ] 文件不存在时返回 schema v1 空状态。
- [ ] 使用同目录临时文件、`fsync` 和 `os.replace` 原子写入。
- [ ] 并发写使用 `fcntl.flock`。
- [ ] schema 版本未知、JSON 损坏时抛出异常，不自动覆盖。
- [ ] 审计使用 append-only JSONL，每条包含时间、run id、模式、输入 hash、计划和结果，不含账户明文。

运行红灯：

```bash
./.venv/bin/python -m pytest \
  backend/tests/test_yitaojin_planner.py \
  backend/tests/test_yitaojin_state.py -q
```

### 3.3 最小实现

- [ ] 实现 `WatchlistState`、`YitaojinStateStore` 和 `append_audit`。
- [ ] 代码规范化在单一函数中完成，拒绝静默截断。
- [ ] 纯规划器不读文件、不调用桥接器、不读系统时间。
- [ ] `plan_watchlist_sync` 所需时间、成功次数和有效性均由调用者显式传入。
- [ ] 规划器返回排序后的 tuple，确保审计稳定、便于 diff。

### 3.4 验证与提交

```bash
./.venv/bin/python -m pytest \
  backend/tests/test_yitaojin_planner.py \
  backend/tests/test_yitaojin_state.py -q
./.venv/bin/ruff check \
  backend/app/integrations/yitaojin/planner.py \
  backend/app/integrations/yitaojin/state.py \
  backend/tests/test_yitaojin_planner.py \
  backend/tests/test_yitaojin_state.py
git add \
  backend/app/integrations/yitaojin/planner.py \
  backend/app/integrations/yitaojin/state.py \
  backend/tests/test_yitaojin_planner.py \
  backend/tests/test_yitaojin_state.py
git commit -m "feat: plan safe yitaojin watchlist changes"
```

**验收：**

- 全部安全规则可在无 App 环境下确定性验证。
- 状态损坏只会阻断同步，不会触发删除。

---

## Task 4：实现只读 macOS Accessibility 桥

**目的：** 建立可测试、无坐标盲点、默认只读的 Swift CLI，先完成权限探测、账户、自选股和有限行情读取。

**文件：**

- 新建：`tools/yitaojin-bridge/Package.swift`
- 新建：`tools/yitaojin-bridge/Sources/YitaojinBridge/main.swift`
- 新建：`tools/yitaojin-bridge/Sources/YitaojinBridge/BridgeModels.swift`
- 新建：`tools/yitaojin-bridge/Sources/YitaojinBridge/AXClient.swift`
- 新建：`tools/yitaojin-bridge/Sources/YitaojinBridge/YitaojinReader.swift`
- 新建：`tools/yitaojin-bridge/Sources/YitaojinBridge/SafetyPolicy.swift`
- 新建：`tools/yitaojin-bridge/Tests/YitaojinBridgeTests/BridgeModelsTests.swift`
- 新建：`tools/yitaojin-bridge/Tests/YitaojinBridgeTests/SafetyPolicyTests.swift`
- 新建：`scripts/build-yitaojin-bridge.sh`

### 4.1 先写 Swift 单元测试

- [ ] JSON 请求只接受 `probe`、`read_account`、`read_watchlist`、`read_quotes`。
- [ ] 未知命令返回结构化错误和非零退出码。
- [ ] 输出 envelope 固定包含 `schemaVersion`、`ok`、`command`、`capturedAt`、`data/error`。
- [ ] 安全策略拒绝任何路径中出现委托、买入、卖出、撤单、清仓、转账、银证、密码、验证码等标签。
- [ ] 股票代码列表去重、排序并限制最多 32 个，避免全市场抓取。
- [ ] 解析固定脱敏 AX 节点样本时正确提取账户和行情字段；缺字段返回错误，不补 0。

请求和响应：

```json
{"schemaVersion":1,"command":"read_quotes","payload":{"codes":["000001","600000"]}}
```

```json
{
  "schemaVersion": 1,
  "ok": true,
  "command": "read_quotes",
  "capturedAt": "2026-07-26T09:30:05+08:00",
  "data": {"quotes": []}
}
```

运行红灯：

```bash
./scripts/test-yitaojin-bridge.sh
```

### 4.2 实现只读命令

- [ ] 目标 bundle id 固定为 `cn.com.gf.trader`，并校验实际进程路径是 `/Applications/GF-Trader.app`。
- [ ] `probe` 只报告：App 是否运行、辅助功能权限、登录态是否可判断、界面版本签名；不返回账户明文。
- [ ] 使用 `AXUIElement` 角色、标题、描述和值查找元素；禁止以绝对屏幕坐标作为主定位。
- [ ] `read_account` 只读取资产和持仓区域；遇到空持仓时额外要求总资产、可用资金和“持仓数量为 0”的显式证据，否则返回 `incomplete_snapshot`。
- [ ] `read_watchlist` 只进入自选股页面并返回代码集合，不改变排序和选中状态以外的持久内容。
- [ ] `read_quotes` 只处理请求代码，最多 32 个；逐项返回成功或具体失败原因。
- [ ] `read_account` 接收 Python 传入的一次性 base64 盐，在 Swift 进程内使用 CryptoKit 产生 `sha256:<digest>`；完整账号等稳定材料不能写 stderr/stdout。
- [ ] App 未登录时返回 `not_logged_in`，不尝试输入密码。

安全策略接口：

```swift
enum BridgeCommand: String, Codable {
    case probe
    case readAccount = "read_account"
    case readWatchlist = "read_watchlist"
    case readQuotes = "read_quotes"
}

struct SafetyPolicy {
    static let forbiddenLabels: Set<String> = [
        "委托", "买入", "卖出", "撤单", "清仓",
        "转账", "银证", "密码", "验证码"
    ]

    func assertReadable(path: [AXNodeSummary]) throws
}
```

### 4.3 构建脚本

- [ ] `build-yitaojin-bridge.sh` 使用 `swift build -c release`。
- [ ] 将产物原子复制到 `${CONGXI_STATE_DIR}/bin/yitaojin-bridge`。
- [ ] 不修改系统 TCC 数据库，不执行任何授权自动化。
- [ ] 脚本结束后打印用户需要手工授予“辅助功能”权限的目标二进制路径和验证命令。

### 4.4 验证与提交

```bash
./scripts/test-yitaojin-bridge.sh
./scripts/build-yitaojin-bridge.sh
printf '%s\n' '{"schemaVersion":1,"command":"probe","payload":{}}' \
  | "$HOME/Library/Application Support/congxicai-v7/bin/yitaojin-bridge"
git add tools/yitaojin-bridge scripts/build-yitaojin-bridge.sh
git commit -m "feat: add read-only yitaojin accessibility bridge"
```

**生产闸门：**

- `probe` 的真实 App 验证需要用户亲自授予辅助功能权限。
- 权限未授予不算代码失败，但禁止进入任何真实 UI 读取或写入验收。

**验收：**

- Swift 单测不依赖真实 App。
- 未授权、未登录、界面不完整均返回结构化失败。
- 只读桥没有写命令和交易命令。

---

## Task 5：实现 Python 桥接客户端与账户隔离快照

**目的：** 将 Swift 输出安全转换为项目账户真值；任何异常先保留旧持仓。

**文件：**

- 新建：`backend/app/integrations/yitaojin/bridge.py`
- 新建：`backend/app/integrations/yitaojin/account.py`
- 新建：`backend/tests/fixtures/yitaojin/account_snapshot_valid.json`
- 新建：`backend/tests/fixtures/yitaojin/account_snapshot_incomplete.json`
- 新建：`backend/tests/test_yitaojin_bridge.py`
- 新建：`backend/tests/test_yitaojin_account.py`
- 修改：`backend/app/services/portfolio_store.py`
- 修改：`backend/tests/test_portfolio_state.py`

### 5.1 先写桥接客户端测试

- [ ] 使用临时假可执行文件验证 JSON stdin/stdout。
- [ ] 超时后终止子进程并抛 `BridgeUnavailableError`。
- [ ] 非零退出、空 stdout、非 JSON、schema 不兼容都失败关闭。
- [ ] stderr 只进入脱敏错误摘要，不能进入账户审计。
- [ ] Python 只允许 `BridgeCommand` 枚举，不接收任意字符串命令。

### 5.2 先写账户同步测试

- [ ] `apply=False` 只生成差异和隔离快照，不修改 `data/user_portfolio.json`。
- [ ] 首次绑定只在显式 bootstrap 参数下保存账户指纹。
- [ ] 首次绑定生成随机盐并以 `0600` 原子写入；盐缺失或权限过宽时阻断绑定。
- [ ] 指纹不一致时不覆盖账户快照和项目持仓。
- [ ] 持仓为空但没有显式空仓证据时不覆盖。
- [ ] 总资产或现金字段缺失时不覆盖。
- [ ] 资产相对上一可信快照突变超过默认 20% 时标记 `requires_manual_review`，不自动覆盖。
- [ ] 正常快照通过 `portfolio_transaction_lock` 写入临时 portfolio 文件并调用 `recalculate_portfolio`。
- [ ] `recalculate_portfolio` 把 `frozen_cash` 纳入总资产，旧文件缺字段时按 0 兼容。
- [ ] 写入后保留股票代码、名称、数量、成本价和可用数量；已有持仓继续保留 `trade_history`。
- [ ] 新出现的持仓标记 `trade_history_status="unattributed_broker_position"`，不倒推买入成交。
- [ ] 易淘金已消失的持仓从当前持仓真值移出，但只记录 `recent_account_events` 和 `realized_pnl_pending_codes`，不虚构卖出成交或已实现盈亏。
- [ ] UI 浮盈只存为 broker observation；项目计算盈亏继续由数量、成本和现价决定。
- [ ] 另存 `broker_snapshot.reported_total_assets`，与 `available_cash + frozen_cash + position market value` 的差异超过 1 元时阻断自动写入。
- [ ] 审计明确区分 `observed`、`validated`、`applied`、`blocked`。

运行红灯：

```bash
./.venv/bin/python -m pytest \
  backend/tests/test_yitaojin_bridge.py \
  backend/tests/test_yitaojin_account.py \
  backend/tests/test_portfolio_state.py -q
```

### 5.3 实现桥接与账户服务

- [ ] `YitaojinBridge` 使用 `subprocess.run(..., input=..., capture_output=True, timeout=...)`，不得 `shell=True`。
- [ ] 校验桥路径是普通可执行文件，不接受目录或符号到 App 包内的异常目标。
- [ ] 账户原始输出先写入内存模型，只有验证成功后才原子写 `account_snapshot.json`。
- [ ] 项目 portfolio 更新必须显式传入路径；测试全部使用 `CONGXI_PORTFOLIO_PATH` 临时副本。
- [ ] 资产突变阈值做成配置，默认 20%，不能自动放宽。
- [ ] 账户同步结果返回差异：

```python
@dataclass(frozen=True)
class AccountDiff:
    added_positions: tuple[str, ...]
    removed_positions: tuple[str, ...]
    changed_shares: tuple[PositionChange, ...]
    changed_costs: tuple[PositionChange, ...]
    total_asset_delta: Decimal
    cash_delta: Decimal
```

- [ ] 不读历史成交、不做归因；那部分属于后续独立计划。

### 5.4 验证与提交

```bash
./.venv/bin/python -m pytest \
  backend/tests/test_yitaojin_bridge.py \
  backend/tests/test_yitaojin_account.py \
  backend/tests/test_portfolio_state.py \
  backend/tests/test_runtime_database_paths.py -q
./.venv/bin/ruff check \
  backend/app/integrations/yitaojin \
  backend/app/services/portfolio_store.py \
  backend/tests/test_yitaojin_bridge.py \
  backend/tests/test_yitaojin_account.py
git add \
  backend/app/integrations/yitaojin \
  backend/app/services/portfolio_store.py \
  backend/tests/fixtures/yitaojin \
  backend/tests/test_yitaojin_bridge.py \
  backend/tests/test_yitaojin_account.py \
  backend/tests/test_portfolio_state.py
git commit -m "feat: validate yitaojin account snapshots"
```

**验收：**

- App 异常不会改写项目持仓。
- 测试中不触碰真实 `data/user_portfolio.json`。
- 正常快照产生可审计的账户差异。

---

## Task 6：实现自选股 dry-run、只新增和受控移除

**目的：** 把已验证的纯规划结果接到易淘金正常自选股界面，保持写功能双重开关和交易区域硬阻断。

**文件：**

- 修改：`tools/yitaojin-bridge/Sources/YitaojinBridge/BridgeModels.swift`
- 修改：`tools/yitaojin-bridge/Sources/YitaojinBridge/YitaojinReader.swift`
- 新建：`tools/yitaojin-bridge/Sources/YitaojinBridge/YitaojinWriter.swift`
- 修改：`tools/yitaojin-bridge/Sources/YitaojinBridge/SafetyPolicy.swift`
- 修改：`tools/yitaojin-bridge/Tests/YitaojinBridgeTests/SafetyPolicyTests.swift`
- 新建：`tools/yitaojin-bridge/Tests/YitaojinBridgeTests/WriterPolicyTests.swift`
- 新建：`backend/app/integrations/yitaojin/service.py`
- 新建：`backend/tests/test_yitaojin_service.py`
- 新建：`scripts/run_yitaojin_sync.py`

### 6.1 先写写入安全测试

- [ ] Swift 仅新增 `add_watchlist` 和 `remove_watchlist` 两个写命令。
- [ ] 单次命令只接受一个 6 位代码。
- [ ] 写操作前后都必须读取当前页面并确认处于“自选股”区域。
- [ ] 搜索结果若无法唯一匹配目标代码则拒绝点击。
- [ ] 任何 AX 路径含禁止标签立即返回 `unsafe_ui_target`。
- [ ] 不存在买入、卖出、委托、撤单、清仓、转账命令。
- [ ] Python `CONGXI_YITAOJIN_WRITE_ENABLED=false` 时，即使调用 `apply=True` 也只返回 blocked，不调用 Swift 写命令。

Swift 路由必须是穷举 switch：

```swift
switch request.command {
case .probe, .readAccount, .readWatchlist, .readQuotes:
    return try reader.execute(request)
case .addWatchlist, .removeWatchlist:
    try safetyPolicy.assertWriteAllowed(request)
    return try writer.execute(request)
}
```

### 6.2 先写服务编排测试

- [ ] bootstrap 读取当前自选并全部保护，第一次运行不修改 App。
- [ ] `dry-run` 输出 `add/remove/keep/blocked` 和原因，不更新成功次数。
- [ ] 应用新增后必须重新读取确认，才能写入 `managed_codes`。
- [ ] 任一新增失败时不调用任何 remove。
- [ ] 第 1 至 3 次成功应用不调用 remove。
- [ ] 第 4 次起，只有双确认且不持仓的 `managed_codes` 可移除。
- [ ] 移除确认成功后才从 `managed_codes` 删除。
- [ ] 当前持仓即使退出标的池也不会进入 remove。
- [ ] 候选池损坏、过期、账户不一致、快照不完整时本轮完全 blocked。
- [ ] App 未登录时记录待同步，不启动密码输入流程。

运行红灯：

```bash
./scripts/test-yitaojin-bridge.sh
./.venv/bin/python -m pytest \
  backend/tests/test_yitaojin_service.py \
  backend/tests/test_yitaojin_planner.py \
  backend/tests/test_yitaojin_state.py -q
```

### 6.3 实现 CLI 和服务

CLI 只允许显式模式：

```bash
./.venv/bin/python scripts/run_yitaojin_sync.py probe
./.venv/bin/python scripts/run_yitaojin_sync.py account --dry-run
./.venv/bin/python scripts/run_yitaojin_sync.py watchlist --dry-run
./.venv/bin/python scripts/run_yitaojin_sync.py watchlist --apply
```

- [ ] `--apply` 仍需两个 enable 开关均为 true。
- [ ] CLI 默认从配置读取候选池和临时/真实 portfolio 路径，不接受任意代码列表做写入。
- [ ] 输出为人类可读摘要 + 可选 `--json`，不输出敏感信息。
- [ ] 每轮生成 UUID `run_id`，规划和单股结果进入 append-only 审计。
- [ ] 服务层先执行所有新增，确认成功后才评估删除。
- [ ] 所有桥调用有 15 秒超时，整轮有总预算，超时即失败关闭。

### 6.4 脱敏集成验证与提交

```bash
./scripts/test-yitaojin-bridge.sh
./.venv/bin/python -m pytest \
  backend/tests/test_yitaojin_service.py \
  backend/tests/test_yitaojin_planner.py \
  backend/tests/test_yitaojin_state.py -q
CONGXI_STATE_DIR="$(mktemp -d)" \
CONGXI_YITAOJIN_ENABLED=true \
CONGXI_YITAOJIN_WRITE_ENABLED=false \
./.venv/bin/python scripts/run_yitaojin_sync.py watchlist --dry-run
git add \
  tools/yitaojin-bridge \
  backend/app/integrations/yitaojin/service.py \
  backend/tests/test_yitaojin_service.py \
  scripts/run_yitaojin_sync.py
git commit -m "feat: sync managed yitaojin watchlist safely"
```

**生产闸门：**

1. 先由用户授予辅助功能权限。
2. 人工确认 `probe` 和账户、自选读取结果。
3. 第一次真实运行只允许 `--dry-run`。
4. 用户确认差异后才能临时开启写开关。
5. 前三次真实应用只允许新增。

**验收：**

- 手工自选和持仓在任何测试组合下均不会被移除。
- 不存在通向交易或资金页面的命令。
- 状态与审计能解释每个新增、保留、阻断和移除。

---

## Task 7：实现有限行情校验和动作门

**目的：** 只读取“持仓 + executable + watching”的准实时行情，为报告和动作展示提供新鲜度与冲突判断。

**文件：**

- 新建：`backend/app/integrations/yitaojin/quotes.py`
- 新建：`backend/tests/test_yitaojin_quotes.py`
- 修改：`backend/app/services/visible_decision_gate.py`
- 修改：`backend/app/services/quant_lifecycle.py`
- 修改：`scripts/daily_report.py`
- 修改：`backend/tests/test_visible_decision_gate.py`
- 修改：`backend/tests/test_quant_lifecycle.py`

**已确认的动作入口：**

- 日报先在 `scripts/daily_report.py` 构建 `visible_decision_gate`，再调用 `apply_visible_decision_gate()` 过滤入场动作。
- 盘中预警通过 `backend/app/services/quant_lifecycle.py` 产生候选，再使用同一 visible decision gate 过滤。
- 因此行情可信度必须进入 `backend/app/services/visible_decision_gate.py`，不在多个报告模板复制规则。

### 7.1 先写行情规则测试

- [ ] 请求代码集合只来自持仓、`executable`、`watching`。
- [ ] 常规行情年龄 `<=90s` 为 fresh，超过则 stale。
- [ ] 关键动作前行情年龄 `<=30s` 为 fresh，超过则阻止新开仓/加仓。
- [ ] 与另一生产源时间戳接近且价格偏差 `>0.5%` 时标记 conflict。
- [ ] stale/conflict 时禁止买入和加仓动作展示为可执行。
- [ ] stale/conflict 时仍保留止损、卖出和风险警告，但明确标注“需人工核价”。
- [ ] 停牌、涨跌停或代码缺失时不生成可执行买入。
- [ ] 报价快照原子写入运行状态目录，不写仓库。

核心接口：

```python
@dataclass(frozen=True)
class QuoteValidation:
    code: str
    status: Literal["fresh", "stale", "conflict", "missing", "halted"]
    age_seconds: float | None
    divergence_pct: Decimal | None
    blocks_new_entry: bool
    requires_manual_price_check: bool
    reasons: tuple[str, ...]


def validate_quote(
    yitaojin: QuoteSnapshot | None,
    reference: MarketQuote | None,
    *,
    now: datetime,
    max_age_seconds: int,
    max_divergence_pct: Decimal,
) -> QuoteValidation: ...
```

运行红灯：

```bash
./.venv/bin/python -m pytest \
  backend/tests/test_yitaojin_quotes.py \
  backend/tests/test_visible_decision_gate.py \
  backend/tests/test_quant_lifecycle.py -q
```

### 7.2 实现最小行情门

- [ ] `quotes.py` 负责收集代码、读取桥、校验和持久化。
- [ ] 生命周期服务只消费结构化 `QuoteValidation`，不直接调用 Swift。
- [ ] `build_visible_decision_gate()` 接受结构化行情校验摘要，并把 stale/conflict/missing 作为入场阻断原因。
- [ ] `apply_visible_decision_gate()` 为候选动作增加 `quote_status`、`quote_as_of`、`requires_manual_price_check`、`execution_blocked_reason`，继续保留风险动作。
- [ ] `scripts/daily_report.py` 从运行状态快照加载当日有效行情摘要，传入同一个 visible decision gate。
- [ ] 易淘金功能关闭时保持现有路径，但明确标注 `quote_status="not_enabled"`；不能伪装为已校验。
- [ ] 易淘金读取失败时不让整个风险报告消失；只关闭新开仓动作并保留持仓风险。

### 7.3 验证与提交

```bash
./.venv/bin/python -m pytest \
  backend/tests/test_yitaojin_quotes.py \
  backend/tests/test_quant_lifecycle.py \
  backend/tests/test_visible_decision_gate.py -q
./.venv/bin/ruff check \
  backend/app/integrations/yitaojin/quotes.py \
  backend/app/services/visible_decision_gate.py \
  backend/app/services/quant_lifecycle.py \
  scripts/daily_report.py \
  backend/tests/test_yitaojin_quotes.py \
  backend/tests/test_visible_decision_gate.py \
  backend/tests/test_quant_lifecycle.py
git add \
  backend/app/integrations/yitaojin/quotes.py \
  backend/app/services/visible_decision_gate.py \
  backend/app/services/quant_lifecycle.py \
  scripts/daily_report.py \
  backend/tests/test_yitaojin_quotes.py \
  backend/tests/test_visible_decision_gate.py \
  backend/tests/test_quant_lifecycle.py
git commit -m "feat: gate executable actions on fresh quotes"
```

**验收：**

- 行情校验失败只会收紧新开仓，不会吞掉持仓风险。
- 关键动作能够区分“行情未启用”“过期”“冲突”和“有效”。

---

## Task 8：接入调度、运行身份和失败状态

**目的：** 在功能默认关闭的前提下接入 08:55、20:45 和盘中每 5 分钟任务，并让服务状态可证明。

**文件：**

- 修改：`backend/app/main.py`
- 修改：`backend/app/config.py`
- 修改：`backend/tests/test_runtime_regressions.py`
- 新建：`backend/tests/test_yitaojin_scheduler.py`
- 修改：`scripts/install-congxicai-v7-launchd.sh`
- 修改：`.env.example`

### 8.1 先写调度测试

- [ ] `CONGXI_YITAOJIN_ENABLED=false` 时任务可注册但立即返回 disabled，不打开 App。
- [ ] 08:55 任务先读账户，再做自选股同步和重点行情校验。
- [ ] 20:45 任务在 20:30 日报后执行账户和自选同步。
- [ ] 盘中行情复用现有每 5 分钟扫描时点，不重复启动第二套全市场扫描。
- [ ] 11:35 和 14:55 重点校验存在；现有 11:35 报告任务不能被阻塞。
- [ ] Swift 子进程调用通过 `asyncio.to_thread` 离开主事件循环。
- [ ] 同一任务 `max_instances=1`、`coalesce=True`，有明确超时。
- [ ] 健康/状态输出包含功能开关、最近成功时间、最近失败原因、bridge build id，但不含账户明文。
- [ ] 安装脚本写入路径环境变量，但保持 enable 开关为 false。

### 8.2 实现调度包装器

- [ ] 新增小型 async wrapper，不在 scheduler 回调里写业务细节。
- [ ] 功能已启用且 App 未运行时，使用参数数组 `["open", "-a", "/Applications/GF-Trader.app"]` 启动，不使用 shell、AppleScript 或模糊应用名。
- [ ] 启动后最多等待 30 秒并重新 `probe`；实际进程路径不匹配或未登录时只记录失败，不输入密码。
- [ ] 失败状态写入运行状态目录并在健康接口展示。
- [ ] 任务失败不影响日报和持仓风险任务继续运行。
- [ ] 真实服务重启不纳入自动测试或本 Task 的默认执行；需要单独人工确认运行目标。

调度目标：

```text
08:55 Asia/Shanghai  account sync -> watchlist sync -> priority quotes
11:35 Asia/Shanghai  priority quotes, 与现有午间任务解耦
14:55 Asia/Shanghai  priority quotes
20:45 Asia/Shanghai  account sync -> watchlist sync
盘中 */5            limited quote validation, 复用生命周期候选集合
```

### 8.3 验证与提交

```bash
./.venv/bin/python -m pytest \
  backend/tests/test_yitaojin_scheduler.py \
  backend/tests/test_runtime_regressions.py \
  backend/tests/test_runtime_database_paths.py -q
./.venv/bin/ruff check \
  backend/app/main.py \
  backend/app/config.py \
  backend/tests/test_yitaojin_scheduler.py
git add \
  backend/app/main.py \
  backend/app/config.py \
  backend/tests/test_runtime_regressions.py \
  backend/tests/test_yitaojin_scheduler.py \
  scripts/install-congxicai-v7-launchd.sh \
  .env.example
git commit -m "feat: schedule disabled-by-default yitaojin sync"
```

**验收：**

- 默认配置不读取、不启动、不修改易淘金。
- 开启后慢 UI 调用不阻塞 FastAPI 事件循环。
- 运行状态能判断“未启用、未授权、未登录、成功、失败”。

---

## Task 9：文档、全量回归和人工生产验收清单

**目的：** 完成可复现交付，但不擅自重启生产服务或修改真实自选股。

**文件：**

- 修改：`README.md`
- 新建：`docs/runbooks/yitaojin-integration.md`
- 新建：`docs/acceptance/yitaojin-production-checklist.md`
- 修改：与本功能相关的测试文件

### 9.1 文档必须包含

- [x] 系统边界：只读账户、有限行情和受控自选；永不自动交易。
- [x] 安装目标固定 `/Applications/GF-Trader.app`。
- [x] Swift bridge 构建和手工辅助功能授权步骤。
- [x] 环境变量和默认禁用状态。
- [x] `probe`、账户 dry-run、自选 dry-run、只新增、受控移除命令。
- [x] 状态和审计文件位置。
- [x] 未登录、账户不一致、空持仓、界面变化、候选池过期的排障。
- [x] 如何完全关闭功能：两个 enable 开关设为 false，不删除审计或登录态。
- [x] 明确说明服务重启和真实写验收必须由用户在场确认。

### 9.2 全量自动验证

先跑相关测试：

```bash
./scripts/test-yitaojin-bridge.sh
./.venv/bin/python -m pytest \
  backend/tests/test_debate_tracker.py \
  backend/tests/test_yitaojin_models.py \
  backend/tests/test_yitaojin_planner.py \
  backend/tests/test_yitaojin_state.py \
  backend/tests/test_yitaojin_bridge.py \
  backend/tests/test_yitaojin_account.py \
  backend/tests/test_yitaojin_service.py \
  backend/tests/test_yitaojin_quotes.py \
  backend/tests/test_yitaojin_scheduler.py -q
```

再跑项目级回归：

```bash
./.venv/bin/ruff check backend/app backend/tests scripts
./.venv/bin/python -m pytest backend/tests -q
```

使用临时真实报告输入验证，不污染账户：

```bash
test_root="$(mktemp -d)"
cp data/user_portfolio.json "$test_root/portfolio.json"
cp data/candidate_pool.json "$test_root/candidate_pool.json"
cp data/position_watch.json "$test_root/position_watch.json"
export CONGXI_PORTFOLIO_PATH="$test_root/portfolio.json"
export CONGXI_CANDIDATE_POOL_PATH="$test_root/candidate_pool.json"
export CONGXI_POSITION_WATCH_PATH="$test_root/position_watch.json"
export CONGXI_VISIBLE_DECISION_GATE_PATH="$test_root/visible_decision_gate.json"
export CONGXI_NOTIFICATION_STATE_PATH="$test_root/notification_state.json"
export CONGXI_REPORT_ARCHIVE_DIR="$test_root/reports"
export CONGXI_STATE_DIR="$test_root/state"
export CONGXI_DATABASE_PATH="$test_root/state/stock_data.db"
export CONGXI_YITAOJIN_ENABLED=false
export CONGXI_YITAOJIN_WRITE_ENABLED=false
export PYTHONPATH="backend:."
./.venv/bin/python -c 'from app.database import init_db; init_db()'
./.venv/bin/python scripts/daily_report.py
```

- [x] 检查报告第一屏仍能回答持仓、买卖、不动和下一信号。
- [x] 检查未启用时不会声称易淘金行情已校验。
- [x] 记录所有未通过测试及其是否为基线问题。
- [x] 用 `rg` 扫描代码和 fixture，确认没有完整账号、手机号、密码、验证码或真实账户标识。

敏感信息扫描：

```bash
rg -n --hidden \
  '(password|验证码|交易密码|手机号|股东账号|account_number|authorization|cookie)' \
  backend/app/integrations tools/yitaojin-bridge backend/tests/fixtures/yitaojin \
  docs/runbooks/yitaojin-integration.md
```

### 9.3 代码评审

- [x] 使用 `superpowers:requesting-code-review`；按用户选择在当前会话内联执行，不委派子代理。
- [x] 重点评审失败关闭、删除所有权、Swift 安全白名单、事件循环、敏感信息和默认开关。
- [x] 修复 P0/P1 问题并重跑对应测试。

### 9.4 提交文档

```bash
git add README.md docs/runbooks/yitaojin-integration.md docs/acceptance/yitaojin-production-checklist.md
git commit -m "docs: add yitaojin integration runbook"
```

### 9.5 人工生产验收，必须由用户在场

以下步骤不在无人值守实施中自动执行：

1. 确认启动的是 `/Applications/GF-Trader.app`，不是安装镜像中的副本。
2. 用户手工授予 bridge 辅助功能权限。
3. 运行 `probe`，确认登录态和界面签名。
4. 运行账户 `--dry-run`，人工核对总资产、现金和持仓数量。
5. 运行自选 `--dry-run`，人工核对现有手工自选全部受保护。
6. 开启读功能，观察至少一个交易日的账户和行情读取。
7. 临时开启写功能，执行第一次只新增；人工核对新增项。
8. 连续三次成功后，制造一个无持仓的系统候选退出场景。
9. 连续两次确认后验证只移除该系统管理项。
10. 确认手工自选和所有持仓均未被删除。
11. 经用户明确同意后再重启常驻服务和持久开启调度。

**最终验收：**

- 自动测试和隔离报告通过。
- 默认开关为关闭。
- 审计可解释每次读取和变更。
- 用户在场的 dry-run 通过之前，不宣称生产接入完成。

---

## 暂停条件

出现以下任一情况立即停止，不自行扩大权限或绕过：

- 需要自动下单、资金权限或凭证输入。
- Accessibility 树无法稳定区分自选操作和交易操作。
- 账户指纹或资产差异无法解释。
- 空持仓无法从 UI 得到明确证据。
- 有可能删除用户手工自选或持仓。
- 必须依赖绝对坐标点击交易邻近区域。
- 全量回归出现与账户、资金、报告真值有关且无法安全修复的失败。
- 当前运行服务的代码身份和重启目标无法证明一致。

## 完成定义

本计划只有在以下条件全部满足时才算完成：

1. 复盘行情失败不再写假 0 收益。
2. 易淘金只读账户快照通过结构校验、指纹校验、空仓证据和资产差异门。
3. 自选规划器对手工保护、持仓优先、只新增三次和双确认移除有完整测试。
4. Swift 命令白名单中不存在任何交易或资金命令。
5. Python 写开关默认关闭，App/账户/候选池异常时不执行任何自选变更。
6. 行情过期或冲突时阻止新开仓，但保留持仓风险提示。
7. 调度不阻塞事件循环，且健康状态可证明最近结果。
8. 全量自动测试、Ruff、隔离报告和敏感信息扫描通过。
9. 生产权限、真实 dry-run、只新增和受控移除仍由用户逐步验收；未完成前明确标为“待生产验收”。

## 后续独立计划

本计划完成后，再分别制定并执行：

1. `A 股可信历史研究与真实执行回测计划`：数据清单、历史时点股票池、复权、T+1、涨跌停、停牌、100 股整数手、费用、滑点、walk-forward 和多重试验修正。
2. `策略影子验证与真实净利润归因计划`：建议 ID、成交匹配、费用后收益、策略晋级/降级、20–40 交易日影子期和显式亏损预算。

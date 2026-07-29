# 广发易淘金 Mac 安全接入运行手册

## 1. 当前状态与边界

这是一条“账户事实源 + 自选股投影 + 有限行情校验”链路，不是自动交易系统。

允许：

- 通过 macOS Accessibility 读取账户总资产、可用/冻结资金和当前持仓。
- 读取普通自选股页面，管理“当前持仓 + `executable` + `watching`”。
- 最多读取 32 个范围内股票的行情，和腾讯行情交叉核验。
- 在两个开关同时开启、所有安全检查通过后，把已验证账户写回本项目，并受控增删普通自选股。

禁止：

- 自动买入、卖出、撤单、清仓、转账或输入认证信息。
- 保存交易密码、验证码、完整账户号、Cookie 或登录态。
- 修改易淘金内部数据库、逆向私有接口或用绝对坐标盲点点击。
- 把 `research_reference`、`removed`、`expired` 当成生产自选或买入授权。
- 删除用户原有/后来手工添加的自选股，或删除任何当前持仓。

唯一允许的 App 是：

```text
/Applications/GF-Trader.app
bundle id: cn.com.gf.trader
```

安装镜像、外置卷或其他路径中的同名 App 都会失败关闭。

## 2. 开关与运行路径

默认值：

```bash
CONGXI_YITAOJIN_ENABLED=false
CONGXI_YITAOJIN_WRITE_ENABLED=false
CONGXI_YITAOJIN_APP_PATH=/Applications/GF-Trader.app
CONGXI_YITAOJIN_BRIDGE_PATH="$HOME/Library/Application Support/congxicai-v7/bin/yitaojin-bridge"
CONGXI_YITAOJIN_TASK_TIMEOUT_SECONDS=120
CONGXI_YITAOJIN_APP_START_TIMEOUT_SECONDS=30
```

含义：

- `ENABLED=false`：调度任务会登记为 `disabled`，不构造桥、不启动或读取 App。
- `ENABLED=true`、`WRITE_ENABLED=false`：允许只读、账户验证和自选 dry-run；不写项目持仓，不增删自选。
- 两个开关都为 `true`：账户可写回项目，自选可按所有权状态机受控应用；仍没有交易命令。

默认状态目录：

```text
~/Library/Application Support/congxicai-v7/
├── bin/yitaojin-bridge
└── yitaojin/
    ├── account_snapshot.json
    ├── account_fingerprint_salt
    ├── account_sync_audit.jsonl
    ├── watchlist_state.json
    ├── watchlist_sync_audit.jsonl
    ├── quote_snapshot.json
    └── runtime_status.json
```

`account_fingerprint_salt` 权限必须是 `0600`。不要删除或手工改写上述状态和审计文件；盐丢失后必须重新人工绑定账户，不能从完整账号恢复。

运行状态可通过以下接口读取，只返回开关、任务状态、最近成功、最近失败原因、bridge build id 和步骤状态：

```bash
curl -s http://127.0.0.1:8000/api/integrations/yitaojin/status
```

## 3. 构建与手工授权

以下步骤会把 bridge 安装到本机运行状态目录，必须在用户在场时执行：

```bash
cd /Users/zhuchenyuan/AI/workflows/恭喜发财

./scripts/test-yitaojin-bridge.sh
swift build --package-path tools/yitaojin-bridge -c release
./scripts/build-yitaojin-bridge.sh
```

自检脚本直接编译受测源码并执行 49 项安全检查；release build 再覆盖真实 CLI 入口。当前 Swift Package 没有 XCTest target，因此不要把 `swift test` 的 `no tests found` 当成测试通过。安装脚本只编译、临时签名并原子安装 bridge，不修改 macOS TCC 数据库。

然后由用户打开：

```text
系统设置 → 隐私与安全性 → 辅助功能
```

手工添加并允许：

```text
~/Library/Application Support/congxicai-v7/bin/yitaojin-bridge
```

不要用脚本修改 TCC。用户自行启动 `/Applications/GF-Trader.app` 并完成登录；系统不会输入密码或验证码。

## 4. 只读探测与首次绑定

设定共同命令环境：

```bash
cd /Users/zhuchenyuan/AI/workflows/恭喜发财
export CONGXI_STATE_DIR="$HOME/Library/Application Support/congxicai-v7"
export CONGXI_YITAOJIN_BRIDGE_PATH="$CONGXI_STATE_DIR/bin/yitaojin-bridge"
```

### 4.1 Probe

`probe` 永远只读，不带写入参数：

```bash
./.venv/bin/python scripts/run_yitaojin_sync.py probe --json
```

必须人工确认：

- `appRunning=true`
- `applicationPathValid=true`
- `accessibilityTrusted=true`
- `loginState=logged_in`
- `interfaceSignature` 非空

任一项不满足都不得进入写验收。

### 4.2 首次账户绑定

账户输出不会包含指纹或原始账户标识。首次只做验证和本地随机盐绑定：

```bash
CONGXI_YITAOJIN_ENABLED=true \
CONGXI_YITAOJIN_WRITE_ENABLED=false \
./.venv/bin/python scripts/run_yitaojin_sync.py account \
  --dry-run --bootstrap --json
```

人工核对总资产、可用资金、冻结资金、持仓代码、名称和数量。没有持仓时，界面必须同时提供“持仓数量为 0”的明确证据；仅看到空表不算可信空仓。

### 4.3 首次保护现有自选股

```bash
CONGXI_YITAOJIN_ENABLED=true \
CONGXI_YITAOJIN_WRITE_ENABLED=false \
./.venv/bin/python scripts/run_yitaojin_sync.py watchlist \
  --dry-run --bootstrap --json
```

这一步不增删股票，只把当前自选全部登记为 `manual_protected_codes`。人工确认数量和代码与 App 一致。

## 5. 日常 dry-run 与应用

### 5.1 账户 dry-run

```bash
CONGXI_YITAOJIN_ENABLED=true \
CONGXI_YITAOJIN_WRITE_ENABLED=false \
./.venv/bin/python scripts/run_yitaojin_sync.py account --dry-run --json
```

结果为 `validated` 才表示结构、指纹、空仓证据和资产勾稽通过。资产相对上一可信快照突变超过 20% 会进入人工复核，不覆盖持仓。

### 5.2 自选 dry-run

```bash
CONGXI_YITAOJIN_ENABLED=true \
CONGXI_YITAOJIN_WRITE_ENABLED=false \
./.venv/bin/python scripts/run_yitaojin_sync.py watchlist --dry-run --json
```

检查 `plan.add`、`plan.remove`、`plan.keep`：

- 用户可见的正式标的池只有两类：`short_term`（短线池）和
  `mid_long_term`（中长线池）。
- 目标集合只应包含两个正式池中 `pool_retained=true` 的标的、当前持仓和
  受保护手工自选；内部 `executable/watching/research_reference` 状态不再单独决定
  是否进入易淘金自选。
- `pool_retained=false`、`removed`、`expired` 不得进入 `add`。
- 持仓和手工自选不得进入 `remove`。

每日 20:30 主报告会重算两个池：

- 短线池新进/留池阈值为 55/50 分。
- 中长线池新进/留池阈值为 60/55 分，并要求长期逻辑未处于
  `broken/stale`。
- 已在池内的标的如果本轮缺少关键数据，先保留并等待下一轮，不能因单次抓数失败
  自动删除。
- 完成评分后，20:45 自选同步读取同一份 `candidate_pool.json`，执行
  add/remove/keep。

### 5.3 首次三轮强制只新增

只有用户核对 dry-run 后，才可临时打开两个开关执行：

```bash
CONGXI_YITAOJIN_ENABLED=true \
CONGXI_YITAOJIN_WRITE_ENABLED=true \
./.venv/bin/python scripts/run_yitaojin_sync.py watchlist --apply --json
```

首次 bootstrap 后的前三个成功应用周期由状态机强制只新增，不会移除。每一只新增都要在 App 回读确认后才写入 `managed_codes`；任何新增失败都会跳过整轮移除。

两池规则上线后，只有同时带有 `pool_kind` 和明确每日评分记录、且曾经
`pool_retained=true` 的旧标的，才会从 `manual_protected_codes` 迁为
`managed_codes`。这让系统能在后续低分时按规则退出；没有两池评分历史的真正
手工自选不会被系统认领。

### 5.4 受控移除

受控移除使用同一条 `watchlist --apply` 命令，没有直接指定代码或强制删除入口。只有同时满足以下条件才可能移除：

1. 已完成至少三个成功应用周期。
2. 股票由系统成功添加并记录在 `managed_codes`。
3. 已退出目标集合且当前不持仓。
4. 连续两个可信同步周期确认退出。
5. 本轮所有新增均成功，候选池和账户输入均可信。

单股移除失败时仍保留系统所有权，等待下一轮重试。人工自选和持仓永不移除。

### 5.5 账户写回项目

账户写回只更新项目的 `data/user_portfolio.json`，不会向易淘金提交交易：

```bash
CONGXI_YITAOJIN_ENABLED=true \
CONGXI_YITAOJIN_WRITE_ENABLED=true \
./.venv/bin/python scripts/run_yitaojin_sync.py account --apply --json
```

新出现持仓标记为未归因的券商持仓；消失持仓移出当前持仓，但原始记录和
`trade_history` 会保存在 `broker_missing_positions_pending`，只记待核对事件，
不虚构卖出成交或已实现盈亏。若后续券商快照重新出现该持仓，系统恢复原历史并
解除该代码的待归因状态。

## 6. 行情门与调度

行情范围固定为当前持仓及短线池、中长线池的留池标的，最多 32 个代码：

- 常规快照年龄 `<=90s` 才是 fresh。
- 新开仓/加仓动作前年龄 `<=30s`。
- 与时间相近的腾讯行情价格偏差 `>0.5%` 标记 conflict。
- stale、conflict、missing、停牌或涨跌停禁止新开仓。
- 止损、卖出和风险警告继续显示，并标注“需人工核价”。
- 功能关闭时显示 `quote_status=not_enabled`，不会伪装成已校验。

默认关闭的调度：

```text
08:55  账户验证/写回 → 自选 dry-run/应用 → 重点行情
11:35  重点行情校验，与午间报告独立
14:55  收盘前重点行情校验
20:30  两个正式池每日评分、晋级与淘汰
20:45  账户验证/写回 → 短线池 + 中长线池 + 持仓 自选 dry-run/应用
盘中每 5 分钟 复用既有生命周期扫描，不启动第二套全市场扫描
```

盘中 UI 调用通过线程执行，不阻塞 FastAPI 事件循环。行情读取失败只封锁候选入场，不中断持仓风险或日报。

## 7. 故障处理

| 状态/原因 | 含义 | 处理 |
|---|---|---|
| `integration_disabled` / `disabled` | 主开关关闭 | 这是默认安全状态；不处理 App |
| `accessibility_permission_missing` | bridge 未获辅助功能权限 | 用户在系统设置中手工授权，不修改 TCC |
| `not_logged_in` | App 未登录 | 用户手工登录；系统不输入认证信息 |
| `application_path_mismatch` | 运行的不是固定 App | 退出安装镜像/外置卷副本，只启动 `/Applications/GF-Trader.app` |
| `account_not_bootstrapped` | 尚未绑定可信账户 | 执行账户 `--dry-run --bootstrap` 并人工核对 |
| `account_fingerprint_mismatch` | 当前账户与已绑定账户不同 | 禁止覆盖；确认是否切换账户，再决定是否重新人工绑定 |
| `empty_positions_unconfirmed` | 空表缺少明确空仓证据 | 不覆盖原持仓；回到 App 核对持仓数量 |
| `asset_reconciliation_gap` | 资产与现金/持仓市值勾稽不符 | 人工核对冻结资金和界面字段，不放宽容差 |
| `total_assets_changed_over_20pct` | 资产突变超过阈值 | 人工核对转入转出、成交和界面解析 |
| `candidate_pool_before_daily_report` | 候选池早于 20:30 主报告 | 等待新主报告完成，不修改自选 |
| `candidate_pool_missed_completed_trading_day` | 候选池错过最近交易日 | 修复主报告/候选池，不沿用过期目标 |
| `unsafe_ui_target` / `UnsafeUiTargetError` | 页面路径靠近交易区或目标不唯一 | 视为界面版本变化，停止；禁止改成坐标点击 |
| `quote_snapshot_unavailable` / `quote_read_failed` | 行情快照损坏或读取失败 | 新开仓失败关闭；持仓风险继续，人工核价 |

界面版本变化时，先保存脱敏 Accessibility 结构证据并更新固定样本测试。未通过 Swift 安全测试前不得恢复写功能。

## 8. 完全关闭

将两个开关都设为 `false`：

```bash
CONGXI_YITAOJIN_ENABLED=false
CONGXI_YITAOJIN_WRITE_ENABLED=false
```

常驻服务只有在用户明确同意时才重启使配置生效。关闭时不要删除：

- 易淘金登录态或 App 数据。
- `account_fingerprint_salt`。
- `watchlist_state.json`。
- 账户/自选审计和运行状态。

保留这些文件才能解释历史读取和变更，也能防止未来重新启用时误把手工自选当成系统所有。

## 9. 生产验收边界

代码测试、Swift 构建和隔离报告通过，只能说明“实现可验”。在用户在场完成 [人工生产验收清单](../acceptance/yitaojin-production-checklist.md) 前，不得宣称：

- 已完成真实账户绑定。
- 已正确写回真实持仓。
- 已安全修改真实自选股。
- 已在常驻服务中生产启用。

服务重启、永久开启调度和任何真实自选写入都必须单独获得用户确认。

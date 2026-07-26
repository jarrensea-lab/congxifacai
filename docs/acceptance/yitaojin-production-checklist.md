# 易淘金生产接入人工验收清单

> 本清单必须由用户在场逐项确认。默认结论是“代码已实现、生产未启用”。未经明确确认，不授予辅助功能权限、不重启服务、不写真实账户或自选股。
>
> 命令说明、状态文件和故障处理见 [广发易淘金 Mac 安全接入运行手册](../runbooks/yitaojin-integration.md)。

## A. 自动验证证据

- [ ] `./scripts/test-yitaojin-bridge.sh` 的 49 项安全检查全绿。
- [ ] `swift build --package-path tools/yitaojin-bridge -c release` 全绿。
- [ ] 易淘金相关 Python 测试全绿。
- [ ] `ruff check backend/app backend/tests scripts` 全绿。
- [ ] `pytest backend/tests -q` 全绿。
- [ ] 使用临时 portfolio、candidate pool、report archive 和 state 生成一次真实结构日报。
- [ ] 隔离日报第一屏仍回答：持仓怎么处理、卖不卖/卖多少、买不买/买谁/多少钱/止损、今天不动等什么。
- [ ] 功能关闭时报告明确显示 `quote_status=not_enabled`，没有声称易淘金行情已校验。
- [ ] 敏感信息扫描没有发现真实账号、手机号、密码、验证码、Cookie、authorization 值或账户标识。

自动验证记录：

```text
日期：
分支/提交：
Swift：
Python：
Ruff：
隔离报告路径：
未通过项：
```

## B. App 身份与权限

- [ ] Finder/终端确认 App 位于 `/Applications/GF-Trader.app`。
- [ ] 确认没有从安装镜像或外置卷运行同名 App。
- [ ] 确认 bundle id 是 `cn.com.gf.trader`。
- [ ] bridge 已构建到 `~/Library/Application Support/congxicai-v7/bin/yitaojin-bridge`。
- [ ] 用户已在“系统设置 → 隐私与安全性 → 辅助功能”中手工授权该 bridge。
- [ ] 没有脚本修改 TCC 数据库。
- [ ] 用户已自行登录；系统没有输入或保存认证信息。

证据/备注：

```text
App 路径：
bundle id：
bridge build id：
授权确认：
```

## C. 只读 Probe

运行：

```bash
./.venv/bin/python scripts/run_yitaojin_sync.py probe --json
```

- [ ] `appRunning=true`
- [ ] `applicationPathValid=true`
- [ ] `accessibilityTrusted=true`
- [ ] `loginState=logged_in`
- [ ] `interfaceSignature` 非空且已记录。
- [ ] 输出不含完整账号、手机号或认证信息。

如任一项失败：停止，不进入账户或自选写验收。

## D. 账户首次绑定与 dry-run

保持：

```bash
CONGXI_YITAOJIN_ENABLED=true
CONGXI_YITAOJIN_WRITE_ENABLED=false
```

运行账户首次绑定：

```bash
CONGXI_YITAOJIN_ENABLED=true \
CONGXI_YITAOJIN_WRITE_ENABLED=false \
./.venv/bin/python scripts/run_yitaojin_sync.py account \
  --dry-run --bootstrap --json
```

- [ ] 人工核对总资产。
- [ ] 人工核对可用和冻结资金。
- [ ] 逐只核对持仓代码、名称、总股数和可用股数。
- [ ] 如为空仓，App 同时显示明确的持仓数量为 0 证据。
- [ ] 输出没有账户指纹、完整账号或认证信息。
- [ ] 资产勾稽差不超过 1 元。
- [ ] `account_fingerprint_salt` 权限为 `0600`。
- [ ] 审计状态为 `validated`，项目真实 portfolio 尚未被覆盖。

如账户指纹不一致、空仓证据不足、资产勾稽失败或资产突变超过 20%：停止并人工核对，不放宽阈值。

## E. 自选首次保护与 dry-run

运行：

```bash
CONGXI_YITAOJIN_ENABLED=true \
CONGXI_YITAOJIN_WRITE_ENABLED=false \
./.venv/bin/python scripts/run_yitaojin_sync.py watchlist \
  --dry-run --bootstrap --json
```

- [ ] App 当前所有手工自选都进入 `manual_protected_codes`。
- [ ] 没有新增、移除或重排自选。
- [ ] 当前真实持仓全部在目标保留集合。

再运行普通 dry-run：

```bash
CONGXI_YITAOJIN_ENABLED=true \
CONGXI_YITAOJIN_WRITE_ENABLED=false \
./.venv/bin/python scripts/run_yitaojin_sync.py watchlist --dry-run --json
```

- [ ] `add` 只含 `executable`、`watching` 或持仓。
- [ ] `research_reference`、`removed`、`expired` 不在 `add`。
- [ ] 手工自选和持仓不在 `remove`。
- [ ] 候选池时间戳对应最近已完成交易日的 20:30 主报告。

## F. 一个交易日只读观察

- [ ] 先保持 `WRITE_ENABLED=false` 观察至少一个完整交易日。
- [ ] 08:55 顺序为账户→自选 dry-run→重点行情。
- [ ] 11:35 午间报告没有被行情 UI 调用阻塞。
- [ ] 14:55 有重点行情校验。
- [ ] 20:45 有账户→自选 dry-run。
- [ ] 盘中每 5 分钟只读取持仓和有效候选，没有第二套全市场扫描。
- [ ] 行情 stale/conflict/missing 时买入/加仓被封锁。
- [ ] 同场景下止损、卖出和风险警告仍显示“需人工核价”。
- [ ] `/api/integrations/yitaojin/status` 能解释最近成功/失败且不含账户材料。

## G. 账户写回项目

仅在用户明确确认后，临时执行：

```bash
CONGXI_YITAOJIN_ENABLED=true \
CONGXI_YITAOJIN_WRITE_ENABLED=true \
./.venv/bin/python scripts/run_yitaojin_sync.py account --apply --json
```

- [ ] 写入前已备份并核对 `data/user_portfolio.json`。
- [ ] 项目持仓代码、名称、数量、成本和可用数量与 App 一致。
- [ ] 冻结资金纳入总资产。
- [ ] 新出现持仓标记为未归因券商持仓，没有虚构买入成交。
- [ ] 消失持仓进入 `broker_missing_positions_pending`，没有虚构卖出和已实现盈亏。
- [ ] 消失或重新出现的持仓，其既有 `trade_history` 未丢失。

任何差异无法解释：立即把两个开关恢复为 `false`，保留审计，不重复 apply。

## H. 第一次只新增

用户核对本轮 dry-run 后，临时运行：

```bash
CONGXI_YITAOJIN_ENABLED=true \
CONGXI_YITAOJIN_WRITE_ENABLED=true \
./.venv/bin/python scripts/run_yitaojin_sync.py watchlist --apply --json
```

- [ ] 本轮只出现 `add_watchlist`，没有移除。
- [ ] 每一只新增都在 App 回读确认存在。
- [ ] 新增项记录为 `managed_codes`。
- [ ] 手工自选未改变。
- [ ] 持仓未改变。
- [ ] 交易、委托、撤单和转账区域从未被触发。

前三个成功应用周期逐轮核对：

- [ ] 第 1 轮只新增。
- [ ] 第 2 轮只新增。
- [ ] 第 3 轮只新增。

## I. 受控移除

仅选择一只“系统曾添加、已退出目标池、当前不持仓”的测试标的：

- [ ] 第一次可信退出确认后只增加 pending count，不移除。
- [ ] 第二次可信退出确认后，dry-run 只计划移除该系统项。
- [ ] 用户再次确认后运行 `watchlist --apply`。
- [ ] App 回读确认该系统项已移除。
- [ ] 其他 `managed_codes` 未误删。
- [ ] 所有 `manual_protected_codes` 未误删。
- [ ] 所有当前持仓未误删。
- [ ] 单股移除失败时仍保留系统所有权，可安全重试。

任何误删迹象：立即关闭两个开关并停止，不继续下一轮。

## J. 常驻服务与最终启用

只有 A–I 全部通过后，用户才能决定是否持久启用：

- [ ] 用户明确同意修改 `.env.local`。
- [ ] 用户明确同意重启 `com.zhuchenyuan.congxicai-v7`。
- [ ] 重启前确认 launchd 运行的是目标项目目录和目标 bridge。
- [ ] 重启后状态接口显示正确开关、bridge build id 和最近任务。
- [ ] 再次确认系统仍不包含任何交易命令。

最终结论：

```text
[ ] 保持关闭
[ ] 只读生产启用
[ ] 账户写回 + 受控自选启用

用户确认：
日期：
证据链接：
剩余风险：
```

## K. 关闭与恢复

完全关闭：

```bash
CONGXI_YITAOJIN_ENABLED=false
CONGXI_YITAOJIN_WRITE_ENABLED=false
```

- [ ] 经用户同意后重启服务使开关生效。
- [ ] 不删除 App 登录态、账户盐、所有权状态、快照或审计。
- [ ] 关闭后任务状态为 `disabled`，不会启动或读取 App。
- [ ] 原有腾讯/Tushare/报告/持仓风险链路继续运行。

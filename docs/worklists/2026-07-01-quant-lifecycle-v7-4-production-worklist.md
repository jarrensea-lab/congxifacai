# v7.4.0-dev 生产闭环 Worklist

日期：2026-07-01
分支：`codex/quant-lifecycle-v7-4`
方案文件：`docs/superpowers/plans/2026-07-01-quant-lifecycle-v7-4-production-plan.md`

## 任务目标

把恭喜发财从“每天生成报告”推进到“候选池、持仓池、定时扫描、飞书预警、收盘复盘”的可运行闭环。

当前阶段不做分钟级和秒级能力，先保证架构、数据、策略、监控、预警能稳定跑通。

## Worklist

### 1. 仓库与版本基线

状态：已完成

执行步骤：

- 创建 feature 分支 `codex/quant-lifecycle-v7-4`。
- 统一 README、ROADMAP、CHANGELOG、FastAPI app 和 health 版本到 `v7.4.0-dev`。
- 重启本地 8000 服务，确认 health 不是旧进程。

预期产物：

- 干净 feature 分支。
- `/api/health` 返回 `v7.4.0-dev`。

验证方式：

- `git status --short --branch`
- `curl -sS http://127.0.0.1:8000/api/health`

### 2. 生产候选池基础服务

状态：已完成

执行步骤：

- 新增 `CandidatePoolStore`。
- 支持推荐入池、数据不足继续观察、可试仓、禁止追高、状态历史。
- 使用 JSON 文件作为当前生产副本，后续再升级数据库表。

预期产物：

- `backend/app/services/quant_lifecycle.py`
- 默认数据文件：`data/candidate_pool.json`

验证方式：

- `backend/tests/test_quant_lifecycle.py`
- 覆盖数据不足入池、000629 类涨停附近禁止追高、低价放量可试仓。

### 3. 持仓监控池基础服务

状态：已完成

执行步骤：

- 新增 `PositionWatchStore`。
- 支持记录持仓止损价和目标价。
- 午后风控读取该计划，补齐 `MonitorService` 原本没有吃到的字段。

预期产物：

- 默认数据文件：`data/position_watch.json`
- 午后持仓触发止损/止盈飞书提醒。

验证方式：

- 单测覆盖跌破止损生成 high alert。
- 单测覆盖触及目标生成 mid alert。

### 4. 主流程接入

状态：已完成

执行步骤：

- 盘前辩论结果写入生产候选池。
- 午盘空仓时扫描候选池，而不是只推空仓观望。
- 午后空仓时扫描候选池。
- 午后有持仓时同时扫描持仓池和候选池。
- 告警等级统一 `medium` 到 `mid`。

预期产物：

- `backend/app/main.py` 接入候选池和持仓池。
- `backend/app/report_engine/renderers/markdown_card.py` 兼容告警等级。

验证方式：

- `PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests/ -q`
- `.venv/bin/python -m ruff check backend scripts`

### 5. Sentinel/Serenity 入生产候选池

状态：未完成，下一步

执行步骤：

- 定位 Sentinel 研究包输出结构。
- 抽取候选主题、候选标的、证据摘要、风险标签。
- 转成 `candidate_pool` 入池事件。
- 保持研究层边界：研究结论不直接买卖，只触发入池、继续观察、禁止追、出池。

预期产物：

- Sentinel/Serenity 入池适配函数或服务。
- 对应单元测试。
- 至少一个历史研究包回放样例。

验证方式：

- 历史 Sentinel 包可以生成候选池事件。
- 无标的映射时必须输出“无法入池原因”，不能静默。

### 6. 告警去重与冷却

状态：未完成

执行步骤：

- 在候选池和持仓池记录 `last_alert_at`、`last_alert_action`。
- 同一标的同类提醒设置冷却窗口。
- 风险升级、止损触发、禁止追高可打破普通冷却。

预期产物：

- 扫描多次不会反复刷同一条飞书。
- 风险升级仍能及时提醒。

验证方式：

- 构造同一 quote 连续扫描，只发送一次。
- 构造风险升级 quote，确认重新发送。

### 7. 生命周期日报

状态：未完成

执行步骤：

- 收盘汇总新入池、继续观察、可试仓、禁止追、出池、错失复盘。
- 推送飞书摘要。
- 本地归档完整状态。

预期产物：

- 每日生命周期报告。
- 可复盘候选池状态迁移。

验证方式：

- 有入池/出池/提醒记录时，报告能完整列出。
- 无事件时报告明确写“无状态迁移”。

### 8. 手动扫描入口

状态：未完成

执行步骤：

- 新增脚本或 API，允许手动触发候选池扫描。
- 支持只扫描、不推送和扫描并推送两种模式。

预期产物：

- `scripts/run_lifecycle_scan.py` 或 FastAPI endpoint。

验证方式：

- 手动运行可看到扫描数量、触发数量和数据源状态。
- Webhook 未配置时不报错，只输出本地结果。

## 已知风险与边界

- 当前只是提醒系统，不是自动交易系统。
- 当前用 JSON 做生产副本，适合小账户和单用户；后续多账户需要迁移数据库。
- 腾讯行情字段在不同股票上可能缺失，必须容错。
- Sentinel/Serenity 的候选映射可能不完整，需要记录未映射原因。
- 用户资金边界、真实下单、自动交易都必须单独确认。

## 会触发暂停的条件

- 需要接入真实券商交易或自动下单。
- 需要改变单票仓位、止损比例、最大回撤等资金规则。
- 需要新增私密凭证或授权。
- 数据源不可用且无法判断行情事实。
- Sentinel/Serenity 研究结论无法映射到 A 股标的，继续硬接会制造假信号。


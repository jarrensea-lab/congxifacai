# 盈利主线 Evidence / 标的池 / Sentinel / Serenity Worklist

日期：2026-07-01
分支：`codex/quant-lifecycle-v7-4`
上游决策：`docs/architecture/2026-07-01-profit-centered-pipeline-decision.md`

## 推理结论

推理通过，但必须按以下约束执行：

- 项目目标只有一个：选出标的，给出策略，用户按策略操作，并通过复盘提高盈利概率。
- 标的池是总生命周期池；候选池不是另一个独立池，而是标的池中的一种状态。
- Sentinel 是证据中枢，不是决策者。
- Serenity 是产业链评分因子和辩论角色，不是买卖决策者。
- 数据源引擎提供事实，Sentinel 归一化证据，Serenity 判断产业链质量，策略评分器和风控决定状态迁移。
- 任何模块如果不能影响标的池、策略、预警或复盘权重，就不进入生产主线。

## 任务目标

把当前“研究包 + 报告 + 软辩论”升级为“证据账本 + 标的生命周期池 + 显式评分 + 飞书预警 + 收益归因”的生产闭环。

最终用户看到的不是更多报告，而是：

- 哪些标的进入标的池。
- 为什么进入。
- 当前处于什么状态。
- 何时可试仓。
- 什么情况禁止追。
- 什么情况出池。
- 策略按什么证据给出。
- 最后是否盈利，哪个证据/角色贡献了结果。

## 术语锁定

### 标的池

推荐正式名称：`target_pool` 或 `stock_lifecycle_pool`。

含义：所有被系统关注、研究、扫描、提醒、持仓、出池过的股票总池。

状态包括：

- `research_only`：只有研究线索，不足以进入候选扫描。
- `candidate`：候选状态，未建仓，进入定时扫描。
- `actionable`：满足策略触发，可试仓，必须人工确认。
- `blocked_chasing`：过热或接近涨停，禁止追高。
- `position`：已建仓，转入持仓监控。
- `removed`：逻辑失效或长期无效，出池。

### 候选池

候选池不是独立数据域，而是标的池中 `status=candidate/actionable/blocked_chasing` 的视图。

当前代码 `CandidatePoolStore` 可以短期保留，但文档和后续重构必须明确：

- 它现在承担的是标的生命周期池 MVP。
- 后续应重命名或增加兼容别名 `TargetPoolStore`。

### Sentinel

Sentinel 是证据中枢，负责：

- 从数据源引擎和研究模块接收事实。
- 生成 `evidence_id`。
- 标准化主题、标的、来源、时间、风险标签。
- 形成证据账本。
- 给标的池提供入池建议和风险标签。

Sentinel 不能：

- 直接决定买入。
- 直接决定卖出。
- 绕过策略评分器和风控。

### Serenity

Serenity 有两个角色：

- 辩论角色：产业链研究员，参与四角色辩论。
- 评分因子：给标的池中的标的提供产业链质量评分。

Serenity 不直接给买点。它只回答：

- 是否处于真实瓶颈环节。
- 是否是伪题材或已过热。
- 产业链逻辑是否支持继续观察。
- 还缺什么证据。

## Worklist

### 1. 术语与数据结构修正

状态：待做

执行步骤：

- 在文档中统一 `标的池 > 候选状态` 的层级关系。
- 保留现有 `CandidatePoolStore`，新增兼容类或别名 `TargetPoolStore`。
- 将 JSON item 字段从“候选推荐”扩展为“生命周期状态”。
- 明确 `candidate_pool.json` 是 MVP 文件名，语义上是 target pool。

预期产物：

- `TargetPoolStore` 兼容入口。
- 标的池状态字段。
- 文档不再把候选池和标的池混作同义词。

验证方式：

- 单测确认旧 `CandidatePoolStore` 调用不破坏。
- 单测确认 `target_pool` item 支持 `research_only/candidate/actionable/blocked_chasing/position/removed`。

### 2. Evidence Ledger

状态：待做

执行步骤：

- 新增 `EvidenceLedgerStore`。
- 为新闻、主题、Serenity 候选、辩论建议、飞书提醒生成统一 `evidence_id`。
- 每条 evidence 记录来源、时间、主题、标的、证据摘要、风险标签。
- 记录 evidence 是否进入策略输入、是否进入标的池、是否触发提醒。

预期产物：

- `data/evidence_ledger.jsonl` 或等价 JSONL 存储。
- evidence 可追溯到标的池状态迁移。

验证方式：

- 给定 Sentinel package，能生成稳定 `evidence_id`。
- 同一新闻重复导入不会重复生成有效 evidence。
- 每个入池标的至少关联一个 evidence。

### 3. Sentinel 证据中枢

状态：待做

执行步骤：

- 将 Sentinel package 转为 Evidence Ledger。
- 提取 `top_themes`、`top_symbols`、`risk_events`、`serenity_deep_dives`。
- 对 A 股代码做格式校验。
- 对无法映射 A 股的主题记录 `research_only`，不能静默丢失。
- Sentinel 只输出证据和入池建议，不输出交易动作。

预期产物：

- `build_sentinel_evidence(package)`。
- `upsert_sentinel_evidence_to_target_pool(package, target_pool, ledger)`。

验证方式：

- 2026-06-30 Sentinel 包可生成 evidence。
- 半导体 Serenity 深挖候选可进入标的池 `candidate` 状态。
- AI/金融主题如果没有候选，记录未入池原因。

### 4. Serenity 产业链评分因子

状态：待做

执行步骤：

- 将 `serenity_deep_dives[].top_candidates` 转为标的池研究因子。
- 保存 `serenity_score`、`chokepoint`、`chain_position`、`verify_next`。
- 初始候选评分中 Serenity 权重设为 15%。
- 如果 Serenity 判断主题过热或证据不足，只能降级或继续观察，不能直接买入。

预期产物：

- 标的池 item 包含 Serenity 因子字段。
- 策略评分器能读取 Serenity 因子。

验证方式：

- Serenity 候选入池后，评分包含 `serenity_score`。
- 缺少行情或财务证据时，状态为 `research_only` 或 `candidate`，不能直接 `actionable`。

### 5. 策略评分器

状态：待做

执行步骤：

- 新增确定性评分器，避免完全依赖 LLM 自由裁决。
- 初始评分：
  - 技术触发 30。
  - 流动性与可买性 20。
  - Sentinel 主题热度 15。
  - Serenity 产业链评分 15。
  - 基本面/估值 10。
  - 风控惩罚 -30 到 0。
- 守夜人风险保留 veto，不只是负权重。

预期产物：

- `score_target_for_action(item, quote, account, evidence)`。
- 输出 `candidate/actionable/blocked_chasing/removed`。
- 输出每个分项得分和扣分理由。

验证方式：

- 低价、可买、放量、证据充分标的可进入 `actionable`。
- 涨停附近标的必须 `blocked_chasing`。
- ST/退市/暴雷风险必须 veto。
- 数据不足只能 `candidate` 或 `research_only`。

### 6. Sentinel evidence 注入辩论

状态：待做

执行步骤：

- 在盘前链路中，先加载 Sentinel evidence，再运行 `run_analysis()` / `run_debate()`。
- 四名辩手看到同一份 Sentinel evidence 摘要。
- 裁判必须输出采用了哪些 evidence。
- 无 Sentinel evidence 时，旧流程必须正常运行。

预期产物：

- `build_sentinel_evidence_context(...)`。
- `analysis_report["sentinel_evidence"]`。
- `run_debate()` prompt 包含 evidence 摘要。

验证方式：

- 单测确认 `run_debate()` 输入包含 Sentinel evidence。
- 没有 Sentinel 包时回退不失败。

### 7. role_votes 与可审计辩论

状态：待做

执行步骤：

- 裁判输出增加 `role_votes`。
- 每个推荐标的必须列出：
  - 猎手评分和理由。
  - 账房评分和理由。
  - 守夜人是否 veto。
  - Serenity 评分和理由。
  - 引用的 evidence_id。
- 如果裁判忽略 Serenity 或 Sentinel，必须说明原因。

预期产物：

- `decision.role_votes`。
- 报告中可展示角色贡献。

验证方式：

- 每个 `stock_pool` 推荐都有 `role_votes`。
- 守夜人 veto 时，标的不能进入 `actionable`。
- Serenity 分数不能凭空生成，必须引用 evidence 或 deep dive。

### 8. 报告引擎接入生命周期与归因

状态：待做

执行步骤：

- 报告引擎新增生命周期报告 schema。
- 展示标的状态、来源、证据、评分、动作、未入池原因。
- 主报告标明 Sentinel/Serenity 是：
  - `已进入策略输入`
  - `已进入标的池`
  - `仅报告展示`
  - `未入池：原因`
- 飞书只推动作摘要，本地归档完整证据。

预期产物：

- `push_lifecycle_alerts()`。
- `build_lifecycle_daily_card()`。
- 生命周期日报归档。

验证方式：

- 候选池触发不再绕过 ReportEngine。
- 报告里能追溯 evidence_id。
- 无状态迁移时明确写“无状态迁移”。

### 9. 复盘与模块贡献评估

状态：待做

执行步骤：

- 收盘复盘回填候选表现。
- 统计 Sentinel 证据命中率。
- 统计 Serenity 候选跑赢指数比例。
- 统计角色投票贡献。
- 统计错失机会原因。

预期产物：

- 模块贡献报告。
- 可回答：
  - Sentinel 是否提高候选命中率。
  - Serenity 是否提高标的质量。
  - 猎手/账房/守夜人/Serenity/裁判谁贡献正收益。

验证方式：

- 至少 20 个候选样本后生成贡献评估。
- 没有样本时报告明确“样本不足”，不假装有效。

### 10. 手动与定时运行入口

状态：待做

执行步骤：

- 新增手动 evidence ingest。
- 新增手动 target pool scan。
- 连接午盘、午后、收盘任务。
- 保持 Webhook 未配置时本地可跑。

预期产物：

- `scripts/run_evidence_ingest.py`
- `scripts/run_lifecycle_scan.py`
- 定时任务复用同一服务。

验证方式：

- 手动运行可生成 evidence。
- 手动运行可更新标的池。
- 手动运行可输出动作摘要。

## 验收标准

- 标的池和候选状态在文档、代码、报告中不再混用。
- Sentinel 生成 evidence，不直接决策。
- Serenity 生成产业链评分，不直接给买点。
- 每个入池标的都有 evidence。
- 每个 `actionable` 标的都有技术触发、可买性和风控通过证据。
- 每个 `blocked_chasing` 标的有过热原因。
- 每个出池标的有出池原因。
- 主报告能说明研究是否进入策略输入。
- 飞书提醒能说明为什么现在提醒、什么情况取消。
- 复盘能评估 Sentinel/Serenity 是否真的帮助盈利。

## 暂停条件

- 需要接入真实券商交易或自动下单。
- 需要改变仓位、止损、最大回撤等资金规则。
- Sentinel/Serenity 无法可靠映射 A 股标的，继续硬接会制造假信号。
- 数据源缺失导致无法判断行情、成交额、一手金额或风险状态。
- 设计实现会让 Sentinel 变成决策者，越过策略评分器和风控。


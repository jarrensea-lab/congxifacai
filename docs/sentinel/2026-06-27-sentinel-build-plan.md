# Sentinel 搭建计划

> 日期：2026-06-27
> 状态：已确认，按此推进
> 目标：把前期 Serenity 研究能力调整为 Sentinel 研究证据层，并与恭喜发财主流程对齐

## 1. 总目标

Sentinel 的建设目标是为恭喜发财增加一个稳定的研究证据层。它接收数据源引擎整理后的统一数据包，输出主题雷达、候选池研究、风险提示和辩论输入包。

它不承担交易执行，不作为第五名辩手，也不绕过裁判和风控。

## 2. 建设原则

- 先契约，后实现。
- 先旁路接入，后主流程引用。
- 先研究证据，后盘中提醒。
- 先文件输入输出，后服务化 API。
- 保留旧流程可运行，避免影响成熟报告和交易管线。
- 所有金融结论必须标记事实、推断和待核验。

## 3. 里程碑

### M0：方案确认

目标：确认角色、命名、串联关系和边界。

交付物：

- `docs/sentinel/2026-06-27-sentinel-optimization-proposal.md`
- `docs/sentinel/2026-06-27-sentinel-server-worklist.md`
- `docs/sentinel/2026-06-27-sentinel-build-plan.md`

验收：

- 用户确认 Sentinel 命名。
- 用户确认 Horizon 与数据源引擎是串联关系。
- 用户确认 Sentinel 只做研究证据，不参与辩论投票。

### M1：输入输出契约

目标：让 Horizon、数据源引擎、Sentinel、辩论系统有统一接口。

状态：进行中，已新增契约文档、示例数据和最小校验模块。

交付物：

- Sentinel 输入 schema 文档。
- Sentinel 输出 schema 文档。
- 示例数据：
  - `news_events.example.jsonl`
  - `market_snapshot.example.json`
  - `portfolio_snapshot.example.json`
  - `candidate_pool.example.json`
  - `sentinel_debate_packet.example.md`

验收：

- 示例数据可以覆盖盘前、盘中、收盘三类场景。
- 缺失字段的降级规则清楚。
- 不出现敏感凭据字段。

### M2：Horizon 新闻接入适配

目标：让恭喜发财数据源引擎可以读取 Horizon 采集到的 TusharePro 新闻。

状态：进行中，已完成本地 raw JSONL 导入、去重、轻量标签、敏感字段拦截和 `news_events.jsonl` 写出能力。

交付物：

- Horizon 新闻导入器。
- 新闻去重逻辑。
- 新闻轻量标签逻辑。
- 数据源引擎统一输出的 `news_events.jsonl`。

验收：

- 同一天重复抓取不会产生重复事件。
- 新闻源不可用时，恭喜发财原有任务继续运行。
- 新闻记录能追溯到来源、抓取时间和正文 hash。

### M3：Sentinel 核心研究引擎

目标：基于统一输入包生成研究证据。

交付物：

- 主题雷达生成器。
- 候选池 review 生成器。
- 风险提示生成器。
- 研究报告渲染器。

验收：

- 能生成 `sentinel_theme_radar.json`。
- 能生成 `sentinel_candidate_review.json`。
- 能生成 `sentinel_debate_packet.md`。
- 输出中不含直接买卖指令。

### M4：辩论系统旁路接入

目标：让四名辩论员可以读取 Sentinel 证据包，但不改变旧流程默认行为。

交付物：

- 辩论入口增加可选 Sentinel 输入。
- 四名辩论员 prompt 增加统一证据引用区。
- 裁判 prompt 增加证据摘要和待核验提示。

验收：

- 无 Sentinel 输入时，旧辩论流程正常。
- 有 Sentinel 输入时，辩论报告能引用证据。
- 裁判仍明确区分证据和观点。

### M5：司库归档和复盘

目标：让原始新闻、Sentinel 研究报告、辩论材料能沉淀到司库。

交付物：

- Sentinel 司库归档目录规则。
- Markdown frontmatter 规范。
- 日内和日终研究报告模板。

验收：

- Obsidian 可以检索 Sentinel 报告。
- 报告可以按日期、主题、标的、模块筛选。
- 复盘时能从研究报告回查原始新闻。

### M6：服务化和自动化

目标：在文件契约稳定后，把 Sentinel 接入每日自动化。

交付物：

- 盘前 Sentinel 任务。
- 午间 Sentinel 任务。
- 收盘 Sentinel 任务。
- 夜间 Sentinel 任务。
- 可选 FastAPI 只读接口。

验收：

- 自动化默认失败不阻断日报主流程。
- Sentinel 任务失败会留下错误报告。
- 飞书只推摘要，不推长原始新闻。

## 4. 推荐执行顺序

```text
第 1 步：用户审核本方案
第 2 步：冻结命名和边界
第 3 步：写输入输出契约
第 4 步：做 Horizon 新闻导入器
第 5 步：做 Sentinel 核心研究输出
第 6 步：旁路接入辩论系统
第 7 步：接入司库归档
第 8 步：再考虑自动化和服务化
```

## 5. 与 Horizon 的分工

Horizon 做：

- 抓 TusharePro 滚动新闻。
- 去重。
- 原始 JSONL 落盘。
- 生成轻量 Markdown 摘要。

Horizon 不做：

- 不做买卖建议。
- 不做候选池最终判断。
- 不直接喂给 Sentinel 正式运行。
- 不保存 cookie、token、authorization header 到输出文件。

Sentinel 做：

- 消费恭喜发财数据源引擎整理后的新闻和上下文。
- 判断主题热度、事件链、候选池证据、风险提示。
- 生成辩论输入包和研究报告。

Sentinel 不做：

- 不直接抓网页。
- 不直接读取 Horizon raw 作为正式输入。
- 不越过裁判和风控。

## 6. 风险和控制

| 风险 | 控制方式 |
| --- | --- |
| 命名混乱 | 新模块叫 Sentinel，辩手 Serenity 保留 |
| 多入口数据冲突 | Horizon 新闻必须先进入数据源引擎 |
| 高频新闻噪音过多 | 原始抓取高频，研究报告低频 |
| 误导交易 | Sentinel 禁止输出买卖指令 |
| 影响旧流程 | 先旁路接入，默认可关闭 |
| 凭据泄露 | 输出文件禁止包含 token/cookie/header |
| 财务数据不足 | 标记 unavailable，不伪造结论 |

## 7. 已确认项

以下方向已确认：

- M0 到 M6 的阶段顺序合适。
- 先做文件契约和旁路接入，再做自动化。
- Sentinel 输出只作为辩论材料和研究证据。
- Horizon 仅作为新闻采集器。
- 旧 Serenity 研究代码采用渐进迁移，不立即大重命名。

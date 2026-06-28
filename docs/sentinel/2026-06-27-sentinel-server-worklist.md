# Sentinel Server 端模块更新工作清单

> 日期：2026-06-27
> 状态：已确认，按此推进
> 目标：为恭喜发财后端接纳 Sentinel 研究证据层建立清晰 work list

## 1. 当前状态

恭喜发财后端已有这些相关能力：

- `backend/app/data_sources/`：Tushare、腾讯行情、东方财富、AKShare 等数据源。
- `backend/app/ai/debate.py`：四角色辩论入口。
- `backend/app/ai/serenity_analyst.py`：旧 Serenity 研究实现，当前会与辩手 Serenity 命名冲突。
- `backend/app/ai/serenity_evidence.py`：行情证据辅助。
- `backend/app/ai/serenity_financial_evidence.py`：财务证据辅助。
- `backend/app/report_engine/`：报告生成。
- `backend/app/trading_engine/`：账户、撮合、风控和信号。

本 work list 的目标不是直接删除旧 Serenity 代码，而是先把新边界搭出来：旧代码逐步迁移到 Sentinel 命名和接口下，辩手 Serenity 保持辩论角色。

## 2. 阶段一：命名和边界对齐

- [ ] 确认产品命名：新研究证据层统一叫 Sentinel。
- [ ] 保留四名辩论员中的 Serenity 名称，作为产业链研究辩手。
- [x] 新增 `docs/sentinel/` 文档目录。
- [ ] 在 README 或架构文档中注明：Sentinel 是研究证据层，不是第五名辩手。
- [ ] 梳理旧 `serenity_analyst.py` 中哪些逻辑属于研究证据层，哪些属于辩手视角。
- [ ] 制定迁移规则：代码可逐步迁移，文件命名最终从 `serenity_*` 收敛到 `sentinel_*`。

验收标准：

- 文档中不再把新增研究模块称为 Serenity。
- “Serenity 辩手”和“Sentinel 研究引擎”职责不冲突。
- 没有任何文档暗示 Sentinel 可以直接下单。

## 3. 阶段二：统一输入契约

- [x] 新增 Sentinel 输入 schema 文档。
- [x] 定义 `news_events.jsonl` 字段：`id`、`source`、`channel`、`published_at`、`fetched_at`、`content`、`is_key`、`symbols`、`themes`、`dedupe_key`、`raw_hash`。
- [x] 定义 `market_snapshot.json` 字段：交易日、指数、板块、个股行情、成交额、涨跌幅、涨速。
- [x] 定义 `portfolio_snapshot.json` 字段：现金、总资产、持仓、成本、可用仓位、账户约束。
- [x] 定义 `candidate_pool.json` 字段：主题、标的、候选理由、现有评分、人工备注。
- [x] 定义 `financial_evidence.json` 字段：收入、利润、毛利率、现金流、资产负债、估值指标、接口状态。
- [x] 定义 `risk_context.json` 字段：黑名单、仓位上限、单票上限、交易日状态、不可操作条件。
- [x] 明确 Sentinel 只读取数据源引擎生成的统一输入目录。

验收标准：

- 每个输入文件都有字段说明、必填项、可选项和缺失时的降级行为。
- 输入契约不包含 cookie、token、authorization header。
- Horizon 的原始新闻不能直接成为 Sentinel 正式输入，必须经过数据源引擎整理。

## 4. 阶段三：数据源引擎接纳 Horizon 新闻

- [x] 新增 Horizon 新闻导入器，读取司库固定目录下的 TusharePro 新闻 JSONL。
- [x] 支持按日期读取新闻。
- [x] 对新闻进行稳定去重。
- [x] 对新闻补充数据源引擎自己的入库时间。
- [x] 尝试从正文提取 A 股代码、公司名、行业和主题。
- [x] 标记新闻证据状态：`enriched`。
- [x] 将清洗后的新闻写入统一输入包 `news_events.jsonl`。
- [ ] 支持按来源和时间窗口读取新闻。
- [ ] 增加 `raw`、`deduped`、`rejected` 的完整状态流转。

当前导入器位置：

```text
backend/app/data_sources/horizon_news_importer.py
```

验收标准：

- 重复抓取同一条新闻不会重复进入 Sentinel 输入。
- 读取失败时不影响原有盘前、午间、收盘流程。
- 新闻导入失败要留下明确错误，不伪造空结论。

## 5. 阶段四：Sentinel 研究引擎

- [ ] 新增 Sentinel 核心模块，例如 `backend/app/ai/sentinel_engine.py`。
- [ ] 支持读取统一输入包。
- [ ] 生成主题雷达：主题、热度、来源数、关键新闻、行情确认、风险因素。
- [ ] 生成候选池 review：支持证据、反对证据、风险标签、待核验问题。
- [ ] 生成盘中提醒：事件、相关主题、相关标的、建议动作类型。
- [x] 明确建议动作类型只允许：`watch`、`verify`、`downgrade`、`risk_up`、`ignore`。
- [x] 禁止输出交易动作：`buy`、`sell`、`clear`、`all_in`。

验收标准：

- Sentinel 可以在无新闻、无财务、无行情任一缺失情况下给出降级报告。
- Sentinel 输出能被 JSON schema 校验。
- Sentinel 输出中不出现直接交易指令。

## 6. 阶段五：辩论系统接入

- [ ] 在辩论入口增加可选 Sentinel 证据包参数。
- [ ] 四名辩论员共享同一份 Sentinel 证据包。
- [ ] 猎手读取事件时间线和行情确认。
- [ ] 账房读取财务证据和估值风险。
- [ ] 守夜人读取负面新闻和风险标签。
- [ ] Serenity 辩手读取产业链主题、供需缺口和瓶颈证据。
- [ ] 裁判读取 Sentinel 摘要，但最终决策仍以辩论、账户和风控为准。

验收标准：

- 没有 Sentinel 证据包时，旧辩论流程仍可运行。
- 有 Sentinel 证据包时，辩论报告能显示“引用了哪些证据”。
- 裁判输出仍通过风控模块约束。

## 7. 阶段六：报告与归档

- [ ] 新增 `sentinel_debate_packet.md` 渲染模板。
- [ ] 新增 `sentinel_daily_research.md` 归档模板。
- [ ] 将 Sentinel 研究报告归档到司库数据采集目录。
- [ ] 报告 frontmatter 增加 `source`、`collection`、`module`、`date`、`verification_status`。
- [ ] 飞书推送默认只推摘要，不推原始长新闻流。

验收标准：

- Markdown 报告可以被 Obsidian 正常索引。
- 报告中明确区分事实、推断、待核验。
- 报告不包含敏感凭据。

## 8. 阶段七：测试和回归

- [x] 单测 Horizon 新闻导入器的去重和字段清洗。
- [x] 单测 Sentinel 输入 schema 的必填字段。
- [x] 单测 Sentinel 输出不包含交易指令。
- [ ] 单测无新闻、无行情、无财务的降级路径。
- [ ] 回归原有日报、盘前、收盘报告流程。
- [ ] 回归四角色辩论在无 Sentinel 情况下仍可运行。
- [ ] 回归风控模块仍是交易建议前的强约束。

验收标准：

- Sentinel 新测试通过。
- 原有与报告、辩论、风控相关的测试通过。
- 代码 diff 不影响成熟交易管线的默认行为。

## 9. 暂不做事项

第一版不做：

- 不做自动交易接入。
- 不做直接读取 Chrome 登录态。
- 不做 cookie/token 持久化。
- 不做分钟级飞书刷屏。
- 不把 Horizon 变成策略分析器。
- 不让 Sentinel 取代四名辩论员或裁判。

## 10. 审核问题

已确认：

- 先做输入输出契约，再做代码迁移。
- 旧 `serenity_*` 代码采用渐进迁移，不一次性大重命名。
- Sentinel 对辩论系统采用可选接入，保证旧流程可回退。
- Horizon 新闻先进入数据源引擎，再进入 Sentinel。

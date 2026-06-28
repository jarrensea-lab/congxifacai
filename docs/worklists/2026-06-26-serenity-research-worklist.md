# Serenity 研究流水线工作清单

> 当前推进分支：`codex/serenity-research-next`

## 目标

把 Serenity 从日报辩论角色推进成可独立运行、可测试、可归档、证据驱动的产业链瓶颈研究流水线。Serenity 只做研究和证据核验，不直接生成交易指令。

## 已完成

- [x] 独立 Serenity 8 维评分模型。
- [x] 红旗信号扩展。
- [x] 主题别名与候选池。
- [x] 候选池外置到 `data/serenity/theme_candidates.json`。
- [x] 证据核验任务骨架。
- [x] 行情证据自动化。
- [x] 独立报告生成。
- [x] CLI 报告入口。

## 下一轮迭代状态

### 10. 把行情证据转成评分调整

- [x] 一手金额超过可用现金时降低 `下行安全`。
- [x] PE/PB 极端时触发估值红旗。
- [x] 成交额为 0 或缺失时削弱流动性证据。
- [x] 实现 `adjust_scores_with_quote_evidence()`。
- [x] 给每个候选标的附加评分调整原因。
- [x] 在报告中展示评分调整原因。
- [x] 验证报告仍然不包含交易指令。

### 11. 接入财报证据

- [x] 新增 `backend/app/ai/serenity_financial_evidence.py`，财报证据不塞进 `serenity_analyst.py`。
- [x] `TushareDataSource` 支持读取 Tushare 包已保存 token，不要求项目 `.env.local` 必须配置。
- [x] 避免 `ts.set_token()` 写入用户 home，改为 `ts.pro_api(token)` 直接使用 token。
- [x] 财报证据 fetcher 优先 Tushare，补充 AKShare，最后回落本地 cache。
- [x] 增加测试，覆盖收入、毛利率、存货、应收、现金流证据对评分和红旗的影响。
- [x] 把财务证据转成 `证据强度`、`传导清晰度`、`下行安全` 调整。
- [x] 应收和存货增速超过收入时触发 `inventory_receivable_growth`。
- [x] 声称瓶颈但毛利率没有改善时触发 `margin_not_improving`。
- [x] 在报告中增加财务证据摘要和财务评分调整原因。
- [x] 实测当前 Tushare token 可识别，但账号当前没有财报接口权限；报告会标记 `verification_status: unavailable`，不伪造财报结论。

### 12. 候选池维护流程

- [x] 为 `data/serenity/theme_candidates.json` 增加 schema 校验。
- [x] 增加测试，覆盖候选标的缺少必填字段。
- [x] 增加测试，覆盖评分维度非法。
- [x] 新增候选池校验脚本 `scripts/validate_serenity_candidates.py`。
- [x] 新增安全添加主题/候选脚本 `scripts/add_serenity_candidate.py`。
- [x] 文档化候选池编辑规则。
- [x] 在测试中运行候选池校验。

### 13. 接入司库知识库

- [x] 默认归档目录改为司库数据采集区：`/Volumes/Aino Kishi/AI/projects/司库/01-资料采集/量化投资/Serenity研究`。
- [x] 支持 `SERENITY_SIKU_ARCHIVE_DIR` 覆盖目标目录。
- [x] 增加报告 metadata，方便司库索引。
- [x] 增加稳定标签：`Serenity`、`产业链瓶颈`、标准化主题。
- [x] 为 Serenity 报告设置独立归档标题。
- [x] 增加测试，覆盖 metadata 和数据采集归档目标。

### 14. 运行边界检查

- [x] 确认 Serenity 不进入主日报交易决策权。
- [x] 增加测试，确认日报导入不依赖 Serenity 候选池 JSON。
- [x] 增加测试，确认 Serenity 行情失败不会影响日报。
- [x] Serenity 默认不推送飞书。
- [x] 文档化手动运行方式：不带行情、带行情、带财报证据。

## 剩余注意事项

- [ ] 如果需要 Tushare 财报实证研究，需要在 Tushare 账号侧开通 `income`、`balancesheet`、`cashflow` 或 `fina_indicator` 权限。
- [ ] 如果希望不用 Tushare 财报权限，可安装并验证 AKShare 后，把公开财报接口作为第一真实来源。
- [ ] 后续可补 `data/serenity/financial_evidence.json` 的人工证据模板，用于接口不可用时做可追踪缓存。

# 恭喜发财路线图

> 最后更新：2026-07-26 | 当前 feature：v8.2.0-dev

## 当前已经落地

- [x] 主报告第一屏以持仓处置和次日动作优先；默认渲染已委托给 `backend/app/report_engine/templates/next_day.py`，`scripts/daily_report.py` 仍保留 `CONGXI_REPORT_LEGACY_SECTIONS=1` 控制的 legacy 大模板/兼容渲染。
- [x] Target Pool 区分 `executable`、`watching`、`research_reference`、`removed`，并执行最小交易单位、账户预算、剧本和风控阻断。
- [x] APScheduler 作业已集中到 `backend/app/services/scheduler_service.py`，`backend/app/main.py` 只注入处理函数和启动一次调度服务。
- [x] Sentinel 在周一至周五和周日 20:00 生成研究证据，Serenity 深挖随后物化到 Long Thesis、Evidence Ledger 和 Target Pool；全链路保持 `research_only`。
- [x] 中线/长线建议不再整段缺席，完整状态机为 `unknown`（无 thesis/空输入的未建论文展示）、`forming`（证据形成中）、`healthy`（假设有效且未过期）、`stale`（证据过期）、`weakened`（假设弱化）、`broken`（红线或核心假设失败）。所有状态只约束研究和风险，不等于交易授权。
- [x] 外部分钟数据和 Tushare 复权因子进入 shadow 数据层；支持年度汇总、日档月归档、沪深北股票和前复权，且不复制、不整包解压。
- [x] 离线覆盖不足可回退到配置 provider；ZIP、路径、时间戳、数值、重叠数据或复权因子损坏一律 fail-closed。
- [x] DeepSeek/Qwen 按运行时配置路由；Qwen 缺失会留下显式 fallback 元数据。fallback 成功、角色/裁判与 validator 输出可用且质量通过时，即使汇总状态是 runtime `degraded` 也可通过生产门；provider 不可用、角色/裁判输出降级/报错、validator 缺失/不可用或质量失败才阻断。
- [x] 易淘金桥接限定为持仓、自选和重点行情；普通自选同步需要独立开关，永不转化为下单、撤单或转账权限。

## P0：下一阶段必须完成

- [ ] 建立数据源覆盖率和新鲜度看板，把连续缺口、fallback 次数和完整性失败原因写入报告并推送运维告警。
- [ ] 用已登记的离线分钟档案跑固定时间窗的 shadow 基准，形成按股票、周期、年份和复权覆盖率的可审计清单。
- [ ] 完成易淘金用户在场的只读验收和普通自选 dry-run；在验收完成前保持两个开关默认关闭。
- [ ] 把预测回看、真实执行复盘和策略参数建议放到同一审计页；任何自动调参先保持 shadow。
- [ ] 拆分 Serenity 大文件中的候选提取、评分、财务桥接和报告渲染，减少研究层耦合。
- [ ] 拆出 `scripts/daily_report.py` 中由 `CONGXI_REPORT_LEGACY_SECTIONS=1` 启用的 legacy 大模板；默认动作优先路径已完成，兼容路径仍需去重。

## P1：盈利闭环增强

- [ ] 按市场状态、剧本、持有周期和触发理由统计样本外胜率、盈亏比、最大回撤和超额收益；样本不足时明确标记，不给伪精确结论。
- [ ] 为 `unknown`、`forming`、`healthy`、`stale`、`weakened`、`broken` 长期状态建立季度复核队列和过期提醒，继续保持 research-only。
- [ ] 对 DeepSeek 主路由、Qwen 主路由和 fallback 路由做同样样本集的质量/成本/延迟评估，以运行证据决定路由，不按模型名称假设能力。
- [ ] 评估实时行情 WebSocket 或更稳定的授权行情源；在替换现有轮询前保留多源价差和新鲜度门。
- [ ] 增加前端复盘仪表盘，统一呈现持仓动作、候选生命周期、预测到期和中长期证据状态。

## 明确不在当前授权范围

- 自动下单、撤单、转账或读取交易凭证。
- 把 Sentinel/Serenity 研究线索直接提升为买入建议。
- 把离线分钟档案用于实时开仓门或替代实时行情。
- 用未验证的 AI 结果、覆盖不足数据或损坏数据写入生产候选池。
- 承诺收益率、胜率或“保证赚钱”。

## 验证基线

- 当前后端完整基线：`1102 passed`（2026-07-26）。
- 后端测试、Ruff 和 `compileall` 必须在修改交易、预警、报告、账户或调度逻辑后通过。
- 真实报告验证必须使用 `CONGXI_PORTFOLIO_PATH`、`CONGXI_CANDIDATE_POOL_PATH`、`CONGXI_REPORT_ARCHIVE_DIR` 临时副本。
- 易淘金验收必须由用户在场，先只读、再 dry-run、最后才允许受控普通自选写入；任何阶段都不包含交易权限。

## 主要风险

| 风险 | 影响 | 当前缓解 |
|------|------|----------|
| 数据源缺失或陈旧 | 错过信号或产生错误动作 | 新鲜度门、覆盖状态、显式 fallback、完整性错误 fail-closed |
| 模型路由或输出失败 | 多角色观点不完整 | 区分成功 fallback 与输出失效；按角色/裁判、validator 和质量门 fail-closed |
| 离线大数据损坏或混入未来数据 | 回测失真 | shadow-only、归档边界、时间戳/重叠/复权完整性校验 |
| 中长期证据过期或假设失效 | 旧 thesis 误导决策 | 六态状态机、红线和复核任务；任何状态不等于交易授权 |
| 券商 UI 变化或误操作 | 自选同步错误 | 默认关闭、账户指纹、系统管理清单、双次确认、用户在场验收 |
| 小账户高收益试验波动大 | 资金回撤 | 硬止损、单笔风险预算、账户回撤线；不承诺收益 |

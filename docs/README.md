# 恭喜发财 · 知识库

> A股智能监控系统的结构化知识库，为系统迭代优化、AI 策略分析、手动交易决策提供参考。

## 目录导航

### 方案搭建
- [系统架构设计](architecture/system-design.md) — 整体架构设计决策、技术栈选型
- [多模型分工策略](architecture/model-routing.md) — Ollama 多模型角色分配与内存优化
- [数据流架构](architecture/data-flow.md) — 数据从采集到 AI 分析的完整链路

### 数据源获取
- [腾讯财经 API](data-sources/tencent-finance.md) — 实时行情、K线、批量查询
- [东方财富 API](data-sources/eastmoney.md) — 板块排名、涨跌分布、市场宽度
- [新浪财经 API](data-sources/sina-finance.md) — 备用数据源、实时报价
- [多源容错策略](data-sources/multi-source-routing.md) — 首选→备选→熔断机制

### Agent 提示词（核心）
- [辩论引擎提示词](prompts/debate-prompts.md) — 猎手/账房/守夜人/裁判 四角色完整提示词
- [分析提示词](prompts/analysis-prompts.md) — 盘前/盘中/复盘/个股建议 四大分析提示词
- [提示词优化日志](prompts/prompt-iterations.md) — 提示词迭代记录（手动投喂）

### 投资策略
- [短线策略体系](strategies/short-term.md) — 1-5天短线交易策略框架（手动投喂）
- [中低频策略体系](strategies/mid-low-freq.md) — 1-4周波段交易策略框架（手动投喂）
- [风控策略](strategies/risk-management.md) — 6维度加权评分风控体系
- [策略日志](strategies/strategy-journal.md) — 策略实践记录（手动投喂）

### 操盘知识
- [技术指标速查](trading-knowledge/technical-indicators.md) — MA/RSI/MACD/PE/PB/量比/换手率/振幅
- [买卖原则](trading-knowledge/entry-exit-rules.md) — 入场条件与出场纪律（手动投喂）
- [仓位管理](trading-knowledge/position-management.md) — 仓位分配与加减仓规则（手动投喂）
- [交易心理](trading-knowledge/trading-psychology.md) — 心态管理与纪律执行（手动投喂）
- [量化交易课程学习报告](trading-knowledge/course-learning-report.md) — 关东升《DeepSeek与量化交易》15章完整知识点总结

### 收益规律分析
- [规律发现日志](profit-patterns/pattern-journal.md) — 量价关系、季节性、资金流规律（手动投喂）

### 系统运维
- [迭代优化记录](system-ops/iteration-log.md) — 系统版本迭代与优化决策
- [模型配置说明](system-ops/model-config.md) — Ollama 模型配置与参数
- [常见问题排查](system-ops/troubleshooting.md) — 运行问题与解决方案

### 参考资料
- [股票术语表](resources/stock-terminology.md) — A股交易常用术语
- [学习资源推荐](resources/reading-list.md) — 书籍、文章、工具推荐

---

## 使用指南

### 对 AI Agent
- 分析任务前，先查阅 `prompts/` 了解当前提示词设计
- 风控判断时，参考 `strategies/risk-management.md` 的评分体系
- 模型调用时，遵循 `architecture/model-routing.md` 的角色分配

### 对手动交易
- 盘前参考 `strategies/short-term.md` 和 `strategies/mid-low-freq.md` 的策略框架
- 盘中决策参考 `trading-knowledge/entry-exit-rules.md` 的买卖纪律
- 盘后总结写入 `strategies/strategy-journal.md`

### 对系统迭代
- 修改提示词后，记录到 `prompts/prompt-iterations.md`
- 发现新规律时，更新 `profit-patterns/pattern-journal.md`
- 架构变更时，更新 `architecture/system-design.md`

---

> 标注「手动投喂」的文件需要你根据实际交易经验逐步填充。
> 标注「自动提取」的内容来自项目源文件，随代码同步更新。

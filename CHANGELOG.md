# 恭喜发财 更新日志


## v7.1.0 (2026-06-16) — Serenity 产业链研究员实装 + 现金约束 + 推荐优化

### 新功能
- ✅ **Serenity·产业链研究员正式加入辩论**：第 4 个 AI 辩论角色完整实装
  - `SERENITY_PROMPT` — 产业链深度分析 prompt（6 维度：图谱解构/供需缺口/技术壁垒/竞争格局/全球视野/政策传导）
  - `_researcher_model()` — Qwen-Plus 路由，提供与 DeepSeek 猎手/账房/守夜人不同的模型视角
  - `cloud-researcher` / `qwen-researcher` — 双路由支持（DeepSeek 降级 / Qwen-Plus 主用）
  - 4 角色并行辩论 → 裁判(Qwen-Plus)聚合
- ✅ **serenity_analyst.py** — 产业链知识引擎（586 行）：8 层价值链地图、16 个 A 股主题卡点、7 维评分卡、10 个红旗信号检测
- ✅ **全流程跑通**：数据抓取 → 4 角色辩论(含 Serenity) → 策略生成 → 飞书推送

### 优化
- ✅ **现金约束实装**：修复了 `available_cash`（当前 ¥3,165.72）在数据管道中被丢弃的 bug
  - `_fetch_market_data()` → `analysis.py` → `workshop.py` → `debate.py` 全链路透传
  - 所有角色 prompt 现在可读取真实账户余额进行仓位约束
- ✅ **Serenity 使用 Qwen-Plus**：多模型多样性（3 DeepSeek + 2 Qwen-Plus）
- ✅ **推荐方向区分**：所有角色 prompt 强制输出「买入候选」和「卖出/规避候选」
- ✅ **现价比对**：每支推荐标注当前价相对于买入区间的位（上沿/下沿/区间内）
- ✅ **辩论记录扩展**：`debate_results` 表新增 `researcher_decision` + `researcher_conviction` 列

### 技术细节
- `backend/app/ai/debate.py` — SERENITY_PROMPT + 4 角色并行 + 模型路由
- `backend/app/ai/serenity_analyst.py` — 产业链知识引擎（NEW）
- `backend/app/ai/cloud_client.py` — Qwen-Plus 路由延伸
- `backend/app/engine/workshop.py` — 现金注入 + ask_role 研究员路由
- `backend/app/engine/analysis.py` — 原始数据透传
- `backend/app/models.py` — DebateResult 扩展
- `backend/app/engine/debate_tracker.py` — 研究员快照保存
- `backend/main.py` — _fetch_market_data() cash 透传

---
# 恭喜发财 更新日志


## v7.0.0 (2026-06-15) — Serenity 产业链分析 + GitHub 发布

### 🎯 核心变化

| V6 | V7 |
|-----|-----|
| 三角色辩论（猎手/账房/守夜人） | 四角色辩论（+产业链研究员） |
| 纯技术面+基本面分析 | 增加 Serenity 式产业链瓶颈分析 |
| 本地开发 | GitHub 开源发布 |

### 🆕 新功能

- **产业链研究员角色**: 第 4 个 AI 辩论角色，实现 Serenity 式供应链瓶颈分析方法论
- **8 层产业链价值链地图**: 终端客户→系统集成→模组→芯片→工艺→设备→材料→基础设施
- **16 个 A 股主题卡点**: AI半导体/CPO光通信/机器人/AI基建等常见主题的典型瓶颈环节
- **7 维评分卡**: 需求确定性/传导清晰度/业务纯度/市值弹性/市场忽视度/验证速度/下行风险
- **10 个红旗风险信号**: 单一客户依赖/社交媒体炒作/融资风险/存货应收异常/毛利率不改善等
- **证据等级分级**: 强证据/中等证据/弱证据三级体系
- **盘前卡点预检**: 开盘前扫描产业链卡点，注入辩论引擎
- **飞书报告卡点章节**: 策略报告新增「🔬 产业链卡点分析」区块

### 🔧 改进

- 辩论引擎从 3 角色升级为 4 角色并行调用
- 裁决器现在综合四角色观点
- 启动时自动补跑错过的盘前任务
- 测试覆盖从 57 → 76 个（+20 个 serenity 专项测试）

### 🐛 Bug 修复

- 修复 _catchup_missed_jobs 函数名引用错误
- 修复 workshop.py 决策字典缺少卡点字段的问题

### 📦 发布

- GitHub 仓库: https://github.com/jarrensea-lab/bcku
- 分支: main
- 许可证: MIT

---

## v6.1.0 (2026-06-05) — 机器人指令 + AI 辩论引擎 + 数据准确性修复

### 机器人集成
- **飞书机器人指令系统**: 支持 `持仓`/`买入`/`卖出`/`清仓`/`生成策略` 五条指令
- 桥接 v8 拦截层：bot_commands 进程内调用，消除子进程延迟
- 指令通过飞书桥接实时收发，替代 Web 前端交互

### AI 辩论引擎升级
- **DeepSeek-R1 裁判**: 前裁判用 deepseek-chat 常出空 JSON，切换为 deepseek-reasoner (R1) 推理模型
- 四角色模型矩阵: 猎手/账房/守夜人=deepseek-chat, 裁判=deepseek-reasoner
- 新增 `生成策略` 指令，触发三角色并行辩论 → R1 裁判聚合 → 自动上传飞书多维表格
- 辩论耗时 ~50s（含 R1 40s 链式思考）

### 数据准确性修复 (CEO 审查 P0)
- **avg_cost 精度修复**: 从 `//` 整数截断改为 `round()` 四舍五入（position.py）
- **费率更新**: 佣金从 0.025% 降为 0.015%，印花税从 0.1% 降为 0.05%（2023年新规）
- **北交所补全**: fee_schedule.get_board_type 增加 4xxxx 代码段识别
- **涨跌停风控激活**: order_manager 从空 try/except 改为基于板块限额的真实计算
- **盘中分析修复**: debate_intraday 字段名 response→content，修复完全失效问题

### 架构安全
- DB Session 泄漏修复：lifespan shutdown 遍历 5 个全局单例关闭 _db
- CloudClient HTTP 连接正确关闭
- TieredCache 增加 threading.Lock 线程安全
- 定时任务增加 max_instances=1 互斥保护
- afternoon status key 独立（不再复用 intraday）
- 持仓数据改用 Position 表（不再从 TradeLog 聚合）

### 飞书集成
- 策略自动上传飞书多维表格（ObTFbBmVMauqE2sBS9ccopvHnme）
- 桥接 v8 增加 lark-cli 全路径引用 + launchd 自动重启守护
- feishu_channels 路径配置化（FEISHU_BRIDGE_PATH + LARK_CLI_PATH）

### 单元测试
- 新增 test_fee_schedule.py (14个用例, 全通过)
- 新增 test_risk_guard.py (11个用例, 全通过)
- pytest-mock 依赖已安装

### 已知限制
- DataRouter 已实现但未接入 main.py（已在 v6.2.1 接入）
- ~~monitor.py 进程守护待从 v5 迁移 → 已接入 (v6.2.3)~~
- 仅 deepseek-chat 参与辩论，未引入多模型多样性
- 数据源无健康检查和交叉验证（已在 v6.2.1 增加 DataRouter 健康检测）


## v6.2.0 (2026-06-08) — 辩论质量追踪 + 安全加固 + 撮合引擎修复

### 辩论质量追踪系统（完整版）
- **新表 DebateResult**: 记录每场辩论四角色（猎手/账房/守夜人/裁判）的判断快照与市场状态
- **debut_tracker 引擎**: `save()` 辩论结束自动保存 → `fill_pending()` 5日/20日后回填推荐标的实际收益 → `get_performance_summary()` 按市场状态统计准确率
- **裁判 prompt 动态注入**: 辩论时查询历史表现（如"猎手近期胜率 46%"，"裁判方向正确率 80%"），让裁判参考历史权重做最终裁决
- **数据积累期自动降级**: 前 5 天无数据时 performance 为空串，行为与 v6.1.0 完全一致

### 安全加固 (P0)
- **飞书 Bot 授权白名单**: [bot_commands.py] 新增 `FEISHU_ALLOWED_USERS` 配置，每条消息校验 sender open_id，非授权用户直接拒绝，默认空列表全局锁死
- **手动卖出空仓检查**: [trading.py] create_manual_order 卖出前检查持仓是否存在且数量 > 0

### 模拟撮合引擎修复 (P0)
- **[broker.py] 完整实现 execute_market_order**: 滑点(±0.1%) → 涨跌停检查 → 最小交易单位取整 → 完整费用计算（佣金/印花税/经手费/证管费）→ 成交回执
- **[broker.py] 完整实现 execute_limit_order**: 限价条件判断 + 上述全流程
- 之前两个方法主体为空，所有订单进入 rejection 分支

### 风控体系加固
- **[risk_guard.py] Gate 10 — 受托人检查**: 买入前估算最大损失（假设跌停卖出），单笔不超过总资产 3%
- **10-Gate Pipeline**: 交易时间 → 涨跌停 → T+1 → 仓位上限 → 日内亏损 → 最大回撤 → 交易频率 → 可用资金 → 板块集中度 → 受托人检查

### 报告结构优化
- **[report_templates.py] 风险优先结构**: 报告顺序改为 "风险预警 → 市场背景 → 仓位管理 → 短线机会 → 中线机会 → 回测参考"
- **反方论证段落展示**: 裁判输出中的自我反方检查，在报告中可视化呈现

### 辩论提示词优化
- **[debate.py] 裁判加入反方自查**: 输出最终决策前先自问"如果错了最可能的原因是什么"+"共识是否已充分反映"，并将反方论证写入 reasoning
- 零额外 API 调用，仅修改 prompt 文本

### 调度与稳定性
- **[main.py] APScheduler 持久化 jobstore**: 从内存 AsyncIOScheduler 切换为 `SQLAlchemyJobStore`（复用 SQLite），重启后恢复未执行任务，宕机不丢任务
- **Pydantic v2 / SQLAlchemy 2.0 弃用警告已清除**: class Config → SettingsConfigDict, sqlalchemy.ext.declarative → sqlalchemy.orm

### 测试覆盖率
- 25 个已有测试全部通过（test_fee_schedule.py 14 个 + test_risk_guard.py 11 个）
- test_pipeline_all_pass 新增 check_fiduciary mock 适配 10-Gate 管道
- broker 单元测试覆盖：市价买入/卖出、T+1 拒绝、涨停拒绝、限价条件、不足最小单位

### 知识库
- **docs/books/** 新增三本经典投资书籍核心原则提炼:
  - `聪明的投资者-格雷厄姆-核心原则.md`: 防御型/进攻型投资者、安全边际、市场先生
  - `投资最重要的事-霍华德马克斯-核心原则.md`: 第二层次思维、钟摆理论、防御型投资
  - `文明现代化价值投资与中国-李录-核心原则.md`: 价值投资四原则、受托人责任、逆向思维
- 提取的 20+ 条投资原则已映射到系统架构（辩论逻辑/风控/仓位/报告），5 项已落地



## v6.2.1 (2026-06-09) — 死代码清理 + 事务回滚 + DataRouter 接入

### 死代码清理
- **移除 Ollama 死代码模块**: 删除 `client.py`、`fallback.py`、`ollama_pool.py` 三个文件
- **config.py 精简**: 移除 `OLLAMA_KEEP_ALIVE`、`OLLAMA_FALLBACK`、`OLLAMA_BASE_URL` 等 6 个 Ollama 配置项
- **移除所有 Ollama 引用**: `main.py`、`lifecycle.py`、`debate.py`、`trading.py` 中的 import/init/close/shutdown
- **代码生成切换至 DeepSeek**: `trading.py` 的策略代码生成从 `ollama_pool.generate("coder")` 迁移到 `cloud.chat("analyst")`
- **清理 `test_reports.py`**: 移除 `test_ollama` 函数及其引用

### DataRouter 正式接入
- **main.py 集成 DataRouter**: 导入 `DataSourceRouter` 并实例化，所有市场数据拉取走多源容错路径
- **熔断保护**: DataRouter 内置指数退避 + 熔断绕过

### 事务稳定性
- **order_manager 事务回滚**: `create_from_signal` 的 submitted→filled 流程增加 `try/except/rollback` 保护
- **异常路径记录**: 回滚后创建 status=error 的订单记录，保留失败原因
- **修复缺失 import**: 补上 `get_price_limit_pct` 的 import（原代码有使用但未导入）

## v6.0.0 (2026-06-04) — Codex 自动化 + 飞书全通道

### 架构重构
- ❌ 砍掉 `frontend/` 整个目录 (Vue3+Vite+Pinia, 5394行)
- ❌ 移除 CORS/WebSocket/StaticFiles/SPA fallback
- ❌ 移除 monitor.py (WebSocket 监控)

### AI 引擎
- 全切 DeepSeek v4-pro + v4-flash 云端 API
- 保留 qwen3.5:3b 作为极简 fallback (仅格式化降级提示)
- 辩论文本三角色并行调用 (原串行 ~150s → ~30s)
- 新增 fallback.py 优雅降级模块

### 数据源
- 移除 Sina 从 data_router (缺PE/PB)
- 移除 Eastmoney fetch() 死人源
- AKShare 标记待替换 (Phase 2 后续用 a-stock-data)
- data_router: Tushare → Tencent 双源

### AI 分析增强
- 新增 position_plan: 分批建仓计划 (阶段/价格/条件/止盈止损)
- 新增 backtest_summary: 回测指标 (胜率/收益/夏普/最大回撤)
- 新增 backtest_engine.py: MA金叉死叉 + 动量策略回测
- 新增 knowledge_corner 知识角教学输出

### 飞书全通道
- 新增 feishu_channels.py: lark-cli 多通道封装
- 消息卡片: 盘前策略/风险预警/午盘简报/系统日报
- 多维表格: 选股池/回测数据/持仓绩效
- 飞书文档: 策略报告/复盘总结
- 画板: K线标注图/收益曲线
- 任务: 止盈止损提醒

### 自动化守护
- 新增 guardian.sh: 进程守护 + 健康检查 + 自修复 + 日报
- 每30分钟检查 API/DeepSeek/Ollama 状态
- 异常自动重启 (最多5次)
- 15:30 自动生成运行日报

### 配置简化
- requirements.txt 精简注释
- config.py: OLLAMA_FALLBACK 替代复杂多模型矩阵
- tiered_cache: L1 60s + L2 600s

### 移除
- frontend/ (35个文件)
- backend/app/services/monitor.py
- report_saver 桌面报告逻辑


## v4.1.0 (2026-06-01) — 路由模块化 & AI 架构统一

### 路由模块化
- **main.py 瘦身**：从 ~1000 行精简到 ~200 行，路由逻辑拆分到独立模块
- **新建 `routers/` 包**：strategy.py（策略生命周期 API）、trading.py（交易执行 API）、market.py（行情数据 API）

### AI 架构升级
- **统一 AI 入口**：`debate.py` 从直接实例化 `OllamaClient` 切换为 `OllamaPool` 统一入口
- **模型解析统一**：新增 `resolve_model()` 函数，所有 AI 调用统一通过此函数解析模型名称
- **Settings 覆盖机制**：`.env.local` 可覆盖模型名称，空值自动 fallback 到 `OLLAMA_MODELS` 默认角色模型
- **OllamaPool 增强**：支持 `temperature`、`num_predict`、`num_ctx`、`top_p`、`top_k` 等选项透传
- **OLLAMA_BASE_URL**：从硬编码改为从 `settings.OLLAMA_BASE_URL` 读取，支持自定义 Ollama 服务地址

### 配置系统强化
- **安全校验**：`get_settings()` 启动时检查 `DEEPSEEK_API_KEY` 和 `FEISHU_WEBHOOK_URL` 配置状态，未配置时输出警告
- **keep_alive 优化**：从 300s 降至 120s，降低多模型同时驻留显存概率
- **模型真相源**：`OLLAMA_MODELS` 字典作为唯一默认真相源，`Settings` 字段支持覆盖

### 数据层修复
- **持仓数据修正**：`MonitorService.get_active_holdings()` 从 `TradeLog` 聚合改为 `PositionManager.get_holdings_codes()` 表查询
- **交易日历**：新增 `app/utils/trading_calendar.py`，内置 2025-2026 A 股节假日休市日期
- **数据库**：`backend/data/stock_data.db` 新增 SQLite 数据库文件

### 依赖补充
- **akshare**：新增数据源 SDK
- **numpy / pandas**：补充量化计算依赖

---

## v4.0.0 (2026-05-29) — 旺财V4：策略生命周期引擎

### 项目更名
- **「恭喜发财」→「旺财V4」**：取「知行合一」理念，策略生命周期闭环

### 架构升级
- **策略生命周期引擎**：新建 `engine/` 包，串联 6 个阶段（分析研判→策略工坊→执行规划→持仓执行→绩效回顾→每日审查）
- **核心数据模型**：新增 `StrategyInstance`（策略实例）、`ReviewLog`（审查日志）、`UserPreference`（用户偏好），贯穿策略全生命周期
- **旧模型关联**：`TradeLog`、`TradingSignal`、`AIStrategy`、`RiskAlert` 新增 `strategy_instance_id` FK 字段

### 模型矩阵
- **本地 Ollama（7 模型）**：qwen3.6:35b-mlx（猎手）、qwen3.6:27b-mlx（裁判）、qwen3.5:9b（账房）、deepseek-r1:14b（码农）、qwen3.5:4b（守夜人）、qwen3.5:2b（门卫）、bge-m3（档案员/RAG）
- **云端 DeepSeek（2 模型）**：DeepSeek-v4-pro（分析师·研报/估值）、DeepSeek-v4-flash（记者·情绪/题材）
- **连接池管理**：新增 `ai/ollama_pool.py`，统一管理 7 模型排队、keep_alive、切换
- **云端客户端**：新增 `ai/cloud_client.py`，支持 DeepSeek API 调用
- **废弃**：qwen3.5:35b-a3b-q4_K_M（被 qwen3.6:35b-mlx 全面取代）、mxbai-embed-large（被 bge-m3 替代）

### 新增引擎
- **`engine/analysis.py`** — ① 分析研判：4 维度并行（技术面/基本面/资金面/情绪面），输出 3 套备选方案（保守/中性/激进）
- **`engine/workshop.py`** — ② 策略工坊：4 角色 AI 辩论（猎手/账房/守夜人/裁判），AI 推荐风险等级 R1-R5，支持追问
- **`engine/planning.py`** — ③ 执行规划：仓位扫描 + 资金分配 + 风控硬约束检查
- **`engine/review.py`** — ⑥ 每日审查：纯规则引擎（仓位/止损/频率/标的池），黄牌预警 → 红牌告警 → 熔断
- **`engine/lifecycle.py`** — 策略生命周期协调器

### 新增 API
- `GET /api/strategy/active` — 获取当前活跃策略
- `POST /api/strategy/analysis` — 触发①分析研判
- `POST /api/strategy/{id}/debate` — 触发②策略工坊辩论
- `POST /api/strategy/{id}/confirm` — 确认策略决策卡
- `POST /api/strategy/debate/ask` — 追问特定角色
- `GET /api/strategy/risk-levels` — 获取 R1-R5 风险等级定义
- `POST /api/strategy/{id}/plan` — 触发③执行规划
- `POST /api/strategy/review` — 触发⑥每日审查
- `GET /api/strategy/reviews` — 获取审查日志

### 前端重设计
- **4+1 页面架构**：策略看板 / 策略工坊 / 持仓总览 / 绩效回顾 / 设置
- **旺财V4 品牌**：金橙主色 `#f0a050`，深夜黑底色 `#0f1117`，柴犬图标
- **新增组件**：StrategyStatus（策略状态卡）、LifecyclePipeline（6阶段流水线）、DebateCards（3角色辩论）、RiskSlider（R1-R5 风险滑块）、ReviewTimeline（审查时间线）
- **Workshop.vue** — 策略工坊核心页面：分析报告→AI辩论→决策卡，完整交互流程
- **Dashboard.vue** — 策略看板：状态卡 + 流水线 + 操作计划书 + 绩效概览
- **Review.vue** — 绩效回顾：收益曲线 + 策略匹配度 + 审查日志时间线
- **Holdings.vue** — 持仓总览简化：账户概览 + 持仓表格 + 成交记录，10s 自动刷新

### 删除 / 精简
- **删除 11 个文件**：prompts.py、sina_client.py、proxy_bypass.py、sync.py、scheduler.py、trend_tracker.py、holdings.js、Analysis.vue、Trading.vue、CodeView.vue
- **缓存精简**：4 层(L1-L4) → 2 层(30s + 5min)
- **定时任务精简**：9 个 → 5 个（风险检查内嵌，不再独立）
- **WebSocket 移除**：单用户场景改用飞书推送 + 前端轮询
- **模拟撮合移除**：broker.py 简化为纯交易日志记录

### 风险等级体系
```
R1保守(≤10%,-2%) → R2稳健(≤20%,-3%) → R3适中(≤30%,-5%) → R4积极(≤50%,-8%) → R5激进(≤70%,-12%)
```

---

## v3.0.0 (2026-05-27)

### 架构重构
- **持仓管理与模拟交易引擎彻底融合**: 删除 StockHoldings 和 PositionAdjustment 模型，持仓由 trade_logs 净头寸汇总计算
- **报告体系统一到辩论引擎**: 删除 prompts.py 旧路径，盘前/盘中/收盘全部使用 AIDebateEngine 多角色辩论
- **新增 AI 代码生成引擎**: deepseek-r1:14b 生成可执行量化策略代码，沙箱安全执行，前端代码查看和对比

### 新增功能
- `POST /api/trading/code/generate` — AI 生成交易策略代码
- `GET /api/trading/code` — 获取最新策略代码
- `GET /api/trading/holdings` — 从交易日志汇总获取当前持仓
- 前端 CodeView 页面 — 策略代码查看和高亮展示
- `scripts/update_changelog.py` — 自动更新日志脚本（支持交互/命令行/自动三种模式）
- `scripts/sync_changelog_to_settings.py` — CHANGELOG 自动同步到设置页面系统信息

### Bug 修复
- **致命**: 修复 scheduler.py 中 `kline.get("data", [])` → `kline.get("bars", [])` 导致策略扫描永不生成信号
- 修复 `ai/client.py` 中 `keep_alive: "0"` 导致的每次请求后卸载模型（改为 "5m"）
- 修复 `tiered_cache.py` 命中率统计从未计数的问题
- 修复 `akshare_market.py` 硬编码日期导致的接口过期
- 修复 `broker.py` 中 `_today_bought` 字典无限增长问题
- 修复 `debate.py` 中 `_parse_json` 失败静默降级（添加日志警告）
- 修复 `proxy_bypass.py` 全局 monkey-patch 副作用说明缺失

### 代码优化
- 提取 `_gather_market_context()` 消除三处约 70% 重复代码
- 消除 `premarket_analysis_job`/`review_job`/`intraday_analysis_job` 中的代码重复
- 删除未使用的 `PREMARKET_PROMPT` 和 `INTRADAY_PROMPT` 提示词模板
- 模型 `keep_alive` 改为 "5m"，避免频繁模型加载/卸载
- Settings.vue 系统信息区改为动态读取 system-info.json，版本号和变更摘要自动展示

### 破坏性变更
- 删除 `GET/POST/PATCH/DELETE /api/holdings` 持仓 CRUD 接口
- 删除 `PATCH /api/holdings/{id}/adjust` 和 `GET /api/holdings/{id}/adjustments`
- 删除 `StockHoldings` 和 `PositionAdjustment` 数据模型
- 前端持仓管理页改为从交易引擎读取，不再支持手动添加/编辑/调仓

## v2.4.1 (2026-05-26) — Python 3.14 兼容性修复 + 搜索 Bug 修复 + 启动流程优化

### Python 3.14 兼容性

系统环境升级到 Python 3.14，旧版依赖包不兼容，`requirements.txt` 中所有固定版本 (`==`) 改为最低版本 (`>=`)：

| 包名 | 旧版本 | 新版本 | 原因 |
|------|--------|--------|------|
| pydantic | 2.5.3 | 2.13.4 | pydantic-core 2.14.6 Rust 编译在 Python 3.14 上失败（`ForwardRef._evaluate()` 缺少 `recursive_guard` 参数） |
| sqlalchemy | 2.0.25 | 2.0.50 | Python 3.14 typing 模块变更导致 `SQLCoreOperations` 类初始化抛出 `AssertionError` |
| fastapi | 0.109.0 | 0.136.3 | 跟随 starlette 升级 |
| uvicorn | 0.27.0 | 0.48.0 | 兼容性升级 |
| websockets | 12.0 | 16.0 | 兼容性升级 |
| httpx | 0.26.0 | 0.28.1 | 兼容性升级 |
| apscheduler | 3.10.4 | 3.11.2 | 兼容性升级 |
| python-dotenv | 1.0.0 | 1.2.2 | 兼容性升级 |

### Bug 修复

- **股票搜索 API 结果 code/name 颠倒**：新浪 suggest 接口实际返回格式为 `名称,市场类型,代码,完整代码,名称,名称,类型,...`，但 `tencent_client.py` 中解析逻辑错误地假设为 `代码,名称,市场`，导致搜索结果中 code 和 name 字段颠倒（例如搜索"茅台"返回 `code: "贵州茅台", name: "11"`）。已修复为正确解析 `parts[2]=代码, parts[4]=名称, parts[3]=全码(推导市场)`
  - 注意：新浪 suggest 的 `parts[1]` 市场类型（11/12）**不可靠**——深市股票也可能标记为 11（例如"000725"返回 11），必须用 `parts[3]` 全码的前缀(`sh`/`sz`)推导市场

### 启动流程修复

- **创建虚拟环境**：Python 3.14 不支持系统级 pip 安装，必须用 `python3 -m venv .venv` 创建虚拟环境后再 `pip install`
- **启动命令修正**：后端需从项目根目录以 `PYTHONPATH=backend python -m app.main` 方式启动（不是在 backend/ 目录下执行 `python app/main.py`），因为代码中使用 `from app.xxx import ...` 绝对导入
- `scripts/start_all.sh` 需同步更新上述启动方式

### 调试经验

> **下次启动项目前的检查清单：**
> 1. 确认 Python 版本 (`python3 --version`)，如果 ≥3.14 则必须用虚拟环境
> 2. `source .venv/bin/activate && pip install -r requirements.txt`（依赖已在本次改为 `>=` 约束，新版本通常兼容）
> 3. 确认 Ollama 正在运行 (`curl http://localhost:11434/api/tags`)
> 4. `PYTHONPATH=backend python -m app.main` 启动后端
> 5. `cd frontend && npx vite` 启动前端开发服务器
> 6. 验证：`curl http://localhost:8000/api/health` 应返回 `{"status":"ok"}`

## v2.4.0 (2026-05-26) — 系统全面优化：数据基础 + AI 深化 + 持仓增强 + 布局重设计

### 新增数据接口

| 接口 | 说明 | 数据源 |
|------|------|------|
| `GET /api/kline/{code}` | K 线数据，支持日/周/月/60/30/15/5 分钟，最多 2000 根 | 腾讯财经 `web.ifzq.gtimg.cn` |
| `GET /api/fund-flow/{code}` | 个股资金流向，主力/超大单/大单/中单/小单 | 东方财富 `push2his.eastmoney.com` |
| `GET /api/search/stock` | 股票代码/名称联想搜索 | 新浪 suggest + 东财搜索备选 |

**新增文件**: `backend/app/data_sources/cls_client.py` — 财联社新闻客户端（电报快讯、热门题材、个股公告）

**改造文件**: `backend/app/data_sources/tencent_client.py` (+2 方法), `backend/app/data_sources/eastmoney_client.py` (+3 方法)

### AI 报告内容深化

- **新闻上下文注入**：盘前/盘中/盘后分析自动拉取财联社电报和热门题材，格式化为「政策/宏观 → 行业/板块 → 个股公告」注入辩论提示词
- **止盈止损具体化**：所有提示词新增铁律约束 —— 必须给出基于技术面的具体价格数字或分层级别，禁止原则性建议
- **数据来源标注**：每个推荐和建议末尾要求注明数据来源
- **裁判推理链暴露**：`deepseek-r1:14b` 裁判模型的 chain-of-thought 推理过程（`thinking` 字段）随报告返回

**改造文件**: `backend/app/ai/debate.py` (4 个角色提示词重写), `backend/app/ai/prompts.py` (4 个提示词重写), `backend/app/ai/client.py` (+thinking 字段), `backend/app/main.py` (3 个分析任务新增新闻采集)

### 持仓管理全面升级

- **联想搜索**：股票代码输入框支持模糊搜索，300ms 防抖，选中自动填入代码和名称
- **持仓调整**：新增增持/减持/平仓操作，手动输入成交价，自动重算成本价
- **调整日志**：每次调整记录持久化到 `position_adjustments` 表，支持历史追溯
- **共享组件**：`PositionTracker` 组件支持 `compact` 模式切换 —— 首页紧凑表格 / 持仓页交互模式（可展开 K 线图、资金流向图、调整记录）

**新增模型**: `PositionAdjustment` (position_adjustments 表)

**新增 API**:
- `PATCH /api/holdings/{id}/adjust` — 持仓调整（增持/减持/平仓）
- `GET /api/holdings/{id}/adjustments` — 调整日志查询

**改造文件**: `frontend/src/components/PositionTracker.vue` (重写，+ECharts 图表), `frontend/src/views/Holdings.vue` (重写，+联想搜索 +共享组件), `frontend/src/api/client.js` (+5 个新函数), `backend/app/models.py`, `backend/app/main.py`

### AI 分析页面增强

- **裁判推理过程**：盘前分析卡片顶部新增可折叠 `<details>` 区块，展示 DeepSeek-R1 的完整推理链，暗色等宽字体、可滚动
- **持仓技术分析**：盘前和复盘卡片底部新增 K 线图（日线蜡烛图 + 成交量双 Y 轴）和资金流向图（主力净流入柱状图），按股票展开、懒加载渲染
- **数据来源脚注**：每个报告卡片底部标注「腾讯行情 | 东方财富板块 | 财联社新闻 | Ollama AI (qwen3.5:35b + deepseek-r1:14b)」及 AI 引用来源

**改造文件**: `frontend/src/views/Analysis.vue` (重写，+ECharts)

### 首页布局重设计

从 2 列等宽网格 → 全宽纵向布局：

```
┌─────────────────────────────────────┐
│  MarketOverview (大盘概览，紧凑版)    │ ← 全宽
├─────────────────────────────────────┤
│  盘前分析 (PremarketSummary，大版)    │ ← 全宽，min-height: 35vh
├─────────────────────────────────────┤
│  盘中分析 (Intraday，大版)            │ ← 全宽，min-height: 30vh
├─────────────────────────────────────┤
│  ▸ 盘后总结 & 持仓动态  [可折叠]      │ ← localStorage 持久化折叠状态
└─────────────────────────────────────┘
```

- 盘前分析在展开模式下展示最多 5 条推荐（原 3 条）+ 知识角内联显示
- 盘中分析增强：操作策略 + 持仓建议 chips + 推荐股票 mini 卡片（买入区间/止损/目标价）
- 底部区域可折叠，CSS transition 动画，状态存入 localStorage

**改造文件**: `frontend/src/views/Dashboard.vue` (重写), `frontend/src/components/PremarketSummary.vue` (+expanded prop)

### 风控增强

- **交易频率检查**：新增风控维度 —— 查询过去 24 小时内 `PositionAdjustment` 记录数，>3 笔记低风险 (0.05)，>5 笔记中风险 (0.10)
- 风控检查调用链已传入 DB session，支持查询调整历史

**改造文件**: `backend/app/services/monitor.py`, `backend/app/main.py`

---

## v2.3.0 (2026-05-26) — 多模型分工 + 推理模型裁判

### 多模型分工策略

从单一 `qwen3.5:35b` 模型升级为**三模型分工**架构，显著提升生成速度和质量：

| 角色 | 模型 | 大小 | 理由 |
|------|------|------|------|
| 猎手 (短线) | `qwen3.5:35b` | 22.8GB | 复杂短线分析，需要大模型的知识广度 |
| 账房 (中低频) | `qwen3.5:9b` | 6.3GB | 估值/趋势分析较直接，9B 胜任 |
| 守夜人 (风控) | `qwen3.5:9b` | 6.3GB | 风险检查逻辑简单，快速模型即可 |
| 裁判 (盘前/复盘) | `deepseek-r1:14b` | 8.6GB | 推理模型擅长综合多方观点做决策 |
| 裁判 (盘中) | `qwen3.5:9b` | 6.3GB | 交易时段速度优先，避免模型切换 |

### Ollama 2-Model 内存限制

当前 Mac 上 Ollama 只能同时驻留 **2 个模型**（VRAM 上限约 60GB）：
- `35B(32G) + 9B(19G)` = 51GB ✅ 可共存
- `R1(42G) + 9B(19G)` = 61GB ✅ 可共存
- `35B(32G) + R1(42G)` = 74GB ❌ 不能共存，需切换模型 (~30s)

### 混合策略

- **盘前/复盘辩论**：裁判用 R1 推理模型，接受一次模型切换 (~30s)，总耗时约 **3 分钟**
- **盘中分析**：裁判用 9B 快速模型，无模型切换，总耗时约 **1.5 分钟**

**性能提升**：盘前辩论 ~6min → **~3min**，盘中分析 ~3min → **~1.5min** (均提升50%)

### 新增配置

- `backend/app/config.py`：新增 `OLLAMA_REASONING_MODEL` 配置（推理模型，用于裁判）
- `OLLAMA_FAST_MODEL` 改为默认 `qwen3.5:9b`（之前为空，需手动配置）
- `backend/app/ai/client.py`：`generate()` 新增 `num_predict` 参数，为 R1 推理模型预留足够 token

### 改造文件

- `backend/app/ai/debate.py`：重写模型路由逻辑，新增 `_hunter_model()`/`_accountant_model()`/`_guardian_model()`/`_aggregator_model()` 方法
- 盘中辩论恢复并行调用（不同模型不会在 Ollama 内部排队）
- 裁判调用自动检测推理模型并设置 `num_predict=2048`

---

## v2.2.1 (2026-05-26) — 页面增强 + 知识普及 + 性能优化

### Bug 修复：AI 辩论全部角色调用失败

**根因**：`debate.py` 中使用 `asyncio.gather` 并行调用 Ollama 3 个角色，但 Ollama 内部排队串行处理请求（~60s/角色），导致第 3 个请求等待 ~120s + 处理 ~60s ≈ 180s，超出 httpx 超时时间。

**修复**：
- `backend/app/ai/debate.py`：`debate()` 方法从并行 `asyncio.gather` 改为顺序调用，每个角色独立错误隔离，任一角色失败不影响其他；聚合器处理缺失角色数据
- `backend/app/ai/client.py`：httpx 超时从 `180.0` → `httpx.Timeout(300.0, connect=10.0)`

### Bug 修复：时间戳显示 UTC 而非北京时间

**根因**：`models.py` 使用 `server_default=func.now()` 调用 SQLite 的 `CURRENT_TIMESTAMP`（返回 UTC），而 Python 端 `datetime.now()` 返回 CST（北京时间），导致时区不一致。

**修复**：
- `backend/app/models.py`：所有模型从 `server_default=func.now()` → `default=datetime.now`、`onupdate=func.now()` → `onupdate=datetime.now`
- 验证：新记录时间戳正确显示 CST（如 `11:16:47` 而非 `02:16:47`）

### 新增功能：持仓管理页实时行情

- `frontend/src/views/Holdings.vue`：重写持仓列表，新增实时行情列（现价、涨跌幅、盈亏%、PE、PB），每 10s 自动刷新
- 新增 `loadRealtime()` 方法和 `realtimeMap` 数据结构
- 表格增加排序和视觉信号（涨绿跌红）

### 增强：AI 分析知识普及

所有 AI 提示词（盘前/盘中/盘后）新增知识普及要求：
- 每个角色的分析中新增 `knowledge_tips` 字段，用通俗语言解释关键术语（MA/RSI/PE/PB/量比等）
- 推荐股票附带「新手解读」说明推荐逻辑
- 风险提示附带「风险入门」说明

**改造文件**：`backend/app/ai/debate.py` (4 个提示词)、`backend/app/ai/prompts.py` (INTRADAY_PROMPT)

### 增强：盘中分析性能优化

- `backend/app/config.py`：新增 `OLLAMA_FAST_MODEL` 配置项，用于风控角色加速
- `backend/app/ai/debate.py`：盘中辩论的守夜人角色支持使用快速模型，预计缩短 30-40% 生成时间
- 守夜人提示词精简（从 40 行 → 25 行），减少 token 消耗

### UI 改进：板块折叠 + 止损/目标价可视化

- `frontend/src/components/MarketOverview.vue`：行业板块新增「展开全部/收起」折叠按钮，默认显示 Top8 + Bottom4
- `frontend/src/components/PositionTracker.vue`：新增止损/目标价列，风控信号更直观
- `frontend/src/styles/index.css`：新增折叠动画样式 `.collapsible-*`

---

## v2.2.0 (2026-05-26) — 异步生成修复 + 盘中分析 + 轮询机制

### Bug 修复：AI 分析生成无响应

**根因**：AI 辩论耗时 2-3 分钟，但前端 axios 超时仅 30 秒，且 POST 端点同步等待辩论完成才返回，导致每次点击「生成分析」必定超时，数据从未写入数据库。

**修复**：
- `backend/app/main.py`：POST `/api/ai/*/generate` 端点改用 FastAPI `BackgroundTasks` 异步执行，立即返回 `{"status": "started"}`；GET 端点增加 `generation` 状态字段（`running`/`started_at`）；新增 `generation_status` 内存追踪字典
- `frontend/src/api/client.js`：新增 `pollUntilGenerated()` 轮询工具（每 5s 查询，最长等待 5min），AI 生成端点超时降至 10s
- `frontend/src/views/Dashboard.vue`：`handleGeneratePremarket` 改用轮询机制，新增 `premarketGenerating` 状态
- `frontend/src/views/Analysis.vue`：`loadPremarket`/`loadReview` 全部改用轮询机制
- `frontend/src/components/PremarketSummary.vue`：新增 `generating` prop，显示「AI 分析进行中，预计 2-3 分钟...」
- `backend/app/ai/client.py`：httpx 超时从 120s → 180s

### 新增功能：盘中分析

- **两种触发方式**：定时自动（11:30 / 14:00，仅交易日）+ 手动点击
- **2 角色辩论**：猎手(操作) + 守夜人(风控) → 裁判聚合，比盘前 3 角色快约 30%
- **内容聚焦**：实时操作策略（加仓/减仓/做T/持有）、支撑压力位、板块轮动方向
- **新增文件/方法**：
  - `backend/app/ai/prompts.py`：新增 `INTRADAY_PROMPT`
  - `backend/app/ai/debate.py`：新增 `debate_intraday()` 方法
  - `backend/app/main.py`：新增 `intraday_analysis_job()`、`GET/POST /api/ai/intraday` 端点、2 个定时任务
  - `frontend/src/views/Dashboard.vue`：新增「盘中分析」板块
  - `frontend/src/views/Analysis.vue`：新增「盘中分析」卡片
  - `frontend/src/api/client.js`：新增 `getIntradayStrategy()` / `generateIntradayStrategy()`

### 设置页版本号

- `frontend/src/views/Settings.vue`：应用版本 1.0.0 → 2.2.0

---

## v2.1.0 (2026-05-26) — 双轨策略 + 大盘板块数据 + 首页重设计

### 盘前/盘后分析增强（双轨策略）

所有 AI 分析从笼统的统一建议升级为**短线 + 中低频**双轨独立输出：

| 维度 | 短线 (1-5天) | 中低频 (1-4周) |
|------|-------------|---------------|
| 分析重点 | MA5/10、RSI、量比、资金流、题材 | MA20/60、PE/PB、ROE、北向趋势 |
| AI 角色 | 猎手主导 | 账房主导 |
| 盘前输出 | 大盘/板块预测 + 持仓建议 + 推荐股票 | 同上，独立一轨 |
| 盘后输出 | 收益分析 + 次日建议 + 推荐股票 | 同上，独立一轨 |

**改造文件**: `backend/app/ai/prompts.py` (重写), `backend/app/ai/debate.py` (三角色双轨提示词)

### 新增数据 API

| 端点 | 说明 | 数据源 |
|------|------|------|
| `GET /api/market/indices` | 三大指数（上证/深证/创业板）实时行情 | 腾讯财经批量查询 |
| `GET /api/market/sectors` | 行业板块排名 Top20，含涨跌幅、领涨股 | 东财 push2 |
| `GET /api/market/breadth` | 涨跌家数分布（板块聚合估算） | 东财 push2 |
| `GET /api/holdings/realtime` | 全部持仓实时数据（批量），含现价/涨跌/盈亏/PE/PB | 腾讯财经批量查询 |

**改造文件**: `backend/app/main.py`, `backend/app/data_sources/tencent_client.py` (新增 `fetch_batch`), `backend/app/data_sources/eastmoney_client.py` (新增 `fetch_sectors`, `fetch_market_breadth`)

### 首页仪表盘重设计

从 3 张简单卡片 → **4 大板块**专业数据仪表盘：

| 板块 | 内容 | 刷新频率 |
|------|------|---------|
| A. 大盘与板块全景 | 三大指数卡片 + 涨跌分布条 + 行业板块排名 | 30s |
| B. 当日盘前分析 | 综合决策 + 短线/中低频双轨摘要卡片 | 按需 |
| C. 盘后总结 | 盈亏概览 + 市场总结 + 经验教训 | 按需 |
| D. 持仓实时动态 | 表格（现价/涨跌/盈亏/风控信号） | 10s |

**新增组件**: `MarketOverview.vue`, `PremarketSummary.vue`, `ReviewSummary.vue`, `PositionTracker.vue`

### UI 全面暗色化

- 全局暗色主题系统：深蓝黑底色 (`#1a1a2e`) + 卡片 (`#16213e`)
- 金色涨 (`#d4a574`) + 红色跌 (`#c0392b`) 双色体系
- 品牌红色导航栏 + 🧧 标识
- 卡片玻璃态效果 (backdrop-filter)
- ECharts 图表支持（已有依赖）

**改造文件**: `frontend/src/styles/index.css` (CSS 变量体系重构), `frontend/src/App.vue` (导航栏暗色化)

### AI 分析页增强

- 盘前策略：辩论角色卡片 + 双轨 Tab 切换（短线/中低频）
- 复盘分析：收益概览 + 板块表现 + 双轨次日建议 + 经验教训
- 点击"生成"实时触发 AI 分析

**改造文件**: `frontend/src/views/Analysis.vue` (重写)

### 定时任务增强

盘前/复盘任务新增板块数据采集，传给 AI 辩论引擎：
- 盘前 (9:00)：采集三大指数 + 行业排名 Top10 + 涨跌分布 + 持仓实时数据
- 复盘 (15:00)：采集大盘收盘 + 行业排名 Top20 + 涨跌分布

**改造文件**: `backend/app/main.py`

---

## v2.0.0 (2026-05-26) — 架构升级：多源容错 + AI辩论 + 多维度风控

### 数据源层（重写）

| 模块 | 文件 | 说明 |
|------|------|------|
| 腾讯财经 | `backend/app/data_sources/tencent_client.py` | 重写。使用技能文件中的正确 API：GBK 解码、`~` 分隔 88 字段、字段索引校准（PE=39、PB=46、振幅=43）。去掉之前错误的 `sh_` 下划线格式。 |
| 新浪财经 | `backend/app/data_sources/sina_client.py` | **新增**。作为备选数据源，提供实时报价。 |
| 多源路由 | `backend/app/data_sources/data_router.py` | **新增**。首选→备选→备选2→熔断。指数退避熔断（30s→60s→120s→240s→300s 上限）。 |

### 缓存系统（新增）

| 模块 | 文件 | 说明 |
|------|------|------|
| 分层缓存 | `backend/app/utils/tiered_cache.py` | **新增**。L1 实时(20s)、L2 日内(2min)、L3 高频(8min)、L4 低频(1h)。LRU 淘汰，TTL 过期。 |

### 风控引擎（增强）

| 模块 | 文件 | 说明 |
|------|------|------|
| 多维度风控 | `backend/app/services/monitor.py` | 从单一价格跌幅检查 → 6 维度加权评分：价格跌幅(35%)、量比异常(20%)、涨跌停预警(15%)、PE 极端值(15%)、换手率异常(10%)、振幅异常(5%)。综合评分 ≥0.30 高风险推送飞书。 |
| WebSocket 广播 | 同上 | 补充了 `websocket_clients` 集合和 `connect_websocket`/`disconnect_websocket` 方法（之前缺失导致 WebSocket 功能不可用）。 |

### AI 引擎（新增）

| 模块 | 文件 | 说明 |
|------|------|------|
| 辩论引擎 | `backend/app/ai/debate.py` | **新增**。猎手(进攻)、账房(稳健)、守夜人(防守) 三角色并行辩论 + 裁判聚合。盘前 9:00 自动运行。 |
| 提示词模板 | `backend/app/ai/prompts.py` | 已有文件，新增辩论角色专用提示词（内嵌于 debate.py）。 |

### 调度系统（增强）

- **7 个交易时段**（周一至周五）：9:00 盘前辩论、9:35/10:30/11:30/13:05/14:00 多维度风控、15:00 收盘复盘
- 盘前分析改为辩论模式，生成后推送飞书
- 风险检查增加 WebSocket 实时推送

### API 变更

| 端点 | 变更 |
|------|------|
| `POST /api/holdings` | 从 URL query params → JSON Body（Pydantic `HoldingCreate` 模型） |
| `POST /api/ai/premarket/generate` | **新增**。手动触发盘前 AI 辩论。 |
| `POST /api/ai/review/generate` | **新增**。手动触发收盘复盘。 |
| `POST /api/ai/risk-check` | **新增**。手动触发多维度风险检查。 |
| `GET /api/health` | 增强：返回缓存命中率统计。 |

---

## v1.0.1 (2026-05-26) — 紧急修复：AI 生成空白

### 根因

`backend/app/ai/client.py` — Ollama API 调用中 `"format": "json"` 参数与 Qwen3.5 模型不兼容。该参数需要模型原生支持 grammar-based constrained decoding，Qwen3.5 不支持此特性，导致模型返回 `content: ""` 空字符串。所有 AI 生成功能（盘前策略、复盘、AI 分析页）均受影响。

### 修复

- 移除 `"format": "json"` 参数
- 新增 `_extract_json()` 静态方法自动剥离 markdown code fence（` ```json ... ``` `）
- 添加 `"options": {"temperature": 0.7}` 控制生成质量

### 影响范围

`backend/app/ai/client.py` — `generate()` 方法。所有调用方（main.py 定时任务、debate.py 辩论引擎）无需修改。

---

## v1.0.0 (2026-05-26) — 初始修复：阻塞性 Bug

### Bug 修复

| 问题 | 文件 | 修复 |
|------|------|------|
| 前端构建目录路径错误 | `backend/app/main.py` | `../frontend/dist` → 基于三层 `os.path.dirname` 的项目根绝对路径 |
| WebSocket 缺少方法 | `backend/app/services/monitor.py` | 新增 `connect_websocket`/`disconnect_websocket`/`websocket_clients` |
| 模板多余闭合标签 | `frontend/src/App.vue` | 移除第 27 行多余的 `</div>` |
| `sync.py` 引用不存在列 | `backend/app/services/sync.py` | `current_price` → `cost_price`（模型中没有 `current_price` 字段） |
| `base.py` 缺少 import | `backend/app/data_sources/base.py` | 添加 `import asyncio`（`fetch_with_retry` 使用 `asyncio.sleep`） |
| 配置相对路径 | `backend/app/config.py` | `DATABASE_PATH` 和 `.env.local` 改为基于 `PROJECT_ROOT` 的绝对路径 |
| 前端 API JSON Body | `frontend/src/api/client.js` | `URLSearchParams` → JSON 对象（配合后端 Pydantic 模型） |

### 补齐功能

| 功能 | 说明 |
|------|------|
| AI 生成端点 | `POST /api/ai/*/generate` — 实际调用 Ollama，不再只读数据库 |
| 调度器 | 集成 APScheduler，7 个交易时段自动执行 |
| 飞书推送 | `FeishuNotifier` 类，盘前策略/风险/复盘推送到飞书机器人 |
| 持仓 API | `POST /api/holdings` 改用 Pydantic Body 模型 |
| 前端刷新按钮 | Analysis.vue 的「刷新」触发 POST generate 而非只读 GET |

---

## 已知限制

1. **mootdx** — 与 Python 3.14 不兼容（`bestip` 返回值格式变化），当前方案跳过了 mootdx TCP 数据源，改用纯 HTTP 方案（腾讯+新浪+东财）。
2. **AI 辩论并行度** — 三个角色并行调用 Ollama，Qwen3.5 35B 模型在本地运行时吞吐量有限（约 40-60s/角色），辩论完成总耗时约 2-3 分钟。
3. **飞书推送** — 需在 `.env.local` 配置 `FEISHU_WEBHOOK_URL`，留空则静默跳过。

---

## 下次迭代备忘

- [ ] mootdx Python 3.14 兼容性修复后，接入 TCP 行情作为首选数据源
- [ ] 辩论中超时角色跳过聚合，避免空结果拖累最终决策
- [ ] 添加量化因子计算（MA/MACD/RSI）增强信号层
- [ ] 前端 Dashboard 增加实时风控评分展示
- [ ] 历史收益曲线和回测功能
- [ ] 融资融券、北向资金信号集成


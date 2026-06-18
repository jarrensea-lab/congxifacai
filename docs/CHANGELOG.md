# 更新日志

## 2026-05-27 — 模拟交易引擎 v3.0 升级

### 设计文档

- [升级方案 Spec](superpowers/specs/2026-05-27-trading-engine-upgrade-design.md) — 模块架构、数据模型、策略规则、前后端设计
- [实施计划](superpowers/plans/2026-05-27-trading-engine-upgrade-plan.md) — 7 阶段 16 任务的具体编码计划

### 新增模块: `backend/app/trading_engine/` (10 文件)

| 文件 | 说明 |
|------|------|
| `account.py` | `SimAccountManager` — 模拟账户管理，资金以「分」为整数单位避免浮点精度 |
| `broker.py` | `SimBroker` — 模拟撮合引擎，市价/限价成交、滑点(±0.1%)、T+1、佣金万2.5、印花税千1 |
| `strategy_base.py` | `BaseStrategy` — 策略抽象基类，定义指标计算/信号生成/仓位计算/止盈止损统一接口 |
| `trend_tracker.py` | `TrendTrackerStrategy` — 趋势跟踪策略，MA5/MA20金叉死叉+ATR(14)止损+量能确认 |
| `signal_engine.py` | `SignalEngine` — 信号生成/审批/过期管理，止损信号自动批准 |
| `order_manager.py` | `OrderManager` — 订单生命周期，从信号创建→风控检查→撮合成交→日志记录 |
| `risk_guard.py` | `RiskGuard` — 交易级风控门禁：单笔≤20%仓位、日亏损≥5%熔断、同股同日≤3次、T+1、涨跌停 |
| `performance.py` | `PerformanceAnalyzer` — 8项绩效指标：胜率/盈亏比/夏普/最大回撤/年化收益/累计收益/交易次数/平均持仓 |
| `scheduler.py` | 策略定时扫描（每5分钟）+ 收盘信号过期清理 |
| `__init__.py` | 模块入口 |

### 新增数据模型 (4 张表)

- `sim_account` — 模拟账户（单例），初始资金 ¥100,000
- `trading_signals` — 策略信号（待审批/已批准/已拒绝/已过期/已执行）
- `trading_orders` — 交易订单（市价/限价/止损，待成交/已成交/已撤销/已拒绝）
- `trade_logs` — 交易日志（成交价/数量/费用/盈亏/持仓天数）

### 新增 API (11 个端点)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/trading/account` | GET | 获取模拟账户概览 |
| `/api/trading/account/reset` | POST | 重置账户到初始状态 |
| `/api/trading/signals` | GET | 获取策略信号列表（可按状态筛选） |
| `/api/trading/signals/{id}/approve` | POST | 批准信号并自动创建订单撮合 |
| `/api/trading/signals/{id}/reject` | POST | 拒绝信号并记录原因 |
| `/api/trading/orders` | GET | 获取交易订单列表 |
| `/api/trading/orders/{id}` | DELETE | 撤销未成交订单 |
| `/api/trading/performance` | GET | 获取绩效指标摘要 |
| `/api/trading/performance/curve` | GET | 获取净值曲线数据 |
| `/api/trading/strategy/params` | GET/PATCH | 获取/更新策略参数 |

### 新增定时任务 (3 个)

| 频率 | 任务 | 说明 |
|------|------|------|
| 每5分钟 | `strategy_scan_job` | 扫描监控股MA/ATR，生成买卖信号 |
| 15:00 | `strategy_scan_close` | 收盘前最后一次扫描 |
| 15:05 | `expire_signals_job` | 清理过期信号 |

### 前端新增: 交易看板 (6 文件)

- `views/Trading.vue` — 4 标签页主页面（策略信号台/模拟账户/策略绩效/参数配置）
- `components/TradingSignal.vue` — 信号审批卡片，绿买入/红卖出，[批准][拒绝]按钮
- `components/TradingAccount.vue` — 账户概览卡片 + ECharts 净值曲线
- `components/TradingPerformance.vue` — 8 项绩效指标卡片，含中英文标识和说明
- `api/trading.js` — 交易 API 客户端封装
- `store/trading.js` — Pinia 状态管理（账户/信号/订单/绩效/参数）

### 专业术语中文化

- 参数配置页：中文名称 + `(英文key)` 灰色小字 + 详细说明
- 绩效指标页：中文名称 + `(English Name)` 灰色小字 + 指标释义
- 信号描述：金叉/死叉/ATR/MA 等术语附带括号内中文解释

### 前端 SPA 路由修复

- `app.mount("/", StaticFiles)` → 自定义 `/{full_path:path}` catch-all 路由，确保 Vue Router `createWebHistory()` 模式的 SPA 路由正常跳转

---

## 2026-05-27 — 报告优化设计与全链路测试

### 设计文档

- 完成[报告推送优化设计](superpowers/specs/2026-05-27-report-optimization-design.md)，涵盖三部分：
  - 飞书推送消息重构（单卡截断 → 4卡独立完整推送）
  - 三级降级容错链路（辩论模式 → 单模型快速 → 模板摘要）
  - 三类报告内容优化（盘前/盘中/复盘）

### 代码变更

- **[client.py](app/ai/client.py#L31)**: `keep_alive` 从 `"15m"` 改为 `"0"` — 防止多模型辩论时 VRAM 累积。已知问题：字符串 `"0"` 需改为整数 `0`，Ollama API 对字符串格式的 0 处理异常，导致模型未按预期释放。

### 全链路测试发现

**测试环境**: Apple Silicon，Ollama 本地部署，7 个模型可用

| 项目 | 结论 |
|------|------|
| 盘前辩论 | 通过。4 角色完整运行 9 分钟，输出质量良好 |
| 盘中分析 | 失败。2B 模型连续超时 10 分钟 (`retries=1`)，回退到降级内容 |
| 复盘报告 | 通过。35B 单次调用 5 分钟完成，含具体投资建议 |

**根因分析**:

1. **VRAM 竞争**: 复盘报告使用 35B 模型（34GB），盘中分析同时加载 2B（10.4GB），总额 ~44.4GB 导致内存压力，2B 生成从正常 10s 暴增至超时。
2. **qwen3.5:2b 中文性能**: 独立测试显示 2B 生成 10 个中文字符需 60 秒（对比：英文 `Hello` 仅 10 秒）。4B 同样慢（109 秒/10 字符）。
3. **`keep_alive` 未生效**: 35B 模型在请求结束后未释放，说明字符串 `"0"` 格式未被 Ollama 正确解析。

**待修复项**（按优先级）:

- [ ] `keep_alive`: `"0"` → `0`（整数），或使用 `"0s"` 格式
- [ ] 复盘报告改用 4B 而非 35B 默认模型，避免 VRAM 竞争
- [ ] 盘中报告改用 4B 替代 2B
- [ ] 报告生成串行化，避免多个 job 同时跑

### 数据采集耗时基线

| 操作 | 耗时 |
|------|------|
| 腾讯批量取指数 (3个) | ~0.5s |
| AKShare 行业资金流 | ~15s |
| AKShare 市场指数 | ~1.5s |
| AKShare 新闻 (3只股票) | ~15s |
| AKShare 全市场上下文 | ~20s |
| **单次报告数据采集总计** | **~50-60s** |

### 设计文档

- 完成[报告推送优化设计](superpowers/specs/2026-05-27-report-optimization-design.md)，涵盖三部分：
  - 飞书推送消息重构（单卡截断 → 4卡独立完整推送）
  - 三级降级容错链路（辩论模式 → 单模型快速 → 模板摘要）
  - 三类报告内容优化（盘前/盘中/复盘）

### 代码变更

- **[client.py](app/ai/client.py#L31)**: `keep_alive` 从 `"15m"` 改为 `"0"` — 防止多模型辩论时 VRAM 累积。已知问题：字符串 `"0"` 需改为整数 `0`，Ollama API 对字符串格式的 0 处理异常，导致模型未按预期释放。

### 全链路测试发现

**测试环境**: Apple Silicon，Ollama 本地部署，7 个模型可用

| 项目 | 结论 |
|------|------|
| 盘前辩论 | 通过。4 角色完整运行 9 分钟，输出质量良好 |
| 盘中分析 | 失败。2B 模型连续超时 10 分钟 (`retries=1`)，回退到降级内容 |
| 复盘报告 | 通过。35B 单次调用 5 分钟完成，含具体投资建议 |

**根因分析**:

1. **VRAM 竞争**: 复盘报告使用 35B 模型（34GB），盘中分析同时加载 2B（10.4GB），总额 ~44.4GB 导致内存压力，2B 生成从正常 10s 暴增至超时。
2. **qwen3.5:2b 中文性能**: 独立测试显示 2B 生成 10 个中文字符需 60 秒（对比：英文 `Hello` 仅 10 秒）。4B 同样慢（109 秒/10 字符）。
3. **`keep_alive` 未生效**: 35B 模型在请求结束后未释放，说明字符串 `"0"` 格式未被 Ollama 正确解析。

**待修复项**（按优先级）:

- [ ] `keep_alive`: `"0"` → `0`（整数），或使用 `"0s"` 格式
- [ ] 复盘报告改用 4B 而非 35B 默认模型，避免 VRAM 竞争
- [ ] 盘中报告改用 4B 替代 2B
- [ ] 报告生成串行化，避免多个 job 同时跑

### 数据采集耗时基线

| 操作 | 耗时 |
|------|------|
| 腾讯批量取指数 (3个) | ~0.5s |
| AKShare 行业资金流 | ~15s |
| AKShare 市场指数 | ~1.5s |
| AKShare 新闻 (3只股票) | ~15s |
| AKShare 全市场上下文 | ~20s |
| **单次报告数据采集总计** | **~50-60s** |

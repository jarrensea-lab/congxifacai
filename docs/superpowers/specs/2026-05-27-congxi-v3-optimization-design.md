# 恭喜发财工作流 v3.0 优化设计文档

> 日期：2026-05-27 | 状态：已批准 | 作者：Claude Code Agent

---

## 一、优化目标

基于量化交易课程代码知识库，对恭喜发财工作流进行深度架构升级，核心目标：

1. **持仓管理与模拟交易引擎彻底融合** — 删除独立持仓模型，持仓由交易日志驱动
2. **报告体系统一到辩论引擎** — 三报告（盘前/盘中/收盘）+ 轻量级消息推送
3. **AI 代码生成引擎** — 策略分析产出可执行 Python 代码，前端可视化查看
4. **模型分时分角色调度** — 充分利用 5 个本地模型，优化 Mac 性能
5. **修复已知 Bug 和消除代码冗余** — 提升系统稳定性

---

## 二、数据模型变更

### 2.1 删除的模型

| 模型 | 表名 | 删除原因 |
|------|------|----------|
| `StockHoldings` | `holdings` | 持仓完全由 `trade_logs` 净头寸汇总计算 |
| `PositionAdjustment` | `position_adjustments` | 调仓操作统一走 信号→审批→订单→成交 流程 |

### 2.2 修改的模型

**`TradingSignal` 新增字段：**
- `code_snippet` (Text, nullable) — AI 生成的操盘代码片段
- `code_language` (String(20), default="python") — 代码语言标识

**`AIStrategy` 新增字段：**
- `generated_code` (Text, nullable) — AI 生成的完整策略代码
- `code_version` (Integer, default=1) — 代码版本号，每次重新生成递增
- `code_status` (String(20), default="draft") — 代码状态：draft/validated/deployed/archived

**`TradeLog` 新增字段：**
- `signal_id` (Integer, FK→trading_signals.id, nullable) — 追溯信号来源
- `strategy_code_version` (Integer, nullable) — 执行时使用的代码版本

### 2.3 持仓查询方案

前端不再依赖 `holdings` 表。通过 `/api/trading/holdings` 接口返回：

```python
# 当前持仓 = trade_logs 按 stock_code 汇总净头寸
def get_current_holdings():
    results = db.query(
        TradeLog.stock_code,
        TradeLog.stock_name,
        func.sum(TradeLog.quantity).label('position'),
        func.avg(TradeLog.price).label('avg_cost')
    ).filter(
        TradeLog.status == 'filled'
    ).group_by(
        TradeLog.stock_code, TradeLog.stock_name
    ).having(
        func.sum(TradeLog.quantity) > 0
    ).all()
    return results
```

---

## 三、报告体系重构

### 3.1 三报告架构

| 时间 | 类型 | 模型分配 | 输出 | 触发方式 |
|------|------|----------|------|----------|
| 09:00 | 盘前策略报告 | 猎手35b → 账房9b → 守夜人4b → 裁判+代码r1:14b | 推荐标的 + 操作策略 + 代码 | APScheduler |
| 11:30 | 盘中监测简报 | 快速分析9b → 审核4b | 持仓状态 + 调整建议 + 代码(如需) | APScheduler |
| 14:00 | 盘中监测简报 | 快速分析9b → 审核4b | 同上 | APScheduler |
| 15:00 | 收盘复盘报告 | 猎手35b → 账房9b → 守夜人4b → 裁判+代码r1:14b | 绩效回顾 + 次日计划 + 代码优化 | APScheduler |

### 3.2 轻量级消息推送（非 AI 生成）

| 消息类型 | 触发条件 | 推送方式 |
|----------|----------|----------|
| 策略信号通知 | 策略扫描生成信号时 | WebSocket + 飞书 |
| 操作申请许可 | 信号需人工审批时 | WebSocket |
| 风险预警 | 风控评分 >= 0.30 | WebSocket + 飞书 |
| 参数优化建议 | 绩效回测后自动评估 | WebSocket |
| 代码执行结果 | AI 代码 exec 后 | WebSocket |

### 3.3 优先级控制

```
报告生成 > 代码生成 > 消息推送 > 风险扫描
```

- 每次 AI 调用前检查当前 token 消耗和 Ollama 模型加载状态
- 模型 keep_alive 设置为 "5m"，避免频繁加载/卸载
- 消息推送队列化，不在报告生成期间抢占模型资源
- 风险扫描使用纯计算逻辑，不调用 AI

---

## 四、代码生成引擎

### 4.1 生成流程

```
AI Report 产出策略建议
    ↓ (如有具体操作建议)
Trigger Code Generation
    ↓
deepseek-r1:14b ← 策略上下文 + 数据
    ↓
生成 Python 策略代码
    ↓
validate_output(qwen 2b) → 语法/安全检查
    ↓
沙箱 exec → 试运行验证
    ↓
保存到 AIStrategy.generated_code (code_version++)
    ↓
前端展示 (Monaco Editor + Diff)
```

### 4.2 安全沙箱

```python
SAFE_GLOBALS = {
    'pd': pandas,
    'np': numpy,
    'bt': backtrader,  # 参考量化课程知识库
}
SAFE_LOCALS = {}

# 禁止: open, exec, eval, __import__, os, sys, subprocess
exec(generated_code, {"__builtins__": {}}, SAFE_LOCALS)
```

### 4.3 代码版本管理

- 每次代码生成保存到 `ai_strategies.generated_code`
- `code_version` 递增
- 历史版本通过 `ai_strategies` 表的 `created_at` 时间戳追溯
- 前端提供当前版本 vs 上一版本的 diff 对比

### 4.4 参考量化交易课程知识库

代码生成引擎参考的知识模式：
- Backtrader 策略模板（ch14 双均线、固定止损止盈）
- 移动止损止盈模板（ch15）
- 波动率头寸管理（ch15）
- RSI 指标计算（ch09）
- 移动平均线信号（ch08）

---

## 五、模型分配策略

### 5.1 模型清单

| 模型 | 用途 | 预估显存 | keep_alive |
|------|------|----------|------------|
| `qwen3.5:35b-a3b-q4_K_M` | 猎手 — 深度短线技术分析 | ~20GB | 5m |
| `qwen3.5:9b` | 账房 — 中低频估值/趋势 | ~6GB | 5m |
| `qwen3.5:4b` | 守夜人 — 风控审核 | ~3GB | 5m |
| `qwen3.5:2b` | 输出校验 validate_output | ~1.5GB | 5m |
| `deepseek-r1:14b` | 裁判 + 代码生成 | ~9GB | 5m |

### 5.2 调度策略

```
盘前 09:00：
  ① 猎手35b (分析) → ② 账房9b (估值) → ③ 守夜人4b (风控)
  → ④ 裁判r1:14b (综合+代码)
  总耗时：~2-3min (顺序执行，keep_alive 避免重复加载)

盘中 11:30 / 14:00：
  ① 快速分析9b → ② 审核4b
  总耗时：~30-60s

收盘 15:00：
  同盘前流程
  总耗时：~2-3min
```

### 5.3 显存管理

- 所有 keep_alive 设为 "5m"，模型在最后一次请求后 5 分钟卸载
- 顺序调用避免同时加载多个大模型（35b + 14b 同时加载需 ~29GB，可能超出 Mac 显存）
- 小模型（4b/2b）可与大模型共存，显存占用可控

---

## 六、前端变更

### 6.1 新增页面/组件

| 组件 | 路径 | 功能 |
|------|------|------|
| `CodeView.vue` | `views/CodeView.vue` | 代码查看页面，Monaco Editor 语法高亮 |
| `CodeDiff.vue` | `components/CodeDiff.vue` | 两个版本代码的 diff 对比 |
| `StrategyCode.vue` | `components/StrategyCode.vue` | 嵌入 Analysis 页的代码卡片 |
| `NotificationBar.vue` | `components/NotificationBar.vue` | 顶部消息推送通知栏 |

### 6.2 修改的组件

| 组件 | 变更 |
|------|------|
| `Holdings.vue` | 重构：删除手动添加/编辑/删除表单，持仓数据从 `/api/trading/holdings` 读取 |
| `PositionTracker.vue` | 删除止盈止损行内编辑、调仓表单、添加持仓按钮。仅保留查看功能和展开的 K 线/资金流图表 |
| `Dashboard.vue` | 新增消息推送通知栏，修改持仓数据源 |
| `Analysis.vue` | 新增策略代码卡片展示区 |

### 6.3 删除的组件

- `Holdings.vue` 中的手动添加持仓表单和相关逻辑
- `PositionTracker.vue` 中的 `editStop`/`editTarget`/调仓表单状态
- 前端 `store/holdings.js` 中的 `addHolding`/`removeHolding` action

### 6.4 路由变更

| 路由 | 变更 |
|------|------|
| `/holdings` | 保留，但内容从手动持仓管理改为交易引擎持仓展示 |
| `/code` | 新增，代码查看页面 |
| 其他路由 | 不变 |

---

## 七、Bug 修复清单

### 致命 Bug
1. **`scheduler.py` line 586**: `kline.get("data", [])` → `kline.get("bars", [])`
   - 影响：策略扫描永不生成信号
   - 修复：修正字段名

### 高风险 Bug
2. **`order_manager.py`**: `TradeLog.order_id` 先写 0 再 UPDATE
   - 修复：在 INSERT 之前获取 order.id，一次写入
3. **`ai/client.py`**: `keep_alive: "0"` 导致每次请求后卸载模型
   - 修复：改为 `"5m"`
4. **`debate.py`**: `_parse_json` 失败静默返回 `{"raw": text}`
   - 修复：添加 error 级别日志，保留 raw 字段供调试

### 中等风险 Bug
5. **`akshare_market.py` line 153**: 硬编码日期 `'20260501'`
   - 修复：改为 `datetime.now() - timedelta(days=30)` 动态计算
6. **`tiered_cache.py`**: `_hits`/`_misses` 从未更新
   - 修复：在 `get()` 方法中添加计数逻辑
7. **`SimBroker._today_bought`**: 无清理机制
   - 修复：添加日期变更时的清理逻辑

### 代码质量问题
8. **全局 monkey-patch**: `proxy_bypass.py` 使用 `from ... import *`
   - 修复：改为上下文管理器或显式作用域
9. **重复代码**: `premarket_analysis_job` / `review_job` / `intraday_analysis_job` 70% 重复
   - 修复：提取 `_gather_market_context()` 和 `_save_and_notify()` 公共函数
10. **未使用的提示词**: `PREMARKET_PROMPT` 和 `INTRADAY_PROMPT` 从未被引用
    - 修复：删除或整合到 debate 引擎

---

## 八、实现优先级

| 优先级 | 任务 | 依赖 |
|--------|------|------|
| P0 | 修复 scheduler.py 致命 Bug | 无 |
| P0 | 删除 StockHoldings + PositionAdjustment 模型 | 无 |
| P0 | 统一报告引擎（删除 prompts.py 旧路径） | 无 |
| P1 | 实现代码生成引擎 | P0 |
| P1 | 前端持仓展示改造 | P0 |
| P1 | 修复 order_manager.py 二次写入 | 无 |
| P2 | 前端 CodeView + CodeDiff 组件 | P1 |
| P2 | 模型 keep_alive + 分时调度优化 | 无 |
| P2 | 提取公共代码消除重复 | 无 |
| P3 | tiered_cache 命中率修复 | 无 |
| P3 | proxy_bypass 改为上下文管理器 | 无 |
| P3 | 前端消息推送通知栏 | P0 |

---

## 九、风险与注意事项

1. **数据迁移**：删除 `holdings` 表前需确认 `trade_logs` 中有足够的成交记录支撑持仓展示
2. **Ollama 并发**：顺序调用确保 Mac 显存不溢出，但总耗时较长
3. **代码安全**：exec 沙箱必须严格限制 builtins，禁止文件系统和网络访问
4. **向后兼容**：前端 `/api/holdings` 接口调用需全部重定向到 `/api/trading/holdings`

---

*设计文档版本：1.0*
*关联知识库：量化交易课程代码知识总结*

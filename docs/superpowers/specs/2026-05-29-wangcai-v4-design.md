# 旺财V4 · 策略生命周期引擎 设计文档

> 日期：2026-05-29 | 状态：设计中 | 作者：Claude Code Agent

---

## 一、项目定位

**旺财V4** 是「恭喜发财」工作流的第四次重大迭代。核心变化：从「AI 生成报告 + 人工解读」升级为「**策略生命周期闭环**」——分析研判 → 策略工坊 → 执行规划 → 持仓执行 → 绩效回顾 → 每日审查 → 反馈调整。

核心理念：**知行合一**。AI 负责「知」（分析研判、方案生成），你负责「行」（确认决策、执行操作），每日审查确保「知行不悖」。

---

## 二、模型矩阵

### 2.1 本地 Ollama（7 模型）

| 模型 | 角色 | 大小 | 用途 |
|------|------|------|------|
| `qwen3.6:35b-mlx` | 猎手 | 21GB | 深度短线技术分析 |
| `qwen3.6:27b-mlx` | 裁判 | 19GB | 多角色辩论综合决策 |
| `qwen3.5:9b` | 账房 | 6.6GB | 中低频估值 / 盘中快速简报 |
| `deepseek-r1:14b` | 码农 | 9GB | 策略代码生成 / 推理链 |
| `qwen3.5:4b` | 守夜人 | 3.4GB | 风控审核 / 输出校验 |
| `qwen3.5:2b` | 门卫 | 2.7GB | 格式化校验 / JSON 修复 |
| `bge-m3` | 档案员 | ~2GB | 中文语义检索 / RAG |

### 2.2 云端 DeepSeek（2 模型）

| 模型 | 角色 | 用途 |
|------|------|------|
| `DeepSeek-v4-pro` | 分析师 | 研报深度解读 / 多维度估值 / 产业链调研 |
| `DeepSeek-v4-flash` | 记者 | 新闻情绪分析 / 题材归因 / 快速摘要 |

### 2.3 任务分配原则

| 任务类型 | 模型 | 原因 |
|---------|------|------|
| 短线技术分析 | qwen3.6:35b-mlx 本地 | 数据敏感，低延迟 |
| 中低频估值 | qwen3.5:9b 本地 | 逻辑直接，9B 足够 |
| 辩论综合决策 | qwen3.6:27b-mlx 本地 | 整合多方输入 |
| 风控审核 | qwen3.5:4b 本地 | 规则检查，轻量即可 |
| 策略代码生成 | deepseek-r1:14b 本地 | 推理模型擅长代码 |
| 输出校验/修复 | qwen3.5:2b 本地 | 最小开销 |
| 历史报告检索 | bge-m3 本地 | 中文 embedding SOTA |
| 研报深度解读 | DeepSeek-v4-pro 云端 | 长文本推理 |
| 多维度估值分析 | DeepSeek-v4-pro 云端 | 复杂计算 |
| 新闻情绪/题材 | DeepSeek-v4-flash 云端 | 快速处理大量文本 |
| 产业链调研 | DeepSeek-v4-pro + flash | 混合深度+广度 |

### 2.4 显存管理

- keep_alive 统一设为 "5m"，模型在最后一次请求后 5 分钟卸载
- 顺序调用避免同时加载多个大模型（35b + 27b + r1 同时加载将超出 Mac 显存）
- 小模型（9b/4b/2b）可与任一大模型共存
- 新增 `ai/ollama_pool.py` 连接池管理模型排队、keep_alive、模型切换

### 2.5 废弃项

- **不参与本项目的模型**：`qwen3.5:35b-a3b-q4_K_M`（被 qwen3.6:35b-mlx 全面取代——更快、更小、更新、MLX 原生）、`mxbai-embed-large`（被 bge-m3 替代，中文检索精度提升 20-40%）

---

## 三、整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                    策略生命周期引擎 (StrategyLifecycle)            │
│                                                                    │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    │
│  │ ①分析研判 │───▶│ ②策略工坊 │───▶│ ③执行规划 │───▶│ ④持仓执行 │   │
│  │ Analysis │    │ Workshop │    │ Planning │    │ Execute  │    │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘    │
│        │                                               │           │
│        │          ┌──────────┐    ┌──────────┐        │           │
│        └─────────▶│ ⑥每日审查 │◀───│ ⑤绩效回顾 │◀───────┘           │
│                   │ Review   │    │ Retro    │                     │
│                   └──────────┘    └──────────┘                     │
└──────────────────────────────────────────────────────────────────┘

模型矩阵
┌──────────────────────┼──────────────────────┐
▼                      ▼                      ▼
本地 Ollama (7模型)    云端 DeepSeek (2模型)
· 策略辩论 · 风控       · 深度分析 · 研报
· 代码生成 · RAG        · 新闻情绪 · 题材
```

### 3.1 核心数据模型：StrategyInstance

```python
class StrategyInstance(Base):
    __tablename__ = "strategy_instances"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.now)
    status = Column(String(20), default="draft")
    # draft → confirmed → planned → executing → completed → reviewed

    # 风险等级
    risk_level = Column(Integer, default=3)  # R1-R5

    # 决策参数
    position_limit_pct = Column(Float)       # 总仓位上限 %
    single_stock_limit_pct = Column(Float)   # 单票仓位上限 %
    stop_loss_pct = Column(Float)            # 止损线
    holding_period_days = Column(Integer)    # 持仓周期(天)

    # 标的池
    stock_pool = Column(JSON)               # [{"code":"000001","name":"平安银行","weight":0.3},...]

    # 分析报告
    analysis_report = Column(JSON)           # ①分析研判输出
    debate_summary = Column(JSON)            # ②策略工坊辩论摘要
    execution_plan = Column(JSON)            # ③执行规划输出

    # 绩效回顾
    expected_return_best = Column(Float)     # 预期最优
    expected_return_neutral = Column(Float)  # 预期中性
    expected_return_worst = Column(Float)    # 预期最差
    actual_return = Column(Float, nullable=True)
    review_notes = Column(Text, nullable=True)
```

### 3.2 新增辅助模型

```python
class ReviewLog(Base):
    __tablename__ = "review_logs"
    id = Column(Integer, primary_key=True)
    strategy_instance_id = Column(Integer, ForeignKey("strategy_instances.id"))
    review_date = Column(Date, default=date.today)
    result = Column(String(20))      # pass / yellow / red / breaker
    violations = Column(JSON)         # [{"rule":"仓位超限","detail":"..."}]
    created_at = Column(DateTime, default=datetime.now)

class UserPreference(Base):
    __tablename__ = "user_preferences"
    id = Column(Integer, primary_key=True)
    dimension_weights = Column(JSON)  # {"technical":0.4,"fundamental":0.3,...}
    default_risk_level = Column(Integer, default=3)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
```

### 3.3 旧模型变更

| 模型 | 变更 |
|------|------|
| `AIStrategy` | 保留，新增 `strategy_instance_id` FK 关联 |
| `TradeLog` | 新增 `strategy_instance_id` FK，追溯策略来源 |
| `TradingSignal` | 新增 `strategy_instance_id` FK |
| `RiskAlert` | 保留，新增 `review_type` 字段区分「风控告警」vs「每日审查」 |

---

## 四、六大阶段详细设计

### 4.1 分析研判 (Analysis)

**目标**：把零散数据查询变成结构化投资倾向性报告。

```
输入: K线/资金流/财务/研报/新闻/板块/持仓
       │
       ▼
┌─────────────────────────────────────────┐
│ 并行多维度分析                            │
│ · 技术面 → qwen3.6:35b-mlx (本地)       │
│ · 基本面 → DeepSeek-v4-pro (云端)       │
│ · 资金面 → qwen3.5:9b (本地)            │
│ · 情绪面 → DeepSeek-v4-flash (云端)     │
│ · RAG增强 → bge-m3 检索相似历史场景     │
│ · 综合研判 → qwen3.6:27b-mlx (本地)     │
└─────────────────────────────────────────┘
       │
       ▼
输出: 投资倾向性报告
  · 技术面评分 (0-100)
  · 基本面评分 (0-100)
  · 资金面评分 (0-100)
  · 情绪面评分 (0-100)
  · 综合倾向度
  · 3套备选方案: 保守(估值修复) / 中性(趋势跟随) / 激进(题材博弈)
  · 数据溯源标注
  · 历史相似场景参考
```

**并行执行策略**：本地模型和云端模型同时调用，互不阻塞。综合研判在所有维度完成后执行。

**偏好学习**：用户对方案的选择偏好记录到 `UserPreference` 表，后续分析时注入偏好权重。

**触发**：盘前 9:00 自动 / 手动随时触发。

### 4.2 策略工坊 (Workshop)

**目标**：交互式策略讨论，AI 推荐风险等级，用户确认决策。

```
① 分析研判报告 (来自阶段①)
       │
       ▼
② AI 多方辩论
  · 猎手(qwen3.6:35b) → 进攻观点
  · 账房(qwen3.5:9b)  → 稳健观点
  · 守夜人(qwen3.5:4b) → 保守观点
  · 裁判(qwen3.6:27b)  → 综合决策 + 风险等级推荐
       │
       ▼
③ 策略决策卡 (用户确认)
  风险等级: [R1] [R2] [R3◉] [R4] [R5]
  仓位上限: 30%   止损线: -5%   周期: 3-5天
  标的池: 3-5支股票
  [确认生效]
```

**风险等级体系：**

| 等级 | 仓位上限 | 止损 | 标的类型 | 杠杆 |
|------|---------|------|---------|------|
| R1 保守 | ≤10% | -2% | ETF/债基 | 无 |
| R2 稳健 | ≤20% | -3% | 蓝筹低波动 | 无 |
| R3 适中 | ≤30% | -5% | 加入成长股 | 无 |
| R4 积极 | ≤50% | -8% | 允许小盘 | 无 |
| R5 激进 | ≤70% | -12% | 允许题材博弈 | 无 |

**AI 推荐逻辑**：根据市场波动率、持仓盈亏状态、历史胜率、宏观环境，推荐风险等级。用户可手动调整。

**可追问**：点击任意角色卡片可查看详细推理链，支持追问——「猎手为什么推荐这支票？」触发单角色深聊。

**移动端**：策略卡通过飞书卡片消息推送，移动端可查看风险等级和确认。

### 4.3 执行规划 (Execution Planning)

**目标**：根据实际仓位和资金，把决策卡翻译成可执行的操作计划。

```
输入: 策略决策卡 + 实时账户(持仓/资金/成本)
       │
       ▼
┌─────────────────────────────────────────┐
│ ① 仓位现状扫描: 当前持仓 vs 目标标的池    │
│ ② 资金分配: Kelly变体算法,按置信度+波动率│
│ ③ 操作指令生成: 买/卖/持/减/加           │
│ ④ 预期收益建模: 最差/中性/最优            │
└─────────────────────────────────────────┘
       │
       ▼
输出: 操作计划书
  · 买入清单: 标的/数量/参考价格区间
  · 卖出/减仓清单
  · 资金分配表: 总投入/预留/可用
  · 预期收益: 最差-3% / 中性+5% / 最优+12%
  · 风控硬约束检查结果
```

**硬约束（不可绕过）**：
- 单票仓位 ≤ 风险等级对应的单票上限
- 总仓位 ≤ 风险等级对应的总仓位上限
- T+1 买入限制检查
- 涨跌停不可交易检查

**软建议（可手工调整）**：资金分配权重、买卖数量。

### 4.4 持仓执行 (Execution)

**简化设计**：不模拟撮合，不做虚拟交易。操作计划书是参考依据，实际成交记录通过交易日志手动/自动录入。

**变更**：`trading_engine/broker.py` 移除模拟撮合逻辑，仅保留交易日志记录。

**交易日志增强**：新增 `strategy_instance_id` 字段，每次交易关联到具体策略。

### 4.5 绩效回顾 (Retrospective)

**目标**：持仓执行后，定期回顾策略效果，提出调整建议。

| 回顾维度 | 数据来源 | 分析方式 |
|---------|---------|---------|
| 收益率 vs 预期 | 操作计划书 vs 实际盈亏 | 纯计算 |
| 胜率 / 盈亏比 | 历史交易记录 | 纯计算 |
| 最大回撤 | 净值曲线 | 纯计算 |
| 策略与市场匹配度 | 策略类型 vs 市场风格 | qwen3.6:27b-mlx |
| 调整建议 | 综合以上 | qwen3.6:27b-mlx |

**触发**：收盘后、周末、月度，由 APScheduler 定时触发。

### 4.6 每日审查 (Daily Review)

**目标**：检查当日操作是否违反策略约束，预警和修正。

```
审查流程(全自动, ~5s,纯规则引擎):
1. 加载当日策略决策卡 + 执行记录
2. 逐项检查:
   □ 仓位 ≤ 风险等级上限？
   □ 止损执行了吗？
   □ 单票仓位 ≤ 上限？
   □ 操作频率 ≤ 阈值？
   □ 是否在标的池外交易？
3. 判定:

   ✅ 全部通过 → 静默记录

   ⚠️ 黄牌: 偏离策略但未突破硬约束
   → 前端通知 + 飞书推送

   🟠 红牌: 突破风险等级硬约束
   → 前端告警 + 飞书强提醒 + 写入风险日志

   🔴 熔断: 连续2日红牌或单日亏损超限
   → 禁用交易入口 + 需手动恢复
```

**审查日志**：每次审查结果记录到 `review_logs` 表，前端时间线展示。

---

## 五、定时任务重构

### 5.1 旧任务（9个 → 废弃）

原有 9 个独立定时任务（盘前、5次风控、2次盘中、收盘）全部并入生命周期引擎调度。

### 5.2 新任务（5个）

| 时间 | 任务 | 触发阶段 |
|------|------|---------|
| 09:00 | 盘前启动 | ①分析研判 → ②策略工坊 |
| 11:30 | 午间简报 | 盘中快照 + ⑤绩效回顾(简版) |
| 14:00 | 下午简报 | 盘中快照 + ③执行规划更新 |
| 15:00 | 收盘处理 | ⑤绩效回顾(完整) + ⑥每日审查 |
| 15:30 | 周/月统计 | 绩效汇总 + 调整建议 |

---

## 六、删除 / 替换 / 缩减清单

### 6.1 删除

| 删除项 | 原因 |
|--------|------|
| `ai/prompts.py` | v3.0 已被 debate.py 取代，零引用 |
| `data_sources/sina_client.py` | 腾讯+东财已覆盖全部维度，新浪从未被选为主源 |
| `data_sources/proxy_bypass.py` | 全局 monkey-patch，副作用不可控 |
| `services/sync.py` | v2.0 遗留代码，引用了不存在的字段 |
| `trading_engine/scheduler.py` | 策略扫描从未产出有效信号，被 StrategyLifecycle 替代 |
| `trading_engine/trend_tracker.py` | 功能过于简单(仅MA5/10交叉)，被AI辩论覆盖 |
| `trading_engine/position.py` T+1 逻辑 | 有 bug（`_today_bought` 日期不清理），移到 risk_guard |
| `store/holdings.js` | v3.0 持仓改为接口聚合，store 只剩冗余转发 |

### 6.2 替换

| 旧项 | 新项 | 原因 |
|------|------|------|
| `ai/client.py` httpx 直连 | `ai/ollama_pool.py` 连接池 | 7个模型需管理 keep_alive、排队、切换 |
| `services/monitor.py` 6维风控 | 纯规则检查（每日审查） | 风控权重是拍脑袋的，硬约束更有效 |
| `data_sources/akshare_market.py` | 保留签名，内部切腾讯/东财 HTTP | akshare 版本变化+Python 3.14 兼容 |
| `mxbai-embed-large` | `bge-m3` | 中文检索精度提升 20-40% |

### 6.3 缩减

| 当前 | 缩减为 | 原因 |
|------|--------|------|
| 9 个定时任务 | 5 个 | 风险检查内嵌，不再独立 |
| 4 层缓存(L1-L4) | 2 层(30s+5min) | L3/L4 个人使用过度 |
| WebSocket 实时推送 | 飞书推送 + 前端轮询 | 单用户无需 WebSocket |
| `broker.py` 模拟撮合 | 纯交易日志记录 | 不需要模拟撮合 |
| 6 个前端页面 | 4+1 个 | Analysis/Trading/CodeView 合并 |
| ECharts 图表 | K线保留，其余CSS数据条 | 减少渲染开销 |

---

## 七、Web 页面重设计

### 7.1 页面结构

```
导航栏: [🐕 旺财V4]  策略看板  策略工坊  持仓总览  绩效回顾  设置

策略看板 (Dashboard) — 首页/核心页
├── 策略状态卡 (当前风险等级 + 审查状态 + 策略阶段进度)
├── 生命周期流水线 (①②③④⑤⑥ 进度可视化)
├── 操作计划书 (待执行列表)
├── 绩效概览 (累计/当日/月度 盈亏)
└── 持仓实时概览 (紧凑4列表格)

策略工坊 (Workshop) — 新增核心页
├── ① 分析研判报告 (3套方案，可折叠展开，Radar图)
├── ② AI 辩论 (4角色观点卡片，可追问/展开推理链)
└── ③ 策略决策卡 (风险等级滑块 + 参数确认 + [确认生效])

持仓总览 (Holdings) — 简化页
├── 账户概览 (总资产/可用/市值/盈亏)
├── 持仓列表 (代码/持仓/现价/涨跌/PE/PB)
└── 交易日志 (最近20条)

绩效回顾 (Review) — 合并页
├── 收益曲线 (累计/月度/日度，精简K线图)
├── 策略匹配度分析 + 调整建议
└── 每日审查日志 (时间线)

设置 (Settings) — 保留
├── 系统信息 / 模型配置 / 通知设置
└── 偏好设置 (分析维度权重、飞书配置)
```

### 7.2 删除/合并的旧页面

| 旧页面 | 去向 |
|--------|------|
| Analysis.vue | 并入 Workshop.vue |
| Trading.vue | 并入 Holdings.vue |
| CodeView.vue | 缩减为 Workshop 中可折叠区域 |

### 7.3 视觉设计

```
配色:
· 底色: #0f1117 (深夜黑)
· 卡片: #1a1d2e (深蓝灰)
· 主色: #f0a050 (金橙 — 旺财品牌色)
· 涨: #ef4444 (红涨 — A股惯例)
· 跌: #22c55e (绿跌)
· 强调: #f0a050 / #6366f1 (金橙 + 靛蓝双强调)

字体:
· 系统默认中文字体 (PingFang SC / Microsoft YaHei)
· 英文/数字: JetBrains Mono / SF Mono
· 标题: 16-20px bold
· 正文: 13-14px regular
· 数据: 14-18px monospace

卡片:
· 无边框，background: #1a1d2e
· box-shadow: 0 2px 8px rgba(0,0,0,0.3)
· border-radius: 8px
· padding: 16-24px

状态指示:
· 圆形脉冲点: 🟢绿/🟡黄/🔴红
· 风险等级: 5档色阶 (绿→黄→橙→红→深红)
· 进度条: 金橙渐变色
· 数据条: 简洁CSS实现，替代ECharts饼图/柱状图

导航栏:
· height: 56px, background: #1a1d2e
· 品牌 Logo + "旺财V4" 左侧
· 导航链接居中
· 移动端: 汉堡菜单折叠
```

### 7.4 项目图标

```
主体: 柴犬正面蹲坐剪影
· 配色: 金橙色 #f0a050 为主，腹部/面部白色
· 颈部: 红色项圈 → 风控约束（止盈止损纪律）
· 眼部: 绿色光点 → AI 智能洞察
· 底部: 金色铜钱纹样 → 旺财聚财
· 姿态: 警觉但沉稳 → 「AI推荐+人确认」哲学
```

---

## 八、移动端方案

- **技术方案**：PWA / 响应式 Web（不开发原生 App）
- **核心功能**：查看策略卡（风险等级/标的池/操作建议）+ 确认风险等级
- **推送方式**：飞书卡片消息 + Web Push 通知
- **不实现**：完整仪表盘、多角色对话、复杂操作

---

## 九、实现优先级

| 优先级 | 任务 | 依赖 |
|--------|------|------|
| P0 | 新建 StrategyInstance 数据模型 | 无 |
| P0 | 新建 ollama_pool.py 连接池 | 无 |
| P0 | 废弃 qwen3.5:35b 替换为 qwen3.6:35b-mlx | 无 |
| P0 | 拉取 bge-m3 替换 mxbai-embed-large | 无 |
| P1 | 实现 ①分析研判引擎 | P0 |
| P1 | 实现 ②策略工坊（辩论+风险定级） | P0 |
| P1 | 删除清单中的文件 | P0 |
| P2 | 实现 ③执行规划引擎 | P1 |
| P2 | 实现 ⑥每日审查引擎 | P1 |
| P2 | 前端页面重设计（4+1页） | P1 |
| P3 | 实现 ⑤绩效回顾 | P2 |
| P3 | 移动端飞书卡片推送 | P2 |
| P3 | 用户偏好学习机制 | P2 |
| P4 | 缓存层缩减（L3/L4移除） | P2 |
| P4 | DeepSeek 云端模型集成 | P0 |

---

## 十、风险与注意事项

1. **显存管理**：35b + 27b 同时加载 ≈ 40GB，接近 Mac 统一内存上限。需严格顺序调用
2. **云端 API 可用性**：DeepSeek API 依赖网络和配额，需本地 fallback 机制
3. **数据迁移**：新增 `strategy_instances` 表，旧数据不受影响
4. **ollama_pool.py**：新模块，连接池/排队/切换逻辑需要充分测试
5. **飞书推送**：依赖 `.env.local` 配置，留空时静默跳过
6. **偏好学习**：初始阶段冷启动，需默认偏好配置

---

*设计文档版本：1.0*
*关联：CHANGELOG.md、docs/architecture/system-design.md*

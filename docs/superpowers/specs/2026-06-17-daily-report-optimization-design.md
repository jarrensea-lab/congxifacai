# 每日报告系统优化设计 — 模块化报告引擎

> 版本: v1.0 | 日期: 2026-06-17 | 状态: 设计稿

## 背景与目标

### 现状问题

当前恭喜发财V7系统存在5种日报输出，各自为政、格式不统一：

| 报告类型 | 触发时间 | 内容特点 | 问题 |
|---------|---------|---------|------|
| 系统日报 | 15:35 | 仅API/DeepSeek/数据源健康检查 | 过于单薄 |
| 盘前策略报告 | 09:05 | AI辩论产出，结构有但不稳定 | 输出格式不固定 |
| 午盘快报 | 11:35 | 简短市场概况 | 内容随意 |
| 个股可操作方案 | 收盘后手动 | 详细复盘+四场景策略 | 未自动化 |
| 漫画全景报告 | 收盘后手动 | 7页视觉化报告 | 手动制作，无标准化 |

**三大核心问题：**
1. **板块不统一** — 各报告结构、粒度、侧重点不一致
2. **内容不统一** — AI输出格式不稳定，缺少标准化约束
3. **缺少可视化与多维表格** — 纯文本为主，无信息图/飞书云文档/多维表格看板

### 优化目标

1. 统一所有报告的内容模板和板块划分
2. 新增信息图自动生成，上传飞书云文档
3. 新增飞书多维表格看板，全天自动更新
4. 系统健康检查增加 Qwen API 检测
5. 保持模块化架构，便于后续扩展

---

## 整体架构

### 模块结构

```
backend/app/report_engine/
├── __init__.py
├── engine.py                    # 核心调度引擎
├── report_schema.py             # 标准化数据模型
├── templates/
│   ├── premarket.py             # 盘前策略报告模板
│   ├── midday.py                # 午盘快报模板
│   ├── afternoon_risk.py        # 午后风控模板
│   └── closing.py               # 收盘全景报告模板
├── renderers/
│   ├── markdown_card.py         # Markdown → 飞书消息卡片
│   ├── infographic.py           # 信息图生成 (matplotlib+PIL)
│   ├── feishu_doc.py            # 飞书云文档生成/上传
│   └── bitable_writer.py        # 飞书多维表格写入
└── __init__.py
```

### 设计原则

- **模板与渲染分离** — 内容结构 vs 输出格式解耦，同一份内容可输出多种格式
- **标准化数据结构** — 所有报告遵循统一 Schema，AI输出经清洗后填充
- **增量接入** — 先改造盘前策略+收盘全景，再逐步覆盖午盘和风控
- **单次生成，多渠道输出** — 一次报告数据同时输出消息卡片+信息图+云文档+多维表格

---

## 标准化数据模型

```python
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class PositionItem(BaseModel):
    code: str
    name: str
    quantity: int
    cost_price: float
    current_price: float
    profit_pct: float
    market_value: float
    risk_level: str  # normal/warning/danger

class Recommendation(BaseModel):
    code: str
    name: str
    strategy_type: str        # short_term / mid_low_freq
    buy_range: str
    stop_loss: str
    target: str
    reason: str
    technical_signals: str    # 技术面信号
    concept_tags: list[str]   # 题材概念标签
    trend_score: int          # 1-10
    beginner_guide: str       # 新手解读
    recommend_date: str       # 推荐日期

class RiskAlert(BaseModel):
    stock_code: str
    stock_name: str
    alert_type: str
    level: str               # low/mid/high
    message: str
    suggestion: str
    timestamp: str

class PerformanceData(BaseModel):
    daily_pnl: float
    daily_pnl_pct: float
    cumulative_pnl: float
    win_rate: float
    position_count: int
    total_assets: float
    available_cash: float

class SystemHealth(BaseModel):
    api_service: bool
    deepseek_api: bool
    qwen_api: bool           # 新增
    tencent_data: bool
    eastmoney_data: bool
    tushare_data: bool
    tasks_success: int
    tasks_fail: int
    last_error: Optional[str]

class ReportData(BaseModel):
    report_type: str              # premarket/midday/risk/closing
    generated_at: datetime
    date: str
    risk_level: int               # 1-5
    market_direction: str
    market_summary: str
    confidence: int
    positions: list[PositionItem]
    recommendations: list[Recommendation]
    alerts: list[RiskAlert]
    performance: Optional[PerformanceData]
    system_health: Optional[SystemHealth]
    knowledge_tip: str
    top_sectors: list[str]
```

---

## 每日数据流与时间线

```
09:05 ─ 盘前策略报告
      │
      ├─→ 飞书消息卡片 (Markdown摘要, 3000字以内)
      ├─→ 信息图PNG → 飞书云文档上传 🆕
      ├─→ 多维表格·策略总览 (新增一条) 🆕
      └─→ 多维表格·标的池 (覆盖更新) 🆕
      
11:35 ─ 午盘快报
      │
      ├─→ 飞书消息卡片
      ├─→ 多维表格·市场指数 (更新) 🆕
      └─→ 多维表格·持仓监控 (实时更新) 🆕
      
14:00 ─ 午后风控检查
      │
      ├─→ 飞书消息卡片 (仅预警时推送)
      ├─→ 多维表格·风险预警 (追加) 🆕
      └─→ 多维表格·持仓监控 (更新) 🆕
      
15:05 ─ 收盘全景报告
      │
      ├─→ 飞书消息卡片
      ├─→ 飞书云文档 (详细复盘文档) 🆕
      ├─→ 信息图PNG → 飞书云文档上传 🆕
      ├─→ 多维表格·绩效追踪 (追加一条) 🆕
      └─→ 多维表格·策略总览 (更新状态) 🆕
```

### 飞书多维表格看板设计 (6个维度表)

| 维度 | 写入时机 | 字段 |
|------|---------|------|
| **今日策略总览** | 盘前新增 | 日期、风险等级、市场方向、置信度、仓位建议、看好板块、状态 |
| **标的池** | 盘前覆盖 | 代码、名称、策略类型、买入区间、止损、目标、趋势评分、题材标签、技术面信号、AI理由、适合人群、推荐日期 |
| **持仓监控** | 盘中实时 | 代码、名称、持仓量、成本、现价、盈亏%、市值、风控状态、更新时间 |
| **市场指数** | 盘中更新 | 指数名称、当前点位、涨跌幅、成交量、更新时间 |
| **风险预警** | 实时追加 | 时间、标的、预警类型、级别、消息、建议、处理状态 |
| **绩效追踪** | 收盘追加 | 日期、日盈亏、累计盈亏、胜率、持仓数、总资产、可用现金 |

---

## 各报告模板设计

### 盘前策略报告模板

**板块划分（固定顺序）：**

1. **⚠️ 风险预警（首要）** — 市场风险、持仓风险、标的止损线
2. **📊 市场背景** — 大盘方向、置信度、核心理由、反方自检
3. **💰 仓位管理** — 建议仓位、现金比例、看好的板块、各标的仓位分配
4. **⚡ 短线机会 (1-5天)** — 标的列表、买入区间、止损、目标、新手解读
5. **📈 中线机会 (1-4周)** — 同上
6. **🔬 产业链深度** — 研究员视角的产业逻辑分析
7. **📚 知识角** — 投资者教育内容

**输出格式：** 飞书消息卡片 + 信息图PNG

**信息图内容：**
- 仓位分配饼图
- 标的买入/止损/目标价格柱状图
- 风险仪表盘（风险等级指示）
- 市场概况卡片（指数、方向、置信度）

### 午盘快报模板

**板块划分：**
1. **🌤️ 上午盘面概况** — 指数涨跌、成交量、热点板块
2. **💼 持仓表现 vs 大盘** — 各标的盈亏、与大盘对比
3. **🎯 下午策略调整** — 操作建议、关注信号
4. **💡 新手提示** — 下午盯盘要点

**输出格式：** 飞书消息卡片 + 多维表格更新

### 午后风控模板

**板块划分：**
1. **🛡️ 持仓风险扫描** — 各标的止损线距离、预警状态
2. **📉 大盘异动监控** — 指数异常波动提醒
3. **🎯 尾盘操作建议** — 是否调整仓位
4. **💳 账户健康概览** — 总资产、现金、持仓市值

**输出格式：** 飞书消息卡片（仅预警时推送，常规状态只在多维表格更新）

### 收盘全景报告模板

**板块划分：**
1. **📊 今日交易回顾** — 买卖记录、成交价格、执行情况 vs 策略计划
2. **💼 持仓表现** — 各标的盈亏、持仓市值、日内表现
3. **📈 大盘复盘** — 上证/深证/创业板涨跌、成交量、北向资金
4. **⚖️ 策略执行检查** — 是否遵守止损/止盈纪律、偏差分析
5. **⚠️ 风控事件** — 今日触发的风险告警及处理情况
6. **🔮 明日预告** — 关注标的、关键价位、策略思路
7. **⚙️ 系统健康** — API/DeepSeek/Qwen/数据源状态、任务执行统计

**输出格式：** 飞书消息卡片 + 飞书云文档 + 信息图PNG

**系统健康检查新增：**
- DeepSeek API 连通性
- **Qwen (通义千问) API 连通性** 🆕
- 腾讯行情数据源
- 东方财富数据源
- Tushare 数据源
- 定时任务执行统计（成功/失败/耗时）

---

## 飞书集成方案

### 信息图 → 飞书云文档

```
1. 生成信息图PNG → /tmp/congxi_v6_charts/{type}_{date}.png
2. 使用 lark-cli drive +upload-media 上传图片
3. 获取上传后的 file_token
4. 创建飞书云文档，将图片插入文档中
5. 获取文档链接，附在飞书消息卡片中
```

### 多维表格写入

使用 `lark-cli base +record-create` / `+record-update` 操作多维表格：

```python
# 示例：新增一条策略总览记录
lark-cli base +record-create \
  --app-token {bitable_app_token} \
  --table-id {table_id} \
  --fields '{"日期":"2026-06-17","风险等级":"R3","市场方向":"震荡偏多","置信度":7}'
```

需要提前在飞书创建好6张维度的多维表格，将 app_token 和 table_id 配置到环境变量中。

---

## 模块实现计划

### Phase 1: 基础设施 (优先级P0)

| 任务 | 文件 | 说明 |
|------|------|------|
| 创建模块结构 | `report_engine/` | 目录、`__init__.py`、`engine.py` |
| 标准化数据模型 | `report_schema.py` | `ReportData` 等 Pydantic 模型 |
| 飞书云文档上传 | `feishu_doc.py` | 图片上传 + 文档创建 |
| 多维表格写入 | `bitable_writer.py` | `lark-cli` 封装 |

### Phase 2: 报告改造 (优先级P1)

| 任务 | 文件 | 说明 |
|------|------|------|
| 盘前策略模板 | `premarket.py` | 标准化7板块模板 |
| 收盘全景模板 | `closing.py` | 标准化7板块模板 |
| 信息图生成 | `infographic.py` | 仓位图+风险表+概况卡片 |
| Markdown卡片 | `markdown_card.py` | 统一的消息卡片构建 |

### Phase 3: 多渠道输出 (优先级P2)

| 任务 | 说明 |
|------|------|
| 盘前策略全渠道输出 | 卡片+信息图+云文档+多维表格 |
| 收盘全景全渠道输出 | 卡片+云文档+信息图+多维表格 |
| 系统健康增强 | 增加 Qwen 检查 |

### Phase 4: 完善覆盖 (优先级P3)

| 任务 | 说明 |
|------|------|
| 午盘快报模板标准化 | 接入报告引擎 |
| 午后风控模板标准化 | 接入报告引擎 |
| 多维表格看板全维度上线 | 6张表全部接入 |

---

## 不影响的部分

- 调度时间表不变（09:05/11:35/14:00/15:05）
- 数据库 schema 不变
- API 端点不变
- AI辩论引擎核心逻辑不变
- 现有仓位管理、交易引擎不变
- 飞书Bot指令系统不变

---

## 环境变量新增

```bash
# 飞书多维表格配置
FEISHU_BITABLE_APP_TOKEN=xxx
FEISHU_TABLE_STRATEGY=table_id_1      # 策略总览
FEISHU_TABLE_STOCK_POOL=table_id_2    # 标的池
FEISHU_TABLE_POSITIONS=table_id_3     # 持仓监控
FEISHU_TABLE_INDICES=table_id_4       # 市场指数
FEISHU_TABLE_RISK=table_id_5          # 风险预警
FEISHU_TABLE_PERFORMANCE=table_id_6   # 绩效追踪

# Qwen API (新增)
QWEN_API_KEY=sk-xxx
QWEN_API_URL=https://dashscope.aliyuncs.com/api/v1
```

---

## 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 飞书API限频 | 低 | 中 | 加本地缓存，批量写入 |
| 信息图生成失败 | 中 | 低 | 降级为纯文本推送，不影响主流程 |
| 多维表格字段类型不匹配 | 中 | 中 | 写入前做类型校验 |
| Qwen API不可用 | 低 | 低 | 系统健康标记为不可用，不影响其他功能 |

---

*设计稿版本: v1.0 | 生成日期: 2026-06-17 | 待用户审阅后进入实现阶段*

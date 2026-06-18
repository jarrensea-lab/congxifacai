# 系统架构设计

> 自动提取自 `README.md`、`CHANGELOG.md`、`backend/app/main.py`

## 整体架构

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Vue3 前端   │────▶│ FastAPI 后端  │────▶│  Ollama 服务  │
└──────────────┘     └──────────────┘     └──────────────┘
         │                   │                       │
         │                   ▼                       │
         │            ┌──────────────┐               │
         └───────────▶│   SQLite     │◀──────────────┘
                      │   数据库       │
                      └──────────────┘
```

## 技术栈

| 层次 | 技术 | 说明 |
|------|------|------|
| 前端 | Vue3 + Vite + Pinia | SPA 单页应用，暗色主题 |
| 后端 | FastAPI + Python 异步 | REST API + WebSocket |
| 数据库 | SQLite + SQLAlchemy | 本地持久化，零运维 |
| AI 引擎 | Ollama + Qwen3.5 / DeepSeek-R1 | 本地推理，多模型分工 |
| 通信 | WebSocket + REST API | 实时推送 + 按需请求 |
| 缓存 | 分层缓存 (L1-L4) | 20s/2min/8min/1h 四级 TTL |
| 调度 | APScheduler (AsyncIOScheduler) | 9 个交易时段定时任务 |
| 通知 | 飞书 Webhook | 风险告警 + AI 策略推送 |
| 图表 | ECharts | 前端数据可视化 |

## 数据流

```
市场数据源 (腾讯/东财/新浪)
    │
    ▼
多源路由层 (data_router.py)
  ├── 首选: 腾讯财经
  ├── 备选: 东方财富 / 新浪财经
  └── 熔断: 指数退避 (30s→60s→120s→240s→300s)
    │
    ▼
分层缓存 (tiered_cache.py)
  ├── L1 实时 (20s)
  ├── L2 日内 (2min)
  ├── L3 高频 (8min)
  └── L4 低频 (1h)
    │
    ▼
AI 辩论引擎 (debate.py)
  ├── 猎手 (35B) ── 短线分析
  ├── 账房 (9B)  ── 中低频分析
  ├── 守夜人 (9B) ── 风控检查
  └── 裁判 (R1/9B) ── 综合决策
    │
    ▼
前端展示 + 飞书推送
```

## 定时任务调度

9 个定时任务（仅交易日 周一至周五）：

| 时间 | 任务 | 说明 |
|------|------|------|
| 09:00 | 盘前 AI 辩论 | 全模型辩论 (猎手+账房+守夜人+裁判)，生成双轨策略 |
| 09:35 | 风险检查 #1 | 多维度风控评分 |
| 10:30 | 风险检查 #2 | 多维度风控评分 |
| 11:30 | 风险检查 #3 + 午间盘分析 | 风控 + 盘中快速分析 (9B, ~60s) |
| 13:05 | 风险检查 #4 | 多维度风控评分 |
| 14:00 | 风险检查 #5 + 下午盘分析 | 风控 + 盘中快速分析 (9B, ~60s) |
| 15:00 | 收盘复盘 | 单模型复盘 (9B)，总结当日盈亏 |

## 核心设计决策

### 1. 双轨策略（v2.1.0）
所有 AI 分析输出短线 (1-5天) 和中低频 (1-4周) 两套独立建议。

### 2. 多模型分工（v2.3.0）
从单一模型升级为四角色分工，详见 [多模型分工策略](model-routing.md)。

### 3. 异步生成 + 轮询（v2.2.0）
AI 生成改为后台异步执行 + 前端轮询，解决 30s 超时问题。

### 4. 多源容错（v2.0.0）
数据源采用首选→备选→熔断策略，指数退避自动恢复。

### 5. 本地优先
所有数据本地化、AI 模型本地运行，不上传云端。

## 数据库模型

### holdings（持仓表）
`id | code | name | position | cost_price | add_time | update_time | is_active`

### risk_alerts（风险告警表）
`id | stock_code | stock_name | alert_type | alert_level | alert_message | suggestion | timestamp`

### ai_strategies（AI 策略表）
`id | strategy_type | content | recommended_stocks | timestamp`

## API 端点概览

| 类别 | 端点数 | 说明 |
|------|--------|------|
| 持仓管理 | 3 | GET/POST/DELETE `/api/holdings` |
| 实时数据 | 4 | 单股/指数/板块/涨跌分布 |
| AI 分析 | 6 | 盘前/盘中/复盘 GET 查询 + POST 生成 |
| 风险告警 | 2 | GET `/api/risk-alerts` + POST 触发 |
| 系统 | 2 | Health check + WebSocket |

---

> 来源: `README.md`、`CHANGELOG.md`、`backend/app/main.py`、`backend/app/models.py`

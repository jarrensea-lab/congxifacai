# 恭喜发财 V7 — Codex 自动化 A 股智能助手

> 基于 DeepSeek 云端 AI + 飞书全通道交互的个人量化交易助手

## V7 核心变化

| V6 | V7 |
|-----|-----|
| Vue3 Web 前端 | ❌ 砍掉，全部交互走飞书 |
| Ollama 本地 35B 模型 | DeepSeek v4-pro 云端 API (月均 ¥2.2) |
| AKShare 爬虫阻塞 | Tushare + a-stock-data 多源 |
| 飞书 Webhook 单一推送 | 飞书全通道 (消息卡片/多维表格/文档/画板/任务) |
| 手动运维 | Codex 交易日守护进程 |

## 架构

```
Codex 交易日守护
    │
    ├── FastAPI 后端 (仅 API 服务, 无前端)
    │   ├── DeepSeek v4-pro/v4-flash 云端 AI
    │   ├── Tushare + Tencent 数据源
    │   ├── 模拟交易引擎 (风控/撮合/信号)
    │   └── SQLite 本地存储
    │
    └── 飞书 App (全通道交互)
        ├── 消息卡片: 盘前策略/风险预警/午盘简报/系统日报
        ├── 多维表格: 选股池/回测/持仓/绩效
        ├── 飞书文档: 策略报告/复盘/周报
        ├── 画板: K线标注图/收益曲线
        └── 任务: 止盈止损提醒
```

## 快速开始

### 1. 配置

```bash
cp .env.example .env.local
# 编辑 .env.local:
#   DEEPSEEK_API_KEY=sk-xxx
#   TUSHARE_TOKEN=xxx
#   FEISHU_WEBHOOK_URL=https://open.feishu.cn/...
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 启动

```bash
# 直接启动
python backend/app/main.py

# 或通过守护进程
./scripts/guardian.sh &
```

## 每日自动化流程

```
08:55 启动检查 (API/DeepSeek/飞书)
09:00 盘前 AI 辩论 + 建仓计划 + 选股池 → 飞书
11:30 午盘快速分析 → 飞书卡片
14:00 午后风险检查
15:00 收盘复盘 → 飞书文档
15:30 系统日报 → 飞书卡片
```

## 飞书对话指令

```
买入 688347 华虹公司 100股 ¥250.5
卖出 688347 100股 ¥255
清仓 688347
查询持仓
今日策略
```

## 成本

DeepSeek API: 约 ¥0.10/交易日，月均 ¥2.20

## 项目结构

```
cong-xi-fa-cai-v6/
├── backend/
│   └── app/
│       ├── ai/          # AI 引擎 (DeepSeek + fallback)
│       ├── data_sources/ # 数据源 (Tushare/Tencent)
│       ├── engine/      # 分析/回测/生命周期
│       ├── services/    # 飞书通道/指令解析
│       ├── trading_engine/ # 模拟交易
│       └── utils/       # 缓存/日志/交易日历
├── scripts/
│   └── guardian.sh      # 交易日守护进程
└── docs/
    └── superpowers/specs/ # 设计文档
```

## 安全

- 所有敏感信息在 `.env.local` (已 gitignore)
- 交易数据本地 SQLite 存储
- AI 调用通过 DeepSeek API (数据不用于训练)

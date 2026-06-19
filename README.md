# 恭喜发财 — Codex 自动化 A 股智能助手

> 基于 DeepSeek 云端 AI + 飞书全通道交互的个人量化交易助手

**恭喜发财**是一个运行在 Codex 之上的自动化 A 股交易智能助手，由多角色 AI 辩论引擎驱动，覆盖盘前策略、盘中监控到收盘复盘的全交易流程。

当前版本 `v7.1.0`，迭代方向见 [ROADMAP.md](ROADMAP.md)。

---

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

# 或通过 launchd 守护（启动后自动运行）
launchctl load ~/Library/LaunchAgents/com.zhuchenyuan.congxicai-v6.plist
```

---

## 每日自动化流程

```
08:55 ── 启动检查（API/DeepSeek/飞书连通性）
09:05 ── 盘前 AI 辩论 + 建仓计划 + 选股池推送至飞书
11:35 ── 午盘快速分析推送至飞书
14:00 ── 午后风险检查
15:05 ── 收盘复盘生成飞书文档
15:35 ── 系统日报推送至飞书消息卡片
```

---

## 架构

```
恭喜发财/
├── backend/
│   └── app/
│       ├── ai/               # AI 引擎
│       │   ├── debate.py     # 多角色辩论（猎手/账房/守夜人/产业链研究员）
│       │   ├── serenity_analyst.py  # 产业链知识引擎（8层价值链）
│       │   └── cloud_client.py      # DeepSeek + Qwen-Plus 双模型路由
│       ├── data_sources/     # 多源数据层
│       │   ├── tushare_client.py    # Tushare 数据
│       │   ├── tencent_client.py    # 腾讯行情
│       │   └── eastmoney_client.py  # 东方财富
│       ├── engine/           # 分析/回测/策略工作流
│       │   ├── analysis.py   # 市场数据分析
│       │   ├── workshop.py   # 策略工作流编排
│       │   └── debate_tracker.py   # 辩论记录追踪
│       ├── services/         # 飞书通道 & 指令解析
│       │   ├── feishu_client.py    # 飞书消息卡片推送
│       │   └── bot_handler.py      # Bot 指令解析
│       ├── trading_engine/   # 模拟交易引擎
│       │   ├── account.py    # 账户管理
│       │   ├── broker.py     # 撮合引擎
│       │   ├── order_manager.py    # 订单管理
│       │   ├── risk_guard.py       # 风控（8道防线）
│       │   └── signal_engine.py    # 信号引擎
│       ├── report_engine/    # 报告生成
│       ├── routers/          # FastAPI 路由
│       └── utils/            # 缓存/日志/交易日历
├── scripts/
│   └── guardian.sh           # 交易日守护进程
└── docs/
    └── superpowers/specs/    # 设计文档
```

---

## 核心技术特性

### AI 辩论引擎（四角色并行）

| 角色 | 模型 | 职责 |
|------|------|------|
| 🎯 猎手（Hunter） | DeepSeek-chat | 技术面形态识别、量价异动、资金流分析 |
| 📊 账房（Accountant） | DeepSeek-chat | 基本面估值、财务健康评分、安全边际计算 |
| 🛡 守夜人（Watchman） | DeepSeek-chat | 风险扫描、下行空间评估、止损逻辑 |
| 🔬 研究员（Serenity） | Qwen-Plus | 产业链深度分析、供需缺口、技术壁垒、竞争格局 |

裁判角色（Qwen-Plus）聚合四路观点，输出统一策略。

### 多源数据层

- **Tushare Pro** — 日线/基本面/财务/资金流
- **腾讯行情** — 实时盘口/分时/K 线
- **东方财富** — 行业板块/资金流向/龙虎榜
- **AKShare** — 新闻情绪/市场指标
- **a-stock-data** — A 股全栈数据工具包

### 风险控制（8 道防线）

仓位约束 → 单票集中度 → 最大回撤 → 波动率过滤 → 流动性格栅 → 黑名单拦截 → 行业偏离度 → 相关性风险

### 飞书全通道

- **消息卡片** — 盘前策略/风险预警/午盘简报/系统日报
- **多维表格** — 选股池/回测记录/持仓/绩效
- **飞书文档** — 策略报告/周报复盘
- **画板** — K 线标注图/收益曲线
- **任务** — 止盈止损提醒

### 飞书对话指令

```
买入 688347 华虹公司 100股 ¥250.5
卖出 688347 100股 ¥255
清仓 688347
查询持仓
今日策略
```

---

## 成本

DeepSeek API: 约 ¥0.10/交易日，月均 ¥2.20

---

## 版本与命名

```
恭喜发财 v7.x.x    ← 当前迭代
恭喜发财 v8.x.x    ← 规划中
```
`congxi` 是「恭喜财」的拼音缩写，用于内部标识和项目路由名。

---

## 目录说明

| 目录 | 用途 |
|------|------|
| `backend/` | FastAPI 后端服务 |
| `scripts/` | 启动脚本、守护进程、数据初始化 |
| `data/` | 运行时数据（数据库、策略输出） |
| `docs/` | 设计文档、知识库 |
| `memory/` | Codex 项目上下文记忆 |

---

## 安全

- 所有敏感信息配置在 `.env.local`（已 `.gitignore`）
- 交易数据本地 SQLite 存储
- AI 调用通过 DeepSeek API（数据不用于训练）
- 飞书指令仅授权用户列表可执行交易操作

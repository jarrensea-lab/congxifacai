# 恭喜发财 — Codex 自动化 A 股智能助手

> 基于 DeepSeek/Qwen 云端 AI + Tushare 数据源 + 飞书 Webhook 群机器人的个人 A 股研究与交易辅助工作流

**恭喜发财**是一个运行在 Codex 之上的自动化 A 股交易智能助手，由多角色 AI 辩论引擎驱动，覆盖盘前策略、盘中监控到收盘复盘的全交易流程。

当前 feature 分支版本 `v7.5.0-dev`，核心方向是“盈利策略管线重构”。迭代方向见 [ROADMAP.md](ROADMAP.md)。

---

## 快速开始

### 1. 配置

```bash
cp .env.example .env.local
# 编辑 .env.local:
#   DEEPSEEK_API_KEY=sk-xxx
#   TUSHARE_TOKEN=xxx
#   FEISHU_WEBHOOK_URL=https://open.feishu.cn/...
#   FEISHU_WEBHOOK_ONLY=true
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 启动

```bash
# 直接启动
python backend/app/main.py

# 或安装 v7 launchd 守护（启动后自动运行）
scripts/install-congxicai-v7-launchd.sh
```

---

## 每日自动化流程

```
周日 20:30 ── 次日投资策略主报告（服务周一）
08:50 ── 盘前短策略校准
11:35 ── 午盘快速分析推送至飞书
14:00 ── 午后风险检查
15:05 ── 收盘复盘
20:30 ── 次日投资策略主报告（服务下一交易日）
21:00 ── Sentinel 绩效回看与归档
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
│       │   ├── sentinel_research.py # Sentinel 新闻证据包 + Serenity 深挖输入
│       │   └── cloud_client.py      # DeepSeek + Qwen-Plus 双模型路由
│       ├── data_sources/     # 多源数据层
│       │   ├── tushare_client.py    # Tushare 数据
│       │   ├── tencent_client.py    # 腾讯行情
│       │   └── eastmoney_client.py  # 东方财富
│       ├── engine/           # 分析/回测/策略工作流
│       │   ├── analysis.py   # 市场数据分析
│       │   ├── workshop.py   # 策略工作流编排
│       │   └── debate_tracker.py   # 辩论记录追踪
│       ├── services/         # 飞书通道、生命周期池 & 指令解析
│       │   ├── feishu_client.py    # 飞书消息卡片推送
│       │   ├── quant_lifecycle.py  # 生产候选池/持仓池扫描与提醒
│       │   ├── target_snapshot.py  # 单标的结构化数据快照
│       │   ├── target_scoring.py   # 账户可执行评分
│       │   ├── report_archive.py   # Markdown 日期归档
│       │   ├── schedule_policy.py  # 主报告/盘前校准交易日规则
│       │   ├── strategy_profile.py # 保守铁律 / 高收益试验模式
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
│   ├── daily_report.py       # 次日投资策略主报告
│   ├── guardian.sh           # v7 交易日监控脚本
│   └── install-congxicai-v7-launchd.sh
└── docs/
    ├── reviews/              # 代码/文件审查
    └── worklists/            # 执行 Worklist
```

---

## 核心技术特性

### v7.5.0-dev 盈利策略管线

`v7.5.0-dev` 将系统从“研究堆料/报告输出”推进到“账户可执行策略输出”：

- **主报告先回答动作**：持仓怎么处理、是否卖、是否买、买哪只、多少钱、什么价格、止损在哪里、今天不动明天等什么信号。
- **标的池分层**：`executable`、`watching`、`research_reference`、`removed` 四类状态，研究参照不能触发买入。
- **账户可执行评分**：按现金、单票预算、A 股最小交易单位和止损目标判断能不能买。
- **池外小账户补扫**：当标的池没有可买标的时，自动生成低价候选补扫，按量能线索、可买一手、追高风险排序，并在主报告给出次日观察价、试错金额、止损和目标。
- **数据快照归一**：每只标的聚合行情、K 线、资金流、北向、新闻/公告、财务、Sentinel 证据和 Serenity 深挖。
- **拒绝泛化观望**：不再用“数据不足，建议观望”兜底，必须写清缺哪类数据或哪个信号未触发。
- **项目清理**：移除 knowX/教程/旧 v6 启动残留，修正 `AGENTS.md` 和 `SYSTEM.md` 项目身份。

执行文档：[v7.5 Profit Pipeline Refactor Worklist](docs/worklists/2026-07-01-v7-5-profit-pipeline-refactor-worklist.md)。
审查报告：[v7.5 代码与文件审查报告](docs/reviews/2026-07-01-v7-5-code-file-review.md)。

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

当前支持两套报告期策略 profile：

| 模式 | 定位 | 关键参数 |
|------|------|----------|
| `capital_preservation` | 默认保守铁律 | 现金底线 30%，小账户单票 10%，单笔止损 3% |
| `growth_sprint` | 一周/短期高收益实验 | 现金底线 10%，单票 35%，账户最大回撤 -10%，单笔止损 5% |

`growth_sprint` 只改变报告和人工复核的风险边界，不承诺收益，也不触发自动交易。AI 原文若出现旧仓位或现金规则，以报告中的“机器可执行校验”为准。

### 飞书全通道

当前生产默认仅启用 **Webhook 群机器人消息卡片**，用于盘前策略、风险预警、午盘简报和系统日报摘要。

飞书多维表格、飞书文档、画板、任务和 lark-cli IM 通道暂时关闭，避免报告内容通过 API 通道写入外部表格或文档。需要重新启用时，先关闭 `FEISHU_WEBHOOK_ONLY` 并单独验证权限。

### Sentinel 研究证据层

`v7.3.0` 新增 Sentinel 自动化研究包与绩效回看入口：

- 接入 Tushare 高频滚动新闻原始归档，默认目录：
  `/Users/zhuchenyuan/AI/projects/司库/01-资料采集/量化投资/Serenity研究/数据采集/tushare-news`
- 支持 `raw/YYYY-MM-DD/*.jsonl` 原始新闻、`index/latest-status.json` 采集状态和 `digest/` 阶段摘要。
- `scripts/run_sentinel.py` 可生成 `data/sentinel/news_events/`、`research_packages/` 和 Sentinel Markdown 报告。
- Sentinel 输出仍是研究证据、主题雷达、候选复核和角色绩效旁路，不直接触发真实交易。
- 一周实验：Sentinel 会从热点主题中选择最多 3 个主题，生成 Serenity 产业链瓶颈深挖。深挖摘要写入 Sentinel 研究包，完整 Markdown 保留在 `恭喜发财报告/历史数据/Serenity深挖/YYYY-MM-DD/`，供学习复盘使用；不单独推送飞书，也不作为买卖指令。

Sentinel 与 Serenity 的边界：

- Sentinel 是新闻证据包、主题雷达和角色绩效复盘层。
- Serenity 是四人辩论中的产业链瓶颈研究员，也是 Sentinel 一周实验中的深度研究子模块。
- Serenity 深挖报告可以作为学习档案保留，但最终交易动作仍由四人辩论、裁判、账户约束和风控共同过滤。

### 次日投资策略主报告

主报告定位为盘后或周日晚生成，服务下一交易日盘前决策。报告结构包括：

- 明日总策略。
- 账户约束。
- 数据源审计。
- Sentinel 研究输入。
- 四人辩论矩阵。
- 裁判裁决。
- 明日执行剧本。

盘前只生成短策略校准，不重复生成长报告。

### Tushare 数据增强

Tushare 已购买 2000 积分，数据权限提升后，系统可使用更丰富的行情、新闻、财务和资金面证据。当前用于：

- 高频滚动新闻捕获与去重。
- Sentinel 研究证据包。
- Serenity/产业链候选的财务与行情核验。
- 日报和策略报告中的数据交叉验证。

### Markdown 本地归档

所有交易日报告都会保存 Markdown 到：

`/Users/zhuchenyuan/AI/projects/司库/01-资料采集/量化投资/恭喜发财报告`

目录按交易日组织：

```text
恭喜发财报告/
└── 2026/
    └── 06/
        └── 2026-06-29/
            ├── 2026-06-29_日报.md
            ├── 2026-06-29_盘前策略.md
            ├── 2026-06-29_盘中分析.md
            ├── 2026-06-29_收盘复盘.md
            ├── 2026-06-29_系统状态.md
            └── 日报索引.md
```

即使 Webhook 推送失败，本地 Markdown 也必须落地。

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

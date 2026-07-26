# 恭喜发财 — Codex 自动化 A 股智能助手

> 基于 DeepSeek/Qwen 云端 AI + Tushare/Tencent 数据源 + 飞书 OpenAPI/Webhook 的个人 A 股研究与交易辅助工作流

**恭喜发财**是一个运行在 Codex 之上的自动化 A 股交易智能助手，由多角色 AI 辩论引擎驱动，覆盖盘前策略、盘中监控到收盘复盘的全交易流程。

当前 feature 分支版本 `v8.2.0-dev`，核心方向是“预测账本 + 实时 K 线 + 绩效闭环”：用 Scrapling 抓取最快实时 K 线，沉淀全市场 T+1/T+3/T+5 预测样本，并把真实执行复盘反向约束下一次策略。迭代方向见 [ROADMAP.md](ROADMAP.md)。

---

## 快速开始

### 1. 配置

```bash
cp .env.example .env.local
# 编辑 .env.local:
#   DEEPSEEK_API_KEY=sk-xxx
#   TUSHARE_TOKEN=xxx
#   FEISHU_APP_ID=cli_xxx
#   FEISHU_APP_SECRET=xxx
#   FEISHU_CHAT_ID=oc_xxx
#   FEISHU_WEBHOOK_URL=https://open.feishu.cn/...
#   FEISHU_WEBHOOK_ONLY=false
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 启动

```bash
# 直接启动
python backend/app/main.py

# 或安装现有 launchd 守护（启动后自动运行）
scripts/install-congxicai-v7-launchd.sh
```

---

## 每日自动化流程

```
周日 20:30 ── 次日投资策略主报告（服务周一）
08:50 ── 盘前短策略校准
08:55 ── 易淘金账户→自选→重点行情（可选，默认关闭）
11:35 ── 午盘快速分析推送至飞书
11:35 ── 易淘金重点行情独立校验（可选，默认关闭）
14:00 ── 午后风险检查
14:55 ── 易淘金收盘前重点行情校验（可选，默认关闭）
15:05 ── 收盘复盘
15:25 ── 预测账本采集与到期评估
20:30 ── 次日投资策略主报告（服务下一交易日）
20:45 ── 易淘金账户→自选同步（可选，默认关闭）
21:00 ── Sentinel 绩效回看与归档
```

---

## 广发易淘金安全接入

项目已经具备 Mac 版广发易淘金的受控桥接能力，但默认完全关闭，且尚未经过用户在场的生产写入验收。它只承担三件事：

- 读取账户资产与持仓，经过账户指纹、空仓证据和资产勾稽校验后，才可写回项目持仓真值。
- 把当前持仓及 Target Pool 中的 `executable`、`watching` 投影到普通自选股页面；永不把“进入自选”解释为买入授权。
- 只读取上述有限股票的行情，用 30/90 秒新鲜度和 0.5% 多源价差门约束新开仓；读取失败不会隐藏止损、卖出或风险告警。

硬边界：固定目标为 `/Applications/GF-Trader.app`；不读取交易密码、验证码或完整账户号；不修改客户端内部数据库；不调用私有交易接口；不自动下单、撤单或转账。自选股只允许移除系统自己添加、已退出目标池、当前不持仓且连续两次确认的股票，手工自选永久保护。

启用需要两个独立开关：

```bash
# 只读账户、自选和有限行情；默认 false
CONGXI_YITAOJIN_ENABLED=false

# 账户写回项目 + 受控自选写入；默认 false
CONGXI_YITAOJIN_WRITE_ENABLED=false
```

只读准备、首次绑定、dry-run、故障处理和关闭方法见 [易淘金接入运行手册](docs/runbooks/yitaojin-integration.md)。任何真实写入、辅助功能授权和服务重启前，必须逐项完成 [人工生产验收清单](docs/acceptance/yitaojin-production-checklist.md)。

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
│       │   ├── realtime_kline_scraper.py # Scrapling + 东方财富实时 K 线
│       │   ├── realtime_market_data.py   # 快速实时行情门面
│       │   └── eastmoney_client.py  # 东方财富
│       ├── engine/           # 分析/回测/策略工作流
│       │   ├── analysis.py   # 市场数据分析
│       │   ├── workshop.py   # 策略工作流编排
│       │   └── debate_tracker.py   # 辩论记录追踪
│       ├── services/         # 飞书通道、生命周期池、剧本和风控预算
│       │   ├── feishu_client.py    # 飞书 Bot/旧通道封装
│       │   ├── feishu_pusher.py    # 飞书 OpenAPI 优先、Webhook 兜底推送
│       │   ├── quant_lifecycle.py  # 生产候选池/持仓池扫描与提醒
│       │   ├── target_snapshot.py  # 单标的结构化数据快照
│       │   ├── target_scoring.py   # 账户可执行评分
│       │   ├── prediction_lab.py   # T+1/T+3/T+5 预测账本与回看
│       │   ├── recommendation_review.py # 真实执行复盘评分
│       │   ├── market_regime.py    # 市场状态过滤
│       │   ├── playbook_engine.py  # breakout/dip 交易剧本选择
│       │   ├── position_sizing.py  # 风险预算仓位计算
│       │   ├── notification_gate.py # 飞书预警去重、冷却和聚合
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

### v8.2.0-dev 预测账本与实时 K 线闭环

`v8.2.0-dev` 将系统从“只复盘真实成交”推进到“每天生成可验证预测样本”：不再只研究用户买过的个股，而是对 Target Pool 或 Tushare 全 A 股票池生成 T+1/T+3/T+5 预测记录，到期后自动验证方向命中、收益误差、超额收益和触发理由表现。

- **Scrapling 实时 K 线**：实测本机最快实时 K 线源为东方财富 `push2his` 1分钟 K 线，中位延迟约 95ms；`realtime_kline_scraper.py` 用 Scrapling 抓取 Eastmoney K 线，支持 `1m/5m/15m/30m/60m/day`。
- **快速行情门面**：`realtime_market_data.py` 保留腾讯实时 quote，K 线优先走 Scrapling/Eastmoney，失败再退回腾讯，避免单一源故障影响盘中扫描和报告评分。
- **高位追买拦截**：`playbook_engine.py` 增加近 20 日区间位置判断，放量上涨但位于区间 80% 以上时降级为 `blocked_high_position / breakout_watch`，不再直接给建仓或加仓建议。
- **预测账本**：`prediction_lab.py` 对候选池或全 A 股票池生成 T+1/T+3/T+5 预测记录，字段包括价格、涨幅、量比、成交额、区间位置、均线、5日涨幅、预测方向、预期收益、置信度和触发理由。
- **到期评估**：`scripts/run_prediction_lab.py evaluate` 会在 K 线走出后验证预测，输出方向命中、实际收益、收益误差、基准超额和触发理由表现；15:25 定时任务会采集当天样本并回看近 10 天到期样本。
- **真实执行复盘**：`recommendation_review.py` 读取本地持仓/已平仓记录、实时行情和 K 线，计算真实执行样本的收益、入场区间、行为分和问题 flags，并接入 Sentinel 21:00 review。
- **策略调整建议**：预测回看会按触发理由聚合表现，生成 `downweight_trigger`、`upweight_trigger` 或 `keep_weight` 建议；当前默认只产出可审计建议，不自动黑箱改参数。

### v8.1.0-dev 中长期研究闭环

`v8.1.0-dev` 将 ai-berkshire 式中长期研究方法接入恭喜发财主链路，但保持交易边界：长期 thesis 不会单独触发买入，P6 飞书长期事件提醒仍需人工确认后开启。

- **长期 thesis 存储**：新增长期论文 store，记录核心假设、红线、估值锚和复核记录。
- **长期状态入池但不扫短线**：Target Pool 支持 `long_research`、`long_watch`、`accumulation_zone`、`thesis_review`、`exit_candidate` 等状态，默认不进入短线 active scan。
- **Evidence Ledger 长期证据**：长期论文、假设和红线生成稳定 evidence id，保留 `confidence`、`source_report_path`、`data_cutoff_date`。
- **评分拆分**：`target_scoring.py` 新增 `score_long_quality()`，长期质量分只作为约束和解释字段，不绕过交易剧本与账户风控。
- **Serenity 升级**：Serenity 输出长期假设、去劣红线、估值问题、季度核验任务、瓶颈持续性和替代路径风险。
- **日报接入**：次日主报告增加“长期依据摘要”和“本次动作性质”，第一屏仍保持可执行动作优先。

执行文档：[v8.1 Long Horizon Integration Plan](docs/worklists/2026-07-06-v8-1-long-horizon-integration-plan.md)。
架构契约：[Long Horizon Contract](docs/architecture/long-horizon-contract.md)。

### v8.0.0-dev 盈利操作系统

`v8.0.0-dev` 将系统从“账户可执行策略输出”推进到“可复盘的盈利操作系统”：

- **市场状态先行**：`market_regime.py` 判断指数、赚钱效应和板块相对强弱；冰点、恐慌、单边下跌或板块过弱时禁止低吸接飞刀。
- **交易剧本选择**：`playbook_engine.py` 将买点拆成 `breakout_entry` 和 `dip_entry`，分别处理放量突破和低吸二次回踩，不再把所有机会压进单一强势确认评分。
- **风险预算仓位**：`position_sizing.py` 用账户权益、现金底线、单票上限、每笔风险预算、入场价和止损价倒推出可买股数，避免“50% 单票上限 + 5% 止损”直接吞噬账户回撤空间。
- **候选池生产化**：Target Pool 继续区分 `executable`、`watching`、`research_reference`、`removed`，并支持 `blocked_chasing`、`risk_budget_too_small`、`regime_blocks_dip` 等 v8 阻断状态，研究线索必须经过剧本、市场状态和仓位预算后才能提醒。
- **池外机会进入雷达**：低价高流动性池外扫描不再只写进报告，而是把买得起、具备触发线索的标的提升为候选池观察项，进入后续盘中扫描。
- **飞书额度安全**：飞书 OpenAPI 优先、Webhook 兜底；`tenant_access_token` 在常驻进程中缓存，`notification_gate.py` 对候选池/持仓预警做本地去重、冷却和聚合，连续盘中扫描不会把每次轮询都推给飞书。
- **报告与提醒分工**：固定日报、盘前、午后和收盘报告保持完整推送；高频盘中扫描只在状态变化、止损止盈或可执行机会出现时触发提醒。
- **AI 的优势位置明确**：Sentinel/Serenity 负责主线发现、证据整理、风险事件和产业链断层，不直接发买卖指令；买点和仓位由确定性规则、硬数据和账户约束落地。

顶层审查：[v8 盈利系统顶层逻辑与架构审查](docs/architecture/2026-07-03-v8-profit-system-architecture-review.md)。

### v7.5 盈利策略管线地基

`v7.5` 已经完成从“研究堆料/报告输出”到“账户可执行策略输出”的地基：

- **主报告先回答动作**：明日唯一实盘狙击标的、买入逻辑、触发价、一手金额、止损/目标和盘前复核信号。
- **标的池分层**：`executable`、`watching`、`research_reference`、`removed` 四类状态，研究参照不能触发买入。
- **账户可执行评分**：按现金、单票预算、A 股最小交易单位和止损目标判断能不能买。
- **小账户低价候选扫描**：当标的池没有可买标的时，自动生成低价候选；买得起且有量能线索的候选会升格到“明日唯一实盘狙击标的”或“明日盘中雷达触发池”，买不起的统一隐藏为预算阻断数量。
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
- **本地离线分钟档案** — 通过 `data/external_market_data_sources.json`
  登记外部 ZIP 和 Tushare 复权因子，按股票懒读取，不复制或解压整套数据。
  该来源强制为 `shadow`，仅供回测、预测补样和策略实验，不能替代实时行情。

本地登记文件默认不进入 Git。可从
`data/examples/external_market_data_sources.example.json` 复制后填写绝对路径，
也可用 `CONGXI_EXTERNAL_MARKET_DATA_REGISTRY` 指向其他登记文件。历史回测在登记
文件有效时优先读取离线数据并按日计算前复权；数据缺失、ZIP 损坏或复权因子不完整
时会失败关闭，并只在离线源明确返回错误时回退到 Tushare/腾讯。

### 风险控制（8 道防线）

仓位约束 → 单票集中度 → 最大回撤 → 波动率过滤 → 流动性格栅 → 黑名单拦截 → 行业偏离度 → 相关性风险

当前支持两套报告期策略 profile：

| 模式 | 定位 | 关键参数 |
|------|------|----------|
| `growth_sprint` | 当前默认：短期高收益实验 | 现金底线 10%，单票上限 50%，账户最大回撤 -10%，每笔账户风险 2%，止损 10%，最低目标 20%（盈亏比至少 2:1） |
| `capital_preservation` | 可手动切回的保守铁律 | 现金底线 30%，小账户单票 10%，单笔止损 3%，每笔风险预算 0.5% |

`growth_sprint` 只改变报告和人工复核的风险边界，不承诺收益，也不触发自动交易。v8 的实际买入股数会再经过 `position_sizing.py` 的风险预算倒推。需要恢复保守档时设置 `CONGXI_STRATEGY_MODE=capital_preservation`。AI 原文若出现旧仓位或现金规则，以报告中的“机器可执行校验”为准。

AI 推荐进入生产候选池采用 fail-closed：裁判质量校验必须明确通过，且任一角色/裁判输出不得带有 `degraded` 或 `error` 标记。校验服务异常、空响应或降级结果仍可进入本地快照和报告审计，但不会写入生产候选池。

### 飞书全通道

当前生产优先使用 **飞书 OpenAPI 群聊卡片**，Webhook 群机器人作为兜底，用于盘前策略、风险预警、午盘简报和系统日报摘要。

额度控制规则：

- `tenant_access_token` 在常驻进程内缓存，避免每条消息都请求 token。
- 候选池和持仓预警通过 `notification_gate.py` 本地去重、冷却和聚合。
- 固定报告不进通知 gate，避免节流误伤主报告。
- 飞书多维表格、飞书文档、画板、任务和 lark-cli IM 通道仍不作为生产主路径，避免报告内容写入外部表格或文档。需要重新启用时，先单独验证权限和额度。

### Sentinel 研究证据层

`v7.3.0` 新增 Sentinel 自动化研究包与绩效回看入口：

- 接入 Tushare 高频滚动新闻原始归档，默认目录：
  `/Users/zhuchenyuan/AI/projects/司库/01-资料采集/量化投资/Serenity研究/数据采集/tushare-news`
- 支持 `raw/YYYY-MM-DD/*.jsonl` 原始新闻、`index/latest-status.json` 采集状态和 `digest/` 阶段摘要。
- `scripts/run_sentinel.py` 可生成 `data/sentinel/news_events/`、`research_packages/` 和 Sentinel Markdown 报告。
- Sentinel 输出仍是研究证据、主题雷达、候选复核和角色绩效旁路，不直接触发真实交易。
- 每日研究任务：交易日和周日 20:00，Sentinel 从热点主题中筛选最多 3 个具有候选映射的主题，生成 Serenity 产业链瓶颈深挖；无候选映射的空主题不会占用深挖名额。深挖摘要写入 Sentinel 研究包，完整 Markdown 保留在 `恭喜发财报告/历史数据/Serenity深挖/YYYY-MM-DD/`，供学习复盘使用；不单独推送飞书，也不作为买卖指令。

Sentinel 与 Serenity 的边界：

- Sentinel 是新闻证据包、主题雷达和角色绩效复盘层。
- Serenity 是四人辩论中的产业链瓶颈研究员，也是 Sentinel 每日研究任务中的深度研究子模块。
- Serenity 深挖报告可以作为学习档案保留，但最终交易动作仍由四人辩论、裁判、账户约束和风控共同过滤。

### 次日投资策略主报告

主报告定位为盘后或周日晚生成，服务下一交易日盘前决策。报告结构包括：

- 明日唯一实盘狙击标的：干不干、干谁、买入逻辑、触发价、一手金额、止损和第一目标。
- 明日盘中雷达触发池：只放账户买得起、可能盘中触发或急需补数据的候选。
- 持仓与市场风控：持仓处理、市场风险、赚钱效应和机器可执行校验。
- 后台风控与策略审计：预算阻断数量、关键数据缺失、中低频观察/配置线和标的分层；买不起的个股名称不进入主报告正文。
- 数据覆盖与评分审计：数据源、结构化评分和角色投票审计。
- 复盘与研究归档：复盘规则、Sentinel/Serenity 证据链接和本地归档。

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
恭喜发财 v8.x.x    ← 当前迭代：盈利操作系统
恭喜发财 v7.x.x    ← 已完成地基：账户可执行策略管线
```
`congxi` 是「恭喜财」的拼音缩写，用于内部标识和项目路由名。

---

## 运行时数据库

- 业务状态默认写入本机磁盘 `~/Library/Application Support/congxicai-v7/stock_data.db`。
- APScheduler 作业独立写入 `~/Library/Application Support/congxicai-v7/scheduler_jobs.db`，避免调度写锁与业务查询共用一个 SQLite 文件。
- 可用 `CONGXI_STATE_DIR` 修改共同根目录，或用 `CONGXI_DATABASE_PATH`、`CONGXI_SCHEDULER_DATABASE_PATH` 分别覆盖。
- `data/user_portfolio.json` 仍是真实账户持仓与现金的事实源；数据库迁移不会改写该文件。
- 外置项目目录中的旧数据库只作为回滚副本保留，不再作为常驻服务默认写入位置。

迁移现有数据库时使用 `scripts/migrate_runtime_databases.py`。脚本采用 SQLite backup API、临时文件和原子替换，执行完整性与逐表行数校验，并拒绝覆盖已有目标库。

---

## 目录说明

| 目录 | 用途 |
|------|------|
| `backend/` | FastAPI 后端服务 |
| `scripts/` | 启动脚本、守护进程、数据初始化 |
| `data/` | 项目数据、策略输出与真实账户 JSON；SQLite 常驻库默认位于本机状态目录 |
| `docs/` | 设计文档、知识库 |
| `memory/` | Codex 项目上下文记忆 |

---

## 安全

- 所有敏感信息配置在 `.env.local`（已 `.gitignore`）
- 交易数据本地 SQLite 存储
- AI 调用通过 DeepSeek API（数据不用于训练）
- 飞书指令仅授权用户列表可执行交易操作

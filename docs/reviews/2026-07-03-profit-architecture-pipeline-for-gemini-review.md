# 恭喜发财盈利逻辑、架构与运行管线梳理

> 生成日期：2026-07-03  
> 用途：给 Gemini / 外部模型做架构与策略 review。  
> 范围：当前本地项目 `/Users/zhuchenyuan/AI/workflows/恭喜发财`。  
> 重要边界：本项目是 A 股研究、策略、风控、报告和预警助手，不自动下单；所有输出仅用于人工复核，不构成投资建议。

## 1. 项目定位

恭喜发财的目标不是“多生成报告”，而是把研究、行情、账户约束和复盘串成一个盈利导向闭环：

```text
发现机会
  -> 形成证据
  -> 进入标的生命周期池
  -> 补齐行情/资金/财务/研究快照
  -> 账户可执行评分
  -> 输出买/卖/持有/观察/剔除动作
  -> 飞书/本地报告提示人工复核
  -> 1/3/5/20 日复盘
  -> 修正评分阈值和角色权重
```

当前默认策略模式是 `growth_sprint`，即小账户高收益试验模式：

- 现金底线：10%。
- 单票上限：50%。
- 账户最大回撤：-10%。
- 单笔硬止损：5%。
- 允许低价高波动，但必须排除 ST、退市整理、流动性极差和无明确催化标的。

保守模式 `capital_preservation` 仍存在，但不是当前默认。

## 2. 核心原则

1. 账户优先于观点：买得起一手、能承受止损、不过现金底线，才有资格进入可执行池。
2. 风控优先于 AI 文案：AI 角色可以辩论，但机器可执行校验覆盖 AI 原始结论。
3. 研究不等于交易：Sentinel / Serenity 只能提供证据和方向，不能直接变成买入建议。
4. 不用泛化观望兜底：必须写清楚缺什么数据，或哪个信号未触发。
5. 主报告是操作界面，不是研究堆料：第一屏必须回答持仓怎么处理、是否买卖、价格、金额、止损、目标和等待信号。
6. 小账户不等于保守账户：当前阶段允许一手试错，但必须有硬止损和复盘。

## 3. 模块架构

```text
backend/app/
  ai/
    debate.py                 # 猎手/账房/守夜人/Serenity 多角色辩论
    sentinel_research.py      # Sentinel 新闻证据包和主题研究
    serenity_analyst.py       # 产业链瓶颈与研究候选
    cloud_client.py           # DeepSeek + Qwen 模型调用

  data_sources/
    tencent_client.py         # 实时行情、指数、K 线
    akshare_market.py         # 个股/行业/概念资金流、北向等
    tushare_client.py         # K 线、资金流、财务、新闻等
    eastmoney_client.py       # 东方财富相关数据源

  services/
    portfolio_store.py        # data/user_portfolio.json 与数据库同步
    strategy_profile.py       # growth_sprint / capital_preservation 策略参数
    quant_lifecycle.py        # Target Pool / Candidate Pool 生命周期
    target_snapshot.py        # 单标的结构化数据快照
    target_scoring.py         # 标的评分与动作输出
    small_account_discovery.py# 小账户池外低价候选种子
    schedule_policy.py        # 交易日报告调度规则
    report_archive.py         # Markdown 归档和 delivery_status
    feishu_pusher.py          # 飞书 webhook 推送

  trading_engine/
    risk_guard.py             # 交易风控 gate
    order_manager.py          # 订单管理
    account.py / position.py  # 模拟账户和持仓

scripts/
  daily_report.py             # 次日投资策略主报告入口
  run_sentinel.py             # Sentinel 研究包/绩效回看入口

data/
  user_portfolio.json         # 用户可编辑真实账户状态源
  candidate_pool.json         # 生产标的生命周期池
  sentinel/                   # Sentinel 证据、报告、绩效和角色评分
```

## 4. 数据与证据流

### 4.1 账户状态

`data/user_portfolio.json` 是当前可编辑账户事实源。`portfolio_store.py` 会把它同步到 SQLite 中的 `Position` / `SimAccount`，用于报告、分析和风控。

关键字段：

- `available_cash`
- `positions`
- `avg_cost`
- `current_price`
- `total_assets`
- `realized_pnl`

### 4.2 研究证据

Sentinel 负责：

- 高频新闻。
- 主题热度。
- 风险事件。
- evidence id。
- 角色绩效回看。

Serenity 负责：

- 产业链瓶颈。
- 真实受益环节。
- 龙头与替代标的。
- 财务和行业验证问题。

重要边界：Sentinel / Serenity 输出默认是 `research_reference` 或 `watching`，不能绕过账户可执行评分直接成为买入建议。

### 4.3 标的快照

`target_snapshot.py` 把单只标的归一成结构化快照：

- `quote`：实时价格、涨跌幅、成交额、量比、PE/PB、涨跌停。
- `kline`：历史 K 线。
- `fund_flow`：个股资金流。
- `northbound`：北向资金。
- `news`：新闻/公告。
- `financial`：财务证据。
- `sentinel`：Sentinel 证据。
- `serenity`：Serenity 证据。

每个字段带 `status=ok|missing|degraded` 风格信息，数据失败不应伪装成结论。

## 5. 标的池生命周期

当前生产标的池使用 `data/candidate_pool.json`，业务上应理解为 Target Pool。

状态分层：

| 状态 | 含义 | 是否能交易 |
|---|---|---|
| `executable` | 买得起、数据够、触发条件满足、风控通过 | 可以进入人工买入复核 |
| `watching` | 买得起但信号未触发，或关键数据待补 | 不能直接买，等触发 |
| `research_reference` | 有研究价值但买不起、不适合交易或只有产业链锚点价值 | 不能交易 |
| `removed` | 逻辑证伪、风险升高、连续不达标、流动性差等 | 剔除 |

入池前账户可执行性校验包括：

- A 股最小交易单位：主板/创业板默认 100 股，科创板 `688/689` 默认 200 股。
- 一手金额是否超过现金或单票预算。
- 当前价是否缺失。

## 6. 盈利逻辑清单

### 6.1 账户可执行性盈利逻辑

目标：避免“看对但买不起 / 买了就超仓 / 止损不起”的伪机会。

规则：

- 买不起最小交易单位的标的只能做研究参照。
- 单票金额不得超过策略 profile 的单票预算。
- 买入后必须保留现金底线。
- 每个买入建议必须同步止损和目标。

当前实现状态：已落地。

### 6.2 放量强势确认逻辑

目标：抓已经被市场确认的短线强势机会。

当前 `target_scoring.py` 偏向这种逻辑：

- 数据齐全。
- 账户买得起。
- 涨幅达到强势阈值。
- 量比 `>= 2`。
- 成交额 `>= 10000 万`。
- 资金流不能转弱。
- 不接近涨停追高。

满足后输出 `buy`，并生成：

- `entry_price`
- `stop_loss`
- `target_price`
- `position_amount`
- `decision_reason`

当前实现状态：部分落地，资金流转强的硬判断仍需继续加强。

### 6.3 不追高逻辑

目标：避免小账户在情绪顶点接盘。

规则：

- 涨幅接近 `9%` 时进入 `blocked_chasing` / watch。
- 接近涨停不追。
- 等回踩确认。
- 池外候选如果过热，不升为可执行。

当前实现状态：已落地基础版。

### 6.4 回踩/抓跌逻辑

目标：在强逻辑标的短线回落后，用更近止损获得更高赔率。

当前已有：

- “等回踩确认”。
- “回落到账户可买上限价以内”。
- “资金流转正且不高开追涨后再一手试错”。
- “回踩不破触发位、资金流未转弱”作为复核文字。

当前缺失：

- 分时不再创新低。
- 二次回踩不破前低。
- 资金流出收敛，而不仅是资金流转正。
- 支撑位/均线/前低自动计算。
- 板块相对强弱确认。
- `dip_entry` / `pullback_entry` 单独动作和评分。

当前实现状态：半成品。系统目前更偏“放量确认买”，不是成熟低吸模型。

### 6.5 小账户低价候选扫描

目标：当主池没有可执行标的时，主动寻找小账户买得起的一手机会，避免报告总是空转。

当前种子：

- `000629` 钒钛股份
- `000100` TCL科技
- `000725` 京东方A
- `600839` 四川长虹
- `002131` 利欧股份
- `002261` 拓维信息
- `300002` 神州泰岳
- `300339` 润和软件

触发条件：

- 现价低于账户可买上限。
- 一手金额通过。
- 有量能线索。
- 成交额足够。
- 资金流转正或至少不能转弱。
- 不高开追涨。

当前实现状态：基础版已落地，但候选源仍是静态种子，尚未动态融合 Sentinel 热点和 Serenity 替代标的。

### 6.6 研究参照转交易逻辑

目标：把高价龙头/产业链锚点变成可用研究，而不是误导小账户买入。

规则：

- 买不起的龙头进入 `research_reference`。
- Serenity 需要给出同产业链可交易替代标的。
- 只有替代标的通过账户、行情、资金、风控后才可进入 `watching` 或 `executable`。

当前实现状态：研究参照隔离已落地；替代标的自动生成不足。

### 6.7 卖出与止损逻辑

目标：盈利系统先控制亏损，再追求收益。

卖出触发：

- 跌破硬止损。
- 放量破位。
- 资金流转弱。
- 冲高回落。
- 公告/新闻利空。
- 仓位超限。
- 账户回撤接近阈值。
- 风险等级升高。

`risk_guard.py` 的 gate 包括：

- 交易时间。
- 涨跌停。
- T+1。
- 仓位上限。
- 日内亏损。
- 最大回撤。
- 交易频率。
- 可用资金。
- 板块集中度。
- 受托人检查。

当前实现状态：基础风控存在；持仓触发预警与报告联动仍需加强。

### 6.8 复盘自迭代逻辑

目标：让系统不是每天重新猜，而是从历史结果里修正。

应评估：

- 1 日表现。
- 3 日表现。
- 5 日表现。
- 20 日表现。
- 命中率。
- 最大回撤。
- 机会成本。
- 错失涨幅。
- 止损有效性。
- 哪个角色误判。
- 哪个角色应降权/升权。

当前实现状态：Sentinel 角色绩效和 advice performance 有雏形，但还没有完整反向驱动评分阈值和角色权重。

## 7. 主报告运行管线

主入口：`scripts/daily_report.py`。

运行顺序：

```text
1. 读取 data/user_portfolio.json
2. 重算持仓市值/盈亏
3. 同步 JSON 持仓到 SQLite
4. 拉腾讯指数与持仓实时行情
5. 加载 Sentinel 研究包
6. Sentinel evidence upsert 到 Target Pool
7. run_analysis 构建市场摘要
8. run_debate 调用多角色辩论和裁判
9. build_target_scores_for_report 对池内标的打分
10. build_outside_pool_scan_for_report 做小账户池外补扫
11. build_next_day_strategy_sections 生成主报告
12. finalize_daily_report 写入 Obsidian/司库归档
13. 更新 delivery_status.json
14. 通过飞书 webhook 推送摘要
```

归档目录：

```text
/Users/zhuchenyuan/AI/projects/司库/01-资料采集/量化投资/恭喜发财报告
```

成功状态以 `delivery_status.json` 为准，不只看 stdout。

## 8. 常驻调度管线

主入口：`backend/app/main.py` + APScheduler / launchd。

README 中定义的节奏：

```text
周日 20:30  次日投资策略主报告，服务周一
08:50       盘前短策略校准
11:35       午盘快速分析推送
14:00       午后风险检查
15:05       收盘复盘
20:30       次日投资策略主报告，服务下一交易日
21:00       Sentinel 绩效回看与归档
```

交易日判断由 `schedule_policy.py` 和 `trading_calendar.py` 控制。主报告在交易日盘后或周日晚运行，服务下一个交易日。

## 9. 当前已实现能力

- 真实账户 JSON 源和 DB 同步。
- 策略 profile：高收益试验 / 保守铁律。
- 标的池状态分层基础能力。
- 账户可执行性 gate。
- A 股一手金额过滤。
- 腾讯实时行情与指数。
- 结构化 Target Snapshot。
- Target Scoring 基础版。
- 小账户低价候选补扫。
- 不追高基础规则。
- 主报告第一屏操作优先结构。
- Markdown 本地归档。
- `delivery_status.json` 交付状态。
- Sentinel 研究包和角色绩效雏形。

## 10. 当前关键缺口

1. 低吸/抓跌模型不完整。
   - 缺 `dip_entry` / `pullback_entry` 动作。
   - 缺分时前低、二次回踩、资金流出收敛、板块相对强弱。

2. 标的评分仍偏简单。
   - 目前更偏强势量价确认。
   - 财务、公告、行业强度、北向和 Serenity 权重还没有完全融合。

3. 池外候选仍是静态种子。
   - 应从 Sentinel 热点、Serenity 替代标的、全市场低价高流动性池动态生成。

4. 飞书事件型预警不足。
   - 现在报告型推送较强，观察池机会/风险触发型提醒还不够。

5. 自迭代未闭环。
   - 已有绩效文件，但未自动调角色权重、评分阈值、入池/出池阈值。

6. 研究参照转交易替代链不足。
   - 高价龙头能被隔离，但同产业链可交易替代标的自动发现不足。

7. 模型健康与数据源健康还可更精确。
   - 报告里应区分 `ok / missing / degraded / unused`，避免 `unknown`。

## 11. 建议让 Gemini 重点 Review 的问题

1. 当前盈利逻辑是否过度偏向“强势确认”，导致低吸/回踩机会利用不足？
2. `target_scoring.py` 的买入阈值是否过于机械？涨幅、量比、成交额、资金流应该如何加权？
3. 小账户 `growth_sprint` 下，单票 50% 是否过激？是否应按标的波动率动态调整？
4. 如何设计 `dip_entry` 低吸模型，使它不变成“接飞刀”？
5. Sentinel / Serenity 研究证据应该如何影响交易评分，而不直接污染可执行池？
6. 当前 Target Pool 状态是否足够？是否需要增加 `blocked_chasing`、`dip_watch`、`risk_alert`、`cooldown` 等状态？
7. 如何把复盘结果真正反向更新角色权重和评分阈值？
8. 哪些数据字段必须落盘，才能让后续回测和复盘可审计？
9. 当前报告第一屏是否已经足够“操作界面化”，还是仍然有研究噪声？
10. 飞书事件型预警应该优先做哪些触发器：止损、资金流转强、回踩不破、公告利空、板块异动？

## 12. Review 时请注意的边界

- 不建议引入自动真实下单；当前阶段保持人工确认。
- 不要把研究报告直接转为买入建议。
- 不要推荐买不起一手的标的。
- 不要为了提高交易频率而弱化硬止损。
- 不要让 LLM 生成伪 evidence id；证据编号必须来自系统 ledger。
- 不要把“数据不足”作为兜底结论；必须说明具体缺口。

## 13. 可参考的关键文件

- `AGENTS.md`：项目身份与操作原则。
- `SYSTEM.md`：模块边界和风控规则。
- `README.md`：架构与当前 v7.5 方向。
- `scripts/daily_report.py`：主报告运行管线。
- `backend/app/services/strategy_profile.py`：策略 profile。
- `backend/app/services/quant_lifecycle.py`：标的池生命周期。
- `backend/app/services/target_snapshot.py`：结构化数据快照。
- `backend/app/services/target_scoring.py`：评分与动作逻辑。
- `backend/app/services/small_account_discovery.py`：小账户池外补扫。
- `backend/app/trading_engine/risk_guard.py`：交易风控 gate。
- `backend/app/services/portfolio_store.py`：账户 JSON 与 DB 同步。
- `docs/worklists/2026-07-01-v7-5-profit-pipeline-refactor-worklist.md`：盈利管线重构目标。

# 恭喜发财 — Codex 项目入口

> 打开本目录时，先按本文和 `SYSTEM.md` 理解项目身份。这里不是 knowX，也不是通用学习助手。

## 项目身份

恭喜发财是个人 A 股研究、策略、风控、报告和预警工作流。项目目标不是“多生成报告”，而是：

1. 选出标的。
2. 给出可执行策略。
3. 用户按策略操作。
4. 通过复盘和数据闭环提高盈利能力。

## 当前主线

- 小账户采用高收益试验模式，但必须有硬止损和账户可执行性校验。
- Sentinel 负责高频新闻、主题热度、风险事件和归一证据包。
- Serenity 负责产业链瓶颈深挖，是研究证据源，不直接发出买卖指令。
- Target Pool 是生产标的生命周期池，必须区分：
  - `executable`：今日可执行。
  - `watching`：观察等待触发。
  - `research_reference`：研究参照，不能直接交易。
  - `removed`：剔除。
- 主报告第一屏必须回答：
  - 当前持仓怎么处理。
  - 是否需要卖，卖多少，看什么信号。
  - 是否需要买，买哪只，多少钱，什么价格，错了哪里止损。
  - 今天不动，明天等什么信号。

## 关键入口

| 入口 | 用途 |
|---|---|
| `backend/app/main.py` | FastAPI + APScheduler 常驻入口 |
| `scripts/daily_report.py` | 次日投资策略主报告 CLI 入口 |
| `backend/app/services/quant_lifecycle.py` | 标的池/持仓预警生命周期 |
| `backend/app/services/target_snapshot.py` | 单标的结构化数据快照 |
| `backend/app/services/target_scoring.py` | 账户可执行评分 |
| `backend/app/report_engine/` | 盘前/盘中/收盘报告引擎 |
| `scripts/install-congxicai-v7-launchd.sh` | v7 launchd 常驻安装 |

## 工作要求

- 不把研究线索直接当买入建议。
- 不用“数据不足，建议观望”做泛化兜底；必须写清缺哪类数据或哪个信号未触发。
- 不推荐买不起最小交易单位的标的；买不起的一律归为研究参照。
- 不暴露 API Key、Webhook、Token、Cookie。
- 修改交易、预警、报告、账户逻辑后必须跑测试。
- 真实报告验证尽量使用 `CONGXI_PORTFOLIO_PATH`、`CONGXI_CANDIDATE_POOL_PATH`、`CONGXI_REPORT_ARCHIVE_DIR` 临时副本，避免污染真实持仓。

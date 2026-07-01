# v7.5 次日策略与小账户补扫交接报告

生成时间：2026-07-01T18:58:08+0800  
项目：恭喜发财  
仓库路径：`/Users/zhuchenyuan/AI/workflows/恭喜发财`  
当前分支：`codex/quant-lifecycle-v7-4`  
PR：`https://github.com/jarrensea-lab/congxifacai/pull/9`  
PR 状态：OPEN，ready，base=`main`，head=`codex/quant-lifecycle-v7-4`

## 当前状态

- Git 工作树：干净。
- 最新提交：`d69480b feat: add small account next-day scan strategy`
- 上一个核心提交：`44ee2ac feat: refactor profit strategy pipeline v7.5`
- 正式报告已生成：
  `/Users/zhuchenyuan/AI/projects/司库/01-资料采集/量化投资/恭喜发财报告/2026/07/2026-07-01/2026-07-01_次日投资策略主报告.md`
- 报告行数：133 行。
- 本次正式报告没有发现旧问题关键词：`unknown`、`数据不足，建议观望`。
- 飞书推送：本次生成报告时显式禁用 `FEISHU_WEBHOOK_URL`，没有推送飞书。

## 用户真实目标

用户要求项目服务于一个目标：选出标的，给出策略，按策略操作并追求盈利。  
报告必须直接回答：

- 现在该不该动？
- 如果动，动哪只？
- 多少钱？
- 什么价格买？
- 错了哪里止损？
- 今天不动，明天等什么信号？
- 持仓有风险或机会时，要能通过飞书预警。

用户明确强调：小账户不要过度保守，要分级投资；当前阶段不需要分钟级/秒级交易，但架构、数据、策略、监控、预警必须先跑通。

## 已完成工作

### v7.5 盈利策略管线

- 主报告改为动作优先结构，第一屏从账户和次日策略开始。
- Target Pool 明确分层：`executable`、`watching`、`research_reference`、`removed`。
- Sentinel/Serenity 研究候选进入策略管线，但必须经过账户可执行性、行情快照和评分，不直接变成买入建议。
- `target_scoring` 不再用“数据不足，建议观望”兜底，必须说明缺什么或等什么信号。
- 买不起一手的标的只进研究参照，不进入可执行策略。

### 小账户池外补扫

新增 `backend/app/services/small_account_discovery.py`：

- 默认低价候选包括：`000629`、`000100`、`000725`、`600839`、`002131`、`002261`、`300002`、`300339`。
- 按账户现金和总资产计算单票预算，当前 `¥6085.61` 账户对应主板/创业板一手最高观察价约 `¥21.29`。
- 排序规则：优先量能线索、买得起一手、无追高风险，再看成交额。

`scripts/daily_report.py` 已接入池外补扫：

- 当标的池没有可执行买入时，报告会输出池外小账户补扫表。
- 表格包含：现价、一手金额、最高观察价、触发/观察价、止损、目标、来源、明日等待信号。
- 顶部“今日账户操作策略”会引用池外首选候选，生成次日条件策略。
- 已修边界：如果池外候选全部买不起，不会误写成“一手试错”，只会写观察等待回落。

### 正式报告结论

2026-07-01 正式报告第一屏核心结论：

- 是否需要买：今天不主动买入；明天只做条件触发，不预挂单。
- 如果动，动哪只：优先复核 钒钛股份 `000629`。
- 多少钱：一手试错约 `¥355.00`，不得超过单票预算 `¥2,129.96`。
- 什么价格买：`¥3.55` 以内观察，资金流未转正或高开追涨不买。
- 错了哪里止损：跌破 `¥3.37` 止损；第一目标看 `¥3.98`。
- 今天不动，明天等什么信号：已具备量能线索，明日若资金流转正且不高开追涨，可一手试错复核。

## 验证记录

最后一次验证命令：

```bash
PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests -q
.venv/bin/python -m ruff check backend scripts
git diff --check
```

结果：

- `210 passed in 0.95s`
- `ruff`: `All checks passed!`
- `git diff --check`: 通过

正式报告关键行验证：

```bash
rg -n "如果动，动哪只：优先复核 钒钛股份\\(000629\\)|一手试错约¥355.00|跌破¥3.37止损|第一目标看¥3.98" \
  "/Users/zhuchenyuan/AI/projects/司库/01-资料采集/量化投资/恭喜发财报告/2026/07/2026-07-01/2026-07-01_次日投资策略主报告.md"
```

已命中策略行。

## 主要文件

- `scripts/daily_report.py`
  - v7.5 主报告结构
  - 池外小账户补扫接入
  - 次日条件策略输出
- `backend/app/services/small_account_discovery.py`
  - 小账户池外候选种子和预算计算
- `backend/app/services/target_scoring.py`
  - 标的评分、缺数据时的具体 next_signal、止损和目标
- `backend/tests/test_daily_report_delivery.py`
  - 主报告结构、池外补扫、买不起边界回归
- `backend/tests/test_small_account_discovery.py`
  - 小账户种子预算测试
- `backend/tests/test_target_scoring.py`
  - 缺数据不再泛化观望测试
- `README.md`、`CHANGELOG.md`
  - v7.5 能力说明和验证记录

## 重要边界与注意事项

- 不要在答复中暴露用户提供过的 API Key、Webhook、token。
- 本次正式报告生成时没有推送飞书；后续如要验证飞书，需要明确使用安全环境变量，不要把 webhook 打印出来。
- 当前策略是条件触发，不是收益承诺，也不是自动下单。
- `Sentinel/Serenity` 的定位已明确：研究证据源和候选输入，不直接决定买入。买入必须经过数据快照、账户可执行性、风控和报告策略。
- 当前小账户池外补扫还是静态种子 + 实时行情排序；下一步应把 Sentinel 高频主题和 Serenity 深挖候选动态注入补扫源。

## 新对话建议启动方式

在新对话中先执行：

```bash
cd /Users/zhuchenyuan/AI/workflows/恭喜发财
git status --short
git branch --show-current
gh pr view 9 --json url,state,isDraft,headRefName,baseRefName,title
```

如果继续开发，建议优先级：

1. 检查 PR #9 CI/评审状态，准备合并到 `main`。
2. 验证生产 API `/api/health` 是否已经运行在 v7.5 版本。
3. 运行一次带真实环境变量的飞书预警 dry-run，确认 webhook 不泄露且能发送策略/持仓预警。
4. 把 Sentinel 高频主题和 Serenity 深挖候选接到 `small_account_discovery` 的动态候选源。
5. 建立次日复盘：000629 是否触发资金流转正、是否高开追涨、是否达到/跌破条件，并把结果写回评分迭代。


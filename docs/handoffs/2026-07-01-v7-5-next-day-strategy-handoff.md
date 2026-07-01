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
- 新开仓结论是什么？
- 候选标的是哪只？
- 建议试仓金额是多少？
- 触发价是什么？
- 止损/目标在哪里？
- 盘前需要复核什么信号？
- 持仓有风险或机会时，要能通过飞书预警。

用户明确强调：小账户不要过度保守，要分级投资；当前阶段不需要分钟级/秒级交易，但架构、数据、策略、监控、预警必须先跑通。

## 已完成工作

### v7.5 盈利策略管线

- 主报告改为盈利导向结构，第一屏是“明日唯一实盘狙击标的”，优先呈现干不干、干谁、买入逻辑、触发价、一手金额、止损和第一目标。
- Target Pool 明确分层：`executable`、`watching`、`research_reference`、`removed`。
- Sentinel/Serenity 研究候选进入策略管线，但必须经过账户可执行性、行情快照和评分，不直接变成买入建议。
- `target_scoring` 不再用“数据不足，建议观望”兜底，必须说明缺什么或等什么信号。
- 买不起一手的标的不进入主报告正文；只在后台显示预算阻断数量，明细保留在本地结构化审计日志。

### 小账户低价候选扫描

新增 `backend/app/services/small_account_discovery.py`：

- 默认低价候选包括：`000629`、`000100`、`000725`、`600839`、`002131`、`002261`、`300002`、`300339`。
- 按账户现金和总资产计算单票预算；用户已确认高收益试验模式单票上限调整为 `50%`，当前 `¥6085.61` 账户对应主板/创业板账户可买上限价约 `¥30.42`。
- 排序规则：优先量能线索、买得起一手、无追高风险，再看成交额。

`scripts/daily_report.py` 已接入池外补扫：

- 当标的池没有可执行买入时，报告会把买得起且有量能线索的池外候选升格为“核心主攻”。
- 第一屏包含：候选标的、买入逻辑、一手金额、单票预算、触发价、止损位和第一目标。
- 其他买得起候选进入“明日盘中雷达触发池”；买不起候选不显示名称，只计入“预算阻断 N 只”。
- 已修边界：如果池外候选全部买不起，不会误写成“一手试错”，只会写观察等待回落。

### 正式报告结论

当前报告第一屏核心结论模板：

- 裁判最终结论：空仓观望，明日仅做条件触发，不预挂单。
- 盘前唯一重点复核标的：钒钛股份 `000629`。
- 买入逻辑：已具备量能线索，博弈低价股资金回流。
- 执行条件：明日个股资金流转正、量能延续，且不高开追涨；触发价参考 `¥3.55`。
- 资金配置：一手约 `¥355.00`，不得超过单票预算 `¥3,042.80`。
- 风控密码：止损位 `¥3.37`；第一目标位 `¥3.98`。

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
rg -n "候选标的：优先复核 钒钛股份\\(000629\\)|一手试错约¥355.00|跌破¥3.37止损|第一目标看¥3.98" \
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

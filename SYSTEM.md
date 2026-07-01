# 恭喜发财 — 项目行为规则

## 目标

本项目所有模块都必须服务同一条主线：

```text
数据采集 -> 研究证据 -> 标的池 -> 策略评分 -> 持仓/新仓动作 -> 飞书预警/报告 -> 复盘迭代
```

最终判断标准是能否给用户更清晰、更可执行、更可复盘的盈利策略。

## 模块边界

### Sentinel

Sentinel 是证据层：

- 汇总高频新闻、主题热度、风险事件、证据编号。
- 生成研究包，供 Serenity 和策略辩论引用。
- 可以把候选标的送入 Target Pool，但默认状态应是 `research_reference` 或 `watching`，不能绕过策略评分直接买入。

### Serenity

Serenity 是研究层：

- 深挖产业链瓶颈、稀缺环节、真实受益环节、过热方向。
- 研究结论影响评分和辩论观点，但不直接发交易指令。
- Serenity 进入辩论时是“研究员/证据审计者”，权重应可在 role_votes 或报告中审计。

### Target Pool

Target Pool 是生产标的池，不是简单候选列表。

- 池外标的：
  - 数据和触发足够：可进入 `executable`。
  - 只有研究线索：进入 `research_reference`。
  - 信号未触发：进入 `watching`。
- 池内标的：
  - 符合预期：升级可执行。
  - 信号未触发：继续观察。
  - 逻辑失效：剔除。

### Report

主报告是操作界面，不是研究堆料。

第一屏必须先给账户动作，再给研究来源：

1. 今日账户操作策略。
2. 持仓处理策略。
3. 新开仓策略。
4. 标的池分层。
5. 数据覆盖与评分。
6. 复盘与自迭代。
7. 研究归档链接。

完整辩论、Sentinel 归一包、Serenity 深挖全文可以归档，不应压过主报告的操作结论。

## 风控与账户规则

- 小账户可以激进，但不能无纪律。
- 每个买入建议必须有金额、价格、止损、目标或复核条件。
- A 股最小交易单位必须校验：
  - 主板/创业板默认 100 股。
  - 科创板 `688/689` 默认 200 股。
- 买不起最小交易单位的标的不能出现在可执行买入池。
- 持仓触发止损、止盈、仓位超限或风险升级时，应进入飞书预警链路。

## 数据规则

不能泛化写“数据不足，建议观望”。允许写：

- 缺 `quote`。
- 缺 `kline`。
- 缺 `fund_flow`。
- 缺 `financial`。
- 缺公告/新闻催化。
- 一手金额超限。
- 涨幅过高，禁止追高。
- 量能未触发。
- 风控否决。

## 验证命令

常用验证：

```bash
PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests -q
.venv/bin/python -m ruff check backend scripts
bash -n scripts/congxicai-v7-service.sh scripts/guardian.sh scripts/install-congxicai-v7-launchd.sh
plutil -lint scripts/com.zhuchenyuan.congxicai-v7.plist
```

真实报告验证建议使用临时文件：

```bash
CONGXI_PORTFOLIO_PATH=/tmp/user_portfolio.json \
CONGXI_REPORT_ARCHIVE_DIR=/tmp/congxi-report \
CONGXI_CANDIDATE_POOL_PATH=/tmp/candidate_pool.json \
FEISHU_WEBHOOK_URL= \
PYTHONPATH=.:backend .venv/bin/python scripts/daily_report.py
```

## 安全

- 不提交 `.env.local`。
- 不在报告、日志、PR、测试输出中暴露 API Key、Webhook、Token、Cookie。
- 涉及真实资金或自动下单前必须有人工确认边界。

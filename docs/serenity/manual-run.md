# Serenity 手动运行

Serenity 独立研究流水线只生成产业链瓶颈研究报告，默认不推送飞书，不进入主日报交易决策，也不生成买入、卖出、仓位或目标价。

## 不带行情

用于离线研究、候选池检查和报告格式验证：

```bash
PYTHONPATH=backend .venv/bin/python scripts/serenity_research_report.py 电网设备 \
  --date 2026-06-26 \
  --archive-dir /tmp/serenity-report \
  --cash 3085.61 \
  --total-assets 3085.61
```

输出会写入归档目录，并在 `delivery_status.json` 中记录 `serenity research archive only`。默认不推送飞书。

## 带行情

只有明确需要实时行情证据时才加 `--with-quotes`：

```bash
PYTHONPATH=backend .venv/bin/python scripts/serenity_research_report.py 电网设备 \
  --date 2026-06-26 \
  --archive-dir /tmp/serenity-report \
  --cash 3085.61 \
  --total-assets 3085.61 \
  --with-quotes
```

带行情模式会尝试拉取腾讯行情，把现价、一手金额、成交额、PE、PB 和涨跌幅写成证据，并可能调整 Serenity 8 维评分。行情失败时，Serenity 报告应安全降级为无行情证据，不影响主日报。

## 带财报证据

只有明确需要财报证据时才加 `--with-financials`：

```bash
PYTHONPATH=backend .venv/bin/python scripts/serenity_research_report.py 电网设备 \
  --date 2026-06-26 \
  --archive-dir /tmp/serenity-report \
  --cash 3085.61 \
  --total-assets 3085.61 \
  --with-financials
```

财报证据会优先尝试 Tushare。`TUSHARE_TOKEN` 可以来自环境变量，也可以来自 Tushare 包自身保存的 token。若当前账号没有财报接口权限，会尝试 AKShare；若 AKShare 未安装或接口失败，则回落到 `data/serenity/financial_evidence.json` 证据缓存。没有真实证据时，报告会明确标记财务证据不可用，不会伪造针对性结论。

## 候选池校验

编辑 `data/serenity/theme_candidates.json` 后先运行：

```bash
PYTHONPATH=backend .venv/bin/python scripts/validate_serenity_candidates.py
```

再运行：

```bash
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_serenity_analyst.py -q
```

## 安全添加候选

可以用脚本安全追加主题别名或候选。脚本会先校验现有候选池，写入前去重，写入后再次校验：

```bash
PYTHONPATH=backend .venv/bin/python scripts/add_serenity_candidate.py \
  --theme AI基建/电力 \
  --alias 电网设备 \
  --candidate-json '{"name":"测试电气","code":"300001","chokepoint":"测试瓶颈","chain_position":"测试位置","scores":{"需求确定性":7,"瓶颈强度":8,"传导清晰度":7,"业务纯度":6,"证据强度":5,"市场忽视度":5,"验证速度":6,"下行安全":5},"evidence_items":[{"fact":"测试证据待核验","strength":"medium","source":"手工维护"}],"verify_next":"核验公告、财报和订单。"}'
```

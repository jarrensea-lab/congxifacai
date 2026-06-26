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

## 候选池校验

编辑 `data/serenity/theme_candidates.json` 后先运行：

```bash
PYTHONPATH=backend .venv/bin/python scripts/validate_serenity_candidates.py
```

再运行：

```bash
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_serenity_analyst.py -q
```

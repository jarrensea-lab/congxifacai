# Sentinel 输入输出契约

> 日期：2026-06-27
> 状态：M1 初版
> 消费方：Sentinel 研究证据层
> 生产方：恭喜发财数据源引擎

## 1. 契约原则

Sentinel 只读取恭喜发财数据源引擎生成的统一输入包，不直接读取 Horizon raw 新闻，也不直接抓网页。

统一输入包必须是一个目录，包含以下文件：

```text
news_events.jsonl
market_snapshot.json
portfolio_snapshot.json
candidate_pool.json
financial_evidence.json
risk_context.json
```

Sentinel 输出目录包含：

```text
sentinel_theme_radar.json
sentinel_candidate_review.json
sentinel_intraday_alerts.json
sentinel_debate_packet.md
sentinel_daily_research.md
```

## 2. 安全字段限制

任何输入和输出文件都不能包含以下字段：

```text
authorization
authorization_header
cookie
cookies
token
api_key
apikey
secret
password
```

登录态、token 和 cookie 只允许留在采集器或数据源引擎自己的安全配置里，不允许写入司库、报告或 Sentinel bundle。

## 3. news_events.jsonl

每行一条新闻事件。

必填字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 稳定事件 ID |
| `source` | string | 来源，例如 财联社、雪球、新浪财经 |
| `channel` | string | 频道，例如 公司、市场、宏观 |
| `published_at` | string | 新闻发布时间，ISO 8601 |
| `fetched_at` | string | Horizon 抓取时间，ISO 8601 |
| `content` | string | 新闻正文 |
| `is_key` | boolean | 是否为来源标记的重点新闻 |
| `symbols` | array[string] | 相关股票代码，可为空 |
| `themes` | array[string] | 相关主题，可为空 |
| `dedupe_key` | string | 去重键 |
| `raw_hash` | string | 原始正文 hash |

可选字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `url` | string | 来源页面或原文链接 |
| `title` | string | 标题；滚动新闻可为空 |
| `ingested_at` | string | 恭喜发财数据源引擎入库时间 |
| `evidence_status` | string | `raw`、`deduped`、`enriched`、`rejected` |

## 4. market_snapshot.json

必填字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `trade_date` | string | 交易日 |
| `indices` | array[object] | 指数快照 |
| `sectors` | array[object] | 板块快照 |
| `symbols` | array[object] | 个股快照 |

个股快照建议字段：

```json
{
  "symbol": "000400.SZ",
  "name": "许继电气",
  "price": 0,
  "change_pct": 0,
  "amount_wan": 0,
  "turnover_rate": 0,
  "source": "tencent/eastmoney/tushare"
}
```

## 5. portfolio_snapshot.json

必填字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `cash` | number | 可用现金 |
| `total_assets` | number | 总资产 |
| `positions` | array[object] | 当前持仓 |

建议字段：

```json
{
  "cash": 3085.61,
  "total_assets": 3085.61,
  "positions": [],
  "account_constraints": {
    "max_single_position_pct": 0.35,
    "min_lot_size": 100
  }
}
```

## 6. candidate_pool.json

必填字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `themes` | array[object] | 候选主题和候选标的 |

建议结构：

```json
{
  "themes": [
    {
      "theme": "AI电力",
      "candidates": [
        {
          "symbol": "000400.SZ",
          "name": "许继电气",
          "reason": "电网设备候选",
          "source": "serenity_candidate_pool"
        }
      ]
    }
  ]
}
```

## 7. financial_evidence.json

必填字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | `success`、`partial`、`unavailable` |
| `items` | array[object] | 标的财务证据 |

当 Tushare 或 AKShare 权限不可用时，必须写：

```json
{
  "status": "unavailable",
  "items": [],
  "reason": "financial endpoints unavailable"
}
```

不能伪造财务结论。

## 8. risk_context.json

必填字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `trade_allowed` | boolean | 当前是否允许进入交易建议流程 |
| `constraints` | array[object] | 风控约束 |

建议结构：

```json
{
  "trade_allowed": false,
  "constraints": [
    {
      "id": "research_only",
      "level": "hard",
      "message": "Sentinel 只输出研究证据，不输出交易指令。"
    }
  ]
}
```

## 9. Sentinel 输出动作限制

`sentinel_intraday_alerts.json` 中 `action_type` 只允许：

```text
watch
verify
downgrade
risk_up
ignore
```

禁止：

```text
buy
sell
clear
all_in
```

Sentinel 可以写“建议关注”“需要核验”“风险升高”，不能写“买入”“卖出”“清仓”“满仓”。

## 10. 当前校验入口

当前已有最小契约校验模块：

```text
backend/app/ai/sentinel_contracts.py
```

当前已有 Horizon 新闻导入器：

```text
backend/app/data_sources/horizon_news_importer.py
```

测试入口：

```bash
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_sentinel_contracts.py backend/tests/test_horizon_news_importer.py -q
```

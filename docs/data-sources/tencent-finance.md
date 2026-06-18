# 腾讯财经 API

> 自动提取自 `backend/app/data_sources/tencent_client.py`
>
> **角色**: 主力数据源（首选），提供实时行情和批量查询。

## 端点

```
GET https://qt.gtimg.cn/q={code}
```

- 单只股票: `q=sh600000`
- 批量查询: `q=sh600000,sz000001,sz399001` (逗号分隔)
- 编码: **GBK**

## 股票代码转换

`_resolve_code(code)` 自动添加交易所前缀：

| 代码首字符 | 交易所 | 转换结果 |
|-----------|--------|---------|
| `6` 或 `9` | 上海 | `sh{code}` |
| `8` | 北京 | `bj{code}` |
| 其他 | 深圳 | `sz{code}` |
| 已有前缀 (`sh`/`sz`/`bj`) | — | 原样返回 |

支持指数代码: `sh000001` (上证指数)、`sz399001` (深证成指)、`sz399006` (创业板指)

## 数据格式

响应使用 `~` (波浪号) 分隔，GBK 编码：

```
v_sh600000="1~平安银行~...~15.23~...~";
```

## 完整字段映射 (88 字段中的关键字段)

| 索引 | 字段名 | 类型 | 说明 |
|------|--------|------|------|
| 1 | `name` | str | 股票名称 |
| 3 | `price` | float | 当前价格 |
| 4 | `last_close` | float | 昨收价 |
| 5 | `open` | float | 今开价 |
| 31 | `change_amt` | float | 涨跌额 |
| 32 | `change_pct` | float | 涨跌幅 (%) |
| 33 | `high` | float | 最高价 |
| 34 | `low` | float | 最低价 |
| 37 | `amount_wan` | float | 成交额 (万元) |
| 38 | `turnover_pct` | float | 换手率 (%) |
| 39 | `pe_ttm` | float | 滚动市盈率 |
| 43 | `amplitude_pct` | float | 振幅 (%) |
| 44 | `mcap_yi` | float | 总市值 (亿元) |
| 45 | `float_mcap_yi` | float | 流通市值 (亿元) |
| 46 | `pb` | float | 市净率 |
| 47 | `limit_up` | float | 涨停价 |
| 48 | `limit_down` | float | 跌停价 |
| 49 | `vol_ratio` | float | 量比 |
| 52 | `pe_static` | float | 静态市盈率 |

## 方法

### `fetch(stock_code) -> Dict | None`
获取单只股票实时行情。返回包含所有字段的字典，异常时返回 None。

### `fetch_batch(codes: List[str]) -> Dict[str, Dict]`
批量获取多只股票实时行情。单次 HTTP 请求完成。返回 `{原始代码: 数据字典}`。

## 使用示例

```python
from app.data_sources.tencent_client import TencentDataSource

client = TencentDataSource()

# 单只股票
data = await client.fetch("000001")
# => {"name": "平安银行", "price": 12.50, "pe_ttm": 6.5, ...}

# 批量查询
batch = await client.fetch_batch(["000001", "600036", "000858"])
# => {"000001": {...}, "600036": {...}, "000858": {...}}
```

---

> 来源: `backend/app/data_sources/tencent_client.py`

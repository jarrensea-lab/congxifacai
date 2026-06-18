# 新浪财经 API

> 自动提取自 `backend/app/data_sources/sina_client.py`
>
> **角色**: 第一备选数据源，提供实时报价。

## 端点

```
GET https://hq.sinajs.cn/list={code}
```

- 编码: **GBK**
- 需要自定义请求头: `Referer: https://finance.sina.com.cn`

## 股票代码转换

与腾讯财经相同规则：`6`/`9` 开头 → `sh{code}`，其他 → `sz{code}`。

> 注意：不保留 `bj` (北京) 前缀，`8` 开头会被映射到深圳。

## 数据格式

响应使用逗号分隔，GBK 编码：

```
var hq_str_sh600000="平安银行,12.50,12.48,12.52,12.60,12.40,...,100000,125000,...";
```

## 字段映射

| 索引 | 字段名 | 类型 | 说明 |
|------|--------|------|------|
| 0 | `name` | str | 股票名称 |
| 1 | `open` | float | 开盘价 |
| 2 | `last_close` | float | 昨收价 |
| 3 | `price` | float | 当前价格 |
| 4 | `high` | float | 最高价 |
| 5 | `low` | float | 最低价 |
| 8 | `volume` | int | 成交量 |
| 9 | `amount_wan` | float | 成交额 (万元, 原始值 / 10000) |

> `change_pct` 为内部计算: `(price - last_close) / last_close * 100`

## 方法

### `fetch(stock_code) -> Dict | None`
获取单只股票实时行情。数据长度不足 32 个字段时返回 None。

## HTTP 配置

- 使用 `urllib.request` (同步阻塞，封装为 async)
- 超时: 10 秒
- User-Agent: 自定义浏览器 UA

## 与腾讯财经的差异

| 维度 | 腾讯财经 | 新浪财经 |
|------|---------|---------|
| 字段数量 | 88 字段 | ~32 字段 |
| PE/PB | ✅ 支持 | ❌ 不支持 |
| 量比 | ✅ 支持 | ❌ 不支持 |
| 换手率 | ✅ 支持 | ❌ 不支持 |
| 振幅 | ✅ 支持 | ❌ 不支持 |
| 批量查询 | ✅ 支持 | ❌ 不支持 |
| 涨跌停价 | ✅ 支持 | ❌ 不支持 |
| 位置 | 首选 | 备选1 |

> 新浪财经适合作为轻量级备选，仅获取基础价格信息。

---

> 来源: `backend/app/data_sources/sina_client.py`

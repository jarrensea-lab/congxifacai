# 东方财富 API

> 自动提取自 `backend/app/data_sources/eastmoney_client.py`
>
> **角色**: 板块排名、涨跌分布、K线数据。基于 `push2.eastmoney.com`。

## 端点

### 实时行情

```
GET http://push2.eastmoney.com/api/qtstock/kline/get
```

**参数**:
| 参数 | 值 | 说明 |
|------|-----|------|
| `secid` | `{market}.{code}` | market: `1`=上海, `0`=深圳 |
| `kline` | `day` | K线周期 |
| `fields1` | `f1,f2,f3,f4,f5` | 基础字段 |
| `fields2` | `f51...f65` | 详细字段 (15个) |

**字段映射**:

| 响应键 | 字段名 | 说明 |
|--------|--------|------|
| `f2` | `name` | 股票名称 |
| `f31` | `price` | 当前价格 |
| `f32` | `change` | 涨跌额 |
| `f33` | `change_pct` | 涨跌幅 |
| `f34` | `open` | 开盘价 |
| `f35` | `high` | 最高价 |
| `f36` | `low` | 最低价 |
| `f37` | `volume` | 成交量 |
| `f38` | `amount` | 成交额 |
| `f40` | `pe_ratio` | 市盈率 |
| `f41` | `pb_ratio` | 市净率 |
| `f42` | `market_cap` | 市值 |

### 行业板块排名

```
GET http://push2.eastmoney.com/api/qt/clist/get
```

**参数**:
| 参数 | 值 | 说明 |
|------|-----|------|
| `pn` | `1` | 页码 |
| `pz` | `20` (可调) | 每页条数 |
| `po` | `1` | 降序 |
| `fid` | `f3` | 按涨跌幅排序 |
| `fs` | `m:90+t:2` | 板块筛选器 |
| `fields` | `f2,f3,f4,f12,f14,f15,f16,f17,f18,f20,f21,f104,f105,f128` | 返回字段 |

**返回字段**:

| 响应键 | 字段名 | 说明 |
|--------|--------|------|
| `f12` | `code` | 板块代码 |
| `f14` | `name` | 板块名称 |
| `f3` | `change_pct` | 涨跌幅 |
| `f2` | `price` | 当前点位 |
| `f104` | `up_count` | 上涨家数 |
| `f105` | `down_count` | 下跌家数 |
| `f128` | `leader_name` | 领涨股名称 |
| `f21` | `leader_change` | 领涨股涨跌幅 |

### 涨跌家数 (市场宽度)

```
GET http://push2.eastmoney.com/api/qt/stock/get
```

**参数**: `secid=1.000001`, `fields=f170,f171,f169,...`

**返回字段**:

| 响应键 | 字段名 | 说明 |
|--------|--------|------|
| `f170` | `up_count` | 上涨家数 |
| `f171` | `down_count` | 下跌家数 |
| `f169` | `flat_count` | 平盘家数 |

涨跌比计算: `ratio = up / (up + down) * 100`

## 方法

### `fetch(stock_code) -> Dict | None`
获取单只股票实时行情 (通过 K线接口取最新一条)。

### `fetch_sectors(top_n=20) -> Dict`
获取行业板块排名。返回 `{"sectors": [...], "total": N}`。

### `fetch_market_breadth() -> Dict`
获取全市场涨跌家数分布。返回 `{"up_count": N, "down_count": N, "flat_count": N, "ratio": %}`。

## 超时设置

| 方法 | 超时 |
|------|------|
| `fetch` | 5 秒 |
| `fetch_sectors` / `fetch_market_breadth` | 10 秒 |

---

> 来源: `backend/app/data_sources/eastmoney_client.py`

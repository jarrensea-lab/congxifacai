# 新闻数据源切换：AKShare 统一方案

## 背景

CLS 财联社 API 全部失效（返回 `Method Not Allowed`），已临时接入 Sina 新浪财经作为应急方案。但 Sina 仅提供市场滚动资讯，个股新闻搜索不生效，无题材热点，无公告数据，信息覆盖度不足。

## 方案

用 AKShare（GitHub 14k+ stars 的 Python 财经数据库）替代 CLS + Sina，作为统一新闻后端。AKShare 内部封装东方财富、新浪、同花顺、财联社、巨潮资讯等多源数据。

## 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `backend/app/data_sources/akshare_news.py` | AKShare 新闻客户端 |
| 删除 | `backend/app/data_sources/cls_client.py` | CLS 已失效 |
| 删除 | `backend/app/data_sources/sina_news.py` | 被 AKShare 替代 |
| 修改 | `backend/app/main.py` | 替换 import 和 fetch_news_with_fallback() |
| 新增依赖 | `pip install akshare` | 后端 venv 安装 |

## AKShare 新闻客户端设计

```
AKShareNewsClient
├── _call_async(fn, *args)              → asyncio.to_thread + 15s 超时包装
├── fetch_market_news(limit=20)         → stock_info_cjzc_em + stock_info_global_em
├── fetch_hot_topics(limit=10)          → stock_hot_rank_em
├── fetch_stock_news(code, limit=5)     → stock_news_em(symbol=code)
├── fetch_stock_notice(code, limit=3)   → stock_notice_report(symbol=code)
└── fetch_all_news(stock_codes) → str   → 汇总上述结果，格式化 AI 可读文本
```

### 关键设计

- **同步→异步**：全部 AKShare 函数用 `asyncio.to_thread` 包装，`asyncio.wait_for(30s)` 超时
- **容错**：`_call_async` 内部 try/except，任一接口失败不影响其他
- **去重**：同一标题前 30 字符去重
- **格式化**：三段式 — `【市场要闻】` / `【题材热点】` / `【个股资讯】`
- **并行**：各个子接口调用并发执行

### main.py 改动

1. 删除 `from app.data_sources.cls_client import ClsClient` 和 `from app.data_sources.sina_news import SinaNewsClient`
2. 新增 `from app.data_sources.akshare_news import AKShareNewsClient`
3. 初始化 `news_client = AKShareNewsClient()`
4. `fetch_news_with_fallback()` 简化为直接调用 `news_client.fetch_all_news(stock_codes)`

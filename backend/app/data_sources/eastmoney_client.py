"""东方财富 — 仅保留股票搜索 (push2.eastmoney.com 已不可达)"""
import httpx
from typing import List, Dict, Any
from app.data_sources.base import BaseDataSource


class EastmoneyDataSource(BaseDataSource):
    """东方财富数据源 — 仅搜索功能可用

    注意: push2.eastmoney.com 和 push2his.eastmoney.com 在网络上被阻断,
    行业板块/市场宽度/资金流向/行情均已迁移至 AKShareMarketClient.
    """

    def __init__(self):
        super().__init__("eastmoney")

    async def fetch(self, stock_code: str):
        """已废弃 — push2 API 不可达"""
        return None

    async def fetch_search(self, keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
        """东方财富股票搜索 (searchadapter.eastmoney.com 仍可用)"""
        try:
            url = "https://searchadapter.eastmoney.com/api/suggest/get"
            params = {
                "input": keyword,
                "type": "14",
                "token": "D43BF722C8E33BDC906FB84D85E326E8",
                "count": str(limit),
            }
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

            results = []
            if data.get("QuotationCodeTable") and data["QuotationCodeTable"].get("Data"):
                for item in data["QuotationCodeTable"]["Data"]:
                    code = item.get("Code", "")
                    market_code = item.get("MktNum", "")
                    market = "sh" if market_code == "1" else "sz"
                    results.append({
                        "code": code,
                        "name": item.get("Name", ""),
                        "market": market,
                        "full_code": f"{market}{code}",
                        "pinyin": item.get("PinYin", ""),
                    })
                return results[:limit]

            if data.get("data"):
                for item in data["data"]:
                    code = str(item.get("Code", ""))
                    market = "sh" if code.startswith("6") else "sz"
                    results.append({
                        "code": code,
                        "name": item.get("Name", ""),
                        "market": market,
                        "full_code": f"{market}{code}",
                        "pinyin": "",
                    })
            return results[:limit]
        except Exception:
            return []

    def is_available(self) -> bool:
        return True

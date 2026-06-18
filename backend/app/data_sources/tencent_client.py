"""腾讯财经 — 实时行情 (PE/PB/市值/换手率/涨跌停) + K线 + 搜索"""
import httpx
import re
from typing import Optional, Dict, Any, List
from app.data_sources.base import BaseDataSource


class TencentDataSource(BaseDataSource):
    """腾讯财经 HTTP API — 不封IP，GBK编码，~分隔"""

    def __init__(self):
        super().__init__("tencent")

    def _resolve_code(self, code: str) -> str:
        """股票代码转腾讯格式: sh600000 / sz000001 / bj8xxxxx
        已带前缀的代码直接返回 (如 sh000001 上证指数, sz399001 深证成指)
        """
        if code.startswith(("sh", "sz", "bj")):
            return code
        code = code.replace("sh", "").replace("sz", "").replace("bj", "")
        if code.startswith(("6", "9")):
            return f"sh{code}"
        elif code.startswith("8"):
            return f"bj{code}"
        else:
            return f"sz{code}"

    def _parse_one(self, line: str, raw_code: str) -> Optional[Dict[str, Any]]:
        """解析单行腾讯行情数据"""
        if not line.strip() or "=" not in line or '"' not in line:
            return None
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            return None
        return {
            "code": raw_code,
            "name": vals[1],
            "price": float(vals[3]) if vals[3] else 0,
            "last_close": float(vals[4]) if vals[4] else 0,
            "open": float(vals[5]) if vals[5] else 0,
            "change_amt": float(vals[31]) if vals[31] else 0,
            "change_pct": float(vals[32]) if vals[32] else 0,
            "high": float(vals[33]) if vals[33] else 0,
            "low": float(vals[34]) if vals[34] else 0,
            "amount_wan": float(vals[37]) if vals[37] else 0,
            "turnover_pct": float(vals[38]) if vals[38] else 0,
            "pe_ttm": float(vals[39]) if vals[39] else 0,
            "amplitude_pct": float(vals[43]) if vals[43] else 0,
            "mcap_yi": float(vals[44]) if vals[44] else 0,
            "float_mcap_yi": float(vals[45]) if vals[45] else 0,
            "pb": float(vals[46]) if vals[46] else 0,
            "limit_up": float(vals[47]) if vals[47] else 0,
            "limit_down": float(vals[48]) if vals[48] else 0,
            "vol_ratio": float(vals[49]) if vals[49] else 0,
            "pe_static": float(vals[52]) if vals[52] else 0,
            "source": "tencent",
        }

    async def fetch(self, stock_code: str) -> Optional[Dict[str, Any]]:
        try:
            prefixed = self._resolve_code(stock_code)
            url = f"https://qt.gtimg.cn/q={prefixed}"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                data = resp.content.decode("gbk")

            for line in data.strip().split(";"):
                result = self._parse_one(line, stock_code)
                if result:
                    return result
            return None
        except Exception:
            return None

    async def fetch_batch(self, codes: List[str]) -> Dict[str, Dict[str, Any]]:
        """批量获取多只股票行情 — 单次HTTP请求"""
        if not codes:
            return {}
        try:
            prefixed = ",".join(self._resolve_code(c) for c in codes)
            url = f"https://qt.gtimg.cn/q={prefixed}"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                data = resp.content.decode("gbk")

            results = {}
            raw_codes = {self._resolve_code(c): c for c in codes}
            for line in data.strip().split(";"):
                for prefix, raw_code in raw_codes.items():
                    if prefix in line:
                        result = self._parse_one(line, raw_code)
                        if result:
                            results[raw_code] = result
                        break
            return results
        except Exception:
            return {}

    async def fetch_kline(self, stock_code: str, period: str = "day", count: int = 120) -> Dict[str, Any]:
        """获取历史K线数据

        Args:
            stock_code: 股票代码 (如 sh000001, sz000001)
            period: K线周期 day/week/month/60/30/15/5
            count: 获取数量，最大2000

        Returns:
            {code, period, bars: [{date, open, close, high, low, volume, amount}], source}
        """
        try:
            prefixed = self._resolve_code(stock_code)
            param = f"{prefixed},{period},,,{count},qfq"
            url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={param}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://gu.qq.com/",
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            if data.get("code") != 0 and not data.get("data"):
                return {"code": stock_code, "period": period, "bars": [], "source": "tencent"}

            stock_data = data.get("data", {}).get(prefixed, {})
            kline_key = {"day": "day", "week": "week", "month": "month"}.get(period, f"m{period}")
            if period not in ("day", "week", "month"):
                kline_key = f"m{period}"
            klines = stock_data.get(kline_key, []) or stock_data.get("qfqday", []) or stock_data.get("day", [])

            bars = []
            for item in klines:
                if len(item) < 6:
                    continue
                bars.append({
                    "date": str(item[0]),
                    "open": float(item[1]) if item[1] else 0,
                    "close": float(item[2]) if item[2] else 0,
                    "high": float(item[3]) if item[3] else 0,
                    "low": float(item[4]) if item[4] else 0,
                    "volume": float(item[5]) if item[5] else 0,
                })
            bars = bars[-count:] if len(bars) > count else bars
            return {"code": stock_code, "period": period, "bars": bars, "source": "tencent"}
        except Exception:
            return {"code": stock_code, "period": period, "bars": [], "source": "tencent"}

    async def search_stock(self, keyword: str, limit: int = 20) -> List[Dict[str, Any]]:
        """搜索股票代码/名称/拼音

        Args:
            keyword: 搜索关键词
            limit: 返回数量上限

        Returns:
            [{code, name, market, pinyin}]
        """
        try:
            url = f"https://suggest3.sinajs.cn/suggest/type=11,12&key={keyword}"
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn",
            }
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                text = resp.text

            results = []
            # 新浪 suggest 返回格式: var suggest_xxx="名称,市场类型,代码,完整代码,名称,...;..."
            # 例: "贵州茅台,11,600519,sh600519,贵州茅台,...;sz000725,11,000725,sz000725,京东方A,..."
            # parts[0]=名称(或全码), parts[1]=市场类型, parts[2]=代码, parts[3]=全码, parts[4]=名称
            match = re.search(r'"([^"]*)"', text)
            if not match:
                return results
            items = match.group(1).split(";")
            for item in items:
                if not item.strip():
                    continue
                parts = item.split(",")
                if len(parts) >= 4:
                    code = parts[2].strip()
                    full_code = parts[3].strip()
                    name = parts[4].strip() if len(parts) >= 5 and parts[4].strip() else parts[0].strip()
                    market = "sh" if full_code.startswith("sh") else "sz"
                    results.append({
                        "code": code,
                        "name": name,
                        "market": market,
                        "full_code": full_code,
                        "pinyin": "",
                    })
                if len(results) >= limit:
                    break
            return results
        except Exception:
            return []

    def is_available(self) -> bool:
        return True

"""Tushare Pro — 主数据源 (Level 1, ¥200/年)"""
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import tushare as ts
from app.data_sources.base import BaseDataSource
from app.utils.logger import logger


class TushareDataSource(BaseDataSource):
    """Tushare Pro 数据源 — 覆盖行情/K线/资金流/基本面/龙虎榜/新闻

    配置: 在 .env.local 中设置 TUSHARE_TOKEN
    注册: https://tushare.pro  (注册送积分, 认证后够换 Level 1)
    """

    def __init__(self):
        super().__init__("tushare")
        token = os.getenv("TUSHARE_TOKEN", "")
        if token:
            ts.set_token(token)
            self._pro = ts.pro_api()
            self._available = True
            logger.info("Tushare 已配置，数据源可用")
        else:
            self._pro = None
            self._available = False
            logger.warning("TUSHARE_TOKEN 未配置")

    def is_available(self) -> bool:
        return self._available

    # ===== 实时行情 =====

    async def fetch(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """获取单只股票实时行情"""
        if not self._available:
            return None
        try:
            ts_code = self._to_ts_code(stock_code)
            df = self._pro.daily(ts_code=ts_code, limit=2)
            if df is None or df.empty:
                return None
            row = df.iloc[0]
            return {
                "code": stock_code,
                "name": "",
                "price": float(row.get("close", 0)),
                "last_close": float(row.get("pre_close", 0)),
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "change_pct": float(row.get("pct_chg", 0)),
                "amount_wan": float(row.get("amount", 0)) / 10000 if row.get("amount") else 0,
                "source": "tushare",
            }
        except Exception as e:
            logger.debug(f"Tushare fetch {stock_code} failed: {e}")
            return None

    async def fetch_batch(self, codes: List[str]) -> Dict[str, Dict[str, Any]]:
        """批量获取多只股票行情"""
        if not self._available or not codes:
            return {}
        try:
            ts_codes = [self._to_ts_code(c) for c in codes]
            code_map = dict(zip(ts_codes, codes))
            df = self._pro.daily(ts_code=",".join(ts_codes), limit=2)
            if df is None or df.empty:
                return {}
            results = {}
            for _, row in df.iterrows():
                ts_c = row.get("ts_code", "")
                orig_code = code_map.get(ts_c, ts_c)
                results[orig_code] = {
                    "code": orig_code,
                    "name": "",
                    "price": float(row.get("close", 0)),
                    "last_close": float(row.get("pre_close", 0)),
                    "open": float(row.get("open", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "change_pct": float(row.get("pct_chg", 0)),
                    "amount_wan": float(row.get("amount", 0)) / 10000 if row.get("amount") else 0,
                    "source": "tushare",
                }
            return results
        except Exception as e:
            logger.debug(f"Tushare batch fetch failed: {e}")
            return {}

    # ===== K线 =====

    async def fetch_kline(self, stock_code: str, period: str = "day", count: int = 120) -> Dict[str, Any]:
        """获取K线数据 (日线/周线/月线)"""
        if not self._available:
            return {"code": stock_code, "period": period, "bars": [], "source": "tushare"}
        try:
            ts_code = self._to_ts_code(stock_code)
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=count * 2)).strftime("%Y%m%d")

            if period == "day":
                df = self._pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
            elif period == "week":
                df = self._pro.weekly(ts_code=ts_code, start_date=start_date, end_date=end_date)
            elif period == "month":
                df = self._pro.monthly(ts_code=ts_code, start_date=start_date, end_date=end_date)
            else:
                return {"code": stock_code, "period": period, "bars": [], "source": "tushare"}

            if df is None or df.empty:
                return {"code": stock_code, "period": period, "bars": [], "source": "tushare"}

            bars = []
            df = df.sort_values("trade_date")
            for _, row in df.tail(count).iterrows():
                bars.append({
                    "date": str(row.get("trade_date", "")),
                    "open": float(row.get("open", 0)),
                    "close": float(row.get("close", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "volume": float(row.get("vol", 0)),
                })
            return {"code": stock_code, "period": period, "bars": bars, "source": "tushare"}
        except Exception as e:
            logger.debug(f"Tushare kline {stock_code} failed: {e}")
            return {"code": stock_code, "period": period, "bars": [], "source": "tushare"}

    # ===== 资金流向 =====

    async def fetch_fund_flow(self, stock_code: str, days: int = 5) -> Optional[list]:
        """获取个股资金流向"""
        if not self._available:
            return None
        try:
            ts_code = self._to_ts_code(stock_code)
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
            df = self._pro.moneyflow(ts_code=ts_code, start_date=start_date, end_date=end_date)
            if df is None or df.empty:
                return None
            results = []
            for _, row in df.head(days).iterrows():
                results.append({
                    "date": str(row.get("trade_date", "")),
                    "buy_sm_vol": float(row.get("buy_sm_vol", 0)),
                    "sell_sm_vol": float(row.get("sell_sm_vol", 0)),
                    "buy_md_vol": float(row.get("buy_md_vol", 0)),
                    "sell_md_vol": float(row.get("sell_md_vol", 0)),
                    "buy_lg_vol": float(row.get("buy_lg_vol", 0)),
                    "sell_lg_vol": float(row.get("sell_lg_vol", 0)),
                    "buy_elg_vol": float(row.get("buy_elg_vol", 0)),
                    "sell_elg_vol": float(row.get("sell_elg_vol", 0)),
                    "net_mf_vol": float(row.get("net_mf_vol", 0)),
                })
            return results
        except Exception as e:
            logger.debug(f"Tushare fund_flow {stock_code} failed: {e}")
            return None

    # ===== 基本面 =====

    async def fetch_fundamentals(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """获取基本面数据 (PE/PB/ROE/营收等)"""
        if not self._available:
            return None
        try:
            ts_code = self._to_ts_code(stock_code)
            df = self._pro.daily_basic(ts_code=ts_code, limit=5)
            if df is None or df.empty:
                return None
            row = df.iloc[0]
            return {
                "code": stock_code,
                "pe": float(row.get("pe", 0)),
                "pe_ttm": float(row.get("pe_ttm", 0)),
                "pb": float(row.get("pb", 0)),
                "ps": float(row.get("ps", 0)),
                "ps_ttm": float(row.get("ps_ttm", 0)),
                "dv_ratio": float(row.get("dv_ratio", 0)),
                "total_mv": float(row.get("total_mv", 0)),
                "circ_mv": float(row.get("circ_mv", 0)),
                "turnover_rate": float(row.get("turnover_rate", 0)),
                "volume_ratio": float(row.get("volume_ratio", 0)),
                "source": "tushare",
            }
        except Exception as e:
            logger.debug(f"Tushare fundamentals {stock_code} failed: {e}")
            return None

    # ===== 辅助 =====

    def _to_ts_code(self, code: str) -> str:
        """转成 Tushare 格式: 000001 → 000001.SZ, 600000 → 600000.SH"""
        code = code.replace("sh", "").replace("sz", "").replace("bj", "")
        if code.startswith(("6", "9")):
            return f"{code}.SH"
        elif code.startswith("8"):
            return f"{code}.BJ"
        else:
            return f"{code}.SZ"

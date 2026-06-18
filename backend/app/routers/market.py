"""市场行情路由 — 指数/板块/行情/K线/搜索"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.utils.tiered_cache import tiered_cache
from app.trading_engine.position import PositionManager
from app.data_sources.tencent_client import TencentDataSource
from app.data_sources.eastmoney_client import EastmoneyDataSource

# 需要从主应用注入的数据源实例
tencent_client: TencentDataSource = None
eastmoney_client: EastmoneyDataSource = None
market_client = None  # AKShareMarketClient

router = APIRouter(prefix="/api", tags=["市场行情"])

import time as _time
from app.ai.cloud_client import cloud as _cloud

_server_start_time = _time.time()


@router.get("/health")
async def health_check():
    """服务健康检查"""
    db_ok = False
    try:
        db = SessionLocal()
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
        db.close()
        db_ok = True
    except Exception:
        pass

    ds_ok = False
    try:
        ds_ok = await _cloud.is_available()
    except Exception:
        pass

    return {
        "status": "ok",
        "uptime_seconds": int(_time.time() - _server_start_time),
        "deepseek": "ok" if ds_ok else "unavailable",
        "database": "ok" if db_ok else "error",
        "version": "v6",
    }




def init_market_router(tc, ec, mc, ms):
    """由 main.py 调用，注入共享的数据源实例"""
    global tencent_client, eastmoney_client, market_client
    tencent_client = tc
    eastmoney_client = ec
    market_client = mc


# ========== 大盘 & 板块 API ==========

@router.get("/market/indices")
async def get_market_indices():
    """获取三大指数实时行情 + 涨跌家数"""
    codes = ["sh000001", "sz399001", "sz399006"]
    data = await tencent_client.fetch_batch(codes)
    return {"indices": data, "timestamp": datetime.now().isoformat()}


@router.get("/market/sectors")
async def get_market_sectors():
    """获取行业资金流向 Top20 (同花顺)"""
    sectors = await market_client.fetch_fund_flow_industry()
    if sectors:
        return {"sectors": sectors, "total": len(sectors), "timestamp": datetime.now().isoformat()}
    return {"sectors": [], "total": 0, "timestamp": datetime.now().isoformat()}


@router.get("/market/breadth")
async def get_market_breadth():
    """获取市场宽度 — 从主要指数数据估算"""
    indices = await market_client.fetch_market_indices()
    if indices:
        return {"indices": indices, "timestamp": datetime.now().isoformat()}
    return {"indices": [], "timestamp": datetime.now().isoformat()}


# ========== 实时数据 & 持仓行情 ==========

@router.get("/realtime/{stock_code}")
async def get_realtime(stock_code: str):
    data = await tencent_client.fetch(stock_code)
    if not data:
        raise HTTPException(status_code=404, detail="无法获取实时数据")
    return {"data": data}


@router.get("/holdings/realtime")
async def get_holdings_realtime(db: Session = Depends(get_db)):
    """批量获取所有当前持仓的实时数据（从 Position 表）"""
    positions = PositionManager.get_all(db)
    if not positions:
        return {"data": [], "timestamp": datetime.now().isoformat()}

    codes = [p.stock_code for p in positions]
    batch_data = await tencent_client.fetch_batch(codes)

    PositionManager.refresh_market_prices(db, batch_data)
    db.commit()

    results = []
    for p in positions:
        rt = batch_data.get(p.stock_code, {})
        price = rt.get("price", 0) or 0
        cost = p.avg_cost / 100 if p.avg_cost else 0
        pnl_pct = ((price - cost) / cost * 100) if cost and price else 0
        results.append({
            "code": p.stock_code, "name": p.stock_name or rt.get("name", ""),
            "position": p.quantity,
            "cost_price": round(cost, 2), "price": price,
            "market_value": round(p.market_value / 100, 2) if p.market_value else 0,
            "unrealized_pnl": round(p.unrealized_pnl / 100, 2) if p.unrealized_pnl else 0,
            "today_bought": p.today_bought_qty, "board_type": p.board_type,
            "change_pct": rt.get("change_pct", 0) if rt else 0,
            "pnl_pct": round(pnl_pct, 2),
            "pe_ttm": rt.get("pe_ttm", 0) if rt else 0,
            "pb": rt.get("pb", 0) if rt else 0,
            "vol_ratio": rt.get("vol_ratio", 0) if rt else 0,
            "turnover_pct": rt.get("turnover_pct", 0) if rt else 0,
        })
    return {"data": results, "timestamp": datetime.now().isoformat()}


# ========== K线 & 资金流 ==========

@router.get("/kline/{stock_code}")
async def get_kline(stock_code: str, period: str = "day", count: int = 120):
    """获取历史K线数据"""
    cache_key = f"kline_{stock_code}_{period}_{count}"
    cached = tiered_cache.get(cache_key)
    if cached:
        return cached
    result = await tencent_client.fetch_kline(stock_code, period, count)
    tiered_cache.set(cache_key, result, tier=2)
    return result


@router.get("/fund-flow/{stock_code}")
async def get_fund_flow(stock_code: str, days: int = 5):
    """获取个股资金流向 (同花顺)"""
    cache_key = f"fundflow_{stock_code}"
    cached = tiered_cache.get(cache_key)
    if cached:
        return cached
    all_flows = await market_client.fetch_fund_flow_individual()
    pure_code = stock_code.replace('sh', '').replace('sz', '').replace('bj', '')
    result = None
    if all_flows:
        for f in all_flows:
            if f.get('code') == pure_code:
                result = f
                break
    tiered_cache.set(cache_key, result or {}, tier=3)
    return result or {}


# ========== 搜索 ==========

@router.get("/search/stock")
async def search_stock(keyword: str, limit: int = 10):
    """股票代码/名称联想搜索"""
    cache_key = f"search_{keyword}_{limit}"
    cached = tiered_cache.get(cache_key)
    if cached:
        return cached
    results = await tencent_client.search_stock(keyword, limit)
    if not results:
        results = await eastmoney_client.fetch_search(keyword, limit)
    resp = {"results": results}
    tiered_cache.set(cache_key, resp, tier=2)
    return resp

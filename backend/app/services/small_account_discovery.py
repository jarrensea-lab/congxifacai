"""Small-account discovery seeds for outside-pool strategy scans."""
from __future__ import annotations

import math
from typing import Any

from app.services.quant_lifecycle import lot_size_for_code
from app.services.strategy_profile import get_strategy_profile


DEFAULT_SMALL_ACCOUNT_SEEDS: tuple[dict[str, str], ...] = (
    {"code": "000629", "name": "钒钛股份", "theme": "资源/钒钛"},
    {"code": "000100", "name": "TCL科技", "theme": "面板/半导体显示"},
    {"code": "000725", "name": "京东方A", "theme": "面板/低价大成交"},
    {"code": "600839", "name": "四川长虹", "theme": "AI终端/家电"},
    {"code": "002131", "name": "利欧股份", "theme": "AI营销/低价高波动"},
    {"code": "002261", "name": "拓维信息", "theme": "华为/算力应用"},
    {"code": "300002", "name": "神州泰岳", "theme": "AI应用/游戏"},
    {"code": "300339", "name": "润和软件", "theme": "鸿蒙/软件"},
)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return default


def build_small_account_seed_candidates(
    *,
    available_cash: float,
    total_assets: float,
    existing_codes: set[str] | None = None,
    max_candidates: int = 8,
) -> list[dict[str, Any]]:
    """Return deterministic outside-pool seeds that a small account can plausibly buy."""
    existing = {str(code).strip() for code in existing_codes or set()}
    profile = get_strategy_profile()
    assets = _to_float(total_assets, _to_float(available_cash))
    cash = _to_float(available_cash)
    single_limit_pct = _to_float(profile.get("single_position_limit_pct"), 50)
    single_budget = min(cash, assets * (single_limit_pct / 100) if assets else cash)
    rows: list[dict[str, Any]] = []
    for item in DEFAULT_SMALL_ACCOUNT_SEEDS:
        code = item["code"]
        if code in existing:
            continue
        lot_size = lot_size_for_code(code)
        max_price = math.floor((single_budget / lot_size) * 100) / 100 if lot_size else 0
        rows.append({
            **item,
            "source": "small_account_discovery",
            "lot_size": lot_size,
            "max_entry_price": max_price,
            "watch_reason": "池外小账户补扫；只有实时价格、量能、成交额、资金流同时触发才可入池。",
        })
    return rows[:max_candidates]

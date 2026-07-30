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


def _money_to_yuan(value: Any) -> float:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return 0.0
    multiplier = 1.0
    if text.endswith("亿"):
        multiplier = 100_000_000.0
        text = text[:-1]
    elif text.endswith("万"):
        multiplier = 10_000.0
        text = text[:-1]
    return _to_float(text) * multiplier


def build_account_budget_snapshot(
    *,
    available_cash: float,
    total_assets: float,
) -> dict[str, float]:
    """Return the shared account reserve and one-name execution budget."""
    profile = get_strategy_profile()
    assets = _to_float(total_assets, _to_float(available_cash))
    cash = _to_float(available_cash)
    single_limit_pct = _to_float(profile.get("single_position_limit_pct"), 50)
    reserve_cash = (
        assets * (_to_float(profile.get("cash_reserve_pct"), 10) / 100)
        if assets
        else 0
    )
    executable_budget = round(
        max(
            0.0,
            min(
                cash - reserve_cash,
                assets * (single_limit_pct / 100) if assets else cash,
            ),
        ),
        2,
    )
    return {
        "available_cash": round(cash, 2),
        "total_assets": round(assets, 2),
        "reserve_cash": round(max(0.0, reserve_cash), 2),
        "executable_budget": executable_budget,
    }


def _executable_budget(*, available_cash: float, total_assets: float) -> float:
    return build_account_budget_snapshot(
        available_cash=available_cash,
        total_assets=total_assets,
    )["executable_budget"]


def discover_affordable_market_candidates(
    *,
    market_rows: list[dict[str, Any]] | None,
    available_cash: float,
    total_assets: float,
    existing_codes: set[str] | None = None,
    priority_codes: set[str] | None = None,
    max_candidates: int = 30,
) -> dict[str, Any]:
    """Scan every supplied A-share row, then rank only currently affordable names."""
    existing = {str(code).strip() for code in existing_codes or set()}
    priorities = {
        str(code).strip().zfill(6)
        for code in priority_codes or set()
        if str(code).strip()
    } - existing
    executable_budget = _executable_budget(
        available_cash=available_cash,
        total_assets=total_assets,
    )
    scanned_count = 0
    tradeable_count = 0
    affordable_count = 0
    budget_blocked = 0
    candidates: list[dict[str, Any]] = []
    seen_priorities: set[str] = set()
    priority_budget_blocked: list[dict[str, Any]] = []
    for raw in market_rows or []:
        if not isinstance(raw, dict):
            continue
        scanned_count += 1
        code = str(raw.get("code") or "").strip().zfill(6)
        name = str(raw.get("name") or code).strip()
        if (
            code in existing
            or len(code) != 6
            or not code.startswith(
                ("000", "001", "002", "003", "300", "600", "601", "603", "605")
            )
            or "ST" in name.upper()
            or "退" in name
        ):
            continue
        price = _to_float(
            raw.get("latest_price")
            or raw.get("price")
            or raw.get("current_price")
        )
        if price <= 0:
            continue
        if code in priorities:
            seen_priorities.add(code)
        tradeable_count += 1
        lot_size = lot_size_for_code(code)
        lot_value = round(price * lot_size, 2)
        if lot_value > executable_budget:
            budget_blocked += 1
            if code in priorities:
                priority_budget_blocked.append({
                    "code": code,
                    "name": name,
                    "price": price,
                    "lot_value": lot_value,
                    "executable_budget": executable_budget,
                    "reason": "lot_size_exceeded",
                })
            continue
        affordable_count += 1
        change_pct = _to_float(raw.get("change_pct"))
        turnover = _to_float(raw.get("turnover"))
        net_flow = _money_to_yuan(raw.get("net") or raw.get("net_amount"))
        amount = _money_to_yuan(raw.get("amount"))
        candidates.append({
            "code": code,
            "name": name,
            "price": price,
            "source": "dynamic_market_discovery",
            "lot_size": lot_size,
            "lot_value": lot_value,
            "max_entry_price": (
                math.floor((executable_budget / lot_size) * 100) / 100
                if lot_size
                else 0
            ),
            "market_evidence": {
                "latest_price": price,
                "change_pct": change_pct,
                "turnover_pct": turnover,
                "net_flow_yuan": net_flow,
                "amount_yuan": amount,
            },
            "watch_reason": (
                "全市场发现后已通过最小交易单位与账户预算过滤；"
                "仍需补齐量价、资金、基本面和风险收益评分。"
            ),
        })
    candidates.sort(
        key=lambda item: (
            -float((item["market_evidence"]).get("net_flow_yuan") or 0),
            -float((item["market_evidence"]).get("amount_yuan") or 0),
            -abs(float((item["market_evidence"]).get("change_pct") or 0)),
            item["code"],
        )
    )
    limit = max(0, int(max_candidates))
    selected = candidates[:limit]
    selected_codes = {item["code"] for item in selected}
    selected.extend(
        item
        for item in candidates
        if item["code"] in priorities and item["code"] not in selected_codes
    )
    priority_missing = [
        {"code": code, "name": code, "reason": "missing_required_data"}
        for code in sorted(priorities - seen_priorities)
    ]
    return {
        "candidates": selected,
        "metrics": {
            "scanned_count": scanned_count,
            "tradeable_count": tradeable_count,
            "affordable_count": affordable_count,
            "ranked_count": len(selected),
            "priority_count": len(priorities),
            "executable_budget": executable_budget,
        },
        "rejected": {
            "budget_blocked": budget_blocked,
            "priority_budget_blocked": priority_budget_blocked,
            "priority_missing": priority_missing,
        },
    }


def build_dynamic_small_account_candidates(
    *,
    market_rows: list[dict[str, Any]] | None,
    available_cash: float,
    total_assets: float,
    existing_codes: set[str] | None = None,
    max_candidates: int = 8,
) -> list[dict[str, Any]]:
    """Build rotating research candidates from the current all-market fund-flow table."""
    existing = {str(code).strip() for code in existing_codes or set()}
    profile = get_strategy_profile()
    assets = _to_float(total_assets, _to_float(available_cash))
    cash = _to_float(available_cash)
    single_limit_pct = _to_float(profile.get("single_position_limit_pct"), 50)
    reserve_cash = assets * (_to_float(profile.get("cash_reserve_pct"), 10) / 100) if assets else 0
    single_budget = max(
        0.0,
        min(cash - reserve_cash, assets * (single_limit_pct / 100) if assets else cash),
    )
    ranked: list[dict[str, Any]] = []
    for raw in market_rows or []:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("code") or "").strip().zfill(6)
        name = str(raw.get("name") or code).strip()
        if (
            code in existing
            or len(code) != 6
            or not code.startswith(("000", "001", "002", "003", "300", "600", "601", "603", "605"))
            or "ST" in name.upper()
            or "退" in name
        ):
            continue
        price = _to_float(raw.get("latest_price"))
        change_pct = _to_float(raw.get("change_pct"))
        turnover = _to_float(raw.get("turnover"))
        net_flow = _money_to_yuan(raw.get("net"))
        amount = _money_to_yuan(raw.get("amount"))
        lot_size = lot_size_for_code(code)
        max_entry_price = math.floor((single_budget / lot_size) * 100) / 100 if lot_size else 0
        if (
            price <= 0
            or price > max_entry_price
            or change_pct < -3
            or change_pct > 8
            or turnover < 0.5
            or turnover > 25
            or net_flow <= 0
            or amount < 100_000_000
        ):
            continue
        ranked.append({
            "code": code,
            "name": name,
            "theme": "动态资金流/量价候选",
            "source": "dynamic_fund_flow_discovery",
            "lot_size": lot_size,
            "max_entry_price": max_entry_price,
            "market_evidence": {
                "latest_price": price,
                "change_pct": change_pct,
                "turnover_pct": turnover,
                "net_flow_yuan": net_flow,
                "amount_yuan": amount,
            },
            "watch_reason": "动态资金流候选；需在同轮补齐实时行情、K线、财务与风控评分。",
        })
    ranked.sort(
        key=lambda item: (
            -float((item.get("market_evidence") or {}).get("net_flow_yuan") or 0),
            -float((item.get("market_evidence") or {}).get("amount_yuan") or 0),
            item["code"],
        )
    )
    return ranked[:max_candidates]


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
    reserve_cash = assets * (_to_float(profile.get("cash_reserve_pct"), 10) / 100) if assets else 0
    single_budget = max(
        0.0,
        min(cash - reserve_cash, assets * (single_limit_pct / 100) if assets else cash),
    )
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

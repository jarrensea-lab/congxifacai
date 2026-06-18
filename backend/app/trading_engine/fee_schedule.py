"""费用引擎 — 按板块/方向查表计算完整交易费用"""
from typing import Dict

# 费率配置 (分, 1元=100分)
FEE_SCHEDULE = {
    "main": {
        "commission_rate": 0.00015,       # 佣金 0.025%
        "min_commission": 500,             # 最低5元
        "stamp_tax_rate": 0.0005,           # 印花税 0.1% (仅卖出)
        "transfer_fee_rate": 0.00001,      # 过户费 0.001% (双向)
        "handling_fee_rate": 0.0000487,    # 经手费 ~0.00487% (双向)
        "regulatory_fee_rate": 0.00002,    # 证管费 ~0.002% (双向)
        "slippage_rate": 0.001,            # 滑点 0.1%
        "lot_size": 100,
    },
    "chi_next": {
        "commission_rate": 0.00015,
        "min_commission": 500,
        "stamp_tax_rate": 0.0005,
        "transfer_fee_rate": 0.00001,
        "handling_fee_rate": 0.0000487,
        "regulatory_fee_rate": 0.00002,
        "slippage_rate": 0.001,
        "lot_size": 100,
    },
    "star": {
        "commission_rate": 0.00015,
        "min_commission": 500,
        "stamp_tax_rate": 0.0005,
        "transfer_fee_rate": 0.00001,
        "handling_fee_rate": 0.0000487,
        "regulatory_fee_rate": 0.00002,
        "slippage_rate": 0.001,
        "lot_size": 200,  # 科创板200股/手
    },
    "bei_jiao": {
        "commission_rate": 0.00015,
        "min_commission": 500,
        "stamp_tax_rate": 0.0005,
        "transfer_fee_rate": 0.00001,
        "handling_fee_rate": 0.0000487,
        "regulatory_fee_rate": 0.00002,
        "slippage_rate": 0.001,
        "lot_size": 100,
    }
}

# 涨跌停比例 (按板块)
PRICE_LIMIT_PCT = {
    "main": 0.10,     # 主板 ±10%
    "chi_next": 0.20, # 创业板 ±20%
    "star": 0.20,     # 科创板 ±20%
    "bei_jiao": 0.30, # 北交所 ±30%
}


def get_board_type(code: str) -> str:
    """根据代码判断板块"""
    if code.startswith("688") or code.startswith("689"):
        return "star"
    elif code.startswith("300") or code.startswith("301"):
        return "chi_next"
    elif code.startswith("8") or code.startswith("4"):
        return "bei_jiao"
    return "main"


def get_fee_config(code: str) -> dict:
    """获取某股票的费率配置"""
    board = get_board_type(code)
    return FEE_SCHEDULE.get(board, FEE_SCHEDULE["main"])


def get_price_limit_pct(code: str) -> float:
    """获取某股票的涨跌停比例"""
    board = get_board_type(code)
    return PRICE_LIMIT_PCT.get(board, 0.10)


def calc_fees(code: str, direction: str, amount_fen: int) -> Dict:
    """计算完整交易费用 (返回分)"""
    cfg = get_fee_config(code)
    commission = max(cfg["min_commission"], int(amount_fen * cfg["commission_rate"]))
    stamp_tax = int(amount_fen * cfg["stamp_tax_rate"]) if direction == "sell" else 0
    transfer = int(amount_fen * cfg["transfer_fee_rate"])
    handling = int(amount_fen * cfg["handling_fee_rate"])
    regulatory = int(amount_fen * cfg["regulatory_fee_rate"])
    total = commission + stamp_tax + transfer + handling + regulatory
    return {
        "commission": commission,
        "stamp_tax": stamp_tax,
        "transfer": transfer,
        "handling": handling,
        "regulatory": regulatory,
        "total": total,
    }


def apply_slippage(price_fen: int, direction: str, code: str) -> int:
    """应用滑点: 买+滑点, 卖-滑点"""
    cfg = get_fee_config(code)
    slip = max(1, int(price_fen * cfg["slippage_rate"]))
    return price_fen + slip if direction == "buy" else price_fen - slip


def get_lot_size(code: str) -> int:
    """获取最小交易单位"""
    cfg = get_fee_config(code)
    return cfg["lot_size"]


def round_lot(quantity: int, code: str) -> int:
    """向下取整到板别最小交易单位，不足 1 手向上取整"""
    lot = get_lot_size(code)
    if quantity <= 0:
        return 0
    if quantity < lot:
        return lot
    return (quantity // lot) * lot

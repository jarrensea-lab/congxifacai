"""模拟撮合引擎 — 市价/限价成交 + 滑点 + 完整费用 + T+1"""
from datetime import date
from typing import Optional
from app.trading_engine.fee_schedule import calc_fees, apply_slippage, get_lot_size
from app.utils.logger import logger


class SimBroker:
    """模拟券商撮合引擎"""

    def __init__(self):
        # T+1: 从 Position.today_bought_date 读取, 不再依赖内存
        pass

    def execute_market_order(self, stock_code: str, direction: str,
                              current_price_fen: int, quantity: int,
                              limit_up_fen: int = 0, limit_down_fen: int = 0,
                              today_bought_qty: int = 0,
                              today: date = None) -> Optional[dict]:
        """市价单撮合, 返回成交信息或 None(被拒)

        返回 dict:
            filled_price: 成交价(分)
            filled_quantity: 成交量
            amount: 成交金额(分)
            fee: 总手续费(分)
            fee_detail: 费用明细 dict
        """
        if today is None:
            today = date.today()

        # T+1 检查: 当日买入不可卖出
        if direction == "sell" and today_bought_qty > 0:
            logger.info(f"T+1 拒绝卖出 {stock_code}: 当日买入 {today_bought_qty} 股未交收")
            return None

        # 滑点: 模拟实际成交价与报价之间的偏差
        filled_price = apply_slippage(current_price_fen, direction, stock_code)

        # 涨跌停检查
        if limit_up_fen > 0 and filled_price > limit_up_fen:
            logger.info(f"涨停拒单 {stock_code}: {filled_price} > {limit_up_fen}")
            return None
        if limit_down_fen > 0 and filled_price < limit_down_fen:
            logger.info(f"跌停拒单 {stock_code}: {filled_price} < {limit_down_fen}")
            return None

        # 最小交易单位检查
        lot_size = get_lot_size(stock_code)
        filled_qty = (quantity // lot_size) * lot_size
        if filled_qty <= 0:
            logger.info(f"数量不足最小交易单位 {stock_code}: {quantity} < {lot_size}")
            return None

        amount = filled_price * filled_qty

        # 计算完整交易费用
        fee_detail = calc_fees(stock_code, direction, amount)
        total_fee = fee_detail["total"]

        logger.info(f"市价撮合成交: {direction} {stock_code} {filled_qty}股 "
                     f"@¥{filled_price/100:.2f} 金额¥{amount/100:.2f} 费用¥{total_fee/100:.2f}")
        return {
            "filled_price": filled_price,
            "filled_quantity": filled_qty,
            "amount": amount,
            "fee": total_fee,
            "fee_detail": fee_detail,
        }

    def execute_limit_order(self, stock_code: str, direction: str,
                             limit_price_fen: int, quantity: int,
                             current_price_fen: int,
                             limit_up_fen: int = 0, limit_down_fen: int = 0,
                             today_bought_qty: int = 0,
                             today: date = None) -> Optional[dict]:
        """限价单撮合, 返回成交信息或 None(未成交/被拒)

        返回 dict 同 execute_market_order
        """
        if today is None:
            today = date.today()

        # T+1 检查
        if direction == "sell" and today_bought_qty > 0:
            logger.info(f"T+1 拒绝卖出 {stock_code}: 当日买入不可卖出")
            return None

        # 限价判断: 买单价需 >= 当前价, 卖单价需 <= 当前价
        if direction == "buy" and limit_price_fen < current_price_fen:
            logger.debug(f"限价买单未达条件 {stock_code}: 限价{limit_price_fen} < 市价{current_price_fen}")
            return None
        if direction == "sell" and limit_price_fen > current_price_fen:
            logger.debug(f"限价卖单未达条件 {stock_code}: 限价{limit_price_fen} > 市价{current_price_fen}")
            return None

        # 滑点: 在限价基础上再加滑点, 模拟市场冲击
        filled_price = apply_slippage(limit_price_fen, direction, stock_code)

        # 涨跌停检查
        if limit_up_fen > 0 and filled_price > limit_up_fen:
            logger.info(f"涨停拒单 {stock_code}: {filled_price} > {limit_up_fen}")
            return None
        if limit_down_fen > 0 and filled_price < limit_down_fen:
            logger.info(f"跌停拒单 {stock_code}: {filled_price} < {limit_down_fen}")
            return None

        # 最小交易单位
        lot_size = get_lot_size(stock_code)
        filled_qty = (quantity // lot_size) * lot_size
        if filled_qty <= 0:
            logger.info(f"数量不足最小交易单位 {stock_code}: {quantity} < {lot_size}")
            return None

        amount = filled_price * filled_qty

        # 完整费用
        fee_detail = calc_fees(stock_code, direction, amount)
        total_fee = fee_detail["total"]

        logger.info(f"限价撮合成交: {direction} {stock_code} {filled_qty}股 "
                     f"@¥{filled_price/100:.2f} 金额¥{amount/100:.2f} 费用¥{total_fee/100:.2f}")
        return {
            "filled_price": filled_price,
            "filled_quantity": filled_qty,
            "amount": amount,
            "fee": total_fee,
            "fee_detail": fee_detail,
        }
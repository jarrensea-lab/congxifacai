"""风控管道 — 9-Gate Pipeline 模式"""
from datetime import datetime, date, time
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import TradeLog, Position
from app.utils.logger import logger


class RiskGuard:
    """多维度风控检查 (Pipeline模式, 每道Gate独立)"""

    MAX_SINGLE_POSITION_PCT = 0.20
    DAILY_LOSS_LIMIT_PCT = 0.05
    MAX_DRAWDOWN_PCT = 0.20
    MAX_DAILY_TRADES_PER_STOCK = 3
    # 最低保留现金 = max(总资产的 30%, ¥500)
    # 修复: 固定¥5000对小账户(¥3000)永远无法满足，改为动态比例
    MIN_CASH_RESERVE_PCT = 0.30
    MIN_CASH_FEN_ABSOLUTE = 50000  # 绝对底线 ¥500
    MAX_STAR_BOARD_PCT = 0.50  # 科创板总仓位上限50%
    MAX_LOSS_PER_TRADE_PCT = 0.03  # 单笔最大可承受损失3%

    # 交易时段
    MORNING_START = time(9, 30)
    MORNING_END = time(11, 30)
    AFTERNOON_START = time(13, 0)
    AFTERNOON_END = time(15, 0)

    def __init__(self):
        self._db: Optional[Session] = None

    def _get_db(self) -> Session:
        if self._db is None:
            self._db = SessionLocal()
        return self._db

    def close(self):
        if self._db:
            self._db.close()
            self._db = None

    # ===== Gate 1: 交易时间 =====
    def check_trading_hours(self) -> Tuple[bool, str]:
        now = datetime.now().time()
        if self.MORNING_START <= now <= self.MORNING_END:
            return True, ""
        if self.AFTERNOON_START <= now <= self.AFTERNOON_END:
            return True, ""
        return False, f"非交易时段: {now.strftime('%H:%M')}"

    # ===== Gate 2: 涨跌停校验 =====
    def check_price_limit(self, stock_code: str, price_fen: int,
                          limit_up_fen: int, limit_down_fen: int,
                          direction: str) -> Tuple[bool, str]:
        if direction == "buy" and limit_up_fen > 0 and price_fen >= limit_up_fen:
            return False, f"涨停限制: ¥{price_fen/100:.2f} >= 涨停¥{limit_up_fen/100:.2f}"
        if direction == "sell" and limit_down_fen > 0 and price_fen <= limit_down_fen:
            return False, f"跌停限制: ¥{price_fen/100:.2f} <= 跌停¥{limit_down_fen/100:.2f}"
        return True, ""

    # ===== Gate 3: T+1 =====
    def check_t1(self, stock_code: str, direction: str) -> Tuple[bool, str]:
        if direction != "sell":
            return True, ""
        db = self._get_db()
        pos = db.query(Position).filter(Position.stock_code == stock_code).first()
        if pos and pos.today_bought_qty > 0:
            today = date.today().isoformat()
            if pos.today_bought_date == today:
                return False, f"T+1限制: 今日买入{pos.today_bought_qty}股, 次交易日方可卖出"
        return True, ""

    # ===== Gate 4: 单票仓位上限 =====
    def check_position_limit(self, stock_code: str, amount_fen: int,
                             cash_fen: int, total_equity_fen: int) -> Tuple[bool, str]:
        max_pct = 0.30 if stock_code.startswith("68") else self.MAX_SINGLE_POSITION_PCT
        max_amount = int(total_equity_fen * max_pct)
        # 渐进放宽: 现金足够 → 50%现金, 还不行 → 只要现金能支付最少一手就放行
        if amount_fen > max_amount and cash_fen >= amount_fen:
            relaxed = int(cash_fen * 0.50)
            if amount_fen <= relaxed:
                max_amount = relaxed
            else:
                # 科创板/高单价股: 资金够买最少一手即放行
                return True, ""
        if amount_fen > max_amount:
            return False, f"仓位超限: ¥{amount_fen/100:.0f} > 上限¥{max_amount/100:.0f}({max_pct*100:.0f}%), 现金不足以支撑"
        return True, ""

    # ===== Gate 5: 日内亏损熔断 =====
    def check_daily_loss(self) -> Tuple[bool, str]:
        db = self._get_db()
        today = date.today()
        daily_start = datetime.combine(today, datetime.min.time())
        today_trades = db.query(TradeLog).filter(TradeLog.traded_at >= daily_start).all()
        if not today_trades:
            return True, ""
        today_pnl = sum(t.pnl or 0 for t in today_trades)
        if today_pnl < 0:
            from app.models import SimAccount
            acc = db.query(SimAccount).first()
            initial = acc.initial_capital if acc else 10000000
            loss_pct = -today_pnl / initial
            if loss_pct > self.DAILY_LOSS_LIMIT_PCT:
                return False, f"日内亏损熔断: {loss_pct*100:.1f}% > {self.DAILY_LOSS_LIMIT_PCT*100:.0f}%"
        return True, ""

    # ===== Gate 6: 最大回撤熔断 =====
    def check_max_drawdown(self, current_total_fen: int) -> Tuple[bool, str]:
        db = self._get_db()
        from app.models import SimAccount
        acc = db.query(SimAccount).first()
        if not acc:
            return True, ""
        peak = max(acc.peak_value, current_total_fen)
        if peak > 0:
            dd = (peak - current_total_fen) / peak
            if dd > self.MAX_DRAWDOWN_PCT:
                return False, f"最大回撤熔断: {dd*100:.1f}% > {self.MAX_DRAWDOWN_PCT*100:.0f}%"
        return True, ""

    # ===== Gate 7: 交易频率 =====
    def check_trade_frequency(self, stock_code: str) -> Tuple[bool, str]:
        db = self._get_db()
        today = date.today()
        daily_start = datetime.combine(today, datetime.min.time())
        count = db.query(TradeLog).filter(
            TradeLog.stock_code == stock_code,
            TradeLog.traded_at >= daily_start
        ).count()
        if count >= self.MAX_DAILY_TRADES_PER_STOCK:
            return False, f"同股同日交易{count}次已达上限{self.MAX_DAILY_TRADES_PER_STOCK}"
        return True, ""

    # ===== Gate 8: 可用资金（动态计算现金底线） =====
    def check_cash_available(self, cash_fen: int, amount_fen: int) -> Tuple[bool, str]:
        """动态计算最低保留现金 = max(总资产30%, ¥500)
        修复: 固定¥5,000底线对小账户(如¥3,000总资产)永远无法满足。
        改为按比例保留，适配任意规模账户，避免系统自锁。
        """
        from app.database import SessionLocal
        from app.models import SimAccount
        db = SessionLocal()
        try:
            acc = db.query(SimAccount).first()
            total_equity = acc.total_value if acc else cash_fen + amount_fen
        finally:
            db.close()

        min_cash = max(
            int(total_equity * self.MIN_CASH_RESERVE_PCT),
            self.MIN_CASH_FEN_ABSOLUTE
        )
        if cash_fen - amount_fen < min_cash:
            remain = (cash_fen - amount_fen) / 100
            return False, f"剩余现金不足: 需保留>=¥{min_cash/100:.0f} (总资产{total_equity/100:.0f}的{self.MIN_CASH_RESERVE_PCT*100:.0f}%), 当前仅剩¥{remain:.0f}"
        return True, ""

    # ===== Gate 9: 板块集中度 =====
    def check_board_concentration(self, stock_code: str, amount_fen: int,
                                   total_equity_fen: int) -> Tuple[bool, str]:
        if not (stock_code.startswith("688") or stock_code.startswith("689")):
            return True, ""
        db = self._get_db()
        star_positions = db.query(Position).filter(
            (Position.stock_code.like("688%")) | (Position.stock_code.like("689%")),
            Position.quantity > 0
        ).all()
        star_value = sum(p.market_value for p in star_positions if p.market_value)
        new_total = star_value + amount_fen
        if total_equity_fen > 0 and new_total / total_equity_fen > self.MAX_STAR_BOARD_PCT:
            return False, f"科创板集中度超限: {(new_total/total_equity_fen)*100:.0f}% > {self.MAX_STAR_BOARD_PCT*100:.0f}%"
        return True, ""

    # ===== Pipeline 入口 =====
    # ===== Gate 10: 受托人检查 =====
    def check_fiduciary(self, stock_code: str, direction: str,
                         amount_fen: int, total_equity_fen: int) -> Tuple[bool, str]:
        """受托人责任检查：每分钱都当作自己父母的钱来管理"""
        if direction != "buy":
            return True, ""

        max_loss_fen = int(amount_fen * 0.10)
        if total_equity_fen > 0:
            loss_pct = max_loss_fen / total_equity_fen
            if loss_pct > self.MAX_LOSS_PER_TRADE_PCT:
                return False, (f"单笔风险超限: 最大约损失{max_loss_fen/100:.0f}元({loss_pct*100:.1f}%) "
                               f"> 上限{self.MAX_LOSS_PER_TRADE_PCT*100:.0f}%")
        return True, ""

    def pipeline_check(self, stock_code: str, direction: str,
                       price_fen: int, quantity: int,
                       cash_fen: int, total_equity_fen: int,
                       limit_up_fen: int = 0, limit_down_fen: int = 0) -> Tuple[bool, str]:
        """10-Gate 风控管道: 任一Gate失败则拒绝"""
        amount_fen = price_fen * quantity

        gates = [
            ("交易时间", self.check_trading_hours()),
            ("涨跌停", self.check_price_limit(stock_code, price_fen, limit_up_fen, limit_down_fen, direction)),
            ("T+1", self.check_t1(stock_code, direction)),
            ("仓位上限", self.check_position_limit(stock_code, amount_fen, cash_fen, total_equity_fen)),
            ("日内亏损", self.check_daily_loss()),
            ("最大回撤", self.check_max_drawdown(total_equity_fen)),
            ("交易频率", self.check_trade_frequency(stock_code)),
            ("可用资金", self.check_cash_available(cash_fen, amount_fen)) if direction == "buy" else ("可用资金", (True, "")),
            ("板块集中度", self.check_board_concentration(stock_code, amount_fen, total_equity_fen)),
            ("受托人检查", self.check_fiduciary(stock_code, direction, amount_fen, total_equity_fen)),
        ]

        for gate_name, (passed, reason) in gates:
            if not passed:
                logger.warning(f"风控拒绝 [{gate_name}]: {stock_code} {direction} {reason}")
                return False, f"[{gate_name}] {reason}"

        return True, ""

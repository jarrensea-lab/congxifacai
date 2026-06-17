"""模拟交易路由 — 信号/订单/账户/绩效"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.models import TradingSignal, TradingOrder, TradeLog, SimAccount, AIStrategy
from app.utils.logger import logger
from app.ai.cloud_client import cloud
from app.trading_engine.position import PositionManager
from app.trading_engine.fee_schedule import round_lot, get_board_type

# 从主应用注入的共享实例
account_mgr = None
sim_broker = None
signal_engine = None
risk_guard = None
order_mgr = None
perf_analyzer = None
tencent_client = None

router = APIRouter(prefix="/api/trading", tags=["模拟交易"])


class SignalAction(BaseModel):
    reason: str = ""


class ManualOrder(BaseModel):
    stock_code: str
    stock_name: str = ""
    direction: str  # "buy" or "sell"
    price: float    # 委托价格(元)
    quantity: int = 100  # 股数，默认100(1手)


def init_trading_router(am, sb, se, rg, om, pa, tc):
    """由 main.py 调用，注入共享实例"""
    global account_mgr, sim_broker, signal_engine, risk_guard, order_mgr, perf_analyzer, tencent_client
    account_mgr = am
    sim_broker = sb
    signal_engine = se
    risk_guard = rg
    order_mgr = om
    perf_analyzer = pa
    tencent_client = tc


@router.get("/holdings")
async def get_trading_holdings(db: Session = Depends(get_db)):
    """从 Position 表获取当前持仓（加权平均成本）"""
    return {"holdings": PositionManager.get_holdings_display(db)}


@router.get("/account")
async def trading_account():
    """获取账户 + Position表持仓市值"""
    db = SessionLocal()
    try:
        base = account_mgr.get_summary()
        positions = PositionManager.get_all(db)
        holdings_value = sum(p.market_value for p in positions if p.market_value)
        holdings_detail = [p.to_dict() for p in positions]

        if positions:
            codes = [p.stock_code for p in positions]
            batch = await tencent_client.fetch_batch(codes)
            for p in positions:
                rt = batch.get(p.stock_code, {})
                price = rt.get("price", 0) or 0
                if price > 0:
                    price_fen = int(price * 100) if price < 10000 else int(price)
                    p.market_price = price_fen
                    p.market_value = p.quantity * price_fen
                    p.unrealized_pnl = p.market_value - (p.avg_cost * p.quantity)
            holdings_value = sum(p.market_value for p in positions)

        real_total = (base["cash"] + base["frozen"]) * 100 + holdings_value
        real_pnl = real_total - base["initial_capital"] * 100
        base["total_value"] = round(real_total / 100, 2)
        base["holdings_value"] = round(holdings_value / 100, 2)
        base["daily_pnl"] = round(real_pnl / 100, 2)
        base["total_pnl"] = round(real_pnl / 100, 2)
        base["total_return_pct"] = round(real_pnl / base["initial_capital"] / 100 * 100, 2) if base["initial_capital"] else 0
        base["holdings_detail"] = holdings_detail
        return {"account": base}
    finally:
        db.close()


@router.post("/account/reset")
async def reset_account():
    account_mgr.reset_account()
    return {"message": "账户已重置", "account": account_mgr.get_summary()}


@router.get("/signals")
async def get_signals(status: str = "pending", limit: int = 50):
    db = SessionLocal()
    try:
        q = db.query(TradingSignal)
        if status != "all":
            q = q.filter(TradingSignal.status == status)
        signals = q.order_by(TradingSignal.created_at.desc()).limit(limit).all()
        result = []
        for s in signals:
            d = s.to_dict()
            if s.reason and "AI盘前推荐" in (s.reason or ""):
                ai_candidates = db.query(AIStrategy).filter(
                    AIStrategy.strategy_type == "premarket"
                ).order_by(AIStrategy.timestamp.desc()).limit(5).all()
                ai = None
                for c in ai_candidates:
                    if c.recommended_stocks:
                        recs = c.recommended_stocks or {}
                        all_recs = recs.get("short_term", []) + recs.get("mid_low_freq", [])
                        if len(all_recs) > 0:
                            ai = c
                            break
                if ai:
                    recs = ai.recommended_stocks or {}
                    all_recs = recs.get("short_term", []) + recs.get("mid_low_freq", [])
                    matched = [r for r in all_recs if str(r.get("code", "")) == s.stock_code]
                    if matched:
                        d["ai_context"] = {
                            "strategy_id": ai.id,
                            "strategy_type": ai.strategy_type,
                            "recommendation": matched[0],
                            "generated_at": ai.timestamp.isoformat() if ai.timestamp else None,
                        }
            result.append(d)
        return {"signals": result}
    finally:
        db.close()


@router.post("/signals/{signal_id}/approve")
async def approve_signal(signal_id: int, body: SignalAction = SignalAction()):
    s = signal_engine.approve_signal(signal_id)
    if not s:
        raise HTTPException(status_code=404, detail="信号不存在或已处理")
    order = order_mgr.create_from_signal(s)
    return {"message": "信号已批准", "signal": s.to_dict(),
            "order": order.to_dict() if order else None}


@router.post("/signals/{signal_id}/reject")
async def reject_signal(signal_id: int, body: SignalAction = SignalAction()):
    s = signal_engine.reject_signal(signal_id, body.reason)
    if not s:
        raise HTTPException(status_code=404, detail="信号不存在或已处理")
    return {"message": "信号已拒绝", "signal": s.to_dict()}


@router.get("/orders")
async def get_orders(status: str = "all", limit: int = 50):
    orders = order_mgr.get_orders(status, limit)
    return {"orders": [o.to_dict() for o in orders]}


@router.delete("/orders/{order_id}")
async def cancel_order(order_id: int):
    o = order_mgr.cancel_order(order_id)
    if not o:
        raise HTTPException(status_code=404, detail="订单不存在或已成交")
    return {"message": "订单已撤销", "order": o.to_dict()}


@router.post("/orders/manual")
async def create_manual_order(body: ManualOrder):
    """手动下单 — 直接创建成交订单（用于建仓和测试，跳过信号流程）"""
    db = SessionLocal()
    try:
        acc = db.query(SimAccount).first()
        if not acc:
            acc = SimAccount()
            db.add(acc)
            db.flush()

        price_fen = int(body.price * 100)
        quantity = round_lot(body.quantity, body.stock_code)
        if quantity <= 0:
            raise HTTPException(status_code=400, detail=f"数量需为100的整数倍，收到: {body.quantity}")

        market_value = PositionManager.get_total_market_value(db)
        total_equity = acc.cash + acc.frozen + market_value
        pos = PositionManager.get_one(db, body.stock_code)
        today_bought = pos.today_bought_qty if pos else 0

        # 卖出前检查持仓是否存在
        if body.direction == "sell" and (pos is None or pos.quantity <= 0):
            raise HTTPException(status_code=400, detail=f"无持仓可卖出: {body.stock_code}")

        ok, reason = risk_guard.pipeline_check(
            body.stock_code, body.direction, price_fen, quantity,
            acc.cash, total_equity
        )
        if not ok:
            raise HTTPException(status_code=400, detail=f"风控拒绝: {reason}")

        result = sim_broker.execute_market_order(
            body.stock_code, body.direction, price_fen, quantity,
            today_bought_qty=today_bought
        )
        if not result:
            raise HTTPException(status_code=400, detail="撮合失败(T+1/价格/数量)")

        total_cost = result["amount"] + result["fee"]
        fee_detail = result.get("fee_detail", {"commission": result["fee"], "stamp_tax": 0, "transfer": 0, "handling": 0, "regulatory": 0, "total": result["fee"]})

        order = TradingOrder(
            signal_id=None, stock_code=body.stock_code,
            stock_name=body.stock_name or body.stock_code,
            board_type=get_board_type(body.stock_code),
            direction=body.direction,
            order_type="market", quantity=quantity,
            status="submitted", submitted_at=datetime.now(),
        )
        db.add(order)
        db.flush()

        order.status = "filled"
        order.filled_price = result["filled_price"]
        order.filled_quantity = result["filled_quantity"]
        order.fee = result["fee"]
        order.fee_detail = fee_detail
        order.filled_at = datetime.now()

        if body.direction == "buy":
            acc.cash -= total_cost
            PositionManager.update_on_buy(
                db, body.stock_code, body.stock_name or body.stock_code,
                result["filled_price"], quantity, result["amount"]
            )
            pnl_fen = 0
        else:
            acc.cash += result["amount"] - result["fee"]
            if pos:
                pnl_fen = (result["filled_price"] - pos.avg_cost) * quantity - result["fee"]
            else:
                pnl_fen = 0
            PositionManager.update_on_sell(
                db, body.stock_code, body.stock_name or body.stock_code,
                result["filled_price"], quantity, pnl_fen
            )

        market_value_after = PositionManager.get_total_market_value(db)
        acc.total_value = acc.cash + acc.frozen + market_value_after
        account_mgr.recalculate_pnl()

        log_qty = quantity if body.direction == "buy" else -quantity
        log = TradeLog(
            order_id=order.id, stock_code=body.stock_code,
            stock_name=body.stock_name or body.stock_code,
            direction=body.direction,
            price=result["filled_price"],
            quantity=log_qty,
            amount=result["amount"], fee=result["fee"],
            pnl=pnl_fen if body.direction == "sell" else None,
            strategy_name="manual",
            signal_id=None,
        )
        db.add(log)
        db.commit()
        db.refresh(order)
        db.refresh(log)

        logger.info(f"手动成交: {body.direction} {body.stock_code} {quantity}股 @¥{result['filled_price']/100:.2f}")
        return {"message": "下单成功", "order": order.to_dict(), "trade_log": log.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"手动下单失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.get("/performance")
async def get_performance(period: str = "1m"):
    summary = perf_analyzer.get_summary()
    return {"summary": summary}


@router.get("/performance/curve")
async def get_equity_curve(days: int = 90):
    return {"curve": perf_analyzer.get_equity_curve(days)}


# ========== AI 代码生成 ==========

@router.post("/code/generate")
async def generate_trading_code(strategy_id: int | None = None):
    """触发 AI 生成交易策略代码"""
    db = SessionLocal()
    try:
        strategy = None
        if strategy_id:
            strategy = db.query(AIStrategy).filter(AIStrategy.id == strategy_id).first()
        else:
            strategy = db.query(AIStrategy).order_by(AIStrategy.timestamp.desc()).first()
        if not strategy:
            raise HTTPException(status_code=404, detail="无可用策略")

        code_prompt = f"""基于以下AI策略分析，生成可执行的Python量化交易代码。
代码应包含：策略类(Cerebro)、指标计算(MA/RSI/ATR/MACD)、买卖信号、风控逻辑(止损/仓位管理)。
必须包含:
1. class TrendStrategy(bt.Strategy): next()方法内的买入/卖出条件
2. 止损逻辑: bt.Order.Sell(exectype=bt.Order.StopLimit)
3. 仓位管理: 单票最大仓位比例、总仓位上限
4. 日志输出: 成交和信号记录
只输出纯Python代码，不要markdown fence，不要解释。

【策略内容】
{strategy.content[:3000]}
"""
        res = await cloud.chat("analyst", [
            {"role": "system", "content": "你是一位量化交易代码生成专家。只输出纯Python backtrader代码，不要markdown fence，不要任何解释。"},
            {"role": "user", "content": code_prompt}
        ], max_tokens=8192)
        code = res.get("content", "")
        if code:
            for fence in ["```python", "```"]:
                if code.startswith(fence):
                    code = code[len(fence):].lstrip()
                if code.endswith("```"):
                    code = code[:-3].rstrip()
            strategy.generated_code = code.strip()
            strategy.code_version = (strategy.code_version or 0) + 1
            strategy.code_status = "validated"
            db.commit()
            return {"message": "代码生成成功", "code": code, "version": strategy.code_version}
        return {"message": "代码生成失败", "error": "AI 返回空内容"}
    finally:
        db.close()


@router.get("/code")
async def get_trading_code(strategy_id: int | None = None):
    """获取最新生成的策略代码"""
    db = SessionLocal()
    try:
        strategy = None
        if strategy_id:
            strategy = db.query(AIStrategy).filter(AIStrategy.id == strategy_id).first()
        else:
            strategy = db.query(AIStrategy).filter(AIStrategy.generated_code != None).order_by(AIStrategy.timestamp.desc()).first()
        if not strategy or not strategy.generated_code:
            raise HTTPException(status_code=404, detail="无可用代码")
        return {
            "code": strategy.generated_code,
            "version": strategy.code_version,
            "status": strategy.code_status,
            "strategy_type": strategy.strategy_type,
            "timestamp": strategy.timestamp.isoformat() if strategy.timestamp else None,
        }
    finally:
        db.close()

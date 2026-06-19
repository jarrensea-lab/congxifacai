"""机器人指令处理 — 接收飞书 Bot 消息，解析持仓/交易指令并更新数据库"""
import re
import json
from datetime import datetime, date
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import SimAccount, Position, TradeLog
from app.trading_engine.position import PositionManager
from app.utils.logger import logger


def process_message(text: str) -> Dict[str, Any]:
    """解析用户消息，执行持仓/交易操作

    支持的指令格式:
    1. 持仓更新: "目前总资产XXXX，可用现金XXXX，[日期]，[操作描述...]"
    2. 买入: "买入XXXX(名称) XX股，成本X.XXX"
    3. 卖出: "卖出XXXX(名称) XX股，价格X.XXX"
    4. 清仓: "清仓XXXX(名称)"
    """
    db = SessionLocal()
    try:
        result = _process(db, text)
        db.commit()
        return result
    except Exception as e:
        db.rollback()
        logger.error(f"指令处理失败: {e}", exc_info=True)
        return {"ok": False, "error": str(e), "action": "error"}
    finally:
        db.close()


def _process(db: Session, text: str) -> Dict[str, Any]:
    text = text.strip()

    # 模式1: 全量持仓更新 "目前总资产XXXX，可用现金XXXX，[日期]，[交易描述]"
    total_match = re.search(r'总资产\s*(\d+\.?\d*)', text)
    cash_match = re.search(r'可用现金\s*(\d+\.?\d*)', text)
    
    if total_match and cash_match:
        total = float(total_match.group(1))
        cash = float(cash_match.group(1))
        return _update_account(db, total, cash)

    # 模式2: 买入 "买入XXXX(名称) XX股，成本X.XXX"
    buy_match = re.search(r'买入\s*(\d{6})\s*[（(]?([^）)]*?)[）)]?\s*(\d+)\s*股[,，\s]*成本\s*(\d+\.?\d*)', text)
    if buy_match:
        code = buy_match.group(1)
        name = buy_match.group(2).strip()
        if not name:
            # Try to look up name from existing positions
            pos = db.query(Position).filter(Position.stock_code == code).first()
            name = pos.stock_name if pos else code
        qty = int(buy_match.group(3))
        cost = float(buy_match.group(4))
        return _execute_buy(db, code, name, qty, cost)

    # 模式3: 卖出 "卖出XXXX(名称) XX股，价格X.XXX"
    sell_match = re.search(r'卖出\s*(\d{6})\s*[（(]([^）)]+)[）)]?\s*(\d+)\s*股[,，]\s*价格\s*(\d+\.?\d*)', text)
    if sell_match:
        code = sell_match.group(1)
        name = sell_match.group(2).strip()
        qty = int(sell_match.group(3))
        price = float(sell_match.group(4))
        return _execute_sell(db, code, name, qty, price)

    # 模式4: 清仓
    clear_match = re.search(r'清仓\s*(\d{6})\s*[（(]?([^）)]*)[）)]?', text)
    if clear_match:
        code = clear_match.group(1)
        name = clear_match.group(2).strip()
        return _execute_clear(db, code, name)

        # 模式5: 持仓查询/更新
    if re.search(r'持仓', text):
        return _query_holdings(db)

    # 模式6: 生成策略
    if re.search(r'生成策略|策略生成|分析', text):
        return _generate_strategy(db)

    return {"ok": False, "error": "无法识别的指令格式", "action": "parse_error"}


def _query_holdings(db: Session) -> Dict[str, Any]:
    positions = db.query(Position).filter(Position.quantity > 0).all()
    acc = db.query(SimAccount).first()
    if not positions:
        return {"ok": True, "action": "holdings", "positions": [], "total": acc.total_value/100 if acc else 0, "cash": acc.cash/100 if acc else 0}
    result = {"ok": True, "action": "holdings", "positions": [], "total": acc.total_value/100 if acc else 0, "cash": acc.cash/100 if acc else 0}
    for p in positions:
        result["positions"].append({
            "code": p.stock_code, "name": p.stock_name,
            "qty": p.quantity, "cost": round(p.avg_cost/100, 3),
            "price": round(p.market_price/100, 3),
            "pnl": round(p.unrealized_pnl/100, 2),
        })
    return result

def _generate_strategy(db: Session) -> Dict[str, Any]:
    """触发AI策略生成并返回结果"""
    import asyncio
    import time
    from app.ai.debate import AIDebateEngine
    from app.models import Position, SimAccount
    
    pos = db.query(Position).filter(Position.quantity > 0).all()
    acc = db.query(SimAccount).first()
    hd = '\n'.join([f'{p.stock_name}({p.stock_code}): {p.quantity}股 成本¥{p.avg_cost/100:.2f}' for p in pos]) or '空仓'
    cash = acc.cash/100 if acc else 0
    
    engine = AIDebateEngine()
    t0 = time.time()
    result = asyncio.run(engine.debate(
        market_data='A股市场实时概况',
        holdings_data=hd,
        overall_timeout=180
    ))
    elapsed = time.time() - t0
    
    final = result.get('final', {})
    decision = final.get('final_decision', 'N/A')
    confidence = final.get('confidence', 0)
    reasoning = final.get('reasoning', '')[:500]
    
    # Upload to base
    try:
        recs = []
        st = final.get('short_term', {})
        for r in st.get('recommendations', [])[:3]:
            recs.append({
                'code': r.get('code',''), 'name': r.get('name',''),
                'direction': '买入', 'type': '短线(1-5天)',
                'buy_range': r.get('buy_range',''), 'target': r.get('target',''),
                'stop_loss': r.get('stop_loss',''), 'reason': r.get('reason',''),
                'level': r.get('level','中'), 'source': 'DeepSeek+R1辩论引擎'
            })
        if recs:
            import subprocess
            import json as j
            from app.config import get_settings
            s = get_settings()
            rows = [[r['code'],r['name'],r['direction'],r['type'],
                     r['buy_range'],r['target'],r['stop_loss'],
                     '🔥高(8-10)' if confidence>=8 else '✅中(5-7)',
                     r['reason'],r['source']] for r in recs]
            subprocess.run(['lark-cli','--profile','gongxifacai','base','+record-batch-create',
                '--base-token','ObTFbBmVMauqE2sBS9ccopvHnme',
                '--table-id','tblvCHEQHaDXnibg','--as','user',
                '--json',j.dumps({'fields':['股票代码','股票名称','推荐方向','策略类型','买入区间(元)','目标价(元)','止损价(元)','信心等级','推荐理由','数据来源'],'rows':rows})],
                capture_output=True, timeout=30)
    except:
        pass
    
    return {
        "ok": True, "action": "strategy",
        "decision": decision, "confidence": confidence,
        "elapsed": round(elapsed, 0),
        "reasoning": reasoning,
        "cash": round(cash, 2),
        "holdings": hd,
    }

def _update_account(db: Session, total: float, cash: float) -> Dict[str, Any]:
    acc = db.query(SimAccount).first()
    if not acc:
        acc = SimAccount(initial_capital=int(total * 100))
        db.add(acc)
        db.flush()

    old_total = acc.total_value / 100
    acc.cash = int(cash * 100)
    acc.total_value = int(total * 100)
    daily_change = (total - old_total)
    acc.total_pnl = acc.total_value - acc.initial_capital
    if acc.total_value > acc.peak_value:
        acc.peak_value = acc.total_value
    acc.updated_at = datetime.now()

    logger.info(f"账户更新: 总资产 ¥{total:.2f} 现金 ¥{cash:.2f} 变动 ¥{daily_change:+.2f}")
    return {
        "ok": True, "action": "account_updated",
        "total": total, "cash": cash, "change": round(daily_change, 2),
    }


def _execute_buy(db: Session, code: str, name: str, qty: int, cost: float) -> Dict[str, Any]:
    cost_fen = int(cost * 100)
    amount_fen = cost_fen * qty

    pos = PositionManager.get_or_create(db, code, name)
    today_str = date.today().isoformat()

    if pos.today_bought_date != today_str:
        pos.today_bought_qty = 0
        pos.today_bought_date = today_str

    # 更新持仓
    old_total = pos.total_buy_amount
    old_qty = pos.total_buy_qty
    pos.total_buy_amount = old_total + amount_fen
    pos.total_buy_qty = old_qty + qty
    pos.avg_cost = round(pos.total_buy_amount / pos.total_buy_qty) if pos.total_buy_qty > 0 else 0
    pos.quantity += qty
    pos.market_price = cost_fen
    pos.market_value = pos.quantity * cost_fen
    pos.today_bought_qty += qty
    pos.today_bought_date = today_str
    if not pos.open_date:
        pos.open_date = datetime.now()
    pos.updated_at = datetime.now()

    # 扣除现金
    acc = db.query(SimAccount).first()
    if acc:
        acc.cash -= amount_fen
        acc.updated_at = datetime.now()

    # 交易日志
    log = TradeLog(
        order_id=0, stock_code=code, stock_name=name,
        direction='buy', price=cost_fen, quantity=qty,
        amount=amount_fen, fee=0,
        strategy_name='manual', traded_at=datetime.now()
    )
    db.add(log)

    logger.info(f"机器人指令-BUY: {name}({code}) {qty}股 @¥{cost:.3f} 金额¥{amount_fen/100:.2f}")
    return {"ok": True, "action": "buy", "code": code, "name": name, "qty": qty, "cost": cost}


def _execute_sell(db: Session, code: str, name: str, qty: int, price: float) -> Dict[str, Any]:
    price_fen = int(price * 100)
    pos = db.query(Position).filter(Position.stock_code == code).first()
    if not pos or pos.quantity <= 0:
        return {"ok": False, "error": f"{code} 无持仓"}

    sell_qty = min(qty, pos.quantity)
    amount_fen = price_fen * sell_qty
    pnl_fen = (price_fen - pos.avg_cost) * sell_qty

    pos.quantity -= sell_qty
    pos.realized_pnl = (pos.realized_pnl or 0) + pnl_fen
    pos.market_price = price_fen
    pos.market_value = pos.quantity * price_fen

    if pos.quantity == 0:
        pos.unrealized_pnl = 0
        pos.avg_cost = 0
        pos.total_buy_amount = 0
        pos.total_buy_qty = 0
        pos.today_bought_qty = 0
    else:
        pos.unrealized_pnl = pos.market_value - (pos.avg_cost * pos.quantity)

    pos.updated_at = datetime.now()

    # 增加现金
    acc = db.query(SimAccount).first()
    if acc:
        acc.cash += amount_fen
        acc.updated_at = datetime.now()

    log = TradeLog(
        order_id=0, stock_code=code, stock_name=name,
        direction='sell', price=price_fen, quantity=-sell_qty,
        amount=amount_fen, fee=0,
        pnl=pnl_fen, strategy_name='manual', traded_at=datetime.now()
    )
    db.add(log)

    logger.info(f"机器人指令-SELL: {name}({code}) {sell_qty}股 @¥{price:.2f} PnL=¥{pnl_fen/100:.2f}")
    return {"ok": True, "action": "sell", "code": code, "name": name, "qty": sell_qty, "price": price, "pnl": round(pnl_fen/100, 2)}


def _execute_clear(db: Session, code: str, name: str) -> Dict[str, Any]:
    pos = db.query(Position).filter(Position.stock_code == code).first()
    if not pos or pos.quantity <= 0:
        return {"ok": False, "error": f"{code} 无持仓"}

    qty = pos.quantity
    price = pos.market_price or pos.avg_cost
    return _execute_sell(db, code, name or pos.stock_name, qty, price / 100)


def check_and_process_new_messages() -> Optional[Dict]:
    """检查飞书桥接是否有新消息，有则处理（带用户鉴权）"""
    import subprocess
    from app.config import get_settings

    s = get_settings()
    import os as _os
    bridge = _os.path.join(s.FEISHU_BRIDGE_PATH, "check_inbox.py")

    if not _os.path.exists(bridge):
        return None

    # 加载授权用户白名单
    allowed_users = json.loads(s.FEISHU_ALLOWED_USERS) if s.FEISHU_ALLOWED_USERS else []
    if not allowed_users:
        return None

    try:
        result = subprocess.run(
            ["python3", bridge, "list"],
            capture_output=True, text=True, timeout=10
        )
        data = json.loads(result.stdout) if result.stdout.strip() else {}
        messages = data.get("messages", [])
    except Exception:
        return None

    for msg in messages:
        text = msg.get("text", "")
        msg_id = msg.get("id", "")
        chat_id = msg.get("chat_id", "")
        sender = msg.get("sender", "")

        if not text or not msg_id:
            continue

        # 鉴权检查
        if sender not in allowed_users:
            try:
                subprocess.run(
                    ["python3", bridge, "reply", chat_id or "default",
                     "❌ 未授权的用户，无法执行交易指令"],
                    capture_output=True, timeout=10
                )
                subprocess.run(
                    ["python3", bridge, "process", msg_id],
                    capture_output=True, timeout=5
                )
            except Exception:
                pass
            continue

        result = process_message(text)

        # 回复确认
        if result.get("ok"):
            reply = _format_reply(result)
        else:
            reply = f"❌ 指令处理失败: {result.get('error', '未知错误')}"
            if result.get("action") == "parse_error":
                reply += "\n支持: 买入/卖出/清仓/持仓更新"

        try:
            subprocess.run(
                ["python3", bridge, "reply", chat_id or "default", reply],
                capture_output=True, timeout=10
            )
            subprocess.run(
                ["python3", bridge, "process", msg_id],
                capture_output=True, timeout=5
            )
        except Exception:
            pass

    return None


def _format_reply(result: Dict) -> str:
    action = result.get("action", "")
    if action == "account_updated":
        return f"✅ 账户已更新: 总资产 ¥{result['total']:.2f} 现金 ¥{result['cash']:.2f}"
    elif action == "buy":
        return f"✅ 买入: {result['name']}({result['code']}) {result['qty']}股 @¥{result['cost']:.3f}"
    elif action == "sell":
        return f"✅ 卖出: {result['name']}({result['code']}) {result['qty']}股 @¥{result['price']:.2f} PnL=¥{result.get('pnl',0):.2f}"
    return "✅ 操作完成"


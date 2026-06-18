"""⑥ 每日审查引擎 — 纯规则检查，预警和修正"""
from datetime import date, timedelta
from app.database import SessionLocal
from app.models import StrategyInstance, ReviewLog, TradeLog
from sqlalchemy import func


def run_daily_review(strategy_instance_id: int = None) -> dict:
    """执行每日审查，检查操作是否违反策略约束。

    Returns:
        {"result": "pass|yellow|red|breaker", "violations": [...], "review_log_id": int}
    """
    db = SessionLocal()
    try:
        # 获取当前策略
        if strategy_instance_id:
            strategy = db.query(StrategyInstance).get(strategy_instance_id)
        else:
            strategy = (
                db.query(StrategyInstance)
                .filter(StrategyInstance.status.in_(["confirmed", "planned", "executing"]))
                .order_by(StrategyInstance.created_at.desc())
                .first()
            )

        if not strategy:
            return {"result": "pass", "violations": [], "message": "无活跃策略"}

        violations = []

        # 检查 1: 交易频率
        today_trades = (
            db.query(TradeLog)
            .filter(
                TradeLog.strategy_instance_id == strategy.id,
                func.date(TradeLog.traded_at) == date.today(),
            )
            .all()
        )

        trade_count = len(today_trades)
        if trade_count > 10:
            violations.append({
                "rule": "操作频率过高",
                "detail": f"今日已执行 {trade_count} 笔操作，超过阈值 10 笔",
                "severity": "yellow",
            })
        if trade_count > 20:
            violations.append({
                "rule": "操作频率严重过高",
                "detail": f"今日已执行 {trade_count} 笔操作，可能情绪化交易",
                "severity": "red",
            })

        # 检查 2: 是否在标的池外交易
        pool_codes = {s["code"] for s in (strategy.stock_pool or []) if isinstance(s, dict) and "code" in s}
        for trade in today_trades:
            if pool_codes and trade.stock_code not in pool_codes:
                violations.append({
                    "rule": "标的池外交易",
                    "detail": f"交易 {trade.stock_code} {trade.stock_name} 不在当前策略标的池",
                    "severity": "yellow",
                })

        # 判定结果
        has_red = any(v["severity"] == "red" for v in violations)
        has_yellow = any(v["severity"] == "yellow" for v in violations)

        # 检查历史红牌 (连续2日红牌 → 熔断)
        if has_red:
            yesterday = date.today() - timedelta(days=1)
            yesterday_red = (
                db.query(ReviewLog)
                .filter(
                    ReviewLog.strategy_instance_id == strategy.id,
                    ReviewLog.review_date == yesterday,
                    ReviewLog.result == "red",
                )
                .first()
            )
            if yesterday_red:
                result = "breaker"
            else:
                result = "red"
        elif has_yellow:
            result = "yellow"
        else:
            result = "pass"

        # 写入审查日志
        review_log = ReviewLog(
            strategy_instance_id=strategy.id,
            review_date=date.today(),
            result=result,
            violations=violations,
        )
        db.add(review_log)
        db.commit()
        db.refresh(review_log)

        return {
            "result": result,
            "violations": violations,
            "review_log_id": review_log.id,
        }
    finally:
        db.close()

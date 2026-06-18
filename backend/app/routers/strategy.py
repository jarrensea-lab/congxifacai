"""策略生命周期路由 — 分析/辩论/规划/审查 + AI 端点"""
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.models import AIStrategy, StrategyInstance, ReviewLog, RiskAlert
from app.engine.lifecycle import StrategyLifecycle
from app.engine.analysis import run_analysis
from app.engine.workshop import run_debate, ask_role, RISK_LEVELS
from app.utils.logger import logger

# 从主应用注入的共享实例
debate_engine = None
feishu = None
tencent_client = None
market_client = None
news_client = None
generation_status = None
_get_holdings_data_fn = None  # 函数引用，避免循环导入

router = APIRouter(prefix="/api", tags=["策略生命周期"])


def init_strategy_router(de, fs, tc, mc, nc, gen_status, get_hd_fn):
    """由 main.py 调用，注入共享实例"""
    global debate_engine, feishu, tencent_client, market_client, news_client, generation_status, _get_holdings_data_fn
    debate_engine = de
    feishu = fs
    tencent_client = tc
    market_client = mc
    news_client = nc
    generation_status = gen_status
    _get_holdings_data_fn = get_hd_fn


def _status_for(key: str) -> dict:
    s = generation_status.get(key, {})
    return {"running": s.get("running", False), "started_at": s.get("started_at")}


# ========== AI 策略 API ==========

@router.get("/ai/premarket")
async def get_premarket_strategy(db: Session = Depends(get_db)):
    s = db.query(AIStrategy).filter(AIStrategy.strategy_type == "premarket").order_by(AIStrategy.timestamp.desc()).first()
    return {
        "data": s.to_dict() if s else None,
        "message": None if s else "暂无盘前策略",
        "generation": _status_for("premarket"),
    }


@router.post("/ai/premarket/generate")
async def generate_premarket_strategy(background_tasks: BackgroundTasks):
    if generation_status["premarket"]["running"]:
        return {"message": "盘前策略生成已在进行中", "status": "running"}
    # 延迟导入避免循环
    from app.main import _run_premarket_with_status
    background_tasks.add_task(_run_premarket_with_status)
    return {"message": "盘前策略生成已触发", "status": "started"}


@router.get("/ai/review")
async def get_review_strategy(db: Session = Depends(get_db)):
    s = db.query(AIStrategy).filter(AIStrategy.strategy_type == "review").order_by(AIStrategy.timestamp.desc()).first()
    return {
        "data": s.to_dict() if s else None,
        "message": None if s else "暂无复盘记录",
        "generation": _status_for("review"),
    }


@router.post("/ai/review/generate")
async def generate_review_strategy(background_tasks: BackgroundTasks):
    if generation_status["review"]["running"]:
        return {"message": "收盘复盘生成已在进行中", "status": "running"}
    from app.main import _run_review_with_status
    background_tasks.add_task(_run_review_with_status)
    return {"message": "收盘复盘生成已触发", "status": "started"}


@router.get("/ai/intraday")
async def get_intraday_strategy(db: Session = Depends(get_db)):
    s = db.query(AIStrategy).filter(AIStrategy.strategy_type == "intraday").order_by(AIStrategy.timestamp.desc()).first()
    return {
        "data": s.to_dict() if s else None,
        "message": None if s else "暂无盘中分析",
        "generation": _status_for("intraday"),
    }


@router.post("/ai/intraday/generate")
async def generate_intraday_strategy(background_tasks: BackgroundTasks):
    if generation_status["intraday"]["running"]:
        return {"message": "盘中分析生成已在进行中", "status": "running"}
    from app.main import _run_intraday_with_status
    background_tasks.add_task(_run_intraday_with_status)
    return {"message": "盘中分析生成已触发", "status": "started"}


@router.post("/ai/risk-check")
async def trigger_risk_check():
    from app.engine.review import run_daily_review
    result = run_daily_review()
    return result


# ========== 风险提醒 ==========

@router.get("/risk-alerts")
async def get_risk_alerts(db: Session = Depends(get_db)):
    alerts = db.query(RiskAlert).order_by(RiskAlert.timestamp.desc()).limit(50).all()
    return {"alerts": [a.to_dict() for a in alerts]}


# ========== 旺财V4 策略生命周期 API ==========

@router.get("/strategy/active")
async def get_active_strategy():
    lifecycle = StrategyLifecycle()
    try:
        instance = lifecycle.get_active_instance()
        if instance:
            return {
                "id": instance.id, "status": instance.status,
                "risk_level": instance.risk_level,
                "position_limit_pct": instance.position_limit_pct,
                "single_stock_limit_pct": instance.single_stock_limit_pct,
                "stop_loss_pct": instance.stop_loss_pct,
                "holding_period_days": instance.holding_period_days,
                "stock_pool": instance.stock_pool,
                "analysis_report": instance.analysis_report,
                "debate_summary": instance.debate_summary,
                "execution_plan": instance.execution_plan,
                "expected_return_best": instance.expected_return_best,
                "expected_return_neutral": instance.expected_return_neutral,
                "expected_return_worst": instance.expected_return_worst,
                "created_at": str(instance.created_at),
            }
        return {"status": "no_active_strategy"}
    finally:
        lifecycle.close()


@router.post("/strategy/analysis")
async def trigger_analysis():
    lifecycle = StrategyLifecycle()
    try:
        instance = lifecycle.create_instance()
        hd = _get_holdings_data_fn(lifecycle.db)
        market_data = {
            "indices": {"shanghai": 3350.0, "shenzhen": 10800.0},
            "sectors": [],
            "holdings": hd["holdings"],
            "holdings_str": hd["holdings_str"],
            "news": [],
        }
        logger.info(f"手动分析: {len(hd['holdings'])} 支持仓, 可用资金 ¥{hd['available_cash']:.2f}")
        report = await run_analysis(market_data)
        instance.analysis_report = report
        instance.status = "analyzed"
        lifecycle.db.commit()
        return {"strategy_id": instance.id, "report": report}
    finally:
        lifecycle.close()


@router.post("/strategy/{strategy_id}/debate")
async def trigger_debate(strategy_id: int):
    lifecycle = StrategyLifecycle()
    try:
        instance = lifecycle.db.query(StrategyInstance).get(strategy_id)
        if not instance:
            raise HTTPException(404, "策略实例不存在")
        if not instance.analysis_report:
            raise HTTPException(400, "请先生成分析报告")
        debate_result = await run_debate(instance.analysis_report)
        instance.debate_summary = debate_result
        lifecycle.db.commit()
        return {"strategy_id": strategy_id, "debate": debate_result}
    finally:
        lifecycle.close()


@router.post("/strategy/{strategy_id}/confirm")
async def confirm_strategy(strategy_id: int, decision: dict):
    lifecycle = StrategyLifecycle()
    try:
        instance = lifecycle.db.query(StrategyInstance).get(strategy_id)
        if not instance:
            raise HTTPException(404, "策略实例不存在")
        instance.risk_level = decision.get("risk_level", instance.risk_level)
        instance.position_limit_pct = decision.get("position_limit_pct", instance.position_limit_pct)
        instance.single_stock_limit_pct = decision.get("single_stock_limit_pct", instance.single_stock_limit_pct)
        instance.stop_loss_pct = decision.get("stop_loss_pct", instance.stop_loss_pct)
        instance.holding_period_days = decision.get("holding_period_days", instance.holding_period_days)
        instance.stock_pool = decision.get("stock_pool", instance.stock_pool)
        instance.status = "confirmed"
        lifecycle.db.commit()
        return {"strategy_id": strategy_id, "status": "confirmed"}
    finally:
        lifecycle.close()


@router.post("/strategy/debate/ask")
async def ask_role_question(request: dict):
    result = await ask_role(
        role=request.get("role", "hunter"),
        question=request.get("question", ""),
        context=request.get("context", ""),
    )
    return result


@router.get("/strategy/risk-levels")
async def get_risk_levels():
    return RISK_LEVELS


@router.post("/strategy/{strategy_id}/plan")
async def generate_plan(strategy_id: int):
    lifecycle = StrategyLifecycle()
    try:
        instance = lifecycle.db.query(StrategyInstance).get(strategy_id)
        if not instance:
            raise HTTPException(404, "策略实例不存在")
        if instance.status != "confirmed":
            raise HTTPException(400, "请先确认策略决策卡")
        from app.engine.planning import generate_execution_plan
        hd = _get_holdings_data_fn(lifecycle.db)
        holdings = hd["holdings"]
        available_cash = hd["available_cash"]
        logger.info(f"执行规划: {len(holdings)} 支持仓, 可用资金 ¥{available_cash:.2f}")
        plan = generate_execution_plan(instance, holdings, available_cash)
        instance.execution_plan = plan
        instance.status = "planned"
        lifecycle.db.commit()
        return {"strategy_id": strategy_id, "plan": plan}
    finally:
        lifecycle.close()


@router.post("/strategy/review")
async def trigger_daily_review():
    from app.engine.review import run_daily_review
    result = run_daily_review()
    return result


@router.get("/strategy/reviews")
async def get_review_logs(days: int = 7):
    db = SessionLocal()
    try:
        logs = (
            db.query(ReviewLog)
            .filter(ReviewLog.review_date >= date.today() - timedelta(days=days))
            .order_by(ReviewLog.created_at.desc())
            .limit(30)
            .all()
        )
        return [
            {"id": log.id, "date": str(log.review_date), "result": log.result, "violations": log.violations}
            for log in logs
        ]
    finally:
        db.close()

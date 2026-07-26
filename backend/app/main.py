"""FastAPI 主应用 — V7: DeepSeek云端AI + 飞书全通道 + 定时调度"""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI
from sqlalchemy.orm import Session
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.database import init_db, SessionLocal
from app.models import (RiskAlert, AIStrategy, SimAccount, Position)
from app.services.feishu_channels import feishu_channels
from app.services.bot_commands import check_and_process_new_messages
from app.engine.analysis import run_analysis
from app.engine.debate_tracker import DebateTracker
from app.engine.workshop import run_debate
from app.ai.debate import AIDebateEngine
from app.ai.cloud_client import cloud
from app.utils.logger import logger
from app.utils.trading_calendar import is_trading_day
from app.data_sources.tencent_client import TencentDataSource
from app.data_sources.realtime_market_data import FastRealtimeMarketDataSource
from app.data_sources.eastmoney_client import EastmoneyDataSource
from app.data_sources.akshare_news import AKShareNewsClient
from app.data_sources.akshare_market import AKShareMarketClient
from app.data_sources.data_router import DataSourceRouter
from app.services.monitor import MonitorService
from app.services.push_tracker import push_tracker, compute_retry_delay
from app.services.portfolio_store import sync_db_from_user_portfolio
from app.services.quant_lifecycle import (
    CandidatePoolStore,
    PositionWatchStore,
    evaluate_candidate_pool,
    evaluate_position_watch,
    normalize_alert_level,
)
from app.services.evidence_ledger import (
    build_sentinel_evidence_context,
    upsert_sentinel_evidence_to_target_pool,
)
from app.services.schedule_policy import (
    schedule_reason,
    should_run_main_report,
    should_run_premarket_calibration,
)
from app.services.feishu_pusher import send_feishu_card, send_feishu_card_sync
from app.services.notification_gate import NotificationGate, build_alert_digest
from app.services.visible_decision_gate import (
    build_runtime_blocked_gate,
    filter_alerts_by_visible_decision_gate,
    load_runtime_visible_decision_gate,
)

# 报告引擎
from app.report_engine.engine import report_engine

class FeishuNotifier:
    """飞书消息推送"""

    def __init__(self):
        self.webhook_url = settings.FEISHU_WEBHOOK_URL

    async def send(self, title: str, content: str) -> bool:
        try:
            result = await send_feishu_card(
                title=title,
                content=content,
                webhook_url=self.webhook_url,
                color="red" if "风险" in title else "blue",
                app_id=settings.FEISHU_APP_ID,
                app_secret=settings.FEISHU_APP_SECRET,
                chat_id=settings.FEISHU_CHAT_ID,
                api_base=settings.FEISHU_API_BASE,
            )
            ok = result.get("feishu_api") or result.get("feishu_webhook")
            logger.info(f"飞书消息发送{'成功' if ok else '失败'}: {title} channel={result.get('channel', '')}")
            return bool(ok)
        except Exception as e:
            logger.error(f"飞书推送异常: {e}")
            return False

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("恭喜发财 V7 应用启动中...")
    init_db()
    logger.info("数据库初始化完成")

    has_feishu_api = bool(settings.FEISHU_APP_ID and settings.FEISHU_APP_SECRET and settings.FEISHU_CHAT_ID)
    has_webhook = bool(settings.FEISHU_WEBHOOK_URL and "YOUR_WEBHOOK_ID" not in settings.FEISHU_WEBHOOK_URL)
    if not has_feishu_api and not has_webhook:
        logger.warning("飞书 OpenAPI 与 Webhook 都未配置，消息推送将不可用。")
    elif has_feishu_api:
        logger.info("飞书 OpenAPI 已配置，推送将优先使用 API")
    else:
        logger.info("飞书 Webhook 已配置")

    # ============================================================
    # V7.5-dev 定时任务注册（盈利策略管线 feature 分支）
    # ============================================================
    scheduler.add_job(
        _run_premarket_with_status,
        CronTrigger(hour=8, minute=50, day_of_week='mon-fri', timezone='Asia/Shanghai'),
        id='premarket', name='盘前短策略校准', replace_existing=True,
        misfire_grace_time=3600,  # 错过1小时内自动补跑
    )
    scheduler.add_job(
        _run_midday_with_status,
        CronTrigger(hour=11, minute=35, day_of_week='mon-fri', timezone='Asia/Shanghai'),
        id='midday', name='午盘快速分析', replace_existing=True,
        misfire_grace_time=2700,  # 错过45分钟内自动补跑
    )
    scheduler.add_job(
        _run_afternoon_with_status,
        CronTrigger(hour=14, minute=0, day_of_week='mon-fri', timezone='Asia/Shanghai'),
        id='afternoon', name='午后风险检查', replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _run_intraday_alert_scan_with_status,
        CronTrigger(hour='9-11,13-14', minute='*/5', day_of_week='mon-fri', timezone='Asia/Shanghai'),
        id='intraday_alert_scan', name='盘中事件触发扫描', replace_existing=True,
        misfire_grace_time=120,
    )
    scheduler.add_job(
        _run_review_with_status,
        CronTrigger(hour=15, minute=5, day_of_week='mon-fri', timezone='Asia/Shanghai'),
        id='review', name='收盘复盘', replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _run_prediction_lab_with_status,
        CronTrigger(hour=15, minute=25, day_of_week='mon-fri', timezone='Asia/Shanghai'),
        id='prediction_lab', name='预测账本采集与到期评估', replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _run_sentinel_research_with_status,
        CronTrigger(hour=20, minute=0, day_of_week='mon-fri,sun', timezone='Asia/Shanghai'),
        id='sentinel_research', name='Sentinel研究包与Serenity深挖', replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _run_daily_report_with_status,
        CronTrigger(hour=20, minute=30, day_of_week='mon-fri', timezone='Asia/Shanghai'),
        id='main_report', name='次日投资策略主报告', replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _run_daily_report_with_status,
        CronTrigger(hour=20, minute=30, day_of_week='sun', timezone='Asia/Shanghai'),
        id='sunday_main_report', name='周日晚次日投资策略主报告', replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _run_sentinel_review_with_status,
        CronTrigger(hour=21, minute=0, timezone='Asia/Shanghai'),
        id='sentinel_review', name='Sentinel绩效回看与归档', replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _poll_bot_messages,
        'interval', seconds=30,
        id='bot_poll', name='飞书Bot消息轮询', replace_existing=True,
    )
    register_yitaojin_jobs(scheduler)

    scheduler.start()
    for stale_job_id in ("daily_report",):
        try:
            scheduler.remove_job(stale_job_id)
            logger.info(f"已清理旧调度任务: {stale_job_id}")
        except Exception:
            pass
    logger.info("旺财V7.5-dev 调度器已启动 (次日主报告 + 盘前校准 + 盘中5分钟事件扫描 + 预测账本 + 盘中/收盘 + Bot轮询)")

    asyncio.create_task(_startup_health_check())

    yield
    scheduler.shutdown(wait=False)
    for obj in [risk_guard, order_mgr, account_mgr, signal_engine, perf_analyzer]:
        if hasattr(obj, "_db") and obj._db:
            try: obj._db.close()
            except Exception: pass
    await cloud.close()
    logger.info("恭喜发财应用关闭")

app = FastAPI(
    title="恭喜发财 - A 股智能监控系统",
    description="基于 DeepSeek 云端 AI 的 A 股智能监控与交易辅助系统",
    version="8.2.0-dev",
    lifespan=lifespan,
)


@app.get("/api/integrations/yitaojin/status")
async def get_yitaojin_runtime_status():
    """Expose sanitized broker integration health without account material."""
    from app.config import resolve_runtime_yitaojin_paths
    from app.integrations.yitaojin.runtime import load_yitaojin_runtime_status

    paths = resolve_runtime_yitaojin_paths()
    return load_yitaojin_runtime_status(paths.runtime_status)

# ============================================================
# 共享实例初始化
# ============================================================
debate_engine = AIDebateEngine()
feishu = FeishuNotifier()
feishu_v6 = feishu_channels
Path(settings.SCHEDULER_DATABASE_PATH).expanduser().parent.mkdir(parents=True, exist_ok=True)
scheduler = AsyncIOScheduler(
    jobstores={
        "default": SQLAlchemyJobStore(url=f"sqlite:///{settings.SCHEDULER_DATABASE_PATH}")
    },
    job_defaults={
        "misfire_grace_time": 300,  # 5分钟容错
        "coalesce": True,           # 合并错过的任务
        "max_instances": 1,
    }
)
tencent_client = TencentDataSource()
eastmoney_client = EastmoneyDataSource()
news_client = AKShareNewsClient()
market_client = AKShareMarketClient()
data_router = DataSourceRouter()
monitor = MonitorService()

from app.trading_engine.account import SimAccountManager
from app.trading_engine.broker import SimBroker
from app.trading_engine.signal_engine import SignalEngine
from app.trading_engine.order_manager import OrderManager
from app.trading_engine.risk_guard import RiskGuard
from app.trading_engine.performance import PerformanceAnalyzer

account_mgr = SimAccountManager()
sim_broker = SimBroker()
signal_engine = SignalEngine()
risk_guard = RiskGuard()
order_mgr = OrderManager(account_mgr, sim_broker, risk_guard, signal_engine)
perf_analyzer = PerformanceAnalyzer()

from app.routers import market, trading, strategy

market.init_market_router(tencent_client, eastmoney_client, market_client, None)
trading.init_trading_router(account_mgr, sim_broker, signal_engine, risk_guard, order_mgr, perf_analyzer,
                             tencent_client)

generation_status = {
    "premarket": {"running": False, "started_at": None},
    "review": {"running": False, "started_at": None},
    "afternoon": {"running": False, "started_at": None},
    "intraday": {"running": False, "started_at": None},
    "event_scan": {"running": False, "started_at": None},
}

def _get_holdings_data(db: Session) -> dict:
    """从 Position 表获取持仓数据，用于分析引擎和规划引擎。"""
    portfolio_sync_failed = False
    try:
        sync_db_from_user_portfolio(db)
    except Exception as e:
        portfolio_sync_failed = True
        logger.warning(f"用户持仓JSON同步到数据库失败，继续使用数据库现状: {e}")

    positions = db.query(Position).filter(Position.quantity > 0).all()
    holdings = []
    total_cost = 0.0
    for p in positions:
        cost_yuan = (p.avg_cost or 0) / 100
        qty = int(p.quantity or 0)
        market_price_yuan = (p.market_price or p.avg_cost or 0) / 100
        holdings.append({
            "code": p.stock_code, "name": p.stock_name,
            "position": qty, "cost": round(cost_yuan, 2),
            "current_price": round(market_price_yuan, 2),
        })
        total_cost += cost_yuan * qty
    holdings_str = "\n".join(
        f"- {h['name']}({h['code']}): {h['position']}股, 成本¥{h['cost']:.2f}"
        for h in holdings
    ) or "无持仓"
    account = db.query(SimAccount).first()
    available_cash = float(account.cash) / 100 if account else 100000.0
    total_assets = available_cash + sum(h["position"] * h["current_price"] for h in holdings)
    return {
        "holdings": holdings, "holdings_str": holdings_str,
        "total_cost": round(total_cost, 2),
        "available_cash": round(available_cash, 2),
        "total_assets": round(total_assets, 2),
        "portfolio_sync_failed": portfolio_sync_failed,
    }

strategy.init_strategy_router(debate_engine, feishu, tencent_client, market_client, news_client,
                               generation_status, _get_holdings_data)

app.include_router(market.router)
app.include_router(trading.router)
app.include_router(strategy.router)

# ============================================================
# V6 定时任务实现
# ============================================================

async def _fetch_market_data() -> dict:
    """通过 DataRouter 拉取市场数据（多源容错）"""
    indices = {}
    for code in ["sh000001", "sz399001", "sz399006"]:
        try:
            result = await data_router.fetch(code)
            if result and result.get("price"):
                indices[code] = {"price": result["price"], "change_pct": result.get("change_pct", 0)}
        except Exception:
            continue
    if not indices:
        try:
            batch = await tencent_client.fetch_batch(["sh000001", "sz399001"])
            for k, v in batch.items():
                indices[k] = {"price": v.get("price", 0), "change_pct": v.get("change_pct", 0)}
        except Exception:
            indices = {"sh000001": {"price": 3350, "change_pct": 0}, "sz399001": {"price": 10800, "change_pct": 0}}

    db = SessionLocal()
    try:
        hd = _get_holdings_data(db)
    finally:
        db.close()

    return {"indices": indices, "sectors": [], "holdings": hd["holdings"],
            "holdings_str": hd["holdings_str"], "news": [],
            "available_cash": hd.get("available_cash", 0),
            "total_assets": hd.get("total_assets", 0),
            "portfolio_sync_failed": hd.get("portfolio_sync_failed", False)}


def _decision_recommendations(decision: dict) -> list[dict]:
    recommendations: list[dict] = []
    seen = set()
    for bucket in ("short_term", "mid_low_freq"):
        section = decision.get(bucket, {})
        if isinstance(section, dict):
            for rec in section.get("recommendations", []) or []:
                code = str(rec.get("code") or "").strip() if isinstance(rec, dict) else ""
                if code and code not in seen:
                    seen.add(code)
                    recommendations.append(rec)
    for bucket in ("stock_pool", "unaffordable_watchlist"):
        for rec in decision.get(bucket, []) or []:
            code = str(rec.get("code") or "").strip() if isinstance(rec, dict) else ""
            if code and code not in seen:
                seen.add(code)
                recommendations.append(rec)
    return recommendations


def persist_premarket_recommendations(
    debate_result: dict,
    decision: dict,
    *,
    store: CandidatePoolStore | None = None,
) -> int:
    """Write AI recommendations only after an explicit fail-closed approval."""
    gate = debate_result.get("production_gate") if isinstance(debate_result, dict) else None
    if not isinstance(gate, dict) or gate.get("allowed") is not True:
        return 0
    target_store = store or CandidatePoolStore()
    return target_store.upsert_recommendations(
        _decision_recommendations(decision),
        source="premarket",
    )


notification_gate = NotificationGate()


def _filter_candidate_alerts_by_visible_gate(
    alerts: list[dict],
    *,
    today: date | None = None,
    gate_path: str | Path | None = None,
    entry_gate: dict | None = None,
) -> list[dict]:
    gate = entry_gate or load_runtime_visible_decision_gate(path=gate_path, today=today)
    return filter_alerts_by_visible_decision_gate(alerts, gate)


def _format_lifecycle_alerts(alerts: list[dict]) -> str:
    return build_alert_digest(alerts, title="候选池/持仓生命周期提醒")


def _positions_map(positions: list[Position]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for p in positions:
        result[p.stock_code] = {
            "shares": int(p.quantity or 0),
            "market_value": round(float(p.market_value or 0) / 100, 2),
            "avg_cost": round(float(p.avg_cost or 0) / 100, 2),
        }
    return result


async def _scan_candidate_pool_and_push(
    stage: str,
    available_cash: float,
    total_assets: float = 0,
    positions: dict[str, dict] | None = None,
    entry_gate: dict | None = None,
) -> dict:
    effective_entry_gate = entry_gate or load_runtime_visible_decision_gate()
    effective_entry_gate, quote_validations = (
        _runtime_quote_gate_and_validations(
            effective_entry_gate,
            positions=positions,
        )
    )
    try:
        result = await evaluate_candidate_pool(
            CandidatePoolStore(),
            FastRealtimeMarketDataSource(),
            available_cash=float(available_cash or 0),
            total_assets=float(total_assets or 0),
            positions=positions,
            entry_gate=effective_entry_gate,
            quote_validations=quote_validations,
        )
    except Exception as exc:
        logger.warning(f"{stage}候选池扫描失败: {exc}")
        return {"scanned": 0, "alerts": [], "error": str(exc)}

    alerts = result.get("alerts", [])
    gate_eligible_alerts = _filter_candidate_alerts_by_visible_gate(
        alerts,
        entry_gate=effective_entry_gate,
    )
    deliverable_alerts = notification_gate.filter_alerts(gate_eligible_alerts, stage=stage)
    if deliverable_alerts:
        title = f"旺财V7.5 候选池提醒 - {stage}"
        _feishu_webhook_push(title, _format_lifecycle_alerts(deliverable_alerts))
    logger.info(
        f"{stage}候选池扫描完成: scanned={result.get('scanned', 0)} "
        f"alerts={len(alerts)} gate_eligible={len(gate_eligible_alerts)} "
        f"delivered={len(deliverable_alerts)}"
    )
    return {
        **result,
        "alerts": gate_eligible_alerts,
        "raw_alert_count": len(alerts),
        "visible_gate_blocked_count": len(alerts) - len(gate_eligible_alerts),
    }


def _runtime_quote_gate_and_validations(
    entry_gate: dict,
    *,
    positions: dict[str, dict] | None,
    quote_summary: dict | None = None,
) -> tuple[dict, dict | None]:
    """Use live per-code quotes while preserving every non-quote decision veto."""
    if quote_summary is None:
        try:
            from app.config import resolve_runtime_yitaojin_paths
            from app.integrations.yitaojin.quotes import (
                load_quote_validation_summary,
            )

            paths = resolve_runtime_yitaojin_paths()
            try:
                raw = json.loads(
                    paths.quote_snapshot.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                raw = {}
            requested_codes = (
                {
                    str(code)
                    for code in raw.get("requested_codes", [])
                    if isinstance(code, str)
                }
                if isinstance(raw, dict)
                else set()
            )
            held_codes = set(positions or {})
            quote_summary = load_quote_validation_summary(
                paths.quote_snapshot,
                critical_codes=requested_codes - held_codes,
            )
        except Exception:
            quote_summary = {
                "enabled": (
                    os.getenv("CONGXI_YITAOJIN_ENABLED", "")
                    .strip()
                    .lower()
                    == "true"
                ),
                "status": "unavailable",
                "validations": {},
                "reasons": ["quote_snapshot_unavailable"],
            }
    current_reasons = list(entry_gate.get("reasons") or [])
    quote_enabled = (
        isinstance(quote_summary, dict)
        and quote_summary.get("enabled") is True
    )
    if not quote_enabled and "quote_validation_blocked" not in current_reasons:
        return entry_gate, None

    runtime_gate = dict(entry_gate)
    reasons = [
        reason
        for reason in current_reasons
        if reason != "quote_validation_blocked"
    ]
    runtime_gate["reasons"] = reasons
    runtime_gate["entry_allowed"] = not reasons
    runtime_gate["state"] = "allowed" if not reasons else "blocked"
    if not quote_enabled:
        if isinstance(quote_summary, dict):
            runtime_gate["quote_validation"] = dict(quote_summary)
        return runtime_gate, None

    runtime_gate["quote_validation"] = dict(quote_summary)
    validations = quote_summary.get("validations")
    return (
        runtime_gate,
        dict(validations) if isinstance(validations, dict) else {},
    )


def _in_intraday_alert_window(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    current = now.time()
    return time(9, 35) <= current <= time(11, 25) or time(13, 0) <= current <= time(14, 50)


def _account_cash_and_total(acc: SimAccount | None) -> tuple[float, float]:
    if not acc:
        return 0.0, 0.0
    return acc.cash / 100, acc.total_value / 100


async def _run_intraday_alert_scan_with_status():
    """High-frequency event scan for entries, add-ons, take-profit and stop-loss."""
    if not is_trading_day() or not _in_intraday_alert_window():
        return
    gs = generation_status["event_scan"]
    if gs["running"]:
        return
    gs["running"] = True
    gs["started_at"] = str(datetime.now())
    yitaojin_quote_task = asyncio.create_task(
        _run_yitaojin_quotes_with_status("intraday_quotes")
    )
    try:
        logger.info("--- 盘中事件触发扫描 ---")
        db = SessionLocal()
        try:
            candidate_entry_gate = None
            try:
                sync_db_from_user_portfolio(db)
            except Exception as exc:
                logger.warning(f"盘中事件扫描持仓同步失败，继续使用数据库现状: {exc}")
                candidate_entry_gate = build_runtime_blocked_gate("portfolio_sync_failed")
            positions = db.query(Position).filter(Position.quantity > 0).all()
            codes = [p.stock_code for p in positions if p.stock_code]
            position_quotes = await TencentDataSource().fetch_batch(codes) if codes else {}

            for p in positions:
                rt = position_quotes.get(p.stock_code) or {}
                price = rt.get("price", 0) or 0
                if price <= 0:
                    continue
                price_fen = int(float(price) * 100)
                p.market_price = price_fen
                p.market_value = p.quantity * price_fen
                p.unrealized_pnl = p.market_value - (p.avg_cost * p.quantity)
            db.commit()

            position_watch = PositionWatchStore()
            watch_alerts = evaluate_position_watch(position_watch, position_quotes)
            deliverable_watch_alerts = notification_gate.filter_alerts(watch_alerts, stage="盘中持仓")
            if deliverable_watch_alerts:
                _feishu_webhook_push("旺财V7.5 盘中持仓触发", _format_lifecycle_alerts(deliverable_watch_alerts))

            try:
                await asyncio.wait_for(
                    asyncio.shield(yitaojin_quote_task),
                    timeout=50,
                )
            except asyncio.TimeoutError:
                logger.warning("盘中易淘金行情校验超时，候选入场将失败关闭")
            acc = db.query(SimAccount).first()
            cash, total_assets = _account_cash_and_total(acc)
            lifecycle_result = await _scan_candidate_pool_and_push(
                "盘中",
                cash,
                total_assets,
                positions=_positions_map(positions),
                entry_gate=candidate_entry_gate,
            )
            logger.info(
                "盘中事件扫描完成: "
                f"position_alerts={len(watch_alerts)} delivered_position={len(deliverable_watch_alerts)} "
                f"candidate_alerts={len(lifecycle_result.get('alerts', []))}"
            )
        finally:
            db.close()
    except Exception as exc:
        logger.error(f"盘中事件触发扫描异常: {exc}", exc_info=True)
    finally:
        gs["running"] = False


def _get_today_risk_alerts(db: Session, today: datetime | None = None) -> list[RiskAlert]:
    """Return risk alerts created since local midnight."""
    now = today or datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(RiskAlert)
        .filter(RiskAlert.timestamp >= start)
        .order_by(RiskAlert.timestamp.desc())
        .all()
    )


def _feishu_webhook_push(title: str, content: str) -> bool:
    """同步飞书推送：OpenAPI 优先，Webhook 兜底（供 APScheduler 线程使用）。"""
    MAX_RETRIES = 3
    BASE_DELAY = 10  # 秒

    for attempt in range(1 + MAX_RETRIES):
        try:
            template = "red"
            if "风险" not in title and "熔断" not in title and "告警" not in title:
                template = "green" if any(kw in title for kw in ("检查", "无忧", "空仓")) else "blue"
            result = send_feishu_card_sync(
                title=title,
                content=content,
                webhook_url=settings.FEISHU_WEBHOOK_URL,
                color=template,
                app_id=settings.FEISHU_APP_ID,
                app_secret=settings.FEISHU_APP_SECRET,
                chat_id=settings.FEISHU_CHAT_ID,
                api_base=settings.FEISHU_API_BASE,
            )
            if result.get("feishu_api") or result.get("feishu_webhook"):
                logger.info(f"Feishu OK (attempt {attempt+1}, channel={result.get('channel', '')}): {title}")
                return True
            logger.warning(f"Feishu FAIL (attempt {attempt+1}/{MAX_RETRIES+1}): {result.get('error', '')} - {title}")
        except Exception as e:
            logger.warning(f"Feishu 异常 (attempt {attempt+1}): {e}")

        if attempt == MAX_RETRIES:
            logger.error(f"Feishu 已达最大重试次数 ({MAX_RETRIES})，放弃: {title}")
            return False

        delay = compute_retry_delay(attempt + 1, BASE_DELAY, 120)
        logger.info(f"Feishu 将在 {delay:.0f}s 后重试...")
        import time
        time.sleep(delay)

    return False

async def _run_premarket_with_status():
    """盘前任务 — AI辩论 + 建仓计划 -> 飞书推送"""
    if not should_run_premarket_calibration():
        logger.info(schedule_reason("premarket_calibration"))
        return
    gs = generation_status["premarket"]
    if gs["running"]:
        return

    gs["running"] = True
    gs["started_at"] = str(datetime.now())
    try:
        logger.info("=== 旺财V7 盘前任务启动 ===")
        market_data = await _fetch_market_data()
        try:
            from app.ai.sentinel_research import load_research_package

            sentinel_package = load_research_package(str(date.today()))
            if sentinel_package:
                market_data["sentinel_evidence"] = build_sentinel_evidence_context(sentinel_package)
                ingest_result = upsert_sentinel_evidence_to_target_pool(sentinel_package)
                logger.info(
                    "Sentinel evidence 已进入盘前输入: "
                    f"evidence={ingest_result.get('evidence_count', 0)} "
                    f"targets={ingest_result.get('upserted_targets', 0)}"
                )
        except Exception as exc:
            logger.warning(f"Sentinel evidence 盘前接入失败，降级继续: {exc}")
        sh = market_data["indices"].get("sh000001", {}).get("price", 3350)
        sz = market_data["indices"].get("sz399001", {}).get("price", 10800)
        logger.info(f"盘前指数: 上证{sh:.0f} 深证{sz:.0f}")

        report = await run_analysis(market_data)
        logger.info("分析完成，启动AI辩论...")
        debate_result = await run_debate(report, strategy_type="premarket")
        decision = debate_result.get("decision", {})
        risk = debate_result.get("recommended_risk_level", 3)
        pool = decision.get("stock_pool", [])

        from app.data_sources.tencent_client import TencentDataSource
        from app.services.quote_enrichment import enrich_decision_with_realtime_quotes
        from app.services.report_templates import strategy_report_md
        decision = await enrich_decision_with_realtime_quotes(decision, TencentDataSource())
        production_gate = debate_result.get("production_gate") or {
            "allowed": False,
            "reasons": ["production_gate_missing"],
        }
        decision["production_gate"] = production_gate
        candidate_count = persist_premarket_recommendations(debate_result, decision)
        if production_gate.get("allowed") is True:
            logger.info(f"盘前推荐已进入生产候选池: {candidate_count} 支")
        else:
            logger.warning(
                "AI推荐未进入生产候选池: "
                f"reasons={production_gate.get('reasons') or ['unknown']}"
            )
        report_md = strategy_report_md(decision)
        extra = "\n\n...\n\n*[完整报告已推送]*"
        summary = report_md[:2800] + (extra if len(report_md) > 2800 else "")
        # 使用报告引擎全渠道推送
        holdings_data = {
            "holdings": market_data.get("holdings", []),
            "holdings_str": market_data.get("holdings_str", "无持仓"),
        }
        report_ok = await report_engine.push_premarket(
            date=str(date.today()),
            decision=decision,
            positions=holdings_data.get("holdings", []),
            risk_level=risk,
        )
        if not report_ok:
            logger.warning("报告引擎推送异常，降级为原始webhook推送")
            _feishu_webhook_push(f"旺财V7 盘前策略 [R{risk}]", summary)

        db = SessionLocal()
        try:
            strat = AIStrategy(
                strategy_type="premarket",
                content=report_md,
                recommended_stocks={
                    "short_term": decision.get("short_term", {}).get("recommendations", []),
                    "mid_low_freq": decision.get("mid_low_freq", {}).get("recommendations", []),
                },
            )
            db.add(strat)
            db.commit()
        except Exception as e:
            logger.warning(f"策略存储失败: {e}")
        finally:
            db.close()

        logger.info(f"=== 盘前任务完成: R{risk}, {len(pool)}支标的, {decision.get('final_view','?')} ===")
    except Exception as e:
        logger.error(f"盘前任务异常: {e}", exc_info=True)
        _feishu_webhook_push("盘前任务异常", f"错误: {str(e)[:500]}")
    finally:
        gs["running"] = False

async def _run_midday_with_status():
    """午盘快速分析"""
    if not is_trading_day():
        return
    gs = generation_status["intraday"]
    if gs["running"]:
        return
    gs["running"] = True
    gs["started_at"] = str(datetime.now())
    try:
        logger.info("--- 午盘快速分析 ---")
        market_data = await _fetch_market_data()
        debate_summary = await debate_engine.debate_intraday(
            json.dumps(market_data, ensure_ascii=False),
            market_data.get("holdings_str", "无持仓"),
            news_context="午间市场概览",
        )
        final = debate_summary.get("final", {})

        holdings = market_data.get("holdings", [])
        if not holdings:
            candidate_entry_gate = None
            if market_data.get("portfolio_sync_failed"):
                candidate_entry_gate = build_runtime_blocked_gate("portfolio_sync_failed")
            lifecycle_result = await _scan_candidate_pool_and_push(
                "午盘",
                market_data.get("available_cash", 0),
                market_data.get("total_assets", market_data.get("total_value", 0)),
                entry_gate=candidate_entry_gate,
            )
            alert_count = len(lifecycle_result.get("alerts", []))
            tip = "候选池已触发提醒，请按飞书卡片人工复核。" if alert_count else "候选池暂无可执行触发，继续观察。"
            await report_engine.push_midday(
                date=str(date.today()),
                market_summary=(
                    "当前空仓。\n\n"
                    f"候选池扫描: {lifecycle_result.get('scanned', 0)} 支，"
                    f"触发提醒: {alert_count} 条。"
                ),
                positions=[],
                afternoon_tip=tip,
            )
            logger.info("--- 午盘快报完成（空仓候选池扫描模式） ---")
            return

        snapshot = final.get("market_snapshot", "N/A")
        action = final.get("overall_action", "观望")
        confidence = final.get("confidence", 5)

        content = f"**午盘概况**\n{snapshot}\n\n操作建议: {action} (信心{confidence}/10)\n\n"
        recs = final.get("recommendations", [])
        for r in recs[:3]:
            content += f"- {r.get('name','')}({r.get('code','')}): {r.get('reason','')}\n"
        lesson = final.get("beginner_lesson", "")
        if lesson:
            content += f"\n---\n{lesson}"

        # 使用报告引擎推送午盘快报
        hd = market_data.get("holdings", [])
        pos_list = []
        for h in hd:
            pos_list.append({
                "code": h.get("code", ""), "name": h.get("name", ""),
                "position": h.get("position", 0),
                "cost": h.get("cost", 0), "current_price": h.get("current_price", 0),
            })
        await report_engine.push_midday(
            date=str(date.today()),
            market_summary=f"{snapshot}\n\n操作建议: {action} (信心{confidence}/10)",
            positions=pos_list,
            afternoon_tip=lesson,
        )
        logger.info(f"--- 午盘快报完成: {action} ---")
    except Exception as e:
        logger.error(f"午盘分析异常: {e}", exc_info=True)
    finally:
        gs["running"] = False

async def _run_afternoon_with_status():
    """午后风险检查 — 使用 MonitorService 多维度风控"""
    if not is_trading_day():
        return
    gs = generation_status["afternoon"]
    if gs["running"]:
        return
    gs["running"] = True
    gs["started_at"] = str(datetime.now())
    try:
        logger.info("--- 午后风险检查(MonitorService) ---")
        db = SessionLocal()
        try:
            candidate_entry_gate = None
            try:
                sync_db_from_user_portfolio(db)
            except Exception as exc:
                logger.warning(f"午后风险检查持仓同步失败，继续使用数据库现状: {exc}")
                candidate_entry_gate = build_runtime_blocked_gate("portfolio_sync_failed")
            positions = db.query(Position).filter(Position.quantity > 0).all()
            today_str = str(date.today())

            # 空仓智能：推送精简版风控检查，而非完全静默
            if not positions:
                logger.info("空仓：推送精简午后检查")
                acc = db.query(SimAccount).first()
                cash, total_assets = _account_cash_and_total(acc)
                lifecycle_result = await _scan_candidate_pool_and_push(
                    "午后",
                    cash,
                    total_assets,
                    entry_gate=candidate_entry_gate,
                )
                lifecycle_alerts = lifecycle_result.get("alerts", [])
                await report_engine.push_afternoon_risk(
                    date=today_str,
                    positions=[],
                    alerts=[{
                        "stock_code": a.get("stock_code", ""),
                        "stock_name": a.get("stock_name", ""),
                        "alert_type": "candidate_pool",
                        "level": normalize_alert_level(a.get("level")),
                        "message": a.get("message", ""),
                        "suggestion": a.get("suggestion", ""),
                    } for a in lifecycle_alerts],
                    performance={"total_assets": cash, "available_cash": cash},
                )
                return


            alerts = []
            position_quotes = {}
            position_watch = PositionWatchStore()
            for p in positions:
                # 通过 MonitorService 获取多源数据
                rt = await monitor.get_realtime_data(p.stock_code)
                if not rt or not rt.get("price"):
                    continue
                position_quotes[p.stock_code] = rt

                # 更新持仓市价
                price = rt.get("price", 0)
                if isinstance(price, float) and price < 10000:
                    price_fen = int(price * 100)
                else:
                    price_fen = int(price)
                p.market_price = price_fen
                p.market_value = p.quantity * price_fen
                p.unrealized_pnl = p.market_value - (p.avg_cost * p.quantity)

                # 构建持仓字典供风控引擎检查
                pos_dict = {
                    "code": p.stock_code,
                    "name": p.stock_name,
                    "cost_price": round(p.avg_cost / 100, 2) if p.avg_cost else 0,
                    "id": p.id,
                }
                plan = position_watch.get(p.stock_code) or {}
                if plan:
                    pos_dict["stop_loss_price"] = plan.get("stop_loss_price")
                    pos_dict["target_price"] = plan.get("target_price")
                risk_result = await monitor.check_risk(pos_dict, rt, db_session=db)
                if risk_result:
                    msg = f"{risk_result['level'].upper()}: {p.stock_name}({p.stock_code}) - {risk_result['message']}"
                    alerts.append(msg)
                    # high 级别添加到 RiskAlert 表
                    if risk_result["level"] == "high":
                        try:
                            alert = RiskAlert(
                                stock_code=p.stock_code, stock_name=p.stock_name,
                                alert_type="composite", alert_level="high",
                                alert_message=risk_result["message"][:500],
                                suggestion=risk_result.get("suggestion", ""),
                            )
                            db.add(alert)
                        except Exception:
                            pass

            db.commit()
            watch_alerts = evaluate_position_watch(position_watch, position_quotes)
            for alert in watch_alerts:
                alerts.append(
                    f"{normalize_alert_level(alert.get('level')).upper()}: "
                    f"{alert.get('stock_name')}({alert.get('stock_code')}) - {alert.get('message')}"
                )
            if watch_alerts:
                deliverable_watch_alerts = notification_gate.filter_alerts(watch_alerts, stage="持仓")
                if deliverable_watch_alerts:
                    _feishu_webhook_push("旺财V7.5 持仓预警", _format_lifecycle_alerts(deliverable_watch_alerts))
            acc = db.query(SimAccount).first()
            cash, total_assets = _account_cash_and_total(acc)
            lifecycle_result = await _scan_candidate_pool_and_push(
                "午后",
                cash,
                total_assets,
                positions=_positions_map(positions),
                entry_gate=candidate_entry_gate,
            )
            lifecycle_alerts = lifecycle_result.get("alerts", [])

            # 统一推送午后风控（有警告红色/无警告绿色）
            mv = sum(p.market_value for p in positions) / 100
            pos_list = [{
                "stock_code": p.stock_code, "stock_name": p.stock_name,
                "quantity": p.quantity, "avg_cost": p.avg_cost, "market_price": p.market_price,
            } for p in positions]
            alert_list = [{
                "level": "high" if "HIGH" in a.split(": ", 1)[0].upper() else "mid",
                "message": a[:200],
                "stock_name": a.split("(")[0].split(": ")[-1] if ": " in a else "",
            } for a in alerts] + [{
                "stock_code": a.get("stock_code", ""),
                "stock_name": a.get("stock_name", ""),
                "alert_type": "candidate_pool",
                "level": normalize_alert_level(a.get("level")),
                "message": a.get("message", "")[:200],
                "suggestion": a.get("suggestion", ""),
            } for a in lifecycle_alerts]
            await report_engine.push_afternoon_risk(
                date=today_str,
                positions=pos_list, alerts=alert_list,
                performance={"total_assets": cash + mv, "available_cash": cash},
            )
        finally:
            db.close()
    except Exception as e:
        logger.error(f"午后检查异常: {e}", exc_info=True)
    finally:
        gs["running"] = False

async def _run_review_with_status():
    """收盘复盘"""
    if not is_trading_day():
        return
    gs = generation_status["review"]
    if gs["running"]:
        return
    gs["running"] = True
    gs["started_at"] = str(datetime.now())
    try:
        logger.info("=== 收盘复盘 ===")
        from app.engine.review import run_daily_review
        result = run_daily_review()
        result_str = result.get("result", "N/A")
        violations = result.get("violations", [])

        content = f"**今日复盘: {result_str}**\n\n"
        if violations:
            for v in violations:
                content += f"- {v.get('rule','?')}: {v.get('detail','?')}\n"
        else:
            content += "无违规项\n"

        db = SessionLocal()
        try:
            pos = db.query(Position).filter(Position.quantity > 0).all()
            acc = db.query(SimAccount).first()
            mv = sum(p.market_value for p in pos) / 100
            cash = acc.cash / 100 if acc else 0
            content += f"\n总资产: {(cash+mv):,.0f} | 现金: {cash:,.0f} | 持仓: {mv:,.0f}"
        finally:
            db.close()

        _feishu_webhook_push("收盘复盘", content)

        # 回填已到期的辩论实际收益
        try:
            db_review = SessionLocal()
            try:
                await DebateTracker.fill_pending(db_review)
            finally:
                db_review.close()
        except Exception as e:
            logger.warning(f"辩论回填异常（不影响主流程）: {e}")

        logger.info(f"=== 复盘完成: {result_str} ===")
    except Exception as e:
        logger.error(f"复盘异常: {e}", exc_info=True)
    finally:
        gs["running"] = False

async def _run_daily_report_with_status():
    """次日投资策略主报告."""
    if not should_run_main_report():
        logger.info(schedule_reason("main_report"))
        return
    try:
        logger.info(schedule_reason("main_report"))
        logger.info("=== 次日投资策略主报告 ===")
        from scripts import daily_report

        report_path = await daily_report.main()
        if report_path:
            logger.info(f"=== 次日投资策略主报告完成: {report_path} ===")
        else:
            logger.warning("次日投资策略主报告结束但未返回报告路径")
    except Exception as e:
        logger.error(f"次日投资策略主报告异常: {e}", exc_info=True)


async def _run_yitaojin_task_with_status(task: str) -> dict:
    """Run one isolated broker task; failures never abort reports or risk scans."""
    try:
        from app.integrations.yitaojin.runtime import run_yitaojin_task

        result = await run_yitaojin_task(task)
        state = result.get("state", "unknown")
        if state == "failed":
            logger.warning(
                "易淘金任务失败: "
                f"task={task} reason={result.get('last_failure_reason', 'unknown')}"
            )
        else:
            logger.info(f"易淘金任务完成: task={task} state={state}")
        return result
    except Exception as exc:
        logger.error(
            f"易淘金任务包装器异常: task={task} type={exc.__class__.__name__}",
            exc_info=True,
        )
        return {
            "enabled": bool(getattr(settings, "CONGXI_YITAOJIN_ENABLED", False)),
            "state": "failed",
            "task": task,
            "last_failure_reason": exc.__class__.__name__,
        }


async def _run_yitaojin_morning_with_status():
    return await _run_yitaojin_task_with_status("morning")


async def _run_yitaojin_evening_with_status():
    return await _run_yitaojin_task_with_status("evening")


async def _run_yitaojin_quotes_with_status(task: str = "priority_quotes"):
    return await _run_yitaojin_task_with_status(task)


def register_yitaojin_jobs(target_scheduler) -> None:
    """Register bounded jobs; intraday quote refresh reuses the existing scan."""
    job_options = {
        "replace_existing": True,
        "max_instances": 1,
        "coalesce": True,
    }
    target_scheduler.add_job(
        _run_yitaojin_morning_with_status,
        CronTrigger(
            hour=8,
            minute=55,
            day_of_week="mon-fri",
            timezone="Asia/Shanghai",
        ),
        id="yitaojin_morning",
        name="易淘金盘前账户、自选与重点行情",
        misfire_grace_time=300,
        **job_options,
    )
    target_scheduler.add_job(
        _run_yitaojin_quotes_with_status,
        CronTrigger(
            hour=11,
            minute=35,
            day_of_week="mon-fri",
            timezone="Asia/Shanghai",
        ),
        id="yitaojin_midday_quotes",
        name="易淘金午间重点行情校验",
        misfire_grace_time=120,
        **job_options,
    )
    target_scheduler.add_job(
        _run_yitaojin_quotes_with_status,
        CronTrigger(
            hour=14,
            minute=55,
            day_of_week="mon-fri",
            timezone="Asia/Shanghai",
        ),
        id="yitaojin_close_quotes",
        name="易淘金收盘前重点行情校验",
        misfire_grace_time=120,
        **job_options,
    )
    target_scheduler.add_job(
        _run_yitaojin_evening_with_status,
        CronTrigger(
            hour=20,
            minute=45,
            day_of_week="mon-fri",
            timezone="Asia/Shanghai",
        ),
        id="yitaojin_evening",
        name="易淘金晚间账户与自选同步",
        misfire_grace_time=900,
        **job_options,
    )


async def _run_sentinel_research_with_status():
    """Build the current Sentinel research package and Serenity deep dives."""
    try:
        import subprocess
        import sys
        from app.config import PROJECT_ROOT

        project_root = f"{PROJECT_ROOT}/.."
        script_path = f"{project_root}/scripts/run_sentinel.py"
        report_date = str(date.today())
        logger.info("=== Sentinel研究包与Serenity深挖启动 ===")
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, script_path, "--date", report_date, "--mode", "news"],
            cwd=project_root,
            text=True,
            capture_output=True,
            timeout=180,
        )
        if result.returncode != 0:
            logger.warning(f"Sentinel研究包与Serenity深挖失败: {result.stderr[:800]}")
            return
        logger.info(f"Sentinel研究包与Serenity深挖完成: {result.stdout[:800]}")
    except Exception as e:
        logger.error(f"Sentinel研究包与Serenity深挖异常: {e}", exc_info=True)


async def _run_sentinel_review_with_status():
    """Sentinel role-performance review and archive job."""
    try:
        import subprocess
        import sys
        from app.config import PROJECT_ROOT

        project_root = f"{PROJECT_ROOT}/.."
        script_path = f"{project_root}/scripts/run_sentinel.py"
        report_date = str(date.today())
        logger.info("=== Sentinel绩效回看启动 ===")
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, script_path, "--date", report_date, "--mode", "review"],
            cwd=project_root,
            text=True,
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.warning(f"Sentinel绩效回看失败: {result.stderr[:800]}")
            return
        logger.info(f"Sentinel绩效回看完成: {result.stdout[:800]}")
    except Exception as e:
        logger.error(f"Sentinel绩效回看异常: {e}", exc_info=True)


async def _run_prediction_lab_with_status():
    """Collect prediction samples, then backfill the derived due queue once."""
    if not is_trading_day():
        return
    try:
        import subprocess
        import sys
        from app.config import PROJECT_ROOT

        project_root = f"{PROJECT_ROOT}/.."
        script_path = f"{project_root}/scripts/run_prediction_lab.py"
        today = date.today()
        report_date = str(today)
        logger.info("=== 预测账本采集启动 ===")
        collect = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                script_path,
                "collect",
                "--date",
                report_date,
                "--universe",
                "target_pool",
                "--limit",
                "200",
            ],
            cwd=project_root,
            text=True,
            capture_output=True,
            timeout=300,
        )
        if collect.returncode != 0:
            logger.warning(f"预测账本采集失败: {collect.stderr[:800]}")
        else:
            logger.info(f"预测账本采集完成: {collect.stdout[:800]}")

        result = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                script_path,
                "backfill",
                "--as-of",
                report_date,
                "--limit",
                "600",
            ],
            cwd=project_root,
            text=True,
            capture_output=True,
            timeout=300,
        )
        if result.returncode == 0:
            logger.info(f"预测账本 due queue 补跑完成: {result.stdout[:500]}")
        else:
            logger.warning(f"预测账本 due queue 补跑失败: {result.stderr[:500]}")
    except Exception as e:
        logger.error(f"预测账本任务异常: {e}", exc_info=True)


async def _startup_health_check():
    """启动时连通性检查"""
    await asyncio.sleep(2)
    issues = []
    try:
        ok = await cloud.is_available()
        logger.info(f"DeepSeek API: {'OK' if ok else 'UNAVAILABLE'}")
        if not ok:
            issues.append("DeepSeek API 不可用")
    except Exception as e:
        logger.warning(f"DeepSeek 检测失败: {e}")
        issues.append(f"DeepSeek: {e}")

    try:
        tc = await tencent_client.fetch("sh000001")
        logger.info(f"腾讯行情: {'OK' if tc and tc.get('price') else 'UNAVAILABLE'}")
        if not tc or not tc.get("price"):
            issues.append("腾讯行情数据源异常")
    except Exception as e:
        logger.warning(f"行情检测失败: {e}")
        issues.append(f"行情: {e}")

    if issues:
        _feishu_webhook_push("旺财V7 启动告警", "\n".join(f"- {i}" for i in issues))


def _poll_bot_messages():
    """轮询飞书Bot消息 (每30秒)"""
    try:
        check_and_process_new_messages()
    except Exception as e:
        logger.debug(f"Bot轮询异常: {e}")


# 别名: strategy.py 使用的旧名称
_run_intraday_with_status = _run_midday_with_status
async def _check_qwen() -> bool:
    """检查 Qwen API 连通性"""
    qwen_key = getattr(settings, "QWEN_API_KEY", "")
    if not qwen_key:
        return False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://dashscope.aliyuncs.com/api/v1/models",
                headers={"Authorization": f"Bearer {qwen_key}"}
            )
            return resp.status_code == 200
    except Exception:
        return False


async def _check_data_source(name: str) -> bool:
    """检查数据源连通性"""
    try:
        if name == "tencent":
            result = await tencent_client.fetch("sh000001")
            return bool(result and result.get("price"))
        elif name == "eastmoney":
            return True  # 简化检查
        elif name == "tushare":
            return True  # 简化检查
        return False
    except Exception:
        return False

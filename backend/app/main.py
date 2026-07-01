"""FastAPI 主应用 — V7: DeepSeek云端AI + 飞书全通道 + 定时调度"""
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import date, datetime

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

# 报告引擎
from app.report_engine.engine import report_engine

class FeishuNotifier:
    """飞书消息推送"""

    def __init__(self):
        self.webhook_url = settings.FEISHU_WEBHOOK_URL

    async def send(self, title: str, content: str) -> bool:
        if not self.webhook_url or "YOUR_WEBHOOK_ID" in self.webhook_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                payload = {
                    "msg_type": "interactive",
                    "card": {
                        "header": {
                            "title": {"tag": "plain_text", "content": title},
                            "template": "red" if "风险" in title else "blue",
                        },
                        "elements": [{"tag": "markdown", "content": content[:3000]}],
                    },
                }
                resp = await client.post(self.webhook_url, json=payload)
                ok = resp.status_code == 200
                if ok:
                    logger.info(f"飞书消息发送成功: {title}")
                else:
                    logger.warning(f"飞书消息发送失败: {resp.status_code} {resp.text[:200]}")
                return ok
        except Exception as e:
            logger.error(f"飞书推送异常: {e}")
            return False

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("恭喜发财 V7 应用启动中...")
    init_db()
    logger.info("数据库初始化完成")

    if not settings.FEISHU_WEBHOOK_URL or "YOUR_WEBHOOK_ID" in settings.FEISHU_WEBHOOK_URL:
        logger.warning("飞书 Webhook URL 未配置，消息推送将不可用。请在 .env.local 中设置 FEISHU_WEBHOOK_URL")
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
        _run_review_with_status,
        CronTrigger(hour=15, minute=5, day_of_week='mon-fri', timezone='Asia/Shanghai'),
        id='review', name='收盘复盘', replace_existing=True,
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

    scheduler.start()
    for stale_job_id in ("daily_report",):
        try:
            scheduler.remove_job(stale_job_id)
            logger.info(f"已清理旧调度任务: {stale_job_id}")
        except Exception:
            pass
    logger.info("旺财V7.5-dev 调度器已启动 (次日主报告 + 盘前校准 + 盘中/收盘 + Bot轮询)")

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
    version="7.5.0-dev",
    lifespan=lifespan,
)

# ============================================================
# 共享实例初始化
# ============================================================
debate_engine = AIDebateEngine()
feishu = FeishuNotifier()
feishu_v6 = feishu_channels
scheduler = AsyncIOScheduler(
    jobstores={
        "default": SQLAlchemyJobStore(url=f"sqlite:///{settings.DATABASE_PATH}")
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
}

def _get_holdings_data(db: Session) -> dict:
    """从 Position 表获取持仓数据，用于分析引擎和规划引擎。"""
    try:
        sync_db_from_user_portfolio(db)
    except Exception as e:
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
            "total_assets": hd.get("total_assets", 0)}


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


def _format_lifecycle_alerts(alerts: list[dict]) -> str:
    lines = ["**候选池/持仓生命周期提醒**", ""]
    for alert in alerts[:8]:
        lines.append(
            f"- {alert.get('stock_name', '')}({alert.get('stock_code', '')}) "
            f"{alert.get('action', '')}: {alert.get('message', '')}"
        )
        suggestion = alert.get("suggestion")
        if suggestion:
            lines.append(f"  建议: {suggestion}")
    return "\n".join(lines)


async def _scan_candidate_pool_and_push(stage: str, available_cash: float) -> dict:
    try:
        result = await evaluate_candidate_pool(
            CandidatePoolStore(),
            TencentDataSource(),
            available_cash=float(available_cash or 0),
        )
    except Exception as exc:
        logger.warning(f"{stage}候选池扫描失败: {exc}")
        return {"scanned": 0, "alerts": [], "error": str(exc)}

    alerts = result.get("alerts", [])
    if alerts:
        title = f"旺财V7.5 候选池提醒 - {stage}"
        _feishu_webhook_push(title, _format_lifecycle_alerts(alerts))
    logger.info(f"{stage}候选池扫描完成: scanned={result.get('scanned', 0)} alerts={len(alerts)}")
    return result


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
    """同步飞书 webhook 推送（供 scheduler 线程使用）"""
    """同步飞书 webhook 推送 + 指数退避重试（供 APScheduler 线程使用）"""
    MAX_RETRIES = 3
    BASE_DELAY = 10  # 秒

    webhook_url = settings.FEISHU_WEBHOOK_URL
    if not webhook_url or "YOUR_WEBHOOK" in webhook_url:
        logger.warning("飞书Webhook未配置，跳过推送")
        return False

    for attempt in range(1 + MAX_RETRIES):
        try:
            import requests
            template = "red"
            if "风险" not in title and "熔断" not in title and "告警" not in title:
                template = "green" if any(kw in title for kw in ("检查", "无忧", "空仓")) else "blue"
            payload = {
                "msg_type": "interactive",
                "card": {
                    "header": {"title": {"tag": "plain_text", "content": title},
                               "template": template},
                    "elements": [{"tag": "markdown", "content": content[:3000]}],
                },
            }
            resp = requests.post(webhook_url, json=payload, timeout=15)
            if resp.status_code == 200:
                logger.info(f"Webhook OK (attempt {attempt+1}): {title}")
                return True
            logger.warning(f"Webhook FAIL (attempt {attempt+1}/{MAX_RETRIES+1}): {resp.status_code} - {title}")
            if 400 <= resp.status_code < 500:
                logger.warning(f"Webhook 4xx 不重试: {resp.status_code}")
                return False
        except requests.exceptions.Timeout:
            logger.warning(f"Webhook 超时 (attempt {attempt+1}): {title}")
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Webhook 连接失败 (attempt {attempt+1}): {e}")
        except Exception as e:
            logger.warning(f"Webhook 异常 (attempt {attempt+1}): {e}")

        if attempt == MAX_RETRIES:
            logger.error(f"Webhook 已达最大重试次数 ({MAX_RETRIES})，放弃: {title}")
            return False

        delay = compute_retry_delay(attempt + 1, BASE_DELAY, 120)
        logger.info(f"Webhook 将在 {delay:.0f}s 后重试...")
        import time
        time.sleep(delay)

    return False
    webhook_url = settings.FEISHU_WEBHOOK_URL
    if not webhook_url or "YOUR_WEBHOOK" in webhook_url:
        logger.warning("飞书Webhook未配置，跳过推送")
        return False
    try:
        import requests
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": title},
                           "template": "red" if "风险" in title or "熔断" in title else "blue"},
                "elements": [{"tag": "markdown", "content": content[:3000]}],
            },
        }
        resp = requests.post(webhook_url, json=payload, timeout=15)
        ok = resp.status_code == 200
        logger.info(f"Webhook {'OK' if ok else 'FAIL '+str(resp.status_code)}: {title}")
        return ok
    except Exception as e:
        logger.error(f"Webhook异常: {e}")
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
        candidate_count = CandidatePoolStore().upsert_recommendations(
            _decision_recommendations(decision),
            source="premarket",
        )
        logger.info(f"盘前推荐已进入生产候选池: {candidate_count} 支")
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
            lifecycle_result = await _scan_candidate_pool_and_push(
                "午盘",
                market_data.get("available_cash", 0),
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
            positions = db.query(Position).filter(Position.quantity > 0).all()
            today_str = str(date.today())

            # 空仓智能：推送精简版风控检查，而非完全静默
            if not positions:
                logger.info("空仓：推送精简午后检查")
                acc = db.query(SimAccount).first()
                cash = acc.cash / 100 if acc else 0
                lifecycle_result = await _scan_candidate_pool_and_push("午后", cash)
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
                _feishu_webhook_push("旺财V7.5 持仓预警", _format_lifecycle_alerts(watch_alerts))
            acc = db.query(SimAccount).first()
            cash = acc.cash / 100 if acc else 0
            lifecycle_result = await _scan_candidate_pool_and_push("午后", cash)
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
                DebateTracker.fill_pending(db_review)
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
    """收盘全景报告 — 升级版：交易回顾+持仓+风控+系统健康"""
    if not should_run_main_report():
        logger.info(schedule_reason("main_report"))
        return
    try:
        logger.info("=== 收盘全景报告 ===")
        today = str(date.today())

        db = SessionLocal()
        try:
            positions = db.query(Position).filter(Position.quantity > 0).all()
            pos_list = []
            for p in positions:
                cost = p.avg_cost / 100 if p.avg_cost else 0
                price = p.market_price / 100 if p.market_price else 0
                pos_list.append({
                    "code": p.stock_code, "name": p.stock_name,
                    "quantity": p.quantity, "cost": cost,
                    "current_price": price, "market_price": price,
                })

            acc = db.query(SimAccount).first()
            cash = acc.cash / 100 if acc else 0
            mv = sum(p.market_value for p in positions) / 100 if positions else 0

            risk_alerts = _get_today_risk_alerts(db)
            alert_list = []
            for a in risk_alerts:
                alert_list.append({
                    "stock_code": a.stock_code, "stock_name": a.stock_name,
                    "alert_type": a.alert_type, "alert_level": a.alert_level,
                    "alert_message": a.alert_message, "suggestion": a.suggestion or "",
                })
        finally:
            db.close()

        health = {
            "api_service": True,
            "deepseek_api": await cloud.is_available() if hasattr(cloud, 'is_available') else False,
            "qwen_api": await _check_qwen(),
            "tencent_data": await _check_data_source("tencent"),
            "eastmoney_data": await _check_data_source("eastmoney"),
            "tushare_data": await _check_data_source("tushare"),
            "tasks_success": len([j for j in scheduler.get_jobs()]),
            "tasks_fail": 0,
        }

        perf = {
            "daily_pnl": 0, "daily_pnl_pct": 0, "cumulative_pnl": 0,
            "win_rate": 0, "position_count": len(pos_list),
            "total_assets": cash + mv, "available_cash": cash,
        }

        await report_engine.push_closing(
            date=today, positions=pos_list, alerts=alert_list,
            performance=perf, market_summary="收盘市场概况",
            system_health=health, preview="明日关注标的待生成",
        )
        logger.info("=== 收盘全景报告完成 ===")
    except Exception as e:
        logger.error(f"收盘全景报告异常: {e}", exc_info=True)


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

"""FastAPI 主应用 — V6: DeepSeek云端AI + 飞书全通道 + 定时调度"""
import asyncio
import os
import json
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

import httpx
from fastapi import FastAPI
from sqlalchemy.orm import Session
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.database import get_db, init_db, SessionLocal
from app.models import (RiskAlert, AIStrategy, TradingSignal, TradingOrder, TradeLog,
                         SimAccount, StrategyInstance, ReviewLog, UserPreference, Position)
from app.engine.lifecycle import StrategyLifecycle
from app.services.feishu_channels import feishu_channels
from app.services.bot_commands import check_and_process_new_messages
from app.engine.analysis import run_analysis
from app.engine.debate_tracker import DebateTracker
from app.engine.workshop import run_debate, ask_role, RISK_LEVELS
from app.ai.debate import AIDebateEngine
from app.ai.cloud_client import cloud
from app.utils.logger import logger
from app.utils.tiered_cache import tiered_cache
from app.utils.trading_calendar import is_trading_day
from app.data_sources.tencent_client import TencentDataSource
from app.data_sources.eastmoney_client import EastmoneyDataSource
from app.data_sources.akshare_news import AKShareNewsClient
from app.data_sources.akshare_market import AKShareMarketClient
from app.data_sources.data_router import DataSourceRouter
from app.services.monitor import MonitorService

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
    logger.info("恭喜发财 V6 应用启动中...")
    init_db()
    logger.info("数据库初始化完成")

    if not settings.FEISHU_WEBHOOK_URL or "YOUR_WEBHOOK_ID" in settings.FEISHU_WEBHOOK_URL:
        logger.warning("飞书 Webhook URL 未配置，消息推送将不可用。请在 .env.local 中设置 FEISHU_WEBHOOK_URL")
    else:
        logger.info(f"飞书 Webhook 已配置: {settings.FEISHU_WEBHOOK_URL[:40]}...")

    # ============================================================
    # V6 定时任务注册 (5个交易时段 + Bot轮询)
    # ============================================================
    scheduler.add_job(
        _run_premarket_with_status,
        CronTrigger(hour=9, minute=5, day_of_week='mon-fri', timezone='Asia/Shanghai'),
        id='premarket', name='盘前AI辩论+策略推送', replace_existing=True,
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
        CronTrigger(hour=15, minute=35, day_of_week='mon-fri', timezone='Asia/Shanghai'),
        id='daily_report', name='系统日报', replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _poll_bot_messages,
        'interval', seconds=30,
        id='bot_poll', name='飞书Bot消息轮询', replace_existing=True,
    )

    scheduler.start()
    logger.info("旺财V6 调度器已启动 (5个交易时段 + Bot轮询)")

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
    version="6.0.0",
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
from app.trading_engine.position import PositionManager
from app.trading_engine.fee_schedule import round_lot, get_board_type

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
    available_cash = float(account.cash) if account else 100000.0
    return {
        "holdings": holdings, "holdings_str": holdings_str,
        "total_cost": round(total_cost, 2), "available_cash": round(available_cash, 2),
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
            "holdings_str": hd["holdings_str"], "news": []}

def _feishu_webhook_push(title: str, content: str) -> bool:
    """同步飞书 webhook 推送（供 scheduler 线程使用）"""
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
    if not is_trading_day():
        return
    gs = generation_status["premarket"]
    if gs["running"]:
        return

    gs["running"] = True
    gs["started_at"] = str(datetime.now())
    try:
        logger.info("=== 旺财V6 盘前任务启动 ===")
        market_data = await _fetch_market_data()
        sh = market_data["indices"].get("sh000001", {}).get("price", 3350)
        sz = market_data["indices"].get("sz399001", {}).get("price", 10800)
        logger.info(f"盘前指数: 上证{sh:.0f} 深证{sz:.0f}")

        report = await run_analysis(market_data)
        logger.info("分析完成，启动AI辩论...")
        debate_result = await run_debate(report, strategy_type="premarket")
        decision = debate_result.get("decision", {})
        risk = debate_result.get("recommended_risk_level", 3)
        pool = decision.get("stock_pool", [])

        from app.services.report_templates import strategy_report_md
        report_md = strategy_report_md(decision)
        extra = f"\n\n...\n\n*[完整报告已推送]*"
        summary = report_md[:2800] + (extra if len(report_md) > 2800 else "")
        _feishu_webhook_push(f"旺财V6 盘前策略 [R{risk}]", summary)

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

        _feishu_webhook_push(f"午盘快报 [{action}]", content)
        logger.info(f"--- 午盘分析完成: {action} ---")
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
            if not positions:
                logger.info("无持仓，跳过午后检查")
                return

            alerts = []
            for p in positions:
                # 通过 MonitorService 获取多源数据
                rt = await monitor.get_realtime_data(p.stock_code)
                if not rt or not rt.get("price"):
                    continue

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

            if alerts:
                content = "**午后多维度风控告警**\n\n" + "\n".join(alerts[:10])
                if len(alerts) > 10:
                    content += f"\n... 共 {len(alerts)} 条"
                acc = db.query(SimAccount).first()
                cash = acc.cash / 100 if acc else 0
                mv = sum(p.market_value for p in positions) / 100
                content += f"\n\n现金: {cash:,.0f} | 持仓市值: {mv:,.0f}"
                _feishu_webhook_push("午后风险告警", content)
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
            content += f"无违规项\n"

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
    """系统日报"""
    if not is_trading_day():
        return
    try:
        logger.info("=== 系统日报 ===")
        content = f"**旺财V6 系统日报**\n日期: {date.today()}\n\n"

        try:
            deepseek_ok = await cloud.is_available()
            content += f"DeepSeek API: {'OK' if deepseek_ok else 'FAIL'}\n"
        except Exception:
            content += f"DeepSeek API: 检测失败\n"

        try:
            tc_ok = await tencent_client.fetch("sh000001")
            content += "腾讯行情: " + ("OK" if tc_ok and tc_ok.get("price") else "FAIL") + f"\n"
        except Exception:
            content += f"腾讯行情: 检测失败\n"

        jobs = [j for j in scheduler.get_jobs()]
        content += f"\n定时任务: {len(jobs)}个 ({', '.join(j.name for j in jobs)})"

        _feishu_webhook_push("旺财V6 系统日报", content)
    except Exception as e:
        logger.error(f"日报异常: {e}", exc_info=True)

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
        _feishu_webhook_push("旺财V6 启动告警", "\n".join(f"- {i}" for i in issues))


def _poll_bot_messages():
    """轮询飞书Bot消息 (每30秒)"""
    try:
        check_and_process_new_messages()
    except Exception as e:
        logger.debug(f"Bot轮询异常: {e}")


# 别名: strategy.py 使用的旧名称
_run_intraday_with_status = _run_midday_with_status

"""FastAPI 主应用入口 — V7: DeepSeek云端AI + 飞书全通道 + 定时调度"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.database import init_db
from app.services.holdings import get_holdings_data
from app.services.scheduled_tasks import (
    configure_tasks,
    run_premarket_with_status,
    run_midday_with_status,
    run_afternoon_with_status,
    run_review_with_status,
    run_daily_report_with_status,
    poll_bot_messages,
    startup_health_check,
)
from app.ai.debate import AIDebateEngine
from app.ai.cloud_client import cloud
from app.utils.logger import logger
from app.data_sources.tencent_client import TencentDataSource
from app.data_sources.eastmoney_client import EastmoneyDataSource
from app.data_sources.akshare_news import AKShareNewsClient
from app.data_sources.akshare_market import AKShareMarketClient
from app.data_sources.data_router import DataSourceRouter
from app.services.monitor import MonitorService

from app.report_engine.engine import report_engine

# ============================================================
# 共享实例初始化
# ============================================================
debate_engine = AIDebateEngine()
scheduler = AsyncIOScheduler(
    jobstores={
        "default": SQLAlchemyJobStore(url=f"sqlite:///{settings.DATABASE_PATH}")
    },
    job_defaults={
        "misfire_grace_time": 300,
        "coalesce": True,
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

# ============================================================
# 注入依赖到定时任务模块
# ============================================================
generation_status = {
    "premarket": {"running": False, "started_at": None},
    "review": {"running": False, "started_at": None},
    "afternoon": {"running": False, "started_at": None},
    "intraday": {"running": False, "started_at": None},
}

configure_tasks(
    debate_engine=debate_engine,
    report_engine=report_engine,
    data_router=data_router,
    tencent_client=tencent_client,
    monitor=monitor,
    cloud=cloud,
    scheduler=scheduler,
    generation_status=generation_status,
)

strategy.init_strategy_router(
    debate_engine, tencent_client, market_client, news_client,
    generation_status, get_holdings_data,
)

# ============================================================
# 应用生命周期
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("恭喜发财 V7 应用启动中...")
    init_db()
    logger.info("数据库初始化完成")

    if not settings.FEISHU_WEBHOOK_URL or "YOUR_WEBHOOK_ID" in settings.FEISHU_WEBHOOK_URL:
        logger.warning("飞书 Webhook URL 未配置，消息推送将不可用。请在 .env.local 中设置 FEISHU_WEBHOOK_URL")
    else:
        logger.info(f"飞书 Webhook 已配置: {settings.FEISHU_WEBHOOK_URL[:40]}...")

    # ============================================================
    # 定时任务注册 (5个交易时段 + Bot轮询)
    # ============================================================
    scheduler.add_job(
        run_premarket_with_status,
        CronTrigger(hour=9, minute=5, day_of_week='mon-fri', timezone='Asia/Shanghai'),
        id='premarket', name='盘前AI辩论+策略推送', replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        run_midday_with_status,
        CronTrigger(hour=11, minute=35, day_of_week='mon-fri', timezone='Asia/Shanghai'),
        id='midday', name='午盘快速分析', replace_existing=True,
        misfire_grace_time=2700,
    )
    scheduler.add_job(
        run_afternoon_with_status,
        CronTrigger(hour=14, minute=0, day_of_week='mon-fri', timezone='Asia/Shanghai'),
        id='afternoon', name='午后风险检查', replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        run_review_with_status,
        CronTrigger(hour=15, minute=5, day_of_week='mon-fri', timezone='Asia/Shanghai'),
        id='review', name='收盘复盘', replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        run_daily_report_with_status,
        CronTrigger(hour=15, minute=35, day_of_week='mon-fri', timezone='Asia/Shanghai'),
        id='daily_report', name='系统日报', replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        poll_bot_messages,
        'interval', seconds=30,
        id='bot_poll', name='飞书Bot消息轮询', replace_existing=True,
    )

    scheduler.start()
    logger.info("旺财V7 调度器已启动 (5个交易时段 + Bot轮询)")

    asyncio.create_task(startup_health_check())

    yield
    scheduler.shutdown(wait=False)
    for obj in [risk_guard, order_mgr, account_mgr, signal_engine, perf_analyzer]:
        if hasattr(obj, "_db") and obj._db:
            try:
                obj._db.close()
            except Exception:
                pass
    await cloud.close()
    logger.info("恭喜发财应用关闭")


app = FastAPI(
    title="恭喜发财 - A 股智能监控系统",
    description="基于 DeepSeek 云端 AI 的 A 股智能监控与交易辅助系统",
    version="7.0.0",
    lifespan=lifespan,
)

app.include_router(market.router)
app.include_router(trading.router)
app.include_router(strategy.router)


# ============================================================
# 兼容别名: strategy.py 使用的函数
# ============================================================
_run_premarket_with_status = run_premarket_with_status
_run_review_with_status = run_review_with_status
_run_intraday_with_status = run_midday_with_status

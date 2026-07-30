"""Central APScheduler registration for the production workflow."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from apscheduler.triggers.cron import CronTrigger

from app.version import PRODUCT_VERSION


JobCallable = Callable[..., Any]
MARKET_TIMEZONE = "Asia/Shanghai"


@dataclass(frozen=True)
class SchedulerJobHandlers:
    """Injected job callables keep scheduler registration dependency-free."""

    premarket: JobCallable
    midday: JobCallable
    afternoon: JobCallable
    intraday_alert_scan: JobCallable
    review: JobCallable
    prediction_lab: JobCallable
    sentinel_research: JobCallable
    main_report: JobCallable
    sentinel_review: JobCallable
    bot_poll: JobCallable
    yitaojin_morning: JobCallable
    yitaojin_quotes: JobCallable
    yitaojin_close_account: JobCallable
    yitaojin_evening: JobCallable
    opportunity_recovery: JobCallable | None = None
    opportunity_delivery_verify: JobCallable | None = None


@dataclass(frozen=True)
class _CronJob:
    handler: str
    job_id: str
    name: str
    hour: int | str
    minute: int | str
    day_of_week: str | None
    misfire_grace_time: int
    bounded: bool = False


CRON_JOBS = (
    _CronJob(
        "premarket",
        "premarket",
        "盘前短策略校准",
        8,
        50,
        "mon-fri",
        3600,
    ),
    _CronJob(
        "yitaojin_morning",
        "yitaojin_morning",
        "易淘金盘前账户、自选与重点行情",
        8,
        55,
        "mon-fri",
        300,
        True,
    ),
    _CronJob(
        "intraday_alert_scan",
        "intraday_alert_scan",
        "盘中事件触发扫描",
        "9-11,13-14",
        "*/5",
        "mon-fri",
        120,
    ),
    _CronJob(
        "midday",
        "midday",
        "午盘快速分析",
        11,
        35,
        "mon-fri",
        2700,
    ),
    _CronJob(
        "yitaojin_quotes",
        "yitaojin_midday_quotes",
        "易淘金午间重点行情校验",
        11,
        35,
        "mon-fri",
        120,
        True,
    ),
    _CronJob(
        "afternoon",
        "afternoon",
        "午后风险检查",
        14,
        0,
        "mon-fri",
        3600,
        True,
    ),
    _CronJob(
        "yitaojin_quotes",
        "yitaojin_close_quotes",
        "易淘金收盘前重点行情校验",
        14,
        55,
        "mon-fri",
        120,
        True,
    ),
    _CronJob(
        "review",
        "review",
        "收盘复盘",
        15,
        5,
        "mon-fri",
        3600,
    ),
    _CronJob(
        "yitaojin_close_account",
        "yitaojin_close_account",
        "易淘金收盘账户快照",
        15,
        10,
        "mon-fri",
        900,
        True,
    ),
    _CronJob(
        "prediction_lab",
        "prediction_lab",
        "预测账本采集与到期评估",
        15,
        25,
        "mon-fri",
        3600,
    ),
    _CronJob(
        "sentinel_research",
        "sentinel_research",
        "Sentinel研究包与Serenity深挖",
        20,
        0,
        "mon-fri,sun",
        3600,
    ),
    _CronJob(
        "main_report",
        "main_report",
        "次日投资策略主报告",
        20,
        30,
        "mon-fri",
        3600,
        True,
    ),
    _CronJob(
        "main_report",
        "sunday_main_report",
        "周日晚次日投资策略主报告",
        20,
        30,
        "sun",
        3600,
        True,
    ),
    _CronJob(
        "yitaojin_evening",
        "yitaojin_evening",
        "易淘金晚间账户与自选同步",
        20,
        45,
        "mon-fri",
        900,
        True,
    ),
    _CronJob(
        "sentinel_review",
        "sentinel_review",
        "Sentinel绩效回看与归档",
        21,
        0,
        None,
        3600,
    ),
    _CronJob(
        "opportunity_recovery",
        "opportunity_recovery",
        "v9机会管线断点恢复",
        20,
        45,
        "mon-fri,sun",
        1800,
        True,
    ),
    _CronJob(
        "opportunity_delivery_verify",
        "opportunity_delivery_verify",
        "v9策略报告交付验真",
        21,
        15,
        "mon-fri,sun",
        1800,
        True,
    ),
)


def register_scheduler_jobs(
    scheduler: Any,
    handlers: SchedulerJobHandlers,
) -> None:
    """Register the complete, unique scheduler contract."""
    for spec in CRON_JOBS:
        trigger_options: dict[str, Any] = {
            "hour": spec.hour,
            "minute": spec.minute,
            "timezone": MARKET_TIMEZONE,
        }
        if spec.day_of_week is not None:
            trigger_options["day_of_week"] = spec.day_of_week
        job_options: dict[str, Any] = {
            "id": spec.job_id,
            "name": spec.name,
            "replace_existing": True,
            "misfire_grace_time": spec.misfire_grace_time,
        }
        if spec.bounded:
            job_options.update(max_instances=1, coalesce=True)
        handler = getattr(handlers, spec.handler)
        if handler is None:
            continue
        scheduler.add_job(
            handler,
            CronTrigger(**trigger_options),
            **job_options,
        )

    scheduler.add_job(
        handlers.bot_poll,
        "interval",
        seconds=30,
        id="bot_poll",
        name="飞书Bot消息轮询",
        replace_existing=True,
    )


def start_scheduler_service(
    scheduler: Any,
    handlers: SchedulerJobHandlers,
    *,
    logger: Any | None = None,
) -> None:
    """Register, start, then remove obsolete persisted job ids."""
    register_scheduler_jobs(scheduler, handlers)
    scheduler.start()
    for stale_job_id in ("daily_report",):
        try:
            scheduler.remove_job(stale_job_id)
            if logger is not None:
                logger.info(f"已清理旧调度任务: {stale_job_id}")
        except Exception:
            pass
    if logger is not None:
        logger.info(
            f"恭喜发财 {PRODUCT_VERSION} 调度器已启动 "
            "(次日主报告 + 断点恢复 + 交付验真 + 盘前校准 + 盘中5分钟事件扫描 + "
            "预测账本 + Sentinel研究/复盘 + 易淘金受控桥接 + Bot轮询)"
        )

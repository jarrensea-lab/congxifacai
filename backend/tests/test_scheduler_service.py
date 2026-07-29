import ast
from pathlib import Path


EXPECTED_JOB_IDS = {
    "premarket",
    "midday",
    "afternoon",
    "intraday_alert_scan",
    "review",
    "prediction_lab",
    "sentinel_research",
    "main_report",
    "sunday_main_report",
    "sentinel_review",
    "bot_poll",
    "yitaojin_morning",
    "yitaojin_midday_quotes",
    "yitaojin_close_quotes",
    "yitaojin_close_account",
    "yitaojin_evening",
}


class FakeScheduler:
    def __init__(self, *, fail_stale_cleanup=False):
        self.jobs = []
        self.events = []
        self.fail_stale_cleanup = fail_stale_cleanup

    def add_job(self, function, trigger, **kwargs):
        self.jobs.append((function, trigger, kwargs))
        self.events.append(f"add:{kwargs['id']}")

    def start(self):
        self.events.append("start")

    def remove_job(self, job_id):
        self.events.append(f"remove:{job_id}")
        if self.fail_stale_cleanup:
            raise LookupError(job_id)


class FakeLogger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(message)


def _handlers():
    from app.services.scheduler_service import SchedulerJobHandlers

    functions = {
        name: (lambda marker=name: marker)
        for name in (
            "premarket",
            "midday",
            "afternoon",
            "intraday_alert_scan",
            "review",
            "prediction_lab",
            "sentinel_research",
            "main_report",
            "sentinel_review",
            "bot_poll",
            "yitaojin_morning",
            "yitaojin_quotes",
            "yitaojin_close_account",
            "yitaojin_evening",
        )
    }
    return SchedulerJobHandlers(**functions), functions


def _cron_fields(trigger):
    return {field.name: str(field) for field in trigger.fields}


def test_register_scheduler_jobs_has_complete_unique_job_contract():
    from app.services.scheduler_service import register_scheduler_jobs

    scheduler = FakeScheduler()
    handlers, functions = _handlers()

    register_scheduler_jobs(scheduler, handlers)

    ids = [kwargs["id"] for _, _, kwargs in scheduler.jobs]
    assert set(ids) == EXPECTED_JOB_IDS
    assert len(ids) == len(set(ids)) == len(EXPECTED_JOB_IDS)
    assert all(kwargs["replace_existing"] is True for _, _, kwargs in scheduler.jobs)
    by_id = {
        kwargs["id"]: (function, trigger, kwargs)
        for function, trigger, kwargs in scheduler.jobs
    }
    assert by_id["main_report"][0] is functions["main_report"]
    assert by_id["sunday_main_report"][0] is functions["main_report"]
    assert by_id["yitaojin_midday_quotes"][0] is functions["yitaojin_quotes"]
    assert by_id["yitaojin_close_quotes"][0] is functions["yitaojin_quotes"]


def test_scheduler_evening_sequence_and_timezone_are_explicit():
    from app.services.scheduler_service import register_scheduler_jobs

    scheduler = FakeScheduler()
    handlers, _ = _handlers()
    register_scheduler_jobs(scheduler, handlers)
    by_id = {
        kwargs["id"]: (trigger, kwargs)
        for _, trigger, kwargs in scheduler.jobs
    }

    expected = {
        "sentinel_research": {
            "hour": "20",
            "minute": "0",
            "day_of_week": "mon-fri,sun",
        },
        "main_report": {
            "hour": "20",
            "minute": "30",
            "day_of_week": "mon-fri",
        },
        "sunday_main_report": {
            "hour": "20",
            "minute": "30",
            "day_of_week": "sun",
        },
        "yitaojin_evening": {
            "hour": "20",
            "minute": "45",
            "day_of_week": "mon-fri",
        },
        "sentinel_review": {
            "hour": "21",
            "minute": "0",
            "day_of_week": "*",
        },
    }
    for job_id, fields in expected.items():
        trigger, _ = by_id[job_id]
        observed = _cron_fields(trigger)
        assert {key: observed[key] for key in fields} == fields
        assert str(trigger.timezone) == "Asia/Shanghai"

    ids = [kwargs["id"] for _, _, kwargs in scheduler.jobs]
    assert ids.index("sentinel_research") < ids.index("main_report")
    assert ids.index("main_report") < ids.index("yitaojin_evening")
    assert ids.index("yitaojin_evening") < ids.index("sentinel_review")


def test_scheduler_preserves_job_options_bot_interval_and_yitaojin_bounds():
    from app.services.scheduler_service import register_scheduler_jobs

    scheduler = FakeScheduler()
    handlers, _ = _handlers()
    register_scheduler_jobs(scheduler, handlers)
    by_id = {
        kwargs["id"]: (trigger, kwargs)
        for _, trigger, kwargs in scheduler.jobs
    }

    expected_misfire = {
        "premarket": 3600,
        "midday": 2700,
        "afternoon": 3600,
        "intraday_alert_scan": 120,
        "review": 3600,
        "prediction_lab": 3600,
        "sentinel_research": 3600,
        "main_report": 3600,
        "sunday_main_report": 3600,
        "sentinel_review": 3600,
        "yitaojin_morning": 300,
        "yitaojin_midday_quotes": 120,
        "yitaojin_close_quotes": 120,
        "yitaojin_close_account": 900,
        "yitaojin_evening": 900,
    }
    assert {
        job_id: by_id[job_id][1]["misfire_grace_time"]
        for job_id in expected_misfire
    } == expected_misfire

    bot_trigger, bot_options = by_id["bot_poll"]
    assert bot_trigger == "interval"
    assert bot_options["seconds"] == 30
    for job_id in (
        "yitaojin_morning",
        "yitaojin_midday_quotes",
        "yitaojin_close_quotes",
        "yitaojin_close_account",
        "yitaojin_evening",
    ):
        assert by_id[job_id][1]["max_instances"] == 1
        assert by_id[job_id][1]["coalesce"] is True


def test_start_scheduler_service_registers_starts_then_cleans_stale_job():
    from app.services.scheduler_service import start_scheduler_service

    scheduler = FakeScheduler()
    logger = FakeLogger()
    handlers, _ = _handlers()

    start_scheduler_service(scheduler, handlers, logger=logger)

    assert scheduler.events[-2:] == ["start", "remove:daily_report"]
    assert scheduler.events.count("start") == 1
    assert any("daily_report" in message for message in logger.messages)
    assert any("v8.2.0-dev" in message for message in logger.messages)


def test_stale_job_absence_does_not_abort_scheduler_start():
    from app.services.scheduler_service import start_scheduler_service

    scheduler = FakeScheduler(fail_stale_cleanup=True)
    handlers, _ = _handlers()

    start_scheduler_service(scheduler, handlers, logger=FakeLogger())

    assert scheduler.events[-2:] == ["start", "remove:daily_report"]


def test_scheduler_service_has_no_reverse_import_of_main():
    source = Path(
        "backend/app/services/scheduler_service.py"
    ).read_text(encoding="utf-8")

    assert "app.main" not in source


def test_main_lifespan_uses_only_unified_scheduler_start_entry():
    source = Path("backend/app/main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    lifespan = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan"
    )
    function_source = ast.get_source_segment(source, lifespan) or ""

    assert function_source.count("start_scheduler_service(") == 1
    assert ".add_job(" not in function_source
    assert "register_yitaojin_jobs" not in source
    assert "恭喜发财 V7 应用启动中" not in source

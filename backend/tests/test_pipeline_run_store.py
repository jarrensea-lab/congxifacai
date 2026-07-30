def test_pipeline_store_resumes_failed_stage_without_duplicate_run(tmp_path):
    from app.services.pipeline_run_store import PipelineRunStore

    store = PipelineRunStore(tmp_path / "pipeline.db")
    run = store.begin_run("2026-07-31")
    first = store.start_stage(
        run["run_id"],
        "market_discovery",
        input_count=5200,
    )
    store.finish_stage(
        run["run_id"],
        "market_discovery",
        attempt=first["attempt"],
        status="failed",
        output_count=0,
        error_code="empty_universe",
    )

    resumed = store.resume_stage(run["run_id"], "market_discovery")

    assert resumed["attempt"] == 2
    assert resumed["status"] == "running"
    assert store.begin_run("2026-07-31")["run_id"] == run["run_id"]


def test_older_failure_cannot_overwrite_newer_success(tmp_path):
    from app.services.pipeline_run_store import PipelineRunStore

    store = PipelineRunStore(tmp_path / "pipeline.db")
    run_id = store.begin_run("2026-07-31")["run_id"]
    first = store.start_stage(run_id, "delivery")
    second = store.resume_stage(run_id, "delivery")
    store.finish_stage(
        run_id,
        "delivery",
        attempt=second["attempt"],
        status="succeeded",
    )

    result = store.finish_stage(
        run_id,
        "delivery",
        attempt=first["attempt"],
        status="failed",
    )

    assert result["status"] == "succeeded"
    assert store.get_stage(run_id, "delivery")["status"] == "succeeded"


def test_latest_summary_never_calls_partial_run_succeeded(tmp_path):
    from app.services.pipeline_run_store import PipelineRunStore

    store = PipelineRunStore(tmp_path / "pipeline.db")
    run_id = store.begin_run("2026-07-31")["run_id"]
    stage = store.start_stage(run_id, "account_snapshot")
    store.finish_stage(
        run_id,
        "account_snapshot",
        attempt=stage["attempt"],
        status="succeeded",
    )

    summary = store.latest_run_summary(
        ("account_snapshot", "market_discovery")
    )

    assert summary["status"] == "running"
    assert summary["missing_stages"] == ["market_discovery"]


def test_incomplete_stages_preserves_expected_order(tmp_path):
    from app.services.pipeline_run_store import PipelineRunStore

    store = PipelineRunStore(tmp_path / "pipeline.db")
    run_id = store.begin_run("2026-07-31")["run_id"]
    stage = store.start_stage(run_id, "account_snapshot")
    store.finish_stage(
        run_id,
        "account_snapshot",
        attempt=stage["attempt"],
        status="succeeded",
    )

    assert store.incomplete_stages(
        run_id,
        ("account_snapshot", "market_discovery", "delivery"),
    ) == ["market_discovery", "delivery"]

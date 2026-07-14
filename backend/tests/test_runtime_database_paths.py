from pathlib import Path


def test_runtime_database_defaults_live_on_local_home_disk(monkeypatch, tmp_path):
    from app.config import resolve_runtime_database_paths

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CONGXI_STATE_DIR", raising=False)
    monkeypatch.delenv("CONGXI_DATABASE_PATH", raising=False)
    monkeypatch.delenv("CONGXI_SCHEDULER_DATABASE_PATH", raising=False)

    paths = resolve_runtime_database_paths()

    expected_root = tmp_path / "Library" / "Application Support" / "congxicai-v7"
    assert Path(paths.business) == expected_root / "stock_data.db"
    assert Path(paths.scheduler) == expected_root / "scheduler_jobs.db"
    assert paths.business != paths.scheduler


def test_runtime_database_environment_overrides_are_respected(monkeypatch, tmp_path):
    from app.config import resolve_runtime_database_paths

    business = tmp_path / "business" / "stock.db"
    scheduler = tmp_path / "scheduler" / "jobs.db"
    monkeypatch.setenv("CONGXI_DATABASE_PATH", str(business))
    monkeypatch.setenv("CONGXI_SCHEDULER_DATABASE_PATH", str(scheduler))

    paths = resolve_runtime_database_paths()

    assert Path(paths.business) == business
    assert Path(paths.scheduler) == scheduler


def test_runtime_database_state_directory_override_sets_both_paths(monkeypatch, tmp_path):
    from app.config import resolve_runtime_database_paths

    state_dir = tmp_path / "runtime-state"
    monkeypatch.setenv("CONGXI_STATE_DIR", str(state_dir))
    monkeypatch.delenv("CONGXI_DATABASE_PATH", raising=False)
    monkeypatch.delenv("CONGXI_SCHEDULER_DATABASE_PATH", raising=False)

    paths = resolve_runtime_database_paths()

    assert Path(paths.business) == state_dir / "stock_data.db"
    assert Path(paths.scheduler) == state_dir / "scheduler_jobs.db"


def test_business_engine_and_scheduler_use_distinct_resolved_paths():
    database_source = Path("backend/app/database.py").read_text(encoding="utf-8")
    main_source = Path("backend/app/main.py").read_text(encoding="utf-8")

    assert "settings.DATABASE_PATH" in database_source
    assert "settings.SCHEDULER_DATABASE_PATH" in main_source
    assert 'SQLAlchemyJobStore(url=f"sqlite:///{settings.DATABASE_PATH}")' not in main_source


def test_pytest_isolates_both_business_and_scheduler_databases():
    conftest_source = Path("backend/tests/conftest.py").read_text(encoding="utf-8")

    assert 'os.environ["CONGXI_DATABASE_PATH"]' in conftest_source
    assert 'os.environ["CONGXI_SCHEDULER_DATABASE_PATH"]' in conftest_source


def test_launchd_wrapper_exports_local_state_directory():
    installer_source = Path("scripts/install-congxicai-v7-launchd.sh").read_text(
        encoding="utf-8"
    )
    plist_source = Path("scripts/com.zhuchenyuan.congxicai-v7.plist").read_text(
        encoding="utf-8"
    )

    assert 'CONGXI_STATE_DIR="${CONGXI_STATE_DIR:-${HOME}/Library/Application Support/congxicai-v7}"' in installer_source
    assert 'export CONGXI_STATE_DIR' in installer_source
    assert '<key>CONGXI_STATE_DIR</key>' in plist_source


def test_example_environment_documents_runtime_database_paths():
    example_source = Path(".env.example").read_text(encoding="utf-8")

    assert "CONGXI_STATE_DIR=" in example_source
    assert "CONGXI_DATABASE_PATH=" in example_source
    assert "CONGXI_SCHEDULER_DATABASE_PATH=" in example_source

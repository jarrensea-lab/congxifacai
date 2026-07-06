from pathlib import Path

from scripts.inspect_a_share_base_data import build_inspection_report
from backend.tests.test_market_state_store import _build_base_data


def test_build_inspection_report_summarizes_base_package(tmp_path):
    root = _build_base_data(tmp_path)
    report = build_inspection_report(root, samples=[("000001", "2024-04-26", 10.98)])

    assert report["base_dir"] == str(root)
    assert report["files"]["stock_master"]["rows"] == 2
    assert report["files"]["delisted_master"]["rows"] == 1
    assert report["daily_dirs"]["st_daily_files"] == 1
    assert report["samples"][0]["code"] == "000001.SZ"
    assert report["samples"][0]["tradable"] is True

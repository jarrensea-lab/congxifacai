from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _current_changelog() -> str:
    changelog = _read("CHANGELOG.md")
    return changelog.split("\n## v8.2.0-dev", 1)[0]


def test_current_docs_cover_the_complete_long_thesis_state_machine():
    documents = (_read("README.md"), _read("ROADMAP.md"), _current_changelog())
    states = ("unknown", "forming", "healthy", "stale", "weakened", "broken")

    for document in documents:
        assert all(f"`{state}`" in document for state in states)
        assert "不等于交易授权" in document


def test_current_docs_distinguish_successful_fallback_from_blocking_failures():
    documents = (_read("README.md"), _read("ROADMAP.md"), _current_changelog())

    for document in documents:
        assert "fallback 成功" in document
        assert "validator" in document
        assert "角色/裁判" in document
        assert "runtime `degraded`" in document

    assert "结果降级时不写入生产候选池" not in _read("README.md")
    assert "结果降级时不写入生产候选池" not in _read("ROADMAP.md")
    assert "结果降级时不写入生产候选池" not in _current_changelog()


def test_current_docs_preserve_the_legacy_report_compatibility_path():
    documents = (_read("README.md"), _read("ROADMAP.md"), _current_changelog())

    for document in documents:
        assert "`CONGXI_REPORT_LEGACY_SECTIONS=1`" in document
        assert "legacy" in document

    assert "只负责取数、编排和落盘" not in _read("README.md")
    assert "只负责取数、编排和落盘" not in _read("ROADMAP.md")
    assert "不再维护重复的大段报告模板" not in _current_changelog()

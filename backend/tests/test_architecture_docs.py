from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _current_documents() -> tuple[str, str, str]:
    changelog = _read("CHANGELOG.md").split("\n## v8.2.0-dev", 1)[0]
    return _read("README.md"), _read("ROADMAP.md"), changelog


def test_long_thesis_empty_state_and_unknown_are_distinct_in_code_and_docs():
    from app.services.target_scoring import score_long_quality
    from scripts.daily_report import _long_horizon_view, _thesis_status_label

    empty = score_long_quality({"quote": {}}, None)

    assert empty["thesis_status"] == ""
    unknown = score_long_quality(
        {"quote": {}},
        {"thesis_status": "unknown", "quality_score": 88},
    )
    assert unknown["thesis_status"] == "unknown"
    assert unknown["long_quality_score"] == 0
    assert unknown["long_horizon_reason"] == "thesis_status_unknown"
    assert _thesis_status_label(empty["thesis_status"]) == "未建论文"
    assert _thesis_status_label("unknown") == "论文状态未知"
    assert _thesis_status_label("") != _thesis_status_label("unknown")
    report_rows = _long_horizon_view({
        "target_scores": [{
            "code": "000001",
            "name": "平安银行",
            **empty,
        }],
    })
    assert report_rows[0]["thesis_status"] == "未建论文"

    explicit_states = (
        "unknown",
        "forming",
        "healthy",
        "stale",
        "weakened",
        "broken",
    )
    for document in _current_documents():
        assert '空 `thesis_status == ""`' in document
        assert all(f"`{state}`" in document for state in explicit_states)
        assert "`unknown` 表示已有上下文但状态无法可靠判定" in document
        assert "不等于交易授权" in document
        assert "`unknown` 表示无 thesis" not in document
        assert "必须按失败关闭处理" not in document

    readme = _read("README.md")
    assert "`unknown` 不提供长期加分或交易授权" in readme
    assert "不会单独阻断已通过全部独立短线硬门的 tactical buy" in readme
    assert "长期状态中只有 `broken` 会直接阻断买入" in readme


def test_production_gate_allows_usable_fallback_and_blocks_unusable_outputs():
    from app.engine.workshop import build_production_gate

    successful_fallback = {
        "quality": {"pass": True, "score": 90},
        "debate": {"Hunter": {"analysis": "可用观点"}},
        "final": {"final_decision": "观望"},
        "model_runtime_status": {
            "status": "degraded",
            "degradation_reasons": ["qwen_api_key_missing"],
            "calls": [
                {
                    "role": "猎手",
                    "provider": "DeepSeek",
                    "requested_provider": "DeepSeek",
                    "status": "success",
                    "output_usable": True,
                },
                {
                    "role": "Serenity·研究员",
                    "provider": "DeepSeek",
                    "requested_provider": "Qwen",
                    "status": "degraded",
                    "fallback_reason": "qwen_api_key_missing",
                    "output_usable": True,
                },
                {
                    "role": "裁判",
                    "provider": "DeepSeek",
                    "requested_provider": "Qwen",
                    "status": "degraded",
                    "fallback_reason": "qwen_api_key_missing",
                    "output_usable": True,
                },
                {
                    "role": "输出校验",
                    "provider": "DeepSeek",
                    "requested_provider": "Qwen",
                    "status": "degraded",
                    "fallback_reason": "qwen_api_key_missing",
                    "output_usable": True,
                },
            ],
        },
    }
    assert build_production_gate(successful_fallback)["allowed"] is True

    degraded_role = {
        **successful_fallback,
        "debate": {"Hunter": {"degraded": True}},
    }
    role_gate = build_production_gate(degraded_role)
    assert role_gate["allowed"] is False
    assert "degraded_role_output" in role_gate["reasons"]

    missing_validator = {
        **successful_fallback,
        "model_runtime_status": {
            "status": "degraded",
            "calls": [],
        },
    }
    validator_gate = build_production_gate(missing_validator)
    assert validator_gate["allowed"] is False
    assert "validator_route_missing" in validator_gate["reasons"]

    unusable_validator = {
        **successful_fallback,
        "model_runtime_status": {
            "status": "degraded",
            "calls": [{
                "role": "输出校验",
                "provider": "",
                "requested_provider": "Qwen",
                "status": "degraded",
                "output_usable": False,
            }],
        },
    }
    unusable_validator_gate = build_production_gate(unusable_validator)
    assert unusable_validator_gate["allowed"] is False
    assert "validator_route_degraded" in unusable_validator_gate["reasons"]

    failed_quality = {
        **successful_fallback,
        "quality": {"pass": False, "score": 0},
    }
    quality_gate = build_production_gate(failed_quality)
    assert quality_gate["allowed"] is False
    assert "quality_check_failed" in quality_gate["reasons"]

    for document in _current_documents():
        assert "fallback 成功" in document
        assert "runtime `degraded`" in document
        assert "角色/裁判" in document
        assert "validator" in document


def test_default_report_delegates_renderer_and_legacy_is_exact_opt_in(
    monkeypatch,
):
    import scripts.daily_report as daily_report

    captured = []

    def fake_renderer(view):
        captured.append(view)
        return ["delegated"]

    monkeypatch.setattr(
        daily_report,
        "render_next_day_sections",
        fake_renderer,
    )
    result = daily_report.build_next_day_strategy_sections(
        report_date="2026-07-26",
        target_date="2026-07-27",
        risk_level=3,
        final_view="观望",
        confidence=5,
        positions=[],
        available_cash=3000,
        total_assets=3000,
        market_data={},
        analysis_report={},
        decision={},
        roles={},
        sentinel_package=None,
    )
    assert result == ["delegated"]
    assert len(captured) == 1

    lines = []
    built = []
    monkeypatch.delenv("CONGXI_REPORT_LEGACY_SECTIONS", raising=False)
    assert daily_report._legacy_report_sections_enabled() is False
    assert daily_report._append_new_report_sections(
        lines,
        legacy_mode=daily_report._legacy_report_sections_enabled(),
        builder=lambda: built.append(True) or ["default"],
    ) is True
    assert built == [True]
    assert lines == ["default"]

    monkeypatch.setenv("CONGXI_REPORT_LEGACY_SECTIONS", "1")
    assert daily_report._legacy_report_sections_enabled() is True
    assert daily_report._append_new_report_sections(
        lines,
        legacy_mode=daily_report._legacy_report_sections_enabled(),
        builder=lambda: built.append(True) or ["unexpected"],
    ) is False
    assert built == [True]

    monkeypatch.setenv("CONGXI_REPORT_LEGACY_SECTIONS", "true")
    assert daily_report._legacy_report_sections_enabled() is False

    for document in _current_documents():
        assert "`CONGXI_REPORT_LEGACY_SECTIONS=1`" in document
        assert "默认动作优先渲染" in document
        assert "legacy" in document

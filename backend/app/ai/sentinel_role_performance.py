"""Sentinel role prediction and advice performance review helpers.

This module is intentionally file-based and旁路. It records and evaluates the
quality of debate roles without changing trading behavior, role weights, prompts,
or account state.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List


DEFAULT_OUTPUT_ROOT = Path("data/sentinel")
DEFAULT_HORIZONS = ("1d", "3d", "5d", "20d")
ROLES = ("hunter", "accountant", "guardian", "researcher", "judge")
ROLE_LABELS = {
    "hunter": "猎手",
    "accountant": "账房",
    "guardian": "守夜人",
    "researcher": "Serenity",
    "judge": "裁判",
}
FORBIDDEN_TRADING_ACTIONS = {"buy", "sell", "clear", "all_in"}
ALLOWED_ADJUSTMENT_ACTIONS = {
    "increase_weight",
    "decrease_weight",
    "keep_weight",
    "watch_role",
    "review_prompt",
    "require_human_review",
}
SECRET_FIELD_NAMES = {
    "authorization",
    "authorization_header",
    "cookie",
    "cookies",
    "token",
    "api_key",
    "apikey",
    "secret",
    "password",
    "webhook",
}


def _today_iso() -> str:
    return date.today().isoformat()


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _safe_json(nested)
            for key, nested in value.items()
            if str(key).lower() not in SECRET_FIELD_NAMES
        }
    if isinstance(value, list):
        return [_safe_json(item) for item in value]
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _stable_id(payload: Dict[str, Any]) -> str:
    digest = hashlib.sha1(_json_dumps(payload).encode("utf-8")).hexdigest()
    return f"sentinel:{digest[:20]}"


def _append_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [_json_dumps(record) for record in records]
    if not lines:
        return
    with path.open("a", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _confidence(value: Any, default: float = 5) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _target_for_role(role: str, role_data: Dict[str, Any], decision: Dict[str, Any]) -> str:
    if role == "guardian":
        return "portfolio_risk"
    if role == "judge":
        return "final_strategy"
    recommendations = role_data.get("recommendations", [])
    if isinstance(recommendations, list):
        codes = [str(item.get("code")) for item in recommendations if isinstance(item, dict) and item.get("code")]
        if codes:
            return ",".join(codes[:5])
    sectors = role_data.get("sector_focus") or decision.get("top_sectors") or []
    if isinstance(sectors, list) and sectors:
        return ",".join(str(item) for item in sectors[:5])
    return "market"


def _prediction_type_for_role(role: str) -> str:
    if role == "guardian":
        return "risk_review"
    if role == "judge":
        return "final_strategy"
    if role == "researcher":
        return "industry_chain"
    return "recommendation"


def _expected_direction(role: str, role_data: Dict[str, Any], decision: Dict[str, Any]) -> str:
    text = _json_dumps(role_data) + _json_dumps(decision if role == "judge" else {})
    if any(word in text for word in ("规避", "减仓", "卖出", "风险", "观望")) and role in {"guardian", "judge"}:
        return "risk_control"
    if any(word in text for word in ("买入", "关注", "看好", "突破", "修复")):
        return "up"
    if any(word in text for word in ("看空", "下跌", "走弱")):
        return "down"
    return "neutral"


def record_debate_predictions(
    debate_result: Dict[str, Any],
    *,
    decision: Dict[str, Any] | None = None,
    prediction_date: str | None = None,
    strategy_type: str = "daily",
    source_report: str = "",
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    persist: bool = True,
) -> List[Dict[str, Any]]:
    """Extract role predictions from one debate result and optionally append JSONL."""
    day = prediction_date or _today_iso()
    decision = decision or debate_result.get("final", {}) or {}
    debate = debate_result.get("debate", {}) or {}
    records: List[Dict[str, Any]] = []

    for role in ROLES:
        role_data = decision if role == "judge" else debate.get(role, {})
        if not isinstance(role_data, dict):
            role_data = {"raw": str(role_data)}
        clean_role_data = _safe_json(role_data)
        base = {
            "role": role,
            "role_label": ROLE_LABELS[role],
            "prediction_date": day,
            "strategy_type": strategy_type,
            "prediction_type": _prediction_type_for_role(role),
            "target": _target_for_role(role, clean_role_data, decision),
            "expected_direction": _expected_direction(role, clean_role_data, decision),
            "confidence": _confidence(
                clean_role_data.get("conviction", clean_role_data.get("confidence", decision.get("confidence")))
            ),
            "source_report": source_report,
            "created_at": _now_iso(),
            "input_summary": {
                "sectors": clean_role_data.get("sector_focus", decision.get("top_sectors", [])),
                "recommendations": clean_role_data.get("recommendations", decision.get("stock_pool", [])),
                "risk_summary": clean_role_data.get("risk_summary", decision.get("risk_summary", "")),
            },
            "raw_excerpt": str(clean_role_data.get("analysis") or clean_role_data.get("reasoning") or "")[:1200],
        }
        for horizon in DEFAULT_HORIZONS:
            record = {**base, "horizon": horizon}
            record["id"] = _stable_id(record)
            records.append(record)

    if persist:
        output_path = Path(output_root) / "role_predictions" / f"{day}.jsonl"
        _append_jsonl(output_path, records)
    return records


def _parse_horizon_days(horizon: str) -> int:
    text = str(horizon).strip().lower()
    if text.endswith("d"):
        return int(text[:-1])
    return int(text)


def _parse_date(value: str) -> date:
    return datetime.fromisoformat(str(value)).date()


def evaluate_prediction(prediction: Dict[str, Any], actuals: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate one prediction against an actual market/outcome snapshot."""
    due_date = _parse_date(prediction["prediction_date"]) + timedelta(days=_parse_horizon_days(prediction["horizon"]))
    as_of = _parse_date(actuals.get("as_of", _today_iso()))
    target = str(prediction.get("target", ""))
    outcome = {
        "prediction_id": prediction.get("id"),
        "role": prediction.get("role"),
        "target": target,
        "horizon": prediction.get("horizon"),
        "due_date": due_date.isoformat(),
        "as_of": as_of.isoformat(),
        "prediction_type": prediction.get("prediction_type"),
        "confidence": prediction.get("confidence"),
        "execution": prediction.get("execution", {}),
    }

    if as_of < due_date:
        return {**outcome, "status": "pending"}

    target_actual = (actuals.get("targets") or {}).get(target)
    if target_actual is None and "," in target:
        parts = [part for part in target.split(",") if part]
        found = [(actuals.get("targets") or {}).get(part) for part in parts]
        found = [item for item in found if isinstance(item, dict)]
        if found:
            target_actual = {
                "direction": found[0].get("direction"),
                "return_pct": mean(float(item.get("return_pct", 0) or 0) for item in found),
                "benchmark_return_pct": mean(float(item.get("benchmark_return_pct", 0) or 0) for item in found),
            }
    if not isinstance(target_actual, dict):
        return {**outcome, "status": "unavailable", "unavailable_reason": "missing_target_actual"}

    actual_return = float(target_actual.get("return_pct", 0) or 0)
    benchmark_return = float(target_actual.get("benchmark_return_pct", 0) or 0)
    predicted_return = prediction.get("predicted_return_pct")
    expected_direction = prediction.get("expected_direction", "neutral")
    actual_direction = target_actual.get("direction") or ("up" if actual_return > 0 else ("down" if actual_return < 0 else "neutral"))
    direction_hit = None
    if expected_direction in {"up", "down", "neutral"}:
        direction_hit = expected_direction == actual_direction
    elif expected_direction == "risk_control":
        direction_hit = bool(target_actual.get("risk_occurred") or actual_return <= benchmark_return)

    return {
        **outcome,
        "status": "verified",
        "expected_direction": expected_direction,
        "actual_direction": actual_direction,
        "direction_hit": direction_hit,
        "predicted_return_pct": predicted_return,
        "actual_return_pct": actual_return,
        "benchmark_return_pct": benchmark_return,
        "excess_return_pct": actual_return - benchmark_return,
        "return_error_pct": abs(float(predicted_return) - actual_return) if predicted_return is not None else None,
        "risk_hit": target_actual.get("risk_occurred"),
        "duty_score": prediction.get("duty_score"),
        "explanation_score": prediction.get("explanation_score"),
        "advice_quality": prediction.get("advice_quality"),
    }


def evaluate_and_persist_outcomes(
    predictions: Iterable[Dict[str, Any]],
    actuals: Dict[str, Any],
    *,
    outcome_date: str | None = None,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> List[Dict[str, Any]]:
    """Evaluate predictions and append the resulting outcome JSONL."""
    day = outcome_date or str(actuals.get("as_of") or _today_iso())
    outcomes = [evaluate_prediction(prediction, actuals) for prediction in predictions]
    output_path = Path(output_root) / "role_outcomes" / f"{day}.jsonl"
    _append_jsonl(output_path, outcomes)
    return outcomes


def _score_result(outcome: Dict[str, Any]) -> float:
    if outcome.get("status") != "verified":
        return 50.0
    base = 60.0
    if outcome.get("direction_hit") is True:
        base += 25
    elif outcome.get("direction_hit") is False:
        base -= 25
    base += max(-15, min(15, float(outcome.get("excess_return_pct", 0) or 0) * 3))
    return max(0.0, min(100.0, base))


def _score_risk(outcome: Dict[str, Any]) -> float:
    if outcome.get("risk_hit") is True:
        return 100.0
    if outcome.get("risk_hit") is False:
        return 45.0
    return 70.0


def _execution_item(outcome: Dict[str, Any]) -> Dict[str, Any] | None:
    execution = outcome.get("execution") or {}
    if not execution.get("advice_given"):
        return None
    user_executed = bool(execution.get("user_executed"))
    execution_match = bool(execution.get("execution_match"))
    if user_executed and execution_match:
        role_impact = "executed_as_advised"
        discipline_impact = "disciplined"
    elif user_executed and not execution_match:
        role_impact = "no_direct_penalty"
        discipline_impact = "user_discipline_only"
    else:
        role_impact = "no_penalty"
        discipline_impact = "user_discipline_only"
    return {
        "role": outcome.get("role"),
        "target": outcome.get("target"),
        "user_executed": user_executed,
        "execution_match": execution_match,
        "execution_deviation_reason": execution.get("execution_deviation_reason", ""),
        "role_score_impact": role_impact,
        "discipline_score_impact": discipline_impact,
    }


def score_roles(outcomes: List[Dict[str, Any]], *, score_date: str | None = None) -> Dict[str, Any]:
    """Build a role scorecard while keeping execution discipline separate."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    discipline_items: List[Dict[str, Any]] = []
    for outcome in outcomes:
        grouped.setdefault(str(outcome.get("role", "unknown")), []).append(outcome)
        item = _execution_item(outcome)
        if item:
            discipline_items.append(item)

    roles: Dict[str, Dict[str, Any]] = {}
    for role, items in grouped.items():
        if role == "judge":
            account = mean(_score_result(item) for item in items)
            synthesis = mean(float(item.get("synthesis_score", item.get("duty_score", 70)) or 70) for item in items)
            risk = mean(_score_risk(item) for item in items)
            score = account * 0.5 + synthesis * 0.3 + risk * 0.2
        else:
            result = mean(_score_result(item) for item in items)
            duty = mean(float(item.get("duty_score", item.get("advice_quality", 70)) or 70) for item in items)
            risk = mean(_score_risk(item) for item in items)
            explanation = mean(float(item.get("explanation_score", 70) or 70) for item in items)
            score = result * 0.4 + duty * 0.35 + risk * 0.15 + explanation * 0.10
        verified = [item for item in items if item.get("status") == "verified"]
        hits = [item for item in verified if item.get("direction_hit") is not None]
        accuracy = (sum(1 for item in hits if item.get("direction_hit")) / len(hits)) if hits else None
        first_discipline = next((item for item in discipline_items if item.get("role") == role), {})
        roles[role] = {
            "score": round(score, 2),
            "sample_count": len(items),
            "verified_count": len(verified),
            "accuracy": round(accuracy, 4) if accuracy is not None else None,
            "execution_discipline": first_discipline,
            "summary": _role_summary(role, score, len(items)),
        }

    return {
        "date": score_date or _today_iso(),
        "roles": roles,
        "execution_discipline": {
            "summary": f"发现 {len(discipline_items)} 条执行纪律记录；角色建议合理但用户未执行时不扣角色分。",
            "items": discipline_items,
        },
    }


def _role_summary(role: str, score: float, sample_count: int) -> str:
    label = ROLE_LABELS.get(role, role)
    if sample_count < 3:
        return f"{label}样本仍少，先观察，不做过度结论。"
    if score >= 80:
        return f"{label}近期职责履行和结果表现较强。"
    if score < 50:
        return f"{label}近期需要复核判断依据或职责边界。"
    return f"{label}近期表现中性，继续滚动观察。"


def summarize_advice_performance(outcomes: List[Dict[str, Any]], *, performance_date: str | None = None) -> Dict[str, Any]:
    executed = []
    paper_only = []
    for outcome in outcomes:
        execution = outcome.get("execution") or {}
        if not execution.get("advice_given"):
            continue
        if execution.get("user_executed"):
            executed.append(outcome)
        else:
            paper_only.append(outcome)

    return {
        "date": performance_date or _today_iso(),
        "executed": _performance_bucket(executed),
        "paper_only": _performance_bucket(paper_only),
    }


def _performance_bucket(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    returns = [float(item.get("actual_return_pct", 0) or 0) for item in items]
    excess = [float(item.get("excess_return_pct", item.get("actual_return_pct", 0) - item.get("benchmark_return_pct", 0)) or 0) for item in items]
    return {
        "count": len(items),
        "win_rate": round(sum(1 for value in returns if value > 0) / len(returns), 4) if returns else 0.0,
        "avg_return_pct": round(mean(returns), 4) if returns else 0.0,
        "avg_excess_return_pct": round(mean(excess), 4) if excess else 0.0,
        "max_drawdown_pct": round(min(returns), 4) if returns else 0.0,
        "risk_improved_count": sum(1 for item in items if item.get("risk_hit") is True),
    }


def suggest_role_adjustments(scorecard: Dict[str, Any]) -> List[Dict[str, Any]]:
    suggestions: List[Dict[str, Any]] = []
    for role, data in sorted((scorecard.get("roles") or {}).items()):
        score = float(data.get("score", 0) or 0)
        samples = int(data.get("sample_count", 0) or 0)
        if samples < 3:
            action = "watch_role"
            reason = "样本不足，先观察，不调整配置。"
        elif score >= 80:
            action = "increase_weight"
            reason = "近期评分较高，可提交用户考虑提高参考权重。"
        elif score < 50:
            action = "review_prompt"
            reason = "近期评分偏低，建议复核职责提示词和证据要求。"
        else:
            action = "keep_weight"
            reason = "表现中性，维持现有参考权重。"
        suggestions.append({"role": role, "action": action, "reason": reason})
    return suggestions


def render_scorecard_markdown(
    scorecard: Dict[str, Any],
    *,
    advice_performance: Dict[str, Any] | None = None,
    suggestions: List[Dict[str, Any]] | None = None,
) -> str:
    """Render a human-readable Sentinel scorecard report."""
    advice_performance = advice_performance or {}
    suggestions = suggestions or []
    lines = [
        f"# Sentinel 角色评分 - {scorecard.get('date', _today_iso())}",
        "",
        "> 本报告用于评估辩论系统质量，不构成投资建议，不自动修改权重或 prompt。",
        "",
        "## 角色评分",
        "",
        "| 角色 | 分数 | 样本 | 准确率 | 说明 |",
        "|---|---:|---:|---:|---|",
    ]
    for role, data in (scorecard.get("roles") or {}).items():
        accuracy = data.get("accuracy")
        accuracy_text = "样本不足" if accuracy is None else f"{accuracy * 100:.1f}%"
        lines.append(
            f"| {ROLE_LABELS.get(role, role)} | {data.get('score', 0):.2f} | "
            f"{data.get('sample_count', 0)} | {accuracy_text} | {data.get('summary', '')} |"
        )

    lines.extend([
        "",
        "## 执行纪律",
        "",
        scorecard.get("execution_discipline", {}).get("summary", "暂无执行纪律记录。"),
        "",
        "## 投资建议绩效",
        "",
        f"- 真实执行建议：{(advice_performance.get('executed') or {}).get('count', 0)} 条，胜率 {(advice_performance.get('executed') or {}).get('win_rate', 0):.2f}",
        f"- 纸面建议：{(advice_performance.get('paper_only') or {}).get('count', 0)} 条",
        "",
        "## 调整建议",
        "",
    ])
    if suggestions:
        for item in suggestions:
            lines.append(f"- **{ROLE_LABELS.get(item.get('role'), item.get('role'))}**: `{item.get('action')}` - {item.get('reason', '')}")
    else:
        lines.append("- 暂无调整建议。")
    lines.extend([
        "",
        "## 边界",
        "",
        "- 角色建议合理但用户未执行，不扣角色分。",
        "- 用户偏离建议导致亏损，不直接归因给角色。",
        "- 所有权重和 prompt 调整都需要用户确认。",
    ])
    return "\n".join(lines) + "\n"


def render_advice_performance_markdown(advice_performance: Dict[str, Any]) -> str:
    """Render advice performance as a separate Markdown report."""
    executed = advice_performance.get("executed") or {}
    paper = advice_performance.get("paper_only") or {}
    lines = [
        f"# Sentinel 投资建议绩效 - {advice_performance.get('date', _today_iso())}",
        "",
        "> 本报告区分纸面建议和真实执行建议。用户未按建议执行时，不直接扣角色分。",
        "",
        "## 真实执行建议",
        "",
        f"- 数量：{executed.get('count', 0)}",
        f"- 胜率：{executed.get('win_rate', 0):.2f}",
        f"- 平均收益：{executed.get('avg_return_pct', 0):.2f}%",
        f"- 平均超额：{executed.get('avg_excess_return_pct', 0):.2f}%",
        f"- 最大回撤：{executed.get('max_drawdown_pct', 0):.2f}%",
        "",
        "## 纸面建议",
        "",
        f"- 数量：{paper.get('count', 0)}",
        f"- 平均收益：{paper.get('avg_return_pct', 0):.2f}%",
        "",
        "## 归因边界",
        "",
        "- 建议本身质量归入角色评分。",
        "- 是否按建议执行归入用户执行纪律。",
        "- 不自动触发交易，不自动调整角色权重。",
    ]
    return "\n".join(lines) + "\n"


def persist_review_outputs(
    *,
    scorecard: Dict[str, Any],
    advice_performance: Dict[str, Any],
    suggestions: List[Dict[str, Any]],
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> Dict[str, str]:
    """Persist score, advice performance, discipline, and markdown report artifacts."""
    root = Path(output_root)
    day = str(scorecard.get("date") or _today_iso())
    paths = {
        "role_scores": root / "role_scores" / f"{day}.json",
        "advice_performance": root / "advice_performance" / f"{day}.json",
        "execution_discipline": root / "execution_discipline" / f"{day}.json",
        "role_scorecard": root / "reports" / f"{day}_sentinel_role_scorecard.md",
        "advice_report": root / "reports" / f"{day}_sentinel_advice_performance.md",
    }
    _write_json(paths["role_scores"], scorecard)
    _write_json(paths["advice_performance"], advice_performance)
    _write_json(paths["execution_discipline"], scorecard.get("execution_discipline", {}))
    scorecard_md = render_scorecard_markdown(scorecard, advice_performance=advice_performance, suggestions=suggestions)
    advice_md = render_advice_performance_markdown(advice_performance)
    paths["role_scorecard"].parent.mkdir(parents=True, exist_ok=True)
    paths["role_scorecard"].write_text(scorecard_md, encoding="utf-8")
    paths["advice_report"].parent.mkdir(parents=True, exist_ok=True)
    paths["advice_report"].write_text(advice_md, encoding="utf-8")
    return {key: str(path) for key, path in paths.items()}


__all__ = [
    "ALLOWED_ADJUSTMENT_ACTIONS",
    "DEFAULT_HORIZONS",
    "evaluate_and_persist_outcomes",
    "evaluate_prediction",
    "persist_review_outputs",
    "render_advice_performance_markdown",
    "record_debate_predictions",
    "render_scorecard_markdown",
    "score_roles",
    "summarize_advice_performance",
    "suggest_role_adjustments",
]

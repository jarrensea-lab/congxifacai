"""All-market prediction ledger and outcome review helpers."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT

DEFAULT_HORIZONS = (1, 3, 5)


def default_prediction_lab_root() -> Path:
    return Path(
        os.environ.get(
            "CONGXI_PREDICTION_LAB_ROOT",
            os.path.abspath(os.path.join(PROJECT_ROOT, "..", "data", "prediction_lab")),
        )
    )


def _today() -> str:
    return date.today().isoformat()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return default


def _normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10]


def _range_position_pct(bars: list[dict[str, Any]], price: float, lookback: int = 20) -> float | None:
    recent = bars[-lookback:] if len(bars) >= 10 else []
    highs = [_to_float(item.get("high") or item.get("close")) for item in recent]
    lows = [_to_float(item.get("low") or item.get("close")) for item in recent]
    highs = [item for item in highs if item > 0]
    lows = [item for item in lows if item > 0]
    if not highs or not lows:
        return None
    high = max(highs)
    low = min(lows)
    if high <= low:
        return None
    return round(max(0.0, min(100.0, (price - low) / (high - low) * 100)), 2)


def _return_pct(current: float, base: float) -> float:
    return round((current - base) / base * 100, 2) if base > 0 else 0.0


def _stable_prediction_id(payload: dict[str, Any]) -> str:
    identity = {
        "date": payload.get("prediction_date"),
        "code": payload.get("code"),
        "horizon": payload.get("horizon"),
        "model_version": payload.get("model_version"),
    }
    digest = hashlib.sha1(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return f"pred_{digest[:20]}"


def extract_price_action_features(
    *,
    quote: dict[str, Any],
    bars: list[dict[str, Any]],
) -> dict[str, Any]:
    """Extract deterministic price-action features for ranking and later attribution."""
    price = _to_float(quote.get("price") or quote.get("close"))
    closes = [_to_float(item.get("close")) for item in bars if _to_float(item.get("close")) > 0]
    ma5 = round(sum(closes[-5:]) / 5, 3) if len(closes) >= 5 else 0.0
    ma10 = round(sum(closes[-10:]) / 10, 3) if len(closes) >= 10 else 0.0
    prev5 = closes[-6] if len(closes) >= 6 else 0.0
    return {
        "price": price,
        "change_pct": _to_float(quote.get("change_pct")),
        "vol_ratio": _to_float(quote.get("vol_ratio")),
        "amount_wan": _to_float(quote.get("amount_wan") or quote.get("amount")),
        "range_position_pct": _range_position_pct(bars, price),
        "ma5": ma5,
        "ma10": ma10,
        "return_5d_pct": _return_pct(price or (closes[-1] if closes else 0), prev5),
        "bar_count": len(bars),
    }


def score_price_action_prediction(features: dict[str, Any]) -> dict[str, Any]:
    """Return a transparent prediction score; every adjustment is inspectable."""
    score = 50.0
    reasons: list[str] = []
    change_pct = _to_float(features.get("change_pct"))
    vol_ratio = _to_float(features.get("vol_ratio"))
    amount_wan = _to_float(features.get("amount_wan"))
    range_pos = features.get("range_position_pct")
    ma5 = _to_float(features.get("ma5"))
    ma10 = _to_float(features.get("ma10"))
    return_5d = _to_float(features.get("return_5d_pct"))

    if amount_wan >= 10000:
        score += 8
        reasons.append("liquid_enough")
    if vol_ratio >= 1.8:
        score += 8
        reasons.append("volume_expansion")
    if 0.5 <= change_pct <= 4:
        score += 8
        reasons.append("positive_not_limit_chasing")
    elif change_pct >= 6:
        score -= 12
        reasons.append("intraday_overheated")
    if range_pos is None:
        score -= 8
        reasons.append("range_missing")
    elif range_pos >= 80:
        score -= 30
        reasons.append("high_position_risk")
    elif 35 <= range_pos <= 70:
        score += 10
        reasons.append("mid_range_entry")
    elif range_pos <= 25 and change_pct > 0:
        score += 6
        reasons.append("low_range_turning")
    if ma5 > 0 and ma10 > 0 and ma5 >= ma10:
        score += 6
        reasons.append("ma5_above_ma10")
    if return_5d >= 12:
        score -= 10
        reasons.append("five_day_overheated")
    elif -6 <= return_5d <= 6:
        score += 4
        reasons.append("five_day_not_extreme")

    score = round(max(0.0, min(100.0, score)), 1)
    expected_direction = "up" if score >= 62 else "down" if score <= 42 else "neutral"
    expected_return_pct = round(max(-5.0, min(5.0, (score - 50) / 8)), 2)
    confidence = round(abs(score - 50) / 10 + 3, 1)
    return {
        "prediction_score": score,
        "expected_direction": expected_direction,
        "expected_return_pct": expected_return_pct,
        "confidence": min(9.0, confidence),
        "reasons": reasons,
    }


def build_prediction_records(
    *,
    code: str,
    name: str,
    quote: dict[str, Any],
    bars: list[dict[str, Any]],
    prediction_date: str | None = None,
    source: str = "prediction_lab",
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
) -> list[dict[str, Any]]:
    features = extract_price_action_features(quote=quote, bars=bars)
    prediction = score_price_action_prediction(features)
    day = prediction_date or _today()
    records: list[dict[str, Any]] = []
    for horizon in horizons:
        record = {
            "prediction_id": "",
            "prediction_date": day,
            "generated_at": _now(),
            "code": str(code),
            "name": name or str(code),
            "horizon": f"T+{horizon}",
            "horizon_days": horizon,
            "model_version": "price_action_v1",
            "source": source,
            "features": features,
            **prediction,
            "data_cutoff_date": day,
            "execution": {"advice_given": False, "user_executed": False},
        }
        record["prediction_id"] = _stable_prediction_id(record)
        records.append(record)
    return records


class PredictionLedger:
    """Append-only prediction and outcome store."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else default_prediction_lab_root()

    def prediction_path(self, day: str) -> Path:
        return self.root / "predictions" / f"{day}.jsonl"

    def outcome_path(self, day: str) -> Path:
        return self.root / "outcomes" / f"{day}.jsonl"

    def append_predictions(self, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0
        path = self.prediction_path(str(records[0].get("prediction_date") or _today()))
        existing = {item.get("prediction_id") for item in self.read_jsonl(path)}
        path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with path.open("a", encoding="utf-8") as fh:
            for record in records:
                if record.get("prediction_id") in existing:
                    continue
                fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                existing.add(record.get("prediction_id"))
                written += 1
        return written

    def append_outcomes(self, records: list[dict[str, Any]], *, outcome_date: str | None = None) -> int:
        if not records:
            return 0
        path = self.outcome_path(outcome_date or _today())
        existing = {item.get("prediction_id") for item in self.read_jsonl(path)}
        path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with path.open("a", encoding="utf-8") as fh:
            for record in records:
                if record.get("prediction_id") in existing:
                    continue
                fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                existing.add(record.get("prediction_id"))
                written += 1
        return written

    @staticmethod
    def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
        path = Path(path)
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
        return rows


def evaluate_prediction_record(
    prediction: dict[str, Any],
    *,
    bars: list[dict[str, Any]],
    benchmark_return_pct: float = 0.0,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Evaluate one prediction when its horizon bar is available."""
    day = _normalize_date(prediction.get("prediction_date"))
    horizon_days = int(_to_float(prediction.get("horizon_days")))
    normalized_bars = [{**item, "date": _normalize_date(item.get("date"))} for item in bars]
    index = next((idx for idx, item in enumerate(normalized_bars) if item.get("date") == day), None)
    base = {
        "prediction_id": prediction.get("prediction_id"),
        "prediction_date": day,
        "as_of": as_of or _today(),
        "code": prediction.get("code"),
        "name": prediction.get("name"),
        "horizon": prediction.get("horizon"),
        "expected_direction": prediction.get("expected_direction"),
        "expected_return_pct": prediction.get("expected_return_pct"),
        "prediction_score": prediction.get("prediction_score"),
        "confidence": prediction.get("confidence"),
        "model_version": prediction.get("model_version"),
        "source": prediction.get("source"),
    }
    if index is None:
        return {**base, "status": "unavailable", "reason": "prediction_date_not_in_bars"}
    due_index = index + horizon_days
    if due_index >= len(normalized_bars):
        return {**base, "status": "pending", "reason": "horizon_not_due"}
    start_price = _to_float(normalized_bars[index].get("close"))
    end_price = _to_float(normalized_bars[due_index].get("close"))
    actual_return = _return_pct(end_price, start_price)
    actual_direction = "up" if actual_return > 0 else "down" if actual_return < 0 else "neutral"
    expected_direction = str(prediction.get("expected_direction") or "neutral")
    return {
        **base,
        "status": "verified",
        "start_price": start_price,
        "end_price": end_price,
        "actual_return_pct": actual_return,
        "actual_direction": actual_direction,
        "direction_hit": expected_direction == "neutral" or expected_direction == actual_direction,
        "return_error_pct": round(abs(actual_return - _to_float(prediction.get("expected_return_pct"))), 2),
        "benchmark_return_pct": benchmark_return_pct,
        "excess_return_pct": round(actual_return - benchmark_return_pct, 2),
        "features": prediction.get("features") or {},
        "reasons": prediction.get("reasons") or [],
    }


def summarize_prediction_outcomes(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    verified = [item for item in outcomes if item.get("status") == "verified"]
    if not verified:
        return {"verified_count": 0, "direction_accuracy": 0.0, "avg_return_pct": 0.0, "avg_excess_return_pct": 0.0}
    return {
        "verified_count": len(verified),
        "direction_accuracy": round(sum(1 for item in verified if item.get("direction_hit")) / len(verified), 3),
        "avg_return_pct": round(sum(_to_float(item.get("actual_return_pct")) for item in verified) / len(verified), 2),
        "avg_excess_return_pct": round(sum(_to_float(item.get("excess_return_pct")) for item in verified) / len(verified), 2),
    }


def suggest_strategy_adjustments(outcomes: list[dict[str, Any]], *, min_samples: int = 3) -> list[dict[str, Any]]:
    """Convert verified outcome groups into auditable strategy adjustment suggestions."""
    verified = [item for item in outcomes if item.get("status") == "verified"]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in verified:
        for reason in item.get("reasons") or ["unclassified"]:
            grouped.setdefault(str(reason), []).append(item)

    suggestions: list[dict[str, Any]] = []
    for reason, rows in sorted(grouped.items()):
        if len(rows) < min_samples:
            continue
        avg_excess = round(sum(_to_float(item.get("excess_return_pct")) for item in rows) / len(rows), 2)
        accuracy = round(sum(1 for item in rows if item.get("direction_hit")) / len(rows), 3)
        if avg_excess <= -1 or accuracy < 0.45:
            action = "downweight_trigger"
        elif avg_excess >= 1 and accuracy >= 0.55:
            action = "upweight_trigger"
        else:
            action = "keep_weight"
        suggestions.append({
            "trigger_reason": reason,
            "sample_count": len(rows),
            "avg_excess_return_pct": avg_excess,
            "direction_accuracy": accuracy,
            "action": action,
        })
    return suggestions

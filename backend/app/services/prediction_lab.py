"""All-market prediction ledger and outcome review helpers."""
from __future__ import annotations

import hashlib
import json
import math
import os
import fcntl
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from app.config import PROJECT_ROOT
from app.trading_engine.fee_schedule import calculate_tradable_return, get_fee_config

DEFAULT_HORIZONS = (1, 3, 5)
DEFAULT_MIN_INDEPENDENT_SAMPLES = 60
DEFAULT_MIN_SIGNAL_COVERAGE = 0.50
MAX_ABS_RETURN_PCT = 1_000_000.0
MAX_COST_PCT = 100.0
MAX_MONEY_FEN = 10**15
MAX_PRICE = 10**9
MAX_QUANTITY = 10**12
_PROCESS_LEDGER_LOCK = RLock()


@contextmanager
def _ledger_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".ledger.lock"
    with _PROCESS_LEDGER_LOCK, lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


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


def _finite_float(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace("%", "").replace(",", ""))
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _to_float(value: Any, default: float = 0.0) -> float:
    parsed = _finite_float(value)
    return default if parsed is None else parsed


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


def _is_available_contract_value(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and text.lower() != "unavailable"


def _stable_prediction_id(payload: dict[str, Any]) -> str:
    identity = {
        "date": payload.get("prediction_date"),
        "code": payload.get("code"),
        "horizon": payload.get("horizon"),
        "model_version": payload.get("model_version"),
        "broad_benchmark_id": payload.get("broad_benchmark_id"),
        "tradable_universe_snapshot_id": payload.get("tradable_universe_snapshot_id"),
        "data_cutoff": payload.get("data_cutoff"),
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
    broad_benchmark_id: str | None = None,
    tradable_universe_snapshot_id: str | None = None,
    data_cutoff: str | None = None,
    tradable_budget_fen: int | None = None,
    entry_policy: str | None = None,
    exit_policy: str | None = None,
    commission_rate: float | None = None,
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
            "broad_benchmark_id": broad_benchmark_id or "unavailable",
            "tradable_universe_snapshot_id": tradable_universe_snapshot_id or "unavailable",
            "data_cutoff": data_cutoff or day,
            "tradable_budget_fen": tradable_budget_fen,
            "entry_policy": entry_policy,
            "exit_policy": exit_policy,
            "commission_rate": commission_rate,
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
        with _ledger_lock(self.root):
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
                if written:
                    fh.flush()
                    os.fsync(fh.fileno())
            return written

    def append_outcomes(self, records: list[dict[str, Any]], *, outcome_date: str | None = None) -> int:
        if not records:
            return 0
        path = self.outcome_path(outcome_date or _today())
        with _ledger_lock(self.root):
            existing_rows = [
                item
                for outcome_path in sorted((self.root / "outcomes").glob("*.jsonl"))
                for item in self.read_jsonl(outcome_path)
            ]
            existing_statuses = {
                (item.get("prediction_id"), item.get("status"))
                for item in existing_rows
            }
            verified_ids = {
                item.get("prediction_id")
                for item in existing_rows
                if item.get("status") == "verified"
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with path.open("a", encoding="utf-8") as fh:
                for record in records:
                    prediction_id = record.get("prediction_id")
                    status = record.get("status")
                    if prediction_id in verified_ids or (prediction_id, status) in existing_statuses:
                        continue
                    fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                    existing_statuses.add((prediction_id, status))
                    if status == "verified":
                        verified_ids.add(prediction_id)
                    written += 1
                if written:
                    fh.flush()
                    os.fsync(fh.fileno())
            return written

    def due_predictions(self, *, as_of: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        """Derive unfinished work from the append-only ledgers, regardless of age."""
        cutoff = _normalize_date(as_of or _today())
        predictions = [
            item
            for prediction_path in sorted((self.root / "predictions").glob("*.jsonl"))
            for item in self.read_jsonl(prediction_path)
            if _normalize_date(item.get("prediction_date")) <= cutoff
        ]
        verified_ids = {
            item.get("prediction_id")
            for outcome_path in sorted((self.root / "outcomes").glob("*.jsonl"))
            for item in self.read_jsonl(outcome_path)
            if item.get("status") == "verified"
        }
        due = [item for item in predictions if item.get("prediction_id") not in verified_ids]
        due.sort(
            key=lambda item: (
                _normalize_date(item.get("prediction_date")),
                str(item.get("code") or ""),
                int(_to_float(item.get("horizon_days"))),
            )
        )
        return due[:limit] if limit else due

    @staticmethod
    def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
        """Read valid object rows, preserving the original compatibility API."""
        return PredictionLedger.read_jsonl_with_diagnostics(path)["rows"]

    @staticmethod
    def read_jsonl_with_diagnostics(path: str | Path) -> dict[str, Any]:
        """Read JSONL object rows and report every malformed non-blank line."""
        path = Path(path)
        if not path.exists():
            return {
                "rows": [],
                "malformed_line_count": 0,
                "malformed_lines": [],
            }
        rows: list[dict[str, Any]] = []
        malformed_lines: list[dict[str, Any]] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                malformed_lines.append({"line_number": line_number, "reason": f"invalid_json: {exc.msg}"})
                continue
            if isinstance(payload, dict):
                rows.append(payload)
            else:
                malformed_lines.append({"line_number": line_number, "reason": "expected_json_object"})
        return {
            "rows": rows,
            "malformed_line_count": len(malformed_lines),
            "malformed_lines": malformed_lines,
        }


def evaluate_prediction_record(
    prediction: dict[str, Any],
    *,
    bars: list[dict[str, Any]],
    benchmark_return_pct: float | None = None,
    broad_benchmark_return_pct: float | None = None,
    tradable_universe_return_pct: float | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Evaluate one prediction when its horizon bar is available."""
    day = _normalize_date(prediction.get("prediction_date"))
    horizon_days = int(_to_float(prediction.get("horizon_days")))
    normalized_bars = [{**item, "date": _normalize_date(item.get("date"))} for item in bars]
    index = next((idx for idx, item in enumerate(normalized_bars) if item.get("date") == day), None)
    commission_override = (
        _finite_float(prediction.get("commission_rate"))
        if prediction.get("commission_rate") is not None
        else None
    )
    cost_config = get_fee_config(
        str(prediction.get("code") or ""),
        commission_rate=commission_override,
    )
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
        "broad_benchmark_id": prediction.get("broad_benchmark_id"),
        "tradable_universe_snapshot_id": prediction.get("tradable_universe_snapshot_id"),
        "data_cutoff": prediction.get("data_cutoff") or prediction.get("data_cutoff_date"),
        "benchmark_return_pct": None,
        "excess_return_pct": None,
        "broad_benchmark_return_pct": None,
        "tradable_universe_return_pct": None,
        "broad_benchmark_excess_return_pct": None,
        "tradable_universe_excess_return_pct": None,
        "benchmark_coverage_complete": False,
        "gross_return_pct": None,
        "round_trip_cost_fen": None,
        "round_trip_cost_pct": None,
        "net_tradable_return_pct": None,
        "cost_model_version": cost_config["cost_model_version"],
        "cost_estimated": cost_config["commission_estimated"],
        "commission_source": cost_config["commission_source"],
        "tradable": False,
        "untradable_reason": "outcome_not_verified",
        "promotion_eligible": False,
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
    broad_return = _finite_float(broad_benchmark_return_pct)
    if broad_return is None:
        broad_return = _finite_float(benchmark_return_pct)
    universe_return = _finite_float(tradable_universe_return_pct)
    benchmark_coverage_complete = (
        broad_return is not None
        and universe_return is not None
        and _is_available_contract_value(prediction.get("broad_benchmark_id"))
        and _is_available_contract_value(prediction.get("tradable_universe_snapshot_id"))
        and _is_available_contract_value(
            prediction.get("data_cutoff") or prediction.get("data_cutoff_date")
        )
    )
    broad_excess = round(actual_return - broad_return, 2) if broad_return is not None else None
    universe_excess = (
        round(actual_return - universe_return, 2)
        if universe_return is not None
        else None
    )
    budget_value = prediction.get("tradable_budget_fen")
    tradable_result = calculate_tradable_return(
        str(prediction.get("code") or ""),
        entry_price=start_price,
        exit_price=end_price,
        budget_fen=int(_to_float(budget_value)) if budget_value is not None else None,
        entry_policy=prediction.get("entry_policy"),
        exit_policy=prediction.get("exit_policy"),
        commission_rate=commission_override,
    )
    return {
        **base,
        "status": "verified",
        "start_price": start_price,
        "end_price": end_price,
        "actual_return_pct": actual_return,
        "actual_direction": actual_direction,
        "direction_hit": None if expected_direction == "neutral" else expected_direction == actual_direction,
        "return_error_pct": round(abs(actual_return - _to_float(prediction.get("expected_return_pct"))), 2),
        "benchmark_return_pct": broad_return,
        "excess_return_pct": broad_excess,
        "broad_benchmark_return_pct": broad_return,
        "tradable_universe_return_pct": universe_return,
        "broad_benchmark_excess_return_pct": broad_excess,
        "tradable_universe_excess_return_pct": universe_excess,
        "benchmark_coverage_complete": benchmark_coverage_complete,
        "promotion_eligible": (
            benchmark_coverage_complete
            and tradable_result["tradable"]
            and tradable_result["cost_estimated"] is False
        ),
        **tradable_result,
        "features": prediction.get("features") or {},
        "reasons": prediction.get("reasons") or [],
    }


def _outcome_timestamp(item: dict[str, Any]) -> str:
    return max(str(item.get("evaluated_at") or ""), str(item.get("as_of") or ""))


def _canonical_outcomes(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for index, item in enumerate(outcomes):
        prediction_id = str(item.get("prediction_id") or f"__legacy_row_{index}")
        grouped.setdefault(prediction_id, []).append(item)

    canonical: list[dict[str, Any]] = []
    for rows in grouped.values():
        canonical.append(
            max(
                rows,
                key=lambda item: (
                    item.get("status") == "verified",
                    _outcome_timestamp(item),
                ),
            )
        )
    return canonical


def _independent_unit_key(item: dict[str, Any], fallback: str) -> tuple[str, ...]:
    prediction_date = str(item.get("prediction_date") or "")
    code = str(item.get("code") or "")
    if prediction_date and code:
        return (prediction_date, code)
    return ("legacy", str(item.get("prediction_id") or fallback))


def _direction_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in rows
        if item.get("expected_direction") != "neutral" and isinstance(item.get("direction_hit"), bool)
    ]


_VERIFIED_NUMERIC_FIELDS = (
    "expected_return_pct",
    "actual_return_pct",
    "excess_return_pct",
    "benchmark_return_pct",
    "broad_benchmark_return_pct",
    "tradable_universe_return_pct",
    "broad_benchmark_excess_return_pct",
    "tradable_universe_excess_return_pct",
    "start_price",
    "end_price",
    "return_error_pct",
    "gross_return_pct",
    "round_trip_cost_fen",
    "round_trip_cost_pct",
    "net_tradable_return_pct",
    "slippage_cost_fen",
    "entry_execution_price_fen",
    "exit_execution_price_fen",
    "quantity",
)


def _verified_numeric_errors(item: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    return_fields = {
        "expected_return_pct",
        "actual_return_pct",
        "excess_return_pct",
        "benchmark_return_pct",
        "broad_benchmark_return_pct",
        "tradable_universe_return_pct",
        "broad_benchmark_excess_return_pct",
        "tradable_universe_excess_return_pct",
        "return_error_pct",
        "gross_return_pct",
        "net_tradable_return_pct",
    }
    cost_pct_fields = {"round_trip_cost_pct"}
    money_fields = {
        "round_trip_cost_fen",
        "slippage_cost_fen",
        "entry_execution_price_fen",
        "exit_execution_price_fen",
    }
    price_fields = {"start_price", "end_price"}
    for field in _VERIFIED_NUMERIC_FIELDS:
        if field not in item or item[field] is None:
            continue
        value = _finite_float(item[field])
        invalid = value is None
        if not invalid and field in return_fields:
            invalid = not -100 <= value <= MAX_ABS_RETURN_PCT
        elif not invalid and field in cost_pct_fields:
            invalid = not 0 <= value <= MAX_COST_PCT
        elif not invalid and field in money_fields:
            invalid = not 0 <= value <= MAX_MONEY_FEN
        elif not invalid and field in price_fields:
            invalid = not 0 < value <= MAX_PRICE
        elif not invalid and field == "quantity":
            invalid = not 0 <= value <= MAX_QUANTITY or not value.is_integer()
        if invalid:
            errors.append(field)
    return errors


def _benchmark_coverage_complete(item: dict[str, Any]) -> bool:
    return (
        _finite_float(item.get("broad_benchmark_return_pct")) is not None
        and _finite_float(item.get("tradable_universe_return_pct")) is not None
        and _is_available_contract_value(item.get("broad_benchmark_id"))
        and _is_available_contract_value(item.get("tradable_universe_snapshot_id"))
        and _is_available_contract_value(item.get("data_cutoff") or item.get("data_cutoff_date"))
    )


def _actual_cost_complete(item: dict[str, Any]) -> bool:
    cost_pct = _finite_float(item.get("round_trip_cost_pct"))
    net_return = _finite_float(item.get("net_tradable_return_pct"))
    return (
        item.get("cost_estimated") is False
        and cost_pct is not None
        and 0 <= cost_pct <= MAX_COST_PCT
        and net_return is not None
        and -100 <= net_return <= MAX_ABS_RETURN_PCT
    )


def _safe_average(values: list[float]) -> float | None:
    if not values or any(not math.isfinite(value) for value in values):
        return None
    try:
        result = math.fsum(values) / len(values)
    except OverflowError:
        return None
    return result if math.isfinite(result) else None


def _safe_sum(values: list[float]) -> float | None:
    if any(not math.isfinite(value) for value in values):
        return None
    try:
        result = math.fsum(values)
    except OverflowError:
        return None
    return result if math.isfinite(result) else None


def _profit_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    versions = {
        str(item.get("model_version") or item.get("strategy_version") or "legacy")
        for item in rows
    }
    mixed_versions = len(versions) > 1
    buy_rows = [item for item in rows if item.get("expected_direction") == "up"]
    evaluated_buys = [item for item in buy_rows if isinstance(item.get("direction_hit"), bool)]
    net_returns = [
        value
        for item in ([] if mixed_versions else buy_rows)
        if item.get("tradable") is True
        for value in [_finite_float(item.get("net_tradable_return_pct"))]
        if value is not None
    ]
    wins = [value for value in net_returns if value > 0]
    losses = [value for value in net_returns if value < 0]
    average_win = _safe_average(wins) or 0.0
    average_loss = _safe_average(losses) or 0.0
    payoff_ratio = average_win / abs(average_loss) if average_loss < 0 else None
    if payoff_ratio is not None and not math.isfinite(payoff_ratio):
        payoff_ratio = None
    payoff_ratio_unbounded = average_win > 0 and average_loss == 0
    gross_profit_value = _safe_sum(wins)
    gross_loss_value = _safe_sum(losses)
    gross_profit = gross_profit_value if gross_profit_value is not None else 0.0
    gross_loss = abs(gross_loss_value) if gross_loss_value is not None else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    if profit_factor is not None and not math.isfinite(profit_factor):
        profit_factor = None
    profit_factor_unbounded = (
        gross_profit_value is not None
        and gross_loss_value is not None
        and gross_profit > 0
        and gross_loss == 0
    )

    equity = 100.0
    peak = equity
    max_drawdown = 0.0
    returns_by_date: dict[str, list[float]] = {}
    if not mixed_versions:
        for item in buy_rows:
            net_return = _finite_float(item.get("net_tradable_return_pct"))
            if item.get("tradable") is not True or net_return is None:
                continue
            prediction_day = _normalize_date(item.get("prediction_date")) or "undated"
            returns_by_date.setdefault(prediction_day, []).append(net_return)
    equity_path_finite = True
    for prediction_day in sorted(returns_by_date):
        daily_return = _safe_average(returns_by_date[prediction_day])
        if daily_return is None:
            continue
        equity *= 1 + daily_return / 100
        if not math.isfinite(equity):
            equity_path_finite = False
            break
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)

    return {
        "buy_signal_count": len(buy_rows),
        "buy_signal_evaluated_count": len(evaluated_buys),
        "buy_signal_precision": round(
            sum(item.get("direction_hit") is True for item in evaluated_buys) / len(evaluated_buys),
            3,
        ) if evaluated_buys else 0.0,
        "average_win_pct": round(average_win, 3),
        "average_loss_pct": round(average_loss, 3),
        "payoff_ratio": round(payoff_ratio, 3) if payoff_ratio is not None else None,
        "payoff_ratio_unbounded": payoff_ratio_unbounded,
        "net_expectancy_after_cost_pct": round(
            _safe_average(net_returns) or 0.0, 3
        ),
        "profit_factor": round(profit_factor, 3) if profit_factor is not None else None,
        "profit_factor_unbounded": profit_factor_unbounded,
        "max_drawdown_pct": round(max_drawdown, 3),
        "max_drawdown_basis": (
            "blocked_mixed_model_versions"
            if mixed_versions
            else "equal_weight_buy_signal_net_return_by_prediction_date_compounded;"
            "overlapping_horizons_equal_weighted_within_date"
        ),
        "aggregate_metrics_finite": all(
            math.isfinite(value)
            for value in (
                average_win,
                average_loss,
                _safe_average(net_returns) or 0.0,
                gross_profit,
                gross_loss,
                max_drawdown,
            )
        ) and gross_profit_value is not None and gross_loss_value is not None and equity_path_finite,
    }


def _summarize_verified(rows: list[dict[str, Any]]) -> dict[str, Any]:
    direction_rows = _direction_rows(rows)
    actual_average = _safe_average([_to_float(item.get("actual_return_pct")) for item in rows])
    excess_average = _safe_average([_to_float(item.get("excess_return_pct")) for item in rows])
    return {
        "verified_count": len(rows),
        "direction_evaluated_count": len(direction_rows),
        "direction_accuracy": round(
            sum(1 for item in direction_rows if item.get("direction_hit")) / len(direction_rows), 3
        )
        if direction_rows
        else 0.0,
        "avg_return_pct": round(actual_average, 2) if actual_average is not None else 0.0,
        "avg_excess_return_pct": round(excess_average, 2) if excess_average is not None else 0.0,
        **_profit_metrics(rows),
    }


def summarize_prediction_outcomes(
    outcomes: list[dict[str, Any]],
    *,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    canonical = _canonical_outcomes(outcomes)
    canonical_verified = [item for item in canonical if item.get("status") == "verified"]
    malformed_verified = [item for item in canonical_verified if _verified_numeric_errors(item)]
    verified = [item for item in canonical_verified if not _verified_numeric_errors(item)]
    abstentions = [item for item in verified if item.get("expected_direction") == "neutral"]
    independent_unit_count = len(
        {_independent_unit_key(item, str(index)) for index, item in enumerate(canonical)}
    )
    verified_independent_unit_count = len(
        {_independent_unit_key(item, str(index)) for index, item in enumerate(verified)}
    )
    malformed_line_count = int((diagnostics or {}).get("malformed_line_count") or 0)
    malformed_outcome_count = len(malformed_verified)
    benchmark_complete_count = sum(_benchmark_coverage_complete(item) for item in verified)
    benchmark_coverage_complete = bool(verified) and benchmark_complete_count == len(verified)
    tradable_count = sum(item.get("tradable") is True for item in verified)
    tradable_coverage_complete = bool(verified) and tradable_count == len(verified)
    actual_cost_complete_count = sum(_actual_cost_complete(item) for item in verified)
    actual_cost_coverage_complete = (
        bool(verified) and actual_cost_complete_count == len(verified)
    )
    verified_summary = _summarize_verified(verified)
    signal_coverage = (
        (len(verified) - len(abstentions)) / len(verified) if verified else 0.0
    )
    outcome_coverage = len(verified) / len(canonical) if canonical else 0.0
    verified_independent_unit_coverage = (
        verified_independent_unit_count / independent_unit_count
        if independent_unit_count
        else 0.0
    )
    model_versions = sorted({
        str(item.get("model_version") or item.get("strategy_version") or "legacy")
        for item in canonical
    })
    mixed_model_versions = len(model_versions) > 1
    summary = {
        "terminal_count": len(canonical),
        **verified_summary,
        "abstention_count": len(abstentions),
        "abstention_rate": round(len(abstentions) / len(verified), 3) if verified else 0.0,
        "signal_coverage": round(signal_coverage, 3),
        "outcome_coverage": round(outcome_coverage, 3),
        "independent_unit_count": independent_unit_count,
        "verified_independent_unit_count": verified_independent_unit_count,
        "verified_independent_unit_coverage": round(verified_independent_unit_coverage, 3),
        "model_versions": model_versions,
        "version_count": len(model_versions),
        "mixed_model_versions": mixed_model_versions,
        "malformed_line_count": malformed_line_count,
        "malformed_outcome_count": malformed_outcome_count,
        "malformed_count": malformed_line_count + malformed_outcome_count,
        "benchmark_complete_count": benchmark_complete_count,
        "benchmark_coverage_complete": benchmark_coverage_complete,
        "tradable_count": tradable_count,
        "tradable_coverage_complete": tradable_coverage_complete,
        "actual_cost_complete_count": actual_cost_complete_count,
        "actual_cost_coverage_complete": actual_cost_coverage_complete,
        "cost_coverage_complete": actual_cost_coverage_complete,
        "promotion_eligible": (
            malformed_line_count == 0
            and malformed_outcome_count == 0
            and verified_independent_unit_count >= DEFAULT_MIN_INDEPENDENT_SAMPLES
            and outcome_coverage >= 0.90
            and verified_independent_unit_coverage >= 0.90
            and not mixed_model_versions
            and benchmark_coverage_complete
            and tradable_coverage_complete
            and actual_cost_coverage_complete
            and verified_summary["buy_signal_evaluated_count"] > 0
            and signal_coverage >= DEFAULT_MIN_SIGNAL_COVERAGE
            and verified_summary["net_expectancy_after_cost_pct"] > 0
            and verified_summary["aggregate_metrics_finite"] is True
            and (
                verified_summary["profit_factor_unbounded"] is True
                or (
                    verified_summary["profit_factor"] is not None
                    and verified_summary["profit_factor"] > 1
                )
            )
        ),
        "by_horizon": {},
        "by_version": {},
    }
    for horizon in sorted({str(item.get("horizon")) for item in canonical if item.get("horizon")}):
        horizon_rows = [
            item
            for item in verified
            if str(item.get("horizon")) == horizon
        ]
        summary["by_horizon"][horizon] = _summarize_verified(horizon_rows)
    for version in model_versions:
        version_rows = [
            item
            for item in verified
            if str(item.get("model_version") or item.get("strategy_version") or "legacy") == version
        ]
        summary["by_version"][version] = _summarize_verified(version_rows)
    return summary


def _comparison_unit_key(item: dict[str, Any]) -> tuple[str, str] | None:
    prediction_date = _normalize_date(item.get("prediction_date"))
    code = str(item.get("code") or "").strip()
    return (prediction_date, code) if prediction_date and code else None


def _normalized_horizon_identity(item: dict[str, Any]) -> str | None:
    text = str(item.get("horizon") or "").strip().upper()
    if not text.startswith("T+") or not text[2:].isdigit():
        return None
    days = int(text[2:])
    return f"T+{days}" if days > 0 else None


def _unit_direction_accuracy(rows: list[dict[str, Any]]) -> float | None:
    if not rows or any(
        item.get("expected_direction") == "neutral"
        or not isinstance(item.get("direction_hit"), bool)
        for item in rows
    ):
        return None
    return sum(item.get("direction_hit") is True for item in rows) / len(rows)


def _unit_net_expectancy_after_cost(rows: list[dict[str, Any]]) -> float | None:
    if not rows or any(
        item.get("expected_direction") != "up"
        or item.get("tradable") is not True
        or not _actual_cost_complete(item)
        for item in rows
    ):
        return None
    net_returns = [
        value
        for item in rows
        for value in [_finite_float(item.get("net_tradable_return_pct"))]
        if value is not None
    ]
    return sum(net_returns) / len(net_returns) if net_returns else None


def _paired_mean_ci95(values: list[float]) -> tuple[float, float, float, bool]:
    if any(not math.isfinite(value) for value in values):
        return 0.0, 0.0, 0.0, False
    try:
        mean = math.fsum(values) / len(values) if values else 0.0
        if len(values) >= 2:
            variance = math.fsum((value - mean) ** 2 for value in values) / (len(values) - 1)
            margin = 1.96 * math.sqrt(variance / len(values))
        else:
            margin = 0.0
        outputs = (mean, mean - margin, mean + margin)
    except (OverflowError, ValueError):
        return 0.0, 0.0, 0.0, False
    return (*outputs, all(math.isfinite(value) for value in outputs))


def _paired_version_side_summary(units: list[list[dict[str, Any]]]) -> dict[str, Any]:
    signaled_units = [rows for rows in units if _unit_direction_accuracy(rows) is not None]
    buy_rows = [item for rows in units for item in rows if item.get("expected_direction") == "up"]
    evaluated_buys = [item for item in buy_rows if isinstance(item.get("direction_hit"), bool)]
    return {
        "signal_coverage": round(len(signaled_units) / len(units), 3) if units else 0.0,
        "abstention_rate": round(1 - len(signaled_units) / len(units), 3) if units else 0.0,
        "buy_signal_precision": round(
            sum(item.get("direction_hit") is True for item in evaluated_buys) / len(evaluated_buys),
            3,
        ) if evaluated_buys else 0.0,
    }


def compare_paired_prediction_versions(
    outcomes: list[dict[str, Any]],
    *,
    candidate_version: str,
    baseline_version: str = "price_action_v1",
) -> dict[str, Any]:
    """Compare versions only on shared date+stock independent units."""
    canonical = [
        item
        for item in _canonical_outcomes(outcomes)
        if item.get("status") == "verified" and not _verified_numeric_errors(item)
    ]
    grouped: dict[tuple[str, tuple[str, str]], list[dict[str, Any]]] = {}
    for item in canonical:
        version = str(item.get("model_version") or item.get("strategy_version") or "")
        unit_key = _comparison_unit_key(item)
        if version in {baseline_version, candidate_version} and unit_key is not None:
            grouped.setdefault((version, unit_key), []).append(item)

    baseline_keys = {key for version, key in grouped if version == baseline_version}
    candidate_keys = {key for version, key in grouped if version == candidate_version}
    paired_keys = sorted(baseline_keys & candidate_keys)
    baseline_units = [grouped[(baseline_version, key)] for key in paired_keys]
    candidate_units = [grouped[(candidate_version, key)] for key in paired_keys]
    paired_horizon_identity_complete = bool(paired_keys) and all(
        _normalized_horizon_identity(item) is not None
        for rows in [*baseline_units, *candidate_units]
        for item in rows
    )
    identical_independent_units = (
        bool(paired_keys)
        and paired_horizon_identity_complete
        and baseline_keys == candidate_keys
        and all(
            sorted(_normalized_horizon_identity(item) for item in baseline_rows)
            == sorted(_normalized_horizon_identity(item) for item in candidate_rows)
            for baseline_rows, candidate_rows in zip(
                baseline_units, candidate_units, strict=True
            )
        )
    )
    baseline_summary = _paired_version_side_summary(baseline_units)
    candidate_summary = _paired_version_side_summary(candidate_units)
    paired_rows = [item for rows in [*baseline_units, *candidate_units] for item in rows]
    paired_benchmark_coverage_complete = bool(paired_rows) and all(
        _benchmark_coverage_complete(item) for item in paired_rows
    )
    paired_tradable_coverage_complete = bool(paired_rows) and all(
        item.get("tradable") is True for item in paired_rows
    )
    paired_actual_cost_coverage_complete = bool(paired_rows) and all(
        _actual_cost_complete(item) for item in paired_rows
    )

    paired_differences: list[float] = []
    for baseline_rows, candidate_rows in zip(baseline_units, candidate_units, strict=True):
        baseline_accuracy = _unit_direction_accuracy(baseline_rows)
        candidate_accuracy = _unit_direction_accuracy(candidate_rows)
        if baseline_accuracy is not None and candidate_accuracy is not None:
            paired_differences.append(candidate_accuracy - baseline_accuracy)

    paired_profit_differences: list[float] = []
    for baseline_rows, candidate_rows in zip(baseline_units, candidate_units, strict=True):
        baseline_profit = _unit_net_expectancy_after_cost(baseline_rows)
        candidate_profit = _unit_net_expectancy_after_cost(candidate_rows)
        if baseline_profit is not None and candidate_profit is not None:
            paired_profit_differences.append(candidate_profit - baseline_profit)

    accuracy_delta, ci_lower, ci_upper, accuracy_outputs_finite = _paired_mean_ci95(
        paired_differences
    )
    profit_delta, profit_ci_lower, profit_ci_upper, profit_outputs_finite = _paired_mean_ci95(
        paired_profit_differences
    )
    aggregate_outputs_finite = accuracy_outputs_finite and profit_outputs_finite
    coverage_collapsed = (
        candidate_summary["signal_coverage"] < baseline_summary["signal_coverage"]
    )
    reasons: list[str] = []
    if len(paired_keys) < DEFAULT_MIN_INDEPENDENT_SAMPLES:
        reasons.append("paired_units_below_60")
    if not aggregate_outputs_finite:
        reasons.append("paired_aggregate_not_finite")
    if not paired_horizon_identity_complete:
        reasons.append("paired_horizon_identity_incomplete")
    if not identical_independent_units:
        reasons.append("version_unit_or_horizon_sets_not_identical")
    if len(paired_differences) < 2:
        reasons.append("paired_direction_units_below_2")
    if ci_lower <= 0 <= ci_upper:
        reasons.append("accuracy_ci_overlaps_zero")
    if accuracy_delta <= 0:
        reasons.append("accuracy_delta_not_positive")
    if len(paired_profit_differences) < 2:
        reasons.append("paired_profit_units_below_2")
    if profit_ci_lower <= 0 <= profit_ci_upper:
        reasons.append("net_profit_ci_overlaps_zero")
    if profit_delta <= 0:
        reasons.append("net_profit_delta_not_positive")
    if coverage_collapsed:
        reasons.append("candidate_coverage_below_baseline")
    paired_evidence_complete = (
        len(paired_differences) == len(paired_keys)
        and len(paired_profit_differences) == len(paired_keys)
        and paired_benchmark_coverage_complete
        and paired_tradable_coverage_complete
        and paired_actual_cost_coverage_complete
    )
    if not paired_evidence_complete:
        reasons.append("paired_evidence_incomplete")
    return {
        "baseline_version": baseline_version,
        "candidate_version": candidate_version,
        "paired_unit_count": len(paired_keys),
        "paired_direction_unit_count": len(paired_differences),
        "paired_profit_unit_count": len(paired_profit_differences),
        "paired_benchmark_coverage_complete": paired_benchmark_coverage_complete,
        "paired_tradable_coverage_complete": paired_tradable_coverage_complete,
        "paired_actual_cost_coverage_complete": paired_actual_cost_coverage_complete,
        "paired_horizon_identity_complete": paired_horizon_identity_complete,
        "paired_aggregate_outputs_finite": aggregate_outputs_finite,
        "identical_independent_units": identical_independent_units,
        "baseline": baseline_summary,
        "candidate": candidate_summary,
        "baseline_signal_coverage": baseline_summary["signal_coverage"],
        "candidate_signal_coverage": candidate_summary["signal_coverage"],
        "accuracy_delta": round(accuracy_delta, 3),
        "accuracy_delta_ci95": [round(ci_lower, 3), round(ci_upper, 3)],
        "net_expectancy_delta_after_cost_pct": round(profit_delta, 3),
        "net_expectancy_delta_ci95": [round(profit_ci_lower, 3), round(profit_ci_upper, 3)],
        "coverage_collapsed": coverage_collapsed,
        "improvement": not reasons,
        "reasons": reasons,
    }


def filter_execution_attributed_outcomes(
    outcomes: list[dict[str, Any]],
    *,
    attribution: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return only outcomes linked to a complete execution lifecycle chain."""
    identity_fields = ("code", "signal_id", "recommendation_id", "fill_id")

    def identity(item: dict[str, Any]) -> tuple[str, str, str, str] | None:
        values = tuple(str(item.get(field) or "").strip() for field in identity_fields)
        return values if all(values) else None

    chains_by_fill: dict[str, list[tuple[str, str, str, str]]] = {}
    for chain in attribution.get("chains", []):
        chain_identity = identity(chain)
        terminal_outcomes = [
            event
            for event in chain.get("events", [])
            if event.get("event_type") == "outcome"
            and str(
                (event.get("payload") or {}).get("state")
                or (event.get("payload") or {}).get("status")
                or ""
            ).strip().lower()
            in {"closed", "terminal"}
        ]
        if (
            chain.get("status") == "attributed"
            and chain_identity is not None
            and len(terminal_outcomes) == 1
        ):
            chains_by_fill.setdefault(chain_identity[-1], []).append(chain_identity)

    unique_chain_identities = {
        rows[0]
        for rows in chains_by_fill.values()
        if len(rows) == 1
    }
    return [
        item
        for item in outcomes
        if identity(item) in unique_chain_identities
    ]


def summarize_execution_attributed_outcomes(
    outcomes: list[dict[str, Any]],
    *,
    attribution: dict[str, Any],
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize executed strategy outcomes without altering shadow metrics."""
    filtered = filter_execution_attributed_outcomes(outcomes, attribution=attribution)
    summary = summarize_prediction_outcomes(filtered, diagnostics=diagnostics)
    return {
        **summary,
        "input_count": len(outcomes),
        "attributed_count": len(filtered),
        "excluded_unattributed_count": len(outcomes) - len(filtered),
        "metric_scope": "complete_execution_attribution_only",
    }


def suggest_strategy_adjustments(
    outcomes: list[dict[str, Any]],
    *,
    min_samples: int = DEFAULT_MIN_INDEPENDENT_SAMPLES,
    diagnostics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Convert verified outcome groups into auditable strategy adjustment suggestions."""
    canonical_verified = [
        item for item in _canonical_outcomes(outcomes) if item.get("status") == "verified"
    ]
    malformed_outcome_count = sum(bool(_verified_numeric_errors(item)) for item in canonical_verified)
    verified = [item for item in canonical_verified if not _verified_numeric_errors(item)]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in verified:
        version = str(item.get("model_version") or item.get("strategy_version") or "legacy")
        for reason in item.get("reasons") or ["unclassified"]:
            grouped.setdefault((version, str(reason)), []).append(item)

    suggestions: list[dict[str, Any]] = []
    for (version, reason), rows in sorted(grouped.items()):
        independent_sample_count = len(
            {_independent_unit_key(item, str(index)) for index, item in enumerate(rows)}
        )
        avg_excess = round(sum(_to_float(item.get("excess_return_pct")) for item in rows) / len(rows), 2)
        direction_rows = _direction_rows(rows)
        accuracy = (
            round(sum(1 for item in direction_rows if item.get("direction_hit")) / len(direction_rows), 3)
            if direction_rows
            else 0.0
        )
        malformed_line_count = int((diagnostics or {}).get("malformed_line_count") or 0)
        benchmark_coverage_complete = all(_benchmark_coverage_complete(item) for item in rows)
        tradable_coverage_complete = all(item.get("tradable") is True for item in rows)
        actual_cost_coverage_complete = all(_actual_cost_complete(item) for item in rows)
        profit_metrics = _profit_metrics(rows)
        abstention_count = sum(item.get("expected_direction") == "neutral" for item in rows)
        signal_coverage = (len(rows) - abstention_count) / len(rows) if rows else 0.0
        promotion_eligible = (
            independent_sample_count >= min_samples
            and malformed_line_count == 0
            and malformed_outcome_count == 0
            and benchmark_coverage_complete
            and tradable_coverage_complete
            and actual_cost_coverage_complete
            and profit_metrics["buy_signal_evaluated_count"] > 0
            and signal_coverage >= DEFAULT_MIN_SIGNAL_COVERAGE
            and profit_metrics["net_expectancy_after_cost_pct"] > 0
            and (
                profit_metrics["profit_factor_unbounded"] is True
                or (
                    profit_metrics["profit_factor"] is not None
                    and profit_metrics["profit_factor"] > 1
                )
            )
        )
        insufficient_direction_evidence = not direction_rows
        if independent_sample_count >= min_samples and (
            avg_excess <= -1 or (direction_rows and accuracy < 0.45)
        ):
            action = "downweight_trigger"
        elif not promotion_eligible:
            action = "research_only"
        elif avg_excess >= 1 and accuracy >= 0.55:
            action = "upweight_trigger"
        else:
            action = "keep_weight"
        suggestions.append({
            "trigger_reason": reason,
            "model_version": version,
            "sample_count": independent_sample_count,
            "outcome_count": len(rows),
            "minimum_samples": min_samples,
            "avg_excess_return_pct": avg_excess,
            "direction_accuracy": accuracy,
            "direction_evaluated_count": len(direction_rows),
            "insufficient_direction_evidence": insufficient_direction_evidence,
            "malformed_line_count": malformed_line_count,
            "malformed_outcome_count": malformed_outcome_count,
            "benchmark_coverage_complete": benchmark_coverage_complete,
            "tradable_coverage_complete": tradable_coverage_complete,
            "actual_cost_coverage_complete": actual_cost_coverage_complete,
            "cost_coverage_complete": actual_cost_coverage_complete,
            "signal_coverage": round(signal_coverage, 3),
            **profit_metrics,
            "research_only": not promotion_eligible,
            "promotion_eligible": promotion_eligible,
            "action": action,
        })
    return suggestions

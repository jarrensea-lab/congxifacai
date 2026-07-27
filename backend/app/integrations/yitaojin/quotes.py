"""Limited-scope Yitaojin quote validation and runtime snapshots."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence
from zoneinfo import ZoneInfo

from app.integrations.yitaojin.models import (
    BridgeCommand,
    BrokerPosition,
    QuoteSnapshot,
    SnapshotValidationError,
    YitaojinError,
    normalize_stock_code,
)
from app.integrations.yitaojin.planner import build_desired_codes


PROJECT_TIMEZONE = ZoneInfo("Asia/Shanghai")
HALTED_STATES = frozenset({"halted", "suspended", "停牌"})
LIMIT_STATES = frozenset(
    {
        "limit_up",
        "limit_down",
        "涨停",
        "跌停",
        "at_limit_up",
        "at_limit_down",
    }
)


@dataclass(frozen=True)
class QuoteValidation:
    code: str
    status: Literal["fresh", "stale", "conflict", "missing", "halted"]
    age_seconds: float | None
    divergence_pct: Decimal | None
    blocks_new_entry: bool
    requires_manual_price_check: bool
    reasons: tuple[str, ...]
    market_time: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.divergence_pct is not None:
            payload["divergence_pct"] = str(self.divergence_pct)
        return payload


@dataclass(frozen=True)
class QuoteRefreshResult:
    status: str
    enabled: bool
    captured_at: str
    requested_codes: tuple[str, ...]
    validations: dict[str, QuoteValidation]
    reasons: tuple[str, ...] = ()

    def to_summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "status": self.status,
            "as_of": self.captured_at,
            "requested_codes": list(self.requested_codes),
            "validations": {
                code: validation.to_dict()
                for code, validation in sorted(self.validations.items())
            },
            "reasons": list(self.reasons),
        }


def collect_quote_codes(
    pool_payload: Mapping[str, object],
    positions: Sequence[BrokerPosition],
) -> tuple[str, ...]:
    """Return only holdings and production target-pool codes."""
    return tuple(sorted(build_desired_codes(pool_payload, positions)))


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=PROJECT_TIMEZONE)
    return value.astimezone(PROJECT_TIMEZONE)


def _reference_value(
    reference: Mapping[str, Any] | None,
) -> tuple[Decimal, datetime] | None:
    if not isinstance(reference, Mapping):
        return None
    raw_price = reference.get("price")
    raw_time = (
        reference.get("quote_timestamp")
        or reference.get("market_time")
        or reference.get("captured_at")
    )
    if raw_price is None or not isinstance(raw_time, str):
        return None
    try:
        price = Decimal(str(raw_price))
        timestamp = datetime.fromisoformat(raw_time.strip().replace("Z", "+00:00"))
    except (InvalidOperation, ValueError):
        return None
    if (
        not price.is_finite()
        or price <= 0
        or timestamp.tzinfo is None
        or timestamp.utcoffset() is None
    ):
        return None
    return price, timestamp


def validate_quote(
    yitaojin: QuoteSnapshot | None,
    reference: Mapping[str, Any] | None,
    *,
    now: datetime,
    max_age_seconds: int,
    max_divergence_pct: Decimal,
    code: str | None = None,
    max_reference_skew_seconds: int = 90,
) -> QuoteValidation:
    if yitaojin is None:
        if code is None:
            raise SnapshotValidationError("missing quote requires a stock code")
        normalized = normalize_stock_code(code)
        return QuoteValidation(
            code=normalized,
            status="missing",
            age_seconds=None,
            divergence_pct=None,
            blocks_new_entry=True,
            requires_manual_price_check=True,
            reasons=("quote_missing",),
        )
    if max_age_seconds <= 0 or max_reference_skew_seconds < 0:
        raise ValueError("quote age thresholds must be nonnegative")
    if max_divergence_pct < 0:
        raise ValueError("max_divergence_pct must be nonnegative")

    current = _aware(now)
    market_time = _aware(yitaojin.market_time)
    age_seconds = (current - market_time).total_seconds()
    normalized_status = yitaojin.status.strip().lower()
    reasons: list[str] = []
    status: Literal["fresh", "stale", "conflict", "missing", "halted"] = "fresh"
    manual_check = False
    blocks_entry = False
    divergence_pct: Decimal | None = None

    required_values = (
        yitaojin.change_pct,
        yitaojin.volume,
        yitaojin.amount,
        yitaojin.high,
        yitaojin.low,
        yitaojin.previous_close,
    )
    if yitaojin.price <= 0:
        status = "halted"
        reasons.append("nonpositive_price")
        blocks_entry = True
        manual_check = True
    elif any(value is None for value in required_values):
        status = "missing"
        reasons.append("quote_fields_incomplete")
        blocks_entry = True
        manual_check = True
    elif normalized_status in HALTED_STATES:
        status = "halted"
        reasons.append(normalized_status)
        blocks_entry = True
    elif age_seconds < 0:
        status = "conflict"
        reasons.append("market_time_in_future")
        blocks_entry = True
        manual_check = True
    elif age_seconds > max_age_seconds:
        status = "stale"
        reasons.append("quote_stale")
        blocks_entry = True
        manual_check = True

    if normalized_status in LIMIT_STATES:
        reasons.append(normalized_status)
        blocks_entry = True

    reference_value = _reference_value(reference)
    if reference_value is not None:
        reference_price, reference_time = reference_value
        skew = abs((market_time - _aware(reference_time)).total_seconds())
        if skew <= max_reference_skew_seconds:
            divergence_pct = (
                abs(yitaojin.price - reference_price) / reference_price * Decimal("100")
            )
            if divergence_pct > max_divergence_pct:
                status = "conflict"
                reasons.append("price_divergence")
                blocks_entry = True
                manual_check = True

    return QuoteValidation(
        code=yitaojin.code,
        status=status,
        age_seconds=age_seconds,
        divergence_pct=divergence_pct,
        blocks_new_entry=blocks_entry,
        requires_manual_price_check=manual_check,
        reasons=tuple(dict.fromkeys(reasons)),
        market_time=yitaojin.market_time.isoformat(),
    )


def _quote_payload(quote: QuoteSnapshot) -> dict[str, Any]:
    return {
        "code": quote.code,
        "capturedAt": quote.captured_at.isoformat(),
        "marketTime": quote.market_time.isoformat(),
        "price": str(quote.price),
        "changePct": (str(quote.change_pct) if quote.change_pct is not None else None),
        "volume": str(quote.volume) if quote.volume is not None else None,
        "amount": str(quote.amount) if quote.amount is not None else None,
        "high": str(quote.high) if quote.high is not None else None,
        "low": str(quote.low) if quote.low is not None else None,
        "previousClose": (
            str(quote.previous_close) if quote.previous_close is not None else None
        ),
        "status": quote.status,
    }


def _atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


class YitaojinQuoteService:
    def __init__(
        self,
        *,
        bridge,
        snapshot_path: str | Path,
        environment: Mapping[str, str] | None = None,
        now_provider=None,
        regular_max_age_seconds: int = 90,
        action_max_age_seconds: int = 30,
        max_divergence_pct: Decimal = Decimal("0.5"),
        bridge_timeout_seconds: float = 15.0,
    ) -> None:
        self.bridge = bridge
        self.snapshot_path = Path(snapshot_path)
        self.environment = dict(os.environ if environment is None else environment)
        self.now_provider = now_provider or (lambda: datetime.now(tz=PROJECT_TIMEZONE))
        self.regular_max_age_seconds = regular_max_age_seconds
        self.action_max_age_seconds = action_max_age_seconds
        self.max_divergence_pct = max_divergence_pct
        self.bridge_timeout_seconds = bridge_timeout_seconds

    def refresh(
        self,
        *,
        pool_payload: Mapping[str, object],
        positions: Sequence[BrokerPosition],
        reference_quotes: Mapping[str, Mapping[str, Any]] | None = None,
        critical_codes: set[str] | None = None,
    ) -> QuoteRefreshResult:
        now = self.now_provider()
        captured_at = _aware(now).isoformat()
        enabled = (
            self.environment.get("CONGXI_YITAOJIN_ENABLED", "").strip().lower()
            == "true"
        )
        codes = collect_quote_codes(pool_payload, positions)
        if not enabled:
            result = QuoteRefreshResult(
                status="not_enabled",
                enabled=False,
                captured_at=captured_at,
                requested_codes=codes,
                validations={},
            )
            self._persist(result, quotes={})
            return result
        if not codes:
            result = QuoteRefreshResult(
                status="ok",
                enabled=True,
                captured_at=captured_at,
                requested_codes=(),
                validations={},
            )
            self._persist(result, quotes={})
            return result
        if len(codes) > 32:
            validations = {
                code: validate_quote(
                    None,
                    None,
                    now=now,
                    max_age_seconds=self.regular_max_age_seconds,
                    max_divergence_pct=self.max_divergence_pct,
                    code=code,
                )
                for code in codes
            }
            result = QuoteRefreshResult(
                status="blocked",
                enabled=True,
                captured_at=captured_at,
                requested_codes=codes,
                validations=validations,
                reasons=("quote_scope_too_large",),
            )
            self._persist(result, quotes={})
            return result

        try:
            payload = self.bridge.run(
                BridgeCommand.READ_QUOTES,
                {"codes": list(codes)},
                timeout=self.bridge_timeout_seconds,
            )
            raw_quotes = payload.get("quotes")
            if not isinstance(raw_quotes, list):
                raise SnapshotValidationError("quotes must be a list")
            quotes = [
                QuoteSnapshot.from_bridge_payload(item)
                for item in raw_quotes
                if isinstance(item, Mapping)
            ]
            if len(quotes) != len(raw_quotes):
                raise SnapshotValidationError("quotes must contain objects")
            by_code = {quote.code: quote for quote in quotes}
            if len(by_code) != len(quotes):
                raise SnapshotValidationError("duplicate quote code")
            if not set(by_code).issubset(codes):
                raise SnapshotValidationError("unexpected quote code")
        except (YitaojinError, SnapshotValidationError):
            result = QuoteRefreshResult(
                status="unavailable",
                enabled=True,
                captured_at=captured_at,
                requested_codes=codes,
                validations={
                    code: validate_quote(
                        None,
                        None,
                        now=now,
                        max_age_seconds=self.regular_max_age_seconds,
                        max_divergence_pct=self.max_divergence_pct,
                        code=code,
                    )
                    for code in codes
                },
                reasons=("quote_read_failed",),
            )
            self._persist(result, quotes={})
            return result

        critical = {normalize_stock_code(code) for code in (critical_codes or set())}
        references = reference_quotes or {}
        validations = {
            code: validate_quote(
                by_code.get(code),
                references.get(code),
                now=now,
                max_age_seconds=(
                    self.action_max_age_seconds
                    if code in critical
                    else self.regular_max_age_seconds
                ),
                max_divergence_pct=self.max_divergence_pct,
                code=code,
            )
            for code in codes
        }
        status = (
            "blocked"
            if any(item.blocks_new_entry for item in validations.values())
            else "ok"
        )
        result = QuoteRefreshResult(
            status=status,
            enabled=True,
            captured_at=captured_at,
            requested_codes=codes,
            validations=validations,
        )
        self._persist(result, quotes=by_code)
        return result

    def _persist(
        self,
        result: QuoteRefreshResult,
        *,
        quotes: Mapping[str, QuoteSnapshot],
    ) -> None:
        payload = {
            "schema_version": 1,
            **result.to_summary(),
            "quotes": {
                code: _quote_payload(quote) for code, quote in sorted(quotes.items())
            },
        }
        _atomic_json_write(self.snapshot_path, payload)


def _unavailable_summary(
    *,
    enabled: bool,
    status: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "status": status,
        "as_of": None,
        "requested_codes": [],
        "validations": {},
        "reasons": [reason],
    }


def load_quote_validation_summary(
    path: str | Path,
    *,
    environment: Mapping[str, str] | None = None,
    now: datetime | None = None,
    critical_codes: set[str] | None = None,
    reference_quotes: Mapping[str, Mapping[str, Any]] | None = None,
    regular_max_age_seconds: int = 90,
    action_max_age_seconds: int = 30,
    max_divergence_pct: Decimal = Decimal("0.5"),
) -> dict[str, Any]:
    effective_environment = dict(os.environ if environment is None else environment)
    enabled = (
        effective_environment.get("CONGXI_YITAOJIN_ENABLED", "").strip().lower()
        == "true"
    )
    if not enabled:
        return _unavailable_summary(
            enabled=False,
            status="not_enabled",
            reason="quote_validation_not_enabled",
        )
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if (
            not isinstance(payload, Mapping)
            or payload.get("schema_version") != 1
            or payload.get("enabled") is not True
        ):
            raise SnapshotValidationError("quote snapshot schema is invalid")
        raw_codes = payload.get("requested_codes")
        raw_quotes = payload.get("quotes")
        if not isinstance(raw_codes, list) or not isinstance(raw_quotes, Mapping):
            raise SnapshotValidationError("quote snapshot is incomplete")
        codes = tuple(normalize_stock_code(code) for code in raw_codes)
        quotes = {
            normalize_stock_code(code): QuoteSnapshot.from_bridge_payload(item)
            for code, item in raw_quotes.items()
            if isinstance(item, Mapping)
        }
    except (
        OSError,
        json.JSONDecodeError,
        SnapshotValidationError,
        TypeError,
    ):
        return _unavailable_summary(
            enabled=True,
            status="unavailable",
            reason="quote_snapshot_unavailable",
        )

    effective_now = now or datetime.now(tz=PROJECT_TIMEZONE)
    critical = {normalize_stock_code(code) for code in (critical_codes or set())}
    references = reference_quotes or {}
    validations = {
        code: validate_quote(
            quotes.get(code),
            references.get(code),
            now=effective_now,
            max_age_seconds=(
                action_max_age_seconds if code in critical else regular_max_age_seconds
            ),
            max_divergence_pct=max_divergence_pct,
            code=code,
        )
        for code in codes
    }
    status = (
        "blocked"
        if any(item.blocks_new_entry for item in validations.values())
        else "ok"
    )
    return {
        "enabled": True,
        "status": status,
        "as_of": payload.get("as_of"),
        "requested_codes": list(codes),
        "validations": {
            code: validation.to_dict()
            for code, validation in sorted(validations.items())
        },
        "reasons": [],
    }

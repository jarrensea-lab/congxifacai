"""Validated domain contracts for the Yitaojin UI bridge."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Mapping


class YitaojinError(RuntimeError):
    """Base integration error."""


class BridgeUnavailableError(YitaojinError):
    """The local bridge cannot provide a valid response."""


class AccessibilityPermissionError(YitaojinError):
    """macOS Accessibility permission is unavailable."""


class AppNotLoggedInError(YitaojinError):
    """The broker application is not logged in."""


class AccountMismatchError(YitaojinError):
    """The observed account differs from the bound account."""


class SnapshotValidationError(YitaojinError):
    """Bridge data is incomplete, malformed, or unsafe."""


class UnsafeUiTargetError(YitaojinError):
    """A requested UI target crosses the integration safety boundary."""


class BridgeCommand(StrEnum):
    PROBE = "probe"
    READ_ACCOUNT = "read_account"
    READ_WATCHLIST = "read_watchlist"
    READ_QUOTES = "read_quotes"
    ADD_WATCHLIST = "add_watchlist"
    REMOVE_WATCHLIST = "remove_watchlist"


_CODE_PATTERN = re.compile(r"^\d{6}$")
_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SENSITIVE_KEYS = frozenset(
    {
        "accountnumber",
        "fullaccount",
        "mobile",
        "phone",
        "phonenumber",
        "shareholderaccount",
        "password",
        "tradepassword",
        "verificationcode",
        "smscode",
    }
)


def normalize_stock_code(value: Any) -> str:
    if not isinstance(value, str) or not _CODE_PATTERN.fullmatch(value.strip()):
        raise SnapshotValidationError("code must be a canonical 6-digit string")
    return value.strip()


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _reject_sensitive_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if _normalized_key(key) in _SENSITIVE_KEYS:
                raise SnapshotValidationError("sensitive account field is forbidden")
            _reject_sensitive_fields(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_sensitive_fields(nested)


def _decimal(value: Any, field: str, *, nonnegative: bool = True) -> Decimal:
    if value is None or isinstance(value, bool):
        raise SnapshotValidationError(f"{field} must be a finite decimal")
    if isinstance(value, str) and not value.strip():
        raise SnapshotValidationError(f"{field} must be a finite decimal")
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise SnapshotValidationError(f"{field} must be a finite decimal") from None
    if not parsed.is_finite():
        raise SnapshotValidationError(f"{field} must be a finite decimal")
    if nonnegative and parsed < 0:
        raise SnapshotValidationError(f"{field} must be nonnegative")
    return parsed


def _optional_decimal(
    value: Any,
    field: str,
    *,
    nonnegative: bool = True,
) -> Decimal | None:
    if value is None or value == "":
        return None
    return _decimal(value, field, nonnegative=nonnegative)


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise SnapshotValidationError(f"{field} must be a nonnegative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise SnapshotValidationError(
            f"{field} must be a nonnegative integer"
        ) from None
    if str(parsed) != str(value).strip() or parsed < 0:
        raise SnapshotValidationError(f"{field} must be a nonnegative integer")
    return parsed


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SnapshotValidationError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise SnapshotValidationError(f"{field} must be an ISO timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SnapshotValidationError(f"{field} must include a timezone")
    return parsed


def _decimal_text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


@dataclass(frozen=True)
class BrokerPosition:
    code: str
    name: str
    shares: int
    available_shares: int
    average_cost: Decimal
    current_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal

    @classmethod
    def from_bridge_payload(cls, payload: Mapping[str, Any]) -> BrokerPosition:
        code = normalize_stock_code(payload.get("code"))
        name = str(payload.get("name") or "").strip()
        if not name:
            raise SnapshotValidationError(f"name is required for {code}")
        shares = _nonnegative_int(payload.get("shares"), "shares")
        available_shares = _nonnegative_int(
            payload.get("availableShares"),
            "availableShares",
        )
        if shares <= 0:
            raise SnapshotValidationError("shares must be positive")
        if available_shares > shares:
            raise SnapshotValidationError("availableShares exceeds shares")
        return cls(
            code=code,
            name=name,
            shares=shares,
            available_shares=available_shares,
            average_cost=_decimal(payload.get("averageCost"), "averageCost"),
            current_price=_decimal(payload.get("currentPrice"), "currentPrice"),
            market_value=_decimal(payload.get("marketValue"), "marketValue"),
            unrealized_pnl=_decimal(
                payload.get("unrealizedPnl"),
                "unrealizedPnl",
                nonnegative=False,
            ),
        )

    def to_persisted_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "shares": self.shares,
            "available_shares": self.available_shares,
            "average_cost": _decimal_text(self.average_cost),
            "current_price": _decimal_text(self.current_price),
            "market_value": _decimal_text(self.market_value),
            "unrealized_pnl": _decimal_text(self.unrealized_pnl),
        }


@dataclass(frozen=True)
class AccountSnapshot:
    captured_at: datetime
    account_fingerprint: str
    total_assets: Decimal
    available_cash: Decimal
    frozen_cash: Decimal
    positions: tuple[BrokerPosition, ...]
    empty_positions_confirmed: bool = False
    source: str = "yitaojin_ui"

    @classmethod
    def from_bridge_payload(cls, payload: Mapping[str, Any]) -> AccountSnapshot:
        if not isinstance(payload, Mapping):
            raise SnapshotValidationError("account payload must be an object")
        _reject_sensitive_fields(payload)
        fingerprint = str(payload.get("accountFingerprint") or "").strip()
        if not _FINGERPRINT_PATTERN.fullmatch(fingerprint):
            raise SnapshotValidationError("accountFingerprint is invalid")
        raw_positions = payload.get("positions")
        if not isinstance(raw_positions, list):
            raise SnapshotValidationError("positions must be a list")
        positions = tuple(
            BrokerPosition.from_bridge_payload(item)
            for item in raw_positions
            if isinstance(item, Mapping)
        )
        if len(positions) != len(raw_positions):
            raise SnapshotValidationError("positions must contain objects")
        codes = [position.code for position in positions]
        if len(codes) != len(set(codes)):
            raise SnapshotValidationError("duplicate position code")
        empty_confirmed = payload.get("emptyPositionsConfirmed", False)
        if not isinstance(empty_confirmed, bool):
            raise SnapshotValidationError("emptyPositionsConfirmed must be boolean")
        return cls(
            captured_at=_timestamp(payload.get("capturedAt"), "capturedAt"),
            account_fingerprint=fingerprint,
            total_assets=_decimal(payload.get("totalAssets"), "totalAssets"),
            available_cash=_decimal(payload.get("availableCash"), "availableCash"),
            frozen_cash=_decimal(payload.get("frozenCash"), "frozenCash"),
            positions=positions,
            empty_positions_confirmed=empty_confirmed,
        )

    def to_persisted_dict(self) -> dict[str, Any]:
        return {
            "captured_at": self.captured_at.isoformat(),
            "account_fingerprint": self.account_fingerprint,
            "total_assets": _decimal_text(self.total_assets),
            "available_cash": _decimal_text(self.available_cash),
            "frozen_cash": _decimal_text(self.frozen_cash),
            "positions": [
                position.to_persisted_dict() for position in self.positions
            ],
            "empty_positions_confirmed": self.empty_positions_confirmed,
            "source": self.source,
        }


@dataclass(frozen=True)
class QuoteSnapshot:
    code: str
    captured_at: datetime
    market_time: datetime
    price: Decimal
    change_pct: Decimal | None
    volume: Decimal | None
    amount: Decimal | None
    high: Decimal | None
    low: Decimal | None
    previous_close: Decimal | None
    status: str

    @classmethod
    def from_bridge_payload(cls, payload: Mapping[str, Any]) -> QuoteSnapshot:
        if not isinstance(payload, Mapping):
            raise SnapshotValidationError("quote payload must be an object")
        status = str(payload.get("status") or "").strip().lower()
        if not status:
            raise SnapshotValidationError("status is required")
        return cls(
            code=normalize_stock_code(payload.get("code")),
            captured_at=_timestamp(payload.get("capturedAt"), "capturedAt"),
            market_time=_timestamp(payload.get("marketTime"), "marketTime"),
            price=_decimal(payload.get("price"), "price"),
            change_pct=_optional_decimal(
                payload.get("changePct"),
                "changePct",
                nonnegative=False,
            ),
            volume=_optional_decimal(payload.get("volume"), "volume"),
            amount=_optional_decimal(payload.get("amount"), "amount"),
            high=_optional_decimal(payload.get("high"), "high"),
            low=_optional_decimal(payload.get("low"), "low"),
            previous_close=_optional_decimal(
                payload.get("previousClose"),
                "previousClose",
            ),
            status=status,
        )


@dataclass(frozen=True)
class WatchlistPlan:
    add: tuple[str, ...]
    remove: tuple[str, ...]
    keep: tuple[str, ...]
    blocked: tuple[str, ...]
    reasons: tuple[str, ...]
    desired_codes: tuple[str, ...]
    current_codes: tuple[str, ...]
    managed_codes: tuple[str, ...]
    manual_protected_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

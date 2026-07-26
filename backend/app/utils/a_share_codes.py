"""Canonical validation and vendor routing for mainland A-share stock codes."""
from __future__ import annotations

import re
from typing import Any


_PREFIXED_CODE = re.compile(r"^(SH|SZ|BJ)(\d{6})$")
_SUFFIXED_CODE = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$")
_PLAIN_CODE = re.compile(r"^\d{6}$")
_SH_PREFIXES = ("600", "601", "603", "605", "688", "689")
_SZ_PREFIXES = ("000", "001", "002", "003", "300", "301", "302")


def _derived_exchange(code: str) -> str | None:
    if code.startswith(_SH_PREFIXES):
        return "SH"
    if code.startswith(_SZ_PREFIXES):
        return "SZ"
    if code.startswith("920"):
        return "BJ"
    if code.startswith(("4", "8")) and not code.startswith("899"):
        return "BJ"
    return None


def normalize_a_share_code(value: Any) -> str:
    """Return six digits for a valid A-share stock, otherwise raise ValueError."""
    raw = str(value or "").strip().upper()
    exchange_hint: str | None = None
    code = raw
    prefixed = _PREFIXED_CODE.fullmatch(raw)
    suffixed = _SUFFIXED_CODE.fullmatch(raw)
    if prefixed:
        exchange_hint, code = prefixed.groups()
    elif suffixed:
        code, exchange_hint = suffixed.groups()
    elif not _PLAIN_CODE.fullmatch(raw):
        raise ValueError(f"invalid A-share code: {value!r}")

    exchange = _derived_exchange(code)
    if exchange is None or (
        exchange_hint is not None and exchange_hint != exchange
    ):
        raise ValueError(f"invalid A-share stock code: {value!r}")
    return code


def validate_a_share_code(value: Any) -> bool:
    try:
        normalize_a_share_code(value)
    except ValueError:
        return False
    return True


def a_share_exchange(value: Any) -> str:
    code = normalize_a_share_code(value)
    exchange = _derived_exchange(code)
    if exchange is None:  # Defensive: normalize already enforces this invariant.
        raise ValueError(f"invalid A-share stock code: {value!r}")
    return exchange


def tencent_symbol(value: Any) -> str:
    code = normalize_a_share_code(value)
    return f"{a_share_exchange(code).lower()}{code}"


def ts_code(value: Any) -> str:
    code = normalize_a_share_code(value)
    return f"{code}.{a_share_exchange(code)}"

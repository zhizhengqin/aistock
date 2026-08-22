"""Canonical A-share ticker normalization with strict ambiguity checks."""

from __future__ import annotations

import re

from app.datahub.errors import DataHubError, DataHubErrorCode


SH_INDEX_CODES = frozenset({"000001", "000016", "000300", "000905", "000852", "000688"})
_VALID_SUFFIXES = {"SH": "SS", "SS": "SS", "SZ": "SZ", "BJ": "BJ"}
_SIX_DIGITS = re.compile(r"^\d{6}$")


def normalise_ticker(value: object) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        raise DataHubError(DataHubErrorCode.VALIDATION, "股票代码不能为空")
    prefix = raw[:2]
    if prefix in {"SH", "SZ", "BJ"}:
        if not _SIX_DIGITS.fullmatch(raw[2:]):
            raise DataHubError(DataHubErrorCode.VALIDATION, "股票代码格式无效")
        return f"{raw[2:]}.{_VALID_SUFFIXES[prefix]}"
    if "." in raw:
        if raw.count(".") != 1:
            raise DataHubError(DataHubErrorCode.VALIDATION, "股票代码格式无效")
        number, suffix = raw.split(".", 1)
        if number[:2] in {"SH", "SZ", "BJ"} or not _SIX_DIGITS.fullmatch(number) or suffix not in _VALID_SUFFIXES:
            raise DataHubError(DataHubErrorCode.VALIDATION, "股票代码格式无效")
        return f"{number}.{_VALID_SUFFIXES[suffix]}"
    if not _SIX_DIGITS.fullmatch(raw):
        raise DataHubError(DataHubErrorCode.VALIDATION, "股票代码必须是 6 位数字")
    if raw.startswith(("92", "4", "8")):
        return f"{raw}.BJ"
    if raw in SH_INDEX_CODES or raw.startswith(("5", "6", "9")):
        return f"{raw}.SS"
    return f"{raw}.SZ"


def vendor_symbol(value: object) -> str:
    code = normalise_ticker(value)
    prefix = {"SS": "sh", "SZ": "sz", "BJ": "bj"}[code.rsplit(".", 1)[1]]
    return prefix + code.split(".", 1)[0]


__all__ = ["SH_INDEX_CODES", "normalise_ticker", "vendor_symbol"]

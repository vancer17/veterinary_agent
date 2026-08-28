"""Deterministic-first temporal and measurement parsers for V6.

These parsers normalize generic linguistic expressions only.  They contain no
medical concepts, never modify a quote, and return an explicit unresolved
reason instead of guessing an unsupported value.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from .v6_contracts import (
    V6NormalizedStatus,
    V6TemporalPrecision,
    V6TemporalRelation,
    V6UnresolvedReason,
)

TEMPORAL_PARSER_VERSION = "v6-temporal-parser-dev-20260826-1"
MEASUREMENT_PARSER_VERSION = "v6-measurement-parser-dev-20260826-1"

_DAY_OFFSET = re.compile(r"^(今天|昨天|前天|大前天)$")
_RECENT_DAYS = re.compile(r"^(?:最近|这)([一二两三四五六七八九十\d]+)天$")
_NUMBER_DURATION = re.compile(r"^([一二三四五六七八九十\d]+)(天|周|小时|分钟)$")
_FREQUENCY = re.compile(r"^(?:一天|每日|每天|每周|每月)([一二三四五六七八九十\d]+)次$")
_NUMERIC_MEASUREMENT = re.compile(
    r"^(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>公斤|千克|克|毫克|毫升|升|次|片|粒|次/天|kg|g|mg|ml|l)$",
    re.IGNORECASE,
)
_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "两": 2,
}


class TemporalParseResult(NamedTuple):
    """Deterministic temporal parser result."""

    relation: V6TemporalRelation | None
    value: str
    precision: V6TemporalPrecision | None
    status: V6NormalizedStatus
    unresolved_reason: V6UnresolvedReason | None


def parse_temporal(
    *,
    temporal_quote: str,
    relation_quote: str = "",
) -> TemporalParseResult:
    """Parse a generic temporal expression without over-precision."""

    quote = temporal_quote.strip()
    if not quote:
        return TemporalParseResult(
            None, "", None, V6NormalizedStatus.NOT_APPLICABLE, None
        )

    if _DAY_OFFSET.fullmatch(quote):
        offsets = {"今天": 0, "昨天": 1, "前天": 2, "大前天": 3}
        relation = (
            V6TemporalRelation.STARTED_AT
            if "开始" in relation_quote or "开始" in quote
            else V6TemporalRelation.UNSTRUCTURED
        )
        return TemporalParseResult(
            relation,
            f"day-{offsets[quote]}",
            V6TemporalPrecision.DAY,
            V6NormalizedStatus.NORMALIZED,
            None,
        )

    recent = _RECENT_DAYS.fullmatch(quote)
    if recent:
        count = _chinese_number(recent.group(1))
        return TemporalParseResult(
            V6TemporalRelation.DURATION,
            f"recent-{count}-days-approximate",
            V6TemporalPrecision.APPROXIMATE_DURATION,
            V6NormalizedStatus.NORMALIZED,
            None,
        )

    duration = _NUMBER_DURATION.fullmatch(quote)
    if duration:
        count = _chinese_number(duration.group(1))
        return TemporalParseResult(
            V6TemporalRelation.DURATION,
            f"{count}-{duration.group(2)}",
            V6TemporalPrecision.APPROXIMATE_DURATION,
            V6NormalizedStatus.NORMALIZED,
            None,
        )

    frequency = _FREQUENCY.fullmatch(quote)
    if frequency:
        count = _chinese_number(frequency.group(1))
        return TemporalParseResult(
            V6TemporalRelation.FREQUENCY,
            f"{count}/day",
            V6TemporalPrecision.FREQUENCY,
            V6NormalizedStatus.NORMALIZED,
            None,
        )

    return TemporalParseResult(
        V6TemporalRelation.UNSTRUCTURED,
        quote,
        V6TemporalPrecision.UNRESOLVED,
        V6NormalizedStatus.UNRESOLVED,
        V6UnresolvedReason.PARSER_UNSUPPORTED,
    )


class MeasurementParseResult(NamedTuple):
    """Deterministic measurement parser result."""

    value: str
    unit: str
    relation: str
    precision: str
    status: V6NormalizedStatus
    unresolved_reason: V6UnresolvedReason | None


def parse_measurement(
    *,
    measurement_quote: str,
    relation_quote: str = "",
) -> MeasurementParseResult:
    """Parse a generic measurement expression without guessing values."""

    quote = measurement_quote.strip()
    if not quote:
        return MeasurementParseResult(
            "",
            "",
            "associated_with",
            "not_applicable",
            V6NormalizedStatus.NOT_APPLICABLE,
            None,
        )

    numeric = _NUMERIC_MEASUREMENT.fullmatch(quote)
    if numeric:
        return MeasurementParseResult(
            numeric.group("value"),
            numeric.group("unit").lower(),
            _measurement_relation(relation_quote or quote),
            "exact",
            V6NormalizedStatus.NORMALIZED,
            None,
        )

    frequency = _FREQUENCY.fullmatch(quote)
    if frequency:
        count = _chinese_number(frequency.group(1))
        return MeasurementParseResult(
            f"{count}/day",
            "day",
            "frequency",
            "frequency",
            V6NormalizedStatus.NORMALIZED,
            None,
        )

    if quote in {"一小把", "半片", "一点", "少许", "适量"}:
        return MeasurementParseResult(
            "",
            "",
            "associated_with",
            "unresolved",
            V6NormalizedStatus.UNRESOLVED,
            V6UnresolvedReason.POLICY_CONSERVATIVE,
        )

    return MeasurementParseResult(
        "",
        "",
        "associated_with",
        "unresolved",
        V6NormalizedStatus.UNRESOLVED,
        V6UnresolvedReason.PARSER_UNSUPPORTED,
    )


def _measurement_relation(text: str) -> str:
    if "每" in text or "/天" in text:
        return "frequency"
    if "重" in text or "公斤" in text or "kg" in text.lower():
        return "weight"
    return "associated_with"


def _chinese_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if "十" in value:
        left, _, right = value.partition("十")
        return _CN_DIGITS.get(left, 1) * 10 + _CN_DIGITS.get(right, 0)
    return _CN_DIGITS.get(value, 0)

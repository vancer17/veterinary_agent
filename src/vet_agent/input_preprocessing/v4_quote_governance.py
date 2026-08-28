"""Conservative quote anchoring for the V4 flat-extraction branch.

The functions in this module only verify that quotes copied by the model can be
found in server-owned source text.  They never perform synonym replacement,
embedding matching, edit-distance repair, or semantic rewriting.
"""

from __future__ import annotations

from dataclasses import dataclass

from .v4_contracts import (
    FlatObservationRaw,
    V4QuoteAnchor,
)

QUOTE_NORMALIZATION_VERSION = "v4-conservative-20260825-1"
_PUNCTUATION = {
    "，": ",",
    "、": ",",
    "。": ".",
    "；": ";",
    "：": ":",
    "！": "!",
    "？": "?",
    "（": "(",
    "）": ")",
    "【": "[",
    "】": "]",
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
}


@dataclass(frozen=True)
class _NormalizedText:
    value: str
    # One (start, end) pair for every character appended to ``value``.
    spans: tuple[tuple[int, int], ...]

    def offsets(self, start: int, end: int) -> tuple[int, int]:
        return self.spans[start][0], self.spans[end - 1][1]


@dataclass(frozen=True)
class _Match:
    start: int
    end: int


def normalize_quote_text(text: str) -> str:
    """Return the conservative normalization used by quote governance."""

    return _normalize(text).value


def resolve_observation_quotes(
    *,
    user_text: str,
    raw: FlatObservationRaw,
) -> tuple[V4QuoteAnchor, V4QuoteAnchor, V4QuoteAnchor | None, V4QuoteAnchor | None]:
    """Resolve evidence, target, temporal, and measurement quote anchors.

    :returns: ``(evidence, target, temporal, measurement)``.  Optional anchors
        are absent when the model did not claim that observation kind.
    :raises ValueError: When an auxiliary quote is supplied with an inactive
        observation status.  The raw contract normally prevents this earlier.
    """

    source = _normalize(user_text)
    evidence = _anchor(
        source=source,
        quote=raw.evidence_quote,
        quote_type="evidence",
    )
    target = _anchor_in_evidence(
        source=source,
        quote=raw.target_quote,
        evidence_quote=raw.evidence_quote,
    )
    temporal = None
    if raw.temporal_quote:
        temporal = _anchor_in_evidence(
            source=source,
            quote=raw.temporal_quote,
            evidence_quote=raw.evidence_quote,
            quote_type="temporal",
        )
    measurement = None
    if raw.measurement_quote:
        measurement = _anchor_in_evidence(
            source=source,
            quote=raw.measurement_quote,
            evidence_quote=raw.evidence_quote,
            quote_type="measurement",
        )
    return evidence, target, temporal, measurement


def _normalize(text: str) -> _NormalizedText:
    characters: list[str] = []
    spans: list[tuple[int, int]] = []
    previous_collapsible = False
    for index, character in enumerate(text):
        if character.isspace():
            continue
        mapped = _PUNCTUATION.get(character, character)
        # Collapse repeated punctuation while preserving the first source span.
        collapsible = len(mapped) == 1 and mapped in {",", ".", ";", ":"}
        if collapsible and previous_collapsible:
            continue
        for _position in range(len(mapped)):
            characters.append(mapped)
            spans.append((index, index + 1))
        previous_collapsible = collapsible
    return _NormalizedText(value="".join(characters), spans=tuple(spans))


def _anchor(
    *,
    source: _NormalizedText,
    quote: str,
    quote_type: str,
) -> V4QuoteAnchor:
    normalized = _normalize(quote).value
    matches = _find_all(source.value, normalized)
    if not matches or not normalized:
        return _unresolved_anchor(
            quote=quote,
            quote_type=quote_type,  # type: ignore[arg-type]
            status="not_found",
        )
    match = matches[0]
    start, end = source.offsets(match.start, match.end)
    return V4QuoteAnchor(
        quote_type=quote_type,  # type: ignore[arg-type]
        raw_quote=quote,
        normalized_quote=normalized,
        start_offset=start,
        end_offset=end,
        occurrence=1,
        status="ambiguous_occurrence" if len(matches) > 1 else "resolved",
        normalization_version=QUOTE_NORMALIZATION_VERSION,
    )


def _anchor_in_evidence(
    *,
    source: _NormalizedText,
    quote: str,
    evidence_quote: str,
    quote_type: str = "target",
) -> V4QuoteAnchor:
    normalized = _normalize(quote).value
    evidence_matches = _find_all(source.value, _normalize(evidence_quote).value)
    if not evidence_matches:
        return _unresolved_anchor(
            quote=quote,
            quote_type=quote_type,  # type: ignore[arg-type]
            status="not_found",
        )
    evidence_match = evidence_matches[0]
    matches = [
        match
        for match in _find_all(source.value, normalized)
        if match.start >= evidence_match.start and match.end <= evidence_match.end
    ]
    if not matches or not normalized:
        return _unresolved_anchor(
            quote=quote,
            quote_type=quote_type,  # type: ignore[arg-type]
            status="not_found",
        )
    match = matches[0]
    start, end = source.offsets(match.start, match.end)
    return V4QuoteAnchor(
        quote_type=quote_type,  # type: ignore[arg-type]
        raw_quote=quote,
        normalized_quote=normalized,
        start_offset=start,
        end_offset=end,
        occurrence=1,
        status="ambiguous_occurrence" if len(matches) > 1 else "resolved",
        normalization_version=QUOTE_NORMALIZATION_VERSION,
    )


def _find_all(source: str, needle: str) -> list[_Match]:
    if not needle:
        return []
    matches: list[_Match] = []
    start = 0
    while True:
        position = source.find(needle, start)
        if position < 0:
            return matches
        matches.append(_Match(start=position, end=position + len(needle)))
        start = position + 1


def _unresolved_anchor(
    *,
    quote: str,
    quote_type: str,
    status: str,
) -> V4QuoteAnchor:
    return V4QuoteAnchor(
        quote_type=quote_type,  # type: ignore[arg-type]
        raw_quote=quote,
        normalized_quote=_normalize(quote).value or " ",
        start_offset=0,
        end_offset=max(1, len(quote)),
        status=status,  # type: ignore[arg-type]
        normalization_version=QUOTE_NORMALIZATION_VERSION,
    )

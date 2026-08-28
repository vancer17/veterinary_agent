"""Conservative quote anchoring for V5 thin claims.

Quote governance only proves that model-copied text exists in the server-owned
source.  It never repairs quotes semantically or with approximate matching.
"""

from __future__ import annotations

from dataclasses import dataclass

from .v5_contracts import ThinUserClaimRaw, V5QuoteAnchor

QUOTE_NORMALIZATION_VERSION = "v5-conservative-20260826-1"
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
    spans: tuple[tuple[int, int], ...]

    def offsets(self, start: int, end: int) -> tuple[int, int]:
        return self.spans[start][0], self.spans[end - 1][1]


@dataclass(frozen=True)
class _Match:
    start: int
    end: int


def normalize_quote_text(text: str) -> str:
    """Return the conservative normalization used by V5."""

    return _normalize(text).value


def resolve_thin_claim_quotes(
    *,
    user_text: str,
    raw: ThinUserClaimRaw,
) -> tuple[V5QuoteAnchor, V5QuoteAnchor, V5QuoteAnchor | None, V5QuoteAnchor | None]:
    """Resolve evidence, target, temporal, and measurement quote anchors."""

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
) -> V5QuoteAnchor:
    normalized = _normalize(quote).value
    matches = _find_all(source.value, normalized)
    if not normalized or not matches:
        return _unresolved_anchor(
            quote=quote,
            quote_type=quote_type,  # type: ignore[arg-type]
            status="not_found",
        )
    match = matches[0]
    start, end = source.offsets(match.start, match.end)
    return V5QuoteAnchor(
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
) -> V5QuoteAnchor:
    normalized = _normalize(quote).value
    evidence_matches = _find_all(
        source.value,
        _normalize(evidence_quote).value,
    )
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
    if not normalized or not matches:
        return _unresolved_anchor(
            quote=quote,
            quote_type=quote_type,  # type: ignore[arg-type]
            status="invalid_containment",
        )
    match = matches[0]
    start, end = source.offsets(match.start, match.end)
    return V5QuoteAnchor(
        quote_type=quote_type,  # type: ignore[arg-type]
        raw_quote=quote,
        normalized_quote=normalized,
        start_offset=start,
        end_offset=end,
        occurrence=1,
        status="ambiguous_occurrence" if len(matches) > 1 else "resolved",
        normalization_version=QUOTE_NORMALIZATION_VERSION,
    )


def _unresolved_anchor(
    *,
    quote: str,
    quote_type: str,
    status: str,
) -> V5QuoteAnchor:
    return V5QuoteAnchor(
        quote_type=quote_type,  # type: ignore[arg-type]
        raw_quote=quote,
        normalized_quote=_normalize(quote).value or quote,
        start_offset=0,
        end_offset=max(1, len(quote)),
        occurrence=0,
        status=status,  # type: ignore[arg-type]
        normalization_version=QUOTE_NORMALIZATION_VERSION,
    )


def _find_all(text: str, needle: str) -> list[_Match]:
    if not needle:
        return []
    return [
        _Match(start, start + len(needle))
        for start in range(len(text) - len(needle) + 1)
        if text[start : start + len(needle)] == needle
    ]

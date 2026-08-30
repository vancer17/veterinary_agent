"""Conservative V7 quote governance shared by attribution microbenches."""

from __future__ import annotations

from dataclasses import dataclass

from .v6_quote_governance import normalize_quote_text


@dataclass(frozen=True)
class V7QuoteCheck:
    """Result of an exact conservative quote check."""

    raw_quote: str
    normalized_quote: str
    found_in_source: bool
    contained_in_evidence: bool

    @property
    def valid(self) -> bool:
        return self.found_in_source and self.contained_in_evidence


def check_source_quote(
    *,
    user_text: str,
    quote: str,
    evidence_quote: str | None = None,
) -> V7QuoteCheck:
    """Check exact/conservative source and optional evidence containment.

    This function never repairs a quote.  A quote is valid only when its
    conservatively normalized form occurs verbatim in the source and, when an
    evidence quote is supplied, also occurs inside that evidence quote.
    """

    source = normalize_quote_text(user_text)
    normalized = normalize_quote_text(quote)
    found = bool(normalized) and normalized in source
    contained = True
    if evidence_quote is not None:
        evidence = normalize_quote_text(evidence_quote)
        contained = bool(normalized) and normalized in evidence
    return V7QuoteCheck(
        raw_quote=quote,
        normalized_quote=normalized,
        found_in_source=found,
        contained_in_evidence=contained,
    )

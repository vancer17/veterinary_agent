"""Conservative phrase-to-source alignment for V13.

The aligner is the evidence boundary of the LLM-first paradigm.  It can only
select a substring already present in the source; it never rewrites or repairs
the model phrase.  Similarity is used to locate a source substring, not to
replace it with a paraphrase.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher

from .v13_contracts import (
    V13AlignedEvidence,
    V13AlignmentStatus,
    V13VerifierStatus,
)

_FULLWIDTH_MAP = {
    "（": "(",
    "）": ")",
    "，": ",",
    "。": ".",
    "！": "!",
    "？": "?",
    "：": ":",
    "；": ";",
    "“": "\"",
    "”": "\"",
    "‘": "'",
    "’": "'",
}
_NEGATION_MARKERS = ("不", "没", "无", "未")
_TEMPORAL_MARKERS = (
    "今天",
    "昨天",
    "前天",
    "今年",
    "去年",
    "这两天",
    "最近",
    "开始",
    "持续",
    "天",
    "周",
    "月",
    "年",
    "小时",
    "分钟",
)


@dataclass(frozen=True)
class V13SourceBlock:
    source_id: str
    source_block_id: str
    text: str


@dataclass(frozen=True)
class _Candidate:
    start: int
    end: int
    text: str
    score: float
    method: str


def normalize_phrase(value: str) -> str:
    """Apply only conservative punctuation and whitespace normalization."""

    value = unicodedata.normalize("NFKC", value).strip()
    value = "".join(_FULLWIDTH_MAP.get(char, char) for char in value)
    return "".join(
        char
        for char in value
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )


def _contains_marker(value: str, markers: Iterable[str]) -> frozenset[str]:
    return frozenset(marker for marker in markers if marker in value)


def _verifier_status(model_phrase: str, aligned_quote: str) -> V13VerifierStatus:
    model_negation = _contains_marker(model_phrase, _NEGATION_MARKERS)
    aligned_negation = _contains_marker(aligned_quote, _NEGATION_MARKERS)
    if model_negation != aligned_negation:
        return V13VerifierStatus.NEGATION_LOST
    model_temporal = _contains_marker(model_phrase, _TEMPORAL_MARKERS)
    aligned_temporal = _contains_marker(aligned_quote, _TEMPORAL_MARKERS)
    if model_temporal != aligned_temporal:
        return V13VerifierStatus.TEMPORAL_LOST
    return V13VerifierStatus.VERIFIED


def _occurrences(source: str, needle: str) -> list[int]:
    if not needle:
        return []
    return [index for index in range(len(source)) if source.startswith(needle, index)]


def _fuzzy_candidates(
    source: str,
    normalized_phrase: str,
    *,
    threshold: float,
) -> list[_Candidate]:
    if not normalized_phrase:
        return []
    # A bounded broad scan is acceptable for consultation turns and keeps the
    # algorithm deterministic without a third-party fuzzy dependency.
    lower = max(1, int(len(normalized_phrase) * 0.50))
    upper = min(len(source), len(normalized_phrase) + 8)
    candidates: list[_Candidate] = []
    for length in range(lower, upper + 1):
        for start in range(len(source) - length + 1):
            end = start + length
            window = source[start:end]
            normalized_window = normalize_phrase(window)
            if not normalized_window:
                continue
            sequence = SequenceMatcher(
                None,
                normalized_phrase,
                normalized_window,
                autojunk=False,
            ).ratio()
            overlap = (
                Counter(normalized_phrase) & Counter(normalized_window)
            ).total() / max(len(normalized_phrase), 1)
            score = max(0.0, min(1.0, 0.55 * sequence + 0.45 * overlap))
            if score >= threshold:
                candidates.append(
                    _Candidate(
                        start=start,
                        end=end,
                        text=window,
                        score=score,
                        method="fuzzy",
                    )
                )
    return sorted(
        candidates,
        key=lambda item: (-item.score, item.start, item.end, item.text),
    )


def _best_unique(
    candidates: list[_Candidate],
    *,
    margin: float,
) -> tuple[_Candidate, float]:
    best = candidates[0]
    competitors = [item for item in candidates[1:] if item.text != best.text]
    second_score = competitors[0].score if competitors else 0.0
    return best, best.score - second_score


def align_phrase(
    *,
    field_name: str,
    phrase: str,
    blocks: list[V13SourceBlock],
    scope: tuple[int, int] | None = None,
    source_block_id: str = "block-001",
    policy: str = "verified",
    threshold: float = 0.75,
    margin: float = 0.03,
) -> V13AlignedEvidence:
    """Align one model phrase to an exact source substring."""

    if not phrase.strip():
        return V13AlignedEvidence(
            field_name=field_name,
            model_phrase=phrase,
            alignment_status=V13AlignmentStatus.EMPTY_PHRASE,
            verifier_status=V13VerifierStatus.UNCERTAIN,
            review_required=True,
        )
    block = next(
        (item for item in blocks if item.source_block_id == source_block_id),
        None,
    )
    if block is None:
        return V13AlignedEvidence(
            field_name=field_name,
            model_phrase=phrase,
            alignment_status=V13AlignmentStatus.CROSS_SOURCE_BLOCK,
            source_block_id=source_block_id,
            verifier_status=V13VerifierStatus.BOUNDARY_CROSSING,
            review_required=True,
        )
    source = block.text
    scope_start, scope_end = scope if scope is not None else (0, len(source))
    scoped = source[scope_start:scope_end]

    exact_positions = [scope_start + index for index in _occurrences(scoped, phrase)]
    if len(exact_positions) == 1:
        start = exact_positions[0]
        return _accepted(
            field_name=field_name,
            phrase=phrase,
            block=block,
            start=start,
            end=start + len(phrase),
            status=V13AlignmentStatus.EXACT,
            score=1.0,
            method="exact",
            margin=1.0,
        )

    normalized = normalize_phrase(phrase)
    normalized_positions: list[tuple[int, int]] = []
    if normalized:
        for start in range(scope_start, scope_end):
            for end in range(
                start + 1,
                min(len(source), start + len(normalized) + 8) + 1,
            ):
                if normalize_phrase(source[start:end]) == normalized:
                    normalized_positions.append((start, end))
                    break
    normalized_positions = list(dict.fromkeys(normalized_positions))
    if len(normalized_positions) == 1:
        start, end = normalized_positions[0]
        return _accepted(
            field_name=field_name,
            phrase=phrase,
            block=block,
            start=start,
            end=end,
            status=V13AlignmentStatus.EXACT_NORMALIZED,
            score=1.0,
            method="normalized_exact",
            margin=1.0,
        )

    ambiguous_exact = len(exact_positions) > 1 or len(normalized_positions) > 1
    if ambiguous_exact:
        return _not_found(
            field_name,
            phrase,
            V13AlignmentStatus.FUZZY_AMBIGUOUS,
        )
    if policy in {"exact", "normalized"}:
        return _not_found(
            field_name,
            phrase,
            V13AlignmentStatus.FUZZY_AMBIGUOUS
            if ambiguous_exact
            else V13AlignmentStatus.FUZZY_NOT_FOUND,
        )

    candidates = [
        _Candidate(
            start=scope_start + item.start,
            end=scope_start + item.end,
            text=item.text,
            score=item.score,
            method=item.method,
        )
        for item in _fuzzy_candidates(
            scoped,
            normalized,
            threshold=max(0.25, threshold - 0.55),
        )
    ]
    if not candidates:
        return _not_found(field_name, phrase, V13AlignmentStatus.FUZZY_NOT_FOUND)
    best, score_margin = _best_unique(candidates, margin=margin)
    verifier = _verifier_status(phrase, best.text)
    if verifier != V13VerifierStatus.VERIFIED:
        return V13AlignedEvidence(
            field_name=field_name,
            model_phrase=phrase,
            aligned_quote=best.text,
            start=best.start,
            end=best.end,
            source_block_id=block.source_block_id,
            alignment_status=V13AlignmentStatus.FUZZY_NOT_FOUND,
            similarity=best.score,
            best_candidate=best.text,
            second_best_candidate=candidates[1].text if len(candidates) > 1 else "",
            score_margin=score_margin,
            alignment_method="fuzzy_rejected",
            verifier_status=verifier,
            review_required=True,
        )
    if best.score < threshold:
        return _not_found(field_name, phrase, V13AlignmentStatus.FUZZY_NOT_FOUND)
    status = (
        V13AlignmentStatus.FUZZY_AMBIGUOUS
        if score_margin < margin
        else V13AlignmentStatus.FUZZY_VERIFIED
    )
    return _accepted(
        field_name=field_name,
        phrase=phrase,
        block=block,
        start=best.start,
        end=best.end,
        status=status,
        score=best.score,
        method="unique_fuzzy",
        margin=score_margin,
        best_candidate=best.text,
        second_candidate=candidates[1].text if len(candidates) > 1 else "",
    )


def _accepted(
    *,
    field_name: str,
    phrase: str,
    block: V13SourceBlock,
    start: int,
    end: int,
    status: V13AlignmentStatus,
    score: float,
    method: str,
    margin: float,
    best_candidate: str = "",
    second_candidate: str = "",
) -> V13AlignedEvidence:
    quote = block.text[start:end]
    verifier = _verifier_status(phrase, quote)
    review = (
        status == V13AlignmentStatus.FUZZY_AMBIGUOUS
        or verifier != V13VerifierStatus.VERIFIED
    )
    return V13AlignedEvidence(
        field_name=field_name,
        model_phrase=phrase,
        aligned_quote=quote,
        start=start,
        end=end,
        source_block_id=block.source_block_id,
        alignment_status=V13AlignmentStatus.FUZZY_NOT_FOUND
        if verifier != V13VerifierStatus.VERIFIED
        else status,
        similarity=score,
        best_candidate=best_candidate or quote,
        second_best_candidate=second_candidate,
        score_margin=margin,
        alignment_method=method,
        verifier_status=verifier,
        review_required=review,
    )


def _not_found(
    field_name: str,
    phrase: str,
    status: V13AlignmentStatus,
) -> V13AlignedEvidence:
    return V13AlignedEvidence(
        field_name=field_name,
        model_phrase=phrase,
        alignment_status=status,
        verifier_status=V13VerifierStatus.UNCERTAIN,
        review_required=True,
    )

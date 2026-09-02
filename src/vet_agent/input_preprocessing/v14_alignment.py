"""Claim-local alignment for V14 approximate phrase proposals."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from .v13_aligner import V13SourceBlock, align_phrase
from .v13_contracts import V13AlignmentStatus, V13VerifierStatus
from .v14_contracts import (
    V14AlignedField,
    V14AlignmentStatus,
    V14ClaimRecordRaw,
    V14GovernedClaim,
)

_SENTENCE_BOUNDARY = re.compile(r"[。！？!?\n；;]+")
_PARTICIPANT_FIELDS = {
    "subject",
    "experiencer",
    "action_agent",
    "action_recipient",
}
_OPTIONAL_FIELDS = (
    "subject",
    "experiencer",
    "action_agent",
    "action_recipient",
    "object",
    "temporal",
    "measurement",
    "relation",
)
_ACCEPTED = {
    V14AlignmentStatus.EXACT,
    V14AlignmentStatus.EXACT_NORMALIZED,
    V14AlignmentStatus.FUZZY_VERIFIED,
}


@dataclass(frozen=True)
class _EvidenceCandidate:
    evidence: V14AlignedField
    score: float


def _convert(
    item: Any,
    *,
    field_name: str,
    alignment_scope: str = "claim_local",
    resolution_method: str = "",
) -> V14AlignedField:
    legacy_status = V13AlignmentStatus(item.alignment_status.value)
    verifier = V13VerifierStatus(item.verifier_status.value)
    status_map = {
        V13AlignmentStatus.EXACT: V14AlignmentStatus.EXACT,
        V13AlignmentStatus.EXACT_NORMALIZED: V14AlignmentStatus.EXACT_NORMALIZED,
        V13AlignmentStatus.FUZZY_VERIFIED: V14AlignmentStatus.FUZZY_VERIFIED,
        V13AlignmentStatus.FUZZY_AMBIGUOUS: V14AlignmentStatus.FUZZY_AMBIGUOUS,
        V13AlignmentStatus.CROSS_SOURCE_BLOCK: V14AlignmentStatus.CROSS_SOURCE_BLOCK,
        V13AlignmentStatus.EMPTY_PHRASE: V14AlignmentStatus.EMPTY_PHRASE,
    }
    if legacy_status in status_map:
        status = status_map[legacy_status]
    elif verifier == V13VerifierStatus.NEGATION_LOST:
        status = V14AlignmentStatus.NEGATION_LOST
    elif verifier == V13VerifierStatus.TEMPORAL_LOST:
        status = V14AlignmentStatus.TEMPORAL_LOST
    elif verifier == V13VerifierStatus.SUBJECT_LOST:
        status = V14AlignmentStatus.SUBJECT_LOST
    elif verifier == V13VerifierStatus.SEMANTIC_MISMATCH:
        status = V14AlignmentStatus.SEMANTIC_MISMATCH
    else:
        status = V14AlignmentStatus.NOT_FOUND
    return V14AlignedField(
        field_name=field_name,
        model_phrase=item.model_phrase,
        aligned_quote=item.aligned_quote,
        start=item.start,
        end=item.end,
        source_block_id=item.source_block_id,
        alignment_status=status,
        similarity=item.similarity,
        best_candidate=item.best_candidate,
        second_best_candidate=item.second_best_candidate,
        score_margin=item.score_margin,
        alignment_method=item.alignment_method,
        verifier_status=verifier,
        review_required=item.review_required or status not in _ACCEPTED,
        alignment_scope=alignment_scope,
        resolution_method=resolution_method,
    )


def _accepted(item: V14AlignedField) -> bool:
    return item.alignment_status in _ACCEPTED and not item.review_required


def _contained(inner: V14AlignedField, outer: V14AlignedField) -> bool:
    return (
        inner.source_block_id == outer.source_block_id
        and inner.start >= outer.start
        and inner.end <= outer.end
    )


def _blocks(text: str) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    cursor = 0
    for match in _SENTENCE_BOUNDARY.finditer(text):
        stop = match.end()
        if stop > cursor + 1:
            result.append((cursor, stop))
        cursor = stop
    if len(text) > cursor:
        result.append((cursor, len(text)))
    return result


def _child_score(
    raw: V14ClaimRecordRaw,
    *,
    blocks: list[V13SourceBlock],
    evidence: V14AlignedField,
) -> float:
    if not _accepted(evidence):
        return -1.0
    scope = (evidence.start, evidence.end)
    score = 0.0
    phrases = (
        raw.target_phrase,
        raw.temporal_phrase,
        raw.measurement_phrase,
        raw.relation_phrase,
        raw.object_phrase,
        raw.action_agent_phrase,
        raw.action_recipient_phrase,
        raw.subject_phrase,
        raw.experiencer_phrase,
    )
    for phrase in phrases:
        if not phrase:
            continue
        item = _convert(
            align_phrase(
                field_name="occurrence_probe",
                phrase=phrase,
                blocks=blocks,
                scope=scope,
                source_block_id=evidence.source_block_id,
            ),
            field_name="occurrence_probe",
        )
        if _accepted(item):
            score += 1.0 + item.similarity
    return score


def _align_evidence(
    raw: V14ClaimRecordRaw,
    *,
    blocks: list[V13SourceBlock],
) -> V14AlignedField:
    direct = _convert(
        align_phrase(
            field_name="evidence",
            phrase=raw.evidence_phrase,
            blocks=blocks,
        ),
        field_name="evidence",
        alignment_scope="source",
    )
    if _accepted(direct):
        return direct
    block = blocks[0]
    candidates: list[_EvidenceCandidate] = []
    scopes: list[tuple[int, int]] = _blocks(block.text)
    if raw.evidence_phrase in block.text:
        start = 0
        while True:
            start = block.text.find(raw.evidence_phrase, start)
            if start < 0:
                break
            scopes.append((start, start + len(raw.evidence_phrase)))
            start += 1
    for scope in scopes:
        item = _convert(
            align_phrase(
                field_name="evidence",
                phrase=raw.evidence_phrase,
                blocks=blocks,
                scope=scope,
            ),
            field_name="evidence",
            alignment_scope="claim_local_occurrence",
        )
        candidates.append(
            _EvidenceCandidate(
                evidence=item,
                score=_child_score(raw, blocks=blocks, evidence=item),
            )
        )
    accepted = [item for item in candidates if _accepted(item.evidence)]
    if not accepted:
        return direct
    accepted.sort(
        key=lambda item: (
            -item.score,
            -item.evidence.similarity,
            -item.evidence.score_margin,
            item.evidence.start,
        )
    )
    if len(accepted) > 1:
        top_score = accepted[0].score
        tied = [item for item in accepted if abs(item.score - top_score) < 0.001]
        if len(tied) > 1:
            evidence = accepted[0].evidence.model_copy(
                update={
                    "alignment_status": V14AlignmentStatus.WRONG_OCCURRENCE,
                    "review_required": True,
                    "resolution_method": "ambiguous_occurrence",
                }
            )
            return evidence
    selected = accepted[0].evidence
    return selected.model_copy(
        update={"resolution_method": "child_containment_disambiguation"}
    )


def align_v14_claim(
    raw: V14ClaimRecordRaw,
    *,
    source_id: str,
    blocks: list[V13SourceBlock],
) -> V14GovernedClaim:
    """Align evidence first, then bind every field inside that envelope."""

    evidence = _align_evidence(raw, blocks=blocks)
    scope = (
        (evidence.start, evidence.end)
        if _accepted(evidence)
        else None
    )
    target = _convert(
        align_phrase(
            field_name="target",
            phrase=raw.target_phrase,
            blocks=blocks,
            scope=scope,
        ),
        field_name="target",
    )
    fields: dict[str, V14AlignedField] = {}
    for field_name in _OPTIONAL_FIELDS:
        phrase = str(getattr(raw, f"{field_name}_phrase") or "")
        if not phrase:
            continue
        local = _convert(
            align_phrase(
                field_name=field_name,
                phrase=phrase,
                blocks=blocks,
                scope=scope,
            ),
            field_name=field_name,
        )
        if _accepted(local):
            aligned = local
        else:
            global_item = _convert(
                align_phrase(
                    field_name=field_name,
                    phrase=phrase,
                    blocks=blocks,
                ),
                field_name=field_name,
                alignment_scope="outside_parent",
                resolution_method=(
                    "TurnContext"
                    if field_name in _PARTICIPANT_FIELDS
                    else "outside_parent"
                ),
            )
            if field_name in _PARTICIPANT_FIELDS and _accepted(global_item):
                aligned = global_item
            else:
                aligned = local
        fields[field_name] = aligned

    blocked: list[str] = []
    if not _accepted(evidence):
        blocked.append(f"evidence_{evidence.alignment_status.value}")
    if not _accepted(target):
        blocked.append(f"target_{target.alignment_status.value}")
    elif _accepted(evidence) and not _contained(target, evidence):
        blocked.append("target_outside_evidence")
    for name, item in fields.items():
        participant_exception = name in _PARTICIPANT_FIELDS and _accepted(item)
        if not _accepted(item) and not participant_exception:
            blocked.append(f"{name}_{item.alignment_status.value}")
        elif (
            _accepted(item)
            and name not in _PARTICIPANT_FIELDS
            and _accepted(evidence)
            and not _contained(item, evidence)
        ):
            blocked.append(f"{name}_outside_evidence")

    digest = hashlib.sha256(
        "\u241f".join(
            (
                source_id,
                evidence.source_block_id,
                str(evidence.start),
                str(evidence.end),
                str(target.start),
                str(target.end),
                raw.claim_type.value,
                raw.user_statement_type.value,
            )
        ).encode("utf-8")
    ).hexdigest()
    return V14GovernedClaim(
        source_id=source_id,
        deterministic_claim_id=f"{source_id}:v14-{digest[:24]}",
        raw_claim=raw,
        evidence=evidence,
        target=target,
        fields=fields,
        projection_ready=not blocked,
        review_required=bool(blocked or raw.needs_review),
        blocked_reasons=sorted(set(blocked)),
    )


def align_v14_output(
    output: Any,
    *,
    source_id: str,
    text: str,
) -> list[V14GovernedClaim]:
    blocks = [V13SourceBlock(source_id, "block-001", text)]
    return [
        align_v14_claim(raw, source_id=source_id, blocks=blocks)
        for raw in output.claims
    ]

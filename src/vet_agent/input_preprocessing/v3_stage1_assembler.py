"""Deterministic assembler for split V3 Stage 1 outputs."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from .errors import InputPreprocessingContractError
from .v3_contracts import (
    V3AssertionState,
    V3AtomicClaimSegment,
    V3DiscourseRole,
    V3EntityBinding,
    V3EntityType,
    V3InputContentProfile,
    V3ParticipantBinding,
    V3ParticipantBindingRawOutput,
    V3PreprocessingIntent,
    V3RawParticipantBinding,
    V3ScopeItem,
    V3ScopeSegmentationRawOutput,
    V3Segment,
    V3SharedAssertionScopeSegment,
    V3Stage1Output,
    V3TurnContext,
    V3TurnIntentRaw,
)


@dataclass(frozen=True)
class V3ItemContext:
    """A flat view of one Stage 1 expected evidence item."""

    segment_id: str
    item_id: str
    item_key: str
    source_text: str
    analysis_text: str
    subject: V3EntityBinding
    participants: tuple[V3ParticipantBinding, ...]
    initial_assertion: str


def assemble_v3_stage1(
    *,
    turn_context: V3TurnContext,
    intent_raw: V3TurnIntentRaw,
    segmentation_raw: V3ScopeSegmentationRawOutput,
    participant_raw: V3ParticipantBindingRawOutput,
) -> V3Stage1Output:
    """Assemble raw model output without asking the model to repeat counts."""

    bindings = {binding.item_key: binding for binding in participant_raw.bindings}
    expected_keys = {
        f"s-{segment_index}:item-{item_index}"
        for segment_index, segment in enumerate(segmentation_raw.segments, start=1)
        if segment.requires_evidence_analysis
        for item_index in range(
            1,
            len(segment.items) + 1
            if segment.kind == "shared_assertion_scope"
            else 2,
        )
    }
    unexpected = set(bindings) - expected_keys
    if unexpected:
        raise InputPreprocessingContractError(
            "v3_participant_binding_unexpected_item:" + ",".join(sorted(unexpected))
        )

    segments: list[V3Segment] = []
    for segment_index, raw_segment in enumerate(segmentation_raw.segments, start=1):
        segment_id = f"s-{segment_index}"
        segment_binding = bindings.get(f"{segment_id}:item-1")
        segment_subject = (
            _binding(segment_binding.subject, turn_context)
            if segment_binding is not None
            else _missing_binding()
        )
        segment_participants = (
            [
                V3ParticipantBinding(
                    role=participant.role,
                    entity=_binding(participant, turn_context),
                )
                for participant in segment_binding.participants
            ]
            if segment_binding is not None
            else []
        )
        segment_confidence = segment_binding.confidence if segment_binding else 0.0

        if raw_segment.kind == "atomic_claim":
            segments.append(
                V3AtomicClaimSegment(
                    segment_id=segment_id,
                    source_text=raw_segment.source_text,
                    analysis_text=raw_segment.analysis_text,
                    discourse_role=raw_segment.discourse_role,
                    requires_evidence_analysis=raw_segment.requires_evidence_analysis,
                    subject=segment_subject,
                    participants=segment_participants,
                    confidence=segment_confidence,
                    item_id="item-1",
                    initial_assertion=raw_segment.initial_assertion
                    or V3AssertionState.UNKNOWN,
                )
            )
            continue

        items: list[V3ScopeItem] = []
        for item_index, raw_item in enumerate(raw_segment.items, start=1):
            item_id = f"item-{item_index}"
            item_binding = bindings.get(f"{segment_id}:{item_id}")
            items.append(
                V3ScopeItem(
                    item_id=item_id,
                    source_text=raw_item.source_text,
                    analysis_text=raw_item.analysis_text,
                    subject=_binding(
                        item_binding.subject if item_binding else None,
                        turn_context,
                    ),
                    participants=(
                        [
                            V3ParticipantBinding(
                                role=participant.role,
                                entity=_binding(participant, turn_context),
                            )
                            for participant in item_binding.participants
                        ]
                        if item_binding
                        else []
                    ),
                    confidence=item_binding.confidence if item_binding else 0.0,
                )
            )
        segments.append(
            V3SharedAssertionScopeSegment(
                segment_id=segment_id,
                source_text=raw_segment.source_text,
                analysis_text=raw_segment.analysis_text,
                discourse_role=raw_segment.discourse_role,
                requires_evidence_analysis=raw_segment.requires_evidence_analysis,
                subject=segment_subject,
                participants=segment_participants,
                confidence=segment_confidence,
                scope_assertion=raw_segment.scope_assertion
                or V3AssertionState.UNKNOWN,
                items=items,
                expected_evidence_count=len(items),
            )
        )

    expected_count = sum(
        segment.expected_evidence_count
        for segment in segments
        if segment.requires_evidence_analysis
    )
    roles = [segment.discourse_role for segment in segments]
    return V3Stage1Output(
        intent=V3PreprocessingIntent.model_validate(intent_raw.model_dump()),
        profile=V3InputContentProfile(
            expected_fact_candidate_count=expected_count,
            has_fact_statement=V3DiscourseRole.FACT_STATEMENT in roles,
            has_user_question=V3DiscourseRole.USER_QUESTION in roles,
            has_control_intent=intent_raw.answer_now or intent_raw.correction,
            has_uncertainty=V3DiscourseRole.UNCERTAIN_STATEMENT in roles,
            has_historical_statement=V3DiscourseRole.HISTORICAL_STATEMENT in roles,
            has_hypothetical_statement=(
                V3DiscourseRole.HYPOTHETICAL_STATEMENT in roles
            ),
        ),
        segments=segments,
    )


def iter_v3_items(stage1: V3Stage1Output) -> Iterator[V3ItemContext]:
    """Yield a stable flat item view for candidate recall and item verification."""

    for segment in stage1.segments:
        if not segment.requires_evidence_analysis:
            continue
        if isinstance(segment, V3AtomicClaimSegment):
            yield V3ItemContext(
                segment_id=segment.segment_id,
                item_id=segment.item_id,
                item_key=f"{segment.segment_id}:{segment.item_id}",
                source_text=segment.source_text,
                analysis_text=segment.analysis_text,
                subject=segment.subject,
                participants=tuple(segment.participants),
                initial_assertion=segment.initial_assertion.value,
            )
            continue
        for item in segment.items:
            yield V3ItemContext(
                segment_id=segment.segment_id,
                item_id=item.item_id,
                item_key=f"{segment.segment_id}:{item.item_id}",
                source_text=item.source_text,
                analysis_text=item.analysis_text,
                subject=item.subject,
                participants=tuple(item.participants),
                initial_assertion=segment.scope_assertion.value,
            )


def _binding(
    raw: V3RawParticipantBinding | None,
    turn_context: V3TurnContext,
) -> V3EntityBinding:
    if raw is None:
        return _missing_binding()
    reference = turn_context.entity_references().get(raw.reference_id or "")
    return V3EntityBinding(
        reference_id=raw.reference_id,
        entity_type=(
            reference.entity_type
            if reference is not None
            else V3EntityType.UNKNOWN
        ),
        resolution_method=raw.resolution_method,
        resolution_status=raw.resolution_status,
        subject_candidates=raw.subject_candidates,
        confidence=raw.confidence,
    )


def _missing_binding() -> V3EntityBinding:
    return V3EntityBinding()

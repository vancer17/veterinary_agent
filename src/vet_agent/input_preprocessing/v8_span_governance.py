"""Deterministic governance for V8 span references.

The macro model never supplies quotes.  This module is the only place that
converts span IDs back into source-backed quotes and checks containment,
source boundaries, entity resolution, and claim readiness.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from .v8_contracts import (
    V8EntityCandidate,
    V8GovernedUserClaim,
    V8MacroClaimRaw,
    V8MacroDiscourseActRaw,
    V8MacroSemanticRawOutput,
    V8QualityGateResult,
    V8ResolvedEntityBinding,
    V8ResolvedSpanBinding,
    V8SpanCandidate,
)


@dataclass
class V8SpanPool:
    """Immutable candidate spans for one or more source texts."""

    sources: dict[str, str]
    spans: list[V8SpanCandidate]
    _by_id: dict[str, V8SpanCandidate] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._by_id = {span.span_id: span for span in self.spans}
        if len(self._by_id) != len(self.spans):
            raise ValueError("v8_duplicate_span_id")

    @property
    def span_ids(self) -> set[str]:
        return set(self._by_id)

    def span(self, span_id: str) -> V8SpanCandidate | None:
        return self._by_id.get(span_id)

    def find_exact(self, *, text: str, source_id: str) -> V8SpanCandidate | None:
        matches = [
            span
            for span in self.spans
            if span.source_id == source_id
            and span.start >= 0
            and span.end <= len(self.sources.get(span.source_id, ""))
            and self.sources[span.source_id][span.start : span.end] == text
        ]
        return matches[0] if matches else None


@dataclass
class V8GovernedMacroResult:
    acts: list[V8MacroDiscourseActRaw]
    governed_claims: list[V8GovernedUserClaim]
    gates: list[V8QualityGateResult]
    invalid_span_references: list[str] = field(default_factory=list)
    invalid_span_bindings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _ClaimGovernanceResult:
    claim: V8GovernedUserClaim | None
    invalid_span_references: tuple[str, ...] = ()
    invalid_span_bindings: tuple[str, ...] = ()
    target_containment_references: tuple[str, ...] = ()


class V8SpanGovernance:
    """Resolve and validate macro output against a fixed span pool."""

    def __init__(self, pool: V8SpanPool) -> None:
        self.pool = pool

    def resolve_span_ids(
        self,
        *,
        span_ids: Iterable[str],
        required: bool = False,
    ) -> V8ResolvedSpanBinding | None:
        ids = list(dict.fromkeys(span_ids))
        if not ids:
            return None
        spans: list[V8SpanCandidate] = []
        for span_id in ids:
            span = self.pool.span(span_id)
            if span is None:
                continue
            spans.append(span)
        if len(spans) != len(ids):
            return self._invalid(ids)
        source_ids = {item.source_id for item in spans}
        block_ids = {item.source_block_id for item in spans}
        if len(source_ids) != 1 or len(block_ids) != 1:
            return self._invalid(ids)
        ordered = sorted(spans, key=lambda item: (item.start, item.end))
        start = ordered[0].start
        end = ordered[-1].end
        source_id = ordered[0].source_id
        source = self.pool.sources.get(source_id)
        if source is None or end > len(source):
            return self._invalid(ids)
        # Support bindings may be composed only from contiguous or overlapping
        # spans.  This prevents the code from creating a quote across an
        # unselected semantic gap.
        cursor = ordered[0].start
        for span in ordered:
            if span.start > cursor:
                return self._invalid(ids)
            cursor = max(cursor, span.end)
        quote = source[start:end]
        if not quote:
            return self._invalid(ids)
        return V8ResolvedSpanBinding(
            span_ids=ids,
            source_id=source_id,
            source_block_id=ordered[0].source_block_id,
            start=start,
            end=end,
            quote=quote,
            status="resolved",
        )

    def resolve_entity(
        self,
        *,
        binding: V8ResolvedSpanBinding | None,
        candidates: list[V8EntityCandidate],
    ) -> V8ResolvedEntityBinding:
        if binding is None or binding.status != "resolved":
            return V8ResolvedEntityBinding(
                resolution_status="missing",
            )
        matches = [
            item
            for item in candidates
            if binding.quote in {item.display_name, *item.mention_aliases}
        ]
        if not matches:
            return V8ResolvedEntityBinding(
                span_ids=binding.span_ids,
                mention_quote=binding.quote,
                resolution_status="unresolved",
            )
        if len(matches) > 1:
            return V8ResolvedEntityBinding(
                span_ids=binding.span_ids,
                mention_quote=binding.quote,
                resolution_status="ambiguous",
                subject_candidates=[item.reference_id for item in matches],
            )
        selected = matches[0]
        return V8ResolvedEntityBinding(
            span_ids=binding.span_ids,
            mention_quote=binding.quote,
            selected_reference_id=selected.reference_id,
            entity_type=selected.entity_type,
            resolution_status="resolved",
        )

    def govern(
        self,
        output: V8MacroSemanticRawOutput,
        *,
        entity_candidates: list[V8EntityCandidate] | None = None,
    ) -> V8GovernedMacroResult:
        candidates = entity_candidates or []
        gates: list[V8QualityGateResult] = []
        invalid_act_references: list[str] = []
        invalid_act_bindings: list[str] = []
        invalid_claim_references: list[str] = []
        invalid_claim_bindings: list[str] = []
        target_containment_references: list[str] = []
        valid_acts: list[V8MacroDiscourseActRaw] = []
        valid_claims: list[V8GovernedUserClaim] = []

        for act in output.acts:
            binding = self.resolve_span_ids(span_ids=act.evidence_span_ids)
            if binding is None or binding.status != "resolved":
                missing, binding_failure = self._reference_failure_ids(
                    act.evidence_span_ids,
                    binding,
                )
                invalid_act_references.extend(missing)
                invalid_act_bindings.extend(binding_failure)
                continue
            valid_acts.append(act)

        for claim in output.claims:
            result = self._govern_claim(
                claim,
                entity_candidates=candidates,
            )
            invalid_claim_references.extend(result.invalid_span_references)
            invalid_claim_bindings.extend(result.invalid_span_bindings)
            target_containment_references.extend(result.target_containment_references)
            if result.claim is not None:
                valid_claims.append(result.claim)

        invalid_act_references = list(dict.fromkeys(invalid_act_references))
        invalid_claim_references = list(dict.fromkeys(invalid_claim_references))
        invalid_act_bindings = list(dict.fromkeys(invalid_act_bindings))
        invalid_claim_bindings = list(dict.fromkeys(invalid_claim_bindings))
        target_containment_references = list(
            dict.fromkeys(target_containment_references)
        )
        if invalid_act_references:
            gates.append(
                V8QualityGateResult(
                    gate_id="v8_act_span_reference",
                    status="failed",
                    severity="blocking",
                    reason_code="invalid_span_reference",
                    evidence_refs=invalid_act_references,
                    metadata={"scope": "discourse_act"},
                )
            )
        if invalid_claim_references:
            gates.append(
                V8QualityGateResult(
                    gate_id="v8_claim_span_reference",
                    status="failed",
                    severity="blocking",
                    reason_code="invalid_span_reference",
                    evidence_refs=invalid_claim_references,
                    metadata={"scope": "claim"},
                )
            )
        if target_containment_references:
            gates.append(
                V8QualityGateResult(
                    gate_id="v8_target_containment",
                    status="failed",
                    severity="blocking",
                    reason_code="target_binding_error",
                    evidence_refs=target_containment_references,
                    metadata={"scope": "claim"},
                )
            )

        invalid_bindings = [*invalid_act_bindings, *invalid_claim_bindings]
        invalid_bindings = list(dict.fromkeys(invalid_bindings))
        if invalid_bindings:
            gates.append(
                V8QualityGateResult(
                    gate_id="v8_span_binding",
                    status="failed",
                    severity="blocking",
                    reason_code="invalid_span_binding",
                    evidence_refs=invalid_bindings,
                    metadata={"scope": "act_and_claim"},
                )
            )

        invalid_references = [
            *invalid_act_references,
            *invalid_claim_references,
        ]
        invalid_reference_count = len(invalid_references)
        gates.append(
            V8QualityGateResult(
                gate_id="v8_free_quote_forbidden",
                status="passed",
                severity="blocking",
                reason_code="schema_contains_no_free_quote_fields",
                metadata={
                    "invalid_span_reference_count": invalid_reference_count,
                    "invalid_span_binding_count": len(invalid_bindings),
                },
            )
        )
        return V8GovernedMacroResult(
            acts=valid_acts,
            governed_claims=valid_claims,
            gates=gates,
            invalid_span_references=list(dict.fromkeys(invalid_references)),
            invalid_span_bindings=invalid_bindings,
        )

    def _govern_claim(
        self,
        claim: V8MacroClaimRaw,
        *,
        entity_candidates: list[V8EntityCandidate],
    ) -> _ClaimGovernanceResult:
        support = self.resolve_span_ids(span_ids=claim.support_span_ids)
        if support is None or support.status != "resolved":
            missing, binding_failure = self._reference_failure_ids(
                claim.support_span_ids,
                support,
            )
            return _ClaimGovernanceResult(
                claim=None,
                invalid_span_references=missing,
                invalid_span_bindings=binding_failure,
            )

        target = self.resolve_span_ids(span_ids=claim.target_span_ids)
        invalid_references: list[str] = []
        invalid_bindings: list[str] = []
        if target is None or target.status != "resolved":
            missing, binding_failure = self._reference_failure_ids(
                claim.target_span_ids,
                target,
            )
            return _ClaimGovernanceResult(
                claim=None,
                invalid_span_references=missing,
                invalid_span_bindings=binding_failure,
            )

        target_valid = self._contained(target, support)
        if not target_valid:
            # Keep the invalid binding visible to gates rather than silently
            # dropping the claim.
            target = target.model_copy(update={"status": "invalid"})

        def optional(ids: list[str]) -> V8ResolvedSpanBinding | None:
            binding = self.resolve_span_ids(span_ids=ids) if ids else None
            if binding is not None and binding.status != "resolved":
                missing, binding_failure = self._reference_failure_ids(
                    ids,
                    binding,
                )
                invalid_references.extend(missing)
                invalid_bindings.extend(binding_failure)
            return binding

        relation = optional(claim.relation_span_ids)
        subject_binding = optional(claim.subject_span_ids)
        subject = (
            self.resolve_entity(
                binding=subject_binding,
                candidates=entity_candidates,
            )
            if subject_binding is not None
            else None
        )
        agent_binding = optional(claim.action_agent_span_ids)
        recipient_binding = optional(claim.action_recipient_span_ids)
        experiencer_binding = optional(claim.experiencer_span_ids)
        object_binding = optional(claim.object_span_ids)
        temporal = optional(claim.temporal_span_ids)
        measurement = optional(claim.measurement_span_ids)

        agent = (
            self.resolve_entity(binding=agent_binding, candidates=entity_candidates)
            if agent_binding is not None
            else None
        )
        recipient = (
            self.resolve_entity(binding=recipient_binding, candidates=entity_candidates)
            if recipient_binding is not None
            else None
        )
        experiencer = (
            self.resolve_entity(
                binding=experiencer_binding,
                candidates=entity_candidates,
            )
            if experiencer_binding is not None
            else None
        )
        auxiliary_valid = all(
            item is None
            or (
                item.status == "resolved"
                and item.source_id == support.source_id
                and item.start >= support.start
                and item.end <= support.end
            )
            for item in (relation, temporal, measurement, object_binding)
        )
        resolved_entities_valid = all(
            item is None
            or (
                item.resolution_status != "resolved" or bool(item.selected_reference_id)
            )
            for item in (subject, agent, recipient, experiencer)
        )
        projection_ready = (
            support.status == "resolved"
            and target.status == "resolved"
            and auxiliary_valid
            and resolved_entities_valid
        )
        claim_result = V8GovernedUserClaim(
            unit_id=claim.unit_id,
            claim_id=claim.claim_id,
            statement_type=claim.statement_type,
            coarse_type=claim.coarse_type,
            support=support,
            target=target,
            relation=relation,
            subject=subject,
            action_agent=agent,
            action_recipient=recipient,
            experiencer=experiencer,
            object_mention=object_binding,
            temporal=temporal,
            measurement=measurement,
            projection_ready=projection_ready,
            review_required=not projection_ready,
        )
        return _ClaimGovernanceResult(
            claim=claim_result,
            invalid_span_references=tuple(invalid_references),
            invalid_span_bindings=tuple(invalid_bindings),
            target_containment_references=(
                tuple(dict.fromkeys(claim.target_span_ids)) if not target_valid else ()
            ),
        )

    def _reference_failure_ids(
        self,
        span_ids: Iterable[str],
        binding: V8ResolvedSpanBinding | None,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        ids = list(dict.fromkeys(span_ids))
        if binding is None or binding.status == "resolved":
            return (), ()
        missing = [span_id for span_id in ids if self.pool.span(span_id) is None]
        if missing:
            return tuple(missing), ()
        return (), tuple(ids)

    @staticmethod
    def _contained(inner: V8ResolvedSpanBinding, outer: V8ResolvedSpanBinding) -> bool:
        return (
            inner.source_id == outer.source_id
            and inner.source_block_id == outer.source_block_id
            and inner.start >= outer.start
            and inner.end <= outer.end
        )

    @staticmethod
    def _invalid(ids: list[str]) -> V8ResolvedSpanBinding:
        return V8ResolvedSpanBinding(
            span_ids=ids,
            source_id="unknown",
            source_block_id="unknown",
            start=0,
            end=1,
            quote="invalid-span-binding",
            status="invalid",
        )

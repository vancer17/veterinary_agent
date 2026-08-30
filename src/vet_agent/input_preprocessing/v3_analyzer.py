"""Third-round V3 split-stage, item-keyed shadow analyzer."""

from __future__ import annotations

import json
import time
from collections import Counter
from typing import Any, Protocol

from pydantic import ValidationError

from .errors import InputPreprocessingContractError, InputPreprocessingDependencyError
from .v3_candidate_linker import V3CandidateRetriever
from .v3_contracts import (
    V3AssertionState,
    V3CandidateSet,
    V3CanonicalMappingStatus,
    V3InputAnalysisResult,
    V3ItemVerificationRaw,
    V3ParticipantBindingRawOutput,
    V3ScopeSegmentationRawOutput,
    V3Stage1Output,
    V3Stage2Output,
    V3TurnContext,
    V3TurnIntentRaw,
    V3VerifiedEvidence,
)
from .v3_gates import evaluate_v3_quality_gates
from .v3_stage1_assembler import (
    V3ItemContext,
    assemble_v3_stage1,
    iter_v3_items,
)
from .vocabulary import CanonicalVocabulary


class V3StructuredClient(Protocol):
    """Minimal structured model interface required by V3."""

    @property
    def available(self) -> bool: ...

    async def chat_structured(
        self,
        messages: list[dict[str, Any]],
        *,
        response_model: type,
        model: str,
        temperature: float = 0.0,
    ) -> Any: ...


class InputPreprocessingV3Analyzer:
    """Run split Stage 1 tasks and one verifier call per expected item."""

    def __init__(
        self,
        *,
        qwen: V3StructuredClient,
        vocabulary: CanonicalVocabulary,
        candidate_retriever: V3CandidateRetriever,
        model: str,
    ) -> None:
        self.qwen = qwen
        self.vocabulary = vocabulary
        self.candidate_retriever = candidate_retriever
        self.model = model

    async def analyze(
        self,
        *,
        user_text: str,
        turn_context: V3TurnContext,
        stage1_override: V3Stage1Output | None = None,
    ) -> V3InputAnalysisResult:
        """Return a report-only V3 result or raise an explicit contract error."""

        if not self.qwen.available:
            raise InputPreprocessingDependencyError("v3_qwen_client_unavailable")

        latency: dict[str, int] = {}
        attempts: dict[str, int] = Counter()

        started = time.perf_counter()
        if stage1_override is None:
            intent = await self._structured(
                payload=self._intent_payload(user_text, turn_context),
                response_model=V3TurnIntentRaw,
                stage="stage1_intent",
            )
            attempts["stage1_intent"] += 1
            segmentation = await self._structured(
                payload=self._segmentation_payload(
                    user_text,
                    turn_context,
                    intent_raw=intent,
                ),
                response_model=V3ScopeSegmentationRawOutput,
                stage="stage1_scope",
            )
            attempts["stage1_scope"] += 1
            participants = await self._structured(
                payload=self._participant_payload(segmentation, turn_context),
                response_model=V3ParticipantBindingRawOutput,
                stage="stage1_participant",
            )
            attempts["stage1_participant"] += 1
            stage1 = assemble_v3_stage1(
                turn_context=turn_context,
                intent_raw=intent,
                segmentation_raw=segmentation,
                participant_raw=participants,
            )
        else:
            stage1 = stage1_override
            attempts["stage1_override"] += 1
        latency["stage1_ms"] = _elapsed_ms(started)

        started = time.perf_counter()
        items = list(iter_v3_items(stage1))
        candidate_sets = [self.candidate_retriever.recall(item) for item in items]
        latency["candidate_recall_ms"] = _elapsed_ms(started)

        started = time.perf_counter()
        observations: list[V3VerifiedEvidence] = []
        for index, (item, candidate_set) in enumerate(
            zip(items, candidate_sets, strict=True), start=1
        ):
            raw_verification = await self._structured(
                payload=self._item_verifier_payload(item, candidate_set),
                response_model=V3ItemVerificationRaw,
                stage=f"stage2_item:{item.item_key}",
            )
            attempts["stage2_item"] += 1
            if raw_verification.item_key != item.item_key:
                raise InputPreprocessingContractError(
                    "v3_stage2_item_key_mismatch:"
                    f"{raw_verification.item_key}:{item.item_key}"
                )
            observations.append(
                self._compose_verified_evidence(
                    evidence_id=f"e-{index}",
                    item=item,
                    candidate_set=candidate_set,
                    verification=raw_verification,
                ),
            )
        stage2 = V3Stage2Output(observations=observations)
        latency["stage2_ms"] = _elapsed_ms(started)

        started = time.perf_counter()
        gates = evaluate_v3_quality_gates(
            user_text=user_text,
            turn_context=turn_context,
            stage1=stage1,
            candidate_sets=candidate_sets,
            stage2=stage2,
            vocabulary=self.vocabulary,
        )
        latency["quality_gates_ms"] = _elapsed_ms(started)
        return V3InputAnalysisResult(
            turn_context=turn_context,
            stage1=stage1,
            candidate_sets=candidate_sets,
            stage2=stage2,
            gates=gates,
            stage_latency_ms=latency,
            stage_attempts=dict(attempts),
            model_name=self.model,
            vocabulary_version=self.vocabulary.version,
            candidate_recall_version=self.candidate_retriever.recall_version,
        )

    def _intent_payload(
        self,
        user_text: str,
        turn_context: V3TurnContext,
    ) -> dict[str, Any]:
        return {
            "task": "只识别用户控制意图，不抽取医学事实，不判断风险。",
            "rules": [
                "answer_now 仅在用户明确要求先根据现有信息回答或不要继续追问时为 true。",
                "不输出症状、时间、药物或疾病事实。",
                "无法判断时保持 false 并降低 confidence。",
            ],
            "turn_context": self._turn_payload(turn_context),
            "user_text": user_text,
        }

    def _segmentation_payload(
        self,
        user_text: str,
        turn_context: V3TurnContext,
        *,
        intent_raw: V3TurnIntentRaw,
    ) -> dict[str, Any]:
        return {
            "task": "只组织输入，不验证医学概念，不做 canonical 映射。",
            "rules": [
                "source_text 必须是用户原文的连续片段，不得改写。",
                "analysis_text 仅允许补全指代和省略，不得引入新事实。",
                "独立主要事实输出 kind=atomic_claim，并给出 initial_assertion。",
                "一个断言作用到多个对象时输出 kind=shared_assertion_scope。",
                "shared_assertion_scope 必须逐项输出 items，不合并、不遗漏。",
                "控制意图不输出为 fact segment。",
                "若 identified_control_intent 已覆盖全部输入且没有医学事实，segments 应为空。",
                "不判断疾病、急诊、治疗或问诊槽位。",
            ],
            "turn_context": self._turn_payload(turn_context),
            "identified_control_intent": intent_raw.model_dump(mode="json"),
            "user_text": user_text,
        }

    def _participant_payload(
        self,
        segmentation: V3ScopeSegmentationRawOutput,
        turn_context: V3TurnContext,
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for segment_index, segment in enumerate(segmentation.segments, start=1):
            if not segment.requires_evidence_analysis:
                continue
            if segment.kind == "atomic_claim":
                items.append(
                    {
                        "item_key": f"s-{segment_index}:item-1",
                        "source_text": segment.source_text,
                        "analysis_text": segment.analysis_text,
                    }
                )
            else:
                items.extend(
                    {
                        "item_key": f"s-{segment_index}:item-{item_index}",
                        "source_text": item.source_text,
                        "analysis_text": item.analysis_text,
                    }
                    for item_index, item in enumerate(segment.items, start=1)
                )
        return {
            "task": "只为列出的 item 绑定主体和事件参与者，不解释医学风险。",
            "rules": [
                "每个 item_key 必须且只能输出一次 binding。",
                "reference_id 只能来自 turn_context.entities。",
                "多宠物无法消歧时 resolution_status=ambiguous，并给出至少两个 subject_candidates。",
                "动作执行者、承受者、体验者和对象必须使用通用 role，不发明角色。",
                "无法解析时保留 missing，不得默认当前宠物。",
            ],
            "turn_context": self._turn_payload(turn_context),
            "items": items,
        }

    def _item_verifier_payload(
        self,
        item: V3ItemContext,
        candidate_set: V3CandidateSetLike,
    ) -> dict[str, Any]:
        return {
            "task": "只验证当前 item，不重新切分，不新增或合并 item。",
            "rules": [
                "item_key 必须原样返回。",
                "只验证 initial_assertion，不能改写成新的断言。",
                "canonical 未映射不影响断言验证；用户明确陈述时仍应返回 verified。",
                "participants 已由 Stage 1 提供，只输出验证状态，不重建参与者。",
                "没有 participants 时 participant_verification=not_applicable，不是 unresolved。",
                "confirmed 时 selected_candidate_id 必须来自 candidates。",
                "candidates 为空时不得 confirmed，应输出 not_found 或 unmapped_mention。",
                "无法确定时输出 mismatch / ambiguous / not_found / unresolved。",
                "不判断医学风险、急诊、治疗或回答充分性。",
            ],
            "item": {
                "item_key": item.item_key,
                "segment_id": item.segment_id,
                "item_id": item.item_id,
                "source_text": item.source_text,
                "analysis_text": item.analysis_text,
                "initial_assertion": item.initial_assertion,
                "subject": item.subject.model_dump(mode="json"),
                "participants": [
                    participant.model_dump(mode="json")
                    for participant in item.participants
                ],
            },
            "candidates": [
                candidate.model_dump(mode="json")
                for candidate in candidate_set.candidates
            ],
            "candidate_recall_status": candidate_set.recall_status,
        }

    def _compose_verified_evidence(
        self,
        *,
        evidence_id: str,
        item: V3ItemContext,
        candidate_set: V3CandidateSetLike,
        verification: V3ItemVerificationRaw,
    ) -> V3VerifiedEvidence:
        mapping_status = verification.mapping_status
        selected = self.candidate_retriever.selected_candidate(
            candidate_set,
            verification.selected_candidate_id,
        )
        if not candidate_set.candidates:
            if mapping_status == V3CanonicalMappingStatus.CONFIRMED:
                mapping_status = V3CanonicalMappingStatus.NOT_FOUND
            elif selected is None:
                mapping_status = (
                    mapping_status
                    if mapping_status
                    in {
                        V3CanonicalMappingStatus.AMBIGUOUS,
                        V3CanonicalMappingStatus.NOT_FOUND,
                        V3CanonicalMappingStatus.UNMAPPED_MENTION,
                        V3CanonicalMappingStatus.NEW_CONCEPT_REQUEST,
                        V3CanonicalMappingStatus.UNRESOLVED,
                    }
                    else V3CanonicalMappingStatus.NOT_FOUND
                )

        canonical_id = (
            selected.canonical_id
            if mapping_status == V3CanonicalMappingStatus.CONFIRMED
            and selected is not None
            else None
        )
        review_required = mapping_status != V3CanonicalMappingStatus.CONFIRMED
        return V3VerifiedEvidence(
            evidence_id=evidence_id,
            segment_id=item.segment_id,
            item_id=item.item_id,
            source_text=item.source_text,
            initial_assertion=V3AssertionState(item.initial_assertion),
            assertion_verification=verification.assertion_verification,
            mapping_status=mapping_status,
            selected_candidate_id=(
                verification.selected_candidate_id
                if candidate_set.candidates
                else None
            ),
            canonical_id=canonical_id,
            candidates=candidate_set.candidates,
            subject=item.subject,
            participants=list(item.participants),
            participant_verification=verification.participant_verification,
            temporal_verification=verification.temporal_verification,
            measurement_verification=verification.measurement_verification,
            review_required=review_required,
            confidence=verification.confidence,
            rationale=verification.rationale,
        )

    async def _structured(
        self,
        *,
        payload: dict[str, Any],
        response_model: type,
        stage: str,
    ) -> Any:
        try:
            return await self.qwen.chat_structured(
                _messages(payload),
                response_model=response_model,
                model=self.model,
                temperature=0.0,
            )
        except ValidationError as exc:
            raise InputPreprocessingContractError(
                f"v3_{stage}_invalid_schema:{_validation_details(exc)}"
            ) from exc
        except Exception as exc:
            raise InputPreprocessingDependencyError(
                f"v3_{stage}_failed:{type(exc).__name__}:{exc}"
            ) from exc

    def _turn_payload(self, turn_context: V3TurnContext) -> dict[str, Any]:
        references = turn_context.entity_references()
        return {
            "reference_time": turn_context.reference_time.isoformat(),
            "entities": [
                {
                    "reference_id": reference.reference_id,
                    "entity_type": reference.entity_type.value,
                    "display_name": reference.display_name,
                }
                for reference in references.values()
            ],
        }


type V3CandidateSetLike = V3CandidateSet


def _messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "role": "system",
            "content": (
                "你是兽医 Agent 的 V3 输入前置预处理实验器。你只输出结构化 JSON，"
                "不诊断、不判断风险、不生成建议、不扫描超出当前输入的内容。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _validation_details(exc: ValidationError) -> str:
    details = [
        {
            "loc": ".".join(str(part) for part in error.get("loc", ())),
            "type": error.get("type", ""),
            "msg": error.get("msg", ""),
        }
        for error in exc.errors()[:20]
    ]
    return json.dumps(details, ensure_ascii=False, separators=(",", ":"))[:2000]


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))

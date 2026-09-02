"""Second-round V2 two-stage structured shadow analyzer."""

from __future__ import annotations

import json
import math
import time
from typing import Any, Protocol

from pydantic import ValidationError

from .errors import InputPreprocessingContractError, InputPreprocessingDependencyError
from .v2_contracts import (
    V2InputAnalysisResult,
    V2Stage1Output,
    V2Stage2Output,
    V2TurnContext,
)
from .v2_gates import evaluate_v2_quality_gates
from .vocabulary import CanonicalVocabulary


class V2StructuredClient(Protocol):
    """Minimal structured model interface required by the V2 analyzer."""

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


class V2EmbeddingClient(Protocol):
    """Minimal synchronous embedding interface used only for candidate recall."""

    @property
    def available(self) -> bool: ...

    def embed(self, text: str) -> list[float]: ...


class InputPreprocessingV2Analyzer:
    """Run Stage 1 input organization and Stage 2 constrained verification."""

    def __init__(
        self,
        *,
        qwen: V2StructuredClient,
        vocabulary: CanonicalVocabulary,
        model: str,
        embeddings: V2EmbeddingClient | None = None,
        candidate_limit: int = 8,
    ) -> None:
        self.qwen = qwen
        self.vocabulary = vocabulary
        self.model = model
        self.embeddings = embeddings
        self.candidate_limit = max(1, candidate_limit)
        self._alias_vectors: list[tuple[str, str, list[float]]] | None = None

    async def analyze(
        self,
        *,
        user_text: str,
        turn_context: V2TurnContext,
        stage1_override: V2Stage1Output | None = None,
    ) -> V2InputAnalysisResult:
        """Return a report-only V2 evidence graph or raise an explicit error."""

        if not self.qwen.available:
            raise InputPreprocessingDependencyError("v2_qwen_client_unavailable")
        latency: dict[str, int] = {}
        attempts: dict[str, int] = {}

        started = time.perf_counter()
        if stage1_override is None:
            stage1, stage1_attempts = await self._call_with_one_retry(
                label="stage1",
                callback=lambda: self._stage1(user_text, turn_context),
            )
        else:
            stage1 = stage1_override
            stage1_attempts = 1
        latency["stage1_ms"] = _elapsed_ms(started)
        attempts["stage1"] = stage1_attempts

        started = time.perf_counter()
        initial_stage2, _ = await self._call_with_one_retry(
            label="stage2",
            callback=lambda: self._stage2(
                user_text=user_text,
                turn_context=turn_context,
                stage1=stage1,
            ),
        )
        if _stage2_handles_all(stage1, initial_stage2):
            stage2 = initial_stage2
            stage2_attempts = 1
        else:
            # A bounded same-contract retry keeps Stage 1 as the item authority and
            # avoids asking Stage 2 to reinterpret the complete raw turn.
            stage2 = await self._stage2_by_segment(
                user_text=user_text,
                turn_context=turn_context,
                stage1=stage1,
            )
            stage2_attempts = 2
        latency["stage2_ms"] = _elapsed_ms(started)
        attempts["stage2"] = stage2_attempts

        started = time.perf_counter()
        gates = evaluate_v2_quality_gates(
            user_text=user_text,
            turn_context=turn_context,
            stage1=stage1,
            stage2=stage2,
            vocabulary=self.vocabulary,
        )
        latency["quality_gates_ms"] = _elapsed_ms(started)
        return V2InputAnalysisResult(
            turn_context=turn_context,
            stage1=stage1,
            stage2=stage2,
            gates=gates,
            stage_latency_ms=latency,
            stage_attempts=attempts,
            model_name=self.model,
            vocabulary_version=self.vocabulary.version,
        )

    async def _stage1(
        self,
        user_text: str,
        turn_context: V2TurnContext,
    ) -> V2Stage1Output:
        payload = {
            "task": "Stage 1：组织用户输入，不验证医学概念，不做风险判断。",
            "rules": [
                "source_text 必须是用户原文的连续片段；analysis_text 仅允许补全指代和省略。",
                "独立主要事实输出 kind=atomic_claim，expected_evidence_count 固定为 1。",
                "atomic_claim.initial_assertion 表达 Stage 1 对该主要断言的初分类，Stage 2 必须验证而不是随意替换。",
                "一个断言作用到多个对象时输出 kind=shared_assertion_scope。",
                "shared_assertion_scope.expected_evidence_count 必须等于 items 数量。",
                "每个 item 必须有 item_id、原文、主体初绑定和必要的参与者初解析。",
                "subject 与 participants 的 reference_id 只能来自 turn_context，或 subject_ambiguous / subject_missing。",
                "多宠物歧义必须输出 subject_ambiguous，并在 subject_candidates 中给出至少两个可信候选。",
                "用户要求先根据现有信息回答、不要继续追问时 intent.answer_now=true。",
                "不判断疾病、急诊、治疗或问诊槽位；不输出 canonical ID。",
            ],
            "turn_context": self._turn_payload(turn_context),
            "user_text": user_text,
        }
        return await self._structured(
            payload,
            response_model=V2Stage1Output,
            stage="stage1",
        )

    async def _stage2(
        self,
        *,
        user_text: str,
        turn_context: V2TurnContext,
        stage1: V2Stage1Output,
    ) -> V2Stage2Output:
        payload = {
            "task": "Stage 2：只验证 Stage 1 声明的每个 item，不重新切分用户原文。",
            "output_requirement": (
                "observations 必须非空，并逐项包含 stage1 中所有 "
                "requires_evidence_analysis=true 的 segment/item。"
            ),
            "rules": [
                "必须逐项输出 Stage 1 中每个 requires_evidence_analysis 的 item，不得新增或遗漏 item。",
                "canonical_id 只能来自 canonical_catalog 或 candidate_canonical_ids；不得发明 ID。",
                "mapping_status=confirmed 时，candidates 必须非空，且 canonical_id 必须出现在 candidates 中。",
                "mapping_status=confirmed 时才允许 canonical_id；not_found / unmapped_mention / ambiguous 必须省略 canonical_id。",
                "not_found 是状态，不是 canonical_id；用户明确表达但词表缺失应输出 unmapped_mention 或 not_found 并 review_required=true。",
                "shared_assertion_scope 的每个 item 必须继承 scope_assertion。",
                "atomic_claim 的每个 item 必须验证 Stage 1 的 initial_assertion。",
                "subject 和 participant 只能验证 Stage 1 绑定或标记 unresolved，不得默认当前宠物。",
                "不判断医学风险、急诊、治疗或回答充分性。",
                "无法确定时保留 ambiguous / not_found / unresolved 状态，不得补造事实。",
            ],
            "turn_context": self._turn_payload(turn_context),
            "stage1": stage1.model_dump(mode="json"),
            "candidate_canonical_ids": self._recall_candidates(stage1),
            "canonical_catalog": self.vocabulary.to_prompt_payload(),
            "user_text_for_evidence_anchor_only": user_text,
        }
        return await self._structured(
            payload,
            response_model=V2Stage2Output,
            stage="stage2",
        )

    async def _stage2_by_segment(
        self,
        *,
        user_text: str,
        turn_context: V2TurnContext,
        stage1: V2Stage1Output,
    ) -> V2Stage2Output:
        observations = []
        evidence_index = 1
        for segment in stage1.segments:
            if not segment.requires_evidence_analysis:
                continue
            single_stage1 = stage1.model_copy(update={"segments": [segment]})
            output = await self._stage2(
                user_text=user_text,
                turn_context=turn_context,
                stage1=single_stage1,
            )
            for item in output.observations:
                observations.append(
                    item.model_copy(update={"evidence_id": f"e-{evidence_index}"})
                )
                evidence_index += 1
        return V2Stage2Output(observations=observations)

    async def _structured(
        self,
        payload: dict[str, Any],
        *,
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
                f"v2_{stage}_invalid_schema:{_validation_details(exc)}"
            ) from exc
        except Exception as exc:
            raise InputPreprocessingDependencyError(
                f"v2_{stage}_failed:{type(exc).__name__}:{exc}"
            ) from exc

    async def _call_with_one_retry(
        self,
        *,
        label: str,
        callback: Any,
    ) -> tuple[Any, int]:
        try:
            return await callback(), 1
        except (InputPreprocessingContractError, InputPreprocessingDependencyError):
            return await callback(), 2

    def _recall_candidates(self, stage1: V2Stage1Output) -> dict[str, list[str]]:
        """Return registry or embedding candidates for each Stage 1 item."""

        result: dict[str, list[str]] = {}
        for segment in stage1.segments:
            if not segment.requires_evidence_analysis:
                continue
            if segment.kind == "atomic_claim":
                key = f"{segment.segment_id}:{segment.item_id}"
                result[key] = self._candidate_ids(segment.source_text)
            else:
                for item in segment.items:
                    key = f"{segment.segment_id}:{item.item_id}"
                    result[key] = self._candidate_ids(item.source_text)
        return result

    def _candidate_ids(self, text: str) -> list[str]:
        if self.embeddings is None or not self.embeddings.available:
            return [term.canonical_id for term in self.vocabulary.terms]
        vectors = self._load_alias_vectors()
        if not vectors:
            return []
        query = self.embeddings.embed(text)
        scored = sorted(
            (
                (_cosine(query, vector), canonical_id, alias)
                for canonical_id, alias, vector in vectors
            ),
            key=lambda item: (-item[0], item[1], item[2]),
        )
        ids: list[str] = []
        for _, canonical_id, _ in scored:
            if canonical_id not in ids:
                ids.append(canonical_id)
            if len(ids) >= self.candidate_limit:
                break
        return ids

    def _load_alias_vectors(self) -> list[tuple[str, str, list[float]]]:
        assert self.embeddings is not None
        if self._alias_vectors is None:
            self._alias_vectors = [
                (term.canonical_id, alias, self.embeddings.embed(alias))
                for term in self.vocabulary.terms
                for alias in term.aliases
            ]
        return self._alias_vectors

    def _turn_payload(self, turn_context: V2TurnContext) -> dict[str, Any]:
        return {
            "reference_time": turn_context.reference_time.isoformat(),
            "current_pet": turn_context.current_pet_subject.model_dump(mode="json"),
            "other_subjects": [
                item.model_dump(mode="json") for item in turn_context.other_subjects
            ],
            "previous_question_target": (
                turn_context.previous_question_target.model_dump(mode="json")
                if turn_context.previous_question_target is not None
                else None
            ),
        }


def _messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是兽医 Agent 的输入前置预处理实验器。你只输出结构化 JSON，"
                "不诊断、不判断风险、不生成建议、不扫描超出用户原文的内容。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _validation_details(exc: ValidationError) -> str:
    """Return bounded, serializable Pydantic error details for trace reports."""

    details = [
        {
            "loc": ".".join(str(part) for part in error.get("loc", ())),
            "type": error.get("type", ""),
            "msg": error.get("msg", ""),
        }
        for error in exc.errors()[:20]
    ]
    return json.dumps(details, ensure_ascii=False, separators=(",", ":"))[:2000]


def _stage2_handles_all(
    stage1: V2Stage1Output,
    stage2: V2Stage2Output,
) -> bool:
    expected = {
        (segment.segment_id, item_id)
        for segment in stage1.segments
        if segment.requires_evidence_analysis
        for item_id in (
            {item.item_id for item in segment.items}
            if segment.kind == "shared_assertion_scope"
            else {segment.item_id}
        )
    }
    actual = {(item.segment_id, item.item_id) for item in stage2.observations}
    return expected == actual


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)

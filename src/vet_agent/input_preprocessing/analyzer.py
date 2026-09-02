"""Two-stage structured analyzer for input-preprocessing shadow validation."""

from __future__ import annotations

import json
import math
import time
from typing import Any, Protocol

from pydantic import ValidationError

from vet_agent.runtime import EmbeddingClient

from .contracts import (
    CanonicalCandidate,
    EvidenceAnalysisOutput,
    InputAnalysisResult,
    SegmentationOutput,
    SegmentModel,
    TurnContext,
)
from .errors import InputPreprocessingContractError, InputPreprocessingDependencyError
from .gates import evaluate_quality_gates
from .vocabulary import CanonicalVocabulary


class QwenStructuredClient(Protocol):
    """Minimal structured-model client required by the shadow analyzer."""

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


class InputPreprocessingAnalyzer:
    """Run segmentation, candidate recall, and structured evidence analysis."""

    def __init__(
        self,
        *,
        qwen: QwenStructuredClient,
        embeddings: EmbeddingClient,
        vocabulary: CanonicalVocabulary,
        model: str,
        candidate_limit: int = 8,
    ) -> None:
        self.qwen = qwen
        self.embeddings = embeddings
        self.vocabulary = vocabulary
        self.model = model
        self.candidate_limit = max(1, candidate_limit)
        self._canonical_vectors: dict[str, list[list[float]]] = {}

    async def analyze(
        self,
        *,
        user_text: str,
        turn_context: TurnContext,
        segmentation_override: SegmentationOutput | None = None,
    ) -> InputAnalysisResult:
        """Return a shadow evidence graph or raise an explicit contract error."""

        if not self.qwen.available:
            raise InputPreprocessingDependencyError(
                "qwen_structured_client_unavailable"
            )
        if not self.embeddings.available:
            raise InputPreprocessingDependencyError("embedding_client_unavailable")

        latency: dict[str, int] = {}
        started = time.perf_counter()
        segmentation = (
            segmentation_override
            if segmentation_override is not None
            else await self._segment(user_text, turn_context)
        )
        latency["segmentation_ms"] = _elapsed_ms(started)

        started = time.perf_counter()
        candidates = self._recall_candidates(segmentation)
        latency["canonical_recall_ms"] = _elapsed_ms(started)

        started = time.perf_counter()
        analysis_segments = [
            segment
            for segment in segmentation.segments
            if segment.requires_evidence_analysis
        ]
        evidence_attempts = 1
        try:
            evidence = await self._analyze_evidence(
                user_text=user_text,
                turn_context=turn_context,
                segments=analysis_segments,
                candidates=candidates,
            )
        except (
            InputPreprocessingContractError,
            InputPreprocessingDependencyError,
        ):
            evidence_attempts = 2
            evidence = await self._analyze_evidence_by_segment(
                user_text=user_text,
                turn_context=turn_context,
                segments=analysis_segments,
                candidates=candidates,
            )
        latency["evidence_analysis_ms"] = _elapsed_ms(started)

        gates = evaluate_quality_gates(
            user_text=user_text,
            turn_context=turn_context,
            segmentation=segmentation,
            evidence=evidence,
            vocabulary=self.vocabulary,
        )
        latency["quality_gates_ms"] = _elapsed_ms(started)
        result = InputAnalysisResult(
            turn_context=turn_context,
            segmentation=segmentation,
            evidence=evidence,
            gates=gates,
            stage_latency_ms=latency,
            stage_attempts={"evidence_analysis": evidence_attempts},
            model_name=self.model,
            vocabulary_version=self.vocabulary.version,
        )
        return result

    async def _segment(
        self, user_text: str, turn_context: TurnContext
    ) -> SegmentationOutput:
        payload = {
            "task": "将用户本轮输入切分为 claim-level 片段并标记话语角色。",
            "rules": [
                "只使用用户本轮输入中明确表达的信息。",
                "source_text 必须是用户原文的连续片段，不得改写。",
                "analysis_text 仅允许补全指代和省略，不得加入新事实。",
                "不判断疾病、风险、急诊或治疗。",
                "事实陈述、用户问题、控制意图、历史、假设和不确定表达必须分开。",
                "用户明确要求先根据现有信息回答、不要继续追问时，intent.answer_now=true。",
                "expected_fact_candidate_count 表示可进行事实分析的片段数量，不是医学严重度。",
                (
                    "一个 segment 可以由共享否定范围派生多个 evidence；"
                    "expected_evidence_count 表示该 segment 应输出的 evidence 数量。"
                ),
                "共享否定、并列症状和并列状态不得只保留第一个项目。",
                "每个事实 segment 必须输出 subject_reference；主体只能来自 turn_context 或 subject_ambiguous。",
                "主体歧义时 subject_reference=subject_ambiguous，并在 subject_candidates 中列出可信主体候选。",
            ],
            "turn_context": _prompt_context(turn_context),
            "user_text": user_text,
        }
        try:
            return await self.qwen.chat_structured(
                _messages(payload),
                response_model=SegmentationOutput,
                model=self.model,
                temperature=0.0,
            )
        except ValidationError as exc:
            raise InputPreprocessingContractError(
                "segmentation_invalid_schema"
            ) from exc
        except Exception as exc:
            raise InputPreprocessingDependencyError("segmentation_failed") from exc

    def _recall_candidates(
        self, segmentation: SegmentationOutput
    ) -> dict[str, list[CanonicalCandidate]]:
        self._load_canonical_vectors()
        result: dict[str, list[CanonicalCandidate]] = {}
        for segment in segmentation.segments:
            if not segment.requires_evidence_analysis:
                result[segment.segment_id] = []
                continue
            segment_vector = self.embeddings.embed(segment.analysis_text)
            scored: list[tuple[float, str, str]] = []
            for canonical_id, vectors in self._canonical_vectors.items():
                term = next(
                    item
                    for item in self.vocabulary.terms
                    if item.canonical_id == canonical_id
                )
                for alias, vector in zip(term.aliases, vectors, strict=True):
                    score = _cosine(segment_vector, vector)
                    scored.append((score, canonical_id, alias))
            ranked = sorted(scored, key=lambda item: (-item[0], item[1], item[2]))
            result[segment.segment_id] = [
                CanonicalCandidate(
                    canonical_id=canonical_id,
                    surface_form=surface_form,
                    score=_normalized_score(score),
                )
                for score, canonical_id, surface_form in ranked[: self.candidate_limit]
            ]
        return result

    async def _analyze_evidence(
        self,
        *,
        user_text: str,
        turn_context: TurnContext,
        segments: list[SegmentModel],
        candidates: dict[str, list[CanonicalCandidate]],
    ) -> EvidenceAnalysisOutput:
        prompt_candidates = {
            segment_id: [candidate.model_dump() for candidate in segment_candidates]
            for segment_id, segment_candidates in candidates.items()
        }
        payload = {
            "task": "为每个 segment 输出断言、主体、时间、度量和 canonical 映射。",
            "rules": [
                "canonical_id 只能来自候选列表或 not_found 结果。",
                "embedding 候选不是事实，必须由你根据原文验证。",
                "断言和 canonical concept 分离；不要把 denied 合并进 canonical_id。",
                "normal、denied、unknown、historical、hypothetical 必须区分。",
                "subject_reference 只能使用 turn_context 提供的引用，或 subject_ambiguous / subject_missing。",
                "多宠物无法消歧时输出 subject_ambiguous，不得默认当前宠物。",
                "subject_reference=subject_ambiguous 时，subject_type 必须是 unknown，resolution_method 必须是 subject_ambiguous。",
                (
                    "subject_ambiguous 必须携带 subject_candidates，"
                    "候选只能来自 turn_context 中的 subject reference。"
                ),
                "Stage 1 已给出的 subject binding 必须保留，除非原文有明确相反指代。",
                "用户说“好像”“可能”时 assertion=possible，不得改成 present。",
                "不要为多宠物环境、信息不足或提问本身发明新的 canonical_id。",
                "无法归一化的时间或度量输出 unresolved，不得猜测数值。",
                "canonical_status=not_found 时不要输出 observations 条目，不要把 not_found 当 canonical_id。",
                (
                    "用户明确表达但无法映射到 canonical 时，输出 unmapped_mentions；"
                    "不要把 not_found、ambiguous 或类型不匹配写成 canonical_id。"
                ),
                "requires_evidence_analysis=false 的 segment 不得生成 observation。",
                "evidence_id 必须全局唯一；直接使用 e1、e2、e3 递增命名。",
                "同一句中的并列症状和否定范围必须逐项拆成 observations，不得只输出第一个。",
                "temporal_observations 挂在哪个 observation 下，就表示修饰哪个 canonical 事实。",
                "status=unresolved 的 temporal 或 measurement 不得猜测数值。",
                "normal 只能表示健康/状态维度正常；干预动作应使用 present 或 historical，不能使用 normal。",
                "明确症状存在或异常时使用 present 或 abnormal，不能因为风险低而改成 normal。",
                "不诊断、不评估风险、不生成医疗建议。",
            ],
            "examples": [
                {
                    "source_text": "没有呕吐",
                    "canonical_id": "vomiting",
                    "assertion": "denied",
                },
                {
                    "source_text": "精神正常",
                    "canonical_id": "mental_status",
                    "assertion": "normal",
                },
                {
                    "source_text": "大便偏软",
                    "canonical_id": "soft_stool",
                    "assertion": "abnormal",
                },
                {
                    "source_text": "前天开始换新猫粮",
                    "canonical_id": "diet_change",
                    "assertion": "present",
                },
                {
                    "source_text": "其中一只好像呕吐",
                    "canonical_id": "vomiting",
                    "assertion": "possible",
                    "subject": {
                        "subject_reference": "subject_ambiguous",
                        "subject_type": "unknown",
                        "resolution_method": "subject_ambiguous",
                    },
                },
            ],
            "canonical_catalog": self.vocabulary.to_prompt_payload(),
            "recall_candidates": prompt_candidates,
            "turn_context": _prompt_context(turn_context),
            "segments": [segment.model_dump() for segment in segments],
            "user_text": user_text,
        }
        try:
            output = await self.qwen.chat_structured(
                _messages(payload),
                response_model=EvidenceAnalysisOutput,
                model=self.model,
                temperature=0.0,
            )
            return self._normalize_subject_bindings(output, segments=segments)
        except ValidationError as exc:
            raise InputPreprocessingContractError("evidence_invalid_schema") from exc
        except Exception as exc:
            raise InputPreprocessingDependencyError(
                f"evidence_analysis_failed:{type(exc).__name__}:{exc}"
            ) from exc

    def _normalize_subject_bindings(
        self,
        output: EvidenceAnalysisOutput,
        *,
        segments: list[SegmentModel],
    ) -> EvidenceAnalysisOutput:
        """Merge Stage 1 subject provenance into optional Stage 2 fields."""

        segment_map = {segment.segment_id: segment for segment in segments}
        observations = []
        for observation in output.observations:
            segment = segment_map.get(observation.segment_id)
            if (
                segment is not None
                and segment.subject_reference is not None
                and observation.subject.subject_reference == segment.subject_reference
            ):
                updates: dict[str, Any] = {}
                if segment.subject_reference == "subject_ambiguous":
                    updates["resolution_status"] = "ambiguous"
                    updates["subject_candidates"] = list(segment.subject_candidates)
                elif observation.subject.resolution_status == "resolved":
                    updates["resolution_status"] = "resolved"
                if updates:
                    observation = observation.model_copy(
                        update={
                            "subject": observation.subject.model_copy(update=updates)
                        }
                    )
            observations.append(observation)
        return EvidenceAnalysisOutput(
            observations=observations,
            unmapped_mentions=output.unmapped_mentions,
        )

    async def _analyze_evidence_by_segment(
        self,
        *,
        user_text: str,
        turn_context: TurnContext,
        segments: list[SegmentModel],
        candidates: dict[str, list[CanonicalCandidate]],
    ) -> EvidenceAnalysisOutput:
        """Retry the same evidence contract with bounded per-segment batches."""

        observations = []
        for segment in segments:
            output = await self._analyze_evidence(
                user_text=user_text,
                turn_context=turn_context,
                segments=[segment],
                candidates=candidates,
            )
            observations.extend(output.observations)
        renumbered = [
            observation.model_copy(update={"evidence_id": f"e-{index}"})
            for index, observation in enumerate(observations, start=1)
        ]
        return EvidenceAnalysisOutput(observations=renumbered)

    def _load_canonical_vectors(self) -> None:
        if self._canonical_vectors:
            return
        vectors: dict[str, list[list[float]]] = {}
        for term in self.vocabulary.terms:
            vectors[term.canonical_id] = [
                self.embeddings.embed(alias) for alias in term.aliases
            ]
        self._canonical_vectors = vectors


def _messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是兽医 Agent 的输入前置分析器。"
                "你只输出结构化证据，不诊断，不判断风险，不生成追问或建议。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _prompt_context(turn_context: TurnContext) -> dict[str, Any]:
    return {
        "reference_time": turn_context.reference_time.isoformat(),
        "subjects": [
            {
                "reference_id": subject.reference_id,
                "subject_type": subject.subject_type,
                "display_name": subject.display_name,
            }
            for subject in turn_context.subject_references().values()
        ],
        "previous_question_target": turn_context.previous_question_target.model_dump(
            exclude_none=True
        )
        if turn_context.previous_question_target
        else None,
        "verified_pet_profile": turn_context.verified_pet_profile,
        "input_channel": turn_context.input_channel,
    }


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise InputPreprocessingDependencyError("embedding_dimension_mismatch")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _normalized_score(score: float) -> float:
    return max(0.0, min(1.0, (score + 1.0) / 2.0))


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))

"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/claim_graph_contracts.py
作用：定义受限语义协作 DAG M12 的 Claim Graph 结构投影契约。
范围：覆盖 graph 身份、Turn Intent / Claim / Gap / Repair / Gate 节点、
      构建策略、结构终态、失败码与 canonical graph digest。
说明：本文件只承载结构契约，不判断语义重复、语义冲突、医学正确性、
      gap 合理性或修复后的语言质量。
=============================================================================
"""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .artifact_contracts import (
    SemanticArtifactGate,
    SemanticClarificationGapStatus,
    SemanticTurnIntentSection,
)
from .review_contracts import (
    ClaimFaithfulnessReviewDimension,
    ClarificationBindingType,
    SemanticReviewOutcome,
)


class SemanticGraphTerminalState(StrEnum):
    """表示 M12 结构投影后的图级可消费状态。

    :return: 无返回值；该状态不表示 M12 重新做出语义判断。
    """

    GRAPH_VERIFIED = "graph_verified"
    GRAPH_PARTIAL_WITH_GAPS = "graph_partial_with_gaps"
    GRAPH_DISAGREEMENT = "graph_disagreement"
    GRAPH_BLOCKED = "graph_blocked"


class SemanticGraphFailureCode(StrEnum):
    """表示 M12 结构门禁失败的稳定错误编码。

    :return: 无返回值；未知失败不得进入图消费。
    """

    ARTIFACT_MISSING = "artifact_missing"
    ARTIFACT_STALE = "artifact_stale"
    PAYLOAD_DIGEST_MISMATCH = "payload_digest_mismatch"
    TURN_INTENT_MISSING = "turn_intent_missing"
    CLAIM_INDEX_INVALID = "claim_index_invalid"
    CLAIM_DIGEST_MISMATCH = "claim_digest_mismatch"
    DUPLICATE_CLAIM = "duplicate_claim"
    REVIEW_BUNDLE_MISSING = "review_bundle_missing"
    REVIEW_COVERAGE_INCOMPLETE = "review_coverage_incomplete"
    REVIEW_CLAIM_MISMATCH = "review_claim_mismatch"
    GAP_REFERENCE_INVALID = "gap_reference_invalid"
    REPAIR_LINEAGE_INVALID = "repair_lineage_invalid"
    REPAIR_VERIFICATION_PENDING = "repair_verification_pending"
    EVIDENCE_BINDING_PENDING = "evidence_binding_pending"
    GATE_MISSING = "gate_missing"
    GATE_SEQUENCE_INVALID = "gate_sequence_invalid"
    UPSTREAM_DISAGREEMENT = "upstream_disagreement"
    EMPTY_GRAPH_NOT_ALLOWED = "empty_graph_not_allowed"
    PARTIAL_GRAPH_NOT_ALLOWED = "partial_graph_not_allowed"
    STRUCTURE_INVALID = "structure_invalid"
    GATE_NOT_CONSUMABLE = "gate_not_consumable"
    SUSPICIOUS_EMPTY = "suspicious_empty"


class SemanticGraphEmptyReason(StrEnum):
    """表示空 claim graph 的上游结构化派生原因。

    :return: 无返回值；该原因来自 M08 derived result，不由 M12 解释原文。
    """

    NO_EXPLICIT_FACT = "no_explicit_fact"
    SUSPICIOUS_EMPTY = "suspicious_empty"


class SemanticGraphBuildPolicy(BaseModel):
    """表示 M12 graph 构建与下游消费策略。

    :return: 无返回值；策略只控制结构消费，不引入语义判断。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    contract_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    allow_partial_with_gaps: bool = Field(default=False)
    allow_empty_graph: bool = Field(default=False)


class SemanticGraphIdentity(BaseModel):
    """表示 Claim Graph 与 M11 artifact 的结构身份绑定。

    :return: 无返回值；graph 是可重建投影，不是第二权威事实源。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    graph_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    graph_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    graph_contract_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    run_id: str = Field(min_length=1, max_length=64)
    turn_snapshot_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_reference: str = Field(min_length=1, max_length=512)
    artifact_version: int = Field(ge=1)
    artifact_payload_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    current_gate_sequence: int = Field(ge=1)


class SemanticGraphIntentNode(BaseModel):
    """表示从 M11 payload 内嵌 section 读取的 Turn Intent 节点。

    :return: 无返回值；M12 不重新判断 intent 与 claim 的语义关系。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    source_task_id: str = Field(min_length=1, max_length=360)
    source_attempt_number: int = Field(ge=1)
    source_proposal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    answer_now: bool
    wants_triage: bool
    correction: bool
    clarification_request: bool
    fact_statement_present: bool
    question_present: bool
    report_context_present: bool

    @classmethod
    def from_section(
        cls,
        section: SemanticTurnIntentSection,
    ) -> SemanticGraphIntentNode:
        """将 M11 Turn Intent section 投影为 graph 节点。

        :param section: M11 权威 payload 内的 Turn Intent section。
        :return: 返回结构等价的 graph intent 节点。
        """
        return cls(
            source_task_id=section.source_task_id,
            source_attempt_number=section.source_attempt_number,
            source_proposal_digest=section.source_proposal_digest,
            answer_now=section.answer_now,
            wants_triage=section.wants_triage,
            correction=section.correction,
            clarification_request=section.clarification_request,
            fact_statement_present=section.fact_statement_present,
            question_present=section.question_present,
            report_context_present=section.report_context_present,
        )


class SemanticGraphClaimNode(BaseModel):
    """表示一个绑定 artifact version 的 scoped claim 节点。

    :return: 无返回值；claim_ref 不跨版本迁移旧审查结论。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    claim_ref: str = Field(min_length=1, max_length=512)
    claim_index: int = Field(ge=0, le=7)
    claim_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    proposition: str = Field(min_length=1, max_length=240)
    review_status: SemanticReviewOutcome
    gap_refs: tuple[str, ...] = Field(default=(), max_length=8)


class SemanticGraphGapNode(BaseModel):
    """表示一个当前版本中的 clarification gap 节点。

    :return: 无返回值；gap 不是自动追问指令，也不是 verified fact。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    gap_id: str = Field(min_length=1, max_length=240)
    claim_ref: str = Field(min_length=1, max_length=512)
    claim_index: int = Field(ge=0, le=7)
    claim_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    ambiguous_dimension: ClaimFaithfulnessReviewDimension
    required_binding_type: ClarificationBindingType
    status: SemanticClarificationGapStatus


class SemanticGraphRepairNode(BaseModel):
    """表示 M11 payload 内嵌 repair lineage 的图节点。

    :return: 无返回值；M12 不重新应用 patch，也不评估修复质量。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    repair_plan_id: str = Field(min_length=64, max_length=64)
    base_artifact_reference: str = Field(min_length=1, max_length=512)
    base_version: int = Field(ge=1)
    result_artifact_reference: str = Field(min_length=1, max_length=512)
    result_version: int = Field(ge=1)
    repair_depth: int = Field(ge=0, le=1)


class SemanticGraphGateNode(BaseModel):
    """表示当前 M11 gate event 的 graph 投影。

    :return: 无返回值；M12 只消费 gate 状态，不改写 gate。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    gate: SemanticArtifactGate
    sequence: int = Field(ge=1)
    reason_code: str = Field(min_length=1, max_length=160)


class SemanticClaimGraph(BaseModel):
    """表示从单个 M11 回合 artifact 重建的结构化 Claim Graph。

    :return: 无返回值；该 graph 是确定性投影，不是第二权威存储。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    identity: SemanticGraphIdentity
    intent: SemanticGraphIntentNode
    claims: tuple[SemanticGraphClaimNode, ...] = Field(max_length=8)
    gaps: tuple[SemanticGraphGapNode, ...] = Field(default=(), max_length=8)
    repair: SemanticGraphRepairNode | None = None
    gate: SemanticGraphGateNode
    terminal_state: SemanticGraphTerminalState
    empty_reason: SemanticGraphEmptyReason | None = None

    @model_validator(mode="after")
    def validate_graph_shape(self) -> Self:
        """校验 graph 内部 scoped 引用与状态负载闭合。

        :return: 返回通过结构闭合校验的 graph。
        :raises ValueError: claim 重复或 gap 引用缺失时抛出。
        """
        claim_refs = {claim.claim_ref for claim in self.claims}
        if len(claim_refs) != len(self.claims):
            raise ValueError("semantic graph claim reference is duplicate")
        gap_refs = {gap.gap_id for gap in self.gaps}
        if len(gap_refs) != len(self.gaps):
            raise ValueError("semantic graph gap reference is duplicate")
        if any(gap.claim_ref not in claim_refs for gap in self.gaps):
            raise ValueError("semantic graph gap claim reference is missing")
        if self.empty_reason is not None and self.claims:
            raise ValueError("non-empty semantic graph cannot carry empty reason")
        return self


class SemanticGraphBuildResult(BaseModel):
    """表示一次 M12 graph 构建和结构门禁结果。

    :return: 无返回值；blocked 结果不携带可消费 graph。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    terminal_state: SemanticGraphTerminalState
    graph: SemanticClaimGraph | None = None
    failure_code: SemanticGraphFailureCode | None = None
    failure_message: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_result_payload(self) -> Self:
        """校验 graph 构建结果与失败负载闭合。

        :return: 返回通过显式状态机校验的构建结果。
        :raises ValueError: blocked 缺失败或非 blocked 带失败时抛出。
        """
        if self.terminal_state is SemanticGraphTerminalState.GRAPH_BLOCKED:
            if self.graph is not None or self.failure_code is None:
                raise ValueError("blocked semantic graph result payload is invalid")
            return self
        if self.graph is None or self.failure_code is not None:
            raise ValueError("accepted semantic graph result payload is invalid")
        return self


def canonical_claim_graph_json(value: object) -> str:
    """生成 M12 graph 契约对象的 canonical JSON。

    :param value: Pydantic graph 契约或可序列化字典。
    :return: 返回排序键、紧凑且保留 Unicode 的稳定 JSON。
    """
    material: object = (
        value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    )
    return json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_claim_graph_digest(graph: SemanticClaimGraph) -> str:
    """计算 Claim Graph canonical digest。

    :param graph: 已通过结构门禁的 graph。
    :return: 返回 SHA-256 graph digest。
    """
    material = graph.model_dump(
        mode="json",
        exclude={"identity": {"graph_digest"}},
    )
    return sha256(canonical_claim_graph_json(material).encode("utf-8")).hexdigest()


__all__ = [
    "SemanticClaimGraph",
    "SemanticGraphBuildPolicy",
    "SemanticGraphBuildResult",
    "SemanticGraphClaimNode",
    "SemanticGraphFailureCode",
    "SemanticGraphGapNode",
    "SemanticGraphGateNode",
    "SemanticGraphIdentity",
    "SemanticGraphIntentNode",
    "SemanticGraphRepairNode",
    "SemanticGraphTerminalState",
    "canonical_claim_graph_json",
    "compute_claim_graph_digest",
]

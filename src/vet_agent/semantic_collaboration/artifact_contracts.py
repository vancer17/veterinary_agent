"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/artifact_contracts.py
作用：定义受限语义协作 DAG M11 的回合级权威 artifact 与版本契约。
范围：覆盖 SemanticTurnArtifact payload、claim record、内嵌 review bundle、
      repair lineage、gate event、stale marker、派生索引、幂等提交请求、
      系统性人工审查记录与仓储端口。
说明：本文件不调用模型、不做医学判断、不写问诊或临床安全领域，也不把
      semantic_review_supported 伪装成 verified；权威粒度是回合级版本。
=============================================================================
"""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .patch_contracts import SemanticPatchSet
from .repair_contracts import ReviewRepairDimension
from .review_contracts import (
    ClaimFaithfulnessReviewDimension,
    ClarificationBindingType,
    SemanticReviewBundle,
    SemanticReviewOutcome,
    compute_claim_digest,
)


class SemanticArtifactKind(StrEnum):
    """表示 M11 权威 artifact 的生产类型。

    :return: 无返回值；当前采用回合级轻量粒度，不按 claim 拆 artifact。
    """

    SEMANTIC_TURN_RESULT = "semantic_turn_result"


class SemanticArtifactGate(StrEnum):
    """表示回合级语义 artifact 的证据与修复门禁状态。

    :return: 无返回值；gate 由 append-only event 派生，不是 payload 字段。
    """

    SCHEMA_VALID = "schema_valid"
    SEMANTIC_REVIEW_PENDING = "semantic_review_pending"
    SEMANTIC_REVIEW_SUPPORTED = "semantic_review_supported"
    REPAIR_REQUIRED = "repair_required"
    CLARIFICATION_REQUIRED = "clarification_required"
    REPAIR_THEN_CLARIFICATION_REQUIRED = "repair_then_clarification_required"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    PATCH_APPLIED = "patch_applied"
    EVIDENCE_BINDING_PENDING = "evidence_binding_pending"
    VERIFIED = "verified"
    REPAIR_VERIFIED = "repair_verified"
    DISAGREEMENT = "disagreement"
    REVIEW_FAILED = "review_failed"
    BLOCKED = "blocked"


class SemanticArtifactActorType(StrEnum):
    """表示触发 artifact gate event 的系统 actor 类型。

    :return: 无返回值；该枚举不表示医学或领域决策者。
    """

    SYSTEM = "system"
    HUMAN_REVIEWER = "human_reviewer"
    EVIDENCE_BINDING = "evidence_binding"


class SemanticArtifactStaleReason(StrEnum):
    """表示一个 artifact version 被新版本取代的稳定原因。

    :return: 无返回值；stale 是独立 marker，不修改不可变 payload。
    """

    SUPERSEDED_BY_REVIEW = "superseded_by_review"
    SUPERSEDED_BY_REPAIR = "superseded_by_repair"


class SemanticClaimReviewStatus(StrEnum):
    """表示派生 claim 索引中的当前门禁状态。

    :return: 无返回值；该索引用于查询统计，不是权威事实源。
    """

    SUPPORTED = "supported"
    REPAIR_REQUIRED = "repair_required"
    CLARIFICATION_REQUIRED = "clarification_required"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    DISAGREEMENT = "disagreement"
    REPAIR_PENDING = "repair_pending"


class SemanticClarificationGapStatus(StrEnum):
    """表示派生 clarification gap 索引状态。

    :return: 无返回值；gap 不是自动追问指令，也不是 verified 事实。
    """

    ACTIVE = "active"
    STALE = "stale"


class SemanticHumanReviewStatus(StrEnum):
    """表示系统性人工审查的稳定结论。

    :return: 无返回值；人工审查系统性缺陷，不逐条改写用户事实。
    """

    SYSTEM_DEFECT_CONFIRMED = "system_defect_confirmed"
    NO_SYSTEM_DEFECT = "no_system_defect"
    INCONCLUSIVE = "inconclusive"


class SemanticArtifactClaimRecord(BaseModel):
    """表示回合级 artifact payload 内的一条系统派生 claim 记录。

    :return: 无返回值；claim 逐条可审计，但不是独立权威 artifact。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    claim_index: int = Field(ge=0, le=7)
    claim_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    proposition: str = Field(min_length=1, max_length=240)


class SemanticTurnIntentSection(BaseModel):
    """表示内嵌于回合 artifact 的 Turn Intent 权威结果。

    :return: 无返回值；该 section 来自 M06/M07，不由 M12 重新推断。
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


class SemanticArtifactRepairLineage(BaseModel):
    """表示产生当前 artifact version 的 M10 patch 谱系。

    :return: 无返回值；谱系内嵌在回合 payload 中，随版本不可变。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    repair_plan_id: str = Field(min_length=64, max_length=64)
    patch_set: SemanticPatchSet
    base_artifact_reference: str = Field(min_length=1, max_length=512)
    base_version: int = Field(ge=1)
    repair_dimensions: tuple[ReviewRepairDimension, ...] = Field(default=())


class SemanticTurnArtifactPayload(BaseModel):
    """表示一个回合级语义结果的不可变权威 payload。

    :return: 无返回值；review bundle 与 repair lineage 是版本内嵌 section。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    contract_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    artifact_kind: SemanticArtifactKind
    run_id: str = Field(min_length=1, max_length=64)
    source_task_id: str = Field(min_length=1, max_length=360)
    source_attempt_number: int = Field(ge=1)
    turn_snapshot_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_proposal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    turn_intent: SemanticTurnIntentSection | None = None
    claims: tuple[SemanticArtifactClaimRecord, ...] = Field(max_length=8)
    review_bundle: SemanticReviewBundle | None = None
    repair_lineage: SemanticArtifactRepairLineage | None = None
    repair_depth: int = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def validate_payload_shape(self) -> Self:
        """校验回合 payload 的 claim、review 与 repair 版本闭合。

        :return: 返回可写入 M11 的不可变回合级 payload。
        :raises ValueError: claim 身份、review claims 或 repair depth 不匹配时抛出。
        """
        indexes = tuple(claim.claim_index for claim in self.claims)
        if indexes != tuple(range(len(self.claims))):
            raise ValueError("semantic artifact claim indexes are not contiguous")
        if any(
            compute_claim_digest(claim.proposition) != claim.claim_digest
            for claim in self.claims
        ):
            raise ValueError("semantic artifact claim digest mismatch")
        if self.review_bundle is not None and self.review_bundle.claims != tuple(
            claim.proposition for claim in self.claims
        ):
            raise ValueError("semantic artifact review claims mismatch")
        if self.repair_depth > 0 and self.repair_lineage is None:
            raise ValueError("repaired semantic artifact requires repair lineage")
        if self.repair_lineage is not None and self.repair_depth == 0:
            raise ValueError("repair lineage requires repair depth")
        if self.turn_intent is not None and (
            self.turn_intent.source_task_id == self.source_task_id
            or self.turn_intent.source_proposal_digest == self.source_proposal_digest
        ):
            raise ValueError("semantic turn intent source identity is ambiguous")
        return self


class SemanticArtifactVersionRecord(BaseModel):
    """表示从 M11 读取的一个权威 artifact version。

    :return: 无返回值；record 的 payload digest 必须可复算。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    artifact_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    version: int = Field(ge=1)
    artifact_kind: SemanticArtifactKind
    payload: SemanticTurnArtifactPayload
    payload_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_reference: str = Field(min_length=1, max_length=512)
    base_artifact_reference: str | None = Field(default=None, max_length=512)
    created_at: str = Field(min_length=1)


class SemanticArtifactCommitRequestKind(StrEnum):
    """表示 M11 幂等提交请求类型。

    :return: 无返回值；同一 request key 的不同 payload 必须冲突。
    """

    REVIEW_BUNDLE = "review_bundle"
    REPAIR_PATCH_SET = "repair_patch_set"
    GATE_EVENT = "gate_event"


class SemanticArtifactCommitRequestRecord(BaseModel):
    """表示一次已完成的 M11 幂等提交请求。

    :return: 无返回值；重放请求返回既有 result reference。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    request_key: str = Field(min_length=1, max_length=240)
    request_kind: SemanticArtifactCommitRequestKind
    artifact_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    version: int = Field(ge=1)
    payload_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    result_reference: str = Field(min_length=1, max_length=512)
    created_at: str = Field(min_length=1)


class SemanticArtifactGateEventRecord(BaseModel):
    """表示一条 artifact gate 状态演进事件。

    :return: 无返回值；当前 gate 由最大 sequence 派生。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    artifact_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    version: int = Field(ge=1)
    sequence: int = Field(ge=1)
    from_gate: SemanticArtifactGate | None
    to_gate: SemanticArtifactGate
    reason_code: str = Field(min_length=1, max_length=160)
    actor_type: SemanticArtifactActorType
    decision_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: dict[str, object] = Field(default_factory=dict)
    created_at: str = Field(min_length=1)


class SemanticArtifactStaleMarkerRecord(BaseModel):
    """表示一个 artifact version 被取代的 stale 记录。

    :return: 无返回值；stale 不改变 payload，也不迁移旧审查结论。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    artifact_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    version: int = Field(ge=1)
    stale_reason: SemanticArtifactStaleReason
    replaced_by_artifact_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    replaced_by_version: int = Field(ge=1)
    created_at: str = Field(min_length=1)


class SemanticClaimReviewIndexRecord(BaseModel):
    """表示从权威 payload 派生的 claim review 查询索引。

    :return: 无返回值；索引可重建，不作为 M11 权威事实源。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    artifact_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    version: int = Field(ge=1)
    run_id: str = Field(min_length=1, max_length=64)
    claim_index: int = Field(ge=0, le=7)
    claim_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    proposition: str = Field(min_length=1, max_length=240)
    review_status: SemanticClaimReviewStatus
    repair_dimensions: tuple[ReviewRepairDimension, ...] = Field(default=())
    created_at: str = Field(min_length=1)


class SemanticClarificationGapIndexRecord(BaseModel):
    """表示从权威 review payload 派生的 gap 查询索引。

    :return: 无返回值；gap 不自动生成追问，也不等于 unknown fact。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    artifact_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    version: int = Field(ge=1)
    run_id: str = Field(min_length=1, max_length=64)
    gap_id: str = Field(min_length=1, max_length=240)
    claim_index: int = Field(ge=0, le=7)
    claim_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    ambiguous_dimension: ClaimFaithfulnessReviewDimension
    required_binding_type: ClarificationBindingType
    status: SemanticClarificationGapStatus
    created_at: str = Field(min_length=1)


class SemanticSystemReviewRecord(BaseModel):
    """表示面向系统性缺陷的人工审查记录。

    :return: 无返回值；该记录不逐条修改 claim，也不批量标记 verified。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    review_id: str = Field(min_length=1, max_length=160)
    reviewer_role: str = Field(min_length=1, max_length=160)
    status: SemanticHumanReviewStatus
    review_dimension: ClaimFaithfulnessReviewDimension | None = None
    summary_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewed_at: str = Field(min_length=1)


class SemanticArtifactCommitBundle(BaseModel):
    """表示一次原子插入到 M11 的完整提交负载。

    :return: 无返回值；artifact、event、索引和幂等请求同事务写入。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    request_key: str = Field(min_length=1, max_length=240)
    request_kind: SemanticArtifactCommitRequestKind
    artifact: SemanticArtifactVersionRecord
    gate_events: tuple[SemanticArtifactGateEventRecord, ...] = Field(min_length=1)
    stale_markers: tuple[SemanticArtifactStaleMarkerRecord, ...] = Field(default=())
    claim_review_index: tuple[SemanticClaimReviewIndexRecord, ...] = Field(default=())
    clarification_gap_index: tuple[SemanticClarificationGapIndexRecord, ...] = Field(
        default=(),
    )


class SemanticArtifactRepository(Protocol):
    """表示 M11 权威 artifact 仓储端口。

    :return: 无返回值；业务层不得绕过该端口访问数据库模型。
    """

    def get_artifact(
        self,
        artifact_reference: str,
    ) -> SemanticArtifactVersionRecord | None:
        """按权威引用读取 artifact version。

        :param artifact_reference: M11 稳定 artifact 引用。
        :return: 找到时返回不可变 version record。
        """

    def get_artifact_by_review(
        self,
        source_proposal_digest: str,
        review_bundle_digest: str,
    ) -> SemanticArtifactVersionRecord | None:
        """按来源与审查摘要读取 review artifact version。

        :param source_proposal_digest: Claim Inventory proposal 摘要。
        :param review_bundle_digest: M08 Review Bundle 摘要。
        :return: 找到时返回对应 version record。
        """

    def get_latest_artifact(
        self,
        run_id: str,
        source_task_id: str,
        turn_snapshot_digest: str,
    ) -> SemanticArtifactVersionRecord | None:
        """读取回合当前最新 artifact version。

        :param run_id: 语义 DAG workflow 标识。
        :param source_task_id: Claim Inventory 来源任务标识。
        :param turn_snapshot_digest: 当前回合上下文摘要。
        :return: 找到时返回最新 version record。
        """

    def get_commit_request(
        self,
        request_key: str,
    ) -> SemanticArtifactCommitRequestRecord | None:
        """读取幂等提交请求结果。

        :param request_key: 稳定请求键。
        :return: 找到时返回既有提交结果。
        """

    def get_current_gate(
        self,
        artifact_reference: str,
    ) -> SemanticArtifactGateEventRecord | None:
        """读取 artifact 当前 gate 事件。

        :param artifact_reference: M11 稳定 artifact 引用。
        :return: 找到时返回最大 sequence 的 gate event。
        """

    def is_artifact_stale(
        self,
        artifact_reference: str,
    ) -> bool:
        """判断 artifact version 是否已被新版本取代。

        :param artifact_reference: M11 稳定 artifact 引用。
        :return: 存在 stale marker 时返回 True。
        """

    def commit(
        self,
        bundle: SemanticArtifactCommitBundle,
    ) -> SemanticArtifactCommitRequestRecord:
        """原子提交 artifact version 及其派生记录。

        :param bundle: 完整 M11 append-only 提交负载。
        :return: 返回新建或既有幂等提交结果。
        """

    def append_gate_event(
        self,
        event: SemanticArtifactGateEventRecord,
        *,
        request_key: str,
        payload_digest: str,
    ) -> SemanticArtifactCommitRequestRecord:
        """向既有 artifact version 追加 gate event。

        :param event: 待追加的 append-only gate event。
        :param request_key: gate event 幂等键。
        :param payload_digest: 结构化决策 digest。
        :return: 返回新建或既有幂等提交结果。
        """


def canonical_artifact_json(value: object) -> str:
    """生成 M11 契约对象的 canonical JSON。

    :param value: Pydantic 契约对象或可序列化字典。
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


def compute_artifact_payload_digest(
    payload: SemanticTurnArtifactPayload,
) -> str:
    """计算回合级 artifact payload 的 canonical 摘要。

    :param payload: 不可变 SemanticTurnArtifact payload。
    :return: 返回用于 corruption 校验的 SHA-256 digest。
    """
    return sha256(canonical_artifact_json(payload).encode("utf-8")).hexdigest()


def compute_semantic_artifact_id(
    *,
    run_id: str,
    source_task_id: str,
    turn_snapshot_digest: str,
) -> str:
    """计算同一回合语义结果链的稳定 artifact id。

    :param run_id: 语义 DAG workflow 标识。
    :param source_task_id: Claim Inventory 来源任务标识。
    :param turn_snapshot_digest: 当前回合上下文摘要。
    :return: 返回 64 位十六进制稳定链身份。
    """
    identity = f"{run_id}\0{source_task_id}\0{turn_snapshot_digest}"
    return sha256(identity.encode("utf-8")).hexdigest()


def build_artifact_reference(
    *,
    artifact_id: str,
    version: int,
) -> str:
    """构造 M11 稳定 artifact 引用。

    :param artifact_id: 回合语义结果链稳定身份。
    :param version: 不可变版本号。
    :return: 返回 semantic-artifact://<id>/<version> 引用。
    """
    return f"semantic-artifact://{artifact_id}/{version}"


def parse_artifact_reference(
    artifact_reference: str,
) -> tuple[str, int]:
    """解析 M11 artifact 引用中的链身份与版本。

    :param artifact_reference: M11 稳定 artifact 引用。
    :return: 返回 artifact id 与 version 元组。
    :raises ValueError: 引用格式或版本非法时抛出。
    """
    prefix = "semantic-artifact://"
    if not artifact_reference.startswith(prefix):
        raise ValueError("semantic artifact reference prefix is invalid")
    identity = artifact_reference.removeprefix(prefix)
    artifact_id, separator, version_text = identity.rpartition("/")
    if (
        not separator
        or len(artifact_id) != 64
        or any(character not in "0123456789abcdef" for character in artifact_id)
        or not version_text.isdecimal()
    ):
        raise ValueError("semantic artifact reference identity is invalid")
    version = int(version_text)
    if version < 1:
        raise ValueError("semantic artifact version is invalid")
    return artifact_id, version


def semantic_review_outcome_to_gate(
    outcome: SemanticReviewOutcome,
) -> SemanticArtifactGate:
    """将 M08 聚合结果映射为 M11 gate 状态。

    :param outcome: M08 deterministic aggregate outcome。
    :return: 返回对应的回合级 gate 状态。
    :raises ValueError: M08 结果没有稳定 M11 映射时抛出。
    """
    mapping = {
        SemanticReviewOutcome.SEMANTIC_REVIEW_SUPPORTED: (
            SemanticArtifactGate.SEMANTIC_REVIEW_SUPPORTED
        ),
        SemanticReviewOutcome.REPAIR_REQUIRED: SemanticArtifactGate.REPAIR_REQUIRED,
        SemanticReviewOutcome.CLARIFICATION_REQUIRED: (
            SemanticArtifactGate.CLARIFICATION_REQUIRED
        ),
        SemanticReviewOutcome.REPAIR_THEN_CLARIFICATION_REQUIRED: (
            SemanticArtifactGate.REPAIR_THEN_CLARIFICATION_REQUIRED
        ),
        SemanticReviewOutcome.HUMAN_REVIEW_REQUIRED: (
            SemanticArtifactGate.HUMAN_REVIEW_REQUIRED
        ),
        SemanticReviewOutcome.DISAGREEMENT: SemanticArtifactGate.DISAGREEMENT,
        SemanticReviewOutcome.REVIEW_FAILED: SemanticArtifactGate.REVIEW_FAILED,
    }
    gate = mapping.get(outcome)
    if gate is None:
        raise ValueError("semantic review outcome cannot map to artifact gate")
    return gate


__all__ = [
    "SemanticArtifactActorType",
    "SemanticArtifactClaimRecord",
    "SemanticArtifactCommitBundle",
    "SemanticArtifactCommitRequestKind",
    "SemanticArtifactCommitRequestRecord",
    "SemanticArtifactGate",
    "SemanticArtifactGateEventRecord",
    "SemanticArtifactKind",
    "SemanticArtifactRepository",
    "SemanticArtifactStaleMarkerRecord",
    "SemanticArtifactStaleReason",
    "SemanticArtifactVersionRecord",
    "SemanticClaimReviewIndexRecord",
    "SemanticClarificationGapIndexRecord",
    "SemanticHumanReviewStatus",
    "SemanticSystemReviewRecord",
    "SemanticTurnArtifactPayload",
    "build_artifact_reference",
    "canonical_artifact_json",
    "compute_artifact_payload_digest",
    "compute_semantic_artifact_id",
    "parse_artifact_reference",
    "semantic_review_outcome_to_gate",
]

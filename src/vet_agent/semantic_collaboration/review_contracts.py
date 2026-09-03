"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/review_contracts.py
作用：定义受限语义协作 DAG M08 的权威 Review 契约。
范围：覆盖 Coverage / Faithfulness 固定布尔矩阵、审查输入身份、验证结果、
      deterministic outcome、clarification gap、审查聚合与 M11 artifact 端口。
说明：本文件只承载契约和确定性身份计算，不调用模型、不修复模型输出、
      不提交数据库 artifact、不做医学判断，也不生成用户追问。
=============================================================================
"""

from __future__ import annotations

from enum import StrEnum
from hashlib import sha256
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import SkillFailureCode, SkillSpec
from .gateway_contracts import (
    SemanticModelProposal,
    StructuredLLMCallMetadata,
)
from .plan_contracts import PlanTask, PlanTaskSelectionSource, SchemaContractReference
from .scheduler_contracts import SemanticTaskExecutionRequest


class ClaimCoverageReviewDimension(StrEnum):
    """表示 Coverage Review 的固定审查维度。

    :return: 无返回值；枚举值与模型中文布尔矩阵字段一一对应。
    """

    MISSING_EXPLICIT_FACT = "存在漏抽显式事实"
    MULTIPLE_FACTS_MERGED = "存在多事实合并"
    DUPLICATE_CLAIM = "存在重复claim"
    UNSUPPORTED_CLAIM = "存在原文不支持的claim"
    NON_SELF_CONTAINED_PROPOSITION = "存在非自包含proposition"
    SHARED_SCOPE_SPLIT_ERROR = "存在shared scope拆分错误"
    UNCLASSIFIED_ISSUE = "未分类覆盖问题"


class ClaimFaithfulnessReviewDimension(StrEnum):
    """表示 Faithfulness Review 的固定语义漂移维度。

    :return: 无返回值；枚举值与模型中文布尔矩阵字段一一对应。
    """

    SUBJECT_OR_REFERENCE_CHANGED = "主体或指代范围改变"
    NEGATION_DIRECTION_CHANGED = "否定方向改变"
    NEGATION_SCOPE_CHANGED = "否定范围改变"
    NORMAL_WRITTEN_AS_DENIED = "正常状态误写为否认"
    FACT_TYPE_CHANGED = "事实类型改变"
    TEMPORAL_SCOPE_CHANGED = "时间范围改变"
    FREQUENCY_OR_QUANTITY_CHANGED = "频率或数量改变"
    DEGREE_OR_INTENSITY_CHANGED = "程度或强度改变"
    CERTAINTY_CHANGED = "确定性改变"
    CAUSALITY_CHANGED = "因果关系改变"
    MEDICAL_INFERENCE_ADDED = "医学推断或建议添加"
    PROPOSITION_NOT_SELF_CONTAINED = "命题不自包含"
    SUBJECT_REFERENCE_UNKNOWN = "指代对象不明"
    TEMPORAL_BASIS_UNKNOWN = "时间基准不明"
    NEGATION_SCOPE_UNKNOWN = "否定范围不明"
    COMPARISON_BASELINE_UNKNOWN = "比较基线不明"
    UNCLASSIFIED_SEMANTIC_CHANGE = "未分类语义改变"


class SemanticReviewOutcome(StrEnum):
    """表示 M08 布尔矩阵经过确定性派生后的业务路由状态。

    :return: 无返回值；该状态不是 artifact verified 状态。
    """

    SEMANTIC_REVIEW_SUPPORTED = "semantic_review_supported"
    REPAIR_REQUIRED = "repair_required"
    CLARIFICATION_REQUIRED = "clarification_required"
    REPAIR_THEN_CLARIFICATION_REQUIRED = "repair_then_clarification_required"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    DISAGREEMENT = "disagreement"
    REVIEW_FAILED = "review_failed"


class ReviewVerificationState(StrEnum):
    """表示单个 Review 模型 proposal 的结构验证结果。

    :return: 无返回值；blocked 结果不得被当作原生成任务通过。
    """

    ACCEPTED = "accepted"
    BLOCKED = "blocked"


class ClaimFaithfulnessExecutionState(StrEnum):
    """表示单条 claim 的 Faithfulness Review 执行状态。

    :return: 无返回值；skipped 必须携带稳定原因，不得静默省略。
    """

    COMPLETED = "completed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class ClaimFaithfulnessSkipReason(StrEnum):
    """表示未执行单条 Faithfulness Review 的确定性原因。

    :return: 无返回值；该枚举防止审查缺失被伪装成通过。
    """

    EMPTY_CLAIM_INVENTORY = "empty_claim_inventory"
    COVERAGE_REVIEW_FAILED = "coverage_review_failed"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    INVENTORY_REPAIR_REQUIRED = "inventory_repair_required"


class ClarificationBindingType(StrEnum):
    """表示来源绑定缺失需要消解的语义类型。

    :return: 无返回值；该类型不是自动追问文案。
    """

    SUBJECT_REFERENCE = "subject_reference"
    TEMPORAL_BASIS = "temporal_basis"
    NEGATION_SCOPE = "negation_scope"
    COMPARISON_BASELINE = "comparison_baseline"


class ClaimCoverageReviewMatrix(BaseModel):
    """表示 Coverage Review 的固定中文布尔矩阵。

    :return: 无返回值；矩阵不包含 verdict、reason 或修正值。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
        populate_by_name=True,
    )

    missing_explicit_fact: bool = Field(alias="存在漏抽显式事实")
    multiple_facts_merged: bool = Field(alias="存在多事实合并")
    duplicate_claim: bool = Field(alias="存在重复claim")
    unsupported_claim: bool = Field(alias="存在原文不支持的claim")
    non_self_contained_proposition: bool = Field(
        alias="存在非自包含proposition",
    )
    shared_scope_split_error: bool = Field(
        alias="存在shared scope拆分错误",
    )
    unclassified_issue: bool = Field(alias="未分类覆盖问题")

    def true_dimensions(self) -> tuple[ClaimCoverageReviewDimension, ...]:
        """读取当前矩阵中为 true 的覆盖审查维度。

        :return: 返回按契约字段顺序排列的 true 维度元组。
        """
        values = (
            self.missing_explicit_fact,
            self.multiple_facts_merged,
            self.duplicate_claim,
            self.unsupported_claim,
            self.non_self_contained_proposition,
            self.shared_scope_split_error,
            self.unclassified_issue,
        )
        return tuple(
            dimension
            for dimension, value in zip(
                (
                    ClaimCoverageReviewDimension.MISSING_EXPLICIT_FACT,
                    ClaimCoverageReviewDimension.MULTIPLE_FACTS_MERGED,
                    ClaimCoverageReviewDimension.DUPLICATE_CLAIM,
                    ClaimCoverageReviewDimension.UNSUPPORTED_CLAIM,
                    ClaimCoverageReviewDimension.NON_SELF_CONTAINED_PROPOSITION,
                    ClaimCoverageReviewDimension.SHARED_SCOPE_SPLIT_ERROR,
                    ClaimCoverageReviewDimension.UNCLASSIFIED_ISSUE,
                ),
                values,
                strict=True,
            )
            if value
        )


class ClaimCoverageReviewOutput(BaseModel):
    """表示 Coverage Review 模型的权威输出形态。

    :return: 无返回值；missing hint 只是 M09 线索，不是权威 claim。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
        populate_by_name=True,
    )

    coverage_matrix: ClaimCoverageReviewMatrix
    missing_claim_candidates: tuple[str, ...] = Field(
        default=(),
        max_length=8,
        description="有界自然语言补抽提示，仅供 M09 或人工审查消费。",
    )

    @field_validator("missing_claim_candidates", mode="before")
    @classmethod
    def coerce_missing_claim_candidates(
        cls,
        value: object,
    ) -> object:
        """将 JSON 数组规整为元组后再进入严格校验。

        :param value: 模型返回的 missing candidate JSON 值。
        :return: 返回可由严格 tuple 字段消费的序列值。
        """
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("missing_claim_candidates")
    @classmethod
    def validate_missing_claim_candidates(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        """校验补抽提示是非空、单行且不重复的自然语言。

        :param value: 模型返回的 missing candidate 集合。
        :return: 返回通过确定性形态校验的候选提示。
        :raises ValueError: 候选为空、多行、重复或携带换行时抛出。
        """
        if any(
            not candidate.strip()
            or candidate != candidate.strip()
            or "\n" in candidate
            or "\r" in candidate
            for candidate in value
        ):
            raise ValueError("missing claim candidate shape is invalid")
        if len(set(value)) != len(value):
            raise ValueError("missing claim candidate is duplicate")
        return value


class ClaimFaithfulnessReviewMatrix(BaseModel):
    """表示 Faithfulness Review 的固定中文布尔矩阵。

    :return: 无返回值；矩阵不包含 corrected proposition 或 evidence。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
        populate_by_name=True,
    )

    subject_or_reference_changed: bool = Field(
        alias="主体或指代范围改变",
    )
    negation_direction_changed: bool = Field(
        alias="否定方向改变",
    )
    negation_scope_changed: bool = Field(
        alias="否定范围改变",
    )
    normal_written_as_denied: bool = Field(
        alias="正常状态误写为否认",
    )
    fact_type_changed: bool = Field(
        alias="事实类型改变",
    )
    temporal_scope_changed: bool = Field(
        alias="时间范围改变",
    )
    frequency_or_quantity_changed: bool = Field(
        alias="频率或数量改变",
    )
    degree_or_intensity_changed: bool = Field(
        alias="程度或强度改变",
    )
    certainty_changed: bool = Field(
        alias="确定性改变",
    )
    causality_changed: bool = Field(
        alias="因果关系改变",
    )
    medical_inference_added: bool = Field(
        alias="医学推断或建议添加",
    )
    proposition_not_self_contained: bool = Field(
        alias="命题不自包含",
    )
    subject_reference_unknown: bool = Field(
        alias="指代对象不明",
    )
    temporal_basis_unknown: bool = Field(
        alias="时间基准不明",
    )
    negation_scope_unknown: bool = Field(
        alias="否定范围不明",
    )
    comparison_baseline_unknown: bool = Field(
        alias="比较基线不明",
    )
    unclassified_semantic_change: bool = Field(
        alias="未分类语义改变",
    )

    def true_dimensions(self) -> tuple[ClaimFaithfulnessReviewDimension, ...]:
        """读取当前矩阵中为 true 的语义漂移维度。

        :return: 返回按契约字段顺序排列的 true 维度元组。
        """
        values = (
            self.subject_or_reference_changed,
            self.negation_direction_changed,
            self.negation_scope_changed,
            self.normal_written_as_denied,
            self.fact_type_changed,
            self.temporal_scope_changed,
            self.frequency_or_quantity_changed,
            self.degree_or_intensity_changed,
            self.certainty_changed,
            self.causality_changed,
            self.medical_inference_added,
            self.proposition_not_self_contained,
            self.subject_reference_unknown,
            self.temporal_basis_unknown,
            self.negation_scope_unknown,
            self.comparison_baseline_unknown,
            self.unclassified_semantic_change,
        )
        dimensions = (
            ClaimFaithfulnessReviewDimension.SUBJECT_OR_REFERENCE_CHANGED,
            ClaimFaithfulnessReviewDimension.NEGATION_DIRECTION_CHANGED,
            ClaimFaithfulnessReviewDimension.NEGATION_SCOPE_CHANGED,
            ClaimFaithfulnessReviewDimension.NORMAL_WRITTEN_AS_DENIED,
            ClaimFaithfulnessReviewDimension.FACT_TYPE_CHANGED,
            ClaimFaithfulnessReviewDimension.TEMPORAL_SCOPE_CHANGED,
            ClaimFaithfulnessReviewDimension.FREQUENCY_OR_QUANTITY_CHANGED,
            ClaimFaithfulnessReviewDimension.DEGREE_OR_INTENSITY_CHANGED,
            ClaimFaithfulnessReviewDimension.CERTAINTY_CHANGED,
            ClaimFaithfulnessReviewDimension.CAUSALITY_CHANGED,
            ClaimFaithfulnessReviewDimension.MEDICAL_INFERENCE_ADDED,
            ClaimFaithfulnessReviewDimension.PROPOSITION_NOT_SELF_CONTAINED,
            ClaimFaithfulnessReviewDimension.SUBJECT_REFERENCE_UNKNOWN,
            ClaimFaithfulnessReviewDimension.TEMPORAL_BASIS_UNKNOWN,
            ClaimFaithfulnessReviewDimension.NEGATION_SCOPE_UNKNOWN,
            ClaimFaithfulnessReviewDimension.COMPARISON_BASELINE_UNKNOWN,
            ClaimFaithfulnessReviewDimension.UNCLASSIFIED_SEMANTIC_CHANGE,
        )
        return tuple(
            dimension
            for dimension, value in zip(dimensions, values, strict=True)
            if value
        )


class ClaimFaithfulnessReviewOutput(BaseModel):
    """表示 Faithfulness Review 模型的权威输出形态。

    :return: 无返回值；输出只包含单条 claim 的固定布尔矩阵。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
        populate_by_name=True,
    )

    faithfulness_matrix: ClaimFaithfulnessReviewMatrix


class ClaimCoverageReviewVerificationResult(BaseModel):
    """表示 Coverage Review proposal 的 M08 结构验证结果。

    :return: 无返回值；blocked 不使原 Claim Inventory 通过。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    review_task_id: str
    state: ReviewVerificationState
    output: ClaimCoverageReviewOutput | None = None
    failure_code: SkillFailureCode | None = None
    failure_message: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_result_payload(self) -> Self:
        """校验 Coverage Review 验证结果与负载闭合。

        :return: 返回通过显式终态契约校验的验证结果。
        :raises ValueError: accepted 缺输出或 blocked 带输出时抛出。
        """
        if self.state is ReviewVerificationState.ACCEPTED:
            if self.output is None or self.failure_code is not None:
                raise ValueError("accepted coverage review result is invalid")
            return self
        if self.output is not None or self.failure_code is None:
            raise ValueError("blocked coverage review result is invalid")
        return self


class ClaimFaithfulnessReviewVerificationResult(BaseModel):
    """表示单条 Faithfulness Review proposal 的结构验证结果。

    :return: 无返回值；blocked 不使原 claim 通过。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    review_task_id: str
    claim_index: int = Field(ge=0)
    claim_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: ReviewVerificationState
    output: ClaimFaithfulnessReviewOutput | None = None
    failure_code: SkillFailureCode | None = None
    failure_message: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_result_payload(self) -> Self:
        """校验 Faithfulness Review 验证结果与负载闭合。

        :return: 返回通过显式终态契约校验的验证结果。
        :raises ValueError: accepted 缺输出或 blocked 带输出时抛出。
        """
        if self.state is ReviewVerificationState.ACCEPTED:
            if self.output is None or self.failure_code is not None:
                raise ValueError("accepted faithfulness review result is invalid")
            return self
        if self.output is not None or self.failure_code is None:
            raise ValueError("blocked faithfulness review result is invalid")
        return self


class ClaimCoverageReviewDerivedResult(BaseModel):
    """表示 Coverage 布尔矩阵的确定性业务派生结果。

    :return: 无返回值；该结果不直接修改 claims。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    outcome: SemanticReviewOutcome
    true_dimensions: tuple[ClaimCoverageReviewDimension, ...]
    missing_claim_candidates: tuple[str, ...] = Field(max_length=8)
    suspicious_empty: bool
    no_explicit_fact: bool


class ClaimFaithfulnessReviewDerivedResult(BaseModel):
    """表示 Faithfulness 布尔矩阵的确定性业务派生结果。

    :return: 无返回值；repair 与 clarification 维度显式分离。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    outcome: SemanticReviewOutcome
    true_dimensions: tuple[ClaimFaithfulnessReviewDimension, ...]
    repair_dimensions: tuple[ClaimFaithfulnessReviewDimension, ...]
    clarification_dimensions: tuple[ClaimFaithfulnessReviewDimension, ...]


class ClaimCoverageReviewRecord(BaseModel):
    """表示一次 Coverage Review 的完整可审计记录。

    :return: 无返回值；该记录不是 M11 append-only artifact 权威存储。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    review_task_id: str
    skill_id: str
    skill_version: str
    turn_snapshot_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_proposal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    verification: ClaimCoverageReviewVerificationResult
    derived: ClaimCoverageReviewDerivedResult | None = None
    metadata: StructuredLLMCallMetadata


class ClaimFaithfulnessReviewRecord(BaseModel):
    """表示单条 claim 的 Faithfulness Review 可审计记录。

    :return: 无返回值；skipped 记录必须保留稳定原因。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    review_task_id: str
    skill_id: str
    skill_version: str
    turn_snapshot_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_proposal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    claim_index: int = Field(ge=0)
    claim_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    claim_proposition: str = Field(min_length=1, max_length=240)
    execution_state: ClaimFaithfulnessExecutionState
    skip_reason: ClaimFaithfulnessSkipReason | None = None
    verification: ClaimFaithfulnessReviewVerificationResult | None = None
    derived: ClaimFaithfulnessReviewDerivedResult | None = None
    metadata: StructuredLLMCallMetadata | None = None

    @model_validator(mode="after")
    def validate_record_payload(self) -> Self:
        """校验单条 claim 审查记录的执行状态与负载闭合。

        :return: 返回通过状态机校验的 Faithfulness Review 记录。
        :raises ValueError: skipped、completed 或 blocked 负载不匹配时抛出。
        """
        if self.execution_state is ClaimFaithfulnessExecutionState.COMPLETED:
            if (
                self.skip_reason is not None
                or self.verification is None
                or self.derived is None
                or self.metadata is None
            ):
                raise ValueError("completed faithfulness review record is invalid")
            return self
        if self.execution_state is ClaimFaithfulnessExecutionState.SKIPPED:
            if (
                self.skip_reason is None
                or self.verification is not None
                or self.derived is not None
                or self.metadata is not None
            ):
                raise ValueError("skipped faithfulness review record is invalid")
            return self
        if self.skip_reason is not None or self.verification is None:
            raise ValueError("blocked faithfulness review record is invalid")
        return self


class ClarificationGapProposal(BaseModel):
    """表示一个显式来源绑定缺失 gap。

    :return: 无返回值；该对象不是 verified、failure 或用户追问指令。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    gap_id: str = Field(min_length=1, max_length=240)
    run_id: str = Field(min_length=1, max_length=64)
    source_task_id: str = Field(min_length=1, max_length=360)
    turn_snapshot_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_proposal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    claim_index: int = Field(ge=0)
    claim_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    claim_proposition: str = Field(min_length=1, max_length=240)
    ambiguous_dimension: ClaimFaithfulnessReviewDimension
    required_binding_type: ClarificationBindingType
    model_overreach_repaired: bool


class SemanticReviewBundle(BaseModel):
    """表示一次 Claim Inventory 的完整 M08 审查聚合结果。

    :return: 无返回值；semantic_review_supported 不等于 verified。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    run_id: str = Field(min_length=1, max_length=64)
    source_task_id: str = Field(min_length=1, max_length=360)
    source_attempt_number: int = Field(ge=1)
    turn_snapshot_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_proposal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    claims: tuple[str, ...] = Field(max_length=8)
    coverage_review: ClaimCoverageReviewRecord
    faithfulness_reviews: tuple[ClaimFaithfulnessReviewRecord, ...]
    clarification_gaps: tuple[ClarificationGapProposal, ...]
    aggregate_outcome: SemanticReviewOutcome

    @model_validator(mode="after")
    def validate_bundle_shape(self) -> Self:
        """校验聚合记录与 claim 集合一一对应。

        :return: 返回通过闭合校验的 M08 审查聚合。
        :raises ValueError: claim 索引缺失、重复或身份不一致时抛出。
        """
        indexes = tuple(record.claim_index for record in self.faithfulness_reviews)
        expected = tuple(range(len(self.claims)))
        if indexes != expected:
            raise ValueError("faithfulness review claim indexes are not closed")
        for record in self.faithfulness_reviews:
            if record.claim_proposition != self.claims[record.claim_index]:
                raise ValueError("faithfulness review claim proposition mismatch")
        return self


class ReviewArtifactStore(Protocol):
    """表示 M11 为 M08 审查结果提供 append-only 提交的端口。

    :return: 无返回值；M08 不实现数据库 artifact 存储细节。
    """

    async def commit_review_bundle(
        self,
        bundle: SemanticReviewBundle,
    ) -> str:
        """以幂等方式提交一个不可变审查聚合 artifact。

        :param bundle: 已完成 M08 确定性验证与结果派生的聚合记录。
        :return: 返回 M11 权威 artifact 引用。
        """


class TODOReviewArtifactStore:
    """表示 M11 Artifact Store 未接入前的显式空壳。

    :return: 无返回值；该占位始终 Fail Fast，不伪造 artifact 引用。
    """

    async def commit_review_bundle(
        self,
        bundle: SemanticReviewBundle,
    ) -> str:
        """阻断尚未实现的 M08 artifact 权威提交。

        :param bundle: 当前 M08 审查聚合记录。
        :raises NotImplementedError: M11 未实现时始终抛出。
        :return: 无返回值。
        """
        raise NotImplementedError("M11 review artifact store is not implemented")


def compute_claim_digest(claim_proposition: str) -> str:
    """计算单条 claim proposition 的 canonical SHA-256 身份。

    :param claim_proposition: 系统 append 的自包含自然语言 proposition。
    :return: 返回用于审查身份与后续 stale 治理的稳定摘要。
    """
    return sha256(claim_proposition.encode("utf-8")).hexdigest()


def compute_review_task_hash(
    source_task_id: str,
    claim_index: int | None,
    claim_digest: str | None,
) -> str:
    """计算动态 Review 任务的确定性身份摘要。

    :param source_task_id: 被审查的 Claim Inventory 权威任务标识。
    :param claim_index: Faithfulness Review 的 claim 序号；Coverage 为空。
    :param claim_digest: Faithfulness Review 的 claim 摘要；Coverage 为空。
    :return: 返回 16 位十六进制稳定身份片段。
    """
    identity = "\0".join(
        (
            source_task_id,
            "" if claim_index is None else str(claim_index),
            claim_digest or "",
        ),
    )
    return sha256(identity.encode("utf-8")).hexdigest()[:16]


def build_claim_coverage_review_execution(
    source: SemanticModelProposal,
    spec: SkillSpec,
) -> SemanticTaskExecutionRequest:
    """构造 Coverage Review 的确定性动态任务执行请求。

    :param source: M07 accepted 的 Claim Inventory 模型 proposal。
    :param spec: SkillCatalog 中的 Coverage Review 权威契约。
    :return: 返回绑定同一 TurnSnapshot digest 的执行请求。
    """
    task_hash = compute_review_task_hash(source.execution.task.task_id, None, None)
    task = PlanTask(
        task_id=(
            f"{source.execution.run_id}:review:{task_hash}:"
            f"{spec.skill_id}:{spec.skill_version}"
        ),
        skill_id=spec.skill_id,
        skill_version=spec.skill_version,
        target_envelope_id=f"review:{task_hash}:{spec.skill_id}",
        depends_on=(source.execution.task.task_id,),
        expected_output_schema=SchemaContractReference.from_contract(
            spec.output_contract,
        ),
        selection_source=PlanTaskSelectionSource.DETERMINISTIC_REVIEW_EXPANSION,
    )
    return SemanticTaskExecutionRequest(
        run_id=source.execution.run_id,
        attempt_number=1,
        task=task,
        turn_snapshot_digest=source.execution.turn_snapshot_digest,
        dependency_artifacts={},
    )


def build_claim_faithfulness_review_execution(
    source: SemanticModelProposal,
    spec: SkillSpec,
    *,
    claim_index: int,
    claim_proposition: str,
) -> SemanticTaskExecutionRequest:
    """构造单条 claim Faithfulness Review 的动态执行请求。

    :param source: M07 accepted 的 Claim Inventory 模型 proposal。
    :param spec: SkillCatalog 中的 Faithfulness Review 权威契约。
    :param claim_index: claim 在 inventory 数组中的确定性序号。
    :param claim_proposition: 当前唯一待审查的自包含 proposition。
    :return: 返回绑定 claim digest 与同一 TurnSnapshot 的执行请求。
    """
    claim_digest = compute_claim_digest(claim_proposition)
    task_hash = compute_review_task_hash(
        source.execution.task.task_id,
        claim_index,
        claim_digest,
    )
    task = PlanTask(
        task_id=(
            f"{source.execution.run_id}:review:{task_hash}:"
            f"{spec.skill_id}:{spec.skill_version}"
        ),
        skill_id=spec.skill_id,
        skill_version=spec.skill_version,
        target_envelope_id=f"review:{task_hash}:{spec.skill_id}:claim-{claim_index}",
        depends_on=(source.execution.task.task_id,),
        expected_output_schema=SchemaContractReference.from_contract(
            spec.output_contract,
        ),
        selection_source=PlanTaskSelectionSource.DETERMINISTIC_REVIEW_EXPANSION,
    )
    return SemanticTaskExecutionRequest(
        run_id=source.execution.run_id,
        attempt_number=1,
        task=task,
        turn_snapshot_digest=source.execution.turn_snapshot_digest,
        dependency_artifacts={},
    )

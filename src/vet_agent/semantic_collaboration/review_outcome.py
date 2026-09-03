"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/review_outcome.py
作用：实现受限语义协作 DAG M08 的确定性结果派生与路由。
范围：覆盖 Coverage 布尔矩阵、Faithfulness 布尔矩阵、空 claim 语义、
      repair / clarification / human review 优先级、disagreement 与 gap 派生。
说明：本文件不让模型输出业务 verdict，不修复 claims，不生成追问文案，
      不调用 M09，也不把来源绑定缺失当作模型漂移补造事实。
=============================================================================
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .review_contracts import (
    ClaimCoverageReviewDerivedResult,
    ClaimCoverageReviewDimension,
    ClaimCoverageReviewOutput,
    ClaimFaithfulnessReviewDerivedResult,
    ClaimFaithfulnessReviewDimension,
    ClaimFaithfulnessReviewMatrix,
    ClaimFaithfulnessReviewRecord,
    ClarificationBindingType,
    ClarificationGapProposal,
    SemanticReviewBundle,
    SemanticReviewOutcome,
    compute_claim_digest,
)


class SemanticReviewDerivationPolicy(BaseModel):
    """表示 M08 布尔矩阵派生业务路由的固定预算策略。

    :return: 无返回值；超过预算进入人工审查，不允许全局自由重写。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    max_repair_dimensions: int = Field(
        default=2,
        ge=1,
        le=4,
        description="单个 Review 结果允许自动路由到局部修复的 true 维度上限。",
    )

    @model_validator(mode="after")
    def validate_policy_bounds(self) -> SemanticReviewDerivationPolicy:
        """校验修复预算不超过已知修复治理边界。

        :return: 返回可用于确定性派生的预算策略。
        :raises ValueError: 预算超过 M10 局部 patch 治理能力时抛出。
        """
        if self.max_repair_dimensions > 4:
            raise ValueError("semantic review repair budget exceeds policy bound")
        return self


class ReviewOutcomeDeriver:
    """表示 M08 固定布尔矩阵的确定性业务结论派生器。

    :return: 无返回值；派生器不读取原文，不重新判断模型语义。
    """

    def __init__(
        self,
        policy: SemanticReviewDerivationPolicy | None = None,
    ) -> None:
        """初始化 M08 deterministic outcome 派生器。

        :param policy: 可选修复维度预算；为空时使用生产默认策略。
        :return: 无返回值。
        """
        self.policy = policy or SemanticReviewDerivationPolicy()

    def derive_coverage(
        self,
        output: ClaimCoverageReviewOutput,
        *,
        claim_count: int,
    ) -> ClaimCoverageReviewDerivedResult:
        """从 Coverage 矩阵派生回合级覆盖路由结果。

        :param output: 已通过 M08 结构验证的 Coverage 输出。
        :param claim_count: M07 确定性派生的 claim 数量。
        :return: 返回覆盖审查支持的、修复的或人工审查的路由结果。
        """
        true_dimensions = output.coverage_matrix.true_dimensions()
        if ClaimCoverageReviewDimension.UNCLASSIFIED_ISSUE in true_dimensions:
            return self._coverage_result(
                outcome=SemanticReviewOutcome.HUMAN_REVIEW_REQUIRED,
                output=output,
                true_dimensions=true_dimensions,
                claim_count=claim_count,
            )
        if (
            output.missing_claim_candidates
            and not output.coverage_matrix.missing_explicit_fact
        ):
            return self._coverage_result(
                outcome=SemanticReviewOutcome.DISAGREEMENT,
                output=output,
                true_dimensions=true_dimensions,
                claim_count=claim_count,
            )
        repair_dimensions = tuple(
            dimension
            for dimension in true_dimensions
            if dimension is not ClaimCoverageReviewDimension.UNCLASSIFIED_ISSUE
        )
        if len(repair_dimensions) > self.policy.max_repair_dimensions:
            return self._coverage_result(
                outcome=SemanticReviewOutcome.HUMAN_REVIEW_REQUIRED,
                output=output,
                true_dimensions=true_dimensions,
                claim_count=claim_count,
            )
        if not true_dimensions:
            return ClaimCoverageReviewDerivedResult(
                outcome=SemanticReviewOutcome.SEMANTIC_REVIEW_SUPPORTED,
                true_dimensions=(),
                missing_claim_candidates=output.missing_claim_candidates,
                suspicious_empty=False,
                no_explicit_fact=claim_count == 0,
            )
        return self._coverage_result(
            outcome=SemanticReviewOutcome.REPAIR_REQUIRED,
            output=output,
            true_dimensions=true_dimensions,
            claim_count=claim_count,
        )

    def derive_faithfulness(
        self,
        matrix: ClaimFaithfulnessReviewRecord,
    ) -> ClaimFaithfulnessReviewDerivedResult:
        """从单条 Faithfulness 矩阵派生 claim 级路由结果。

        :param matrix: 已通过 M08 结构验证的单 claim 审查记录。
        :return: 返回 supported、repair、clarification 或组合路由结果。
        """
        if matrix.verification is None or matrix.verification.output is None:
            raise ValueError("faithfulness review output is required for derivation")
        return self.derive_faithfulness_matrix(
            matrix.verification.output.faithfulness_matrix,
        )

    def derive_faithfulness_matrix(
        self,
        matrix: ClaimFaithfulnessReviewMatrix,
    ) -> ClaimFaithfulnessReviewDerivedResult:
        """从固定 Faithfulness 矩阵直接派生 claim 级路由结果。

        :param matrix: 已通过 M08 结构验证的单 claim 布尔矩阵。
        :return: 返回 supported、repair、clarification 或组合路由结果。
        """
        true_dimensions = matrix.true_dimensions()
        if (
            ClaimFaithfulnessReviewDimension.UNCLASSIFIED_SEMANTIC_CHANGE
            in true_dimensions
        ):
            return ClaimFaithfulnessReviewDerivedResult(
                outcome=SemanticReviewOutcome.HUMAN_REVIEW_REQUIRED,
                true_dimensions=true_dimensions,
                repair_dimensions=(),
                clarification_dimensions=(),
            )
        binding_dimensions = _binding_dimensions(true_dimensions)
        repair_dimensions = tuple(
            dimension
            for dimension in true_dimensions
            if dimension not in binding_dimensions
        )
        if len(repair_dimensions) > self.policy.max_repair_dimensions:
            return ClaimFaithfulnessReviewDerivedResult(
                outcome=SemanticReviewOutcome.HUMAN_REVIEW_REQUIRED,
                true_dimensions=true_dimensions,
                repair_dimensions=(),
                clarification_dimensions=(),
            )
        if repair_dimensions and binding_dimensions:
            outcome = SemanticReviewOutcome.REPAIR_THEN_CLARIFICATION_REQUIRED
        elif repair_dimensions:
            outcome = SemanticReviewOutcome.REPAIR_REQUIRED
        elif binding_dimensions:
            outcome = SemanticReviewOutcome.CLARIFICATION_REQUIRED
        else:
            outcome = SemanticReviewOutcome.SEMANTIC_REVIEW_SUPPORTED
        return ClaimFaithfulnessReviewDerivedResult(
            outcome=outcome,
            true_dimensions=true_dimensions,
            repair_dimensions=repair_dimensions,
            clarification_dimensions=binding_dimensions,
        )

    def derive_bundle(
        self,
        bundle: SemanticReviewBundle,
    ) -> SemanticReviewBundle:
        """基于完整审查记录重新派生聚合状态和 clarification gaps。

        :param bundle: M08 Runner 生成的初步审查聚合。
        :return: 返回带确定性聚合路由与 gap 集合的不可变审查聚合。
        """
        coverage = bundle.coverage_review
        if coverage.verification.state.value == "blocked":
            return bundle.model_copy(
                update={
                    "aggregate_outcome": SemanticReviewOutcome.REVIEW_FAILED,
                    "clarification_gaps": (),
                },
            )
        if any(
            record.execution_state.value == "blocked"
            for record in bundle.faithfulness_reviews
        ):
            return bundle.model_copy(
                update={
                    "aggregate_outcome": SemanticReviewOutcome.REVIEW_FAILED,
                    "clarification_gaps": (),
                },
            )
        if (
            coverage.derived is not None
            and coverage.derived.outcome is SemanticReviewOutcome.HUMAN_REVIEW_REQUIRED
        ):
            return bundle.model_copy(
                update={
                    "aggregate_outcome": SemanticReviewOutcome.HUMAN_REVIEW_REQUIRED,
                    "clarification_gaps": (),
                },
            )
        derived_faithfulness = tuple(
            (
                record,
                self.derive_faithfulness(record),
            )
            for record in bundle.faithfulness_reviews
            if record.execution_state.value == "completed"
        )
        updated_records = tuple(
            record.model_copy(update={"derived": derived})
            for record, derived in derived_faithfulness
        )
        record_by_index = {record.claim_index: record for record in updated_records}
        completed_records = tuple(
            record_by_index.get(record.claim_index, record)
            for record in bundle.faithfulness_reviews
        )
        gaps = self._clarification_gaps(bundle, completed_records)
        coverage_derived = (
            None
            if coverage.verification.output is None
            else self.derive_coverage(
                coverage.verification.output,
                claim_count=len(bundle.claims),
            )
        )
        aggregate = self._aggregate_outcome(
            coverage_derived=coverage_derived,
            faithfulness_records=completed_records,
        )
        return bundle.model_copy(
            update={
                "coverage_review": coverage.model_copy(
                    update={
                        "derived": coverage_derived,
                    },
                ),
                "faithfulness_reviews": completed_records,
                "clarification_gaps": gaps,
                "aggregate_outcome": aggregate,
            },
        )

    def _coverage_result(
        self,
        *,
        outcome: SemanticReviewOutcome,
        output: ClaimCoverageReviewOutput,
        true_dimensions: tuple[ClaimCoverageReviewDimension, ...],
        claim_count: int,
    ) -> ClaimCoverageReviewDerivedResult:
        """构造 Coverage 矩阵的确定性派生结果。

        :param outcome: 当前确定性业务路由状态。
        :param output: Coverage Review 权威输出。
        :param true_dimensions: 当前矩阵中的 true 维度集合。
        :param claim_count: 当前 Claim Inventory 数量。
        :return: 返回覆盖审查派生结果。
        """
        return ClaimCoverageReviewDerivedResult(
            outcome=outcome,
            true_dimensions=true_dimensions,
            missing_claim_candidates=output.missing_claim_candidates,
            suspicious_empty=(
                claim_count == 0
                and ClaimCoverageReviewDimension.MISSING_EXPLICIT_FACT
                in true_dimensions
            ),
            no_explicit_fact=claim_count == 0 and not true_dimensions,
        )

    def _aggregate_outcome(
        self,
        *,
        coverage_derived: ClaimCoverageReviewDerivedResult | None,
        faithfulness_records: tuple[ClaimFaithfulnessReviewRecord, ...],
    ) -> SemanticReviewOutcome:
        """按稳定优先级聚合 Coverage 与 Faithfulness 派生结果。

        :param coverage_derived: Coverage Review 已派生业务结果。
        :param faithfulness_records: 全部单 claim 审查记录。
        :return: 返回当前 Claim Inventory 的聚合路由状态。
        """
        completed = tuple(
            record for record in faithfulness_records if record.derived is not None
        )
        if any(
            record.derived is not None
            and record.derived.outcome is SemanticReviewOutcome.HUMAN_REVIEW_REQUIRED
            for record in completed
        ):
            return SemanticReviewOutcome.HUMAN_REVIEW_REQUIRED
        if any(
            record.derived is not None
            and record.derived.outcome
            is SemanticReviewOutcome.REPAIR_THEN_CLARIFICATION_REQUIRED
            for record in completed
        ):
            return SemanticReviewOutcome.REPAIR_THEN_CLARIFICATION_REQUIRED
        if (
            coverage_derived is not None
            and coverage_derived.outcome is SemanticReviewOutcome.DISAGREEMENT
        ):
            return SemanticReviewOutcome.DISAGREEMENT
        has_claim_repair = any(
            record.derived is not None
            and record.derived.outcome is SemanticReviewOutcome.REPAIR_REQUIRED
            for record in completed
        )
        if has_claim_repair:
            return SemanticReviewOutcome.REPAIR_REQUIRED
        if (
            coverage_derived is not None
            and coverage_derived.outcome is SemanticReviewOutcome.REPAIR_REQUIRED
        ):
            claim_level_dimensions = {
                ClaimCoverageReviewDimension.UNSUPPORTED_CLAIM,
                ClaimCoverageReviewDimension.NON_SELF_CONTAINED_PROPOSITION,
            }
            coverage_requires_claim_review = any(
                dimension in claim_level_dimensions
                for dimension in coverage_derived.true_dimensions
            )
            if (
                coverage_requires_claim_review
                and completed
                and all(
                    record.derived is not None
                    and record.derived.outcome
                    is SemanticReviewOutcome.SEMANTIC_REVIEW_SUPPORTED
                    for record in completed
                )
            ):
                return SemanticReviewOutcome.DISAGREEMENT
            return SemanticReviewOutcome.REPAIR_REQUIRED
        if any(
            record.derived is not None
            and record.derived.outcome is SemanticReviewOutcome.CLARIFICATION_REQUIRED
            for record in completed
        ):
            return SemanticReviewOutcome.CLARIFICATION_REQUIRED
        return SemanticReviewOutcome.SEMANTIC_REVIEW_SUPPORTED

    def _clarification_gaps(
        self,
        bundle: SemanticReviewBundle,
        records: tuple[ClaimFaithfulnessReviewRecord, ...],
    ) -> tuple[ClarificationGapProposal, ...]:
        """从来源绑定缺失维度派生显式 clarification gap。

        :param bundle: 当前 M08 审查聚合。
        :param records: 已更新派生结果的单 claim 审查记录。
        :return: 返回不包含追问文案的结构化 gap 集合。
        """
        gaps: list[ClarificationGapProposal] = []
        for record in records:
            derived = record.derived
            if derived is None:
                continue
            for dimension in derived.clarification_dimensions:
                gaps.append(
                    ClarificationGapProposal(
                        gap_id=self._gap_id(
                            bundle.source_proposal_digest,
                            record.claim_digest,
                            dimension,
                        ),
                        run_id=bundle.run_id,
                        source_task_id=bundle.source_task_id,
                        turn_snapshot_digest=bundle.turn_snapshot_digest,
                        source_proposal_digest=bundle.source_proposal_digest,
                        claim_index=record.claim_index,
                        claim_digest=record.claim_digest,
                        claim_proposition=record.claim_proposition,
                        ambiguous_dimension=dimension,
                        required_binding_type=_binding_type(dimension),
                        model_overreach_repaired=False,
                    ),
                )
        return tuple(gaps)

    def _gap_id(
        self,
        source_proposal_digest: str,
        claim_digest: str,
        dimension: ClaimFaithfulnessReviewDimension,
    ) -> str:
        """计算 clarification gap 的稳定工程身份。

        :param source_proposal_digest: 被审查 Claim Inventory 的摘要。
        :param claim_digest: 当前 proposition 的摘要。
        :param dimension: 来源绑定缺失维度。
        :return: 返回不暴露用户原文的稳定 gap 标识。
        """
        return "::".join(
            (
                "semantic-clarification-gap",
                source_proposal_digest[:16],
                claim_digest[:16],
                dimension.value,
            ),
        )


def _binding_dimensions(
    dimensions: tuple[ClaimFaithfulnessReviewDimension, ...],
) -> tuple[ClaimFaithfulnessReviewDimension, ...]:
    """筛选来源绑定缺失类 true 维度。

    :param dimensions: Faithfulness 矩阵中的 true 维度集合。
    :return: 返回无法由 Repair 猜测补全的来源绑定维度。
    """
    binding_dimensions = {
        ClaimFaithfulnessReviewDimension.SUBJECT_REFERENCE_UNKNOWN,
        ClaimFaithfulnessReviewDimension.TEMPORAL_BASIS_UNKNOWN,
        ClaimFaithfulnessReviewDimension.NEGATION_SCOPE_UNKNOWN,
        ClaimFaithfulnessReviewDimension.COMPARISON_BASELINE_UNKNOWN,
    }
    return tuple(
        dimension for dimension in dimensions if dimension in binding_dimensions
    )


def _binding_type(
    dimension: ClaimFaithfulnessReviewDimension,
) -> ClarificationBindingType:
    """映射来源绑定缺失维度到 gap 消解类型。

    :param dimension: 当前来源绑定缺失维度。
    :return: 返回后续问诊领域可消费的结构化消解类型。
    :raises ValueError: 传入维度不属于来源绑定缺失集合时抛出。
    """
    mapping = {
        ClaimFaithfulnessReviewDimension.SUBJECT_REFERENCE_UNKNOWN: (
            ClarificationBindingType.SUBJECT_REFERENCE
        ),
        ClaimFaithfulnessReviewDimension.TEMPORAL_BASIS_UNKNOWN: (
            ClarificationBindingType.TEMPORAL_BASIS
        ),
        ClaimFaithfulnessReviewDimension.NEGATION_SCOPE_UNKNOWN: (
            ClarificationBindingType.NEGATION_SCOPE
        ),
        ClaimFaithfulnessReviewDimension.COMPARISON_BASELINE_UNKNOWN: (
            ClarificationBindingType.COMPARISON_BASELINE
        ),
    }
    binding_type = mapping.get(dimension)
    if binding_type is None:
        raise ValueError("faithfulness dimension does not require clarification")
    return binding_type


__all__ = [
    "ReviewOutcomeDeriver",
    "SemanticReviewDerivationPolicy",
    "compute_claim_digest",
]

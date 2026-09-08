"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/artifact_auto_finalization.py
作用：实现受限语义协作 DAG M11 的确定性 artifact 自动终态化策略。
范围：覆盖 M08 supported 结果到 verified / repair_verified 的结构闭合校验、
      空图策略、clarification gap 保守保留与系统决策摘要契约。
说明：本文件不调用 LLM、不读取用户原文、不做医学判断、不生成用户追问，
      也不把 clarification_required 伪装为 verified；结构不可闭合时返回
      None，由 M11 保留上游显式 gate 并由 M12 阻断消费。
=============================================================================
"""

from __future__ import annotations

from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .artifact_contracts import (
    SemanticArtifactGate,
    SemanticArtifactVersionRecord,
    compute_artifact_payload_digest,
)
from .repair_contracts import compute_review_bundle_digest
from .review_contracts import SemanticReviewBundle, SemanticReviewOutcome

SEMANTIC_ARTIFACT_AUTO_FINALIZATION_POLICY_VERSION: Final[str] = "1.0.0"


class SemanticArtifactAutoFinalizationDecision(BaseModel):
    """表示一次 M11 自动终态化的系统决策负载。

    :return: 无返回值；该决策由系统生成，不允许模型或下游领域输出。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    artifact_payload_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_bundle_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    turn_snapshot_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    claim_digests: tuple[str, ...] = Field(max_length=8)
    repair_plan_id: str | None = Field(default=None, min_length=64, max_length=64)
    repair_base_artifact_reference: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
    )
    repair_base_version: int | None = Field(default=None, ge=1)
    active_gap_ids: tuple[str, ...] = Field(default=(), max_length=8)
    from_gate: SemanticArtifactGate
    to_gate: SemanticArtifactGate

    @model_validator(mode="after")
    def validate_decision_gates(self) -> Self:
        """校验自动终态化决策只授予可消费终态。

        :return: 返回通过状态闭合校验的系统决策。
        :raises ValueError: from/to gate 不是合法自动终态化迁移时抛出。
        """
        valid_pairs = {
            (
                SemanticArtifactGate.SEMANTIC_REVIEW_SUPPORTED,
                SemanticArtifactGate.VERIFIED,
            ),
            (
                SemanticArtifactGate.SEMANTIC_REVIEW_SUPPORTED,
                SemanticArtifactGate.REPAIR_VERIFIED,
            ),
        }
        if (self.from_gate, self.to_gate) not in valid_pairs:
            raise ValueError("semantic artifact auto finalization gate pair is invalid")
        if self.to_gate is SemanticArtifactGate.REPAIR_VERIFIED:
            if (
                self.repair_plan_id is None
                or self.repair_base_artifact_reference is None
                or self.repair_base_version is None
            ):
                raise ValueError("repair verified decision requires lineage binding")
        elif (
            self.repair_plan_id is not None
            or self.repair_base_artifact_reference is not None
            or self.repair_base_version is not None
        ):
            raise ValueError("verified decision cannot carry repair lineage binding")
        return self


class SemanticArtifactAutoFinalizationPolicy(BaseModel):
    """表示 M11 artifact 自动终态化的不可变生产策略。

    :return: 无返回值；策略只执行结构闭合判断，不重复 M08 语义审查。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    policy_version: str = Field(
        default=SEMANTIC_ARTIFACT_AUTO_FINALIZATION_POLICY_VERSION,
        pattern=r"^\d+\.\d+\.\d+$",
    )

    @classmethod
    def production(cls) -> SemanticArtifactAutoFinalizationPolicy:
        """构造当前生产环境的自动终态化策略。

        :return: 返回固定版本的 M11 deterministic policy。
        """
        return cls()

    def finalize(
        self,
        *,
        artifact: SemanticArtifactVersionRecord,
        bundle: SemanticReviewBundle,
    ) -> SemanticArtifactAutoFinalizationDecision | None:
        """对 M08 supported artifact 执行确定性结构终态化。

        :param artifact: 待提交的 M11 回合级 artifact version。
        :param bundle: artifact 内嵌的 M08 Review Bundle。
        :return: 结构闭合时返回 verified / repair_verified 决策，否则返回 None。
        """
        if bundle.aggregate_outcome is not SemanticReviewOutcome.SEMANTIC_REVIEW_SUPPORTED:
            return None
        if artifact.payload.turn_intent is None:
            return None
        if not self._artifact_bundle_identity_is_closed(
            artifact=artifact,
            bundle=bundle,
        ):
            return None
        if bundle.clarification_gaps or not self._claims_are_structurally_closed(
            artifact=artifact,
            bundle=bundle,
        ):
            return None
        if not artifact.payload.claims and not self._empty_graph_is_finalizable(bundle):
            return None
        if not self._repair_lineage_is_closed(artifact):
            return None
        return SemanticArtifactAutoFinalizationDecision(
            policy_version=self.policy_version,
            artifact_payload_digest=artifact.payload_digest,
            review_bundle_digest=compute_review_bundle_digest(bundle),
            turn_snapshot_digest=artifact.payload.turn_snapshot_digest,
            claim_digests=tuple(claim.claim_digest for claim in artifact.payload.claims),
            repair_plan_id=(
                None
                if artifact.payload.repair_lineage is None
                else artifact.payload.repair_lineage.repair_plan_id
            ),
            repair_base_artifact_reference=(
                None
                if artifact.payload.repair_lineage is None
                else artifact.payload.repair_lineage.base_artifact_reference
            ),
            repair_base_version=(
                None
                if artifact.payload.repair_lineage is None
                else artifact.payload.repair_lineage.base_version
            ),
            active_gap_ids=(),
            from_gate=SemanticArtifactGate.SEMANTIC_REVIEW_SUPPORTED,
            to_gate=(
                SemanticArtifactGate.REPAIR_VERIFIED
                if artifact.payload.repair_depth == 1
                else SemanticArtifactGate.VERIFIED
            ),
        )

    def _claims_are_structurally_closed(
        self,
        *,
        artifact: SemanticArtifactVersionRecord,
        bundle: SemanticReviewBundle,
    ) -> bool:
        """校验 claims、逐 claim review 与 gap 引用的结构闭合。

        :param artifact: 待终态化的 M11 artifact version。
        :param bundle: artifact 内嵌的 M08 Review Bundle。
        :return: 结构闭合时返回 True，否则返回 False。
        """
        if compute_artifact_payload_digest(artifact.payload) != artifact.payload_digest:
            return False
        claims = artifact.payload.claims
        if tuple(claim.claim_index for claim in claims) != tuple(range(len(claims))):
            return False
        if len({claim.claim_digest for claim in claims}) != len(claims):
            return False
        reviews = bundle.faithfulness_reviews
        if tuple(review.claim_index for review in reviews) != tuple(range(len(claims))):
            return False
        claim_by_index = {claim.claim_index: claim for claim in claims}
        for review in reviews:
            claim = claim_by_index[review.claim_index]
            if (
                review.claim_digest != claim.claim_digest
                or review.claim_proposition != claim.proposition
            ):
                return False
        return all(
            gap.claim_index in claim_by_index
            and claim_by_index[gap.claim_index].claim_digest == gap.claim_digest
            for gap in bundle.clarification_gaps
        )

    def _artifact_bundle_identity_is_closed(
        self,
        *,
        artifact: SemanticArtifactVersionRecord,
        bundle: SemanticReviewBundle,
    ) -> bool:
        """校验 artifact payload 与 M08 Review Bundle 的权威身份闭合。

        :param artifact: 待终态化的 M11 artifact version。
        :param bundle: artifact 内嵌的 M08 Review Bundle。
        :return: run / task / attempt / snapshot / proposal 身份一致时返回 True。
        """
        payload = artifact.payload
        return (
            payload.review_bundle == bundle
            and payload.run_id == bundle.run_id
            and payload.source_task_id == bundle.source_task_id
            and payload.source_attempt_number == bundle.source_attempt_number
            and payload.turn_snapshot_digest == bundle.turn_snapshot_digest
            and payload.source_proposal_digest == bundle.source_proposal_digest
        )

    def _empty_graph_is_finalizable(
        self,
        bundle: SemanticReviewBundle,
    ) -> bool:
        """判断空 claim inventory 是否具有合法结构化空图原因。

        :param bundle: 当前 M08 Review Bundle。
        :return: 仅 no_explicit_fact 且非 suspicious_empty 时返回 True。
        """
        derived = bundle.coverage_review.derived
        return derived is not None and not derived.suspicious_empty

    def _repair_lineage_is_closed(
        self,
        artifact: SemanticArtifactVersionRecord,
    ) -> bool:
        """校验普通与修复后 artifact 的 repair lineage 结构。

        :param artifact: 待终态化的 M11 artifact version。
        :return: repair_depth 与 lineage 闭合时返回 True，否则返回 False。
        """
        lineage = artifact.payload.repair_lineage
        if artifact.payload.repair_depth == 0:
            return lineage is None
        return (
            artifact.payload.repair_depth == 1
            and lineage is not None
            and lineage.base_version < artifact.version
            and artifact.payload.review_bundle is not None
        )


__all__ = [
    "SEMANTIC_ARTIFACT_AUTO_FINALIZATION_POLICY_VERSION",
    "SemanticArtifactAutoFinalizationDecision",
    "SemanticArtifactAutoFinalizationPolicy",
]

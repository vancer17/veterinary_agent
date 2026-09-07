"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/claim_graph_verifier.py
作用：实现受限语义协作 DAG M12 的纯结构一致性门禁。
范围：覆盖 artifact 当前性、Turn Intent section、claim/review/gap/repair
      引用闭合、gate 状态机与空图消费策略检查。
说明：本文件不判断语义重复、语义冲突、医学正确性、gap 合理性或修复质量；
      所有语义结论必须来自上游 M08/M10/M08 re-review 并由 M11 gate 承载。
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass

from .artifact_contracts import (
    SemanticArtifactGate,
    SemanticArtifactGateEventRecord,
    SemanticArtifactVersionRecord,
)
from .claim_graph_contracts import (
    SemanticClaimGraph,
    SemanticGraphBuildPolicy,
    SemanticGraphFailureCode,
    SemanticGraphTerminalState,
    compute_claim_graph_digest,
)


@dataclass(frozen=True)
class SemanticGraphGateDecision:
    """表示 M12 结构门禁的分类结果。

    :return: 无返回值；该结果只描述结构与状态，不描述语义正确性。
    """

    terminal_state: SemanticGraphTerminalState
    failure_code: SemanticGraphFailureCode | None = None
    failure_message: str | None = None


class SemanticGraphStructuralConsistencyGate:
    """表示 M12 Claim Graph 的确定性结构门禁。

    :return: 无返回值；该门禁不读取原文、不调用模型、不做领域判断。
    """

    def classify(
        self,
        *,
        artifact: SemanticArtifactVersionRecord,
        latest: SemanticArtifactVersionRecord | None,
        current_gate: SemanticArtifactGateEventRecord | None,
        policy: SemanticGraphBuildPolicy,
    ) -> SemanticGraphGateDecision:
        """对 M11 artifact 的结构消费条件进行分类。

        :param artifact: 待投影的 M11 artifact version。
        :param latest: 同一回合的当前最新 artifact version。
        :param current_gate: artifact 当前 gate event。
        :param policy: graph 构建与消费策略。
        :return: 返回 graph 终态或 blocked 失败分类。
        """
        if latest is None or latest.artifact_reference != artifact.artifact_reference:
            return self._blocked(
                SemanticGraphFailureCode.ARTIFACT_STALE,
                "semantic graph source is not the latest artifact",
            )
        if artifact.payload.turn_intent is None:
            return self._blocked(
                SemanticGraphFailureCode.TURN_INTENT_MISSING,
                "semantic graph requires embedded turn intent",
            )
        if artifact.payload.review_bundle is None:
            if artifact.payload.repair_depth == 1:
                return self._blocked(
                    SemanticGraphFailureCode.REPAIR_VERIFICATION_PENDING,
                    "repaired artifact has not completed semantic re-review",
                )
            return self._blocked(
                SemanticGraphFailureCode.REVIEW_BUNDLE_MISSING,
                "semantic artifact review bundle is missing",
            )
        if current_gate is None:
            return self._blocked(
                SemanticGraphFailureCode.GATE_MISSING,
                "semantic artifact current gate is missing",
            )
        if current_gate.sequence < 1:
            return self._blocked(
                SemanticGraphFailureCode.GATE_SEQUENCE_INVALID,
                "semantic artifact gate sequence is invalid",
            )
        if current_gate.to_gate is SemanticArtifactGate.DISAGREEMENT:
            return SemanticGraphGateDecision(
                SemanticGraphTerminalState.GRAPH_DISAGREEMENT,
            )
        if current_gate.to_gate is SemanticArtifactGate.PATCH_APPLIED:
            return self._blocked(
                SemanticGraphFailureCode.REPAIR_VERIFICATION_PENDING,
                "patch applied artifact awaits M07 and M08 re-review",
            )
        if current_gate.to_gate not in {
            SemanticArtifactGate.VERIFIED,
            SemanticArtifactGate.REPAIR_VERIFIED,
        }:
            return self._blocked(
                SemanticGraphFailureCode.GATE_NOT_CONSUMABLE,
                "semantic artifact gate is not consumable as a claim graph",
            )
        if artifact.payload.repair_depth == 1 and current_gate.to_gate is not (
            SemanticArtifactGate.REPAIR_VERIFIED
        ):
            return self._blocked(
                SemanticGraphFailureCode.GATE_NOT_CONSUMABLE,
                "repaired artifact requires repair_verified gate",
            )
        coverage = artifact.payload.review_bundle.coverage_review.derived
        if not artifact.payload.claims:
            if coverage is not None and coverage.suspicious_empty:
                return self._blocked(
                    SemanticGraphFailureCode.SUSPICIOUS_EMPTY,
                    "empty claim inventory is marked suspicious by M08",
                )
            if not policy.allow_empty_graph:
                return self._blocked(
                    SemanticGraphFailureCode.EMPTY_GRAPH_NOT_ALLOWED,
                    "empty semantic graph is not allowed by policy",
                )
            return SemanticGraphGateDecision(
                SemanticGraphTerminalState.GRAPH_VERIFIED,
            )
        if artifact.payload.review_bundle.clarification_gaps and not (
            policy.allow_partial_with_gaps
        ):
            return self._blocked(
                SemanticGraphFailureCode.PARTIAL_GRAPH_NOT_ALLOWED,
                "semantic graph gaps are not allowed by policy",
            )
        if artifact.payload.review_bundle.clarification_gaps:
            return SemanticGraphGateDecision(
                SemanticGraphTerminalState.GRAPH_PARTIAL_WITH_GAPS,
            )
        return SemanticGraphGateDecision(SemanticGraphTerminalState.GRAPH_VERIFIED)

    def verify(
        self,
        *,
        graph: SemanticClaimGraph,
        artifact: SemanticArtifactVersionRecord,
        latest: SemanticArtifactVersionRecord | None,
        current_gate: SemanticArtifactGateEventRecord | None,
        policy: SemanticGraphBuildPolicy,
    ) -> SemanticGraphGateDecision:
        """验证已组装 graph 的结构、身份、引用与状态闭合。

        :param graph: 待验证的 scoped claim graph。
        :param artifact: 来源 M11 artifact version。
        :param latest: 同一回合最新 artifact version。
        :param current_gate: artifact 当前 gate event。
        :param policy: graph 构建与消费策略。
        :return: 返回结构门禁终态或 blocked 分类。
        """
        try:
            SemanticClaimGraph.model_validate_json(graph.model_dump_json())
        except ValueError:
            return self._blocked(
                SemanticGraphFailureCode.STRUCTURE_INVALID,
                "semantic graph internal structure is invalid",
            )
        if (
            graph.identity.artifact_reference != artifact.artifact_reference
            or graph.identity.artifact_payload_digest != artifact.payload_digest
            or compute_claim_graph_digest(graph) != graph.identity.graph_digest
        ):
            return self._blocked(
                SemanticGraphFailureCode.PAYLOAD_DIGEST_MISMATCH,
                "semantic graph identity or digest mismatch",
            )
        return self.classify(
            artifact=artifact,
            latest=latest,
            current_gate=current_gate,
            policy=policy,
        )

    def _blocked(
        self,
        failure_code: SemanticGraphFailureCode,
        failure_message: str,
    ) -> SemanticGraphGateDecision:
        """构造显式 blocked 结构门禁结果。

        :param failure_code: 稳定结构失败码。
        :param failure_message: 面向工程排障的失败说明。
        :return: 返回 blocked 分类。
        """
        return SemanticGraphGateDecision(
            SemanticGraphTerminalState.GRAPH_BLOCKED,
            failure_code,
            failure_message,
        )


__all__ = [
    "SemanticGraphGateDecision",
    "SemanticGraphStructuralConsistencyGate",
]

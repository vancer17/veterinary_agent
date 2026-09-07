"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/claim_graph_builder.py
作用：实现受限语义协作 DAG M12 的 Claim Graph 确定性构建器。
范围：覆盖 M11 回合 artifact 读取、当前版本校验、Turn Intent / claims /
      review / gaps / repair lineage 结构投影、scoped reference 构造与
      纯结构一致性门禁调用。
说明：本文件不调用 LLM、不读取原始用户文本、不判断语义重复或冲突、
      不做医学判断、不生成追问，也不写任何下游领域状态。
=============================================================================
"""

from __future__ import annotations

from hashlib import sha256

from .artifact_contracts import (
    SemanticArtifactGateEventRecord,
    SemanticArtifactRepository,
    SemanticArtifactVersionRecord,
    SemanticClarificationGapStatus,
    compute_artifact_payload_digest,
)
from .claim_graph_contracts import (
    SemanticClaimGraph,
    SemanticGraphBuildPolicy,
    SemanticGraphBuildResult,
    SemanticGraphClaimNode,
    SemanticGraphEmptyReason,
    SemanticGraphFailureCode,
    SemanticGraphGapNode,
    SemanticGraphGateNode,
    SemanticGraphIdentity,
    SemanticGraphIntentNode,
    SemanticGraphRepairNode,
    SemanticGraphTerminalState,
    canonical_claim_graph_json,
    compute_claim_graph_digest,
)
from .claim_graph_verifier import SemanticGraphStructuralConsistencyGate
from .errors import SemanticArtifactStoreError
from .review_contracts import SemanticReviewOutcome


class SemanticClaimGraphBuilder:
    """表示从 M11 当前 artifact 重建结构化 Claim Graph 的构建器。

    :return: 无返回值；graph 是可重建投影，不是第二权威事实源。
    """

    def __init__(
        self,
        repository: SemanticArtifactRepository,
        gate: SemanticGraphStructuralConsistencyGate | None = None,
    ) -> None:
        """初始化 M12 graph builder。

        :param repository: M11 权威 artifact 仓储端口。
        :param gate: 可选纯结构一致性门禁；为空时使用默认实现。
        :return: 无返回值。
        """
        self.repository = repository
        self.gate = gate or SemanticGraphStructuralConsistencyGate()

    def build(
        self,
        artifact_reference: str,
        policy: SemanticGraphBuildPolicy | None = None,
    ) -> SemanticGraphBuildResult:
        """从 M11 当前回合 artifact 构建结构化 Claim Graph。

        :param artifact_reference: M11 稳定 artifact 引用。
        :param policy: 可选 graph 消费策略；为空时使用默认策略。
        :return: 返回 graph 终态或显式 blocked 结果。
        """
        materialized_policy = policy or SemanticGraphBuildPolicy()
        artifact = self.repository.get_artifact(artifact_reference)
        if artifact is None:
            return self._blocked(
                SemanticGraphFailureCode.ARTIFACT_MISSING,
                "semantic graph source artifact is not found",
            )
        try:
            self._validate_artifact_record(artifact)
        except SemanticArtifactStoreError as error:
            return self._blocked(
                SemanticGraphFailureCode.PAYLOAD_DIGEST_MISMATCH,
                str(error),
            )
        if self.repository.is_artifact_stale(artifact_reference):
            return self._blocked(
                SemanticGraphFailureCode.ARTIFACT_STALE,
                "semantic graph source artifact is stale",
            )
        latest = self.repository.get_latest_artifact(
            artifact.payload.run_id,
            artifact.payload.source_task_id,
            artifact.payload.turn_snapshot_digest,
        )
        current_gate = self.repository.get_current_gate(artifact_reference)
        decision = self.gate.classify(
            artifact=artifact,
            latest=latest,
            current_gate=current_gate,
            policy=materialized_policy,
        )
        if decision.terminal_state is SemanticGraphTerminalState.GRAPH_BLOCKED:
            return self._blocked(
                decision.failure_code or SemanticGraphFailureCode.STRUCTURE_INVALID,
                decision.failure_message or "semantic graph structure is invalid",
            )
        structural_failure = self._validate_payload_structure(artifact)
        if structural_failure is not None:
            return structural_failure
        assert artifact.payload.turn_intent is not None
        assert artifact.payload.review_bundle is not None
        assert current_gate is not None
        graph = self._build_graph(
            artifact=artifact,
            current_gate=current_gate,
            policy=materialized_policy,
            terminal_state=decision.terminal_state,
        )
        verification = self.gate.verify(
            graph=graph,
            artifact=artifact,
            latest=latest,
            current_gate=current_gate,
            policy=materialized_policy,
        )
        if verification.terminal_state is SemanticGraphTerminalState.GRAPH_BLOCKED:
            return self._blocked(
                verification.failure_code or SemanticGraphFailureCode.STRUCTURE_INVALID,
                verification.failure_message or "semantic graph verification failed",
            )
        if verification.terminal_state != graph.terminal_state:
            graph = graph.model_copy(
                update={"terminal_state": verification.terminal_state},
            )
            graph = graph.model_copy(
                update={
                    "identity": graph.identity.model_copy(
                        update={"graph_digest": compute_claim_graph_digest(graph)},
                    ),
                },
            )
        return SemanticGraphBuildResult(
            terminal_state=graph.terminal_state,
            graph=graph,
        )

    def _validate_artifact_record(
        self,
        artifact: SemanticArtifactVersionRecord,
    ) -> None:
        """校验 M11 artifact record 身份与 payload digest。

        :param artifact: M11 artifact version record。
        :return: 无返回值。
        :raises SemanticArtifactStoreError: payload digest 或身份不匹配时抛出。
        """
        if compute_artifact_payload_digest(artifact.payload) != artifact.payload_digest:
            raise SemanticArtifactStoreError(
                "semantic artifact payload digest mismatch"
            )

    def _validate_payload_structure(
        self,
        artifact: SemanticArtifactVersionRecord,
    ) -> SemanticGraphBuildResult | None:
        """校验 payload 内 claim、review、gap 与 repair 引用闭合。

        :param artifact: M11 artifact version record。
        :return: 发现结构失败时返回 blocked，否则返回 None。
        """
        claims = artifact.payload.claims
        indexes = tuple(claim.claim_index for claim in claims)
        if indexes != tuple(range(len(claims))):
            return self._blocked(
                SemanticGraphFailureCode.CLAIM_INDEX_INVALID,
                "semantic graph claim indexes are not contiguous",
            )
        digests = [claim.claim_digest for claim in claims]
        if len(set(digests)) != len(digests):
            return self._blocked(
                SemanticGraphFailureCode.DUPLICATE_CLAIM,
                "semantic graph contains duplicate claim digest",
            )
        bundle = artifact.payload.review_bundle
        if bundle is None:
            return self._blocked(
                SemanticGraphFailureCode.REVIEW_BUNDLE_MISSING,
                "semantic graph requires review bundle",
            )
        review_indexes = tuple(
            record.claim_index for record in bundle.faithfulness_reviews
        )
        if review_indexes != indexes:
            return self._blocked(
                SemanticGraphFailureCode.REVIEW_COVERAGE_INCOMPLETE,
                "semantic graph faithfulness review coverage is incomplete",
            )
        claim_by_index = {claim.claim_index: claim for claim in claims}
        for record in bundle.faithfulness_reviews:
            claim = claim_by_index[record.claim_index]
            if (
                record.claim_digest != claim.claim_digest
                or record.claim_proposition != claim.proposition
            ):
                return self._blocked(
                    SemanticGraphFailureCode.REVIEW_CLAIM_MISMATCH,
                    "semantic graph review claim identity mismatch",
                )
        for gap in bundle.clarification_gaps:
            gap_claim = claim_by_index.get(gap.claim_index)
            if gap_claim is None or gap_claim.claim_digest != gap.claim_digest:
                return self._blocked(
                    SemanticGraphFailureCode.GAP_REFERENCE_INVALID,
                    "semantic graph clarification gap reference is invalid",
                )
        repair = artifact.payload.repair_lineage
        if artifact.payload.repair_depth == 1 and repair is None:
            return self._blocked(
                SemanticGraphFailureCode.REPAIR_LINEAGE_INVALID,
                "repaired semantic graph requires repair lineage",
            )
        if repair is not None and (
            artifact.payload.repair_depth != 1
            or repair.base_version >= artifact.version
        ):
            return self._blocked(
                SemanticGraphFailureCode.REPAIR_LINEAGE_INVALID,
                "semantic graph repair lineage is invalid",
            )
        return None

    def _build_graph(
        self,
        *,
        artifact: SemanticArtifactVersionRecord,
        current_gate: object,
        policy: SemanticGraphBuildPolicy,
        terminal_state: SemanticGraphTerminalState,
    ) -> SemanticClaimGraph:
        """将 M11 payload 投影为 scoped Claim Graph 节点集合。

        :param artifact: M11 artifact version record。
        :param current_gate: 当前 gate event record。
        :param policy: graph 消费策略。
        :param terminal_state: 结构门禁初分类。
        :return: 返回不可变 Claim Graph。
        """
        from .artifact_contracts import SemanticArtifactGateEventRecord

        if not isinstance(current_gate, SemanticArtifactGateEventRecord):
            raise SemanticArtifactStoreError("semantic graph gate record is invalid")
        intent_section = artifact.payload.turn_intent
        bundle = artifact.payload.review_bundle
        if intent_section is None or bundle is None:
            raise SemanticArtifactStoreError(
                "semantic graph payload section is missing"
            )
        intent = SemanticGraphIntentNode.from_section(intent_section)
        gaps: list[SemanticGraphGapNode] = []
        gaps_by_claim: dict[int, list[str]] = {}
        for gap in bundle.clarification_gaps:
            gap_ref = self._gap_ref(artifact, gap.gap_id)
            gaps_by_claim.setdefault(gap.claim_index, []).append(gap_ref)
            gaps.append(
                SemanticGraphGapNode(
                    gap_id=gap.gap_id,
                    claim_ref=self._claim_ref(
                        artifact,
                        gap.claim_index,
                        gap.claim_digest,
                    ),
                    claim_index=gap.claim_index,
                    claim_digest=gap.claim_digest,
                    ambiguous_dimension=gap.ambiguous_dimension,
                    required_binding_type=gap.required_binding_type,
                    status=SemanticClarificationGapStatus.ACTIVE,
                ),
            )
        claims = tuple(
            SemanticGraphClaimNode(
                claim_ref=self._claim_ref(
                    artifact,
                    claim.claim_index,
                    claim.claim_digest,
                ),
                claim_index=claim.claim_index,
                claim_digest=claim.claim_digest,
                proposition=claim.proposition,
                review_status=self._claim_review_status(
                    bundle,
                    claim.claim_index,
                ),
                gap_refs=tuple(gaps_by_claim.get(claim.claim_index, ())),
            )
            for claim in artifact.payload.claims
        )
        repair_lineage = artifact.payload.repair_lineage
        repair = (
            None
            if repair_lineage is None
            else SemanticGraphRepairNode(
                repair_plan_id=repair_lineage.repair_plan_id,
                base_artifact_reference=repair_lineage.base_artifact_reference,
                base_version=repair_lineage.base_version,
                result_artifact_reference=artifact.artifact_reference,
                result_version=artifact.version,
                repair_depth=artifact.payload.repair_depth,
            )
        )
        identity = self._identity(
            artifact=artifact,
            current_gate=current_gate,
            policy=policy,
            graph_digest="0" * 64,
        )
        coverage = bundle.coverage_review.derived
        empty_reason = None
        if not claims and coverage is not None:
            empty_reason = (
                SemanticGraphEmptyReason.SUSPICIOUS_EMPTY
                if coverage.suspicious_empty
                else SemanticGraphEmptyReason.NO_EXPLICIT_FACT
            )
        graph = SemanticClaimGraph(
            identity=identity,
            intent=intent,
            claims=claims,
            gaps=tuple(gaps),
            repair=repair,
            gate=SemanticGraphGateNode(
                gate=current_gate.to_gate,
                sequence=current_gate.sequence,
                reason_code=current_gate.reason_code,
            ),
            terminal_state=terminal_state,
            empty_reason=empty_reason,
        )
        digest = compute_claim_graph_digest(graph)
        return graph.model_copy(
            update={
                "identity": graph.identity.model_copy(update={"graph_digest": digest}),
            },
        )

    def _claim_review_status(
        self,
        bundle: object,
        claim_index: int,
    ) -> SemanticReviewOutcome:
        """读取指定 claim 的上游 M08 派生状态。

        :param bundle: M08 Review Bundle。
        :param claim_index: claim 序号。
        :return: 返回上游 review outcome。
        """
        from .review_contracts import SemanticReviewBundle

        if not isinstance(bundle, SemanticReviewBundle):
            raise SemanticArtifactStoreError("semantic graph review bundle is invalid")
        for record in bundle.faithfulness_reviews:
            if record.claim_index == claim_index and record.derived is not None:
                return record.derived.outcome
        return bundle.aggregate_outcome

    def _identity(
        self,
        *,
        artifact: SemanticArtifactVersionRecord,
        current_gate: SemanticArtifactGateEventRecord,
        policy: SemanticGraphBuildPolicy,
        graph_digest: str,
    ) -> SemanticGraphIdentity:
        """构造 graph 的确定性身份。

        :param artifact: 来源 artifact。
        :param current_gate: 当前 gate event。
        :param policy: graph 消费策略。
        :param graph_digest: graph canonical digest。
        :return: 返回 graph identity。
        """
        graph_id = sha256(
            canonical_claim_graph_json(
                {
                    "artifact_id": artifact.artifact_id,
                    "artifact_version": artifact.version,
                    "gate_sequence": current_gate.sequence,
                    "policy": policy.model_dump(mode="json"),
                },
            ).encode("utf-8"),
        ).hexdigest()
        return SemanticGraphIdentity(
            graph_id=graph_id,
            graph_digest=graph_digest,
            graph_contract_version=policy.contract_version,
            run_id=artifact.payload.run_id,
            turn_snapshot_digest=artifact.payload.turn_snapshot_digest,
            artifact_reference=artifact.artifact_reference,
            artifact_version=artifact.version,
            artifact_payload_digest=artifact.payload_digest,
            current_gate_sequence=current_gate.sequence,
        )

    def _claim_ref(
        self,
        artifact: SemanticArtifactVersionRecord,
        claim_index: int,
        claim_digest: str,
    ) -> str:
        """构造绑定 artifact version 的 scoped claim reference。

        :param artifact: 来源 artifact。
        :param claim_index: claim 序号。
        :param claim_digest: claim digest。
        :return: 返回 scoped claim_ref。
        """
        return (
            f"semantic-claim://{artifact.artifact_id}/{artifact.version}/"
            f"{claim_index}/{claim_digest}"
        )

    def _gap_ref(
        self,
        artifact: SemanticArtifactVersionRecord,
        gap_id: str,
    ) -> str:
        """构造绑定 artifact version 的 scoped gap reference。

        :param artifact: 来源 artifact。
        :param gap_id: M08 gap 稳定标识。
        :return: 返回 scoped gap_ref 字符串。
        """
        return f"semantic-gap://{artifact.artifact_id}/{artifact.version}/{gap_id}"

    def _blocked(
        self,
        failure_code: SemanticGraphFailureCode,
        failure_message: str,
    ) -> SemanticGraphBuildResult:
        """构造显式 blocked graph 构建结果。

        :param failure_code: 稳定结构失败码。
        :param failure_message: 工程排障说明。
        :return: 返回不携带 graph 的 blocked 结果。
        """
        return SemanticGraphBuildResult(
            terminal_state=SemanticGraphTerminalState.GRAPH_BLOCKED,
            failure_code=failure_code,
            failure_message=failure_message,
        )


__all__ = ["SemanticClaimGraphBuilder"]

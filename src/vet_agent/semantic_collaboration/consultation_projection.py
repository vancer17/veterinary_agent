"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/consultation_projection.py
作用：实现受限语义协作 DAG M13 的问诊领域投影 Adapter。
范围：覆盖 M12 verified / partial graph 到问诊可消费的 Turn Intent、语义事实、
      clarification gap 与来源身份的结构化投影。
说明：本文件只做确定性结构透传，不解释 proposition 自然语言、不映射
      normal / denied / uncertain、不判断医学风险、不决定是否追问，也不写
      问诊领域状态；回答充分性与 followup 策略由问诊领域自行负责。
=============================================================================
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .artifact_contracts import SemanticClarificationGapStatus
from .claim_graph_contracts import (
    SemanticClaimGraph,
    SemanticGraphBuildResult,
    SemanticGraphClaimNode,
    SemanticGraphEmptyReason,
    SemanticGraphGapNode,
    SemanticGraphTerminalState,
)
from .domain_projection_contracts import (
    SemanticProjectionAdapterKind,
    SemanticProjectionContract,
    SemanticProjectionFailureCode,
    SemanticProjectionImplementationState,
    SemanticProjectionResult,
    build_projected_projection_result,
    build_rejected_projection_result,
)
from .review_contracts import (
    ClaimFaithfulnessReviewDimension,
    ClarificationBindingType,
    SemanticReviewOutcome,
)


class ConsultationProjectionIntent(BaseModel):
    """表示问诊领域可消费的 Turn Intent 结构投影。

    :return: 无返回值；answer_now 是意图字段，不表示跳过 clarification gap。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    answer_now: bool
    wants_triage: bool
    correction: bool
    clarification_request: bool
    fact_statement_present: bool
    question_present: bool
    report_context_present: bool


class ConsultationProjectedFact(BaseModel):
    """表示问诊领域可消费的一条不透明语义事实投影。

    :return: 无返回值；proposition 原样透传，不在 M13 做文本语义分类。
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


class ConsultationClarificationGap(BaseModel):
    """表示问诊领域可消费的一条显式信息缺口投影。

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


class ConsultationProjection(BaseModel):
    """表示 M12 graph 到问诊领域的完整结构投影负载。

    :return: 无返回值；该负载不包含医学判断、追问指令或问诊状态写入结果。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    intent: ConsultationProjectionIntent
    facts: tuple[ConsultationProjectedFact, ...] = Field(default=(), max_length=8)
    clarification_gaps: tuple[ConsultationClarificationGap, ...] = Field(
        default=(),
        max_length=8,
    )
    empty_reason: SemanticGraphEmptyReason | None = None

    @model_validator(mode="after")
    def validate_projection_shape(self) -> ConsultationProjection:
        """校验问诊投影的事实、缺口与空图结构闭合。

        :return: 返回通过结构校验的问诊投影负载。
        :raises ValueError: 空图携带事实或非空图携带空图原因时抛出。
        """
        if self.empty_reason is not None and self.facts:
            raise ValueError("empty consultation projection cannot carry facts")
        if not self.facts and self.empty_reason is None:
            raise ValueError("empty consultation projection requires empty reason")
        return self


class ConsultationProjectionAdapter:
    """表示 M13 问诊领域的确定性投影 Adapter。

    :return: 无返回值；该 adapter 是语义 DAG 与问诊领域之间的防腐边界。
    """

    contract: Final[SemanticProjectionContract] = SemanticProjectionContract(
        adapter_kind=SemanticProjectionAdapterKind.CONSULTATION,
        contract_version="1.0.0",
        implementation_state=SemanticProjectionImplementationState.PRODUCTION,
        accepted_terminal_states=(
            SemanticGraphTerminalState.GRAPH_VERIFIED,
            SemanticGraphTerminalState.GRAPH_PARTIAL_WITH_GAPS,
        ),
    )

    def project(
        self,
        graph_result: SemanticGraphBuildResult,
    ) -> SemanticProjectionResult[ConsultationProjection]:
        """将 M12 graph result 投影为问诊领域消费契约。

        :param graph_result: M12 graph 构建与结构门禁结果。
        :return: 返回显式 projected 或 rejected 问诊投影结果。
        """
        if graph_result.terminal_state not in self.contract.accepted_terminal_states:
            return build_rejected_projection_result(
                contract=self.contract,
                graph=graph_result.graph,
                failure_code=SemanticProjectionFailureCode.TERMINAL_STATE_NOT_ALLOWED,
                failure_message=(
                    "consultation projection cannot consume graph terminal state "
                    f"{graph_result.terminal_state.value}"
                ),
            )
        graph = graph_result.graph
        if graph is None:
            return build_rejected_projection_result(
                contract=self.contract,
                graph=None,
                failure_code=SemanticProjectionFailureCode.GRAPH_MISSING,
                failure_message="accepted graph result does not carry a graph",
            )
        if graph.terminal_state is not graph_result.terminal_state:
            return build_rejected_projection_result(
                contract=self.contract,
                graph=graph,
                failure_code=SemanticProjectionFailureCode.GRAPH_TERMINAL_STATE_MISMATCH,
                failure_message="graph and build result terminal states disagree",
            )
        if not self._gap_shape_matches_terminal_state(graph):
            return build_rejected_projection_result(
                contract=self.contract,
                graph=graph,
                failure_code=SemanticProjectionFailureCode.GAP_SHAPE_MISMATCH,
                failure_message="graph gap shape does not match terminal state",
            )
        if not graph.claims and graph.empty_reason is None:
            return build_rejected_projection_result(
                contract=self.contract,
                graph=graph,
                failure_code=SemanticProjectionFailureCode.PROJECTION_CONTRACT_MISMATCH,
                failure_message="empty graph does not carry a structured empty reason",
            )
        projection = ConsultationProjection(
            intent=self._project_intent(graph),
            facts=tuple(self._project_fact(claim) for claim in graph.claims),
            clarification_gaps=tuple(
                self._project_gap(gap) for gap in graph.gaps
            ),
            empty_reason=graph.empty_reason,
        )
        return build_projected_projection_result(
            contract=self.contract,
            graph=graph,
            projection=projection,
        )

    def _project_intent(
        self,
        graph: SemanticClaimGraph,
    ) -> ConsultationProjectionIntent:
        """将 graph intent 节点确定性投影为问诊意图负载。

        :param graph: 来源 Claim Graph。
        :return: 返回问诊可消费的 Turn Intent 投影。
        """
        return ConsultationProjectionIntent(
            answer_now=graph.intent.answer_now,
            wants_triage=graph.intent.wants_triage,
            correction=graph.intent.correction,
            clarification_request=graph.intent.clarification_request,
            fact_statement_present=graph.intent.fact_statement_present,
            question_present=graph.intent.question_present,
            report_context_present=graph.intent.report_context_present,
        )

    def _project_fact(
        self,
        claim: SemanticGraphClaimNode,
    ) -> ConsultationProjectedFact:
        """将单个 graph claim 节点投影为不透明问诊事实。

        :param claim: M12 scoped claim 节点。
        :return: 返回原样保留 proposition 的问诊事实投影。
        """
        return ConsultationProjectedFact(
            claim_ref=claim.claim_ref,
            claim_index=claim.claim_index,
            claim_digest=claim.claim_digest,
            proposition=claim.proposition,
            review_status=claim.review_status,
            gap_refs=claim.gap_refs,
        )

    def _project_gap(
        self,
        gap: SemanticGraphGapNode,
    ) -> ConsultationClarificationGap:
        """将单个 graph gap 节点投影为显式问诊信息缺口。

        :param gap: M12 clarification gap 节点。
        :return: 返回不生成追问指令的 gap 投影。
        """
        return ConsultationClarificationGap(
            gap_id=gap.gap_id,
            claim_ref=gap.claim_ref,
            claim_index=gap.claim_index,
            claim_digest=gap.claim_digest,
            ambiguous_dimension=gap.ambiguous_dimension,
            required_binding_type=gap.required_binding_type,
            status=gap.status,
        )

    def _gap_shape_matches_terminal_state(
        self,
        graph: SemanticClaimGraph,
    ) -> bool:
        """校验 graph gap 负载与终态的结构关系。

        :param graph: 来源 Claim Graph。
        :return: partial graph 必须携带 active gap，verified graph 不得携带 gap。
        """
        if graph.terminal_state is SemanticGraphTerminalState.GRAPH_PARTIAL_WITH_GAPS:
            return bool(graph.gaps) and all(
                gap.status is SemanticClarificationGapStatus.ACTIVE
                for gap in graph.gaps
            )
        return not graph.gaps


__all__ = [
    "ConsultationClarificationGap",
    "ConsultationProjectedFact",
    "ConsultationProjection",
    "ConsultationProjectionAdapter",
    "ConsultationProjectionIntent",
]

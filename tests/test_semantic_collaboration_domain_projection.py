"""
=============================================================================
文件：tests/test_semantic_collaboration_domain_projection.py
作用：验证受限语义协作 DAG M13 领域投影 Adapter 的生产消费边界。
范围：覆盖问诊 verified / partial graph 投影、语义差异原样保留、空图、
      blocked graph 显式拒绝与临床安全 / 长期记忆 TODO 空壳失败。
说明：本测试只使用进程内 M11/M12 测试替身，不访问数据库、LiteLLM、
      Temporal、OPA、问诊状态、临床安全链路或长期记忆存储。
=============================================================================
"""

from __future__ import annotations

import asyncio

import pytest

from tests.test_semantic_collaboration_artifact_store import (
    FakeArtifactRepository,
    _turn_intent_proposal,
)
from tests.test_semantic_collaboration_repair_planner import _bundle
from tests.test_semantic_collaboration_review import (
    _empty_coverage_payload,
    _empty_faithfulness_payload,
    _faithfulness_payload,
    _snapshot,
)
from vet_agent.semantic_collaboration import (
    ConsultationProjectionAdapter,
    PostgresSemanticArtifactStore,
    SemanticClaimGraphBuilder,
    SemanticGraphBuildPolicy,
    SemanticGraphBuildResult,
    SemanticGraphEmptyReason,
    SemanticGraphFailureCode,
    SemanticGraphTerminalState,
    SemanticProjectionAdapterKind,
    SemanticProjectionContract,
    SemanticProjectionExecutionState,
    SemanticProjectionFailureCode,
    SemanticProjectionImplementationState,
    TODOClinicalSafetyProjectionAdapter,
    TODOLongTermMemoryProjectionAdapter,
)


def _build_verified_graph(
    *,
    claims: tuple[str, ...],
    faithfulness_payload: dict[str, object] | None = None,
) -> SemanticGraphBuildResult:
    """构造已通过 M11 自动终态化的 M12 graph 测试输入。

    :param claims: 自包含自然语言 proposition 集合。
    :param faithfulness_payload: 可选 Faithfulness 审查矩阵。
    :return: 返回 M12 build result 与 M11 仓储替身。
    """
    repository = FakeArtifactRepository()
    store = PostgresSemanticArtifactStore(repository)
    builder = SemanticClaimGraphBuilder(repository)
    snapshot = _snapshot()
    faithfulness_payloads = [faithfulness_payload or _empty_faithfulness_payload()]
    faithfulness_payloads.extend(
        _empty_faithfulness_payload() for _ in range(max(len(claims) - 1, 0))
    )
    bundle = _bundle(
        (
            {"claims": list(claims)},
            _empty_coverage_payload(),
            *faithfulness_payloads,
        ),
    )
    reference = asyncio.run(
        store.commit_semantic_turn_result(
            review_bundle=bundle,
            turn_intent_proposal=_turn_intent_proposal(bundle, snapshot),
        ),
    )
    return builder.build(
        reference,
        SemanticGraphBuildPolicy(allow_empty_graph=not claims),
    )


def test_consultation_projection_preserves_verified_graph_identity() -> None:
    """验证问诊投影原样保留 proposition 且绑定 graph 来源身份。

    :return: 无返回值。
    """
    graph_result = _build_verified_graph(
        claims=("英短精神状态正常", "英短没有呕吐"),
    )

    projection = ConsultationProjectionAdapter().project(graph_result)

    assert projection.execution_state is SemanticProjectionExecutionState.PROJECTED
    assert projection.projection is not None
    assert projection.source is not None
    assert projection.source.terminal_state is (
        SemanticGraphTerminalState.GRAPH_VERIFIED
    )
    assert projection.projection.intent.fact_statement_present is True
    assert [fact.proposition for fact in projection.projection.facts] == [
        "英短精神状态正常",
        "英短没有呕吐",
    ]
    assert projection.projection.clarification_gaps == ()


def test_consultation_projection_preserves_gap_without_followup_decision() -> None:
    """验证 partial graph 保留 clarification gap 且不生成追问决策。

    :return: 无返回值。
    """
    repository = FakeArtifactRepository()
    store = PostgresSemanticArtifactStore(repository)
    builder = SemanticClaimGraphBuilder(repository)
    snapshot = _snapshot()
    bundle = _bundle(
        (
            {"claims": ["它的精神状态不明"]},
            _empty_coverage_payload(),
            _faithfulness_payload(指代对象不明=True),
        ),
    )
    reference = asyncio.run(
        store.commit_semantic_turn_result(
            review_bundle=bundle,
            turn_intent_proposal=_turn_intent_proposal(bundle, snapshot),
        ),
    )
    graph_result = builder.build(
        reference,
        SemanticGraphBuildPolicy(allow_partial_with_gaps=True),
    )

    projection = ConsultationProjectionAdapter().project(graph_result)

    assert projection.execution_state is SemanticProjectionExecutionState.PROJECTED
    assert projection.projection is not None
    assert projection.source is not None
    assert projection.source.terminal_state is (
        SemanticGraphTerminalState.GRAPH_PARTIAL_WITH_GAPS
    )
    fact = projection.projection.facts[0]
    gap = projection.projection.clarification_gaps[0]
    assert len(projection.projection.facts) == 1
    assert len(projection.projection.clarification_gaps) == 1
    assert fact.claim_ref == gap.claim_ref
    assert fact.claim_digest == gap.claim_digest
    assert len(fact.gap_refs) == 1
    assert gap.status.value == "active"
    assert gap.required_binding_type.value == "subject_reference"
    assert gap.ambiguous_dimension.value == "指代对象不明"


def test_consultation_projection_preserves_legal_empty_graph() -> None:
    """验证 no_explicit_fact 空图不会被转换成失败或空事实列表。

    :return: 无返回值。
    """
    graph_result = _build_verified_graph(claims=())

    projection = ConsultationProjectionAdapter().project(graph_result)

    assert projection.execution_state is SemanticProjectionExecutionState.PROJECTED
    assert projection.projection is not None
    assert projection.projection.facts == ()
    assert projection.projection.empty_reason is (
        SemanticGraphEmptyReason.NO_EXPLICIT_FACT
    )


def test_consultation_projection_rejects_blocked_graph() -> None:
    """验证问诊投影不消费 blocked graph 且不降级为空 facts。

    :return: 无返回值。
    """
    repository = FakeArtifactRepository()
    store = PostgresSemanticArtifactStore(repository)
    builder = SemanticClaimGraphBuilder(repository)
    bundle = _bundle(
        (
            {"claims": ["英短没有呕吐"]},
            _empty_coverage_payload(),
            _empty_faithfulness_payload(),
        ),
    )
    reference = asyncio.run(
        store.commit_review_bundle(bundle),
    )
    blocked = builder.build(reference)

    projection = ConsultationProjectionAdapter().project(blocked)

    assert blocked.failure_code is SemanticGraphFailureCode.TURN_INTENT_MISSING
    assert projection.execution_state is SemanticProjectionExecutionState.REJECTED
    assert projection.failure_code is (
        SemanticProjectionFailureCode.TERMINAL_STATE_NOT_ALLOWED
    )
    assert projection.source is None
    assert projection.projection is None


def test_consultation_projection_rejects_graph_state_mismatch() -> None:
    """验证问诊投影同时校验 build result 与 graph 内部终态。

    :return: 无返回值。
    """
    graph_result = _build_verified_graph(claims=("英短没有呕吐",))
    mismatched_result = graph_result.model_copy(
        update={
            "terminal_state": SemanticGraphTerminalState.GRAPH_PARTIAL_WITH_GAPS,
        },
    )

    projection = ConsultationProjectionAdapter().project(mismatched_result)

    assert projection.execution_state is SemanticProjectionExecutionState.REJECTED
    assert projection.failure_code is (
        SemanticProjectionFailureCode.GRAPH_TERMINAL_STATE_MISMATCH
    )
    assert projection.source is not None
    assert projection.projection is None


def test_projection_contract_rejects_unavailable_graph_states() -> None:
    """验证 M13 契约启动校验阻止声明 blocked / disagreement 消费。

    :return: 无返回值。
    """
    with pytest.raises(ValueError, match="cannot consume blocked graph state"):
        SemanticProjectionContract(
            adapter_kind=SemanticProjectionAdapterKind.CONSULTATION,
            contract_version="1.0.0",
            implementation_state=SemanticProjectionImplementationState.PRODUCTION,
            accepted_terminal_states=(
                SemanticGraphTerminalState.GRAPH_BLOCKED,
            ),
        )


def test_clinical_safety_and_memory_adapters_fail_explicitly() -> None:
    """验证未就绪领域 adapter 显式失败且不输出空领域集合。

    :return: 无返回值。
    """
    graph_result = _build_verified_graph(claims=("英短没有呕吐",))

    clinical_safety = TODOClinicalSafetyProjectionAdapter().project(graph_result)
    memory = TODOLongTermMemoryProjectionAdapter().project(graph_result)

    assert clinical_safety.execution_state is SemanticProjectionExecutionState.REJECTED
    assert clinical_safety.failure_code is (
        SemanticProjectionFailureCode.ADAPTER_NOT_IMPLEMENTED
    )
    assert clinical_safety.projection is None
    assert memory.execution_state is SemanticProjectionExecutionState.REJECTED
    assert memory.failure_code is SemanticProjectionFailureCode.ADAPTER_NOT_IMPLEMENTED
    assert memory.projection is None

"""
=============================================================================
文件：tests/test_semantic_collaboration_claim_graph.py
作用：验证受限语义协作 DAG M12 Claim Graph 结构投影与结构门禁。
范围：覆盖 Turn Intent 内嵌、verified / partial graph、旧版本阻断、
      evidence pending 阻断、缺 intent 阻断与不做语义重复判断边界。
说明：本测试使用进程内 M11 仓储替身，不访问数据库、LiteLLM、Temporal、
      OPA 或任何 input_preprocessing 历史 experiment runner。
=============================================================================
"""

from __future__ import annotations

import asyncio

from tests.test_semantic_collaboration_artifact_store import (
    FakeArtifactRepository,
    _repair_runner,
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
    PostgresSemanticArtifactStore,
    SemanticArtifactGate,
    SemanticClaimGraphBuilder,
    SemanticGraphBuildPolicy,
    SemanticGraphFailureCode,
    SemanticGraphTerminalState,
)


def test_graph_consumes_m11_auto_verified_artifact() -> None:
    """验证 M12 需要 M11 内嵌 intent 且消费自动 verified gate。

    :return: 无返回值。
    """
    repository = FakeArtifactRepository()
    store = PostgresSemanticArtifactStore(repository)
    builder = SemanticClaimGraphBuilder(repository)
    snapshot = _snapshot()
    bundle = _bundle(
        (
            {"claims": ["英短没有呕吐"]},
            _empty_coverage_payload(),
            _empty_faithfulness_payload(),
        )
    )
    reference = asyncio.run(
        store.commit_semantic_turn_result(
            review_bundle=bundle,
            turn_intent_proposal=_turn_intent_proposal(bundle, snapshot),
        ),
    )

    verified = builder.build(reference)

    assert verified.terminal_state is SemanticGraphTerminalState.GRAPH_VERIFIED
    assert verified.graph is not None
    assert verified.graph.intent.source_task_id != bundle.source_task_id
    assert verified.graph.claims[0].proposition == "英短没有呕吐"
    assert verified.graph.claims[0].review_status.value == ("semantic_review_supported")


def test_graph_blocks_historical_evidence_binding_pending_artifact() -> None:
    """验证历史 pending gate 保持 blocked 且不被 M12 提升。

    :return: 无返回值。
    """
    repository = FakeArtifactRepository()
    store = PostgresSemanticArtifactStore(repository)
    builder = SemanticClaimGraphBuilder(repository)
    snapshot = _snapshot()
    bundle = _bundle(
        (
            {"claims": ["英短没有呕吐"]},
            _empty_coverage_payload(),
            _empty_faithfulness_payload(),
        )
    )
    reference = asyncio.run(
        store.commit_semantic_turn_result(
            review_bundle=bundle,
            turn_intent_proposal=_turn_intent_proposal(bundle, snapshot),
        ),
    )
    current = repository.get_current_gate(reference)
    if current is None:
        raise AssertionError("historical pending fixture is invalid")
    repository.gates[reference] = current.model_copy(
        update={
            "to_gate": SemanticArtifactGate.EVIDENCE_BINDING_PENDING,
            "reason_code": "historical_evidence_binding_pending",
        },
    )

    result = builder.build(reference)

    assert result.terminal_state is SemanticGraphTerminalState.GRAPH_BLOCKED
    assert result.failure_code is SemanticGraphFailureCode.GATE_NOT_CONSUMABLE


def test_graph_preserves_partial_gaps_without_generating_followup() -> None:
    """验证 active gap 进入 partial graph 且不生成追问指令。

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
        )
    )
    reference = asyncio.run(
        store.commit_semantic_turn_result(
            review_bundle=bundle,
            turn_intent_proposal=_turn_intent_proposal(bundle, snapshot),
        ),
    )

    result = builder.build(
        reference,
        SemanticGraphBuildPolicy(allow_partial_with_gaps=True),
    )

    assert result.terminal_state is (SemanticGraphTerminalState.GRAPH_PARTIAL_WITH_GAPS)
    assert result.graph is not None
    assert len(result.graph.gaps) == 1
    assert result.graph.gaps[0].required_binding_type.value == ("subject_reference")


def test_graph_blocks_old_artifact_version() -> None:
    """验证 M12 不消费被新 review 版本取代的旧 artifact。

    :return: 无返回值。
    """
    repository = FakeArtifactRepository()
    store = PostgresSemanticArtifactStore(repository)
    builder = SemanticClaimGraphBuilder(repository)
    snapshot = _snapshot()
    bundle = _bundle(
        (
            {"claims": ["英短没有呕吐"]},
            _empty_coverage_payload(),
            _empty_faithfulness_payload(),
        )
    )
    old_reference = asyncio.run(
        store.commit_semantic_turn_result(
            review_bundle=bundle,
            turn_intent_proposal=_turn_intent_proposal(bundle, snapshot),
        ),
    )
    newer_bundle = _bundle(
        (
            {"claims": ["英短没有呕吐"]},
            _empty_coverage_payload(),
            _faithfulness_payload(确定性改变=True),
        )
    )
    asyncio.run(store.commit_review_bundle(newer_bundle))

    result = builder.build(old_reference)

    assert result.terminal_state is SemanticGraphTerminalState.GRAPH_BLOCKED
    assert result.failure_code is SemanticGraphFailureCode.ARTIFACT_STALE


def test_graph_does_not_detect_semantic_duplicate_claims() -> None:
    """验证 M12 不做语义重复判断，只阻止结构重复。

    :return: 无返回值。
    """
    repository = FakeArtifactRepository()
    store = PostgresSemanticArtifactStore(repository)
    builder = SemanticClaimGraphBuilder(repository)
    snapshot = _snapshot()
    bundle = _bundle(
        (
            {
                "claims": [
                    "英短没有呕吐",
                    "英短未呕吐",
                ],
            },
            _empty_coverage_payload(),
            _empty_faithfulness_payload(),
            _empty_faithfulness_payload(),
        )
    )
    reference = asyncio.run(
        store.commit_semantic_turn_result(
            review_bundle=bundle,
            turn_intent_proposal=_turn_intent_proposal(bundle, snapshot),
        ),
    )

    result = builder.build(reference)

    assert result.terminal_state is SemanticGraphTerminalState.GRAPH_VERIFIED
    assert result.graph is not None
    assert len(result.graph.claims) == 2


def test_graph_blocks_artifact_without_embedded_intent() -> None:
    """验证 M12 不回读 M06 proposal 来补 Turn Intent。

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
        )
    )
    reference = asyncio.run(store.commit_review_bundle(bundle))

    assert repository.get_current_gate(reference).to_gate is (
        SemanticArtifactGate.SEMANTIC_REVIEW_SUPPORTED
    )
    result = builder.build(reference)

    assert result.terminal_state is SemanticGraphTerminalState.GRAPH_BLOCKED
    assert result.failure_code is SemanticGraphFailureCode.TURN_INTENT_MISSING


def test_graph_consumes_repaired_artifact_after_review_and_gate() -> None:
    """验证修复后 artifact 必须完成 re-review 与 repair_verified gate。

    :return: 无返回值。
    """
    from tests.test_semantic_collaboration_repair_planner import _planner

    repository = FakeArtifactRepository()
    store = PostgresSemanticArtifactStore(repository)
    builder = SemanticClaimGraphBuilder(repository)
    snapshot = _snapshot()
    bundle = _bundle(
        (
            {"claims": ["英短精神状态被否认"]},
            _empty_coverage_payload(),
            _faithfulness_payload(正常状态误写为否认=True),
        )
    )
    base_reference = asyncio.run(
        store.commit_semantic_turn_result(
            review_bundle=bundle,
            turn_intent_proposal=_turn_intent_proposal(bundle, snapshot),
        ),
    )
    plan = _planner().plan(bundle)
    repair_result = asyncio.run(
        _repair_runner(store, snapshot).repair(
            plan,
            bundle,
        ),
    )
    assert repair_result.patch_set is not None
    assert repair_result.preview is not None
    patched_reference = asyncio.run(
        store.commit(
            repair_result.patch_set,
            repair_result.preview,
        ),
    )
    patched_result = builder.build(patched_reference)
    assert patched_result.failure_code is (
        SemanticGraphFailureCode.REPAIR_VERIFICATION_PENDING
    )

    reviewed_bundle = _bundle(
        (
            {"claims": ["英短精神状态正常"]},
            _empty_coverage_payload(),
            _empty_faithfulness_payload(),
        )
    ).model_copy(
        update={"source_proposal_digest": bundle.source_proposal_digest},
    )
    reviewed_reference = asyncio.run(
        store.commit_review_bundle(reviewed_bundle),
    )
    result = builder.build(reviewed_reference)

    assert result.terminal_state is SemanticGraphTerminalState.GRAPH_VERIFIED
    assert result.graph is not None
    assert result.graph.repair is not None
    assert result.graph.repair.base_artifact_reference == base_reference
    assert result.graph.claims[0].proposition == "英短精神状态正常"

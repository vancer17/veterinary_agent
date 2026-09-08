"""
=============================================================================
文件：tests/test_semantic_collaboration_preprocessing.py
作用：验证受限语义协作 DAG 的问诊预处理主链闭合契约。
范围：覆盖 M02 快照存档、M04 clarification_required 终态、首轮来源边界、
      可信宠物画像投影以及 M06～M13 的 verified / partial / repair 端到端替身链路。
说明：本测试不访问 PostgreSQL、LiteLLM、Temporal、问诊状态或临床安全领域。
=============================================================================
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from tests.test_semantic_collaboration_artifact_store import FakeArtifactRepository
from tests.test_semantic_collaboration_review import (
    SequentialReviewTransport,
    _empty_coverage_payload,
    _empty_faithfulness_payload,
    _faithfulness_payload,
)
from tests.test_semantic_collaboration_turn_snapshot import (
    StubPetContextReader,
    _budget,
    _request,
)
from vet_agent import TrustedIdentity
from vet_agent.repositories import SessionBinding, VerifiedPetProfile
from vet_agent.semantic_collaboration import (
    CLAIM_COVERAGE_REVIEW_SPEC,
    CLAIM_FAITHFULNESS_REVIEW_SPEC,
    CLAIM_INVENTORY_REPAIR_SPEC,
    CLAIM_INVENTORY_SPEC,
    CLAIM_PROPOSITION_REPAIR_SPEC,
    TURN_INTENT_SPEC,
    DAGTaskExecutionResult,
    DAGTaskTerminalState,
    InMemorySemanticTurnSnapshotStore,
    PersistentTurnSnapshotReader,
    SemanticArtifactGate,
    SemanticConsultationPreprocessingRequest,
    SemanticGenerationModelPolicy,
    SemanticGenerationModelRule,
    SemanticPreprocessingExecutionState,
    SemanticPreprocessingFailureCode,
    TurnSnapshotBuilder,
    TurnSnapshotSourceRequest,
    TurnSnapshotSourceScope,
    TurnSnapshotSourceUnavailableError,
    build_semantic_consultation_preprocessing_service,
    semantic_dag_run_id,
)
from vet_agent.semantic_preprocessing import (
    FirstTurnOnlyBoundedHistoryReader,
    FirstTurnOnlyVerifiedPriorFactReader,
    ScopeTrustedPetContextReader,
)


def _turn_intent_payload() -> dict[str, object]:
    """构造合法 Turn Intent 模型输出。

    :return: 返回七个固定布尔意图字段。
    """
    return {
        "answer_now": False,
        "wants_triage": False,
        "correction": False,
        "clarification_request": False,
        "fact_statement_present": True,
        "question_present": False,
        "report_context_present": False,
    }


@dataclass
class StubScopeRepository:
    """提供固定服务端宠物画像与会话绑定的范围仓储替身。"""

    profile: VerifiedPetProfile | None
    binding: SessionBinding | None

    def get_pet_profile(
        self,
        identity: TrustedIdentity,
    ) -> VerifiedPetProfile | None:
        """返回固定宠物画像。

        :param identity: 可信范围。
        :return: 返回测试画像。
        """
        return self.profile

    def get_session_binding(
        self,
        session_id: str,
    ) -> SessionBinding | None:
        """返回固定会话绑定。

        :param session_id: 会话标识。
        :return: 返回测试绑定。
        """
        return self.binding


def _model_policy(*specs: Any) -> SemanticGenerationModelPolicy:
    """构造覆盖指定 SKILL 的精确模型策略。

    :param specs: 当前策略需要覆盖的 SkillSpec 集合。
    :return: 返回无 fallback 的结构化模型策略。
    """
    return SemanticGenerationModelPolicy(
        rules=tuple(
            SemanticGenerationModelRule(
                skill_id=spec.skill_id,
                skill_version=spec.skill_version,
                model="qwen-plus",
            )
            for spec in specs
        ),
    )


def _snapshot_builder() -> TurnSnapshotBuilder:
    """构造首轮语义预处理使用的 M02 构建器。

    :return: 返回使用显式首轮边界的 TurnSnapshotBuilder。
    """
    return TurnSnapshotBuilder(
        history_reader=FirstTurnOnlyBoundedHistoryReader(),
        prior_fact_reader=FirstTurnOnlyVerifiedPriorFactReader(),
        pet_context_reader=StubPetContextReader(),
        budget=_budget(),
    )


def _service(
    transport: SequentialReviewTransport,
    repository: FakeArtifactRepository,
):
    """组装进程内语义预处理主链。

    :param transport: 顺序结构化模型传输替身。
    :param repository: 进程内 M11 权威仓储替身。
    :return: 返回语义预处理应用服务。
    """
    return build_semantic_consultation_preprocessing_service(
        snapshot_builder=_snapshot_builder(),
        snapshot_store=InMemorySemanticTurnSnapshotStore(),
        artifact_repository=repository,
        transport=transport,
        generation_model_policy=_model_policy(
            TURN_INTENT_SPEC,
            CLAIM_INVENTORY_SPEC,
        ),
        review_model_policy=_model_policy(
            CLAIM_COVERAGE_REVIEW_SPEC,
            CLAIM_FAITHFULNESS_REVIEW_SPEC,
        ),
        repair_model_policy=_model_policy(
            CLAIM_INVENTORY_REPAIR_SPEC,
            CLAIM_PROPOSITION_REPAIR_SPEC,
        ),
    )


def _run(
    payloads: tuple[dict[str, object], ...],
) -> tuple[Any, FakeArtifactRepository]:
    """执行一次进程内语义预处理主链。

    :param payloads: 按模型调用顺序排列的固定输出。
    :return: 返回主链结果和 M11 仓储替身。
    """
    transport = SequentialReviewTransport(payloads=payloads)
    repository = FakeArtifactRepository()
    result = asyncio.run(
        _service(transport, repository).preprocess(
            SemanticConsultationPreprocessingRequest(
                snapshot_request=_request("英短精神状态正常，没有呕吐。"),
            ),
        ),
    )
    return result, repository


def test_turn_snapshot_store_is_immutable_and_readable() -> None:
    """验证快照可幂等保存、按 digest 读取且 scope 冲突不被覆盖。"""
    store = InMemorySemanticTurnSnapshotStore()
    built = asyncio.run(_snapshot_builder().build(_request()))

    store.save(built.snapshot, scope=_request().scope)
    store.save(built.snapshot, scope=_request().scope)

    reader = PersistentTurnSnapshotReader(store)
    assert asyncio.run(reader.load(built.snapshot.context_digest)) == built.snapshot

    changed_scope = TurnSnapshotSourceScope(
        user_id="user-2",
        session_id="session-1",
        pet_id="pet-1",
    )
    with pytest.raises(Exception, match="context digest conflict"):
        store.save(built.snapshot, scope=changed_scope)


def test_turn_snapshot_store_rejects_same_turn_with_different_digest() -> None:
    """验证同一 turn_id 不能写入不同 snapshot digest。"""
    store = InMemorySemanticTurnSnapshotStore()
    first = asyncio.run(_snapshot_builder().build(_request("第一回合。")))
    second = asyncio.run(_snapshot_builder().build(_request("不同内容。")))
    changed = second.snapshot.model_copy(
        update={"turn_id": first.snapshot.turn_id},
    )

    store.save(first.snapshot, scope=_request().scope)
    with pytest.raises(Exception, match="turn id conflict"):
        store.save(changed, scope=_request().scope)


def test_first_turn_sources_fail_fast_for_later_turns() -> None:
    """验证多轮历史与历史事实读模型未就绪时不得伪装为空集合。"""
    request = TurnSnapshotSourceRequest(
        scope=TurnSnapshotSourceScope(
            user_id="user-1",
            session_id="session-1",
            pet_id="pet-1",
        ),
        turn_id="turn-2",
        turn_index=1,
    )

    with pytest.raises(TurnSnapshotSourceUnavailableError):
        asyncio.run(FirstTurnOnlyBoundedHistoryReader().read(request))
    with pytest.raises(TurnSnapshotSourceUnavailableError):
        asyncio.run(FirstTurnOnlyVerifiedPriorFactReader().read(request))


def test_preprocessing_multi_turn_memory_source_is_explicit_failure() -> None:
    """验证多轮记忆读模型未就绪时主链失败而不生成空投影。"""
    transport = SequentialReviewTransport(payloads=())
    repository = FakeArtifactRepository()
    snapshot_request = _request("第二轮输入。").model_copy(
        update={"turn_id": "turn-2", "turn_index": 1},
    )

    result = asyncio.run(
        _service(transport, repository).preprocess(
            SemanticConsultationPreprocessingRequest(
                snapshot_request=snapshot_request,
            ),
        ),
    )

    assert result.execution_state is SemanticPreprocessingExecutionState.FAILED
    assert result.failure_code is SemanticPreprocessingFailureCode.TURN_SNAPSHOT_FAILED
    assert result.consultation_projection is None


def test_scope_reader_projects_only_whitelist_pet_profile() -> None:
    """验证可信宠物上下文只投影白名单字段并执行范围匹配。"""
    scope = TurnSnapshotSourceScope(
        user_id="user-1",
        session_id="session-1",
        pet_id="pet-1",
    )
    repository = StubScopeRepository(
        profile=VerifiedPetProfile(
            user_id="user-1",
            pet_id="pet-1",
            profile={
                "species": "cat",
                "breed": "British Shorthair",
                "age": "2 years",
                "weight_kg": 4.5,
                "sex": "female",
                "neutered": True,
                "name": "不应进入语义上下文",
            },
            is_active=True,
        ),
        binding=SessionBinding(
            session_id="session-1",
            user_id="user-1",
            pet_id="pet-1",
        ),
    )
    request = TurnSnapshotSourceRequest(
        scope=scope,
        turn_id="turn-1",
        turn_index=0,
    )

    context = asyncio.run(ScopeTrustedPetContextReader(repository).read(request))

    assert context.profile.species == "cat"
    assert context.profile.breed == "British Shorthair"
    assert context.profile.age_text == "2 years"
    assert context.profile.weight_kg == 4.5
    assert context.profile.sex == "female"
    assert context.profile.neutered is True
    assert not hasattr(context.profile, "name")

    broken = StubScopeRepository(
        profile=None,
        binding=repository.binding,
    )
    with pytest.raises(TurnSnapshotSourceUnavailableError):
        asyncio.run(ScopeTrustedPetContextReader(broken).read(request))


def test_clarification_required_is_artifact_terminal_state() -> None:
    """验证 clarification_required 是携带 artifact 的合法业务终态。"""
    result = DAGTaskExecutionResult(
        task_id="claim-inventory-task",
        terminal_state=DAGTaskTerminalState.CLARIFICATION_REQUIRED,
        artifact_reference=f"semantic-artifact://{'0' * 64}/1",
    )

    assert result.terminal_state.requires_artifact() is True
    assert result.terminal_state.is_dependency_success() is False
    with pytest.raises(ValueError):
        DAGTaskExecutionResult(
            task_id="claim-inventory-task",
            terminal_state=DAGTaskTerminalState.CLARIFICATION_REQUIRED,
        )


def test_preprocessing_supported_claims_reach_consultation_projection() -> None:
    """验证 supported claims 经 M11/M12/M13 输出问诊投影。"""
    result, repository = _run(
        (
            _turn_intent_payload(),
            {"claims": ["英短精神状态正常", "英短没有呕吐"]},
            _empty_coverage_payload(),
            _empty_faithfulness_payload(),
            _empty_faithfulness_payload(),
        ),
    )

    assert result.execution_state is (
        SemanticPreprocessingExecutionState.COMPLETED_VERIFIED
    )
    assert result.failure_code is None
    assert result.consultation_projection is not None
    assert [
        fact.proposition for fact in result.consultation_projection.facts
    ] == ["英短精神状态正常", "英短没有呕吐"]
    assert result.artifact_version == 1
    assert result.run_id == semantic_dag_run_id(result.plan_id or "")
    assert repository.gates[result.artifact_reference or ""].to_gate is (
        SemanticArtifactGate.VERIFIED
    )


def test_preprocessing_preserves_clarification_as_partial_graph() -> None:
    """验证 clarification gap 被保留为 partial graph 而不是强制追问。"""
    result, _ = _run(
        (
            _turn_intent_payload(),
            {"claims": ["它的精神状态不明"]},
            _empty_coverage_payload(),
            _faithfulness_payload(指代对象不明=True),
        ),
    )

    assert result.execution_state is (
        SemanticPreprocessingExecutionState.COMPLETED_PARTIAL_WITH_GAPS
    )
    assert result.consultation_projection is not None
    assert result.consultation_projection.clarification_gaps
    assert (
        result.consultation_projection.clarification_gaps[0].status.value
        == "active"
    )


def test_preprocessing_repairs_then_re_reviews_before_projection() -> None:
    """验证 M10 patch 后必须完成 M08 re-review 才能输出问诊投影。"""
    result, repository = _run(
        (
            _turn_intent_payload(),
            {"claims": ["英短精神状态被否认"]},
            _empty_coverage_payload(),
            _faithfulness_payload(正常状态误写为否认=True),
            {"proposition": "英短精神状态正常"},
            _empty_coverage_payload(),
            _empty_faithfulness_payload(),
        ),
    )

    assert result.execution_state is (
        SemanticPreprocessingExecutionState.COMPLETED_VERIFIED
    )
    assert result.artifact_version == 3
    assert result.consultation_projection is not None
    assert (
        result.consultation_projection.facts[0].proposition
        == "英短精神状态正常"
    )
    assert repository.gates[result.artifact_reference or ""].to_gate is (
        SemanticArtifactGate.REPAIR_VERIFIED
    )


def test_preprocessing_empty_inventory_keeps_structured_empty_reason() -> None:
    """验证合法空图保留 no_explicit_fact 而不是转成失败或空事实列表。"""
    result, _ = _run(
        (
            _turn_intent_payload(),
            {"claims": []},
            _empty_coverage_payload(),
        ),
    )

    assert result.execution_state is (
        SemanticPreprocessingExecutionState.COMPLETED_VERIFIED
    )
    assert result.consultation_projection is not None
    assert result.consultation_projection.facts == ()
    assert result.consultation_projection.empty_reason is not None
    assert result.consultation_projection.empty_reason.value == "no_explicit_fact"


def test_preprocessing_blocked_generation_does_not_return_empty_facts() -> None:
    """验证 M07 blocked 时不生成 M13 空投影。"""
    result, _ = _run(
        (
            _turn_intent_payload(),
            {"claims": ["重复claim", "重复claim"]},
        ),
    )

    assert result.execution_state is SemanticPreprocessingExecutionState.BLOCKED
    assert result.failure_code is SemanticPreprocessingFailureCode.GENERATION_BLOCKED
    assert result.consultation_projection is None

"""
=============================================================================
文件：tests/test_semantic_collaboration_plan.py
作用：验证受限语义协作 DAG M03 确定性 Root Plan Compiler 与 Validator。
范围：覆盖生产策略闭合、并行根任务展开、无 claim 数量预估、canonical 身份、
      上下文绑定、预算失败、非法任务、依赖环与 schema 不匹配。
说明：本测试不依赖数据库、LiteLLM、OPA、Mem0、规划 LLM 或任何
      input_preprocessing 历史 experiment runner。
=============================================================================
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from tests.test_semantic_collaboration_turn_snapshot import (
    StubHistoryReader,
    StubPetContextReader,
    StubPriorFactReader,
    _budget,
    _request,
)
from vet_agent.semantic_collaboration import (
    PLAN_IR_VERSION,
    PRODUCTION_PLAN_POLICY_ID,
    DeterministicPlanCompiler,
    PlanDependency,
    PlanDependencyRule,
    PlanDependencyScope,
    PlanEnvelope,
    PlanEnvelopeKind,
    PlanIR,
    PlanPolicySpec,
    PlanValidationFailureCode,
    PlanValidator,
    TurnSnapshot,
    TurnSnapshotBuilder,
    build_production_plan_policy,
    build_production_skill_catalog,
)


def _snapshot() -> TurnSnapshot:
    """构造用于 M03 测试的当前回合 TurnSnapshot。

    :return: 返回成功构建并通过 digest 校验的不可变快照。
    """
    builder = TurnSnapshotBuilder(
        history_reader=StubHistoryReader(),
        prior_fact_reader=StubPriorFactReader(),
        pet_context_reader=StubPetContextReader(),
        budget=_budget(),
    )
    return asyncio.run(builder.build(_request())).snapshot


def _compiler() -> DeterministicPlanCompiler:
    """构造绑定生产目录与策略的测试编译器。

    :return: 返回可执行确定性编译的 M03 编译器。
    """
    catalog = build_production_skill_catalog()
    return DeterministicPlanCompiler(
        registry=catalog.registry(),
        policy=build_production_plan_policy(catalog.registry()),
    )


def _validator() -> PlanValidator:
    """构造绑定生产目录与策略的测试校验器。

    :return: 返回可输出显式终态的 M03 计划校验器。
    """
    catalog = build_production_skill_catalog()
    registry = catalog.registry()
    return PlanValidator(
        registry=registry,
        policy=build_production_plan_policy(registry),
    )


def _with_plan_id(plan: PlanIR) -> PlanIR:
    """按篡改后的权威字段重算计划身份。

    :param plan: 已修改权威字段但尚未重算 plan_id 的计划。
    :return: 返回携带新 canonical digest 的计划变体。
    """
    return plan.model_copy(update={"plan_id": plan.plan_digest()})


def _failure_codes(plan: PlanIR, snapshot: TurnSnapshot) -> set[str]:
    """读取计划校验失败码集合。

    :param plan: 待校验的计划。
    :param snapshot: 当前回合权威 TurnSnapshot。
    :return: 返回稳定失败码字符串集合。
    """
    result = _validator().validate(plan, snapshot)
    return {failure.code.value for failure in result.failures}


def test_production_plan_policy_is_closed_to_registered_root_skills() -> None:
    """验证生产 PlanPolicy 只包含两个可执行并行根任务。

    :return: 无返回值。
    """
    catalog = build_production_skill_catalog()
    policy = build_production_plan_policy(catalog.registry())
    active_rules = [rule for rule in policy.skills if rule.requirement.value == "always"]

    assert policy.policy_id == PRODUCTION_PLAN_POLICY_ID
    assert policy.policy_version == "3.0.0"
    assert policy.max_task_count == 2
    assert [rule.skill_id for rule in active_rules] == [
        "turn_intent",
        "claim_inventory",
    ]
    assert all(rule.depends_on == () for rule in active_rules)
    assert policy.skills[-1].requirement.value == "forbidden"


def test_plan_policy_rejects_invalid_budget_and_dependency_cycle() -> None:
    """验证生产策略自身阻止预算不足与静态依赖环。

    :return: 无返回值。
    """
    catalog = build_production_skill_catalog()
    policy = build_production_plan_policy(catalog.registry())

    with pytest.raises(ValidationError, match="cannot cover required tasks"):
        PlanPolicySpec(
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            max_task_count=1,
            skills=policy.skills,
        )

    cyclic_first_rule = policy.skills[0].model_copy(
        update={
            "depends_on": (
                PlanDependencyRule(
                    dependency_skill_id="claim_inventory",
                    dependency_scope=PlanDependencyScope.ROOT_ENVELOPE,
                ),
            ),
        },
    )
    cyclic_second_rule = policy.skills[1].model_copy(
        update={
            "depends_on": (
                PlanDependencyRule(
                    dependency_skill_id="turn_intent",
                    dependency_scope=PlanDependencyScope.ROOT_ENVELOPE,
                ),
            ),
        },
    )
    cyclic_skills = (
        cyclic_first_rule,
        cyclic_second_rule,
        *policy.skills[2:],
    )

    with pytest.raises(ValidationError, match="contains a cycle"):
        PlanPolicySpec(
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            max_task_count=policy.max_task_count,
            skills=cyclic_skills,
        )


def test_compiler_expands_parallel_root_plan_without_claim_estimate() -> None:
    """验证初始计划不调用模型、不预估 claim 数量且根任务可并行。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    compiler = _compiler()
    plan = compiler.compile(snapshot)
    result = _validator().validate(plan, snapshot)

    assert result.validated_plan is not None
    assert result.failures == ()
    assert plan.plan_version == PLAN_IR_VERSION
    assert plan.plan_id == plan.plan_digest()
    assert [envelope.envelope_id for envelope in plan.envelopes] == ["turn_root"]
    assert [task.skill_id for task in plan.tasks] == [
        "turn_intent",
        "claim_inventory",
    ]
    assert all(task.depends_on == () for task in plan.tasks)
    assert plan.dependencies == ()
    assert compiler.compile(snapshot).plan_id == plan.plan_id


def test_validator_blocks_snapshot_digest_mismatch() -> None:
    """验证计划绑定旧 TurnSnapshot digest 时进入 blocked 终态。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    plan = _compiler().compile(snapshot)
    tampered = _with_plan_id(
        plan.model_copy(update={"snapshot_digest": "0" * 64}),
    )

    assert _failure_codes(tampered, snapshot) >= {
        PlanValidationFailureCode.SNAPSHOT_DIGEST_MISMATCH.value,
        PlanValidationFailureCode.CONTEXT_POLICY_VIOLATION.value,
    }


def test_validator_blocks_unknown_skill() -> None:
    """验证未注册 SKILL 不会被目录模糊匹配或默认任务替代。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    plan = _compiler().compile(snapshot)
    tasks = (
        plan.tasks[0].model_copy(update={"skill_id": "unknown_skill"}),
        *plan.tasks[1:],
    )
    tampered = _with_plan_id(plan.model_copy(update={"tasks": tasks}))

    assert (
        PlanValidationFailureCode.UNKNOWN_SKILL_SELECTED.value
        in _failure_codes(tampered, snapshot)
    )


def test_validator_blocks_preallocated_claim_envelopes() -> None:
    """验证初始 Root Plan 不能在 Claim Inventory 前预分配 claim envelope。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    plan = _compiler().compile(snapshot)
    envelopes = (
        *plan.envelopes,
        PlanEnvelope(
            envelope_id="claim_env_0000",
            kind=PlanEnvelopeKind.CLAIM,
            parent_envelope_id="turn_root",
            ordinal=0,
        ),
    )
    tampered = _with_plan_id(plan.model_copy(update={"envelopes": envelopes}))

    assert (
        PlanValidationFailureCode.ENVELOPE_POLICY_VIOLATION.value
        in _failure_codes(tampered, snapshot)
    )


def test_validator_blocks_output_schema_mismatch() -> None:
    """验证任务输出 schema 引用与 SkillCatalog 不一致时被阻断。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    plan = _compiler().compile(snapshot)
    changed_schema = plan.tasks[0].expected_output_schema.model_copy(
        update={"schema_digest": "0" * 64},
    )
    tasks = (
        plan.tasks[0].model_copy(update={"expected_output_schema": changed_schema}),
        *plan.tasks[1:],
    )
    tampered = _with_plan_id(plan.model_copy(update={"tasks": tasks}))

    assert (
        PlanValidationFailureCode.OUTPUT_SCHEMA_MISMATCH.value
        in _failure_codes(tampered, snapshot)
    )


def test_validator_blocks_dependency_cycle() -> None:
    """验证策略外的循环依赖无法进入 M04 调度器。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    plan = _compiler().compile(snapshot)
    turn_intent = plan.tasks[0]
    claim_inventory = plan.tasks[1]
    tasks = (
        turn_intent.model_copy(update={"depends_on": (claim_inventory.task_id,)}),
        claim_inventory.model_copy(update={"depends_on": (turn_intent.task_id,)}),
    )
    dependencies = (
        PlanDependency(
            task_id=turn_intent.task_id,
            depends_on_task_id=claim_inventory.task_id,
        ),
        PlanDependency(
            task_id=claim_inventory.task_id,
            depends_on_task_id=turn_intent.task_id,
        ),
    )
    tampered = _with_plan_id(
        plan.model_copy(update={"tasks": tasks, "dependencies": dependencies}),
    )

    assert (
        PlanValidationFailureCode.DEPENDENCY_CYCLE_DETECTED.value
        in _failure_codes(tampered, snapshot)
    )

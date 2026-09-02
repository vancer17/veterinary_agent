"""
=============================================================================
文件：tests/test_semantic_collaboration_plan.py
作用：验证受限语义协作 DAG M03 PlanSelection、Plan Compiler 与 Plan Validator。
范围：覆盖生产策略闭合、确定性任务展开、静态依赖、canonical 身份、上下文
      绑定、预算失败、非法任务、依赖环、schema 不匹配与结构化模型适配失败。
说明：本测试不依赖数据库、LiteLLM、OPA、Mem0 或任何 input_preprocessing
      历史 experiment runner。
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
    LLMPlanSelector,
    PlanCompilationError,
    PlanDependency,
    PlanDependencyRule,
    PlanDependencyScope,
    PlanIR,
    PlanModelClientError,
    PlanPolicySpec,
    PlanSelection,
    PlanSelectionSchemaError,
    PlanValidationFailureCode,
    PlanValidator,
    StructuredPlanModelClient,
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


def _selection(
    *,
    claim_envelope_count: int = 2,
    run_participant_phrase: bool = True,
) -> PlanSelection:
    """构造测试用最小固定字段计划选择。

    :param claim_envelope_count: claim 执行 envelope 数量。
    :param run_participant_phrase: 是否启用 participant phrase lane。
    :return: 返回通过契约一致性的 PlanSelection。
    """
    return PlanSelection(
        claim_envelope_count=claim_envelope_count,
        run_statement_semantics=claim_envelope_count > 0,
        run_participant_phrase=run_participant_phrase and claim_envelope_count > 0,
        run_temporal_phrase=False,
        run_measurement_phrase=False,
        run_canonical_descriptor=False,
    )


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
    :param snapshot: 当前回合 TurnSnapshot。
    :return: 返回稳定失败码字符串集合。
    """
    result = _validator().validate(plan, snapshot)
    return {failure.code.value for failure in result.failures}


def _dependency_key(dependency: PlanDependency) -> tuple[str, str]:
    """读取测试依赖边的排序键。

    :param dependency: 任意计划依赖边对象。
    :return: 返回任务标识与被依赖任务标识组成的元组。
    """
    return dependency.task_id, dependency.depends_on_task_id


def test_production_plan_policy_is_closed_to_registered_skills() -> None:
    """验证生产 PlanPolicy 绑定精确版本且不开放初始修复任务。

    :return: 无返回值。
    """
    catalog = build_production_skill_catalog()
    policy = build_production_plan_policy(catalog.registry())

    assert policy.policy_id == PRODUCTION_PLAN_POLICY_ID
    assert policy.max_claim_envelope_count == 8
    assert len(policy.skills) == 10
    assert all(policy.skills[0].skill_id == "turn_intent" for _ in (0,))
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
            max_claim_envelope_count=policy.max_claim_envelope_count,
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
    cyclic_skills = (cyclic_first_rule, *policy.skills[1:])

    with pytest.raises(ValidationError, match="contains a cycle"):
        PlanPolicySpec(
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            max_claim_envelope_count=policy.max_claim_envelope_count,
            max_task_count=policy.max_task_count,
            skills=cyclic_skills,
        )


def test_compiler_expands_canonical_plan_and_validator_passes() -> None:
    """验证最小模型选择可确定性展开为通过准入的完整 Plan IR。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    compiler = _compiler()
    plan = compiler.compile(_selection(), snapshot)
    result = _validator().validate(plan, snapshot)

    assert result.validated_plan is not None
    assert result.failures == ()
    assert plan.plan_version == PLAN_IR_VERSION
    assert plan.plan_id == plan.plan_digest()
    assert tuple(envelope.envelope_id for envelope in plan.envelopes) == (
        "turn_root",
        "claim_env_0000",
        "claim_env_0001",
    )
    assert len(plan.tasks) == 6
    assert {task.skill_id for task in plan.tasks} == {
        "turn_intent",
        "claim_inventory",
        "statement_semantics",
        "participant_phrase",
    }
    assert plan.dependencies == tuple(
        sorted(
            plan.dependencies,
            key=_dependency_key,
        ),
    )
    assert compiler.compile(_selection(), snapshot).plan_id == plan.plan_id


def test_zero_claim_selection_compiles_only_required_root_tasks() -> None:
    """验证零 claim 选择只展开必选根任务且仍通过准入。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    plan = _compiler().compile(_selection(claim_envelope_count=0), snapshot)
    result = _validator().validate(plan, snapshot)

    assert result.validated_plan is not None
    assert tuple(task.skill_id for task in plan.tasks) == (
        "turn_intent",
        "claim_inventory",
    )
    assert plan.dependencies[0].depends_on_task_id.startswith("turn-1:turn_root:")


def test_plan_selection_rejects_claim_lanes_without_envelope() -> None:
    """验证 claim lane 与零 envelope 的矛盾不会进入编译层。

    :return: 无返回值。
    """
    with pytest.raises(ValidationError, match="claim lanes cannot run"):
        PlanSelection(
            claim_envelope_count=0,
            run_statement_semantics=True,
            run_participant_phrase=False,
            run_temporal_phrase=False,
            run_measurement_phrase=False,
            run_canonical_descriptor=False,
        )


def test_plan_budget_failure_is_explicit() -> None:
    """验证超过策略预算的计划选择显式失败且不截断。

    :return: 无返回值。
    """
    with pytest.raises(PlanCompilationError, match="claim envelope count") as error:
        _compiler().compile(
            _selection(claim_envelope_count=9),
            _snapshot(),
        )

    assert error.value.failure_code == "plan_budget_exceeded"


def test_validator_blocks_snapshot_digest_mismatch() -> None:
    """验证计划绑定旧 TurnSnapshot digest 时进入 blocked 终态。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    plan = _compiler().compile(_selection(claim_envelope_count=0), snapshot)
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
    plan = _compiler().compile(_selection(claim_envelope_count=0), snapshot)
    tasks = (
        plan.tasks[0].model_copy(update={"skill_id": "unknown_skill"}),
        *plan.tasks[1:],
    )
    tampered = _with_plan_id(plan.model_copy(update={"tasks": tasks}))

    assert (
        PlanValidationFailureCode.UNKNOWN_SKILL_SELECTED.value
        in _failure_codes(tampered, snapshot)
    )


def test_validator_blocks_output_schema_mismatch() -> None:
    """验证任务输出 schema 引用与 SkillCatalog 不一致时被阻断。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    plan = _compiler().compile(_selection(claim_envelope_count=0), snapshot)
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
    """验证静态策略外的循环依赖无法进入 M04 调度器。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    plan = _compiler().compile(_selection(claim_envelope_count=0), snapshot)
    turn_intent = plan.tasks[0]
    claim_inventory = plan.tasks[1]
    tasks = (
        turn_intent.model_copy(update={"depends_on": (claim_inventory.task_id,)}),
        claim_inventory,
    )
    dependencies = (
        *plan.dependencies,
        type(plan.dependencies[0])(
            task_id=turn_intent.task_id,
            depends_on_task_id=claim_inventory.task_id,
        ),
    )
    tampered = _with_plan_id(
        plan.model_copy(update={"tasks": tasks, "dependencies": dependencies}),
    )

    assert (
        PlanValidationFailureCode.DEPENDENCY_CYCLE_DETECTED.value
        in _failure_codes(tampered, snapshot)
    )


class FixedPlanModelClient:
    """返回固定 PlanSelection 的结构化模型测试替身。

    :return: 无返回值；该替身不访问 LiteLLM 或外部服务。
    """

    async def chat_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_model: type[PlanSelection],
        model: str | None,
        temperature: float,
    ) -> PlanSelection:
        """返回固定计划选择并检查调用契约。

        :param messages: 模型消息列表。
        :param response_model: 必须为 PlanSelection 的响应契约。
        :param model: 可选模型名称。
        :param temperature: 结构化调用温度。
        :return: 返回固定 PlanSelection。
        """
        assert response_model is PlanSelection
        assert temperature == 0.0
        assert "不要输出任务图" in messages[0]["content"]
        return _selection(claim_envelope_count=1, run_participant_phrase=False)


class FailingPlanModelClient:
    """模拟结构化模型网关调用失败。

    :return: 无返回值；该替身用于验证无隐式回退。
    """

    async def chat_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_model: type[PlanSelection],
        model: str | None,
        temperature: float,
    ) -> PlanSelection:
        """抛出模型网关底层错误。

        :param messages: 模型消息列表。
        :param response_model: PlanSelection 响应契约。
        :param model: 可选模型名称。
        :param temperature: 结构化调用温度。
        :return: 该方法不会返回。
        :raises RuntimeError: 固定抛出模型网关错误。
        """
        raise RuntimeError("model gateway unavailable")


class InvalidPlanModelClient:
    """模拟模型返回缺失字段的非法结构化结果。

    :return: 无返回值；该替身不使用宽松 JSON 修复。
    """

    async def chat_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_model: type[PlanSelection],
        model: str | None,
        temperature: float,
    ) -> PlanSelection:
        """触发 PlanSelection 严格校验失败。

        :param messages: 模型消息列表。
        :param response_model: PlanSelection 响应契约。
        :param model: 可选模型名称。
        :param temperature: 结构化调用温度。
        :return: 该方法不会返回。
        :raises ValidationError: 固定触发严格 schema 失败。
        """
        PlanSelection.model_validate_json("{}")
        raise AssertionError("invalid PlanSelection unexpectedly passed validation")


def _selector(
    client: StructuredPlanModelClient,
) -> LLMPlanSelector:
    """构造测试用任务规划模型选择器。

    :param client: 结构化模型客户端测试替身。
    :return: 返回绑定生产策略的 LLMPlanSelector。
    """
    catalog = build_production_skill_catalog()
    return LLMPlanSelector(
        client=client,
        policy=build_production_plan_policy(catalog.registry()),
    )


def test_plan_selector_returns_only_fixed_field_selection() -> None:
    """验证任务规划模型适配器只返回最小 PlanSelection。

    :return: 无返回值。
    """
    selection = asyncio.run(_selector(FixedPlanModelClient()).select(_snapshot()))

    assert selection.claim_envelope_count == 1
    assert selection.run_statement_semantics is True
    assert selection.run_participant_phrase is False


def test_plan_selector_model_failure_is_explicit() -> None:
    """验证模型调用失败不会触发默认计划或旧抽取器回退。

    :return: 无返回值。
    """
    with pytest.raises(PlanModelClientError, match="model client call failed"):
        asyncio.run(_selector(FailingPlanModelClient()).select(_snapshot()))


def test_plan_selector_schema_failure_is_explicit() -> None:
    """验证非法结构化模型输出不会被宽松 JSON 解析修复。

    :return: 无返回值。
    """
    with pytest.raises(PlanSelectionSchemaError, match="strict schema"):
        asyncio.run(_selector(InvalidPlanModelClient()).select(_snapshot()))

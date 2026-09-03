"""
=============================================================================
文件：tests/test_semantic_collaboration_scheduler.py
作用：验证受限语义协作 DAG M04 Temporal-first 调度实现。
范围：覆盖任务策略投影、投影仓储终态闭合、任务 activity 边界、TODO 端口
      Fail Fast、Temporal workflow / activity 稳定定义和数据库防退化边界。
说明：本测试不启动 Temporal Server，不访问 LiteLLM、OPA、Mem0、问诊状态、
      临床安全或长期记忆。
=============================================================================
"""

from __future__ import annotations

import asyncio
from collections import Counter

import pytest
from sqlalchemy import inspect as sqlalchemy_inspect
from temporalio.exceptions import ApplicationError

from tests.test_semantic_collaboration_turn_snapshot import (
    StubHistoryReader,
    StubPetContextReader,
    StubPriorFactReader,
    _budget,
    _request,
)
from vet_agent.db import (
    SemanticDAGRunProjectionModel,
    SemanticDAGTaskProjectionModel,
)
from vet_agent.semantic_collaboration import (
    DAGExecutionPolicy,
    DAGFrontier,
    DAGRunProjectionInitializeRequest,
    DAGRunStatus,
    DAGTaskExecutionResult,
    DAGTaskTerminalState,
    DeterministicPlanCompiler,
    InMemorySemanticDAGProjectionRepository,
    PlanValidator,
    SemanticTaskExecutionError,
    SemanticTaskExecutionRequest,
    SkillFailureCode,
    TemporalDAGActivityRuntime,
    TemporalTaskActivityInput,
    TODOSemanticTaskExecutor,
    TurnSnapshot,
    TurnSnapshotBuilder,
    ValidatedPlan,
    build_dag_task_policies,
    build_production_plan_policy,
    build_production_skill_catalog,
    evaluate_dag_frontier,
    semantic_dag_run_id,
)


def _snapshot() -> TurnSnapshot:
    """构造用于 M04 测试的当前回合 TurnSnapshot。

    :return: 返回成功构建并通过 digest 校验的不可变快照。
    """
    builder = TurnSnapshotBuilder(
        history_reader=StubHistoryReader(),
        prior_fact_reader=StubPriorFactReader(),
        pet_context_reader=StubPetContextReader(),
        budget=_budget(),
    )
    return asyncio.run(builder.build(_request())).snapshot


def _validated_plan(snapshot: TurnSnapshot) -> ValidatedPlan:
    """构造并通过 M03 校验的权威计划。

    :param snapshot: 当前回合 TurnSnapshot。
    :return: 返回 ValidatedPlan。
    """
    registry = build_production_skill_catalog().registry()
    policy = build_production_plan_policy(registry)
    plan = DeterministicPlanCompiler(
        registry=registry,
        policy=policy,
    ).compile(snapshot)
    result = PlanValidator(
        registry=registry,
        policy=policy,
    ).validate(
        plan,
        snapshot,
    )
    assert result.validated_plan is not None
    return result.validated_plan


def _initialize_request(
    validated_plan: ValidatedPlan,
) -> DAGRunProjectionInitializeRequest:
    """构造投影初始化请求。

    :param validated_plan: 权威计划。
    :return: 返回与 SkillCatalog 策略闭合的初始化请求。
    """
    return DAGRunProjectionInitializeRequest(
        run_id=semantic_dag_run_id(validated_plan.plan.plan_id),
        workflow_id=semantic_dag_run_id(validated_plan.plan.plan_id),
        validated_plan=validated_plan,
        policy=DAGExecutionPolicy(),
        task_policies=build_dag_task_policies(
            registry=build_production_skill_catalog().registry(),
            validated_plan=validated_plan,
        ),
    )


class StubSemanticTaskExecutor:
    """提供按 SKILL 返回显式终值的测试执行端口。

    :param mismatch_result: 是否返回错误任务身份。
    :param first_retryable_failure: 首次返回的可重试失败码。
    :return: 无返回值。
    """

    def __init__(
        self,
        *,
        mismatch_result: bool = False,
        first_retryable_failure: SkillFailureCode | None = None,
    ) -> None:
        """初始化测试执行端口。

        :param mismatch_result: 是否返回错误任务身份。
        :param first_retryable_failure: 首次返回的可重试失败码。
        :return: 无返回值。
        """
        self.mismatch_result = mismatch_result
        self.first_retryable_failure = first_retryable_failure
        self.requests: list[SemanticTaskExecutionRequest] = []
        self.skill_counts: Counter[str] = Counter()

    async def execute(
        self,
        request: SemanticTaskExecutionRequest,
    ) -> DAGTaskExecutionResult:
        """返回当前任务的显式测试结果。

        :param request: Temporal activity 传入的任务请求。
        :return: 返回 verified 或身份不匹配的失败结果。
        """
        self.requests.append(request)
        skill_id = request.task.skill_id
        self.skill_counts[skill_id] += 1
        if (
            self.first_retryable_failure is not None
            and self.skill_counts[skill_id] == 1
        ):
            return DAGTaskExecutionResult(
                task_id=request.task.task_id,
                terminal_state=DAGTaskTerminalState.BLOCKED,
                failure_code=self.first_retryable_failure,
                failure_message="test retryable failure",
            )
        return DAGTaskExecutionResult(
            task_id=(
                "invalid-task-id"
                if self.mismatch_result
                else request.task.task_id
            ),
            terminal_state=(
                DAGTaskTerminalState.BLOCKED
                if self.mismatch_result
                else DAGTaskTerminalState.VERIFIED
            ),
            artifact_reference=None if self.mismatch_result else (
                f"artifact://{skill_id}/{request.task.task_id}"
            ),
            failure_code=(
                SkillFailureCode.VERIFIER_FAILED if self.mismatch_result else None
            ),
            failure_message=(
                "test identity mismatch" if self.mismatch_result else None
            ),
        )


def test_task_policies_are_projected_from_skill_catalog() -> None:
    """验证任务策略由 SkillCatalog 权威失败策略投影。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    validated_plan = _validated_plan(snapshot)
    policies = build_dag_task_policies(
        registry=build_production_skill_catalog().registry(),
        validated_plan=validated_plan,
    )
    policy_map = {
        policy.task_id: policy
        for policy in policies
    }
    turn_intent_task = next(
        task
        for task in validated_plan.plan.tasks
        if task.skill_id == "turn_intent"
    )

    assert {policy.task_id for policy in policies} == {
        task.task_id for task in validated_plan.plan.tasks
    }
    assert policy_map[turn_intent_task.task_id].max_attempts == 2
    assert SkillFailureCode.MODEL_CALL_FAILED in (
        policy_map[turn_intent_task.task_id].retryable_failure_codes
    )


def test_dag_frontier_initially_releases_parallel_root_tasks() -> None:
    """验证无依赖根任务在初始前沿即可并行执行。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    validated_plan = _validated_plan(snapshot)
    task_by_skill = {
        task.skill_id: task
        for task in validated_plan.plan.tasks
    }
    first_frontier = evaluate_dag_frontier(
        validated_plan.plan.tasks,
        {},
    )
    completed_frontier: DAGFrontier = evaluate_dag_frontier(
        validated_plan.plan.tasks,
        {
            task_by_skill["turn_intent"].task_id: (
                DAGTaskTerminalState.VERIFIED
            ),
            task_by_skill["claim_inventory"].task_id: (
                DAGTaskTerminalState.BLOCKED
            ),
        },
    )

    assert first_frontier.ready_task_ids == tuple(
        sorted(
            (
                task_by_skill["claim_inventory"].task_id,
                task_by_skill["turn_intent"].task_id,
            ),
        ),
    )
    assert first_frontier.waiting_task_ids == ()
    assert completed_frontier.ready_task_ids == ()
    assert completed_frontier.waiting_task_ids == ()
    assert completed_frontier.dependency_failures == ()


def test_projection_repository_records_terminal_closure() -> None:
    """验证投影仓储能记录任务结果与依赖失败并闭合 run。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    validated_plan = _validated_plan(snapshot)
    repository = InMemorySemanticDAGProjectionRepository()
    request = _initialize_request(validated_plan)
    initialized = repository.initialize_run(request)
    task_by_skill = {
        task.skill_id: task
        for task in validated_plan.plan.tasks
    }
    turn_intent_result = DAGTaskExecutionResult(
        task_id=task_by_skill["turn_intent"].task_id,
        terminal_state=DAGTaskTerminalState.VERIFIED,
        artifact_reference=(
            f"artifact://turn_intent/{task_by_skill['turn_intent'].task_id}"
        ),
    )
    claim_result = DAGTaskExecutionResult(
        task_id=task_by_skill["claim_inventory"].task_id,
        terminal_state=DAGTaskTerminalState.BLOCKED,
        failure_code=SkillFailureCode.SCHEMA_INVALID,
        failure_message="test blocked claim",
    )

    repository.record_task_result(
        request.run_id,
        turn_intent_result,
    )
    repository.record_task_result(
        request.run_id,
        claim_result,
    )
    final = repository.finish_run(
        request.run_id,
        DAGRunStatus.COMPLETED_WITH_FAILURES,
    )
    terminal_by_skill = {
        task.skill_id: task.terminal_state
        for task in final.tasks
    }

    assert initialized.status == DAGRunStatus.RUNNING
    assert final.status == DAGRunStatus.COMPLETED_WITH_FAILURES
    assert terminal_by_skill["turn_intent"] == DAGTaskTerminalState.VERIFIED
    assert terminal_by_skill["claim_inventory"] == DAGTaskTerminalState.BLOCKED


def test_temporal_task_activity_executes_executor_port() -> None:
    """验证任务 activity 只通过执行端口返回显式结果。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    validated_plan = _validated_plan(snapshot)
    repository = InMemorySemanticDAGProjectionRepository()
    initialize_request = _initialize_request(validated_plan)
    repository.initialize_run(initialize_request)
    executor = StubSemanticTaskExecutor()
    runtime = TemporalDAGActivityRuntime(
        repository=repository,
        executor=executor,
    )
    task = next(
        task
        for task in validated_plan.plan.tasks
        if task.skill_id == "turn_intent"
    )

    result = asyncio.run(
        runtime._execute_task_with_attempt(
            request=TemporalTaskActivityInput(
                run_id=initialize_request.run_id,
                task=task,
                task_policy=next(
                    policy
                    for policy in initialize_request.task_policies
                    if policy.task_id == task.task_id
                ),
                turn_snapshot_digest=snapshot.context_digest,
                dependency_artifacts={},
                semantic_retry_backoff_seconds=0.0,
            ),
            attempt_number=1,
        ),
    )

    assert result.task_id == task.task_id
    assert result.terminal_state == DAGTaskTerminalState.VERIFIED
    assert executor.requests[0].turn_snapshot_digest == snapshot.context_digest


def test_temporal_task_activity_blocks_identity_mismatch() -> None:
    """验证任务 activity 不接受身份不匹配的执行结果。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    validated_plan = _validated_plan(snapshot)
    repository = InMemorySemanticDAGProjectionRepository()
    initialize_request = _initialize_request(validated_plan)
    repository.initialize_run(initialize_request)
    runtime = TemporalDAGActivityRuntime(
        repository=repository,
        executor=StubSemanticTaskExecutor(mismatch_result=True),
    )
    task = next(
        task
        for task in validated_plan.plan.tasks
        if task.skill_id == "turn_intent"
    )

    with pytest.raises(SemanticTaskExecutionError):
        asyncio.run(
            runtime._execute_task_with_attempt(
                request=TemporalTaskActivityInput(
                    run_id=initialize_request.run_id,
                    task=task,
                    task_policy=next(
                        policy
                        for policy in initialize_request.task_policies
                        if policy.task_id == task.task_id
                    ),
                    turn_snapshot_digest=snapshot.context_digest,
                    dependency_artifacts={},
                    semantic_retry_backoff_seconds=0.0,
                ),
                attempt_number=1,
            ),
        )


def test_retryable_semantic_failure_is_delegated_to_temporal() -> None:
    """验证语义可重试失败通过 ApplicationError 交给 Temporal RetryPolicy。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    validated_plan = _validated_plan(snapshot)
    initialize_request = _initialize_request(validated_plan)
    task = next(
        task
        for task in validated_plan.plan.tasks
        if task.skill_id == "turn_intent"
    )
    runtime = TemporalDAGActivityRuntime(
        repository=InMemorySemanticDAGProjectionRepository(),
        executor=StubSemanticTaskExecutor(
            first_retryable_failure=SkillFailureCode.MODEL_CALL_FAILED,
        ),
    )

    with pytest.raises(ApplicationError) as error:
        asyncio.run(
            runtime._execute_task_with_attempt(
                request=TemporalTaskActivityInput(
                    run_id=initialize_request.run_id,
                    task=task,
                    task_policy=next(
                        policy
                        for policy in initialize_request.task_policies
                        if policy.task_id == task.task_id
                    ),
                    turn_snapshot_digest=snapshot.context_digest,
                    dependency_artifacts={},
                    semantic_retry_backoff_seconds=0.0,
                ),
                attempt_number=1,
            ),
        )

    assert error.value.type == "RetryableSemanticTaskFailure"



def test_todo_executor_fails_fast() -> None:
    """验证 M05～M11 空壳端口不会生成伪成功。

    :return: 无返回值。
    """
    with pytest.raises(NotImplementedError):
        asyncio.run(
            TODOSemanticTaskExecutor().execute(
                SemanticTaskExecutionRequest(
                    run_id="a" * 64,
                    attempt_number=1,
                    task=_validated_plan(_snapshot()).plan.tasks[0],
                    turn_snapshot_digest="b" * 64,
                    dependency_artifacts={},
                ),
            ),
        )


def test_temporal_definitions_are_stable_and_projection_tables_have_comments() -> None:
    """验证 Temporal 稳定定义与投影表字段描述。

    :return: 无返回值。
    """
    from vet_agent.semantic_collaboration import SemanticDAGWorkflow

    workflow_definition = SemanticDAGWorkflow.__temporal_workflow_definition
    execute_definition = (
        TemporalDAGActivityRuntime.execute_task.__temporal_activity_definition
    )
    forbidden_columns = {
        "worker_id",
        "lease_until",
        "runtime_state",
        "attempt_count",
        "max_attempts",
    }

    assert workflow_definition.name == "semantic-collaboration-dag.v2"
    assert workflow_definition.sandboxed is False
    assert execute_definition.name == "semantic_dag.execute_task"
    for model in (
        SemanticDAGRunProjectionModel,
        SemanticDAGTaskProjectionModel,
    ):
        table = model.__table__
        assert table.comment
        for column in sqlalchemy_inspect(model).columns:
            assert column.comment, (
                f"{table.name}.{column.name} lacks description"
            )
            assert column.name not in forbidden_columns

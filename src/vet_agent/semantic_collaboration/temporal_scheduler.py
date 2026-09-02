"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/temporal_scheduler.py
作用：实现受限语义协作 DAG M04 的 Temporal-first 生产调度层。
范围：覆盖稳定 workflow 输入、任务 activity、投影 activity、workflow 图推进、
      语义有界重试、依赖失败传播、取消信号、worker 构建与 client 门面。
说明：任务队列、activity 分发、基础设施重试、超时、中断恢复和执行历史
      均由 Temporal 负责；本文件不实现数据库租约或自研任务生命周期。
=============================================================================
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from temporalio import activity, workflow
from temporalio.client import Client, WorkflowHandle
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.exceptions import TimeoutError as TemporalTimeoutError
from temporalio.worker import Worker

from .catalog import SkillRegistry
from .contracts import SkillFailureCode
from .errors import SchedulerError, SemanticTaskExecutionError
from .plan_contracts import PlanTask, ValidatedPlan
from .scheduler_contracts import (
    DAGExecutionPolicy,
    DAGRunProjectionInitializeRequest,
    DAGRunProjectionRecord,
    DAGRunStatus,
    DAGTaskExecutionResult,
    DAGTaskPolicy,
    DAGTaskProjectionRecord,
    DAGTaskTerminalState,
    SemanticTaskExecutionRequest,
    semantic_dag_run_id,
)
from .scheduler_graph import evaluate_dag_frontier
from .scheduler_ports import (
    SemanticDAGProjectionRepository,
    SemanticTaskExecutor,
)


class TemporalDAGWorkflowInput(BaseModel):
    """表示语义协作 DAG workflow 的稳定输入。

    :return: 无返回值；输入进入 Temporal event history，不包含下游领域状态。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run_id: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="由权威 Plan IR 派生的 workflow 稳定标识。",
    )
    workflow_id: str = Field(
        min_length=1,
        max_length=160,
        description="Temporal workflow 标识，与 run id 保持一致。",
    )
    validated_plan: ValidatedPlan = Field(
        description="已通过 M03 全部准入校验的权威计划。",
    )
    policy: DAGExecutionPolicy = Field(
        description="本次 workflow 固化的执行策略。",
    )
    task_policies: tuple[DAGTaskPolicy, ...] = Field(
        description="由 SkillCatalog 失败策略投影出的任务策略集合。",
    )


class TemporalTaskActivityInput(BaseModel):
    """表示单个语义任务 activity 的稳定输入。

    :return: 无返回值；输入只包含任务契约、上下文摘要和上游 artifact 引用。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run_id: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="DAG workflow 稳定标识。",
    )
    task: PlanTask = Field(
        description="权威 PlanTask 快照。",
    )
    task_policy: DAGTaskPolicy = Field(
        description="SkillCatalog 投影出的任务语义重试策略。",
    )
    turn_snapshot_digest: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="任务必须绑定的 TurnSnapshot digest。",
    )
    dependency_artifacts: dict[str, str] = Field(
        description="直接上游成功 artifact 引用映射。",
    )
    semantic_retry_backoff_seconds: float = Field(
        ge=0.0,
        le=30.0,
        description="语义失败交给 Temporal 重试时的下一次尝试延迟。",
    )


class TemporalTaskResultInput(BaseModel):
    """表示任务结果投影 activity 的稳定输入。

    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run_id: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="DAG workflow 稳定标识。",
    )
    result: DAGTaskExecutionResult = Field(
        description="任务端口返回的显式业务终态。",
    )


class TemporalDependencyFailureInput(BaseModel):
    """表示依赖失败投影 activity 的稳定输入。

    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run_id: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="DAG workflow 稳定标识。",
    )
    task_id: str = Field(
        min_length=1,
        max_length=360,
        description="被阻断任务标识。",
    )
    dependency_task_id: str = Field(
        min_length=1,
        max_length=360,
        description="已失败上游任务标识。",
    )


class TemporalFinishRunInput(BaseModel):
    """表示 workflow 终态投影 activity 的稳定输入。

    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run_id: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="DAG workflow 稳定标识。",
    )
    status: DAGRunStatus = Field(
        description="workflow 业务终态。",
    )


def build_dag_task_policies(
    *,
    registry: SkillRegistry,
    validated_plan: ValidatedPlan,
) -> tuple[DAGTaskPolicy, ...]:
    """从 SkillCatalog 权威失败策略投影任务语义重试策略。

    :param registry: 已冻结的 SkillRegistry。
    :param validated_plan: 已通过 M03 校验的权威计划。
    :return: 返回与 Plan IR 任务一一对应的任务策略集合。
    :raises SchedulerError: 任务 SKILL 缺失时抛出。
    """
    policies: list[DAGTaskPolicy] = []
    for task in validated_plan.plan.tasks:
        spec = registry.get(
            task.skill_id,
            task.skill_version,
        )
        if spec is None:
            raise SchedulerError("semantic dag task skill is not registered")
        policies.append(
            DAGTaskPolicy(
                task_id=task.task_id,
                max_attempts=spec.failure_policy.max_attempts,
                retryable_failure_codes=spec.failure_policy.retryable_on,
            ),
        )
    return tuple(policies)


@workflow.defn(
    name="semantic-collaboration-dag.v2",
    sandboxed=False,
)
class SemanticDAGWorkflow:
    """定义受限语义协作 DAG 的 Temporal durable workflow。

    :return: 无返回值；workflow 不直接访问数据库或模型，也不实现任务队列。
    """

    def __init__(self) -> None:
        """初始化 workflow 内的取消信号状态。

        :return: 无返回值；该状态随 Temporal event history 重放恢复。
        """
        self._cancel_requested = False
        self._cancel_message = "semantic dag workflow canceled by signal"
        self._terminal_states: dict[str, DAGTaskTerminalState] = {}
        self._artifact_references: dict[str, str] = {}

    @workflow.run
    async def run(
        self,
        workflow_input: TemporalDAGWorkflowInput,
    ) -> DAGRunProjectionRecord:
        """执行 durable 语义协作 DAG 并返回终态投影。

        :param workflow_input: DAG workflow 稳定输入。
        :return: 返回全部任务显式终态后的 run 投影。
        """
        await workflow.execute_activity(
            "semantic_dag.initialize_projection",
            DAGRunProjectionInitializeRequest(
                run_id=workflow_input.run_id,
                workflow_id=workflow_input.workflow_id,
                validated_plan=workflow_input.validated_plan,
                policy=workflow_input.policy,
                task_policies=workflow_input.task_policies,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=self._infrastructure_retry_policy(workflow_input.policy),
        )
        task_policies = {
            policy.task_id: policy
            for policy in workflow_input.task_policies
        }
        tasks = tuple(
            DAGTaskProjectionRecord(
                task_id=task.task_id,
                skill_id=task.skill_id,
                skill_version=task.skill_version,
                target_envelope_id=task.target_envelope_id,
            )
            for task in workflow_input.validated_plan.plan.tasks
        )
        tasks_by_id = {
            task.task_id: task
            for task in workflow_input.validated_plan.plan.tasks
        }
        while True:
            if self._cancel_requested:
                return await self._cancel_projection(
                    run_id=workflow_input.run_id,
                    tasks=tasks,
                )
            frontier = evaluate_dag_frontier(
                workflow_input.validated_plan.plan.tasks,
                self._terminal_states,
            )
            for dependency_failure in frontier.dependency_failures:
                await workflow.execute_activity(
                    "semantic_dag.dependency_failure",
                    TemporalDependencyFailureInput(
                        run_id=workflow_input.run_id,
                        task_id=dependency_failure.task_id,
                        dependency_task_id=dependency_failure.dependency_task_id,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=self._infrastructure_retry_policy(
                        workflow_input.policy,
                    ),
                )
                self._terminal_states[dependency_failure.task_id] = (
                    DAGTaskTerminalState.DEPENDENCY_FAILED
                )
            if frontier.ready_task_ids:
                ready_ids = frontier.ready_task_ids[
                    : workflow_input.policy.max_concurrency
                ]
                outcomes = await asyncio.gather(
                    *[
                        self._execute_task_with_policy(
                            run_id=workflow_input.run_id,
                            task=tasks_by_id[task_id],
                            snapshot_digest=(
                                workflow_input.validated_plan.plan.snapshot_digest
                            ),
                            task_policy=task_policies[task_id],
                            policy=workflow_input.policy,
                        )
                        for task_id in ready_ids
                    ],
                )
                for result in outcomes:
                    await workflow.execute_activity(
                        "semantic_dag.record_task_result",
                        TemporalTaskResultInput(
                            run_id=workflow_input.run_id,
                            result=result,
                        ),
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=self._infrastructure_retry_policy(
                            workflow_input.policy,
                        ),
                    )
                    self._record_workflow_result(result)
                continue
            if frontier.waiting_task_ids:
                raise RuntimeError("semantic dag workflow cannot progress")
            if len(frontier.terminal_task_ids) != len(tasks):
                raise RuntimeError("semantic dag workflow has unresolved task")
            final_status = (
                DAGRunStatus.COMPLETED
                if all(
                    state.is_dependency_success()
                    for state in self._terminal_states.values()
                )
                else DAGRunStatus.COMPLETED_WITH_FAILURES
            )
            return await workflow.execute_activity(
                "semantic_dag.finish_projection",
                TemporalFinishRunInput(
                    run_id=workflow_input.run_id,
                    status=final_status,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=self._infrastructure_retry_policy(
                    workflow_input.policy,
                ),
            )

    @workflow.signal
    def cancel(
        self,
        message: str,
    ) -> None:
        """记录外部取消信号，供下一个 workflow 循环显式收敛。

        :param message: 外部取消原因。
        :return: 无返回值。
        """
        self._cancel_requested = True
        self._cancel_message = message or self._cancel_message

    @workflow.query
    def terminal_states(
        self,
    ) -> dict[str, str]:
        """查询 workflow event history 中已确认的任务终态。

        :return: 返回 task_id 到业务终态值的映射。
        """
        return {
            task_id: state.value
            for task_id, state in self._terminal_states.items()
        }

    async def _execute_task_with_policy(
        self,
        *,
        run_id: str,
        task: PlanTask,
        snapshot_digest: str,
        task_policy: DAGTaskPolicy,
        policy: DAGExecutionPolicy,
    ) -> DAGTaskExecutionResult:
        """将任务交给 Temporal activity 与 RetryPolicy 执行并返回业务结果。

        :param run_id: DAG workflow 稳定标识。
        :param task: 权威 PlanTask。
        :param snapshot_digest: TurnSnapshot digest。
        :param task_policy: SkillCatalog 投影出的任务策略。
        :param policy: workflow 固化执行策略。
        :return: 返回最终任务业务结果。
        """
        try:
            return await workflow.execute_activity(
                "semantic_dag.execute_task",
                TemporalTaskActivityInput(
                    run_id=run_id,
                    task=task,
                    task_policy=task_policy,
                    turn_snapshot_digest=snapshot_digest,
                    dependency_artifacts=self._dependency_artifacts(task),
                    semantic_retry_backoff_seconds=(
                        policy.semantic_retry_backoff_seconds
                    ),
                ),
                start_to_close_timeout=timedelta(
                    seconds=policy.task_timeout_seconds,
                ),
                activity_id=f"semantic-task:{task.task_id}",
                retry_policy=self._infrastructure_retry_policy(policy),
            )
        except ActivityError as error:
            if not isinstance(error.cause, TemporalTimeoutError):
                raise
            return DAGTaskExecutionResult(
                task_id=task.task_id,
                terminal_state=DAGTaskTerminalState.TIMEOUT,
                failure_code=SkillFailureCode.TIMEOUT,
                failure_message="semantic task activity timed out",
            )

    def _record_workflow_result(
        self,
        result: DAGTaskExecutionResult,
    ) -> None:
        """记录 activity 结果到可重放的 workflow 本地状态。

        :param result: 任务业务结果。
        :return: 无返回值。
        """
        self._terminal_states[result.task_id] = result.terminal_state
        if result.artifact_reference is not None:
            self._artifact_references[result.task_id] = result.artifact_reference

    def _dependency_artifacts(
        self,
        task: PlanTask,
    ) -> dict[str, str]:
        """读取任务直接上游的已验证 artifact 引用。

        :param task: 待执行任务。
        :return: 返回上游 task_id 到 artifact 引用映射。
        """
        return {
            dependency_id: self._artifact_references[dependency_id]
            for dependency_id in task.depends_on
            if dependency_id in self._artifact_references
        }

    def _infrastructure_retry_policy(
        self,
        policy: DAGExecutionPolicy,
    ) -> RetryPolicy:
        """构造 Temporal activity 重试策略。

        :param policy: workflow 固化执行策略。
        :return: 返回用于基础设施异常和语义可重试失败的 Temporal RetryPolicy。
        """
        return RetryPolicy(
            initial_interval=timedelta(
                seconds=policy.infrastructure_retry_initial_interval_seconds,
            ),
            maximum_interval=timedelta(
                seconds=policy.infrastructure_retry_max_interval_seconds,
            ),
            maximum_attempts=3,
            non_retryable_error_types=[
                "NotImplementedError",
                "SemanticTaskExecutionError",
                "DAGProjectionRepositoryError",
            ],
        )

    async def _cancel_projection(
        self,
        *,
        run_id: str,
        tasks: tuple[DAGTaskProjectionRecord, ...],
    ) -> DAGRunProjectionRecord:
        """将取消信号收敛为任务和 run 的显式投影终态。

        :param run_id: DAG workflow 稳定标识。
        :param tasks: 全部任务投影身份集合。
        :return: 返回 canceled run 投影。
        """
        for task in tasks:
            if task.task_id in self._terminal_states:
                continue
            result = DAGTaskExecutionResult(
                task_id=task.task_id,
                terminal_state=DAGTaskTerminalState.BLOCKED,
                failure_code=SkillFailureCode.TIMEOUT,
                failure_message=self._cancel_message,
            )
            await workflow.execute_activity(
                "semantic_dag.record_task_result",
                TemporalTaskResultInput(
                    run_id=run_id,
                    result=result,
                ),
                start_to_close_timeout=timedelta(seconds=30),
            )
            self._record_workflow_result(result)
        return await workflow.execute_activity(
            "semantic_dag.finish_projection",
            TemporalFinishRunInput(
                run_id=run_id,
                status=DAGRunStatus.CANCELED,
            ),
            start_to_close_timeout=timedelta(seconds=30),
        )


class TemporalDAGActivityRuntime:
    """提供语义协作 DAG 的 Temporal activity 执行边界。

    :param repository: 只读投影仓储。
    :param executor: M05～M11 任务执行端口。
    :return: 无返回值。
    """

    def __init__(
        self,
        *,
        repository: SemanticDAGProjectionRepository,
        executor: SemanticTaskExecutor,
    ) -> None:
        """初始化 Temporal activity runtime。

        :param repository: 只读投影仓储。
        :param executor: M05～M11 任务执行端口。
        :return: 无返回值。
        """
        self._repository = repository
        self._executor = executor

    @activity.defn(name="semantic_dag.initialize_projection")
    async def initialize_projection(
        self,
        request: DAGRunProjectionInitializeRequest,
    ) -> DAGRunProjectionRecord:
        """幂等初始化 DAG run 投影。

        :param request: 投影初始化请求。
        :return: 返回 run 投影。
        """
        return self._repository.initialize_run(request)

    @activity.defn(name="semantic_dag.execute_task")
    async def execute_task(
        self,
        request: TemporalTaskActivityInput,
    ) -> DAGTaskExecutionResult:
        """执行一次受限语义任务并返回显式业务结果。

        :param request: 任务 activity 输入。
        :return: 返回任务端口和 verifier 确认后的结果。
        :raises SemanticTaskExecutionError: 结果任务身份不一致时抛出。
        """
        return await self._execute_task_with_attempt(
            request=request,
            attempt_number=activity.info().attempt,
        )

    async def _execute_task_with_attempt(
        self,
        *,
        request: TemporalTaskActivityInput,
        attempt_number: int,
    ) -> DAGTaskExecutionResult:
        """按指定 Temporal attempt 编号执行任务端口。

        :param request: 任务 activity 输入。
        :param attempt_number: Temporal activity 当前尝试编号。
        :return: 返回任务端口和 verifier 确认后的结果。
        :raises SemanticTaskExecutionError: 结果任务身份不一致时抛出。
        :raises ApplicationError: 语义失败仍处重试预算内时抛给 Temporal 重试。
        """
        result = await self._executor.execute(
            SemanticTaskExecutionRequest(
                run_id=request.run_id,
                attempt_number=attempt_number,
                task=request.task,
                turn_snapshot_digest=request.turn_snapshot_digest,
                dependency_artifacts=request.dependency_artifacts,
            ),
        )
        if result.task_id != request.task.task_id:
            raise SemanticTaskExecutionError(
                "semantic task result identity mismatch",
                failure_code="semantic_task_result_identity_mismatch",
            )
        if (
            result.failure_code in request.task_policy.retryable_failure_codes
            and attempt_number < request.task_policy.max_attempts
        ):
            raise ApplicationError(
                "retryable semantic task failure",
                result.model_dump(mode="json"),
                type="RetryableSemanticTaskFailure",
                next_retry_delay=timedelta(
                    seconds=request.semantic_retry_backoff_seconds,
                ),
            )
        return result

    @activity.defn(name="semantic_dag.record_task_result")
    async def record_task_result(
        self,
        request: TemporalTaskResultInput,
    ) -> DAGRunProjectionRecord:
        """记录任务业务终态投影。

        :param request: 任务结果投影输入。
        :return: 返回更新后的 run 投影。
        """
        return self._repository.record_task_result(
            request.run_id,
            request.result,
        )

    @activity.defn(name="semantic_dag.dependency_failure")
    async def dependency_failure(
        self,
        request: TemporalDependencyFailureInput,
    ) -> DAGRunProjectionRecord:
        """记录 dependency_failed 任务投影。

        :param request: 依赖失败输入。
        :return: 返回更新后的 run 投影。
        """
        return self._repository.record_dependency_failure(
            request.run_id,
            request.task_id,
            request.dependency_task_id,
        )

    @activity.defn(name="semantic_dag.finish_projection")
    async def finish_projection(
        self,
        request: TemporalFinishRunInput,
    ) -> DAGRunProjectionRecord:
        """记录 workflow 业务终态投影。

        :param request: workflow 终态输入。
        :return: 返回终态 run 投影。
        """
        return self._repository.finish_run(
            request.run_id,
            request.status,
        )


def build_temporal_semantic_dag_worker(
    *,
    client: Client,
    task_queue: str,
    repository: SemanticDAGProjectionRepository,
    executor: SemanticTaskExecutor,
) -> Worker:
    """构建语义协作 DAG 的 Temporal worker。

    :param client: 已连接的 Temporal client。
    :param task_queue: 语义 DAG worker task queue。
    :param repository: 只读投影仓储。
    :param executor: M05～M11 任务执行端口。
    :return: 返回未启动的 Temporal Worker。
    """
    runtime = TemporalDAGActivityRuntime(
        repository=repository,
        executor=executor,
    )
    return Worker(
        client=client,
        task_queue=task_queue,
        workflows=[SemanticDAGWorkflow],
        activities=[
            runtime.initialize_projection,
            runtime.execute_task,
            runtime.record_task_result,
            runtime.dependency_failure,
            runtime.finish_projection,
        ],
    )


class TemporalDAGRunHandle:
    """表示已提交到 Temporal 的语义协作 DAG run 句柄。

    :param run_id: DAG workflow 稳定标识。
    :param handle: Temporal workflow handle。
    :return: 无返回值。
    """

    def __init__(
        self,
        *,
        run_id: str,
        handle: WorkflowHandle[Any, Any],
    ) -> None:
        """初始化 Temporal run 句柄。

        :param run_id: DAG workflow 稳定标识。
        :param handle: Temporal workflow handle。
        :return: 无返回值。
        """
        self.run_id = run_id
        self._handle = handle

    async def result(
        self,
        timeout_seconds: float | None = None,
    ) -> DAGRunProjectionRecord:
        """等待 Temporal workflow 返回终态投影。

        :param timeout_seconds: 可选等待秒数。
        :return: 返回 run 终态投影。
        """
        if timeout_seconds is None:
            return await self._handle.result()
        return await asyncio.wait_for(
            self._handle.result(),
            timeout=timeout_seconds,
        )

    async def cancel(
        self,
        message: str,
    ) -> None:
        """发送语义 DAG 取消信号。

        :param message: 取消原因。
        :return: 无返回值。
        """
        await self._handle.signal(
            SemanticDAGWorkflow.cancel,
            message,
        )


class TemporalSemanticDAGScheduler:
    """提供 Temporal-first 语义 DAG workflow 启动门面。

    :param client: 已连接的 Temporal client。
    :param task_queue: worker task queue。
    :param registry: 已冻结的 SkillRegistry。
    :return: 无返回值。
    """

    def __init__(
        self,
        *,
        client: Client,
        task_queue: str,
        registry: SkillRegistry,
    ) -> None:
        """初始化 Temporal 调度门面。

        :param client: 已连接的 Temporal client。
        :param task_queue: worker task queue。
        :param registry: 已冻结的 SkillRegistry。
        :return: 无返回值。
        """
        self._client = client
        self._task_queue = task_queue
        self._registry = registry

    async def start(
        self,
        validated_plan: ValidatedPlan,
        policy: DAGExecutionPolicy | None = None,
    ) -> TemporalDAGRunHandle:
        """启动语义协作 DAG Temporal workflow。

        :param validated_plan: 已通过 M03 校验的权威计划。
        :param policy: 可选执行策略；缺省使用生产策略。
        :return: 返回 durable workflow 句柄。
        :raises SchedulerError: 任务策略无法从 SkillCatalog 投影时抛出。
        """
        execution_policy = policy or DAGExecutionPolicy()
        run_id = semantic_dag_run_id(validated_plan.plan.plan_id)
        workflow_input = TemporalDAGWorkflowInput(
            run_id=run_id,
            workflow_id=run_id,
            validated_plan=validated_plan,
            policy=execution_policy,
            task_policies=build_dag_task_policies(
                registry=self._registry,
                validated_plan=validated_plan,
            ),
        )
        handle = await self._client.start_workflow(
            SemanticDAGWorkflow.run,
            workflow_input,
            id=run_id,
            task_queue=self._task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            run_timeout=timedelta(
                seconds=execution_policy.run_timeout_seconds,
            ),
        )
        return TemporalDAGRunHandle(
            run_id=run_id,
            handle=handle,
        )

    def attach(
        self,
        run_id: str,
    ) -> TemporalDAGRunHandle:
        """附着到既有语义 DAG workflow。

        :param run_id: DAG workflow 稳定标识。
        :return: 返回 durable workflow 句柄。
        """
        handle = self._client.get_workflow_handle(
            run_id,
            result_type=DAGRunProjectionRecord,
        )
        return TemporalDAGRunHandle(
            run_id=run_id,
            handle=handle,
        )

"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/scheduler_repository.py
作用：实现受限语义协作 DAG M04 的只读投影仓储。
范围：覆盖 PostgreSQL 与进程内 run / task 终态投影、幂等初始化、任务结果
      写入、依赖失败写入和 workflow 终态写入。
说明：本文件不实现任务队列、租约、attempt、worker 恢复或调度重试；
      durable 执行权威在 Temporal，本仓储仅服务查询、审计与排障。
=============================================================================
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from vet_agent.db import (
    SemanticDAGRunProjectionModel,
    SemanticDAGTaskProjectionModel,
)

from .contracts import SkillFailureCode
from .errors import DAGProjectionRepositoryError
from .plan_contracts import PlanTask
from .scheduler_contracts import (
    DAGRunProjectionInitializeRequest,
    DAGRunProjectionRecord,
    DAGRunStatus,
    DAGTaskExecutionResult,
    DAGTaskPolicy,
    DAGTaskProjectionRecord,
    DAGTaskTerminalState,
)
from .scheduler_ports import SemanticDAGProjectionRepository


def _now_utc() -> datetime:
    """读取当前 UTC-aware 时间。

    :return: 返回带 UTC 时区信息的当前时间。
    """
    return datetime.now(UTC)


def _ensure_utc(
    value: datetime,
) -> datetime:
    """将数据库时间统一转换为 UTC-aware 值。

    :param value: 数据库返回的时间。
    :return: 返回转换后的 UTC-aware datetime。
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _task_sort_key(
    task: PlanTask,
) -> str:
    """读取权威 PlanTask 的稳定排序键。

    :param task: 权威 PlanTask。
    :return: 返回任务标识。
    """
    return task.task_id


def _task_policy_sort_key(
    policy: DAGTaskPolicy,
) -> str:
    """读取任务策略的稳定排序键。

    :param policy: 任务策略。
    :return: 返回策略绑定的任务标识。
    """
    return policy.task_id


def _run_record_from_row(
    run_row: SemanticDAGRunProjectionModel,
) -> DAGRunProjectionRecord:
    """将 run 投影数据库行转换为稳定 Pydantic 快照。

    :param run_row: run 投影数据库行。
    :return: 返回完整 run 投影。
    :raises DAGProjectionRepositoryError: 持久化 JSON 或枚举非法时抛出。
    """
    try:
        return DAGRunProjectionRecord(
            run_id=run_row.run_id,
            contract_version=run_row.contract_version,
            workflow_id=run_row.workflow_id,
            plan_id=run_row.plan_id,
            turn_id=run_row.turn_id,
            snapshot_digest=run_row.snapshot_digest,
            skill_catalog_digest=run_row.skill_catalog_digest,
            plan_policy_digest=run_row.plan_policy_digest,
            status=DAGRunStatus(run_row.status),
            tasks=tuple(
                DAGTaskProjectionRecord(
                    task_id=task_row.task_id,
                    skill_id=task_row.skill_id,
                    skill_version=task_row.skill_version,
                    target_envelope_id=task_row.target_envelope_id,
                    terminal_state=(
                        DAGTaskTerminalState(task_row.terminal_state)
                        if task_row.terminal_state is not None
                        else None
                    ),
                    artifact_reference=task_row.artifact_reference,
                    failure_code=(
                        SkillFailureCode(task_row.failure_code)
                        if task_row.failure_code is not None
                        else None
                    ),
                    failure_message=task_row.failure_message,
                )
                for task_row in run_row.tasks
            ),
        )
    except (ValidationError, ValueError, TypeError) as error:
        raise DAGProjectionRepositoryError("invalid semantic dag projection") from error


def _validate_existing_run(
    *,
    run_row: SemanticDAGRunProjectionModel,
    request: DAGRunProjectionInitializeRequest,
) -> None:
    """校验既有 run 投影与权威初始化请求一致。

    :param run_row: 既有 run 投影数据库行。
    :param request: 当前幂等初始化请求。
    :return: 无返回值。
    :raises DAGProjectionRepositoryError: 身份、策略或任务集合冲突时抛出。
    """
    plan = request.validated_plan.plan
    expected = {
        "workflow_id": request.workflow_id,
        "plan_id": plan.plan_id,
        "turn_id": plan.turn_id,
        "snapshot_digest": plan.snapshot_digest,
        "skill_catalog_digest": plan.skill_catalog_digest,
        "plan_policy_digest": plan.plan_policy_digest,
        "policy": request.policy.model_dump(mode="json"),
    }
    actual = {
        "workflow_id": run_row.workflow_id,
        "plan_id": run_row.plan_id,
        "turn_id": run_row.turn_id,
        "snapshot_digest": run_row.snapshot_digest,
        "skill_catalog_digest": run_row.skill_catalog_digest,
        "plan_policy_digest": run_row.plan_policy_digest,
        "policy": run_row.policy,
    }
    if expected != actual:
        raise DAGProjectionRepositoryError("semantic dag projection contract mismatch")
    try:
        request_policies = {
            policy.task_id: policy
            for policy in request.task_policies
        }
        policies = {
            policy.task_id: policy
            for policy in (
                DAGTaskPolicy.model_validate(item)
                for item in run_row.task_policies
            )
        }
    except (ValidationError, ValueError, TypeError) as error:
        raise DAGProjectionRepositoryError("invalid persisted task policy") from error
    task_rows = {
        task_row.task_id: task_row
        for task_row in run_row.tasks
    }
    for plan_task in plan.tasks:
        task_row = task_rows.get(plan_task.task_id)
        policy = policies.get(plan_task.task_id)
        if task_row is None or policy is None:
            raise DAGProjectionRepositoryError("semantic dag projection task is missing")
        if (
            task_row.skill_id != plan_task.skill_id
            or task_row.skill_version != plan_task.skill_version
            or task_row.target_envelope_id != plan_task.target_envelope_id
            or policy.max_attempts
            != request_policies[plan_task.task_id].max_attempts
            or set(policy.retryable_failure_codes)
            != set(request_policies[plan_task.task_id].retryable_failure_codes)
        ):
            raise DAGProjectionRepositoryError("semantic dag projection task mismatch")


def _task_result_matches(
    *,
    task_row: SemanticDAGTaskProjectionModel,
    result: DAGTaskExecutionResult,
) -> bool:
    """判断任务投影与结果是否已经幂等一致。

    :param task_row: 任务投影数据库行。
    :param result: 任务执行结果。
    :return: 一致返回 True，否则返回 False。
    """
    return (
        task_row.terminal_state == result.terminal_state.value
        and task_row.artifact_reference == result.artifact_reference
        and task_row.failure_code == (
            result.failure_code.value if result.failure_code is not None else None
        )
        and task_row.failure_message == result.failure_message
    )


class InMemorySemanticDAGProjectionRepository(SemanticDAGProjectionRepository):
    """提供语义 DAG 投影的进程内测试仓储。

    :return: 无返回值；该实现不参与生产 durable 调度。
    """

    def __init__(self) -> None:
        """初始化进程内投影存储。

        :return: 无返回值。
        """
        self._runs: dict[str, DAGRunProjectionRecord] = {}
        self._policies: dict[str, tuple[DAGTaskPolicy, ...]] = {}

    def initialize_run(
        self,
        request: DAGRunProjectionInitializeRequest,
    ) -> DAGRunProjectionRecord:
        """幂等初始化进程内 run 投影。

        :param request: 投影初始化请求。
        :return: 返回新建或既有 run 投影。
        :raises DAGProjectionRepositoryError: 身份或策略冲突时抛出。
        """
        existing = self._runs.get(request.run_id)
        if existing is not None:
            expected = (
                request.workflow_id,
                request.validated_plan.plan.plan_id,
                request.validated_plan.plan.turn_id,
                request.validated_plan.plan.snapshot_digest,
                request.policy,
            )
            actual = (
                existing.workflow_id,
                existing.plan_id,
                existing.turn_id,
                existing.snapshot_digest,
                self._policy_for_existing(request.run_id),
            )
            if expected != actual:
                raise DAGProjectionRepositoryError("semantic dag projection mismatch")
            return existing
        plan = request.validated_plan.plan
        record = DAGRunProjectionRecord(
            run_id=request.run_id,
            workflow_id=request.workflow_id,
            plan_id=plan.plan_id,
            turn_id=plan.turn_id,
            snapshot_digest=plan.snapshot_digest,
            skill_catalog_digest=plan.skill_catalog_digest,
            plan_policy_digest=plan.plan_policy_digest,
            status=DAGRunStatus.RUNNING,
            tasks=tuple(
                DAGTaskProjectionRecord(
                    task_id=task.task_id,
                    skill_id=task.skill_id,
                    skill_version=task.skill_version,
                    target_envelope_id=task.target_envelope_id,
                )
                for task in sorted(plan.tasks, key=_task_sort_key)
            ),
        )
        self._runs[request.run_id] = record
        self._policies[request.run_id] = request.task_policies
        return record

    def load_run(
        self,
        run_id: str,
    ) -> DAGRunProjectionRecord | None:
        """读取进程内 run 投影。

        :param run_id: DAG workflow 稳定标识。
        :return: 找到时返回 run 投影，否则返回 None。
        """
        return self._runs.get(run_id)

    def record_task_result(
        self,
        run_id: str,
        result: DAGTaskExecutionResult,
    ) -> DAGRunProjectionRecord:
        """记录进程内任务终态投影。

        :param run_id: DAG workflow 稳定标识。
        :param result: 任务执行结果。
        :return: 返回更新后的 run 投影。
        :raises DAGProjectionRepositoryError: run 或任务缺失、结果冲突时抛出。
        """
        run = self._require_run(run_id)
        tasks: list[DAGTaskProjectionRecord] = []
        found = False
        for task in run.tasks:
            if task.task_id != result.task_id:
                tasks.append(task)
                continue
            found = True
            if task.terminal_state is not None:
                self._validate_repeat_result(
                    task=task,
                    result=result,
                )
            tasks.append(
                DAGTaskProjectionRecord(
                    task_id=task.task_id,
                    skill_id=task.skill_id,
                    skill_version=task.skill_version,
                    target_envelope_id=task.target_envelope_id,
                    terminal_state=result.terminal_state,
                    artifact_reference=result.artifact_reference,
                    failure_code=result.failure_code,
                    failure_message=result.failure_message,
                ),
            )
        if not found:
            raise DAGProjectionRepositoryError("semantic dag projection task is missing")
        updated = run.model_copy(
            update={
                "tasks": tuple(tasks),
            },
        )
        self._runs[run_id] = updated
        return updated

    def record_dependency_failure(
        self,
        run_id: str,
        task_id: str,
        dependency_task_id: str,
    ) -> DAGRunProjectionRecord:
        """记录进程内 dependency_failed 投影。

        :param run_id: DAG workflow 稳定标识。
        :param task_id: 被阻断任务标识。
        :param dependency_task_id: 已失败上游任务标识。
        :return: 返回更新后的 run 投影。
        """
        return self.record_task_result(
            run_id,
            DAGTaskExecutionResult(
                task_id=task_id,
                terminal_state=DAGTaskTerminalState.DEPENDENCY_FAILED,
                failure_code=SkillFailureCode.DEPENDENCY_FAILED,
                failure_message=f"dependency failed: {dependency_task_id}",
            ),
        )

    def finish_run(
        self,
        run_id: str,
        status: DAGRunStatus,
    ) -> DAGRunProjectionRecord:
        """记录进程内 workflow 终态投影。

        :param run_id: DAG workflow 稳定标识。
        :param status: workflow 业务终态。
        :return: 返回终态 run 投影。
        :raises DAGProjectionRepositoryError: 任务未全部终态时抛出。
        """
        run = self._require_run(run_id)
        if any(task.terminal_state is None for task in run.tasks):
            raise DAGProjectionRepositoryError("semantic dag projection has unfinished task")
        updated = run.model_copy(
            update={
                "status": status,
            },
        )
        self._runs[run_id] = updated
        return updated

    def _policy_for_existing(
        self,
        run_id: str,
    ) -> tuple[DAGTaskPolicy, ...]:
        """读取既有 run 的任务策略。

        :param run_id: DAG workflow 稳定标识。
        :return: 返回任务策略集合。
        """
        return self._policies[run_id]

    def _require_run(
        self,
        run_id: str,
    ) -> DAGRunProjectionRecord:
        """读取必需的进程内 run 投影。

        :param run_id: DAG workflow 稳定标识。
        :return: 返回 run 投影。
        :raises DAGProjectionRepositoryError: run 不存在时抛出。
        """
        run = self._runs.get(run_id)
        if run is None:
            raise DAGProjectionRepositoryError("semantic dag projection is not found")
        return run

    def _validate_repeat_result(
        self,
        *,
        task: DAGTaskProjectionRecord,
        result: DAGTaskExecutionResult,
    ) -> None:
        """校验重复写入的任务结果一致。

        :param task: 已终态任务投影。
        :param result: 新收到的任务结果。
        :return: 无返回值。
        :raises DAGProjectionRepositoryError: 结果冲突时抛出。
        """
        if (
            task.terminal_state != result.terminal_state
            or task.artifact_reference != result.artifact_reference
            or task.failure_code != result.failure_code
            or task.failure_message != result.failure_message
        ):
            raise DAGProjectionRepositoryError("semantic dag projection result conflict")


class PostgresSemanticDAGProjectionRepository(SemanticDAGProjectionRepository):
    """提供 PostgreSQL 上的语义 DAG 投影仓储。

    :param session_factory: SQLAlchemy Session 工厂。
    :return: 无返回值；业务层必须通过 SemanticDAGProjectionRepository 消费。
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
    ) -> None:
        """初始化 PostgreSQL 投影仓储。

        :param session_factory: SQLAlchemy Session 工厂。
        :return: 无返回值。
        """
        self._session_factory = session_factory

    def initialize_run(
        self,
        request: DAGRunProjectionInitializeRequest,
    ) -> DAGRunProjectionRecord:
        """幂等初始化 PostgreSQL run 投影。

        :param request: 投影初始化请求。
        :return: 返回新建或既有 run 投影。
        :raises DAGProjectionRepositoryError: 数据库访问或契约校验失败时抛出。
        """
        try:
            with self._session_factory.begin() as session:
                run_row = self._locked_run_row(
                    session=session,
                    run_id=request.run_id,
                )
                if run_row is None:
                    run_row = self._create_run_row(
                        session=session,
                        request=request,
                    )
                else:
                    _validate_existing_run(
                        run_row=run_row,
                        request=request,
                    )
                return _run_record_from_row(run_row)
        except DAGProjectionRepositoryError:
            raise
        except SQLAlchemyError as error:
            raise DAGProjectionRepositoryError(
                "semantic dag projection repository access failed",
            ) from error

    def load_run(
        self,
        run_id: str,
    ) -> DAGRunProjectionRecord | None:
        """读取 PostgreSQL run 投影。

        :param run_id: DAG workflow 稳定标识。
        :return: 找到时返回 run 投影，否则返回 None。
        :raises DAGProjectionRepositoryError: 数据库访问或契约失败时抛出。
        """
        try:
            with self._session_factory() as session:
                run_row = session.scalar(
                    select(SemanticDAGRunProjectionModel).where(
                        SemanticDAGRunProjectionModel.run_id == run_id,
                    ),
                )
                if run_row is None:
                    return None
                return _run_record_from_row(run_row)
        except DAGProjectionRepositoryError:
            raise
        except SQLAlchemyError as error:
            raise DAGProjectionRepositoryError(
                "semantic dag projection repository access failed",
            ) from error

    def record_task_result(
        self,
        run_id: str,
        result: DAGTaskExecutionResult,
    ) -> DAGRunProjectionRecord:
        """记录 PostgreSQL 任务终态投影。

        :param run_id: DAG workflow 稳定标识。
        :param result: 任务执行结果。
        :return: 返回更新后的 run 投影。
        :raises DAGProjectionRepositoryError: run 或任务缺失、结果冲突时抛出。
        """
        now = _now_utc()
        try:
            with self._session_factory.begin() as session:
                run_row, task_row = self._locked_run_task_rows(
                    session=session,
                    run_id=run_id,
                    task_id=result.task_id,
                )
                if task_row.terminal_state is not None:
                    if not _task_result_matches(
                        task_row=task_row,
                        result=result,
                    ):
                        raise DAGProjectionRepositoryError(
                            "semantic dag projection result conflict",
                        )
                else:
                    task_row.terminal_state = result.terminal_state.value
                    task_row.artifact_reference = result.artifact_reference
                    task_row.failure_code = (
                        result.failure_code.value
                        if result.failure_code is not None
                        else None
                    )
                    task_row.failure_message = result.failure_message
                    task_row.updated_at = now
                    run_row.updated_at = now
                return _run_record_from_row(run_row)
        except DAGProjectionRepositoryError:
            raise
        except SQLAlchemyError as error:
            raise DAGProjectionRepositoryError(
                "semantic dag projection repository access failed",
            ) from error

    def record_dependency_failure(
        self,
        run_id: str,
        task_id: str,
        dependency_task_id: str,
    ) -> DAGRunProjectionRecord:
        """记录 PostgreSQL dependency_failed 投影。

        :param run_id: DAG workflow 稳定标识。
        :param task_id: 被阻断任务标识。
        :param dependency_task_id: 已失败上游任务标识。
        :return: 返回更新后的 run 投影。
        """
        return self.record_task_result(
            run_id,
            DAGTaskExecutionResult(
                task_id=task_id,
                terminal_state=DAGTaskTerminalState.DEPENDENCY_FAILED,
                failure_code=SkillFailureCode.DEPENDENCY_FAILED,
                failure_message=f"dependency failed: {dependency_task_id}",
            ),
        )

    def finish_run(
        self,
        run_id: str,
        status: DAGRunStatus,
    ) -> DAGRunProjectionRecord:
        """记录 PostgreSQL workflow 终态投影。

        :param run_id: DAG workflow 稳定标识。
        :param status: workflow 业务终态。
        :return: 返回终态 run 投影。
        :raises DAGProjectionRepositoryError: run 缺失或任务未全部终态时抛出。
        """
        now = _now_utc()
        try:
            with self._session_factory.begin() as session:
                run_row = self._locked_run_row(
                    session=session,
                    run_id=run_id,
                )
                if run_row is None:
                    raise DAGProjectionRepositoryError(
                        "semantic dag projection is not found",
                    )
                if any(
                    task_row.terminal_state is None
                    for task_row in run_row.tasks
                ):
                    raise DAGProjectionRepositoryError(
                        "semantic dag projection has unfinished task",
                    )
                run_row.status = status.value
                run_row.finished_at = run_row.finished_at or now
                run_row.updated_at = now
                return _run_record_from_row(run_row)
        except DAGProjectionRepositoryError:
            raise
        except SQLAlchemyError as error:
            raise DAGProjectionRepositoryError(
                "semantic dag projection repository access failed",
            ) from error

    def _create_run_row(
        self,
        *,
        session: Session,
        request: DAGRunProjectionInitializeRequest,
    ) -> SemanticDAGRunProjectionModel:
        """创建 PostgreSQL run 与任务投影行。

        :param session: 当前事务 Session。
        :param request: 投影初始化请求。
        :return: 返回已加入 Session 的 run 投影行。
        """
        plan = request.validated_plan.plan
        now = _now_utc()
        run_row = SemanticDAGRunProjectionModel(
            run_id=request.run_id,
            contract_version="1.0.0",
            workflow_id=request.workflow_id,
            plan_id=plan.plan_id,
            turn_id=plan.turn_id,
            snapshot_digest=plan.snapshot_digest,
            skill_catalog_digest=plan.skill_catalog_digest,
            plan_policy_digest=plan.plan_policy_digest,
            status=DAGRunStatus.RUNNING.value,
            policy=request.policy.model_dump(mode="json"),
            task_policies=[
                policy.model_dump(mode="json")
                for policy in sorted(
                    request.task_policies,
                    key=_task_policy_sort_key,
                )
            ],
            created_at=now,
            updated_at=now,
        )
        for task in sorted(plan.tasks, key=_task_sort_key):
            run_row.tasks.append(
                SemanticDAGTaskProjectionModel(
                    run_id=request.run_id,
                    task_id=task.task_id,
                    skill_id=task.skill_id,
                    skill_version=task.skill_version,
                    target_envelope_id=task.target_envelope_id,
                    created_at=now,
                    updated_at=now,
                ),
            )
        session.add(run_row)
        session.flush()
        return run_row

    def _locked_run_row(
        self,
        *,
        session: Session,
        run_id: str,
    ) -> SemanticDAGRunProjectionModel | None:
        """按行锁读取 run 投影。

        :param session: 当前事务 Session。
        :param run_id: DAG workflow 稳定标识。
        :return: 找到时返回锁定 run 行，否则返回 None。
        """
        return session.scalar(
            select(SemanticDAGRunProjectionModel)
            .where(SemanticDAGRunProjectionModel.run_id == run_id)
            .with_for_update(),
        )

    def _locked_run_task_rows(
        self,
        *,
        session: Session,
        run_id: str,
        task_id: str,
    ) -> tuple[
        SemanticDAGRunProjectionModel,
        SemanticDAGTaskProjectionModel,
    ]:
        """按 run 行锁读取 run 与 task 投影。

        :param session: 当前事务 Session。
        :param run_id: DAG workflow 稳定标识。
        :param task_id: 权威任务标识。
        :return: 返回锁定 run 行与任务行。
        :raises DAGProjectionRepositoryError: run 或任务缺失时抛出。
        """
        run_row = self._locked_run_row(
            session=session,
            run_id=run_id,
        )
        if run_row is None:
            raise DAGProjectionRepositoryError("semantic dag projection is not found")
        task_row = session.scalar(
            select(SemanticDAGTaskProjectionModel)
            .where(
                SemanticDAGTaskProjectionModel.run_id == run_id,
                SemanticDAGTaskProjectionModel.task_id == task_id,
            )
            .with_for_update(),
        )
        if task_row is None:
            raise DAGProjectionRepositoryError(
                "semantic dag projection task is not found",
            )
        return run_row, task_row


__all__ = [
    "InMemorySemanticDAGProjectionRepository",
    "PostgresSemanticDAGProjectionRepository",
]

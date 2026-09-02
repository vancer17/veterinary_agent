"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/scheduler_graph.py
作用：提供 M04 DAG 调度器与 Temporal workflow 共享的确定性图推进内核。
范围：覆盖拓扑执行层、就绪任务计算、依赖失败传播和无法推进检测。
说明：本文件不访问数据库、不调用模型、不解释语义结果；它只消费权威
      PlanTask 与任务终态，确保自研内核与 durable runtime 使用同一规则。
=============================================================================
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from graphlib import CycleError, TopologicalSorter

from pydantic import BaseModel, ConfigDict, Field

from .errors import SchedulerError
from .plan_contracts import PlanTask
from .scheduler_contracts import DAGTaskTerminalState


class DAGDependencyFailure(BaseModel):
    """表示一条需要持久化的上游依赖失败传播记录。

    :return: 无返回值；该对象不判断失败医学原因，只传播显式终态。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task_id: str = Field(
        min_length=1,
        max_length=360,
        description="被上游失败阻断的任务标识。",
    )
    dependency_task_id: str = Field(
        min_length=1,
        max_length=360,
        description="已经进入失败终态的上游任务标识。",
    )


class DAGFrontier(BaseModel):
    """表示当前终态集合下可推进、可传播失败与仍等待的 DAG 前沿。

    :return: 无返回值；调度器根据该纯函数结果执行状态迁移。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    ready_task_ids: tuple[str, ...] = Field(
        description="依赖全部成功且尚未终态的任务标识集合。",
    )
    dependency_failures: tuple[DAGDependencyFailure, ...] = Field(
        description="需要写入 dependency_failed 的上游失败传播集合。",
    )
    waiting_task_ids: tuple[str, ...] = Field(
        description="仍有上游未进入终态的任务标识集合。",
    )
    terminal_task_ids: tuple[str, ...] = Field(
        description="已经进入业务终态的任务标识集合。",
    )

    @property
    def can_progress(self) -> bool:
        """判断当前前沿是否存在可推进的状态迁移。

        :return: 存在 ready 任务或 dependency failure 时返回 True。
        """
        return bool(self.ready_task_ids or self.dependency_failures)


def execution_layers(
    tasks: Sequence[PlanTask],
) -> tuple[tuple[PlanTask, ...], ...]:
    """计算 PlanTask DAG 的确定性拓扑执行层。

    :param tasks: 权威 PlanTask 集合。
    :return: 返回按拓扑层级与 task_id 排序的任务二维元组。
    :raises SchedulerError: 任务重复、依赖缺失或依赖成环时抛出。
    """
    tasks_by_id = {task.task_id: task for task in tasks}
    if len(tasks_by_id) != len(tasks):
        raise SchedulerError("duplicate semantic dag task id")
    graph = {
        task.task_id: set(task.depends_on)
        for task in tasks
    }
    for task in tasks:
        for dependency_id in task.depends_on:
            if dependency_id not in tasks_by_id:
                raise SchedulerError("semantic dag dependency is not found")
    try:
        TopologicalSorter(graph).prepare()
    except CycleError as error:
        raise SchedulerError("semantic dag dependency cycle detected") from error

    layered_ids: list[list[str]] = []
    remaining_indegree = {
        task_id: len(dependencies)
        for task_id, dependencies in graph.items()
    }
    pending = sorted(graph)
    while pending:
        current = sorted(
            task_id
            for task_id in pending
            if remaining_indegree[task_id] == 0
        )
        if not current:
            raise SchedulerError("semantic dag cannot produce topology layer")
        layered_ids.append(current)
        next_pending: list[str] = []
        for task_id in pending:
            if task_id in current:
                continue
            next_pending.append(task_id)
            for downstream_id in pending:
                if task_id in graph[downstream_id]:
                    remaining_indegree[downstream_id] -= 1
        pending = next_pending
    return tuple(
        tuple(tasks_by_id[task_id] for task_id in layer)
        for layer in layered_ids
    )


def evaluate_dag_frontier(
    tasks: Sequence[PlanTask],
    terminal_states: Mapping[str, DAGTaskTerminalState],
) -> DAGFrontier:
    """根据当前任务终态计算下一批可推进或需失败传播的任务。

    :param tasks: 权威 PlanTask 集合。
    :param terminal_states: task_id 到已确认业务终态的映射。
    :return: 返回 ready、dependency failure、waiting 与 terminal 前沿。
    :raises SchedulerError: 终态映射引用未知任务时抛出。
    """
    task_ids = {task.task_id for task in tasks}
    unknown_states = set(terminal_states) - task_ids
    if unknown_states:
        raise SchedulerError("terminal state references unknown semantic dag task")

    ready: list[str] = []
    failures: list[DAGDependencyFailure] = []
    waiting: list[str] = []
    terminal: list[str] = []
    for task in tasks:
        if task.task_id in terminal_states:
            terminal.append(task.task_id)
            continue
        failed_dependency: str | None = None
        dependencies_complete = True
        for dependency_id in task.depends_on:
            dependency_state = terminal_states.get(dependency_id)
            if dependency_state is None:
                dependencies_complete = False
                continue
            if not dependency_state.is_dependency_success() and failed_dependency is None:
                failed_dependency = dependency_id
        if failed_dependency is not None:
            failures.append(
                DAGDependencyFailure(
                    task_id=task.task_id,
                    dependency_task_id=failed_dependency,
                ),
            )
        elif dependencies_complete:
            ready.append(task.task_id)
        else:
            waiting.append(task.task_id)
    return DAGFrontier(
        ready_task_ids=tuple(sorted(ready)),
        dependency_failures=tuple(
            sorted(
                failures,
                key=_dependency_failure_sort_key,
            ),
        ),
        waiting_task_ids=tuple(sorted(waiting)),
        terminal_task_ids=tuple(sorted(terminal)),
    )


def _dependency_failure_sort_key(
    failure: DAGDependencyFailure,
) -> tuple[str, str]:
    """读取依赖失败传播记录的稳定排序键。

    :param failure: 待排序的依赖失败传播记录。
    :return: 返回被阻断任务与上游任务标识组成的元组。
    """
    return failure.task_id, failure.dependency_task_id


__all__ = [
    "DAGDependencyFailure",
    "DAGFrontier",
    "evaluate_dag_frontier",
    "execution_layers",
]

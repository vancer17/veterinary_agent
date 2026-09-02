"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/scheduler_ports.py
作用：定义 M04 Temporal-first 调度器的任务执行端口与投影仓储端口。
范围：覆盖 M05～M11 语义任务执行端口、M04 TODO 空壳和 PostgreSQL 只读投影
      仓储协议。
说明：本文件不定义任务队列、租约、attempt 状态或调度恢复方法；durable
      execution 由 Temporal workflow / activity runtime 负责。
=============================================================================
"""

from __future__ import annotations

from typing import Protocol

from .scheduler_contracts import (
    DAGRunProjectionInitializeRequest,
    DAGRunProjectionRecord,
    DAGRunStatus,
    DAGTaskExecutionResult,
    SemanticTaskExecutionRequest,
)


class SemanticTaskExecutor(Protocol):
    """定义受限语义任务的异步执行端口。

    :return: 无返回值；M05 Gateway、M07 Verifier 和 M11 Artifact 提交应在
             实现侧组成显式结果，不允许调度器理解 SKILL 输出内容。
    """

    async def execute(
        self,
        request: SemanticTaskExecutionRequest,
    ) -> DAGTaskExecutionResult:
        """执行一次受限语义任务并返回权威业务终态。

        :param request: Temporal activity 传入的任务执行请求。
        :return: 返回包含 verifier 结论与 artifact 引用的显式任务结果。
        """


class TODOSemanticTaskExecutor(SemanticTaskExecutor):
    """表示 M05～M11 任务执行链路尚未接入前的显式空壳。

    :return: 无返回值；该占位始终 Fail Fast，不生成伪 verified 结果。
    """

    async def execute(
        self,
        request: SemanticTaskExecutionRequest,
    ) -> DAGTaskExecutionResult:
        """阻断尚未接入的语义任务执行请求。

        :param request: Temporal activity 传入的任务执行请求。
        :raises NotImplementedError: M05～M11 执行链路未实现时始终抛出。
        :return: 无返回值。
        """
        raise NotImplementedError(
            "semantic task execution requires M05 gateway, M07 verifier and M11 artifact store"
        )


class SemanticDAGProjectionRepository(Protocol):
    """定义语义协作 DAG 只读投影仓储协议。

    :return: 无返回值；该协议只服务查询与审计，不参与任务调度或恢复。
    """

    def initialize_run(
        self,
        request: DAGRunProjectionInitializeRequest,
    ) -> DAGRunProjectionRecord:
        """幂等初始化 run 与任务的只读投影。

        :param request: 投影初始化请求。
        :return: 返回完整 run 投影。
        """

    def load_run(
        self,
        run_id: str,
    ) -> DAGRunProjectionRecord | None:
        """读取一个 DAG workflow 的只读投影。

        :param run_id: DAG workflow 稳定标识。
        :return: 找到时返回 run 投影，否则返回 None。
        """

    def record_task_result(
        self,
        run_id: str,
        result: DAGTaskExecutionResult,
    ) -> DAGRunProjectionRecord:
        """记录任务终态投影。

        :param run_id: DAG workflow 稳定标识。
        :param result: 任务端口返回的显式业务结果。
        :return: 返回更新后的 run 投影。
        """

    def record_dependency_failure(
        self,
        run_id: str,
        task_id: str,
        dependency_task_id: str,
    ) -> DAGRunProjectionRecord:
        """记录下游 dependency_failed 投影。

        :param run_id: DAG workflow 稳定标识。
        :param task_id: 被阻断任务标识。
        :param dependency_task_id: 已失败上游任务标识。
        :return: 返回更新后的 run 投影。
        """

    def finish_run(
        self,
        run_id: str,
        status: DAGRunStatus,
    ) -> DAGRunProjectionRecord:
        """记录 workflow 业务终态投影。

        :param run_id: DAG workflow 稳定标识。
        :param status: workflow 业务终态。
        :return: 返回终态 run 投影。
        """


__all__ = [
    "DAGRunProjectionInitializeRequest",
    "DAGRunProjectionRecord",
    "DAGRunStatus",
    "DAGTaskExecutionResult",
    "SemanticDAGProjectionRepository",
    "SemanticTaskExecutionRequest",
    "SemanticTaskExecutor",
    "TODOSemanticTaskExecutor",
]

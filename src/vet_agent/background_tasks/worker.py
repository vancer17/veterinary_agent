"""
=============================================================================
文件：src/vet_agent/background_tasks/worker.py
作用：提供可持久化后台任务 worker 的独立运行入口。
范围：当前阶段负责领取后台任务、分发处理器、推进任务状态与执行重试；
      不承载主回合 HTTP 入口、不负责模型网关路由，也不直接暴露业务 API。
说明：worker 应作为独立进程或容器运行，依赖 PostgreSQL 任务表作为唯一任务
      权威源；主 API 进程仅负责入队，不负责等待后台任务完成。
=============================================================================
"""

from __future__ import annotations

import asyncio
import sys
from uuid import uuid4

from pydantic import ValidationError

from vet_agent import Settings
from vet_agent.background_tasks import (
    BackgroundTaskExecutionOutcome,
    BackgroundTaskHandlerRegistry,
    BackgroundTaskRecord,
    BackgroundTaskType,
    BackgroundTaskStatus,
    MemoryCandidateExtractionTaskHandler,
)
from vet_agent.memory_extraction import MemoryExtractionAgent
from vet_agent.repositories import BackgroundTaskRepository, PostgresBackgroundTaskRepository


class BackgroundTaskWorker:
    """执行可持久化后台任务的 worker 进程。

    :return: 无返回值。
    """

    def __init__(
        self,
        settings: Settings,
        repository: BackgroundTaskRepository,
        handlers: BackgroundTaskHandlerRegistry,
        *,
        worker_id: str | None = None,
    ) -> None:
        """初始化后台任务 worker。

        :param settings: 当前运行环境配置。
        :param repository: 持久化后台任务仓储。
        :param handlers: 后台任务处理器注册表。
        :param worker_id: 可选的 worker 稳定标识。
        :return: 无返回值。
        """
        self.settings = settings
        self.repository = repository
        self.handlers = handlers
        self.worker_id = worker_id or f"worker_{uuid4().hex}"

    async def run_once(self) -> int:
        """领取并处理一批后台任务。

        :return: 返回本轮实际处理的任务数量。
        """
        tasks = self.repository.claim_due_tasks(
            worker_id=self.worker_id,
            batch_size=max(1, int(self.settings.background_tasks_worker_batch_size)),
            lease_seconds=self.settings.background_tasks_worker_lease_seconds,
            task_types=self.handlers.task_types(),
        )
        for task in tasks:
            await self._process_task(task)
        return len(tasks)

    async def run_forever(self) -> None:
        """持续领取和处理后台任务。

        :return: 无返回值。
        """
        while True:
            processed = await self.run_once()
            if processed == 0:
                await asyncio.sleep(self.settings.background_tasks_worker_poll_interval_seconds)

    async def _process_task(self, task: BackgroundTaskRecord) -> None:
        """处理单条已领取的后台任务。

        :param task: 已领取的后台任务记录。
        :return: 无返回值。
        """
        handler = self.handlers.get(task.task_type)
        if handler is None:
            self.repository.dead_letter_task(
                task.task_id,
                worker_id=self.worker_id,
                error_type="unsupported_task_type",
                error_message=f"unsupported background task type: {task.task_type.value}",
                result={"task": task.to_metadata()},
            )
            return
        try:
            outcome = await handler.handle(task)
        except ValidationError as exc:
            outcome = BackgroundTaskExecutionOutcome(
                status=BackgroundTaskStatus.DEAD_LETTER,
                result={"task": task.to_metadata()},
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        except Exception as exc:
            outcome = BackgroundTaskExecutionOutcome(
                status=BackgroundTaskStatus.RETRYING,
                result={"task": task.to_metadata()},
                retry_after_seconds=self._retry_delay(task.attempt_count),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

        self._persist_outcome(task, outcome)

    def _persist_outcome(self, task: BackgroundTaskRecord, outcome: BackgroundTaskExecutionOutcome) -> None:
        """将 worker 的执行结果持久化到后台任务表。

        :param task: 已领取的后台任务记录。
        :param outcome: 任务执行结果。
        :return: 无返回值。
        """
        if outcome.status == BackgroundTaskStatus.SUCCEEDED:
            self.repository.complete_task(
                task.task_id,
                worker_id=self.worker_id,
                result=outcome.to_metadata(),
            )
            return
        if outcome.status == BackgroundTaskStatus.RETRYING:
            self.repository.retry_task(
                task.task_id,
                worker_id=self.worker_id,
                error_type=outcome.error_type or "retryable_task_error",
                error_message=outcome.error_message or "background task retry requested",
                retry_after_seconds=outcome.retry_after_seconds or self._retry_delay(task.attempt_count),
                result=outcome.to_metadata(),
            )
            return
        self.repository.dead_letter_task(
            task.task_id,
            worker_id=self.worker_id,
            error_type=outcome.error_type or "dead_letter_task_error",
            error_message=outcome.error_message or "background task dead-lettered",
            result=outcome.to_metadata(),
        )

    def _retry_delay(self, attempt_count: int) -> float:
        """计算后台任务默认重试延迟。

        :param attempt_count: 当前任务尝试次数。
        :return: 返回建议延迟秒数。
        """
        attempt_count = max(int(attempt_count), 1)
        return float(min(30.0 * (2 ** max(attempt_count - 1, 0)), 3600.0))


def build_background_task_worker(settings: Settings) -> BackgroundTaskWorker:
    """构造可直接运行的后台任务 worker。

    :param settings: 当前运行环境配置。
    :return: 返回后台任务 worker 实例。
    """
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for background task worker")
    from vet_agent import Container

    container = Container(settings)
    repository = PostgresBackgroundTaskRepository(settings.database_url)
    extractor = MemoryExtractionAgent(container.qwen_client, settings)
    handlers = BackgroundTaskHandlerRegistry(
        {
            BackgroundTaskType.MEMORY_CANDIDATE_EXTRACTION: MemoryCandidateExtractionTaskHandler(
                extractor,
            ),
        }
    )
    return BackgroundTaskWorker(settings, repository, handlers)


async def _wait_for_repository_ready(
    repository: BackgroundTaskRepository,
    *,
    poll_interval_seconds: float,
) -> None:
    """等待后台任务仓储进入可用状态。

    :param repository: 后台任务仓储。
    :param poll_interval_seconds: 轮询间隔秒数。
    :return: 无返回值。
    """
    interval = max(float(poll_interval_seconds), 1.0)
    waiting_reported = False
    while not repository.is_ready():
        if not waiting_reported:
            print("后台任务仓储尚未就绪，worker 正在等待", file=sys.stderr)
            waiting_reported = True
        await asyncio.sleep(interval)


async def run_worker() -> None:
    """运行后台任务 worker 的协程入口。

    :return: 无返回值。
    """
    settings = Settings.from_env()
    worker = build_background_task_worker(settings)
    await _wait_for_repository_ready(
        worker.repository,
        poll_interval_seconds=settings.background_tasks_worker_poll_interval_seconds,
    )
    await worker.run_forever()


def main() -> None:
    """运行后台任务 worker 的命令行入口。

    :return: 无返回值。
    """
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()

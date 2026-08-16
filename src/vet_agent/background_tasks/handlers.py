"""
=============================================================================
文件：src/vet_agent/background_tasks/handlers.py
作用：定义可持久化后台任务的处理器协议与长期记忆候选抽取处理器。
范围：当前阶段仅实现长期记忆候选抽取任务处理；其它后台任务类型保留为 TODO
      占位，不在本文件内扩展新的业务写入链路。
说明：本文件只处理已领取后台任务的业务执行与结果归一，不直接领取任务、不
      直接实现 worker 调度循环，也不直接写入权威长期事实库。
=============================================================================
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from vet_agent import AgentTurnResponse
from vet_agent.background_tasks.models import (
    BackgroundTaskExecutionOutcome,
    BackgroundTaskRecord,
    BackgroundTaskStatus,
    BackgroundTaskType,
    MemoryCandidateExtractionTaskPayload,
)
from vet_agent.memory_extraction import (
    MemoryExtractionAgent,
    MemoryExtractionResult,
    MemoryExtractionStrategy,
)


class BackgroundTaskHandler(Protocol):
    """定义后台任务处理器协议。

    :return: 无返回值。
    """

    task_type: BackgroundTaskType

    async def handle(self, task: BackgroundTaskRecord) -> BackgroundTaskExecutionOutcome:
        """处理一条已领取的后台任务。

        :param task: 已领取的后台任务记录。
        :return: 返回结构化执行结果。
        """
        ...


class MemoryCandidateExtractionTaskHandler:
    """处理长期记忆候选抽取后台任务。

    :return: 无返回值。
    """

    task_type = BackgroundTaskType.MEMORY_CANDIDATE_EXTRACTION

    def __init__(
        self,
        extractor: MemoryExtractionAgent,
    ) -> None:
        """初始化长期记忆候选抽取处理器。

        :param extractor: 长期记忆候选抽取器。
        :return: 无返回值。
        """
        self.extractor = extractor

    async def handle(self, task: BackgroundTaskRecord) -> BackgroundTaskExecutionOutcome:
        """执行长期记忆候选抽取后台任务。

        :param task: 已领取的后台任务记录。
        :return: 返回结构化执行结果。
        """
        payload = MemoryCandidateExtractionTaskPayload.model_validate(task.payload)
        response = AgentTurnResponse.model_validate(payload.response_snapshot)
        result = await self.extractor.extract(
            identity=payload.identity,
            user_text=payload.user_text,
            response=response,
            model=response.model,
        )
        if result.strategy in {
            MemoryExtractionStrategy.MEMORY_EXTRACTION_UNAVAILABLE,
            MemoryExtractionStrategy.MEMORY_EXTRACTION_INVALID_SCHEMA,
            MemoryExtractionStrategy.MEMORY_EXTRACTION_FAILED,
        }:
            return BackgroundTaskExecutionOutcome(
                status=BackgroundTaskStatus.RETRYING,
                result=self._build_result(task, result),
                retry_after_seconds=self._retry_after_seconds(task.attempt_count),
                error_type=result.strategy.value,
                error_message=result.fallback_reason or "memory extraction failed",
            )
        if result.strategy in {
            MemoryExtractionStrategy.MEMORY_EXTRACTION_DISABLED,
            MemoryExtractionStrategy.MEMORY_EXTRACTION_SKIPPED,
            MemoryExtractionStrategy.MEMORY_EXTRACTION_EMPTY_SOURCE,
        }:
            return BackgroundTaskExecutionOutcome(
                status=BackgroundTaskStatus.DEAD_LETTER,
                result=self._build_result(task, result),
                error_type=result.strategy.value,
                error_message=result.fallback_reason or "memory extraction task is not runnable",
            )

        return BackgroundTaskExecutionOutcome(
            status=BackgroundTaskStatus.SUCCEEDED,
            result=self._build_result(task, result),
        )

    def _retry_after_seconds(self, attempt_count: int) -> float:
        """计算后台任务的建议重试延迟。

        :param attempt_count: 当前任务尝试次数。
        :return: 返回下一次重试建议延迟。
        """
        attempt_count = max(int(attempt_count), 1)
        return float(min(30.0 * (2 ** max(attempt_count - 1, 0)), 3600.0))

    def _build_result(
        self,
        task: BackgroundTaskRecord,
        result: MemoryExtractionResult,
    ) -> dict[str, Any]:
        """构造长期记忆候选抽取任务的结果摘要。

        :param task: 已领取的后台任务记录。
        :param result: 长期记忆候选抽取结果。
        :return: 返回可持久化的任务结果摘要。
        """
        metadata = result.to_metadata()
        metadata.update(
            {
                "task": task.to_metadata(),
                "stored_fact_count": 0,
                "stored_fact_keys": [],
                "fact_write_status": "TODO",
                "fact_write_reason": "long_term_fact_write_governance_not_implemented",
            }
        )
        return metadata


class BackgroundTaskHandlerRegistry:
    """定义后台任务处理器注册表。

    :return: 无返回值。
    """

    def __init__(self, handlers: Mapping[BackgroundTaskType, BackgroundTaskHandler]) -> None:
        """初始化后台任务处理器注册表。

        :param handlers: 后台任务类型到处理器的映射。
        :return: 无返回值。
        """
        self.handlers = dict(handlers)

    def get(self, task_type: BackgroundTaskType) -> BackgroundTaskHandler | None:
        """读取指定任务类型的处理器。

        :param task_type: 后台任务类型。
        :return: 返回匹配的任务处理器或空值。
        """
        return self.handlers.get(task_type)

    def task_types(self) -> tuple[BackgroundTaskType, ...]:
        """返回注册表当前支持的后台任务类型集合。

        :return: 返回已注册任务类型元组。
        """
        return tuple(self.handlers.keys())

"""
=============================================================================
文件：src/vet_agent/background_tasks/service.py
作用：编排可持久化后台任务的入队与响应摘要生成。
范围：当前阶段仅封装长期记忆候选抽取任务的持久化入队；不执行 worker 领取、
      任务消费、模型调用或数据库写入裁决。
说明：本文件负责把主回合结束后的结构化响应封装成持久化后台任务记录，并将
      任务摘要回填到响应 metadata；具体任务执行由 worker 负责。
=============================================================================
"""

from __future__ import annotations

from typing import Any, Protocol

from vet_agent import AgentTurnRequest, AgentTurnResponse, Settings
from vet_agent.memory_extraction import MemoryExtractionStrategy

from .models import BackgroundTaskRecord, BackgroundTaskType, MemoryCandidateExtractionTaskPayload
from ..repositories import BackgroundTaskRepository


class BackgroundTaskServiceProtocol(Protocol):
    """定义可持久化后台任务入队服务协议。

    :return: 无返回值。
    """

    enabled: bool

    async def enqueue_memory_candidate_extraction(
        self,
        request: AgentTurnRequest,
        response: AgentTurnResponse,
    ) -> BackgroundTaskRecord | None:
        """创建长期记忆候选抽取后台任务。

        :param request: 当前 Agent 回合请求。
        :param response: 当前 Agent 回合响应。
        :return: 返回持久化任务记录或空值。
        """
        ...

    def is_ready(self) -> bool:
        """检查后台任务入队服务是否可用。

        :return: 服务就绪时返回 True。
        """
        ...


class BackgroundTaskService(BackgroundTaskServiceProtocol):
    """提供可持久化后台任务的入队服务。

    :return: 无返回值。
    """

    enabled = True

    def __init__(self, settings: Settings, repository: BackgroundTaskRepository) -> None:
        """初始化后台任务入队服务。

        :param settings: 当前运行环境配置。
        :param repository: 持久化后台任务仓储。
        :return: 无返回值。
        """
        self.settings = settings
        self.repository = repository

    async def enqueue_memory_candidate_extraction(
        self,
        request: AgentTurnRequest,
        response: AgentTurnResponse,
    ) -> BackgroundTaskRecord | None:
        """创建长期记忆候选抽取后台任务。

        :param request: 当前 Agent 回合请求。
        :param response: 当前 Agent 回合响应。
        :return: 返回持久化任务记录或空值。
        """
        if not self.settings.enable_memory_extraction:
            return None
        payload = MemoryCandidateExtractionTaskPayload.from_turn(request, response)
        business_key = request.turn_options.idempotency_key or request.request_context.request_id
        ordering_key = f"{request.trusted_identity.user_id}:{request.trusted_identity.pet_id}:{request.trusted_identity.session_id}"
        return self.repository.enqueue_task(
            request.trusted_identity,
            task_type=BackgroundTaskType.MEMORY_CANDIDATE_EXTRACTION,
            business_key=business_key,
            ordering_key=ordering_key,
            payload=payload.model_dump(mode="json"),
            source_turn_id=response.id,
            source_request_id=request.request_context.request_id,
            source_trace_id=request.request_context.trace_id,
            metadata={
                "source": "turn_finalize",
                "response_status": response.status,
                "source_count": payload.source_count,
                "source_task_key": business_key,
            },
            priority=100,
            run_after=None,
            max_attempts=max(1, int(self.settings.background_tasks_max_attempts)),
        )

    def is_ready(self) -> bool:
        """检查后台任务入队服务所需仓储是否可访问。

        :return: 仓储就绪时返回 True。
        """
        return self.repository.is_ready()


class DisabledBackgroundTaskService(BackgroundTaskServiceProtocol):
    """表示显式禁用的后台任务入队服务。

    :return: 无返回值。
    """

    enabled = False

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化显式禁用的后台任务入队服务。

        :param settings: 可选的应用配置对象，仅用于保持构造签名一致。
        :return: 无返回值。
        """
        self.settings = settings

    async def enqueue_memory_candidate_extraction(
        self,
        request: AgentTurnRequest,
        response: AgentTurnResponse,
    ) -> BackgroundTaskRecord | None:
        """在显式禁用后台任务时跳过入队。

        :param request: 当前 Agent 回合请求。
        :param response: 当前 Agent 回合响应。
        :return: 始终返回空值。
        """
        del request, response
        return None

    def is_ready(self) -> bool:
        """检查显式禁用的后台任务入队服务是否可用于装配。

        :return: 始终返回 True，表示禁用为显式配置状态。
        """
        return True


def make_memory_extraction_task_metadata(
    task: BackgroundTaskRecord | None,
    *,
    response_text: str,
    disabled_reason: str | None = None,
) -> dict[str, Any]:
    """生成长期记忆候选抽取的响应 metadata 摘要。

    :param task: 后台任务记录。
    :param response_text: 当前回合输出文本。
    :param disabled_reason: 后台任务服务禁用原因。
    :return: 返回可直接写入响应 metadata 的字典。
    """
    if task is None:
        return {
            "agent": "MemoryExtractionAgent",
            "strategy": MemoryExtractionStrategy.BACKGROUND_TASK_DISABLED.value,
            "fallback_reason": disabled_reason or "background_tasks_disabled",
            "confidence": 0.0,
            "source_text": response_text[:500],
            "trusted": False,
            "proposal_count": 0,
            "proposal_keys": [],
            "proposals": [],
            "stored_fact_count": 0,
            "stored_fact_keys": [],
            "task_id": None,
            "task_status": "disabled",
            "task": None,
        }
    return {
        "agent": "MemoryExtractionAgent",
        "strategy": MemoryExtractionStrategy.BACKGROUND_TASK_QUEUED.value,
        "fallback_reason": None,
        "confidence": 0.0,
        "source_text": response_text[:500],
        "trusted": False,
        "proposal_count": 0,
        "proposal_keys": [],
        "proposals": [],
        "stored_fact_count": 0,
        "stored_fact_keys": [],
        "task_id": task.task_id,
        "task_status": task.status.value,
        "task": task.to_metadata(),
    }

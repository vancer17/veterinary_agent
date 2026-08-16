"""
=============================================================================
文件：src/vet_agent/background_tasks/models.py
作用：定义可持久化后台任务链路的结构化领域模型与任务载荷。
范围：承载后台任务类型、任务状态、队列记录、执行结果与长期记忆候选抽取
      的持久化载荷；不访问数据库、不调用外部服务、不执行任务领取或业务写入。
说明：本文件仅描述后台任务链路的稳定数据形状；任务领取、重试与执行调度
      应由仓储与 worker 协作完成，长期事实写入治理不在本阶段实现。
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vet_agent import AgentTurnRequest, AgentTurnResponse, TrustedIdentity


class BackgroundTaskType(StrEnum):
    """表示可持久化后台任务的类型。

    说明：当前仅实现长期记忆候选抽取任务；其它任务类型保留为 TODO 占位，
    避免业务层在此阶段扩展出新的可信源。

    :return: 无返回值。
    """

    MEMORY_CANDIDATE_EXTRACTION = "memory_candidate_extraction"
    MEM0_TURN_PROJECTION = "mem0_turn_projection"
    TODO = "TODO"


class BackgroundTaskStatus(StrEnum):
    """表示持久化后台任务的执行状态。

    :return: 无返回值。
    """

    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"

    def is_terminal(self) -> bool:
        """判断任务状态是否已进入终态。

        :return: 终态返回 True，否则返回 False。
        """
        return self in {self.SUCCEEDED, self.DEAD_LETTER, self.CANCELLED}


@dataclass(frozen=True)
class BackgroundTaskRecord:
    """表示持久化后台任务表中的结构化任务记录。

    :param task_id: 后台任务稳定标识。
    :param task_type: 后台任务类型。
    :param business_key: 任务业务幂等键。
    :param ordering_key: 任务顺序约束键。
    :param user_id: 任务来源用户标识。
    :param pet_id: 任务来源宠物标识。
    :param session_id: 任务来源会话标识。
    :param source_turn_id: 任务来源回合标识。
    :param source_request_id: 任务来源请求标识。
    :param source_trace_id: 任务来源链路追踪标识。
    :param status: 当前任务状态。
    :param priority: 任务优先级。
    :param run_after: 任务最早可执行时间。
    :param attempt_count: 已执行尝试次数。
    :param max_attempts: 最大执行尝试次数。
    :param locked_by: 当前租约持有 worker 标识。
    :param locked_until: 当前租约过期时间。
    :param payload: 任务执行载荷。
    :param result: 任务执行结果或失败摘要。
    :param last_error: 最近一次结构化错误信息。
    :param metadata: 任务附加审计元数据。
    :param started_at: 最近一次开始执行时间。
    :param finished_at: 最终完成时间。
    :param created_at: 任务创建时间。
    :param updated_at: 任务最近更新时间。
    :return: 无返回值；该对象仅承载持久化任务快照。
    """

    task_id: str
    task_type: BackgroundTaskType
    business_key: str
    ordering_key: str
    user_id: str
    pet_id: str
    session_id: str
    source_turn_id: str | None
    source_request_id: str | None
    source_trace_id: str | None
    status: BackgroundTaskStatus
    priority: int
    run_after: datetime
    attempt_count: int
    max_attempts: int
    locked_by: str | None
    locked_until: datetime | None
    payload: dict[str, Any]
    result: dict[str, Any] | None = None
    last_error: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_metadata(self) -> dict[str, Any]:
        """转换为可写入响应 metadata 的轻量任务摘要。

        :return: 返回后台任务摘要字典。
        """
        return {
            "task_id": self.task_id,
            "task_type": self.task_type.value,
            "business_key": self.business_key,
            "ordering_key": self.ordering_key,
            "status": self.status.value,
            "priority": self.priority,
            "run_after": self.run_after.isoformat() if self.run_after else None,
            "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts,
            "source_turn_id": self.source_turn_id,
            "source_request_id": self.source_request_id,
            "source_trace_id": self.source_trace_id,
        }


class MemoryCandidateExtractionTaskPayload(BaseModel):
    """定义长期记忆候选抽取后台任务的持久化载荷。

    说明：该载荷仅保存重建 worker 上下文所需的最小结构化信息，不保存业务
    写入裁决、临床状态机或其他领域的隐式回退内容。

    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid")

    identity: TrustedIdentity = Field(description="任务对应的可信身份范围。")
    user_text: str = Field(min_length=1, description="本轮用于抽取的用户输入文本。")
    response_snapshot: dict[str, Any] = Field(description="完整响应快照，用于 worker 重建抽取上下文。")
    source_count: int = Field(ge=0, description="任务可见的显式来源片段数量。")

    @classmethod
    def from_turn(
        cls,
        request: AgentTurnRequest,
        response: AgentTurnResponse,
    ) -> "MemoryCandidateExtractionTaskPayload":
        """根据 Agent 回合请求和响应构造后台任务载荷。

        :param request: 当前 Agent 回合请求。
        :param response: 当前 Agent 回合响应。
        :return: 返回可持久化的长期记忆候选抽取载荷。
        """
        response_snapshot = response.model_dump(mode="json")
        raw_sources = response_snapshot.get("metadata", {}).get("memory_extraction_sources")
        source_count = len(raw_sources) if isinstance(raw_sources, list) else 0
        return cls(
            identity=request.trusted_identity,
            user_text=request.joined_text(),
            response_snapshot=response_snapshot,
            source_count=source_count,
        )


@dataclass(frozen=True)
class BackgroundTaskExecutionOutcome:
    """表示后台 worker 对单个任务的结构化执行结果。

    :param status: 任务执行后的目标状态。
    :param result: 任务执行结果摘要。
    :param retry_after_seconds: 需要重试时的建议延迟秒数。
    :param error_type: 失败异常类型。
    :param error_message: 失败异常描述。
    :return: 无返回值；该对象仅用于 worker 与仓储之间的结果传递。
    """

    status: BackgroundTaskStatus
    result: dict[str, Any] = field(default_factory=dict)
    retry_after_seconds: float | None = None
    error_type: str | None = None
    error_message: str | None = None

    def to_metadata(self) -> dict[str, Any]:
        """转换为任务结果审计 metadata。

        :return: 返回可持久化的执行结果摘要。
        """
        return {
            "status": self.status.value,
            "result": dict(self.result),
            "retry_after_seconds": self.retry_after_seconds,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }

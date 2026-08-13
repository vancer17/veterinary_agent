"""
=============================================================================
文件：src/vet_agent/task_routing/models.py
作用：定义多任务拆分迁移后的结构化任务路由领域模型。
范围：承载任务域目录、当前 session 活跃任务摘要、LiteLLM response_format 输出
      契约、不可变任务执行计划和响应 metadata 审计结构。
说明：本文件只定义跨数据链传递的稳定结构，不访问数据库、不调用外部服务、
      不执行 OPA 策略，也不实现问诊状态机。
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


DEFAULT_TASK_KEY = "__default__"
TASK_ROUTING_SCHEMA_VERSION = "v1"


class TaskRoutingStrategy(StrEnum):
    """表示任务路由可观察策略名称。

    :return: 无返回值；枚举值用于响应 metadata、trace 和测试断言。
    """

    LITELLM_RESPONSE_FORMAT = "litellm_response_format_task_router"


@dataclass(frozen=True)
class TaskRoutingDomain:
    """表示可被任务路由器使用的稳定任务域。

    :param domain: 任务域技术标识。
    :param title: 面向用户展示的任务标题。
    :param description: 面向运维和模型提示词的任务域说明。
    :param priority: 任务域默认排序优先级。
    :param version: 任务域配置版本。
    :param metadata: 任务域附加审计元数据。
    :return: 无返回值。
    """

    domain: str
    title: str
    description: str
    priority: int = 100
    version: str = TASK_ROUTING_SCHEMA_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_prompt_item(self) -> dict[str, Any]:
        """转换为模型任务路由提示词中的任务域目录项。

        :return: 返回不包含关键词、规则表达式或动作策略的任务域摘要。
        """
        return {
            "domain": self.domain,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
        }

    def to_metadata(self) -> dict[str, Any]:
        """转换为响应 metadata 中可审计的任务域摘要。

        :return: 返回任务域配置摘要。
        """
        return {
            "domain": self.domain,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "version": self.version,
        }


@dataclass(frozen=True)
class TaskRoutingDomainCatalog:
    """表示任务路由使用的任务域目录。

    :param domains: 已启用的任务域集合。
    :return: 无返回值；目录用于约束结构化模型输出和生成任务展示标题。
    """

    domains: tuple[TaskRoutingDomain, ...]

    def allowed_domains(self) -> set[str]:
        """读取允许模型返回的任务域标识集合。

        :return: 返回任务域标识集合。
        """
        return {item.domain for item in self.domains}

    def domain_by_key(self, domain: str) -> TaskRoutingDomain | None:
        """按技术标识读取任务域配置。

        :param domain: 任务域技术标识。
        :return: 返回匹配任务域；不存在时返回 None。
        """
        for item in self.domains:
            if item.domain == domain:
                return item
        return None

    def to_prompt_items(self) -> list[dict[str, Any]]:
        """转换为模型任务路由提示词使用的任务域目录。

        :return: 返回按优先级排序后的任务域摘要列表。
        """
        return [
            item.to_prompt_item()
            for item in sorted(self.domains, key=lambda value: (value.priority, value.domain))
        ]

    def to_metadata(self) -> list[dict[str, Any]]:
        """转换为响应 metadata 中的任务域目录摘要。

        :return: 返回按优先级排序后的任务域审计摘要。
        """
        return [
            item.to_metadata()
            for item in sorted(self.domains, key=lambda value: (value.priority, value.domain))
        ]


@dataclass(frozen=True)
class ActiveTaskState:
    """表示当前 session 中仍可被路由器引用的活跃问诊任务摘要。

    :param task_key: 活跃任务稳定键。
    :param domain: 活跃任务所属问诊域。
    :param phase: 活跃问诊阶段。
    :param chief_complaint: 活跃任务主诉摘要。
    :param missing_slots: 当前仍缺失的问诊槽位。
    :param asked_questions: 已追问的问题摘要。
    :return: 无返回值；该对象只用于任务归属，不承载医学裁决。
    """

    task_key: str
    domain: str
    phase: str
    chief_complaint: str = ""
    missing_slots: tuple[str, ...] = ()
    asked_questions: tuple[str, ...] = ()

    @classmethod
    def from_state(cls, task_key: str, state: dict[str, Any]) -> "ActiveTaskState":
        """从持久化问诊状态构造任务路由摘要。

        :param task_key: 持久化问诊任务键。
        :param state: 持久化问诊状态字典。
        :return: 返回可传入任务路由器的活跃任务摘要。
        """
        answerability = dict(state.get("answerability") or {})
        unresolved = tuple(str(item) for item in answerability.get("unresolved_slots") or ())
        return cls(
            task_key=task_key,
            domain=str(state.get("domain") or "general"),
            phase=str(state.get("phase") or ""),
            chief_complaint=str(state.get("chief_complaint") or "")[:200],
            missing_slots=unresolved,
            asked_questions=tuple(str(item)[:160] for item in state.get("asked_questions") or ()),
        )

    def to_prompt_item(self) -> dict[str, Any]:
        """转换为模型任务路由提示词中的活跃任务摘要。

        :return: 返回不包含完整历史对话或原始记忆的活跃任务摘要。
        """
        return {
            "task_key": self.task_key,
            "domain": self.domain,
            "phase": self.phase,
            "chief_complaint": self.chief_complaint,
            "missing_slots": list(self.missing_slots),
            "asked_questions": list(self.asked_questions[-3:]),
        }


@dataclass(frozen=True)
class TaskRoutingRequestContext:
    """表示任务路由策略裁决所需的可信请求范围摘要。

    :param request_id: 当前 Agent 回合请求标识。
    :param trace_id: 当前 Agent 回合链路追踪标识。
    :param user_id: 当前可信用户标识。
    :param pet_id: 当前可信宠物标识。
    :param session_id: 当前可信会话标识。
    :return: 无返回值；该对象不包含用户原始长文本。
    """

    request_id: str
    trace_id: str
    user_id: str
    pet_id: str
    session_id: str

    def to_policy_input(self) -> dict[str, str]:
        """转换为 OPA 使用的请求范围摘要。

        :return: 返回任务路由策略输入中的 context 字典。
        """
        return {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "user_id": self.user_id,
            "pet_id": self.pet_id,
            "session_id": self.session_id,
        }


class TaskRoutingTaskProposal(BaseModel):
    """定义 LiteLLM response_format 返回的单个任务路由候选。

    :return: 无返回值；该模型只校验结构，不表示候选已被策略允许执行。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    domain: str = Field(min_length=1, description="任务域技术标识，必须来自任务域目录。")
    text: str = Field(min_length=1, max_length=800, description="当前用户输入中属于该任务的文本片段。")
    existing_task_key: str | None = Field(
        default=None,
        description="如果本轮内容是在回答已有任务追问，则填写已有任务键；新任务为空。",
    )
    priority: int = Field(default=100, ge=1, le=100, description="本轮任务执行和展示优先级，数值越小越优先。")


class TaskRoutingProposal(BaseModel):
    """定义 LiteLLM response_format 返回的任务路由候选集合。

    :return: 无返回值；该模型用于替代手写 JSON 解析和旧版规则拆分回退。
    """

    model_config = ConfigDict(extra="forbid")

    tasks: list[TaskRoutingTaskProposal] = Field(
        min_length=1,
        max_length=5,
        description="本轮用户输入对应的任务路由候选列表。",
    )


@dataclass(frozen=True)
class RoutedTask:
    """表示已经通过结构校验和策略准入的单个可执行任务。

    :param task_id: 本轮响应内的任务展示标识。
    :param task_key: 当前 session 内持久化问诊状态使用的稳定任务键。
    :param text: 当前任务消费的用户文本。
    :param domain: 当前任务所属任务域。
    :param title: 当前任务面向用户展示的标题。
    :param priority: 当前任务执行优先级。
    :param existing_task_key: 被延续的已有任务键；新任务为空。
    :return: 无返回值。
    """

    task_id: str
    task_key: str
    text: str
    domain: str
    title: str
    priority: int
    existing_task_key: str | None = None

    @property
    def state_key(self) -> str:
        """读取兼容当前问诊状态存储的任务状态键。

        :return: 返回当前任务的稳定状态键。
        """
        return self.task_key

    def to_metadata(self) -> dict[str, Any]:
        """转换为响应 metadata 中的任务路由摘要。

        :return: 返回当前任务的可审计摘要。
        """
        return {
            "task_id": self.task_id,
            "task_key": self.task_key,
            "domain": self.domain,
            "title": self.title,
            "priority": self.priority,
            "existing_task_key": self.existing_task_key,
        }


@dataclass(frozen=True)
class TaskExecutionPlan:
    """表示本轮已经通过策略准入的不可变任务执行计划。

    :param tasks: 本轮需要执行的任务集合。
    :param strategy: 本轮任务计划生成策略。
    :param schema_version: 任务路由输出契约版本。
    :return: 无返回值；生成后主链路不得重新启用规则拆分或文本回填。
    """

    tasks: tuple[RoutedTask, ...]
    strategy: str
    schema_version: str = TASK_ROUTING_SCHEMA_VERSION

    def to_metadata(self) -> dict[str, Any]:
        """转换为响应 metadata 中的任务执行计划摘要。

        :return: 返回任务执行计划审计摘要。
        """
        return {
            "schema_version": self.schema_version,
            "strategy": self.strategy,
            "task_count": len(self.tasks),
            "tasks": [task.to_metadata() for task in self.tasks],
        }


@dataclass(frozen=True)
class TaskRoutingDecision:
    """表示任务路由服务输出的最终决策。

    :param plan: 已通过结构校验和策略准入的不可变任务执行计划。
    :param policy_metadata: 策略裁决和任务域目录审计摘要。
    :return: 无返回值。
    """

    plan: TaskExecutionPlan
    policy_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def tasks(self) -> tuple[RoutedTask, ...]:
        """读取本轮最终可执行任务集合。

        :return: 返回不可变任务元组。
        """
        return self.plan.tasks

    @property
    def strategy(self) -> str:
        """读取本轮任务路由策略名称。

        :return: 返回任务路由策略名称。
        """
        return self.plan.strategy

    def to_metadata(self) -> dict[str, Any]:
        """转换为响应 metadata 中的任务路由审计摘要。

        :return: 返回任务路由审计 metadata。
        """
        return {
            **self.plan.to_metadata(),
            "policy": self.policy_metadata,
        }

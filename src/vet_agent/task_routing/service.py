"""
=============================================================================
文件：src/vet_agent/task_routing/service.py
作用：编排多任务拆分迁移后的结构化任务路由流程。
范围：位于记忆读取之后、问诊语义抽取之前；负责调用 LiteLLM response_format、
      校验 Pydantic 契约、执行本地技术不变量校验、提交 OPA 策略准入并生成不可变
      TaskExecutionPlan。
说明：本服务不使用关键词拆分、不读取旧 consultation_rules、不提供规则回退；
      任一依赖不可用或契约非法均按 Fail Fast 处理。
=============================================================================
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from vet_agent import Settings

from .errors import TaskRoutingContractError, TaskRoutingDependencyError
from .models import (
    DEFAULT_TASK_KEY,
    ActiveTaskState,
    RoutedTask,
    TaskExecutionPlan,
    TaskRoutingDecision,
    TaskRoutingDomainCatalog,
    TaskRoutingProposal,
    TaskRoutingRequestContext,
    TaskRoutingStrategy,
    TaskRoutingTaskProposal,
)
from .policy import TaskRoutingPolicyClient, TaskRoutingPolicyInput, ensure_policy_allows
from .ports import StructuredChatClient, TaskRoutingDomainRepository


class TaskRoutingService:
    """提供结构化任务路由服务。

    :return: 无返回值。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        domain_repository: TaskRoutingDomainRepository,
        policy_client: TaskRoutingPolicyClient,
        structured_client: StructuredChatClient,
    ) -> None:
        """初始化结构化任务路由服务。

        :param settings: 当前运行环境配置。
        :param domain_repository: 任务域目录仓储。
        :param policy_client: 任务路由策略客户端。
        :param structured_client: 结构化模型客户端。
        :return: 无返回值。
        """
        self.settings = settings
        self.domain_repository = domain_repository
        self.policy_client = policy_client
        self.structured_client = structured_client

    async def route(
        self,
        *,
        context: TaskRoutingRequestContext,
        user_text: str,
        pet_context_summary: str,
        active_tasks: tuple[ActiveTaskState, ...],
        model: str | None = None,
    ) -> TaskRoutingDecision:
        """执行本轮结构化任务路由。

        :param context: 当前回合可信请求范围摘要。
        :param user_text: 当前用户输入原文。
        :param pet_context_summary: 已验证宠物上下文摘要。
        :param active_tasks: 当前 session 活跃任务摘要。
        :param model: 模型名称。
        :return: 返回已通过策略准入的任务路由决策。
        """
        text = user_text.strip()
        if not text:
            raise TaskRoutingContractError(
                "user text is required for task routing",
                details={"reason": "empty_user_text"},
            )
        catalog = self.domain_repository.task_routing_domains()
        proposal = await self._structured_proposal(
            user_text=text,
            pet_context_summary=pet_context_summary,
            active_tasks=active_tasks,
            catalog=catalog,
            model=model,
        )
        routed_tasks = self._normalize_tasks(proposal.tasks, active_tasks, catalog)
        policy_input = TaskRoutingPolicyInput(
            context=context,
            tasks=routed_tasks,
            catalog=catalog,
            active_tasks=active_tasks,
            max_task_count=self.settings.task_routing_max_task_count,
        )
        policy_decision = await self.policy_client.decide(policy_input)
        ensure_policy_allows(policy_decision)
        plan = TaskExecutionPlan(
            tasks=routed_tasks,
            strategy=TaskRoutingStrategy.LITELLM_RESPONSE_FORMAT.value,
        )
        return TaskRoutingDecision(
            plan=plan,
            policy_metadata={
                **policy_decision.to_metadata(),
                "domain_catalog": catalog.to_metadata(),
                "active_task_count": len(active_tasks),
            },
        )

    def is_ready(self) -> bool:
        """检查任务路由服务是否具备执行条件。

        :return: 模型、任务域目录和策略客户端均就绪时返回 True。
        """
        return (
            self.structured_client.available
            and self.domain_repository.is_ready()
            and self.policy_client.is_ready()
        )

    async def _structured_proposal(
        self,
        *,
        user_text: str,
        pet_context_summary: str,
        active_tasks: tuple[ActiveTaskState, ...],
        catalog: TaskRoutingDomainCatalog,
        model: str | None,
    ) -> TaskRoutingProposal:
        """调用 LiteLLM response_format 获取结构化任务路由候选。

        :param user_text: 当前用户输入原文。
        :param pet_context_summary: 已验证宠物上下文摘要。
        :param active_tasks: 当前 session 活跃任务摘要。
        :param catalog: 当前任务域目录。
        :param model: 模型名称。
        :return: 返回模型结构化任务路由候选。
        :raises TaskRoutingDependencyError: 模型不可用、调用失败或 response_format 不可用时抛出。
        """
        if not self.structured_client.available:
            raise TaskRoutingDependencyError(
                "task routing structured model is unavailable",
                details={"reason": "structured_model_unavailable"},
            )
        try:
            result = await self.structured_client.chat_structured(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是兽医多 Agent 系统中的 TaskRouterAgent。"
                            "你的职责是把用户本轮输入归属到一个或多个受控任务域；"
                            "不得诊断、不得建议治疗、不得根据关键词硬规则生成任务。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": self._prompt_payload(
                            user_text=user_text,
                            pet_context_summary=pet_context_summary,
                            active_tasks=active_tasks,
                            catalog=catalog,
                        ),
                    },
                ],
                response_model=TaskRoutingProposal,
                model=model,
                temperature=0.0,
            )
        except ValidationError as exc:
            raise TaskRoutingContractError(
                "task routing structured output violated its schema",
                details={"reason": "invalid_structured_output", "error_type": type(exc).__name__},
            ) from exc
        except Exception as exc:
            raise TaskRoutingDependencyError(
                "task routing structured model call failed",
                details={"error_type": type(exc).__name__},
            ) from exc
        return result

    def _prompt_payload(
        self,
        *,
        user_text: str,
        pet_context_summary: str,
        active_tasks: tuple[ActiveTaskState, ...],
        catalog: TaskRoutingDomainCatalog,
    ) -> str:
        """构造任务路由结构化模型调用的 JSON 输入。

        :param user_text: 当前用户输入原文。
        :param pet_context_summary: 已验证宠物上下文摘要。
        :param active_tasks: 当前 session 活跃任务摘要。
        :param catalog: 当前任务域目录。
        :return: 返回 JSON 字符串形式的任务路由提示负载。
        """
        return json.dumps(
            {
                "task": "将用户本轮输入归一为任务路由计划。",
                "schema_version": "v1",
                "rules": [
                    "只判断任务归属，不生成诊断、治疗、用药或安全动作。",
                    "domain 必须来自 domain_catalog，不允许创造新 domain。",
                    "如果本轮内容是在回答 active_tasks 中的已有追问，应填写 existing_task_key。",
                    "如果同一 domain 有多段相关内容，应合并为一个任务。",
                    "不同主题且可独立问诊或独立回复的内容应拆为不同任务。",
                    "text 只能来自当前用户输入的相关内容，不得加入用户未表达的信息。",
                    "不确定是否需要拆分时，优先返回一个 general 或最接近的单任务。",
                ],
                "max_task_count": self.settings.task_routing_max_task_count,
                "domain_catalog": catalog.to_prompt_items(),
                "active_tasks": [task.to_prompt_item() for task in active_tasks],
                "pet_context_summary": pet_context_summary,
                "user_text": user_text,
            },
            ensure_ascii=False,
        )

    def _normalize_tasks(
        self,
        proposals: list[TaskRoutingTaskProposal],
        active_tasks: tuple[ActiveTaskState, ...],
        catalog: TaskRoutingDomainCatalog,
    ) -> tuple[RoutedTask, ...]:
        """将结构化候选归一为不可变任务执行计划条目。

        :param proposals: 模型返回的结构化任务候选。
        :param active_tasks: 当前 session 活跃任务摘要。
        :param catalog: 当前任务域目录。
        :return: 返回已通过本地技术不变量校验的任务元组。
        :raises TaskRoutingContractError: 候选任务违反技术不变量时抛出。
        """
        if not proposals:
            raise TaskRoutingContractError(
                "task routing model returned no tasks",
                details={"reason": "empty_tasks"},
            )
        if len(proposals) > self.settings.task_routing_max_task_count:
            raise TaskRoutingContractError(
                "task routing model returned too many tasks",
                details={
                    "reason": "too_many_tasks",
                    "task_count": len(proposals),
                    "max_task_count": self.settings.task_routing_max_task_count,
                },
            )
        allowed_domains = catalog.allowed_domains()
        active_task_keys = {task.task_key for task in active_tasks}
        has_only_default_active = active_task_keys.issubset({DEFAULT_TASK_KEY})
        sorted_proposals = sorted(proposals, key=lambda item: (item.priority, item.domain, item.text))
        tasks: list[RoutedTask] = []
        used_keys: set[str] = set()
        for proposal in sorted_proposals:
            domain = proposal.domain.strip()
            if domain not in allowed_domains:
                raise TaskRoutingContractError(
                    "task routing model returned an unknown domain",
                    details={"reason": "unknown_domain", "domain": domain},
                )
            text = proposal.text.strip()
            if not text:
                raise TaskRoutingContractError(
                    "task routing model returned an empty task text",
                    details={"reason": "empty_task_text", "domain": domain},
                )
            existing_task_key = _clean_existing_task_key(proposal.existing_task_key)
            if existing_task_key is not None and existing_task_key not in active_task_keys:
                raise TaskRoutingContractError(
                    "task routing model referenced an unknown active task",
                    details={"reason": "unknown_existing_task_key", "task_key": existing_task_key},
                )
            task_key = self._task_key_for_proposal(
                proposal=proposal,
                proposal_count=len(sorted_proposals),
                active_task_keys=active_task_keys,
                has_only_default_active=has_only_default_active,
            )
            if task_key in used_keys:
                raise TaskRoutingContractError(
                    "task routing model returned duplicate task keys",
                    details={"reason": "duplicate_task_key", "task_key": task_key},
                )
            if task_key in active_task_keys and existing_task_key is None:
                raise TaskRoutingContractError(
                    "task routing model created a task that conflicts with an active task",
                    details={"reason": "active_task_collision", "task_key": task_key},
                )
            domain_config = catalog.domain_by_key(domain)
            if domain_config is None:
                raise TaskRoutingContractError(
                    "task routing domain catalog is inconsistent",
                    details={"reason": "missing_domain_config", "domain": domain},
                )
            used_keys.add(task_key)
            tasks.append(
                RoutedTask(
                    task_id=f"task_{len(tasks) + 1:03d}",
                    task_key=task_key,
                    text=text[:800],
                    domain=domain,
                    title=domain_config.title[:80],
                    priority=proposal.priority,
                    existing_task_key=existing_task_key,
                )
            )
        return tuple(tasks)

    def _task_key_for_proposal(
        self,
        *,
        proposal: TaskRoutingTaskProposal,
        proposal_count: int,
        active_task_keys: set[str],
        has_only_default_active: bool,
    ) -> str:
        """为结构化任务候选分配稳定任务状态键。

        :param proposal: 模型返回的结构化任务候选。
        :param proposal_count: 本轮候选任务数量。
        :param active_task_keys: 当前 session 活跃任务键集合。
        :param has_only_default_active: 当前是否只存在默认活跃任务。
        :return: 返回当前任务的稳定任务键。
        """
        existing_task_key = _clean_existing_task_key(proposal.existing_task_key)
        if existing_task_key is not None:
            # 默认单任务状态在一次多任务计划中需要迁移到具体任务域，避免
            # __default__ 状态与 task_consultation_states 两套存储同时表达同一任务。
            if existing_task_key == DEFAULT_TASK_KEY and proposal_count > 1:
                return proposal.domain.strip()
            return existing_task_key
        if proposal_count == 1 and (not active_task_keys or has_only_default_active):
            return DEFAULT_TASK_KEY
        return proposal.domain.strip()


def _clean_existing_task_key(value: str | None) -> str | None:
    """归一模型返回的已有任务键。

    :param value: 模型返回的 existing_task_key 字段。
    :return: 有效任务键或 None。
    """
    if value is None:
        return None
    text = value.strip()
    return text or None

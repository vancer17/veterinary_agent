"""
=============================================================================
文件：src/vet_agent/task_routing/policy.py
作用：定义任务路由策略准入契约与 OPA 策略客户端。
范围：位于 LiteLLM response_format 结构化任务路由之后、不可变 TaskExecutionPlan
      生成之前；负责校验任务数量、任务域、任务键、已有任务引用和环境准入。
说明：本文件不扫描用户自然语言、不执行关键词拆分、不修改模型输出，也不提供
      生产环境的本地规则回退。生产容器默认使用 OPA；本地策略仅供测试显式注入。
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from .errors import TaskRoutingDependencyError, TaskRoutingPolicyRejectedError
from .models import ActiveTaskState, RoutedTask, TaskRoutingDomainCatalog, TaskRoutingRequestContext


class TaskRoutingPolicyAction(StrEnum):
    """表示任务路由策略可以返回的有限动作集合。

    :return: 无返回值；枚举值用于 OPA 与 Agent 主链路之间的稳定动作契约。
    """

    ALLOW = "allow"
    REJECT = "reject"


@dataclass(frozen=True)
class TaskRoutingPolicyInput:
    """表示提交给任务路由 OPA 策略的完整结构化输入。

    :param context: 当前回合可信请求范围摘要。
    :param tasks: 已通过本地技术不变量校验的任务候选。
    :param catalog: 当前任务域目录。
    :param active_tasks: 当前 session 活跃任务摘要。
    :param max_task_count: 本轮允许的最大任务数量。
    :return: 无返回值。
    """

    context: TaskRoutingRequestContext
    tasks: tuple[RoutedTask, ...]
    catalog: TaskRoutingDomainCatalog
    active_tasks: tuple[ActiveTaskState, ...]
    max_task_count: int

    def to_payload(self) -> dict[str, Any]:
        """转换为 OPA Data API 请求所需的结构化 JSON 负载。

        :return: 返回不包含用户原始文本扫描路径的策略输入字典。
        """
        return {
            "context": self.context.to_policy_input(),
            "schema_version": "v1",
            "max_task_count": self.max_task_count,
            "allowed_domains": sorted(self.catalog.allowed_domains()),
            "active_task_keys": [task.task_key for task in self.active_tasks],
            "tasks": [
                {
                    "task_id": task.task_id,
                    "task_key": task.task_key,
                    "domain": task.domain,
                    "text_length": len(task.text),
                    "priority": task.priority,
                    "existing_task_key": task.existing_task_key,
                }
                for task in self.tasks
            ],
        }


@dataclass(frozen=True)
class TaskRoutingPolicyDecision:
    """表示任务路由策略返回的准入结果。

    :param action: 策略动作。
    :param allow: 是否允许生成并执行任务计划。
    :param message: 策略说明。
    :param reasons: 策略原因。
    :param metadata: 策略后端、策略路径和调用审计摘要。
    :return: 无返回值。
    """

    action: TaskRoutingPolicyAction
    allow: bool
    message: str
    reasons: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        """转换为响应 metadata 中的策略审计摘要。

        :return: 返回任务路由策略审计 metadata。
        """
        return {
            "action": self.action.value,
            "allow": self.allow,
            "message": self.message,
            "reasons": list(self.reasons),
            **dict(self.metadata),
        }


class TaskRoutingPolicyClient(Protocol):
    """定义任务路由策略客户端协议。

    :return: 无返回值；业务层通过该协议隔离 OPA 传输实现。
    """

    async def decide(self, policy_input: TaskRoutingPolicyInput) -> TaskRoutingPolicyDecision:
        """对本轮任务执行计划执行策略准入裁决。

        :param policy_input: 已完成本地技术不变量校验的任务路由策略输入。
        :return: 返回任务路由策略裁决。
        """
        ...

    def is_ready(self) -> bool:
        """检查任务路由策略客户端是否就绪。

        :return: 策略客户端具备调用所需配置时返回 True。
        """
        ...


class OpaTaskRoutingPolicyClient(TaskRoutingPolicyClient):
    """通过 OPA Data API 执行任务路由策略准入。

    :return: 无返回值；该实现是生产环境任务路由策略的默认后端。
    """

    def __init__(
        self,
        *,
        base_url: str,
        version: str,
        package_path: str,
        rule_name: str,
        auth_token: str | None = None,
        timeout_seconds: float = 3.0,
    ) -> None:
        """初始化 OPA 任务路由策略客户端。

        :param base_url: OPA Data API 基准地址，可包含网关前缀。
        :param version: OPA REST API 版本，例如 v1。
        :param package_path: OPA package 数据路径。
        :param rule_name: OPA 决策规则名称。
        :param auth_token: 可选的 OPA 鉴权令牌。
        :param timeout_seconds: 单次 OPA 请求超时时间。
        :return: 无返回值。
        """
        normalized_base_url = base_url.strip().rstrip("/")
        normalized_version = version.strip().strip("/")
        if normalized_base_url.endswith(f"/{normalized_version}"):
            self.base_url = normalized_base_url
        else:
            self.base_url = f"{normalized_base_url}/{normalized_version}"
        self.version = normalized_version
        self.package_path = package_path.strip("/")
        self.rule_name = rule_name.strip("/")
        self.auth_token = auth_token
        self.timeout_seconds = timeout_seconds

    async def decide(self, policy_input: TaskRoutingPolicyInput) -> TaskRoutingPolicyDecision:
        """向 OPA 提交任务路由结构化策略输入并解析结果。

        :param policy_input: 已完成本地不变量校验的任务路由策略输入。
        :return: 返回 OPA 任务路由策略裁决。
        :raises TaskRoutingDependencyError: OPA 不可用或响应契约不合法时抛出。
        """
        url = self._decision_url()
        headers = {"Content-Type": "application/json"}
        context = policy_input.context
        if context.request_id:
            headers["X-Request-ID"] = context.request_id
        if context.trace_id:
            headers["X-Trace-ID"] = context.trace_id
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    url,
                    headers=headers,
                    json={"input": policy_input.to_payload()},
                )
                response.raise_for_status()
                result = response.json()
        except Exception as exc:
            raise TaskRoutingDependencyError(
                "task routing OPA policy decision failed",
                details={"error_type": type(exc).__name__},
            ) from exc
        payload = self._unwrap_result(result)
        return _decision_from_payload(payload, backend="opa")

    def is_ready(self) -> bool:
        """检查 OPA 策略客户端配置是否完整。

        :return: OPA 连接参数齐全时返回 True。
        """
        return bool(self.base_url and self.version and self.package_path and self.rule_name)

    def _decision_url(self) -> str:
        """构造 OPA Data API 裁决 URL。

        :return: 返回可提交裁决请求的完整 URL。
        """
        package_parts = [quote(part, safe="") for part in self.package_path.replace("/", ".").split(".") if part]
        rule = quote(self.rule_name, safe="")
        path = "/".join([*package_parts, rule])
        return f"{self.base_url}/data/{path}"

    def _unwrap_result(self, result: Any) -> dict[str, Any]:
        """归一 OPA 客户端返回结构。

        :param result: OPA 客户端原始返回。
        :return: 返回裁决字典。
        :raises TaskRoutingDependencyError: 响应结构不合法时抛出。
        """
        if isinstance(result, dict) and isinstance(result.get("result"), dict):
            return dict(result["result"])
        if isinstance(result, dict):
            return dict(result)
        raise TaskRoutingDependencyError(
            "task routing OPA policy returned invalid decision payload",
            details={"reason": "invalid_opa_result_payload"},
        )


class LocalTaskRoutingPolicyClient(TaskRoutingPolicyClient):
    """显式注入的本地任务路由策略客户端。

    说明：该实现只消费已经结构化的任务计划，不扫描用户文本，不执行关键词拆分；
    仅供测试或特殊嵌入场景使用，生产容器不会自动选择该实现。

    :return: 无返回值。
    """

    async def decide(self, policy_input: TaskRoutingPolicyInput) -> TaskRoutingPolicyDecision:
        """对本轮任务计划执行本地最小准入裁决。

        :param policy_input: 已完成本地不变量校验的任务路由策略输入。
        :return: 返回任务路由策略裁决。
        """
        payload = policy_input.to_payload()
        reasons = _policy_violations(payload)
        if reasons:
            return TaskRoutingPolicyDecision(
                action=TaskRoutingPolicyAction.REJECT,
                allow=False,
                message="任务路由策略拒绝本轮任务计划。",
                reasons=tuple(reasons),
                metadata={"policy_backend": "local"},
            )
        return TaskRoutingPolicyDecision(
            action=TaskRoutingPolicyAction.ALLOW,
            allow=True,
            message="任务路由策略允许本轮任务计划。",
            metadata={"policy_backend": "local"},
        )

    def is_ready(self) -> bool:
        """检查本地任务路由策略客户端是否可用。

        :return: 始终返回 True。
        """
        return True


def ensure_policy_allows(decision: TaskRoutingPolicyDecision) -> None:
    """确保策略裁决允许当前任务计划继续执行。

    :param decision: 任务路由策略裁决。
    :return: 无返回值；裁决拒绝时抛出异常。
    :raises TaskRoutingPolicyRejectedError: 策略拒绝当前任务计划时抛出。
    """
    if decision.allow and decision.action == TaskRoutingPolicyAction.ALLOW:
        return
    raise TaskRoutingPolicyRejectedError(
        "task routing policy rejected the execution plan",
        details=decision.to_metadata(),
    )


def _decision_from_payload(payload: dict[str, Any], *, backend: str) -> TaskRoutingPolicyDecision:
    """将策略响应转换为任务路由策略裁决对象。

    :param payload: 策略返回的裁决字典。
    :param backend: 策略后端名称。
    :return: 返回任务路由策略裁决。
    :raises TaskRoutingDependencyError: 策略动作或响应结构不合法时抛出。
    """
    raw_action = str(payload.get("action") or ("allow" if payload.get("allow", True) else "reject"))
    try:
        action = TaskRoutingPolicyAction(raw_action)
    except ValueError as exc:
        raise TaskRoutingDependencyError(
            "task routing policy returned invalid action",
            details={"action": raw_action},
        ) from exc
    allow = bool(payload.get("allow", action == TaskRoutingPolicyAction.ALLOW))
    message = str(payload.get("message") or "")
    reasons = tuple(str(item) for item in payload.get("reasons", []) if str(item).strip())
    return TaskRoutingPolicyDecision(
        action=action,
        allow=allow,
        message=message or "任务路由策略完成裁决。",
        reasons=reasons,
        metadata={"policy_backend": backend, "policy_payload": payload},
    )


def _policy_violations(payload: dict[str, Any]) -> list[str]:
    """执行本地最小任务计划策略校验。

    :param payload: 任务路由策略输入字典。
    :return: 返回策略拒绝原因列表。
    """
    tasks = list(payload.get("tasks") or [])
    allowed_domains = set(payload.get("allowed_domains") or [])
    active_task_keys = set(payload.get("active_task_keys") or [])
    max_task_count = int(payload.get("max_task_count") or 1)
    reasons: list[str] = []
    if not tasks:
        reasons.append("task_routing_no_tasks")
    if len(tasks) > max_task_count:
        reasons.append("task_routing_too_many_tasks")
    task_keys: set[str] = set()
    for task in tasks:
        domain = str(task.get("domain") or "")
        task_key = str(task.get("task_key") or "")
        if domain not in allowed_domains:
            reasons.append(f"task_routing_invalid_domain:{domain}")
        if int(task.get("text_length") or 0) <= 0:
            reasons.append(f"task_routing_empty_text:{task_key}")
        if task_key in task_keys:
            reasons.append(f"task_routing_duplicate_task_key:{task_key}")
        task_keys.add(task_key)
        existing_task_key = task.get("existing_task_key")
        if existing_task_key and str(existing_task_key) not in active_task_keys:
            reasons.append(f"task_routing_invalid_existing_task_key:{existing_task_key}")
    return reasons

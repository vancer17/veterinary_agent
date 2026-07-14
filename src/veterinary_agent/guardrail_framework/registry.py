##################################################################################################
# 文件: src/veterinary_agent/guardrail_framework/registry.py
# 作用: 实现 GuardrailFramework 的内存策略注册表与 handler 注册表，供应用内默认装配和测试使用。
# 边界: 不加载外部配置文件、不实现 L2 业务 handler、不执行护栏运行或 trace 写入。
##################################################################################################

from threading import RLock

from veterinary_agent.guardrail_framework.dto import GuardrailPolicyDto
from veterinary_agent.guardrail_framework.enums import (
    GuardrailFrameworkErrorCode,
    GuardrailFrameworkOperation,
    GuardrailStage,
)
from veterinary_agent.guardrail_framework.errors import GuardrailFrameworkError
from veterinary_agent.guardrail_framework.ports import GuardrailHandler


class InMemoryGuardrailPolicyRegistry:
    """GuardrailFramework 内存策略注册表。"""

    def __init__(self, policies: list[GuardrailPolicyDto] | None = None) -> None:
        """初始化内存策略注册表。

        :param policies: 可选初始策略列表。
        :return: None。
        """

        self._policies: dict[tuple[GuardrailStage, str, str], GuardrailPolicyDto] = {}
        self._lock = RLock()
        for policy in policies or []:
            self.register_policy(policy)

    def register_policy(self, policy: GuardrailPolicyDto) -> None:
        """注册或覆盖一条护栏策略。

        :param policy: 待注册的护栏策略。
        :return: None。
        :raises GuardrailFrameworkError: 当策略字段不满足框架调度要求时抛出。
        """

        self.validate_policy(policy)
        key = (policy.stage, policy.policy_id, policy.policy_version)
        with self._lock:
            self._policies[key] = policy

    def resolve_policies(
        self,
        *,
        stage: GuardrailStage,
        generation_profile: str | None = None,
    ) -> list[GuardrailPolicyDto]:
        """按阶段解析已启用护栏策略。

        :param stage: 待执行的护栏阶段。
        :param generation_profile: 可选业务生成剖面；当前内存实现不按剖面筛选。
        :return: 与阶段匹配的已启用护栏策略列表。
        :raises GuardrailFrameworkError: 当阶段没有可执行策略时抛出。
        """

        del generation_profile
        with self._lock:
            policies = [
                policy
                for policy in self._policies.values()
                if policy.stage is stage and policy.enabled
            ]
        if not policies:
            raise GuardrailFrameworkError(
                code=GuardrailFrameworkErrorCode.GUARDRAIL_POLICY_NOT_FOUND,
                operation=GuardrailFrameworkOperation.RESOLVE_POLICY,
                message="未找到当前阶段可执行的护栏策略",
                retryable=False,
                stage=stage,
            )
        return sorted(
            policies, key=lambda policy: (policy.policy_id, policy.policy_version)
        )

    def validate_policy(self, policy: GuardrailPolicyDto) -> None:
        """校验单条护栏策略是否可被框架调度。

        :param policy: 待校验的护栏策略。
        :return: None。
        :raises GuardrailFrameworkError: 当策略缺少 handler 或版本字段时抛出。
        """

        if not policy.handler_ref:
            raise GuardrailFrameworkError(
                code=GuardrailFrameworkErrorCode.GUARDRAIL_POLICY_SCHEMA_INVALID,
                operation=GuardrailFrameworkOperation.VALIDATE_POLICY,
                message="护栏策略缺少 handler_ref",
                retryable=False,
                stage=policy.stage,
                policy_id=policy.policy_id,
            )
        if not policy.policy_version:
            raise GuardrailFrameworkError(
                code=GuardrailFrameworkErrorCode.GUARDRAIL_POLICY_VERSION_UNAVAILABLE,
                operation=GuardrailFrameworkOperation.VALIDATE_POLICY,
                message="护栏策略缺少 policy_version",
                retryable=False,
                stage=policy.stage,
                policy_id=policy.policy_id,
                handler_ref=policy.handler_ref,
            )


class InMemoryGuardrailHandlerRegistry:
    """GuardrailFramework 内存 handler 注册表。"""

    def __init__(self, handlers: dict[str, GuardrailHandler] | None = None) -> None:
        """初始化内存 handler 注册表。

        :param handlers: 可选初始 handler 映射。
        :return: None。
        """

        self._handlers: dict[str, GuardrailHandler] = {}
        self._lock = RLock()
        for handler_ref, handler in (handlers or {}).items():
            self.register_handler(handler_ref=handler_ref, handler=handler)

    def register_handler(self, *, handler_ref: str, handler: GuardrailHandler) -> None:
        """注册或覆盖一个 handler。

        :param handler_ref: handler 稳定引用。
        :param handler: 待注册的 handler 实例。
        :return: None。
        :raises GuardrailFrameworkError: 当 handler 引用为空时抛出。
        """

        normalized_ref = handler_ref.strip()
        if not normalized_ref:
            raise GuardrailFrameworkError(
                code=GuardrailFrameworkErrorCode.GUARDRAIL_HANDLER_NOT_REGISTERED,
                operation=GuardrailFrameworkOperation.EXECUTE_HANDLER,
                message="handler_ref 不得为空",
                retryable=False,
            )
        with self._lock:
            self._handlers[normalized_ref] = handler

    def get_handler(self, handler_ref: str) -> GuardrailHandler | None:
        """读取指定 handler。

        :param handler_ref: handler 稳定引用。
        :return: 已注册 handler；不存在时返回 None。
        """

        with self._lock:
            return self._handlers.get(handler_ref.strip())


__all__: tuple[str, ...] = (
    "InMemoryGuardrailHandlerRegistry",
    "InMemoryGuardrailPolicyRegistry",
)

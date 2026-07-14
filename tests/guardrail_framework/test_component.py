##################################################################################################
# 文件: tests/guardrail_framework/test_component.py
# 作用: 验证 GuardrailFramework 的组件级配置暴露、默认策略装配和 TODO 依赖 fail-closed 行为。
# 边界: 只验证框架契约与默认空壳行为，不实现 L2 兽医安全规则、不连接真实外部服务。
##################################################################################################

import asyncio
from typing import cast

from veterinary_agent.config import GuardrailFrameworkSettings, RuntimeConfigNamespace
from veterinary_agent.guardrail_framework import (
    GuardActionType,
    GuardrailFrameworkErrorCode,
    GuardrailStage,
    GuardrailStatus,
    build_default_guardrail_policy_registry,
    create_default_guardrail_framework,
)

from .helpers import action_types, build_provider, build_request


def test_runtime_config_exposes_guardrail_framework_namespace() -> None:
    """验证 RuntimeConfig 暴露 GuardrailFramework 命名空间和点路径读取能力。

    :return: None。
    """

    provider = build_provider()

    settings = cast(
        GuardrailFrameworkSettings,
        provider.get_namespace(RuntimeConfigNamespace.GUARDRAIL_FRAMEWORK),
    )
    trace_summary = cast(
        dict[str, object],
        provider.current_snapshot().trace_safe_summary["guardrail_framework"],
    )

    assert settings.framework_version == "guardrail-framework.v1"
    assert (
        provider.get_value(key="guardrail_framework.framework_version")
        == "guardrail-framework.v1"
    )
    assert trace_summary["framework_version"] == "guardrail-framework.v1"


def test_default_policy_registry_contains_three_guardrail_stages() -> None:
    """验证默认策略注册表包含三个标准护栏阶段。

    :return: None。
    """

    provider = build_provider()
    settings = provider.current_snapshot().guardrail_framework
    registry = build_default_guardrail_policy_registry(settings)

    assert len(registry.resolve_policies(stage=GuardrailStage.PRE_GENERATION)) == 1
    assert (
        len(registry.resolve_policies(stage=GuardrailStage.POST_GENERATION_REVIEW)) == 1
    )
    assert len(registry.resolve_policies(stage=GuardrailStage.DETERMINISTIC_GATE)) == 1


def test_default_todo_handler_fails_closed_for_post_generation_review() -> None:
    """验证默认 TODO handler 会按 fail-closed 产生阻断结果。

    :return: None。
    """

    provider = build_provider()
    framework = create_default_guardrail_framework(runtime_config_provider=provider)

    result = asyncio.run(
        framework.run_post_generation_review(
            build_request(
                stage=GuardrailStage.POST_GENERATION_REVIEW,
                provider=provider,
            )
        )
    )

    assert result.status is GuardrailStatus.BLOCKED
    assert result.publish_allowed is False
    assert (
        result.error_code
        is GuardrailFrameworkErrorCode.GUARDRAIL_HANDLER_NOT_IMPLEMENTED
    )
    assert action_types(result) == [GuardActionType.DEGRADE, GuardActionType.BLOCK]

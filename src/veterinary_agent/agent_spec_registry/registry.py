##################################################################################################
# 文件: src/veterinary_agent/agent_spec_registry/registry.py
# 作用: 基于默认 AgentSpec 目录创建 AgentSpecRegistry，并在应用启动期校验规格唯一性与模型 profile 引用。
# 边界: 不实现远程注册中心、动态灰度、数据库读取或业务 Agent；真实治理平台后续可替换 AgentSpecRegistry 端口。
##################################################################################################

from typing import TypeAlias

from veterinary_agent.agent_runner import (
    AgentRunnerError,
    AgentRunnerErrorCode,
    AgentRunnerOperation,
    AgentSpecDto,
    AgentSpecRegistry,
    InMemoryAgentSpecRegistry,
)
from veterinary_agent.config import RuntimeConfigSnapshot

from .catalog import build_default_agent_specs

AgentSpecKey: TypeAlias = tuple[str, str]


def _spec_key(spec: AgentSpecDto) -> AgentSpecKey:
    """构建 Agent 规格唯一键。

    :param spec: Agent 规格。
    :return: 由 Agent ID 与版本组成的唯一键。
    """

    return (spec.agent_id, spec.agent_version)


def _model_profile_ids(snapshot: RuntimeConfigSnapshot) -> set[str]:
    """收集当前 LlmGateway 配置中的模型 profile ID。

    :param snapshot: 当前 RuntimeConfig 快照。
    :return: 模型 profile ID 集合。
    """

    return {profile.model_profile_id for profile in snapshot.llm_gateway.model_profiles}


def _validate_unique_specs(specs: tuple[AgentSpecDto, ...]) -> None:
    """校验默认规格目录不存在重复键。

    :param specs: 默认 Agent 规格元组。
    :return: None。
    :raises AgentRunnerError: 当存在重复 Agent ID 与版本组合时抛出。
    """

    seen: set[AgentSpecKey] = set()
    for spec in specs:
        key = _spec_key(spec)
        if key in seen:
            raise AgentRunnerError(
                code=AgentRunnerErrorCode.AGENT_SPEC_VERSION_UNAVAILABLE,
                operation=AgentRunnerOperation.VALIDATE_AGENT_SPEC,
                message="默认 AgentSpec 目录存在重复规格版本",
                agent_id=spec.agent_id,
                agent_version=spec.agent_version,
                model_profile_id=spec.model_profile,
                conflict_with={"reason": "duplicate_default_spec"},
            )
        seen.add(key)


def _validate_spec_model_profiles(
    *,
    snapshot: RuntimeConfigSnapshot,
    specs: tuple[AgentSpecDto, ...],
) -> None:
    """校验 Agent 规格引用的模型 profile 均存在。

    :param snapshot: 当前 RuntimeConfig 快照。
    :param specs: 默认 Agent 规格元组。
    :return: None。
    :raises AgentRunnerError: 当任一规格引用不存在的模型 profile 时抛出。
    """

    profile_ids = _model_profile_ids(snapshot)
    for spec in specs:
        if spec.model_profile in profile_ids:
            continue
        raise AgentRunnerError(
            code=AgentRunnerErrorCode.AGENT_RUNNER_NOT_READY,
            operation=AgentRunnerOperation.VALIDATE_AGENT_SPEC,
            message="默认 AgentSpec 引用了不存在的 LlmGateway model profile",
            agent_id=spec.agent_id,
            agent_version=spec.agent_version,
            model_profile_id=spec.model_profile,
            conflict_with={
                "available_model_profiles": sorted(profile_ids),
                "reason": "model_profile_missing",
            },
        )


def create_default_agent_spec_registry(
    snapshot: RuntimeConfigSnapshot,
) -> AgentSpecRegistry:
    """创建默认 AgentSpecRegistry。

    :param snapshot: 当前 RuntimeConfig 快照。
    :return: 已注册默认 Agent 规格的内存版 AgentSpecRegistry；LlmGateway 禁用时为空注册表。
    :raises AgentRunnerError: 当默认规格重复或引用无效模型 profile 时抛出。
    :raises ValueError: 当 LlmGateway 已启用但没有 model profile 时抛出。
    """

    specs = build_default_agent_specs(snapshot)
    _validate_unique_specs(specs)
    _validate_spec_model_profiles(snapshot=snapshot, specs=specs)
    return InMemoryAgentSpecRegistry(specs)


__all__: tuple[str, ...] = ("create_default_agent_spec_registry",)

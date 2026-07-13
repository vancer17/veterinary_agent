##################################################################################################
# 文件: tests/vet_context_builder/test_config_and_ports.py
# 作用: 验证 VetContextBuilder 配置加载、RuntimeConfig 命名空间和 TODO 来源端口降级契约。
# 边界: 不访问真实 ConversationStore、CheckpointStore、远程画像、记忆或化验服务。
##################################################################################################

import asyncio
from pathlib import Path

from pydantic import ValidationError
import pytest

from veterinary_agent.config import (
    RuntimeConfigNamespace,
    VetContextBuilderSettings,
    create_runtime_config_provider,
    load_vet_context_builder_settings,
)
from veterinary_agent.vet_context_builder import (
    ContextSourceLoadRequestDto,
    ContextSourceStatus,
    ContextSourceType,
    build_default_context_source_ports,
)


def _source_request() -> ContextSourceLoadRequestDto:
    """构建 TODO 来源端口读取请求。

    :return: 包含完整身份与近期消息上限的来源请求。
    """

    return ContextSourceLoadRequestDto(
        request_id="req_context_1",
        trace_id="trace_context_1",
        session_id="session_context_1",
        user_id="user_context_1",
        current_pet_id="pet_context_1",
        task_id="task_context_1",
        params_version="params.v1",
        recent_message_limit=20,
    )


def test_load_default_context_builder_settings() -> None:
    """验证默认 YAML 配置可加载且 P0 基线有效。

    :return: None。
    """

    settings = load_vet_context_builder_settings()

    assert settings.enabled is True
    assert settings.config_version == "vet-context-builder-config.v1"
    assert "species" in settings.p0_fields
    assert settings.timeouts.safety_total_seconds < settings.timeouts.total_seconds


def test_custom_context_builder_config_rejects_missing_species(
    tmp_path: Path,
) -> None:
    """验证自定义配置不能从 P0 基线中移除 species。

    :param tmp_path: pytest 提供的临时目录。
    :return: None。
    """

    config_path = tmp_path / "vet_context_builder.yaml"
    config_path.write_text(
        "enabled: true\np0_fields:\n  - age\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_vet_context_builder_settings(config_path)


def test_runtime_config_exposes_context_builder_namespace() -> None:
    """验证 RuntimeConfig 可按一级命名空间读取 VetContextBuilder 配置。

    :return: None。
    """

    provider = create_runtime_config_provider()

    namespace = provider.get_namespace(RuntimeConfigNamespace.VET_CONTEXT_BUILDER)
    value = provider.get_value(key="vet_context_builder.max_prompt_blocks")

    assert isinstance(namespace, VetContextBuilderSettings)
    assert namespace.config_version == "vet-context-builder-config.v1"
    assert value == 16


def test_default_source_ports_return_explicit_todo_degradation() -> None:
    """验证未接入的领域来源统一返回显式 unavailable 结果。

    :return: None。
    """

    ports = build_default_context_source_ports()
    source_types = [port.source_type for port in ports]
    core_port = next(
        port
        for port in ports
        if port.source_type is ContextSourceType.CORE_FACT_SNAPSHOT
    )

    result = asyncio.run(core_port.load(_source_request()))

    assert result.status is ContextSourceStatus.UNAVAILABLE
    assert result.error_code == "CONTEXT_SOURCE_NOT_IMPLEMENTED"
    assert result.source_refs[0].pet_id == "pet_context_1"
    assert len(source_types) == len(set(source_types))
    assert set(source_types) == set(ContextSourceType) - {
        ContextSourceType.CURRENT_TASK
    }

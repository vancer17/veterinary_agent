##################################################################################################
# 文件: tests/nonmedical_pet_care_agent/test_runtime_config.py
# 作用: 验证 NonmedicalPetCareAgent 配置加载、RuntimeConfig 聚合和 trace-safe 摘要。
# 边界: 不启动应用、不连接外部服务、不执行非医疗建议生成。
##################################################################################################

from veterinary_agent.config import (
    NonmedicalPetCareAgentSettings,
    RuntimeConfigNamespace,
    create_runtime_config_provider,
    load_nonmedical_pet_care_agent_settings,
)


def test_load_nonmedical_pet_care_settings_from_default_yaml() -> None:
    """验证默认 YAML 可加载 NonmedicalPetCareAgent 配置。

    :return: None。
    """

    settings = load_nonmedical_pet_care_agent_settings()

    assert settings.enabled is True
    assert settings.nonmedical_agent_version == "nonmedical-pet-care-agent.v1"
    assert settings.writer_agent_id == "nonmedical_advice_writer"


def test_runtime_config_snapshot_contains_nonmedical_settings() -> None:
    """验证 RuntimeConfig 快照包含 NonmedicalPetCareAgent 配置。

    :return: None。
    """

    provider = create_runtime_config_provider()
    snapshot = provider.current_snapshot()

    assert snapshot.nonmedical_pet_care.nonmedical_agent_version == (
        "nonmedical-pet-care-agent.v1"
    )
    assert snapshot.nonmedical_pet_care.rag.enabled is True


def test_runtime_config_provider_reads_nonmedical_namespace() -> None:
    """验证 RuntimeConfig provider 可按命名空间读取非医疗配置。

    :return: None。
    """

    provider = create_runtime_config_provider()

    namespace = provider.get_namespace(RuntimeConfigNamespace.NONMEDICAL_PET_CARE)

    assert isinstance(namespace, NonmedicalPetCareAgentSettings)
    assert namespace.writer_agent_id == "nonmedical_advice_writer"


def test_trace_safe_summary_contains_nonmedical_summary() -> None:
    """验证 trace-safe 摘要包含非医疗配置摘要。

    :return: None。
    """

    provider = create_runtime_config_provider()
    summary = provider.trace_safe_summary()

    nonmedical_summary = summary["nonmedical_pet_care"]
    assert isinstance(nonmedical_summary, dict)
    assert nonmedical_summary["enabled"] is True
    assert nonmedical_summary["nonmedical_agent_version"] == (
        "nonmedical-pet-care-agent.v1"
    )

##################################################################################################
# 文件: tests/education_agent/test_runtime_config.py
# 作用: 验证 EducationAgent 运行配置可被默认 YAML、RuntimeConfig 快照与命名空间读取正确装配。
# 边界: 只测试配置组件对 EducationAgent 的公共装配结果，不初始化 Agent、RAG、Trace 或 GraphRuntime。
##################################################################################################

from veterinary_agent.config import (
    EducationAgentSettings,
    RuntimeConfigNamespace,
    create_runtime_config_provider,
    load_education_agent_settings,
)


def test_load_education_agent_settings_from_default_yaml() -> None:
    """验证 EducationAgent 默认 YAML 配置可被加载为严格配置对象。

    :return: None。
    """

    settings = load_education_agent_settings()

    assert isinstance(settings, EducationAgentSettings)
    assert settings.enabled is True
    assert settings.education_agent_version == "education-agent.v1"
    assert settings.rag.default_collections == ["vet_kb_public_mvp"]


def test_runtime_config_snapshot_contains_education_agent_settings() -> None:
    """验证 RuntimeConfig 快照包含 EducationAgent 配置命名空间。

    :return: None。
    """

    provider = create_runtime_config_provider()
    snapshot = provider.current_snapshot()

    assert snapshot.education_agent.education_agent_version == "education-agent.v1"
    assert snapshot.education_agent.rag.enabled is True
    assert snapshot.education_agent.timeouts.total_seconds >= (
        snapshot.education_agent.timeouts.writer_seconds
    )


def test_runtime_config_provider_reads_education_agent_namespace() -> None:
    """验证 RuntimeConfig provider 可通过公共命名空间读取 EducationAgent 配置。

    :return: None。
    """

    provider = create_runtime_config_provider()
    namespace = provider.get_namespace(RuntimeConfigNamespace.EDUCATION_AGENT)

    assert isinstance(namespace, EducationAgentSettings)
    assert namespace.writer_agent_id == "education_writer"
    assert namespace.grounding_checker_agent_id == "education_grounding_checker"


def test_runtime_config_provider_reads_education_agent_value_by_key() -> None:
    """验证 RuntimeConfig provider 可通过点路径读取 EducationAgent 配置值。

    :return: None。
    """

    provider = create_runtime_config_provider()
    snapshot = provider.current_snapshot()

    assert provider.get_value(
        key="education_agent.rag.top_k",
        config_snapshot_id=snapshot.config_snapshot_id,
    ) == 5
    assert provider.get_value(
        key="education_agent.timeouts.rag_seconds",
        config_snapshot_id=snapshot.config_snapshot_id,
    ) == 1.5


def test_trace_safe_summary_contains_education_agent_summary() -> None:
    """验证 trace-safe 配置摘要包含 EducationAgent 的低敏摘要。

    :return: None。
    """

    provider = create_runtime_config_provider()
    summary = provider.trace_safe_summary()

    education_summary = summary["education_agent"]
    assert isinstance(education_summary, dict)
    assert education_summary["enabled"] is True
    assert education_summary["education_agent_version"] == "education-agent.v1"
    rag_summary = education_summary["rag"]
    assert isinstance(rag_summary, dict)
    assert rag_summary["enabled"] is True
    assert rag_summary["top_k"] == 5

##################################################################################################
# 文件: tests/config/test_runtime_config_component.py
# 作用: 验证 RuntimeConfig 组件的配置加载、快照生成、安全锁、命名空间读取与 trace-safe 摘要约束。
# 边界: 仅测试应用内配置组件；不初始化数据库、不启动 FastAPI、不创建 GraphRuntime 或业务 Agent。
##################################################################################################

from pathlib import Path

import pytest

from veterinary_agent import (
    ApiIngressSettings,
    CheckpointStoreSettings,
    RuntimeConfigError,
    RuntimeConfigErrorCode,
    RuntimeConfigNamespace,
    RuntimeConfigOperation,
    RuntimeConfigSafetyLockSettings,
    RuntimeConfigSettings,
    build_runtime_config_snapshot,
    create_runtime_config_provider,
    load_runtime_config_settings,
)


def _runtime_config_settings(**updates: object) -> RuntimeConfigSettings:
    """构建测试用 RuntimeConfig 组件自身配置。

    :param updates: RuntimeConfigSettings 字段覆盖项。
    :return: 已合并覆盖项的 RuntimeConfigSettings。
    """

    return RuntimeConfigSettings().model_copy(update=updates)


def test_load_runtime_config_settings_from_default_yaml() -> None:
    """验证 RuntimeConfig 可从默认配置源加载。

    :return: None。
    """

    settings = load_runtime_config_settings()

    assert settings.params_version == "params.v1"
    assert settings.config_schema_version == "runtime-config.v1"
    assert settings.safety_locks.enforce_pet_session_policy is True


def test_load_runtime_config_settings_from_custom_yaml(tmp_path: Path) -> None:
    """验证 RuntimeConfig 可从指定 YAML 文件加载。

    :param tmp_path: pytest 提供的临时目录。
    :return: None。
    """

    config_path = tmp_path / "runtime_config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "params_version: params.test",
                "config_schema_version: runtime-config.test",
                "safety_locks:",
                "  enforce_pet_session_policy: true",
                "  require_output_safety_review: true",
                "  fail_closed_guardrails: true",
                "  prevent_direct_model_publish: true",
                "  forbid_sensitive_observability_labels: true",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_runtime_config_settings(config_path)

    assert settings.params_version == "params.test"
    assert settings.config_schema_version == "runtime-config.test"


def test_runtime_config_snapshot_id_is_stable_for_same_effective_config() -> None:
    """验证相同有效配置生成稳定快照 ID。

    :return: None。
    """

    runtime_settings = _runtime_config_settings(params_version="params.stable")
    api_ingress_settings = ApiIngressSettings()
    checkpoint_store_settings = CheckpointStoreSettings()

    first_snapshot = build_runtime_config_snapshot(
        runtime_config_settings=runtime_settings,
        api_ingress_settings=api_ingress_settings,
        checkpoint_store_settings=checkpoint_store_settings,
    )
    second_snapshot = build_runtime_config_snapshot(
        runtime_config_settings=runtime_settings,
        api_ingress_settings=api_ingress_settings,
        checkpoint_store_settings=checkpoint_store_settings,
    )

    assert first_snapshot.config_snapshot_id == second_snapshot.config_snapshot_id
    assert first_snapshot.params_version == "params.stable"
    assert first_snapshot.trace_safe_summary["config_snapshot_id"] == (
        first_snapshot.config_snapshot_id
    )


def test_runtime_config_rejects_disabled_safety_lock() -> None:
    """验证 RuntimeConfig 会拒绝被关闭的安全锁定项。

    :return: None。
    """

    runtime_settings = _runtime_config_settings(
        safety_locks=RuntimeConfigSafetyLockSettings(
            enforce_pet_session_policy=False,
        )
    )

    with pytest.raises(RuntimeConfigError) as exc_info:
        build_runtime_config_snapshot(
            runtime_config_settings=runtime_settings,
            api_ingress_settings=ApiIngressSettings(),
            checkpoint_store_settings=CheckpointStoreSettings(),
        )

    assert exc_info.value.code is RuntimeConfigErrorCode.CONFIG_SAFETY_LOCK_VIOLATION
    assert exc_info.value.operation is RuntimeConfigOperation.VALIDATE_CANDIDATE_CONFIG


def test_runtime_config_provider_reads_namespaces() -> None:
    """验证 RuntimeConfig provider 可按命名空间读取配置对象。

    :return: None。
    """

    runtime_settings = _runtime_config_settings(params_version="params.namespace")
    api_ingress_settings = ApiIngressSettings()
    checkpoint_store_settings = CheckpointStoreSettings()
    provider = create_runtime_config_provider(
        runtime_config_settings=runtime_settings,
        api_ingress_settings=api_ingress_settings,
        checkpoint_store_settings=checkpoint_store_settings,
    )

    assert provider.is_ready() is True
    assert provider.get_namespace(RuntimeConfigNamespace.RUNTIME_CONFIG) is (
        provider.current_snapshot().runtime_config
    )
    assert provider.get_namespace(RuntimeConfigNamespace.API_INGRESS) is (
        provider.current_snapshot().api_ingress
    )
    assert provider.get_namespace(RuntimeConfigNamespace.CHECKPOINT_STORE) is (
        provider.current_snapshot().checkpoint_store
    )


def test_runtime_config_provider_reads_value_by_key() -> None:
    """验证 RuntimeConfig provider 可按点路径读取配置值。

    :return: None。
    """

    runtime_settings = _runtime_config_settings(params_version="params.value")
    provider = create_runtime_config_provider(
        runtime_config_settings=runtime_settings,
        api_ingress_settings=ApiIngressSettings(),
        checkpoint_store_settings=CheckpointStoreSettings(),
    )
    snapshot = provider.current_snapshot()

    assert provider.get_value(key="runtime_config.params_version") == "params.value"
    assert (
        provider.get_value(
            key="api_ingress.service_name",
            config_snapshot_id=snapshot.config_snapshot_id,
        )
        == snapshot.api_ingress.service_name
    )
    assert provider.get_value(key="checkpoint_store.history.max_list_limit") == (
        snapshot.checkpoint_store.history.max_list_limit
    )


def test_runtime_config_provider_get_value_rejects_unknown_snapshot_id() -> None:
    """验证按配置键读取值时会校验指定快照 ID。

    :return: None。
    """

    provider = create_runtime_config_provider(
        api_ingress_settings=ApiIngressSettings(),
        checkpoint_store_settings=CheckpointStoreSettings(),
    )

    with pytest.raises(RuntimeConfigError) as exc_info:
        provider.get_value(
            key="runtime_config.params_version",
            config_snapshot_id="sha256:missing",
        )

    assert exc_info.value.code is RuntimeConfigErrorCode.CONFIG_SNAPSHOT_NOT_FOUND
    assert exc_info.value.operation is RuntimeConfigOperation.GET_CONFIG_VALUE


def test_runtime_config_provider_get_value_rejects_unknown_key() -> None:
    """验证按配置键读取值时会拒绝不存在的键。

    :return: None。
    """

    provider = create_runtime_config_provider(
        api_ingress_settings=ApiIngressSettings(),
        checkpoint_store_settings=CheckpointStoreSettings(),
    )

    with pytest.raises(RuntimeConfigError) as exc_info:
        provider.get_value(key="api_ingress.not_exists")

    assert exc_info.value.code is RuntimeConfigErrorCode.CONFIG_SCHEMA_INVALID
    assert exc_info.value.operation is RuntimeConfigOperation.GET_CONFIG_VALUE


def test_runtime_config_trace_safe_summary_rejects_unknown_snapshot_id() -> None:
    """验证读取 trace-safe 摘要时会校验指定快照 ID。

    :return: None。
    """

    provider = create_runtime_config_provider(
        api_ingress_settings=ApiIngressSettings(),
        checkpoint_store_settings=CheckpointStoreSettings(),
    )

    with pytest.raises(RuntimeConfigError) as exc_info:
        provider.trace_safe_summary(config_snapshot_id="sha256:missing")

    assert exc_info.value.code is RuntimeConfigErrorCode.CONFIG_SNAPSHOT_NOT_FOUND
    assert exc_info.value.operation is (
        RuntimeConfigOperation.GET_TRACE_SAFE_CONFIG_SUMMARY
    )


def test_runtime_config_trace_safe_summary_contains_no_sensitive_fields() -> None:
    """验证 RuntimeConfig trace-safe 摘要不包含敏感配置字段。

    :return: None。
    """

    provider = create_runtime_config_provider(
        api_ingress_settings=ApiIngressSettings(),
        checkpoint_store_settings=CheckpointStoreSettings(),
    )
    summary = provider.trace_safe_summary()

    assert "database_url" not in str(summary)
    assert "api_key" not in str(summary)
    assert "password" not in str(summary)

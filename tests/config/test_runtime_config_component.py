##################################################################################################
# 文件: tests/config/test_runtime_config_component.py
# 作用: 验证 RuntimeConfig 组件的配置加载、快照生成、安全锁、跨组件关系、命名空间读取与 trace-safe 摘要约束。
# 边界: 仅测试应用内配置组件；不初始化数据库、不启动 FastAPI、不创建 GraphRuntime 或业务 Agent。
##################################################################################################

from datetime import UTC
from pathlib import Path

from pydantic import ValidationError
import pytest

from veterinary_agent import (
    ApiIngressSettings,
    CheckpointStoreSettings,
    ObservabilitySettings,
    ObservabilityTracingConfig,
    RUNTIME_CONFIG_TRACE_SAFE_SCHEMA_VERSION,
    RuntimeConfigError,
    RuntimeConfigErrorCode,
    RuntimeConfigNamespace,
    RuntimeConfigOperation,
    RuntimeConfigSafetyLockSettings,
    RuntimeConfigSettings,
    build_runtime_config_snapshot,
    create_runtime_config_provider,
    load_runtime_config_settings,
    validate_runtime_config_candidate,
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


def test_load_runtime_config_settings_rejects_unknown_yaml_field(
    tmp_path: Path,
) -> None:
    """验证 RuntimeConfig 会拒绝 YAML 中的未知配置字段。

    :param tmp_path: pytest 提供的临时目录。
    :return: None。
    """

    config_path = tmp_path / "runtime_config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "params_version: params.test",
                "config_schema_version: runtime-config.test",
                "unknown_runtime_config_field: true",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_runtime_config_settings(config_path)


def test_load_runtime_config_settings_uses_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 RuntimeConfig 支持通过环境变量覆盖部署参数。

    :param monkeypatch: pytest 提供的环境变量临时覆盖工具。
    :return: None。
    """

    monkeypatch.setenv("RUNTIME_CONFIG_PARAMS_VERSION", "params.env")

    settings = load_runtime_config_settings()

    assert settings.params_version == "params.env"


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


def test_runtime_config_snapshot_id_changes_when_effective_config_changes() -> None:
    """验证有效配置变化会生成新的快照 ID。

    :return: None。
    """

    api_ingress_settings = ApiIngressSettings()
    checkpoint_store_settings = CheckpointStoreSettings()
    first_snapshot = build_runtime_config_snapshot(
        runtime_config_settings=_runtime_config_settings(params_version="params.one"),
        api_ingress_settings=api_ingress_settings,
        checkpoint_store_settings=checkpoint_store_settings,
    )
    second_snapshot = build_runtime_config_snapshot(
        runtime_config_settings=_runtime_config_settings(params_version="params.two"),
        api_ingress_settings=api_ingress_settings,
        checkpoint_store_settings=checkpoint_store_settings,
    )

    assert first_snapshot.config_snapshot_id != second_snapshot.config_snapshot_id


def test_runtime_config_snapshot_contains_version_metadata() -> None:
    """验证 RuntimeConfig 快照会携带版本、时间与 trace-safe schema 元数据。

    :return: None。
    """

    runtime_settings = _runtime_config_settings(params_version="params.metadata")
    snapshot = build_runtime_config_snapshot(
        runtime_config_settings=runtime_settings,
        api_ingress_settings=ApiIngressSettings(),
        checkpoint_store_settings=CheckpointStoreSettings(),
    )

    assert snapshot.params_version == "params.metadata"
    assert snapshot.config_schema_version == runtime_settings.config_schema_version
    assert (
        snapshot.trace_safe_schema_version == RUNTIME_CONFIG_TRACE_SAFE_SCHEMA_VERSION
    )
    assert snapshot.created_at.tzinfo is UTC
    assert (
        snapshot.trace_safe_summary["config_snapshot_id"] == snapshot.config_snapshot_id
    )


def test_runtime_config_snapshot_is_frozen() -> None:
    """验证 RuntimeConfig 快照激活后不可修改。

    :return: None。
    """

    snapshot = build_runtime_config_snapshot(
        runtime_config_settings=_runtime_config_settings(
            params_version="params.frozen"
        ),
        api_ingress_settings=ApiIngressSettings(),
        checkpoint_store_settings=CheckpointStoreSettings(),
    )

    with pytest.raises(ValidationError):
        setattr(snapshot, "params_version", "params.mutated")


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
    conflict_with = exc_info.value.to_dto().conflict_with
    assert isinstance(conflict_with, dict)
    assert conflict_with["disabled_fields"] == ["enforce_pet_session_policy"]


def test_runtime_config_rejects_checkpoint_timeout_above_run_lock_ttl() -> None:
    """验证 RuntimeConfig 会拒绝 CheckpointStore 操作超时大于运行锁最大 TTL。

    :return: None。
    """

    with pytest.raises(RuntimeConfigError) as exc_info:
        validate_runtime_config_candidate(
            runtime_config_settings=RuntimeConfigSettings(),
            api_ingress_settings=ApiIngressSettings(),
            checkpoint_store_settings=CheckpointStoreSettings(
                operation_timeout_seconds=901.0,
            ),
            observability_settings=ObservabilitySettings(),
        )

    assert exc_info.value.code is RuntimeConfigErrorCode.CONFIG_RELATION_INVALID
    assert exc_info.value.operation is RuntimeConfigOperation.VALIDATE_CANDIDATE_CONFIG
    conflict_with = exc_info.value.to_dto().conflict_with
    assert isinstance(conflict_with, dict)
    assert conflict_with["operation_timeout_seconds"] == 901.0


def test_runtime_config_rejects_sse_timeout_above_stream_total_timeout() -> None:
    """验证 RuntimeConfig 会拒绝 SSE 最大持续时间大于编排层流式总超时。

    :return: None。
    """

    api_ingress_settings = ApiIngressSettings()
    api_ingress_settings = api_ingress_settings.model_copy(
        update={
            "sse": api_ingress_settings.sse.model_copy(
                update={
                    "max_stream_duration_seconds": (
                        api_ingress_settings.orchestrator.stream_total_timeout_seconds
                        + 1.0
                    )
                }
            )
        }
    )

    with pytest.raises(RuntimeConfigError) as exc_info:
        validate_runtime_config_candidate(
            runtime_config_settings=RuntimeConfigSettings(),
            api_ingress_settings=api_ingress_settings,
            checkpoint_store_settings=CheckpointStoreSettings(),
            observability_settings=ObservabilitySettings(),
        )

    assert exc_info.value.code is RuntimeConfigErrorCode.CONFIG_RELATION_INVALID
    assert exc_info.value.operation is RuntimeConfigOperation.VALIDATE_CANDIDATE_CONFIG
    conflict_with = exc_info.value.to_dto().conflict_with
    assert isinstance(conflict_with, dict)
    assert "sse.max_stream_duration_seconds" in conflict_with


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
    assert provider.get_namespace(RuntimeConfigNamespace.OBSERVABILITY) is (
        provider.current_snapshot().observability
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
    assert provider.get_value(key="observability.metrics.endpoint_path") == (
        snapshot.observability.metrics.endpoint_path
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


def test_runtime_config_provider_get_value_rejects_invalid_namespace() -> None:
    """验证按配置键读取值时会拒绝未知命名空间。

    :return: None。
    """

    provider = create_runtime_config_provider(
        api_ingress_settings=ApiIngressSettings(),
        checkpoint_store_settings=CheckpointStoreSettings(),
    )

    with pytest.raises(RuntimeConfigError) as exc_info:
        provider.get_value(key="unknown_namespace.params_version")

    assert exc_info.value.code is RuntimeConfigErrorCode.CONFIG_SCHEMA_INVALID
    assert exc_info.value.operation is RuntimeConfigOperation.GET_CONFIG_VALUE


def test_runtime_config_provider_get_value_rejects_invalid_key_format() -> None:
    """验证按配置键读取值时会拒绝非法点路径格式。

    :return: None。
    """

    provider = create_runtime_config_provider(
        api_ingress_settings=ApiIngressSettings(),
        checkpoint_store_settings=CheckpointStoreSettings(),
    )

    with pytest.raises(RuntimeConfigError) as exc_info:
        provider.get_value(key="runtime_config")

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
    assert "secret" not in str(summary)
    assert "token" not in str(summary)


def test_runtime_config_rejects_sensitive_trace_safe_summary_value() -> None:
    """验证 RuntimeConfig 会拒绝 trace-safe 摘要中的疑似敏感字符串值。

    :return: None。
    """

    observability_settings = ObservabilitySettings(
        tracing=ObservabilityTracingConfig(
            service_name="postgresql://runtime-config-secret",
        )
    )

    with pytest.raises(RuntimeConfigError) as exc_info:
        build_runtime_config_snapshot(
            runtime_config_settings=RuntimeConfigSettings(),
            api_ingress_settings=ApiIngressSettings(),
            checkpoint_store_settings=CheckpointStoreSettings(),
            observability_settings=observability_settings,
        )

    assert exc_info.value.code is RuntimeConfigErrorCode.CONFIG_TRACE_SUMMARY_UNSAFE
    assert exc_info.value.operation is (
        RuntimeConfigOperation.GET_TRACE_SAFE_CONFIG_SUMMARY
    )


def test_runtime_config_error_converts_to_dto() -> None:
    """验证 RuntimeConfig 领域异常可转换为统一错误 DTO。

    :return: None。
    """

    error = RuntimeConfigError(
        code=RuntimeConfigErrorCode.CONFIG_SNAPSHOT_NOT_FOUND,
        operation=RuntimeConfigOperation.GET_CURRENT_CONFIG_SNAPSHOT,
        message="RuntimeConfig 当前快照不存在",
        retryable=True,
        conflict_with={"reason": "snapshot_missing"},
    )

    dto = error.to_dto()

    assert dto.code is RuntimeConfigErrorCode.CONFIG_SNAPSHOT_NOT_FOUND
    assert dto.operation is RuntimeConfigOperation.GET_CURRENT_CONFIG_SNAPSHOT
    assert dto.retryable is True
    assert dto.conflict_with == {"reason": "snapshot_missing"}
    assert "RuntimeConfig 当前快照不存在" in str(error)

##################################################################################################
# 文件: tests/integration/compose_dev/test_compose_dev_contract.py
# 作用: 验证 compose.dev 基础集成测试编排的静态契约、依赖顺序、运行环境与占位清理。
# 边界: 仅展开并断言 Compose 配置；不启动容器、不连接数据库、不调用生产业务实现。
##################################################################################################

from collections.abc import Mapping
from typing import cast

from tests.integration.compose_dev import (
    BANNED_PLACEHOLDER_TOKENS,
    REQUIRED_RUNTIME_ENV_KEYS,
    REQUIRED_SERVICES,
    load_compose_config,
    read_compose_related_file_text,
    read_env_example_values,
    service_config,
)


def _mapping_field(
    owner: Mapping[str, object],
    field_name: str,
) -> Mapping[str, object]:
    """从映射对象读取指定字段并断言其仍为映射。

    :param owner: 需要读取字段的父映射。
    :param field_name: 需要读取的字段名。
    :return: 指定字段对应的映射值。
    """

    value = owner.get(field_name)
    assert isinstance(value, dict)
    return cast(Mapping[str, object], value)


def _sequence_field(
    owner: Mapping[str, object],
    field_name: str,
) -> tuple[object, ...]:
    """从映射对象读取指定字段并断言其为序列。

    :param owner: 需要读取字段的父映射。
    :param field_name: 需要读取的字段名。
    :return: 指定字段对应的不可变序列。
    """

    value = owner.get(field_name)
    assert isinstance(value, list)
    return tuple(value)


def test_compose_config_exposes_required_base_services() -> None:
    """验证基础集成测试 Compose 配置声明了当前真实链路所需服务。

    :return: None。
    """

    config = load_compose_config()
    services = _mapping_field(config, "services")

    assert REQUIRED_SERVICES.issubset(services.keys())
    assert "smoke" not in services


def test_compose_smoke_profile_exposes_smoke_service() -> None:
    """验证启用 smoke profile 后 Compose 配置包含烟测服务。

    :return: None。
    """

    config = load_compose_config(include_smoke_profile=True)
    services = _mapping_field(config, "services")
    smoke = service_config(config, "smoke")
    smoke_depends_on = _mapping_field(smoke, "depends_on")
    app_dependency = _mapping_field(smoke_depends_on, "app")
    smoke_command = _sequence_field(smoke, "command")

    assert REQUIRED_SERVICES.union({"smoke"}).issubset(services.keys())
    assert app_dependency["condition"] == "service_healthy"
    assert "http://app:8080/ready" in "\n".join(str(item) for item in smoke_command)
    assert "http://app:8080/health" in "\n".join(str(item) for item in smoke_command)


def test_compose_runtime_chain_orders_postgres_migration_and_app() -> None:
    """验证 PostgreSQL、迁移任务与应用服务之间的启动依赖顺序。

    :return: None。
    """

    config = load_compose_config()
    migrate = service_config(config, "migrate")
    app = service_config(config, "app")
    migrate_depends_on = _mapping_field(migrate, "depends_on")
    app_depends_on = _mapping_field(app, "depends_on")
    migrate_postgres_dependency = _mapping_field(migrate_depends_on, "postgres")
    app_postgres_dependency = _mapping_field(app_depends_on, "postgres")
    app_migrate_dependency = _mapping_field(app_depends_on, "migrate")

    assert migrate_postgres_dependency["condition"] == "service_healthy"
    assert app_postgres_dependency["condition"] == "service_healthy"
    assert app_migrate_dependency["condition"] == "service_completed_successfully"


def test_compose_app_uses_real_runtime_database_environment() -> None:
    """验证 app 服务通过 DATABASE_URL 进入真实数据库运行链路。

    :return: None。
    """

    config = load_compose_config()
    app = service_config(config, "app")
    environment = _mapping_field(app, "environment")
    app_command = _sequence_field(app, "command")
    ports = _sequence_field(app, "ports")

    assert REQUIRED_RUNTIME_ENV_KEYS.issubset(environment.keys())
    assert environment["DATABASE_URL"] == (
        "postgresql+psycopg://veterinary_agent:veterinary_agent@postgres:5432/"
        "veterinary_agent_dev"
    )
    assert environment["LANGGRAPH_POSTGRES_SETUP_ON_STARTUP"] == "true"
    assert environment["LANGGRAPH_STRICT_MSGPACK"] == "true"
    assert tuple(app_command[:3]) == (
        "uvicorn",
        "veterinary_agent.app:create_app",
        "--factory",
    )
    assert any(
        isinstance(port, dict)
        and port.get("host_ip") == "127.0.0.1"
        and port.get("published") == "8080"
        for port in ports
    )


def test_compose_migration_service_runs_alembic_upgrade_head() -> None:
    """验证 migrate 服务只负责执行项目级 Alembic 迁移。

    :return: None。
    """

    config = load_compose_config()
    migrate = service_config(config, "migrate")
    environment = _mapping_field(migrate, "environment")
    command = _sequence_field(migrate, "command")
    volumes = _sequence_field(migrate, "volumes")

    assert command == ("alembic", "-c", "/app/alembic.ini", "upgrade", "head")
    assert environment["DATABASE_URL"] == (
        "postgresql+psycopg://veterinary_agent:veterinary_agent@postgres:5432/"
        "veterinary_agent_dev"
    )
    assert any(
        isinstance(volume, dict) and volume.get("target") == "/app/migrations"
        for volume in volumes
    )
    assert any(
        isinstance(volume, dict) and volume.get("target") == "/app/alembic.ini"
        for volume in volumes
    )


def test_env_example_matches_compose_runtime_defaults() -> None:
    """验证 .env.example 与 compose.dev 的默认真实链路变量保持一致。

    :return: None。
    """

    values = read_env_example_values()

    assert values["POSTGRES_DB"] == "veterinary_agent_dev"
    assert values["POSTGRES_USER"] == "veterinary_agent"
    assert values["POSTGRES_PASSWORD"] == "veterinary_agent"
    assert values["DATABASE_URL"] == (
        "postgresql+psycopg://veterinary_agent:veterinary_agent@postgres:5432/"
        "veterinary_agent_dev"
    )
    assert values["LANGGRAPH_POSTGRES_SETUP_ON_STARTUP"] == "true"
    assert values["LANGGRAPH_STRICT_MSGPACK"] == "true"


def test_compose_dev_files_do_not_reference_removed_asset_placeholders() -> None:
    """验证基础集成测试编排不再引用缺失资产路径占位。

    :return: None。
    """

    text = read_compose_related_file_text()

    for token in BANNED_PLACEHOLDER_TOKENS:
        assert token not in text

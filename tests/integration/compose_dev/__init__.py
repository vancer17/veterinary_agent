##################################################################################################
# 文件: tests/integration/compose_dev/__init__.py
# 作用: 作为 compose.dev 基础集成测试子包统一出口，集中暴露测试辅助契约。
# 边界: 仅服务 Docker Compose 黑盒集成测试；不导出生产实现，不跨包直连内部方法。
##################################################################################################

from tests.integration.compose_dev.helpers import (
    COMPOSE_LIFECYCLE_ENV_NAME,
    BANNED_PLACEHOLDER_TOKENS,
    REQUIRED_ALEMBIC_REVISION,
    REQUIRED_DATABASE_TABLES,
    REQUIRED_RUNTIME_ENV_KEYS,
    REQUIRED_SERVICES,
    ComposeCommandResult,
    ComposeConfig,
    assert_compose_success,
    build_compose_project_name,
    exec_app_python,
    exec_postgres_query,
    load_compose_config,
    read_compose_related_file_text,
    read_env_example_values,
    require_compose_cli,
    require_compose_lifecycle_enabled,
    rerun_compose_migration,
    run_compose,
    run_compose_smoke,
    service_config,
    start_compose_app_stack,
    stop_compose_stack,
    stdout_lines,
)

__all__: tuple[str, ...] = (
    "BANNED_PLACEHOLDER_TOKENS",
    "COMPOSE_LIFECYCLE_ENV_NAME",
    "REQUIRED_ALEMBIC_REVISION",
    "REQUIRED_DATABASE_TABLES",
    "REQUIRED_RUNTIME_ENV_KEYS",
    "REQUIRED_SERVICES",
    "ComposeCommandResult",
    "ComposeConfig",
    "assert_compose_success",
    "build_compose_project_name",
    "exec_app_python",
    "exec_postgres_query",
    "load_compose_config",
    "read_compose_related_file_text",
    "read_env_example_values",
    "require_compose_cli",
    "require_compose_lifecycle_enabled",
    "rerun_compose_migration",
    "run_compose",
    "run_compose_smoke",
    "service_config",
    "start_compose_app_stack",
    "stop_compose_stack",
    "stdout_lines",
)

##################################################################################################
# 文件: tests/integration/compose_dev/test_compose_dev_database.py
# 作用: 验证 compose.dev 数据库链路的统一 DATABASE_URL、Alembic 迁移幂等性与关键表结构。
# 边界: 默认仅做 Compose 配置契约断言；真实数据库黑盒测试需显式环境变量开启。
##################################################################################################

from collections.abc import Mapping
from typing import cast

from tests.integration.compose_dev import (
    REQUIRED_ALEMBIC_REVISION,
    REQUIRED_DATABASE_TABLES,
    assert_compose_success,
    build_compose_project_name,
    exec_postgres_query,
    load_compose_config,
    require_compose_cli,
    require_compose_lifecycle_enabled,
    rerun_compose_migration,
    service_config,
    start_compose_app_stack,
    stop_compose_stack,
    stdout_lines,
)


def _service_environment(service: Mapping[str, object]) -> Mapping[str, object]:
    """读取 Compose 服务环境变量映射。

    :param service: 展开后的 Compose 服务配置。
    :return: 服务环境变量映射。
    """

    environment = service.get("environment")
    assert isinstance(environment, dict)
    return cast(Mapping[str, object], environment)


def test_app_and_migrate_share_single_runtime_database_url() -> None:
    """验证 app 与 migrate 服务使用同一个真实运行链路数据库地址。

    :return: None。
    """

    config = load_compose_config()
    app_environment = _service_environment(service_config(config, "app"))
    migrate_environment = _service_environment(service_config(config, "migrate"))

    assert app_environment["DATABASE_URL"] == migrate_environment["DATABASE_URL"]
    assert str(app_environment["DATABASE_URL"]).startswith("postgresql+psycopg://")
    assert "@postgres:5432/" in str(app_environment["DATABASE_URL"])


def test_compose_dev_migration_is_idempotent_and_revision_is_current() -> None:
    """验证真实 compose.dev 数据库迁移可重复执行且版本处于当前 head。

    :return: None。
    """

    require_compose_lifecycle_enabled()
    require_compose_cli()
    project_name = build_compose_project_name()
    try:
        up_result = start_compose_app_stack(project_name)
        assert_compose_success(
            up_result,
            description="启动 compose.dev 数据库幂等测试栈",
        )

        migrate_result = rerun_compose_migration(project_name)
        assert_compose_success(
            migrate_result,
            description="重复运行 compose.dev Alembic 迁移",
        )

        revision_result = exec_postgres_query(
            project_name,
            "select version_num from alembic_version",
        )
        assert_compose_success(
            revision_result,
            description="查询 compose.dev Alembic 当前版本",
        )
        assert REQUIRED_ALEMBIC_REVISION in stdout_lines(revision_result.stdout)

        table_result = exec_postgres_query(
            project_name,
            (
                "select table_name from information_schema.tables "
                "where table_schema = 'public' order by table_name"
            ),
        )
        assert_compose_success(
            table_result,
            description="查询 compose.dev 公共 schema 表结构",
        )
        assert REQUIRED_DATABASE_TABLES.issubset(stdout_lines(table_result.stdout))
    finally:
        down_result = stop_compose_stack(project_name)
        assert_compose_success(
            down_result,
            description="清理 compose.dev 数据库幂等测试栈",
        )

##################################################################################################
# 文件: tests/integration/compose_dev/test_compose_dev_lifecycle.py
# 作用: 验证 compose.dev 可启动真实数据库链路、完成迁移并通过基础 HTTP smoke 检查。
# 边界: 仅在显式环境变量开启时运行 Docker Compose 黑盒测试；不接入真实外部 LLM 或跨领域未实现资源。
##################################################################################################

from tests.integration.compose_dev import (
    REQUIRED_DATABASE_TABLES,
    assert_compose_success,
    build_compose_project_name,
    exec_postgres_query,
    require_compose_cli,
    require_compose_lifecycle_enabled,
    run_compose_smoke,
    start_compose_app_stack,
    stop_compose_stack,
    stdout_lines,
)


def test_compose_dev_stack_runs_smoke_and_migrates_database() -> None:
    """验证 compose.dev 真实基础链路可启动、探针可用且数据库迁移已落表。

    :return: None。
    """

    require_compose_lifecycle_enabled()
    require_compose_cli()
    project_name = build_compose_project_name()
    try:
        up_result = start_compose_app_stack(project_name)
        assert_compose_success(
            up_result,
            description="启动 compose.dev app 基础集成测试栈",
        )

        smoke_result = run_compose_smoke(project_name)
        assert_compose_success(
            smoke_result,
            description="运行 compose.dev smoke HTTP 探针",
        )
        assert "compose.dev smoke check passed" in smoke_result.stdout

        table_result = exec_postgres_query(
            project_name,
            (
                "select table_name from information_schema.tables "
                "where table_schema = 'public' order by table_name"
            ),
        )
        assert_compose_success(
            table_result,
            description="查询 compose.dev 数据库迁移结果",
        )
        assert REQUIRED_DATABASE_TABLES.issubset(stdout_lines(table_result.stdout))
    finally:
        down_result = stop_compose_stack(project_name)
        assert_compose_success(
            down_result,
            description="清理 compose.dev 基础集成测试栈",
        )

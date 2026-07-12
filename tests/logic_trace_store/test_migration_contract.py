##################################################################################################
# 文件: tests/logic_trace_store/test_migration_contract.py
# 作用: 验证 LogicTraceStore 组件 Alembic 迁移创建和回滚的表结构、索引契约。
# 边界: 使用临时 SQLite 运行项目迁移；不验证 PostgreSQL 专属执行计划或 JSONB 索引性能。
##################################################################################################

from pathlib import Path

import pytest
from sqlalchemy import inspect

from tests.logic_trace_store.helpers import (
    build_sqlite_database_url,
    downgrade_to_base,
    open_engine,
    upgrade_to_head,
)

LOGIC_TRACE_TABLE_NAMES: set[str] = {
    "logic_trace",
    "logic_trace_event",
    "logic_trace_call_summary",
    "logic_trace_artifact",
    "logic_trace_projection",
    "logic_trace_outbox",
}


def _index_names(
    *,
    database_url: str,
    table_name: str,
) -> set[str]:
    """读取指定表上的索引名集合。

    :param database_url: SQLAlchemy 数据库连接地址。
    :param table_name: 需要读取索引的表名。
    :return: 指定表上的索引名集合。
    """

    engine = open_engine(database_url)
    try:
        return {str(index["name"]) for index in inspect(engine).get_indexes(table_name)}
    finally:
        engine.dispose()


def test_logic_trace_store_migration_creates_component_tables_and_indexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 migration 创建 LogicTraceStore 表和关键查询索引。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = build_sqlite_database_url(tmp_path / "migration_contract.db")
    upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    engine = open_engine(database_url)
    try:
        table_names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert LOGIC_TRACE_TABLE_NAMES.issubset(table_names)
    assert {
        "ix_logic_trace_request_id",
        "ix_logic_trace_run_id",
        "ix_logic_trace_session_started_at",
        "ix_logic_trace_idempotency_key",
        "ix_logic_trace_status_updated_at",
    }.issubset(_index_names(database_url=database_url, table_name="logic_trace"))
    assert {
        "ix_logic_trace_event_trace_created_at",
        "ix_logic_trace_event_type_created_at",
        "ix_logic_trace_event_segment",
    }.issubset(_index_names(database_url=database_url, table_name="logic_trace_event"))
    assert {
        "ix_logic_trace_projection_trace_type",
    }.issubset(
        _index_names(database_url=database_url, table_name="logic_trace_projection")
    )
    assert {
        "ix_logic_trace_outbox_status_next_retry",
        "ix_logic_trace_outbox_trace",
    }.issubset(_index_names(database_url=database_url, table_name="logic_trace_outbox"))


def test_logic_trace_store_migration_downgrade_removes_component_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 downgrade base 会移除 LogicTraceStore 组件表。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = build_sqlite_database_url(tmp_path / "migration_downgrade.db")
    upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    downgrade_to_base(monkeypatch=monkeypatch, database_url=database_url)
    engine = open_engine(database_url)
    try:
        table_names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert LOGIC_TRACE_TABLE_NAMES.isdisjoint(table_names)

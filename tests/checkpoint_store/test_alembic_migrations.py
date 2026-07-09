##################################################################################################
# 文件: tests/checkpoint_store/test_alembic_migrations.py
# 作用: 验证 CheckpointStore 控制平面 Alembic 迁移可执行，并落实关键表结构约束。
# 边界: 仅使用临时 SQLite 数据库测试迁移行为，不连接真实 PostgreSQL、不调用 Repository 或 LangGraph。
##################################################################################################

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, Inspector, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from veterinary_agent.checkpoint_store import DATABASE_URL_ENV_NAME


def _build_alembic_config() -> Config:
    """构建测试用 Alembic 配置对象。

    :return: 指向项目 alembic.ini 的 Alembic 配置对象。
    """

    return Config("alembic.ini")


def _build_sqlite_database_url(database_path: Path) -> str:
    """构建临时 SQLite 数据库连接地址。

    :param database_path: 临时 SQLite 数据库文件路径。
    :return: SQLAlchemy 可使用的 SQLite 数据库 URL。
    """

    return f"sqlite:///{database_path}"


def _upgrade_to_head(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    """运行 Alembic upgrade head。

    :param monkeypatch: pytest 环境变量修改夹具。
    :param database_url: 本次迁移使用的数据库连接地址。
    :return: None。
    """

    monkeypatch.setenv(DATABASE_URL_ENV_NAME, database_url)
    command.upgrade(_build_alembic_config(), "head")


def _downgrade_to_base(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    """运行 Alembic downgrade base。

    :param monkeypatch: pytest 环境变量修改夹具。
    :param database_url: 本次迁移使用的数据库连接地址。
    :return: None。
    """

    monkeypatch.setenv(DATABASE_URL_ENV_NAME, database_url)
    command.downgrade(_build_alembic_config(), "base")


def _open_engine(database_url: str) -> Engine:
    """打开测试数据库引擎。

    :param database_url: SQLAlchemy 数据库连接地址。
    :return: 已创建的 SQLAlchemy Engine。
    """

    return create_engine(database_url)


def _get_table_names(engine: Engine) -> set[str]:
    """读取当前数据库表名集合。

    :param engine: SQLAlchemy 数据库引擎。
    :return: 当前数据库中的表名集合。
    """

    inspector = inspect(engine)
    return set(inspector.get_table_names())


def _get_index_names(inspector: Inspector, table_name: str) -> set[str]:
    """读取指定表的索引名集合。

    :param inspector: SQLAlchemy Inspector。
    :param table_name: 需要检查索引的表名。
    :return: 指定表上的索引名集合。
    """

    return {
        index_name
        for index in inspector.get_indexes(table_name)
        if (index_name := index["name"]) is not None
    }


def _get_unique_constraint_names(inspector: Inspector, table_name: str) -> set[str]:
    """读取指定表的唯一约束名集合。

    :param inspector: SQLAlchemy Inspector。
    :param table_name: 需要检查唯一约束的表名。
    :return: 指定表上的唯一约束名集合。
    """

    return {
        constraint_name
        for constraint in inspector.get_unique_constraints(table_name)
        if (constraint_name := constraint["name"]) is not None
    }


def test_checkpoint_store_migration_upgrade_and_downgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 CheckpointStore 控制平面迁移可升级并可回滚。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "migration.db")

    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    engine = _open_engine(database_url)
    try:
        table_names = _get_table_names(engine)
        assert "checkpoint_thread" in table_names
        assert "checkpoint_run_lock" in table_names
        assert "checkpoint_segment_publish" in table_names
        assert "alembic_version" in table_names
    finally:
        engine.dispose()

    _downgrade_to_base(monkeypatch=monkeypatch, database_url=database_url)
    engine = _open_engine(database_url)
    try:
        table_names = _get_table_names(engine)
        assert "checkpoint_thread" not in table_names
        assert "checkpoint_run_lock" not in table_names
        assert "checkpoint_segment_publish" not in table_names
        assert "alembic_version" in table_names
    finally:
        engine.dispose()


def test_checkpoint_store_migration_creates_expected_indexes_and_constraints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证迁移创建了关键索引和唯一约束。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "constraints.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    engine = _open_engine(database_url)
    try:
        inspector = inspect(engine)

        thread_uniques = _get_unique_constraint_names(inspector, "checkpoint_thread")
        segment_uniques = _get_unique_constraint_names(
            inspector,
            "checkpoint_segment_publish",
        )
        assert "uq_checkpoint_thread_session_id" in thread_uniques
        assert "uq_checkpoint_segment_publish_thread_segment" in segment_uniques

        thread_indexes = _get_index_names(inspector, "checkpoint_thread")
        lock_indexes = _get_index_names(inspector, "checkpoint_run_lock")
        segment_indexes = _get_index_names(inspector, "checkpoint_segment_publish")
        assert "ix_checkpoint_thread_user_id" in thread_indexes
        assert "ix_checkpoint_thread_user_pet" in thread_indexes
        assert "ix_checkpoint_thread_status_updated_at" in thread_indexes
        assert "ix_checkpoint_run_lock_expires_at" in lock_indexes
        assert "ix_checkpoint_run_lock_run_id" in lock_indexes
        assert "ix_checkpoint_segment_publish_thread_status" in segment_indexes
        assert "ix_checkpoint_segment_publish_thread_task" in segment_indexes
        assert "ix_checkpoint_segment_publish_published_at" in segment_indexes
    finally:
        engine.dispose()


def test_checkpoint_store_migration_enforces_control_plane_constraints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证迁移创建的表结构能落实控制平面关键约束。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "enforcement.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    engine = _open_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            connection.execute(
                text(
                    """
                    INSERT INTO checkpoint_thread (
                        thread_id,
                        session_id,
                        user_id,
                        pet_id,
                        status,
                        latest_version
                    )
                    VALUES (
                        'thread_1',
                        'session_1',
                        'user_1',
                        'pet_1',
                        'initialized',
                        0
                    )
                    """
                )
            )

            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        """
                        INSERT INTO checkpoint_thread (
                            thread_id,
                            session_id,
                            user_id,
                            status,
                            latest_version
                        )
                        VALUES (
                            'thread_duplicate_session',
                            'session_1',
                            'user_1',
                            'initialized',
                            0
                        )
                        """
                    )
                )

            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        """
                        INSERT INTO checkpoint_thread (
                            thread_id,
                            session_id,
                            user_id,
                            status,
                            latest_version
                        )
                        VALUES (
                            'thread_bad_version',
                            'session_bad_version',
                            'user_1',
                            'initialized',
                            -1
                        )
                        """
                    )
                )

            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        """
                        INSERT INTO checkpoint_thread (
                            thread_id,
                            session_id,
                            user_id,
                            status,
                            latest_version
                        )
                        VALUES (
                            'thread_bad_status',
                            'session_bad_status',
                            'user_1',
                            'unknown',
                            0
                        )
                        """
                    )
                )

            connection.execute(
                text(
                    """
                    INSERT INTO checkpoint_run_lock (
                        thread_id,
                        run_id,
                        expires_at
                    )
                    VALUES (
                        'thread_1',
                        'run_1',
                        CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        """
                        INSERT INTO checkpoint_run_lock (
                            thread_id,
                            run_id,
                            expires_at
                        )
                        VALUES (
                            'thread_1',
                            'run_2',
                            CURRENT_TIMESTAMP
                        )
                        """
                    )
                )

            connection.execute(
                text(
                    """
                    INSERT INTO checkpoint_segment_publish (
                        thread_id,
                        segment_id,
                        run_id,
                        status,
                        published_at
                    )
                    VALUES (
                        'thread_1',
                        'segment_1',
                        'run_1',
                        'published',
                        CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        """
                        INSERT INTO checkpoint_segment_publish (
                            thread_id,
                            segment_id,
                            run_id,
                            status,
                            published_at
                        )
                        VALUES (
                            'thread_1',
                            'segment_1',
                            'run_2',
                            'published',
                            CURRENT_TIMESTAMP
                        )
                        """
                    )
                )

            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        """
                        INSERT INTO checkpoint_segment_publish (
                            thread_id,
                            segment_id,
                            run_id,
                            status,
                            published_at
                        )
                        VALUES (
                            'thread_1',
                            'segment_bad_status',
                            'run_1',
                            'unknown',
                            CURRENT_TIMESTAMP
                        )
                        """
                    )
                )

            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        """
                        INSERT INTO checkpoint_segment_publish (
                            thread_id,
                            segment_id,
                            run_id,
                            status,
                            published_at
                        )
                        VALUES (
                            'missing_thread',
                            'segment_missing_thread',
                            'run_1',
                            'published',
                            CURRENT_TIMESTAMP
                        )
                        """
                    )
                )
    finally:
        engine.dispose()

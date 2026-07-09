##################################################################################################
# 文件: tests/checkpoint_store/test_migration_settings.py
# 作用: 验证 CheckpointStore 迁移配置占位能力只从 DATABASE_URL 读取数据库连接。
# 边界: 仅测试公共迁移配置出口，不执行数据库连接、不访问其他领域组件。
##################################################################################################

import pytest

from veterinary_agent.checkpoint_store import (
    DATABASE_URL_ENV_NAME,
    load_checkpoint_store_migration_settings,
)


def test_load_checkpoint_store_migration_settings_reads_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证迁移配置从 DATABASE_URL 环境变量读取数据库地址。

    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    monkeypatch.setenv(DATABASE_URL_ENV_NAME, "postgresql+psycopg://user:pass@db/app")

    settings = load_checkpoint_store_migration_settings()

    assert settings.database_url == "postgresql+psycopg://user:pass@db/app"


def test_load_checkpoint_store_migration_settings_rejects_missing_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证缺失 DATABASE_URL 时迁移配置加载会明确失败。

    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    monkeypatch.delenv(DATABASE_URL_ENV_NAME, raising=False)

    with pytest.raises(ValueError, match="DATABASE_URL"):
        load_checkpoint_store_migration_settings()

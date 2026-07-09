##################################################################################################
# 文件: src/veterinary_agent/checkpoint_store/migrations.py
# 作用: 定义 CheckpointStore 数据库迁移配置占位能力，供项目级 Alembic 环境统一读取。
# 边界: 仅解析迁移运行所需 DATABASE_URL，不创建连接、不执行迁移、不访问其他领域组件。
##################################################################################################

import os
from dataclasses import dataclass
from typing import Final

DATABASE_URL_ENV_NAME: Final[str] = "DATABASE_URL"


@dataclass(frozen=True, slots=True)
class CheckpointStoreMigrationSettings:
    """CheckpointStore 迁移配置。"""

    database_url: str


def load_checkpoint_store_migration_settings() -> CheckpointStoreMigrationSettings:
    """加载 CheckpointStore 迁移配置。

    :return: CheckpointStore 迁移配置。
    :raises ValueError: 当 DATABASE_URL 未配置或为空字符串时抛出。
    """

    database_url = os.environ.get(DATABASE_URL_ENV_NAME, "").strip()
    if not database_url:
        raise ValueError("DATABASE_URL 未配置，无法运行 Alembic 数据库迁移")
    return CheckpointStoreMigrationSettings(database_url=database_url)


__all__: tuple[str, ...] = (
    "CheckpointStoreMigrationSettings",
    "DATABASE_URL_ENV_NAME",
    "load_checkpoint_store_migration_settings",
)

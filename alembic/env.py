"""
文件：alembic/env.py
作用：提供数据库迁移环境与版本脚本。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from vet_agent.db import Base, sqlalchemy_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    """执行 database_url 业务逻辑。

    :return: 返回函数执行结果。
    """
    return sqlalchemy_url(os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url"))


def run_migrations_offline() -> None:
    """执行 run_migrations_offline 业务逻辑。

    :return: 返回函数执行结果。
    """
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """执行 run_migrations_online 业务逻辑。

    :return: 返回函数执行结果。
    """
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

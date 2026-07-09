##################################################################################################
# 文件: migrations/env.py
# 作用: 定义 Alembic 迁移运行环境，从 CheckpointStore 公共出口读取迁移配置并执行在线/离线迁移。
# 边界: 仅负责迁移运行编排，不定义业务表结构、不访问 Repository、不调用 LangGraph 或其他领域组件。
##################################################################################################

from logging.config import fileConfig

from alembic import context
from sqlalchemy import MetaData, engine_from_config, pool

from veterinary_agent.checkpoint_store import load_checkpoint_store_migration_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = MetaData()


def _resolve_database_url() -> str:
    """解析 Alembic 本次运行使用的数据库连接地址。

    :return: 迁移使用的数据库连接地址。
    :raises ValueError: 当 DATABASE_URL 未配置时抛出。
    """

    settings = load_checkpoint_store_migration_settings()
    return settings.database_url


def _apply_database_url() -> None:
    """将数据库连接地址写入 Alembic 配置对象。

    :return: None。
    """

    config.set_main_option("sqlalchemy.url", _resolve_database_url())


def run_migrations_offline() -> None:
    """以离线模式运行 Alembic 迁移。

    :return: None。
    """

    _apply_database_url()
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """以在线模式运行 Alembic 迁移。

    :return: None。
    """

    _apply_database_url()
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
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

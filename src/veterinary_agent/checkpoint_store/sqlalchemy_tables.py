##################################################################################################
# 文件: src/veterinary_agent/checkpoint_store/sqlalchemy_tables.py
# 作用: 集中定义 CheckpointStore SQLAlchemy Core 控制面表对象，供各控制面仓储复用。
# 边界: 仅声明数据库表结构映射，不创建连接、不执行 SQL、不承载领域流程。
##################################################################################################

from sqlalchemy import Column, DateTime, Integer, JSON, MetaData, Table, Text

CHECKPOINT_STORE_METADATA = MetaData()

CHECKPOINT_THREAD_TABLE = Table(
    "checkpoint_thread",
    CHECKPOINT_STORE_METADATA,
    Column("thread_id", Text(), primary_key=True),
    Column("session_id", Text(), nullable=False),
    Column("user_id", Text(), nullable=False),
    Column("pet_id", Text(), nullable=True),
    Column("status", Text(), nullable=False),
    Column("latest_version", Integer(), nullable=False),
    Column("latest_checkpoint_id", Text(), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

CHECKPOINT_RUN_LOCK_TABLE = Table(
    "checkpoint_run_lock",
    CHECKPOINT_STORE_METADATA,
    Column("thread_id", Text(), primary_key=True),
    Column("run_id", Text(), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("acquired_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

CHECKPOINT_SEGMENT_PUBLISH_TABLE = Table(
    "checkpoint_segment_publish",
    CHECKPOINT_STORE_METADATA,
    Column("thread_id", Text(), nullable=False),
    Column("segment_id", Text(), nullable=False),
    Column("run_id", Text(), nullable=False),
    Column("task_id", Text(), nullable=True),
    Column("status", Text(), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    Column("metadata", JSON(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


__all__: tuple[str, ...] = (
    "CHECKPOINT_RUN_LOCK_TABLE",
    "CHECKPOINT_SEGMENT_PUBLISH_TABLE",
    "CHECKPOINT_STORE_METADATA",
    "CHECKPOINT_THREAD_TABLE",
)

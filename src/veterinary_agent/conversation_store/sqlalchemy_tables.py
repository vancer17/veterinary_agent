##################################################################################################
# 文件: src/veterinary_agent/conversation_store/sqlalchemy_tables.py
# 作用: 集中定义 ConversationStore SQLAlchemy Core 表对象，供 session、message、segment 和读取仓储复用。
# 边界: 仅声明数据库表结构映射，不创建连接、不执行 SQL、不承载领域流程。
##################################################################################################

from sqlalchemy import Column, DateTime, Integer, JSON, MetaData, Table, Text

CONVERSATION_STORE_METADATA = MetaData()

CONVERSATION_SESSION_TABLE = Table(
    "conversation_session",
    CONVERSATION_STORE_METADATA,
    Column("session_id", Text(), primary_key=True),
    Column("user_id", Text(), nullable=False),
    Column("pet_id", Text(), nullable=False),
    Column("status", Text(), nullable=False),
    Column("metadata", JSON(), nullable=False),
    Column("next_sequence_no", Integer(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("last_message_at", DateTime(timezone=True), nullable=True),
)

CONVERSATION_MESSAGE_TABLE = Table(
    "conversation_message",
    CONVERSATION_STORE_METADATA,
    Column("message_id", Text(), primary_key=True),
    Column("session_id", Text(), nullable=False),
    Column("user_id", Text(), nullable=False),
    Column("pet_id", Text(), nullable=False),
    Column("role", Text(), nullable=False),
    Column("content_type", Text(), nullable=False),
    Column("content", Text(), nullable=False),
    Column("sequence_no", Integer(), nullable=False),
    Column("status", Text(), nullable=False),
    Column("reply_to_message_id", Text(), nullable=True),
    Column("idempotency_key", Text(), nullable=True),
    Column("metadata", JSON(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("finalized_at", DateTime(timezone=True), nullable=True),
)

CONVERSATION_MESSAGE_SEGMENT_TABLE = Table(
    "conversation_message_segment",
    CONVERSATION_STORE_METADATA,
    Column("segment_id", Text(), primary_key=True),
    Column("message_id", Text(), nullable=False),
    Column("session_id", Text(), nullable=False),
    Column("pet_id", Text(), nullable=False),
    Column("segment_order", Integer(), nullable=False),
    Column("content", Text(), nullable=False),
    Column("idempotency_key", Text(), nullable=True),
    Column("metadata", JSON(), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
)

CONVERSATION_ATTACHMENT_REF_TABLE = Table(
    "conversation_attachment_ref",
    CONVERSATION_STORE_METADATA,
    Column("attachment_ref_id", Text(), primary_key=True),
    Column("attachment_id", Text(), nullable=False),
    Column("message_id", Text(), nullable=False),
    Column("session_id", Text(), nullable=False),
    Column("pet_id", Text(), nullable=False),
    Column("attachment_type", Text(), nullable=False),
    Column("metadata", JSON(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


__all__: tuple[str, ...] = (
    "CONVERSATION_ATTACHMENT_REF_TABLE",
    "CONVERSATION_MESSAGE_SEGMENT_TABLE",
    "CONVERSATION_MESSAGE_TABLE",
    "CONVERSATION_SESSION_TABLE",
    "CONVERSATION_STORE_METADATA",
)

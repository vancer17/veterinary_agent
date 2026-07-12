##################################################################################################
# 文件: src/veterinary_agent/logic_trace_store/sqlalchemy_tables.py
# 作用: 集中定义 LogicTraceStore SQLAlchemy Core 控制面表对象，供存储与查询仓储复用。
# 边界: 仅声明数据库表结构映射，不创建连接、不执行 SQL、不承载业务流程或投影逻辑。
##################################################################################################

from sqlalchemy import Column, DateTime, Integer, JSON, MetaData, Table, Text

LOGIC_TRACE_STORE_METADATA = MetaData()

LOGIC_TRACE_TABLE = Table(
    "logic_trace",
    LOGIC_TRACE_STORE_METADATA,
    Column("trace_id", Text(), primary_key=True),
    Column("request_id", Text(), nullable=False),
    Column("turn_id", Text(), nullable=False),
    Column("run_id", Text(), nullable=False),
    Column("session_id", Text(), nullable=False),
    Column("user_id", Text(), nullable=False),
    Column("pet_id", Text(), nullable=False),
    Column("params_version", Text(), nullable=False),
    Column("config_snapshot_id", Text(), nullable=False),
    Column("idempotency_key", Text(), nullable=False),
    Column("capture_policy_ref", Text(), nullable=True),
    Column("status", Text(), nullable=False),
    Column("final_status", Text(), nullable=True),
    Column("user_message_id", Text(), nullable=True),
    Column("error_code", Text(), nullable=True),
    Column("summary", JSON(), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finalized_at", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

LOGIC_TRACE_EVENT_TABLE = Table(
    "logic_trace_event",
    LOGIC_TRACE_STORE_METADATA,
    Column("event_id", Text(), primary_key=True),
    Column("trace_id", Text(), nullable=False),
    Column("request_id", Text(), nullable=False),
    Column("event_type", Text(), nullable=False),
    Column("source_component", Text(), nullable=False),
    Column("node_id", Text(), nullable=True),
    Column("task_id", Text(), nullable=True),
    Column("segment_id", Text(), nullable=True),
    Column("input_hash", Text(), nullable=True),
    Column("output_hash", Text(), nullable=True),
    Column("summary", JSON(), nullable=False),
    Column("business_payload", JSON(), nullable=False),
    Column("schema_ref", Text(), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

LOGIC_TRACE_CALL_SUMMARY_TABLE = Table(
    "logic_trace_call_summary",
    LOGIC_TRACE_STORE_METADATA,
    Column("call_id", Text(), primary_key=True),
    Column("trace_id", Text(), nullable=False),
    Column("request_id", Text(), nullable=False),
    Column("call_type", Text(), nullable=False),
    Column("source_component", Text(), nullable=False),
    Column("provider_ref", Text(), nullable=True),
    Column("input_ref", Text(), nullable=True),
    Column("output_ref", Text(), nullable=True),
    Column("usage", JSON(), nullable=False),
    Column("status", Text(), nullable=False),
    Column("summary", JSON(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

LOGIC_TRACE_ARTIFACT_TABLE = Table(
    "logic_trace_artifact",
    LOGIC_TRACE_STORE_METADATA,
    Column("artifact_id", Text(), primary_key=True),
    Column("trace_id", Text(), nullable=False),
    Column("artifact_type", Text(), nullable=False),
    Column("storage_ref", Text(), nullable=False),
    Column("content_hash", Text(), nullable=True),
    Column("visibility_policy", Text(), nullable=False),
    Column("metadata", JSON(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

LOGIC_TRACE_PROJECTION_TABLE = Table(
    "logic_trace_projection",
    LOGIC_TRACE_STORE_METADATA,
    Column("projection_id", Text(), primary_key=True),
    Column("trace_id", Text(), nullable=False),
    Column("projection_type", Text(), nullable=False),
    Column("version", Text(), nullable=False),
    Column("view_payload", JSON(), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

LOGIC_TRACE_OUTBOX_TABLE = Table(
    "logic_trace_outbox",
    LOGIC_TRACE_STORE_METADATA,
    Column("outbox_id", Text(), primary_key=True),
    Column("trace_id", Text(), nullable=False),
    Column("event_kind", Text(), nullable=False),
    Column("payload", JSON(), nullable=False),
    Column("status", Text(), nullable=False),
    Column("retry_count", Integer(), nullable=False),
    Column("next_retry_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


__all__: tuple[str, ...] = (
    "LOGIC_TRACE_ARTIFACT_TABLE",
    "LOGIC_TRACE_CALL_SUMMARY_TABLE",
    "LOGIC_TRACE_EVENT_TABLE",
    "LOGIC_TRACE_OUTBOX_TABLE",
    "LOGIC_TRACE_PROJECTION_TABLE",
    "LOGIC_TRACE_STORE_METADATA",
    "LOGIC_TRACE_TABLE",
)

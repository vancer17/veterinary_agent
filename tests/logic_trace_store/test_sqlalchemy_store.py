##################################################################################################
# 文件: tests/logic_trace_store/test_sqlalchemy_store.py
# 作用: 验证 LogicTraceStore SQLAlchemy 实现的核心写入、查询、投影和现有 trace 端口适配。
# 边界: 使用临时 SQLite 数据库和 Alembic 迁移；不连接真实 PostgreSQL、不实现 VetTraceSchema 或业务图。
##################################################################################################

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect

from veterinary_agent.agent_application_service import (
    AgentTraceDeliveryStatus,
    AgentTraceFinalStatus,
    AgentTraceFinalizeCommandDto,
    AgentTraceStartCommandDto,
)
from veterinary_agent.agent_runner import (
    AgentRunStatus,
    AgentRunSummaryDto,
    AgentRunnerTraceWriteStatus,
    AgentUsageSummaryDto,
)
from veterinary_agent.checkpoint_store import DATABASE_URL_ENV_NAME
from veterinary_agent.llm_gateway import (
    LlmCallSummaryDto,
    LlmFinishReason,
    LlmTraceWriteStatus,
    LlmUsageSummaryDto,
)
from veterinary_agent.logic_trace_store import (
    AppendTraceEventCommandDto,
    BuildTraceProjectionCommandDto,
    GetTraceQueryDto,
    LogicTraceFinalStatus,
    ListTracesQueryDto,
    RecordTraceArtifactCommandDto,
    StartTraceCommandDto,
    TraceArtifactType,
    TraceProjectionType,
    create_sqlalchemy_logic_trace_store,
)
from veterinary_agent.pet_session_policy import (
    PetSessionDecision,
    PetSessionPolicyAction,
    PetSessionTraceRecordDto,
    PetSessionTraceWriteStatus,
)


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
    *,
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


def _open_engine(database_url: str) -> Engine:
    """打开测试数据库引擎。

    :param database_url: SQLAlchemy 数据库连接地址。
    :return: 已创建的 SQLAlchemy Engine。
    """

    return create_engine(database_url)


def _now() -> datetime:
    """读取当前 UTC 时间。

    :return: 当前 UTC 时间。
    """

    return datetime.now(UTC)


def test_logic_trace_store_migration_creates_expected_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 Alembic head 创建 LogicTraceStore 表结构。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "migration.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    engine = _open_engine(database_url)
    try:
        table_names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert {
        "logic_trace",
        "logic_trace_event",
        "logic_trace_call_summary",
        "logic_trace_artifact",
        "logic_trace_projection",
        "logic_trace_outbox",
    }.issubset(table_names)


def test_sqlalchemy_logic_trace_store_records_trace_timeline_and_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证真实存储可写入 trace、事件、artifact、投影并查询完整详情。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "trace.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store = create_sqlalchemy_logic_trace_store(database_url)
    try:
        start_result = asyncio.run(
            store.start_trace(
                StartTraceCommandDto(
                    request_id="req_1",
                    trace_id="trace_1",
                    turn_id="turn_1",
                    run_id="run_1",
                    session_id="session_1",
                    user_id="user_1",
                    pet_id="pet_1",
                    params_version="params_v1",
                    config_snapshot_id="cfg_1",
                    idempotency_key="idem_1",
                )
            )
        )
        event_result = asyncio.run(
            store.append_trace_event(
                AppendTraceEventCommandDto(
                    request_id="req_1",
                    trace_id="trace_1",
                    event_id="event_1",
                    event_type="graph.node_completed",
                    source_component="GraphRuntime",
                    node_id="node_1",
                    summary={"patch_keys": ["business_state"]},
                    business_payload={"safe_field": "value"},
                    schema_ref="vet.trace.patch.v1",
                    created_at=_now(),
                )
            )
        )
        artifact_result = asyncio.run(
            store.record_trace_artifact(
                RecordTraceArtifactCommandDto(
                    artifact_id="artifact_1",
                    trace_id="trace_1",
                    artifact_type=TraceArtifactType.OUTPUT_SUMMARY,
                    storage_ref="s3://trace/artifact_1",
                    content_hash="sha256:test",
                    visibility_policy="internal_only",
                    metadata={"kind": "summary"},
                    created_at=_now(),
                )
            )
        )
        projection = asyncio.run(
            store.build_trace_projection(
                BuildTraceProjectionCommandDto(
                    trace_id="trace_1",
                    projection_type=TraceProjectionType.TIMELINE,
                    version="logic-trace.projection.v1",
                    request_id="req_1",
                )
            )
        )
        detail = asyncio.run(
            store.get_trace(GetTraceQueryDto(trace_id="trace_1", request_id="req_1"))
        )
        page = asyncio.run(
            store.list_traces(ListTracesQueryDto(session_id="session_1"))
        )
    finally:
        store.dispose()

    assert start_result.status.value == "written"
    assert event_result.status.value == "written"
    assert artifact_result.status.value == "written"
    assert projection.projection_type is TraceProjectionType.TIMELINE
    assert detail.trace.trace_id == "trace_1"
    assert len(detail.events) == 1
    assert len(detail.artifacts) == 1
    assert len(detail.projections) == 1
    assert page.total == 1


def test_sqlalchemy_logic_trace_store_adapts_existing_trace_ports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证真实存储可适配应用层、LLM、AgentRunner 与 PetSessionPolicy trace 端口。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "adapters.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store = create_sqlalchemy_logic_trace_store(database_url)
    try:
        start_result = asyncio.run(
            store.start_trace(
                AgentTraceStartCommandDto(
                    request_id="req_2",
                    trace_id="trace_2",
                    turn_id="turn_2",
                    run_id="run_2",
                    session_id="session_2",
                    user_id="user_2",
                    pet_id="pet_2",
                    params_version="params_v1",
                    config_snapshot_id="cfg_1",
                    idempotency_key="idem_2",
                )
            )
        )
        llm_result = asyncio.run(
            store.write_summary(
                LlmCallSummaryDto(
                    call_id="llm_1",
                    trace_id="trace_2",
                    request_id="req_2",
                    caller_component="AgentRunner",
                    requested_profile_id="profile_primary",
                    actual_profile_id="profile_primary",
                    provider_route_id="route_primary",
                    actual_model="model_1",
                    status="succeeded",
                    finish_reason=LlmFinishReason.STOP,
                    usage=LlmUsageSummaryDto(input_tokens=10, output_tokens=5),
                    latency_ms=120,
                    retry_count=0,
                    fallback_chain=["profile_primary"],
                    config_snapshot_id="cfg_1",
                )
            )
        )
        agent_result = asyncio.run(
            store.write_run_summary(
                AgentRunSummaryDto(
                    run_id="agent_run_1",
                    trace_id="trace_2",
                    request_id="req_2",
                    agent_id="agent_1",
                    agent_version="v1",
                    model_profile="profile_primary",
                    actual_model="model_1",
                    status=AgentRunStatus.SUCCEEDED,
                    schema_valid=True,
                    usage=AgentUsageSummaryDto(input_tokens=10, output_tokens=5),
                    latency_ms=130,
                    retry_count=0,
                )
            )
        )
        policy_result = asyncio.run(
            store.write_decision(
                PetSessionTraceRecordDto(
                    request_id="req_2",
                    trace_id="trace_2",
                    user_id="user_2",
                    session_id="session_2",
                    requested_pet_id="pet_2",
                    current_pet_id="pet_2",
                    decision=PetSessionDecision.ALLOW_NEW_SESSION_BOUND,
                    policy_action=PetSessionPolicyAction.ALLOW_CONTINUE,
                    allow_continue=True,
                    retryable=False,
                    is_new_session=True,
                    params_version="params_v1",
                    config_snapshot_id="cfg_1",
                )
            )
        )
        final_result = asyncio.run(
            store.finalize_trace(
                AgentTraceFinalizeCommandDto(
                    request_id="req_2",
                    trace_id="trace_2",
                    turn_id="turn_2",
                    run_id="run_2",
                    final_status=AgentTraceFinalStatus.COMPLETED,
                    user_message_id="message_2",
                    summary={"segment_count": 1},
                )
            )
        )
        detail = asyncio.run(
            store.get_trace(GetTraceQueryDto(trace_id="trace_2", request_id="req_2"))
        )
    finally:
        store.dispose()

    assert start_result.status is AgentTraceDeliveryStatus.WRITTEN
    assert llm_result.status is LlmTraceWriteStatus.DELIVERED
    assert agent_result.status is AgentRunnerTraceWriteStatus.DELIVERED
    assert policy_result.status is PetSessionTraceWriteStatus.RECORDED
    assert final_result.status is AgentTraceDeliveryStatus.WRITTEN
    assert detail.trace.final_status is LogicTraceFinalStatus.COMPLETED
    assert len(detail.call_summaries) == 3

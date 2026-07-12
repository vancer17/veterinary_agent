##################################################################################################
# 文件: tests/logic_trace_store/helpers.py
# 作用: 提供 LogicTraceStore 组件测试内复用的数据库迁移、临时存储与命令 DTO 构造能力。
# 边界: 仅服务测试包；不承载生产逻辑，不绕过 LogicTraceStore 包级公共出口访问内部实现。
##################################################################################################

import asyncio
from collections.abc import Coroutine, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeVar

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, Table, create_engine, func, select
from sqlalchemy.engine import RowMapping

from veterinary_agent.checkpoint_store import DATABASE_URL_ENV_NAME
from veterinary_agent.logic_trace_store import (
    AppendTraceEventCommandDto,
    FinalizeTraceCommandDto,
    JsonMap,
    LogicTraceFinalStatus,
    LogicTraceSchemaValidator,
    LogicTraceStoreSettings,
    RecordCallSummaryCommandDto,
    RecordTraceArtifactCommandDto,
    SqlAlchemyLogicTraceStore,
    StartTraceCommandDto,
    TraceArtifactType,
    TraceCallStatus,
    TraceCallType,
    create_sqlalchemy_logic_trace_store,
)

_T = TypeVar("_T")


def run_async(awaitable: Coroutine[object, object, _T]) -> _T:
    """同步运行一个组件测试中的异步调用。

    :param awaitable: 待运行的协程对象。
    :return: 协程执行后的返回值。
    """

    return asyncio.run(awaitable)


def build_alembic_config() -> Config:
    """构建测试用 Alembic 配置对象。

    :return: 指向项目 alembic.ini 的 Alembic 配置对象。
    """

    return Config("alembic.ini")


def build_sqlite_database_url(database_path: Path) -> str:
    """构建临时 SQLite 数据库连接地址。

    :param database_path: 临时 SQLite 数据库文件路径。
    :return: SQLAlchemy 可使用的 SQLite 数据库 URL。
    """

    return f"sqlite:///{database_path}"


def upgrade_to_head(
    *,
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    """运行项目 Alembic migration 到最新版本。

    :param monkeypatch: pytest 环境变量修改夹具。
    :param database_url: 本次迁移使用的数据库连接地址。
    :return: None。
    """

    monkeypatch.setenv(DATABASE_URL_ENV_NAME, database_url)
    command.upgrade(build_alembic_config(), "head")


def downgrade_to_base(
    *,
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    """回滚项目 Alembic migration 到 base。

    :param monkeypatch: pytest 环境变量修改夹具。
    :param database_url: 本次迁移使用的数据库连接地址。
    :return: None。
    """

    monkeypatch.setenv(DATABASE_URL_ENV_NAME, database_url)
    command.downgrade(build_alembic_config(), "base")


def open_engine(database_url: str) -> Engine:
    """打开测试数据库引擎。

    :param database_url: SQLAlchemy 数据库连接地址。
    :return: 已创建的 SQLAlchemy Engine。
    """

    return create_engine(database_url)


def create_migrated_store(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    database_name: str,
    settings: LogicTraceStoreSettings | None = None,
    schema_validator: LogicTraceSchemaValidator | None = None,
) -> tuple[str, SqlAlchemyLogicTraceStore]:
    """创建已完成迁移的临时 LogicTraceStore。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :param database_name: 临时 SQLite 数据库文件名。
    :param settings: 可选 LogicTraceStore 测试配置。
    :param schema_validator: 可选 trace event schema 校验器。
    :return: 数据库连接地址和已创建的 SQLAlchemy LogicTraceStore。
    """

    database_url = build_sqlite_database_url(tmp_path / database_name)
    upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    return (
        database_url,
        create_sqlalchemy_logic_trace_store(
            database_url,
            settings=settings,
            schema_validator=schema_validator,
        ),
    )


def now(offset_seconds: int = 0) -> datetime:
    """读取测试用 UTC 时间。

    :param offset_seconds: 相对当前时间追加的秒数偏移。
    :return: 当前 UTC 时间加偏移后的时间。
    """

    return datetime.now(UTC) + timedelta(seconds=offset_seconds)


def build_start_command(
    *,
    suffix: str,
    trace_id: str | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    idempotency_key: str | None = None,
) -> StartTraceCommandDto:
    """构建启动逻辑链命令。

    :param suffix: 用于生成默认字段值的稳定后缀。
    :param trace_id: 可选逻辑链 ID 覆盖值。
    :param request_id: 可选请求 ID 覆盖值。
    :param session_id: 可选 session ID 覆盖值。
    :param run_id: 可选运行 ID 覆盖值。
    :param idempotency_key: 可选幂等键覆盖值。
    :return: 启动逻辑链命令 DTO。
    """

    return StartTraceCommandDto(
        request_id=request_id or f"req_{suffix}",
        trace_id=trace_id or f"trace_{suffix}",
        turn_id=f"turn_{suffix}",
        run_id=run_id or f"run_{suffix}",
        session_id=session_id or f"session_{suffix}",
        user_id=f"user_{suffix}",
        pet_id=f"pet_{suffix}",
        params_version="params_v1",
        config_snapshot_id="cfg_1",
        idempotency_key=idempotency_key or f"idem_{suffix}",
    )


def build_event_command(
    *,
    suffix: str,
    trace_id: str,
    request_id: str | None = None,
    event_id: str | None = None,
    summary: Mapping[str, object] | None = None,
    business_payload: Mapping[str, object] | None = None,
    offset_seconds: int = 0,
) -> AppendTraceEventCommandDto:
    """构建追加逻辑链事件命令。

    :param suffix: 用于生成默认字段值的稳定后缀。
    :param trace_id: 目标逻辑链 ID。
    :param request_id: 可选请求 ID 覆盖值。
    :param event_id: 可选事件 ID 覆盖值。
    :param summary: 可选事件摘要覆盖值。
    :param business_payload: 可选业务负载覆盖值。
    :param offset_seconds: 事件创建时间偏移秒数。
    :return: 追加逻辑链事件命令 DTO。
    """

    return AppendTraceEventCommandDto(
        request_id=request_id or f"req_{suffix}",
        trace_id=trace_id,
        event_id=event_id or f"event_{suffix}",
        event_type="graph.node_completed",
        source_component="GraphRuntime",
        node_id=f"node_{suffix}",
        segment_id=f"segment_{suffix}",
        summary=dict(summary or {"patch_keys": ["business_state"]}),
        business_payload=dict(business_payload or {"safe_field": f"value_{suffix}"}),
        schema_ref="vet.trace.patch.v1",
        created_at=now(offset_seconds),
    )


def build_call_summary_command(
    *,
    suffix: str,
    trace_id: str,
    request_id: str | None = None,
    call_id: str | None = None,
    summary: Mapping[str, object] | None = None,
) -> RecordCallSummaryCommandDto:
    """构建记录调用摘要命令。

    :param suffix: 用于生成默认字段值的稳定后缀。
    :param trace_id: 目标逻辑链 ID。
    :param request_id: 可选请求 ID 覆盖值。
    :param call_id: 可选调用 ID 覆盖值。
    :param summary: 可选调用摘要覆盖值。
    :return: 记录调用摘要命令 DTO。
    """

    return RecordCallSummaryCommandDto(
        call_id=call_id or f"call_{suffix}",
        trace_id=trace_id,
        request_id=request_id or f"req_{suffix}",
        call_type=TraceCallType.MODEL,
        source_component="LlmGateway",
        provider_ref="route_primary",
        input_ref="profile_primary",
        output_ref="model_primary",
        usage={"input_tokens": 12, "output_tokens": 6},
        status=TraceCallStatus.SUCCEEDED,
        summary=dict(summary or {"finish_reason": "stop"}),
        created_at=now(),
    )


def build_artifact_command(
    *,
    suffix: str,
    trace_id: str,
    artifact_id: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> RecordTraceArtifactCommandDto:
    """构建记录 trace artifact 命令。

    :param suffix: 用于生成默认字段值的稳定后缀。
    :param trace_id: 目标逻辑链 ID。
    :param artifact_id: 可选 artifact ID 覆盖值。
    :param metadata: 可选 artifact 元信息覆盖值。
    :return: 记录 trace artifact 命令 DTO。
    """

    return RecordTraceArtifactCommandDto(
        artifact_id=artifact_id or f"artifact_{suffix}",
        trace_id=trace_id,
        artifact_type=TraceArtifactType.OUTPUT_SUMMARY,
        storage_ref=f"s3://logic-trace/{trace_id}/artifact_{suffix}",
        content_hash=f"sha256:{suffix}",
        visibility_policy="internal_only",
        metadata=dict(metadata or {"kind": "summary"}),
        created_at=now(),
    )


def build_finalize_command(
    *,
    suffix: str,
    trace_id: str,
    final_status: LogicTraceFinalStatus = LogicTraceFinalStatus.COMPLETED,
    user_message_id: str | None = None,
    error_code: str | None = None,
    summary: Mapping[str, object] | None = None,
) -> FinalizeTraceCommandDto:
    """构建完成逻辑链命令。

    :param suffix: 用于生成默认字段值的稳定后缀。
    :param trace_id: 目标逻辑链 ID。
    :param final_status: 逻辑链最终状态。
    :param user_message_id: 可选用户消息 ID。
    :param error_code: 可选失败错误码。
    :param summary: 可选完成摘要。
    :return: 完成逻辑链命令 DTO。
    """

    return FinalizeTraceCommandDto(
        request_id=f"req_{suffix}",
        trace_id=trace_id,
        turn_id=f"turn_{suffix}",
        run_id=f"run_{suffix}",
        final_status=final_status,
        user_message_id=user_message_id or f"message_{suffix}",
        error_code=error_code,
        summary=dict(summary or {"segment_count": 1}),
        finalized_at=now(),
    )


def count_table_rows(
    *,
    engine: Engine,
    table: Table,
) -> int:
    """统计测试数据库中指定表的行数。

    :param engine: SQLAlchemy 数据库引擎。
    :param table: 需要统计的公开表对象。
    :return: 指定表中的行数。
    """

    with engine.begin() as connection:
        return int(
            connection.execute(select(func.count()).select_from(table)).scalar_one()
        )


def read_table_rows(
    *,
    engine: Engine,
    table: Table,
) -> list[RowMapping]:
    """读取测试数据库中指定表的所有行。

    :param engine: SQLAlchemy 数据库引擎。
    :param table: 需要读取的公开表对象。
    :return: 指定表中的行映射列表。
    """

    with engine.begin() as connection:
        return list(connection.execute(select(table)).mappings().all())


def assert_json_map(value: object) -> JsonMap:
    """将测试读取到的未知 JSON 值断言为字符串键映射。

    :param value: 待断言的未知 JSON 值。
    :return: 字符串键 JSON 映射。
    """

    assert isinstance(value, dict)
    return {str(key): item for key, item in value.items()}

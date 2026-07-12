##################################################################################################
# 文件: tests/logic_trace_store/test_projection_outbox_contract.py
# 作用: 验证 LogicTraceStore 组件 projection 构建和 schema 降级 outbox 补偿契约。
# 边界: 使用测试内 schema validator 空壳；不实现真实 VetTraceSchema、事件总线或 SSE 推送。
##################################################################################################

from pathlib import Path

import pytest

from tests.logic_trace_store.helpers import (
    assert_json_map,
    build_artifact_command,
    build_call_summary_command,
    build_event_command,
    build_start_command,
    count_table_rows,
    create_migrated_store,
    open_engine,
    read_table_rows,
    run_async,
)
from veterinary_agent.logic_trace_store import (
    AppendTraceEventCommandDto,
    BuildTraceProjectionCommandDto,
    GetTraceQueryDto,
    LOGIC_TRACE_OUTBOX_TABLE,
    LOGIC_TRACE_PROJECTION_TABLE,
    LogicTraceErrorCode,
    LogicTraceSchemaValidationResultDto,
    LogicTraceStoreError,
    LogicTraceWriteStatus,
    TraceOutboxStatus,
    TraceProjectionType,
)


class DegradedSchemaValidator:
    """测试用 schema validator，模拟 schema 尚未完全接入但允许透传。"""

    async def validate_trace_event(
        self,
        command: AppendTraceEventCommandDto,
    ) -> LogicTraceSchemaValidationResultDto:
        """返回带降级标记的 schema 校验结果。

        :param command: 待校验的逻辑链事件命令。
        :return: 表示可写入但带降级标记的校验结果 DTO。
        """

        return LogicTraceSchemaValidationResultDto(
            valid=True,
            degraded_flags=["vet_trace_schema_not_connected"],
            normalized_business_payload={
                **dict(command.business_payload),
                "normalized_by": "test_validator",
            },
            schema_ref=command.schema_ref,
            errors=[],
            warnings=["schema.todo"],
        )


def test_logic_trace_store_writes_outbox_when_schema_validation_degrades(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 schema 降级透传会写入事件并产生 outbox 补偿记录。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url, store = create_migrated_store(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        database_name="degraded_outbox.db",
        schema_validator=DegradedSchemaValidator(),
    )
    engine = open_engine(database_url)
    try:
        run_async(
            store.start_trace(
                build_start_command(suffix="outbox", trace_id="trace_outbox")
            )
        )
        result = run_async(
            store.append_trace_event(
                build_event_command(suffix="outbox", trace_id="trace_outbox")
            )
        )
        detail = run_async(
            store.get_trace(
                GetTraceQueryDto(trace_id="trace_outbox", request_id="req_outbox")
            )
        )
        outbox_rows = read_table_rows(engine=engine, table=LOGIC_TRACE_OUTBOX_TABLE)
    finally:
        engine.dispose()
        store.dispose()

    assert result.status is LogicTraceWriteStatus.WRITTEN
    assert len(detail.events) == 1
    assert detail.events[0].business_payload["normalized_by"] == "test_validator"
    assert detail.events[0].summary["degraded_flags"] == [
        "vet_trace_schema_not_connected"
    ]
    assert len(outbox_rows) == 1
    assert outbox_rows[0]["event_kind"] == "trace_schema_degraded"
    assert outbox_rows[0]["status"] == TraceOutboxStatus.PENDING.value
    payload = assert_json_map(outbox_rows[0]["payload"])
    assert payload["trace_id"] == "trace_outbox"
    assert payload["event_id"] == "event_outbox"


def test_logic_trace_store_builds_projection_variants_and_updates_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 timeline、decision、artifact 和 reasoning display 投影可持久化。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url, store = create_migrated_store(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        database_name="projection_variants.db",
    )
    engine = open_engine(database_url)
    try:
        run_async(
            store.start_trace(build_start_command(suffix="proj", trace_id="trace_proj"))
        )
        run_async(
            store.append_trace_event(
                build_event_command(suffix="proj", trace_id="trace_proj")
            )
        )
        run_async(
            store.record_call_summary(
                build_call_summary_command(suffix="proj", trace_id="trace_proj")
            )
        )
        run_async(
            store.record_trace_artifact(
                build_artifact_command(suffix="proj", trace_id="trace_proj")
            )
        )
        timeline = run_async(
            store.build_trace_projection(
                BuildTraceProjectionCommandDto(
                    trace_id="trace_proj",
                    projection_type=TraceProjectionType.TIMELINE,
                    version="logic-trace.projection.test",
                    request_id="req_proj",
                )
            )
        )
        decision = run_async(
            store.build_trace_projection(
                BuildTraceProjectionCommandDto(
                    trace_id="trace_proj",
                    projection_type=TraceProjectionType.DECISION,
                    version="logic-trace.projection.test",
                    request_id="req_proj",
                )
            )
        )
        artifact = run_async(
            store.build_trace_projection(
                BuildTraceProjectionCommandDto(
                    trace_id="trace_proj",
                    projection_type=TraceProjectionType.ARTIFACT,
                    version="logic-trace.projection.test",
                    request_id="req_proj",
                )
            )
        )
        reasoning = run_async(
            store.build_trace_projection(
                BuildTraceProjectionCommandDto(
                    trace_id="trace_proj",
                    projection_type=TraceProjectionType.REASONING_DISPLAY,
                    version="logic-trace.projection.test",
                    request_id="req_proj",
                    segment_id="segment_proj",
                    display_payload={
                        "projection_id": "reasoning_trace_proj",
                        "trace_id": "trace_proj",
                        "segment_id": "segment_proj",
                        "title": "处理过程",
                        "text": "已完成输入校验并调用模型。",
                    },
                )
            )
        )
        repeated_reasoning = run_async(
            store.build_trace_projection(
                BuildTraceProjectionCommandDto(
                    trace_id="trace_proj",
                    projection_type=TraceProjectionType.REASONING_DISPLAY,
                    version="logic-trace.projection.test",
                    request_id="req_proj",
                    segment_id="segment_proj",
                    display_payload={
                        "projection_id": "reasoning_trace_proj",
                        "trace_id": "trace_proj",
                        "segment_id": "segment_proj",
                        "title": "处理过程",
                        "text": "已完成输入校验并调用模型。",
                    },
                )
            )
        )
        detail = run_async(
            store.get_trace(
                GetTraceQueryDto(trace_id="trace_proj", request_id="req_proj")
            )
        )
        projection_count = count_table_rows(
            engine=engine,
            table=LOGIC_TRACE_PROJECTION_TABLE,
        )
    finally:
        engine.dispose()
        store.dispose()

    assert timeline.projection_type is TraceProjectionType.TIMELINE
    assert decision.projection_type is TraceProjectionType.DECISION
    assert artifact.projection_type is TraceProjectionType.ARTIFACT
    assert reasoning.projection_type is TraceProjectionType.REASONING_DISPLAY
    assert repeated_reasoning.projection_id == reasoning.projection_id
    assert reasoning.view_payload["text"] == "已完成输入校验并调用模型。"
    assert projection_count == 4
    assert len(detail.projections) == 4
    assert detail.trace.summary["projection_count"] == 4


def test_logic_trace_store_rejects_reasoning_projection_without_safe_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 reasoning display 缺少安全展示负载时返回投影构建错误。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    _, store = create_migrated_store(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        database_name="reasoning_projection_error.db",
    )
    try:
        run_async(
            store.start_trace(
                build_start_command(suffix="empty_reasoning", trace_id="trace_empty")
            )
        )
        with pytest.raises(LogicTraceStoreError) as exc_info:
            run_async(
                store.build_trace_projection(
                    BuildTraceProjectionCommandDto(
                        trace_id="trace_empty",
                        projection_type=TraceProjectionType.REASONING_DISPLAY,
                        version="logic-trace.projection.test",
                        request_id="req_empty_reasoning",
                    )
                )
            )
    finally:
        store.dispose()

    assert exc_info.value.code is LogicTraceErrorCode.TRACE_PROJECTION_BUILD_FAILED
    assert exc_info.value.retryable is False

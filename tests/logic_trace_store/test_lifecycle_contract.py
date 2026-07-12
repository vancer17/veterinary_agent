##################################################################################################
# 文件: tests/logic_trace_store/test_lifecycle_contract.py
# 作用: 验证 LogicTraceStore 组件完整生命周期、详情查询裁剪和列表过滤分页契约。
# 边界: 使用临时 SQLite 与真实 Alembic 迁移；不连接真实 PostgreSQL、不实现业务图或外部 schema。
##################################################################################################

from pathlib import Path

import pytest

from tests.logic_trace_store.helpers import (
    build_artifact_command,
    build_call_summary_command,
    build_event_command,
    build_finalize_command,
    build_start_command,
    create_migrated_store,
    run_async,
)
from veterinary_agent.logic_trace_store import (
    BuildTraceProjectionCommandDto,
    GetTraceQueryDto,
    ListTracesQueryDto,
    LogicTraceFinalStatus,
    LogicTraceStatus,
    LogicTraceWriteStatus,
    TraceProjectionType,
)


def test_logic_trace_store_records_full_component_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 LogicTraceStore 可完成 trace 全生命周期并维护查询投影。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    _, store = create_migrated_store(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        database_name="lifecycle.db",
    )
    try:
        start_result = run_async(
            store.start_trace(build_start_command(suffix="life", trace_id="trace_life"))
        )
        event_result = run_async(
            store.append_trace_event(
                build_event_command(suffix="life", trace_id="trace_life")
            )
        )
        call_result = run_async(
            store.record_call_summary(
                build_call_summary_command(suffix="life", trace_id="trace_life")
            )
        )
        artifact_result = run_async(
            store.record_trace_artifact(
                build_artifact_command(suffix="life", trace_id="trace_life")
            )
        )
        projection = run_async(
            store.build_trace_projection(
                BuildTraceProjectionCommandDto(
                    trace_id="trace_life",
                    projection_type=TraceProjectionType.TIMELINE,
                    version="logic-trace.projection.test",
                    request_id="req_life",
                )
            )
        )
        finalize_result = run_async(
            store.finalize_trace(
                build_finalize_command(suffix="life", trace_id="trace_life")
            )
        )
        detail = run_async(
            store.get_trace(
                GetTraceQueryDto(trace_id="trace_life", request_id="req_life")
            )
        )
    finally:
        store.dispose()

    assert start_result.status is LogicTraceWriteStatus.WRITTEN
    assert event_result.status is LogicTraceWriteStatus.WRITTEN
    assert call_result.status is LogicTraceWriteStatus.WRITTEN
    assert artifact_result.status is LogicTraceWriteStatus.WRITTEN
    assert finalize_result.status is LogicTraceWriteStatus.WRITTEN
    assert projection.projection_type is TraceProjectionType.TIMELINE
    assert detail.trace.status is LogicTraceStatus.FINALIZED
    assert detail.trace.final_status is LogicTraceFinalStatus.COMPLETED
    assert detail.trace.summary["event_count"] == 1
    assert detail.trace.summary["call_summary_count"] == 1
    assert detail.trace.summary["artifact_count"] == 1
    assert detail.trace.summary["projection_count"] == 1
    assert len(detail.events) == 1
    assert len(detail.call_summaries) == 1
    assert len(detail.artifacts) == 1
    assert len(detail.projections) == 1


def test_logic_trace_store_get_trace_honors_include_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证详情查询可按 include 标记裁剪明细集合。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    _, store = create_migrated_store(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        database_name="detail_flags.db",
    )
    try:
        run_async(
            store.start_trace(
                build_start_command(suffix="detail", trace_id="trace_detail")
            )
        )
        run_async(
            store.append_trace_event(
                build_event_command(suffix="detail", trace_id="trace_detail")
            )
        )
        run_async(
            store.record_call_summary(
                build_call_summary_command(suffix="detail", trace_id="trace_detail")
            )
        )
        run_async(
            store.record_trace_artifact(
                build_artifact_command(suffix="detail", trace_id="trace_detail")
            )
        )
        detail = run_async(
            store.get_trace(
                GetTraceQueryDto(
                    trace_id="trace_detail",
                    request_id="req_detail",
                    include_events=False,
                    include_calls=False,
                    include_artifacts=False,
                    include_projections=False,
                )
            )
        )
    finally:
        store.dispose()

    assert detail.trace.trace_id == "trace_detail"
    assert detail.events == []
    assert detail.call_summaries == []
    assert detail.artifacts == []
    assert detail.projections == []


def test_logic_trace_store_lists_traces_with_filters_and_pagination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 trace 列表查询支持过滤、trace ID 集合和分页。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    _, store = create_migrated_store(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        database_name="list_filters.db",
    )
    try:
        run_async(
            store.start_trace(
                build_start_command(
                    suffix="a",
                    trace_id="trace_a",
                    session_id="shared_session",
                    run_id="run_a",
                    request_id="req_a",
                )
            )
        )
        run_async(
            store.start_trace(
                build_start_command(
                    suffix="b",
                    trace_id="trace_b",
                    session_id="shared_session",
                    run_id="run_b",
                    request_id="req_b",
                )
            )
        )
        run_async(
            store.start_trace(
                build_start_command(
                    suffix="c",
                    trace_id="trace_c",
                    session_id="other_session",
                    run_id="run_c",
                    request_id="req_c",
                )
            )
        )
        by_session = run_async(
            store.list_traces(ListTracesQueryDto(session_id="shared_session", limit=10))
        )
        by_run = run_async(store.list_traces(ListTracesQueryDto(run_id="run_b")))
        by_request = run_async(
            store.list_traces(ListTracesQueryDto(request_id="req_c"))
        )
        by_trace_ids = run_async(
            store.list_traces(
                ListTracesQueryDto(trace_ids=["trace_a", "trace_c"], limit=10)
            )
        )
        paged = run_async(
            store.list_traces(
                ListTracesQueryDto(session_id="shared_session", limit=1, offset=1)
            )
        )
    finally:
        store.dispose()

    assert by_session.total == 2
    assert {trace.trace_id for trace in by_session.traces} == {"trace_a", "trace_b"}
    assert by_run.total == 1
    assert by_run.traces[0].trace_id == "trace_b"
    assert by_request.total == 1
    assert by_request.traces[0].trace_id == "trace_c"
    assert by_trace_ids.total == 2
    assert {trace.trace_id for trace in by_trace_ids.traces} == {
        "trace_a",
        "trace_c",
    }
    assert paged.total == 2
    assert len(paged.traces) == 1

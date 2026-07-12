##################################################################################################
# 文件: tests/logic_trace_store/test_idempotency_contract.py
# 作用: 验证 LogicTraceStore 组件在重复写入、幂等命中和冲突输入下的稳定契约。
# 边界: 使用临时 SQLite 与真实 Alembic 迁移；不验证底层数据库并发锁语义。
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
    GetTraceQueryDto,
    LogicTraceErrorCode,
    LogicTraceWriteResultDto,
    LogicTraceWriteStatus,
)


def test_logic_trace_store_deduplicates_repeated_component_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证重复启动、事件、调用摘要、artifact 和完成写入均命中幂等。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    _, store = create_migrated_store(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        database_name="idempotency.db",
    )
    try:
        start_command = build_start_command(
            suffix="idem",
            trace_id="trace_idem",
        )
        event_command = build_event_command(suffix="idem", trace_id="trace_idem")
        call_command = build_call_summary_command(
            suffix="idem",
            trace_id="trace_idem",
        )
        artifact_command = build_artifact_command(
            suffix="idem",
            trace_id="trace_idem",
        )
        finalize_command = build_finalize_command(
            suffix="idem",
            trace_id="trace_idem",
        )

        first_start = run_async(store.start_trace(start_command))
        second_start = run_async(store.start_trace(start_command))
        first_event = run_async(store.append_trace_event(event_command))
        second_event = run_async(store.append_trace_event(event_command))
        first_call = run_async(store.record_call_summary(call_command))
        second_call = run_async(store.record_call_summary(call_command))
        first_artifact = run_async(store.record_trace_artifact(artifact_command))
        second_artifact = run_async(store.record_trace_artifact(artifact_command))
        first_finalize = run_async(store.finalize_trace(finalize_command))
        second_finalize = run_async(store.finalize_trace(finalize_command))
        detail = run_async(
            store.get_trace(
                GetTraceQueryDto(trace_id="trace_idem", request_id="req_idem")
            )
        )
    finally:
        store.dispose()

    assert first_start.status is LogicTraceWriteStatus.WRITTEN
    assert isinstance(second_start, LogicTraceWriteResultDto)
    assert second_start.idempotent is True
    assert first_event.status is LogicTraceWriteStatus.WRITTEN
    assert second_event.idempotent is True
    assert first_call.status is LogicTraceWriteStatus.WRITTEN
    assert second_call.idempotent is True
    assert first_artifact.status is LogicTraceWriteStatus.WRITTEN
    assert second_artifact.idempotent is True
    assert first_finalize.status is LogicTraceWriteStatus.WRITTEN
    assert isinstance(second_finalize, LogicTraceWriteResultDto)
    assert second_finalize.idempotent is True
    assert len(detail.events) == 1
    assert len(detail.call_summaries) == 1
    assert len(detail.artifacts) == 1


def test_logic_trace_store_rejects_conflicting_start_for_existing_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证相同 trace ID 携带冲突上下文时返回稳定领域错误。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    _, store = create_migrated_store(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        database_name="conflicting_start.db",
    )
    try:
        first_result = run_async(
            store.start_trace(
                build_start_command(
                    suffix="conflict",
                    trace_id="trace_conflict",
                    request_id="req_original",
                    run_id="run_original",
                    idempotency_key="idem_original",
                )
            )
        )
        conflict_result = run_async(
            store.start_trace(
                build_start_command(
                    suffix="conflict",
                    trace_id="trace_conflict",
                    request_id="req_changed",
                    run_id="run_changed",
                    idempotency_key="idem_changed",
                )
            )
        )
        detail = run_async(
            store.get_trace(
                GetTraceQueryDto(
                    trace_id="trace_conflict",
                    request_id="req_original",
                )
            )
        )
    finally:
        store.dispose()

    assert first_result.status is LogicTraceWriteStatus.WRITTEN
    assert conflict_result.status is LogicTraceWriteStatus.SKIPPED
    assert (
        conflict_result.error_code == LogicTraceErrorCode.TRACE_INVALID_ARGUMENT.value
    )
    assert conflict_result.retryable is False
    assert detail.trace.request_id == "req_original"
    assert detail.trace.run_id == "run_original"

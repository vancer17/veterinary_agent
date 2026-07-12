##################################################################################################
# 文件: tests/logic_trace_store/test_error_contract.py
# 作用: 验证 LogicTraceStore 组件在 schema 校验失败、payload 超限和非法状态下的错误映射契约。
# 边界: 使用测试内 schema validator 空壳；不实现真实 VetTraceSchema 或跨领域业务规则。
##################################################################################################

from pathlib import Path

import pytest

from tests.logic_trace_store.helpers import (
    build_event_command,
    build_finalize_command,
    build_start_command,
    count_table_rows,
    create_migrated_store,
    open_engine,
    run_async,
)
from veterinary_agent.logic_trace_store import (
    AppendTraceEventCommandDto,
    GetTraceQueryDto,
    LOGIC_TRACE_EVENT_TABLE,
    LogicTraceErrorCode,
    LogicTraceSchemaValidationResultDto,
    LogicTraceStoreError,
    LogicTraceStoreSettings,
    LogicTraceWriteStatus,
)


class InvalidSchemaValidator:
    """测试用 schema validator，模拟 VetTraceSchema 校验失败。"""

    async def validate_trace_event(
        self,
        command: AppendTraceEventCommandDto,
    ) -> LogicTraceSchemaValidationResultDto:
        """返回 schema 校验失败结果。

        :param command: 待校验的逻辑链事件命令。
        :return: 表示 schema 校验失败的结果 DTO。
        """

        return LogicTraceSchemaValidationResultDto(
            valid=False,
            degraded_flags=[],
            normalized_business_payload=dict(command.business_payload),
            schema_ref=command.schema_ref,
            errors=["schema.invalid"],
            warnings=[],
        )


def test_logic_trace_store_maps_invalid_schema_to_skipped_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 schema 校验失败会返回稳定 skipped 结果且不落事件。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url, store = create_migrated_store(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        database_name="invalid_schema.db",
        schema_validator=InvalidSchemaValidator(),
    )
    engine = open_engine(database_url)
    try:
        run_async(
            store.start_trace(
                build_start_command(suffix="schema", trace_id="trace_schema")
            )
        )
        result = run_async(
            store.append_trace_event(
                build_event_command(suffix="schema", trace_id="trace_schema")
            )
        )
        event_count = count_table_rows(engine=engine, table=LOGIC_TRACE_EVENT_TABLE)
    finally:
        engine.dispose()
        store.dispose()

    assert result.status is LogicTraceWriteStatus.SKIPPED
    assert result.error_code == LogicTraceErrorCode.TRACE_EVENT_SCHEMA_INVALID.value
    assert result.retryable is False
    assert event_count == 0


def test_logic_trace_store_applies_event_payload_limit_from_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证事件 payload 上限配置会阻止超限事件落库。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url, store = create_migrated_store(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        database_name="payload_limit.db",
        settings=LogicTraceStoreSettings(max_event_payload_bytes=16),
    )
    engine = open_engine(database_url)
    try:
        run_async(
            store.start_trace(
                build_start_command(suffix="limit", trace_id="trace_limit")
            )
        )
        result = run_async(
            store.append_trace_event(
                build_event_command(
                    suffix="limit",
                    trace_id="trace_limit",
                    business_payload={"oversized": "x" * 128},
                )
            )
        )
        event_count = count_table_rows(engine=engine, table=LOGIC_TRACE_EVENT_TABLE)
    finally:
        engine.dispose()
        store.dispose()

    assert result.status is LogicTraceWriteStatus.SKIPPED
    assert result.error_code == LogicTraceErrorCode.TRACE_INVALID_ARGUMENT.value
    assert result.retryable is False
    assert event_count == 0


def test_logic_trace_store_rejects_append_after_trace_finalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 trace 完结后继续追加事件会返回不可重试错误。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    _, store = create_migrated_store(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        database_name="finalized_append.db",
    )
    try:
        run_async(
            store.start_trace(
                build_start_command(suffix="finalized", trace_id="trace_finalized")
            )
        )
        run_async(
            store.finalize_trace(
                build_finalize_command(
                    suffix="finalized",
                    trace_id="trace_finalized",
                )
            )
        )
        result = run_async(
            store.append_trace_event(
                build_event_command(
                    suffix="finalized",
                    trace_id="trace_finalized",
                )
            )
        )
    finally:
        store.dispose()

    assert result.status is LogicTraceWriteStatus.SKIPPED
    assert result.error_code == LogicTraceErrorCode.TRACE_ALREADY_FINALIZED.value
    assert result.retryable is False


def test_logic_trace_store_raises_domain_error_for_missing_trace_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证查询不存在 trace 时抛出 LogicTraceStore 领域异常。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    _, store = create_migrated_store(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        database_name="missing_trace.db",
    )
    try:
        with pytest.raises(LogicTraceStoreError) as exc_info:
            run_async(
                store.get_trace(
                    GetTraceQueryDto(
                        trace_id="trace_missing",
                        request_id="req_missing",
                    )
                )
            )
    finally:
        store.dispose()

    assert exc_info.value.code is LogicTraceErrorCode.TRACE_NOT_FOUND
    assert exc_info.value.retryable is False

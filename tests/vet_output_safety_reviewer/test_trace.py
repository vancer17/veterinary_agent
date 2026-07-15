##################################################################################################
# 文件: tests/vet_output_safety_reviewer/test_trace.py
# 作用: 验证 VetOutputSafetyReviewer trace sink 适配器的事件映射与脱敏摘要。
# 边界: 仅用测试替身验证结构化写入，不连接真实 LogicTraceStore 或业务数据库。
##################################################################################################

import asyncio

from veterinary_agent.logic_trace_store import LogicTraceWriteStatus
from veterinary_agent.vet_output_safety_reviewer import (
    LogicTraceVetOutputSafetyReviewerTraceSink,
    OutputReviewTraceRecordDto,
    OutputReviewTraceWriteStatus,
)

from .helpers import (
    RecordingLogicTraceStore,
    RecordingOutputReviewTraceSink,
    build_logic_trace_sink_store,
    build_output_review_request,
    build_provider,
    build_reviewer,
)


def test_logic_trace_sink_writes_output_review_summary() -> None:
    """验证输出审查 trace sink 会写入标准事件摘要。

    :return: None。
    """

    provider = build_provider()
    reviewer = build_reviewer(
        provider=provider,
        trace_sink=RecordingOutputReviewTraceSink(),
    )
    request = build_output_review_request(
        provider=provider,
        draft_text="如果出现呼吸困难，可以先观察几天。",
        signal_codes=["SAF-03"],
    )
    result = asyncio.run(reviewer.review_draft_response_safety(request))
    store = RecordingLogicTraceStore(status=LogicTraceWriteStatus.WRITTEN)
    sink = LogicTraceVetOutputSafetyReviewerTraceSink(
        store=build_logic_trace_sink_store(store)
    )

    write_result = asyncio.run(
        sink.write_output_review_trace(
            OutputReviewTraceRecordDto(
                request=request,
                result=result,
                duration_ms=11,
            )
        )
    )

    assert write_result.status is OutputReviewTraceWriteStatus.RECORDED
    assert len(store.events) == 1
    event = store.events[0]
    assert event.event_type == "output_review"
    assert event.schema_ref == "vet.output-review.trace.v1"
    assert event.summary["status"] == result.status.value
    assert event.summary["reviewed_draft_ref"] == result.reviewed_draft_ref
    assert event.business_payload["patch_type"] == "output_review"
    payload = event.business_payload["payload"]
    assert isinstance(payload, dict)
    assert payload["reviewed_draft_ref"] == result.reviewed_draft_ref
    trace_patch = payload["trace_patch"]
    assert isinstance(trace_patch, dict)
    assert trace_patch["reviewer_version"] == "vet-output-safety-reviewer.v1"

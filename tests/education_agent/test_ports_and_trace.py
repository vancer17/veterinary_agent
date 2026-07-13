##################################################################################################
# 文件: tests/education_agent/test_ports_and_trace.py
# 作用: 验证 EducationAgent 的 TODO RAG 与 trace 空壳降级契约。
# 边界: 不实现真实跨领域依赖，只校验 TODO 空壳的保守返回和 trace 降级语义。
##################################################################################################

import asyncio

from veterinary_agent.education_agent import (
    EducationDraftStatus,
    EducationTracePatchDto,
    EducationTraceRecordDto,
    EducationTraceWriteStatus,
    ExplanationDimensionCode,
    RetrievalFacetDto,
    EducationRetrievalPurpose,
    TodoEducationRagPort,
    TodoEducationTraceSink,
)

from .helpers import build_provider, build_request


def test_todo_rag_port_returns_degraded_result() -> None:
    """验证 TODO RAG 端口返回显式降级结果。

    :return: None。
    """

    provider = build_provider()
    request = build_request(provider)
    port = TodoEducationRagPort()
    facet = RetrievalFacetDto(
        dimension_code=ExplanationDimensionCode.DEFINITION,
        retrieval_purpose=EducationRetrievalPurpose.EDUCATION_EXPLANATION,
        queries=["dog seizure"],
        query_hashes=["sha256:test"],
        collections=["vet_kb_public_mvp"],
        top_k=5,
    )

    result = asyncio.run(
        port.retrieve(
            request=request,
            facet=facet,
            timeout_seconds=0.1,
        )
    )

    assert result.degraded is True
    assert result.retrieval_purpose is (
        EducationRetrievalPurpose.EDUCATION_EXPLANATION
    )
    assert result.evidence_hints == []


def test_todo_trace_sink_reports_degraded_write() -> None:
    """验证 TODO trace sink 返回可补偿降级状态。

    :return: None。
    """

    provider = build_provider()
    request = build_request(provider)
    sink = TodoEducationTraceSink()
    record = EducationTraceRecordDto(
        request_id=request.request_id,
        trace_id=request.trace_id,
        run_id=request.run_id,
        session_id=request.session_id,
        user_id=request.user_id,
        current_pet_id=request.current_pet_id or "pet_1",
        task_id=request.task_id,
        status=EducationDraftStatus.INSUFFICIENT_EVIDENCE,
        trace_patch=EducationTracePatchDto(
            education_agent_version="education-agent.v1",
            planner_version="education-planner.v1",
            writer_version="education-writer.v1",
            selected_dimensions=[ExplanationDimensionCode.DEFINITION],
        ),
        evidence_binding_count=0,
        rag_invoked=True,
        params_version=request.params_version,
        config_snapshot_id=request.config_snapshot_id,
    )

    result = asyncio.run(sink.write_education_trace(record))

    assert result.status is EducationTraceWriteStatus.DEGRADED
    assert result.retryable is True

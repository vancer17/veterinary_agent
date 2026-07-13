##################################################################################################
# 文件: tests/standard_consultation_agent/test_ports_and_trace.py
# 作用: 验证 StandardConsultationAgent 的 TODO RAG、MedicationPolicy 与 trace 空壳降级契约。
# 边界: 不实现真实跨领域依赖，只校验 TODO 空壳的保守返回和 trace 降级语义。
##################################################################################################

import asyncio

from veterinary_agent.standard_consultation_agent import (
    ConsultationLayer,
    DraftStatus,
    RetrievalPurpose,
    StandardConsultationTraceRecordDto,
    StandardTracePatchDto,
    StandardTraceWriteStatus,
    TodoStandardConsultationTraceSink,
    TodoStandardMedicationPolicyPort,
    TodoStandardRagPort,
)

from .helpers import (
    build_provider,
    build_request,
)


def test_todo_rag_port_returns_degraded_bundle() -> None:
    """验证 TODO RAG 端口返回显式降级证据包。

    :return: None。
    """

    provider = build_provider()
    request = build_request(provider)
    port = TodoStandardRagPort()

    result = asyncio.run(
        port.retrieve(
            request=request,
            purpose=RetrievalPurpose.STANDARD_PRESEARCH,
            query_text=request.normalized_query,
            top_k=5,
            timeout_seconds=0.1,
        )
    )

    assert result.degraded is True
    assert result.retrieval_purpose is RetrievalPurpose.STANDARD_PRESEARCH
    assert result.evidence_hints == []


def test_todo_medication_policy_blocks_care_plan() -> None:
    """验证 TODO MedicationPolicy 端口保守禁止 L4 护理建议。

    :return: None。
    """

    provider = build_provider()
    request = build_request(provider)
    port = TodoStandardMedicationPolicyPort()

    allowed = asyncio.run(
        port.allows_care_plan(
            request=request,
            contraindication_completeness=1.0,
        )
    )

    assert allowed is False


def test_todo_trace_sink_reports_degraded_write() -> None:
    """验证 TODO trace sink 返回可补偿降级状态。

    :return: None。
    """

    provider = build_provider()
    request = build_request(provider)
    agent = TodoStandardConsultationTraceSink()
    record = StandardConsultationTraceRecordDto(
        request_id=request.request_id,
        trace_id=request.trace_id,
        run_id=request.run_id,
        session_id=request.session_id,
        user_id=request.user_id,
        current_pet_id=request.current_pet_id or "pet_1",
        task_id=request.task_id,
        status=DraftStatus.NEEDS_MORE_INFO,
        trace_patch=StandardTracePatchDto(
            standard_agent_version="standard-consultation-agent.v1",
            orchestrator_version="standard-consultation-orchestrator.v1",
            layer_before=ConsultationLayer.L0_COLLECTION,
            layer_after=ConsultationLayer.L1_TRIAGE,
        ),
        selected_question_count=0,
        evidence_binding_count=0,
        params_version=request.params_version,
        config_snapshot_id=request.config_snapshot_id,
    )

    result = asyncio.run(agent.write_standard_trace(record))

    assert result.status is StandardTraceWriteStatus.DEGRADED
    assert result.retryable is True

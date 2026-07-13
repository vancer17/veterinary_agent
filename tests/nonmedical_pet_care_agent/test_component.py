##################################################################################################
# 文件: tests/nonmedical_pet_care_agent/test_component.py
# 作用: 验证 NonmedicalPetCareAgent 的保守降级、契约校验、fake 依赖完整路径、安全升级、trace 降级和图节点适配。
# 边界: 只通过生产包一级出口导入公共契约，不接入真实 LLM、RAG、Trace 存储或输出安全审查。
##################################################################################################

import asyncio

import pytest

from veterinary_agent.config import (
    NonmedicalPetCareAgentSettings,
    NonmedicalPetCareTimeoutConfig,
)
from veterinary_agent.graph_runtime import GraphState
from veterinary_agent.nonmedical_pet_care_agent import (
    NonmedicalAgentError,
    NonmedicalAgentErrorCode,
    NonmedicalDraftStatus,
    NonmedicalPetCareAgentGraphNode,
    NonmedicalTraceWriteStatus,
    create_default_nonmedical_pet_care_agent,
)
from veterinary_agent.vet_context_builder import ContextCompressionStrategy

from .helpers import (
    FakeAgentRunner,
    FakeNonmedicalRagPort,
    RecordingNonmedicalTraceSink,
    build_context_bundle,
    build_graph_context,
    build_provider,
    build_request,
    build_success_agent_outputs,
    graph_state_for_nonmedical_request,
)


def test_conservative_draft_when_domain_dependencies_are_todo() -> None:
    """验证 RAG 与 AgentRunner 未接入时返回保守结构化草稿。

    :return: None。
    """

    provider = build_provider()
    trace_sink = RecordingNonmedicalTraceSink()
    agent = create_default_nonmedical_pet_care_agent(
        runtime_config_provider=provider,
        trace_sink=trace_sink,
    )

    result = asyncio.run(agent.generate_draft(build_request(provider)))

    assert result.status is NonmedicalDraftStatus.KNOWLEDGE_DEGRADED_CONSERVATIVE
    assert result.trace_delivery_status is NonmedicalTraceWriteStatus.RECORDED
    assert {
        "advice_planner_unavailable",
        "knowledge_planner_unavailable",
        "rag_degraded",
        "rule_fallback_used",
        "nonmedical_writer_unavailable",
    }.issubset(set(result.trace_patch.degraded_flags))
    assert len(trace_sink.records) == 1


def test_disabled_runtime_config_marks_agent_not_ready() -> None:
    """验证配置关闭时组件不可执行并返回稳定错误码。

    :return: None。
    """

    provider = build_provider(
        nonmedical_pet_care_settings=NonmedicalPetCareAgentSettings(enabled=False)
    )
    agent = create_default_nonmedical_pet_care_agent(runtime_config_provider=provider)

    assert agent.is_ready() is False
    with pytest.raises(NonmedicalAgentError) as exc_info:
        asyncio.run(agent.generate_draft(build_request(provider)))

    assert exc_info.value.code is NonmedicalAgentErrorCode.NONMED_NOT_READY


def test_executor_mismatch_is_rejected() -> None:
    """验证非 nonmedical_pet_care 执行器会被稳定错误码拒绝。

    :return: None。
    """

    provider = build_provider()
    agent = create_default_nonmedical_pet_care_agent(runtime_config_provider=provider)

    with pytest.raises(NonmedicalAgentError) as exc_info:
        asyncio.run(
            agent.generate_draft(
                build_request(provider, executor_key="education"),
            )
        )

    assert exc_info.value.code is NonmedicalAgentErrorCode.NONMED_EXECUTOR_MISMATCH


def test_generation_profile_is_rejected_for_pure_nonmedical_request() -> None:
    """验证纯非医疗请求携带生成剖面时会被拒绝。

    :return: None。
    """

    provider = build_provider()
    agent = create_default_nonmedical_pet_care_agent(runtime_config_provider=provider)

    with pytest.raises(NonmedicalAgentError) as exc_info:
        asyncio.run(
            agent.generate_draft(
                build_request(provider, generation_profile="education"),
            )
        )

    assert exc_info.value.code is NonmedicalAgentErrorCode.NONMED_EXECUTOR_MISMATCH


def test_missing_current_pet_id_is_rejected() -> None:
    """验证缺少当前宠物 ID 时返回稳定错误码。

    :return: None。
    """

    provider = build_provider()
    agent = create_default_nonmedical_pet_care_agent(runtime_config_provider=provider)

    with pytest.raises(NonmedicalAgentError) as exc_info:
        asyncio.run(
            agent.generate_draft(
                build_request(provider, current_pet_id=None),
            )
        )

    assert exc_info.value.code is NonmedicalAgentErrorCode.NONMED_MISSING_CURRENT_PET_ID


def test_pet_context_mismatch_is_rejected() -> None:
    """验证请求宠物 ID 与上下文宠物 ID 不一致时会被拒绝。

    :return: None。
    """

    provider = build_provider()
    agent = create_default_nonmedical_pet_care_agent(runtime_config_provider=provider)
    context = build_context_bundle(current_pet_id="pet_2")

    with pytest.raises(NonmedicalAgentError) as exc_info:
        asyncio.run(
            agent.generate_draft(
                build_request(provider, current_pet_id="pet_1", context=context),
            )
        )

    assert exc_info.value.code is NonmedicalAgentErrorCode.NONMED_PET_CONTEXT_INVALID


def test_context_compression_mismatch_is_rejected() -> None:
    """验证非 education_light 的非医疗上下文会被拒绝。

    :return: None。
    """

    provider = build_provider()
    agent = create_default_nonmedical_pet_care_agent(runtime_config_provider=provider)
    context = build_context_bundle(
        compression_strategy=ContextCompressionStrategy.SAFETY_MINIMAL,
    )

    with pytest.raises(NonmedicalAgentError) as exc_info:
        asyncio.run(agent.generate_draft(build_request(provider, context=context)))

    assert exc_info.value.code is NonmedicalAgentErrorCode.NONMED_CONTEXT_MISSING


def test_full_nonmedical_path_with_fake_dependencies() -> None:
    """验证 fake 依赖齐备时可生成 DRAFT_READY 非医疗草稿。

    :return: None。
    """

    provider = build_provider()
    agent_runner = FakeAgentRunner(outputs=build_success_agent_outputs())
    rag_port = FakeNonmedicalRagPort()
    trace_sink = RecordingNonmedicalTraceSink()
    agent = create_default_nonmedical_pet_care_agent(
        runtime_config_provider=provider,
        agent_runner=agent_runner,
        rag_port=rag_port,
        trace_sink=trace_sink,
    )

    result = asyncio.run(agent.generate_draft(build_request(provider)))

    assert result.status is NonmedicalDraftStatus.DRAFT_READY
    assert result.rag_summary.rag_invoked is True
    assert result.advice_constraints
    assert result.trace_delivery_status is NonmedicalTraceWriteStatus.RECORDED
    assert [request.runtime_options["stage"] for request in agent_runner.requests] == [
        "advice_dimension_planner",
        "knowledge_retrieval_planner",
        "nonmedical_advice_writer",
        "safety_practicality_checker",
    ]
    assert len(rag_port.requests) == 1
    assert len(trace_sink.records) == 1


def test_rag_timeout_degrades_to_conservative_draft() -> None:
    """验证 RAG 超时时组件降级为保守草稿但不中断生成。

    :return: None。
    """

    settings = NonmedicalPetCareAgentSettings(
        timeouts=NonmedicalPetCareTimeoutConfig(rag_seconds=0.01)
    )
    provider = build_provider(nonmedical_pet_care_settings=settings)
    agent_runner = FakeAgentRunner(outputs=build_success_agent_outputs())
    rag_port = FakeNonmedicalRagPort(delay_seconds=0.05)
    agent = create_default_nonmedical_pet_care_agent(
        runtime_config_provider=provider,
        agent_runner=agent_runner,
        rag_port=rag_port,
    )

    result = asyncio.run(agent.generate_draft(build_request(provider)))

    assert result.status is NonmedicalDraftStatus.KNOWLEDGE_DEGRADED_CONSERVATIVE
    assert result.rag_summary.degraded is True
    assert "NONMEDICAL_RAG_TIMEOUT" in result.rag_summary.degraded_reasons
    assert len(rag_port.requests) == 1


def test_failed_self_check_degrades_to_conservative_status() -> None:
    """验证自检失败时组件返回保守降级状态。

    :return: None。
    """

    provider = build_provider()
    outputs = build_success_agent_outputs()
    outputs[-1] = {
        "passed": False,
        "risk_flags": ["punitive_training_detected"],
        "punitive_training_detected": True,
    }
    agent_runner = FakeAgentRunner(outputs=outputs)
    rag_port = FakeNonmedicalRagPort()
    agent = create_default_nonmedical_pet_care_agent(
        runtime_config_provider=provider,
        agent_runner=agent_runner,
        rag_port=rag_port,
    )

    result = asyncio.run(agent.generate_draft(build_request(provider)))

    assert result.status is NonmedicalDraftStatus.KNOWLEDGE_DEGRADED_CONSERVATIVE
    assert result.self_check.passed is False
    assert "punitive_training_detected" in result.self_check.risk_flags


def test_trace_exception_degrades_without_blocking_draft() -> None:
    """验证 trace 写入异常不会阻断草稿返回。

    :return: None。
    """

    provider = build_provider()
    trace_sink = RecordingNonmedicalTraceSink(exception=RuntimeError("trace down"))
    agent = create_default_nonmedical_pet_care_agent(
        runtime_config_provider=provider,
        trace_sink=trace_sink,
    )

    result = asyncio.run(agent.generate_draft(build_request(provider)))

    assert result.trace_delivery_status is NonmedicalTraceWriteStatus.DEGRADED
    assert result.status is NonmedicalDraftStatus.KNOWLEDGE_DEGRADED_CONSERVATIVE
    assert len(trace_sink.records) == 1


def test_l3_signal_builds_safety_escalation_draft() -> None:
    """验证 L3 信号误入时不输出普通非医疗建议。

    :return: None。
    """

    provider = build_provider()
    trace_sink = RecordingNonmedicalTraceSink()
    agent = create_default_nonmedical_pet_care_agent(
        runtime_config_provider=provider,
        trace_sink=trace_sink,
    )
    request = build_request(
        provider,
        signals=[
            {
                "signal_id": "sig_l3_1",
                "code": "SAF-03",
                "strength": "L3",
            }
        ],
    )

    result = asyncio.run(agent.generate_draft(request))

    assert result.status is NonmedicalDraftStatus.NEEDS_SAFETY_ESCALATION
    assert "safety_escalation_required" in result.trace_patch.degraded_flags
    assert result.rag_summary.rag_invoked is False


def test_saf_01_signal_builds_safety_escalation_draft() -> None:
    """验证 SAF-01 信号误入时会升级给安全链路处理。

    :return: None。
    """

    provider = build_provider()
    agent = create_default_nonmedical_pet_care_agent(runtime_config_provider=provider)
    request = build_request(
        provider,
        signals=[
            {
                "signal_id": "sig_saf_01",
                "code": "SAF-01",
                "strength": "L1",
            }
        ],
    )

    result = asyncio.run(agent.generate_draft(request))

    assert result.status is NonmedicalDraftStatus.NEEDS_SAFETY_ESCALATION
    assert result.rag_summary.rag_invoked is False


def test_graph_node_writes_nonmedical_state_patch() -> None:
    """验证图节点从 state 构建请求并写回非医疗状态。

    :return: None。
    """

    provider = build_provider()
    trace_sink = RecordingNonmedicalTraceSink()
    agent = create_default_nonmedical_pet_care_agent(
        runtime_config_provider=provider,
        trace_sink=trace_sink,
    )
    node = NonmedicalPetCareAgentGraphNode(agent=agent)
    state: GraphState = GraphState(graph_state_for_nonmedical_request())

    result = asyncio.run(node(state, build_graph_context(provider)))

    assert result.state_patch["nonmedical_generation_status"] == (
        NonmedicalDraftStatus.KNOWLEDGE_DEGRADED_CONSERVATIVE.value
    )
    assert result.state_patch["draft_response_ref"] == (
        "draft:trace_nonmedical_1:task_nonmedical_1"
    )
    assert result.state_patch["nonmedical_rag_invoked"] is True


def test_graph_node_writes_escalation_patch_for_safety_signal() -> None:
    """验证图节点在安全升级草稿中写回升级请求状态。

    :return: None。
    """

    provider = build_provider()
    agent = create_default_nonmedical_pet_care_agent(runtime_config_provider=provider)
    node = NonmedicalPetCareAgentGraphNode(agent=agent)
    state: GraphState = GraphState(
        graph_state_for_nonmedical_request(
            signals=[
                {
                    "signal_id": "sig_l3_graph",
                    "code": "SAF-03",
                    "strength": "L3",
                }
            ]
        )
    )

    result = asyncio.run(node(state, build_graph_context(provider)))

    assert result.state_patch["nonmedical_generation_status"] == (
        NonmedicalDraftStatus.NEEDS_SAFETY_ESCALATION.value
    )
    assert result.state_patch["nonmedical_escalation_requested"] is True
    assert result.state_patch["escalation_request"] == {
        "reason_code": "NONMED_SAFETY_ESCALATION_REQUIRED",
        "target_profile": "safety_trigger",
        "summary": "非医疗链路收到 SAF-01 或 L3 强信号，建议升级处理。",
    }

##################################################################################################
# 文件: tests/standard_consultation_agent/test_service_paths.py
# 作用: 验证 StandardConsultationAgent 的 readiness、受控子 Agent、升级和问题选择服务路径。
# 边界: 使用测试替身模拟 AgentRunner、RAG 与 MedicationPolicy，不接入真实模型、知识库或药物策略。
##################################################################################################

import asyncio

import pytest

from veterinary_agent.standard_consultation_agent import (
    ConsultationLayer,
    DraftStatus,
    StandardConsultationError,
    StandardConsultationErrorCode,
    create_default_standard_consultation_agent,
)

from .helpers import (
    AllowingMedicationPolicyPort,
    FakeAgentRunner,
    FakeStandardRagPort,
    RecordingStandardTraceSink,
    build_escalation_agent_outputs,
    build_full_context_bundle,
    build_layered_agent_outputs,
    build_provider,
    build_request,
)


def test_full_layered_path_reaches_l4_with_fake_dependencies() -> None:
    """验证 fake 依赖齐备时可按受控子 Agent 路径推进到 L4。

    :return: None。
    """

    provider = build_provider()
    agent_runner = FakeAgentRunner(outputs=build_layered_agent_outputs())
    rag_port = FakeStandardRagPort()
    medication_policy = AllowingMedicationPolicyPort()
    trace_sink = RecordingStandardTraceSink()
    agent = create_default_standard_consultation_agent(
        runtime_config_provider=provider,
        agent_runner=agent_runner,
        rag_port=rag_port,
        medication_policy_port=medication_policy,
        trace_sink=trace_sink,
    )
    request = build_request(provider, context=build_full_context_bundle())

    result = asyncio.run(agent.generate_draft(request))

    assert result.status is DraftStatus.DRAFT_READY
    assert result.reached_layer is ConsultationLayer.L4_CARE_PLAN
    assert result.selected_questions == []
    assert result.evidence_bindings[0].claim_id == "claim_1"
    assert len(agent_runner.requests) == 6
    assert len(rag_port.requests) == 1
    assert len(medication_policy.requests) == 1
    assert len(trace_sink.records) == 1


def test_triage_escalation_stops_high_level_generation() -> None:
    """验证分诊子 Agent 输出升级请求后跳过方向、鉴别和护理路径。

    :return: None。
    """

    provider = build_provider()
    agent_runner = FakeAgentRunner(outputs=build_escalation_agent_outputs())
    agent = create_default_standard_consultation_agent(
        runtime_config_provider=provider,
        agent_runner=agent_runner,
        rag_port=FakeStandardRagPort(),
        trace_sink=RecordingStandardTraceSink(),
    )

    result = asyncio.run(agent.generate_draft(build_request(provider)))

    assert result.status is DraftStatus.NEEDS_SAFETY_ESCALATION
    assert result.escalation_request is not None
    assert result.escalation_request.reason_code == "persistent_collapse"
    assert [request.runtime_options["stage"] for request in agent_runner.requests] == [
        "question_collector",
        "triage_urgency",
        "standard_draft_synthesizer",
    ]


def test_question_selection_deduplicates_known_and_asked_slots() -> None:
    """验证候选问题会过滤已知事实、已问问题并遵守问题预算。

    :return: None。
    """

    provider = build_provider()
    agent_runner = FakeAgentRunner(
        outputs=[
            {
                "candidate_questions": [
                    {
                        "question_id": "known_species",
                        "question_text": "它是什么物种？",
                        "target_fact_key": "species",
                        "risk_impact": "high",
                        "information_gain": 1.0,
                    },
                    {
                        "question_id": "asked_duration",
                        "question_text": "这个情况已经持续多久了？",
                        "target_fact_key": "symptom_duration",
                        "risk_impact": "high",
                        "information_gain": 0.9,
                    },
                    {
                        "question_id": "hydration_high",
                        "question_text": "它喝水和排尿有没有异常？",
                        "target_fact_key": "hydration",
                        "risk_impact": "high",
                        "information_gain": 0.8,
                    },
                    {
                        "question_id": "hydration_duplicate",
                        "question_text": "口腔湿润程度怎么样？",
                        "target_fact_key": "hydration",
                        "risk_impact": "medium",
                        "information_gain": 0.7,
                    },
                    {
                        "question_id": "appetite",
                        "question_text": "今天食欲怎么样？",
                        "target_fact_key": "appetite",
                        "risk_impact": "medium",
                        "information_gain": 0.6,
                    },
                ]
            },
            {"triage_summary": {"urgency": "routine"}},
            {"direction_hints": [{"direction": "gastrointestinal"}]},
            {"draft_response": "需要先补齐关键信息。"},
        ]
    )
    agent = create_default_standard_consultation_agent(
        runtime_config_provider=provider,
        agent_runner=agent_runner,
        rag_port=FakeStandardRagPort(),
        trace_sink=RecordingStandardTraceSink(),
    )
    request = build_request(
        provider,
        session_state={
            "asked_question_index": {"symptom_duration": ["这个情况已经持续多久了？"]}
        },
        question_budget={"max_questions": 2},
    )

    result = asyncio.run(agent.generate_draft(request))

    assert result.status is DraftStatus.NEEDS_MORE_INFO
    assert [question.question_id for question in result.selected_questions] == [
        "hydration_high",
        "appetite",
    ]


def test_pet_context_mismatch_is_rejected() -> None:
    """验证请求宠物 ID 与上下文宠物 ID 不一致时稳定拒绝。

    :return: None。
    """

    provider = build_provider()
    agent = create_default_standard_consultation_agent(
        runtime_config_provider=provider,
    )
    request = build_request(
        provider,
        current_pet_id="pet_other",
    )

    with pytest.raises(StandardConsultationError) as exc_info:
        asyncio.run(agent.generate_draft(request))

    assert exc_info.value.code is (
        StandardConsultationErrorCode.STANDARD_PET_CONTEXT_INVALID
    )

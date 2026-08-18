"""
=============================================================================
文件：tests/test_response_generation.py
作用：验证回复生成上下文编译链路的结构化输入、Fail Fast 契约与提示词分区。
范围：覆盖 answer 分支上下文编译、ask 分支拒绝与审计 metadata；
      不调用真实 LiteLLM、Qwen、数据库或外部 RAG 服务。
说明：测试通过包级导出访问稳定能力，不直接穿透 response_generation 内部实现文件。
=============================================================================
"""

from __future__ import annotations

import asyncio

import pytest

from vet_agent import Evidence, Settings
from vet_agent.answer_rag import (
    AnswerRagResult,
    AnswerRagRetrievalResult,
    AnswerRagStrategy,
)
from vet_agent.clinical_safety import (
    ClinicalSafetyEvaluationResult,
    ClinicalSafetyFallbackState,
    ClinicalSafetySemanticResult,
)
from vet_agent.consultation_state import ConsultationDecision, ConsultationState
from vet_agent.memory import MemoryContextSection, MemoryPromptContext
from vet_agent.repositories import KnowledgeHit
from vet_agent.response_generation import (
    ResponseGenerationContextBuilder,
    ResponseGenerationContractError,
    ResponseGenerationDependencyError,
    ResponseGenerationRequest,
    ResponseGenerationService,
)
from vet_agent.runtime import QwenClient
from vet_agent.services import PetContext
from vet_agent.task_routing import RoutedTask


def _response_generation_request(
    *,
    ready: bool = True,
    answerability_decision: str = "answer",
) -> ResponseGenerationRequest:
    """构造回复生成上下文编译测试请求。

    :param ready: 问诊状态是否允许进入回答分支。
    :param answerability_decision: 回答充分性策略动作。
    :return: 返回包含完整结构化上游结果的测试请求。
    """
    state = ConsultationState(
        chief_complaint="今天开始腹泻。",
        domain="digestive",
        phase="ready_to_answer" if ready else "collecting_info",
        slots={
            "species": "dog",
            "life_stage_or_age": "3岁",
            "weight": "12公斤",
        },
        working_facts=[
            {
                "key": "onset",
                "value": "今天",
                "status": "confirmed",
                "confidence": 0.94,
            }
        ],
        observations=[
            {
                "category": "digestive",
                "label": "腹泻",
                "value": "无血便",
                "status": "confirmed",
                "confidence": 0.92,
            }
        ],
        answerability={
            "decision": answerability_decision,
            "mode": "sufficient_semantic_evidence",
            "reason": "已具备阶段性回答所需证据。",
            "blocking_slots": [],
            "unresolved_slots": ["腹泻次数未确认"],
            "policy_payload": {"internal": "不得进入模型提示词"},
        },
        semantic_extraction={
            "strategy": "litellm_response_format",
            "confidence": 0.93,
            "source_text": "不得进入回复生成提示词",
        },
        temporal_context={
            "scope": "ongoing",
            "resolution_state": "ongoing",
            "text": "今天开始",
        },
    )
    consultation_decision = ConsultationDecision(
        state=state,
        ready=ready,
        missing_slots=[] if ready else ["onset"],
        questions=[],
        answerability=dict(state.answerability),
    )
    answer_rag_result = AnswerRagResult(
        strategy=AnswerRagStrategy.STATIC_TEST,
        retrieval=AnswerRagRetrievalResult(
            query="dog digestive diarrhea",
            hits=[
                KnowledgeHit(
                    title="犬腹泻阶段性护理",
                    summary="优先观察精神、食欲、饮水、呕吐、血便和症状持续时间。",
                    source="test_knowledge",
                    public_citation=True,
                    score=0.95,
                )
            ],
            node_count=1,
            backend="static_test",
            min_score=0.9,
            top_k=1,
        ),
    )
    return ResponseGenerationRequest(
        task=RoutedTask(
            task_id="task_1",
            task_key="digestive:1",
            text="它今天开始腹泻，没有血便，精神食欲正常。",
            domain="digestive",
            title="腹泻问诊",
            priority=1,
        ),
        pet_context=PetContext(
            verified_profile={
                "species": "dog",
                "breed": "柯基",
                "age": "3岁",
                "weight_kg": 12,
            },
            evidence=[
                Evidence(
                    source="可信宠物画像",
                    detail="测试宠物画像已加载。",
                )
            ],
        ),
        memory_context=MemoryPromptContext(
            prompt_text=(
                "已验证长期事实:\n"
                "- pet.weight: 12公斤\n\n"
                "当前会话上下文:\n"
                "- 用户: 它有点拉稀；助手摘要: 已进入补充问诊。"
            ),
            sections=(
                MemoryContextSection(
                    scope="pet",
                    authority="authoritative",
                    content="- pet.weight: 12公斤",
                    source_label="已验证长期事实",
                ),
                MemoryContextSection(
                    scope="session_shared",
                    authority="conversational",
                    content="- 助手摘要：已进入补充问诊。",
                    source_label="当前会话参考",
                ),
                MemoryContextSection(
                    scope="task",
                    authority="conversational",
                    content="- 当前任务专属记忆。",
                    source_label="当前任务记忆",
                    task_key="digestive:1",
                ),
                MemoryContextSection(
                    scope="task",
                    authority="conversational",
                    content="- 其他任务记忆不得进入。",
                    source_label="其他任务记忆",
                    task_key="skin:1",
                ),
                MemoryContextSection(
                    scope="pet",
                    authority="semantic_hint",
                    content="- 过去可能出现过类似软便。",
                    source_label="历史语义线索",
                ),
            ),
            evidence=(
                Evidence(
                    source="结构化记忆读取",
                    detail="测试记忆上下文已编译。",
                ),
            ),
            metadata={"prompt_chars": 80},
        ),
        consultation_decision=consultation_decision,
        answer_rag_result=answer_rag_result,
        clinical_safety_semantic=ClinicalSafetySemanticResult(
            species="dog",
            symptom_state="present",
            temporal_state="current",
            temporal_scope="ongoing",
            resolution_state="ongoing",
            intent_type="triage",
            risk_evidence_state="sufficient",
            confidence=0.91,
            strategy="litellm_response_format",
        ),
        clinical_safety_resolution=ClinicalSafetyEvaluationResult(
            signals=[],
            fallback_state=ClinicalSafetyFallbackState(),
            policy_decision={
                "action": "allow",
                "allow": True,
                "reason": "未命中需要阻断或升级的临床安全动作。",
                "policy_payload": {"internal": "不得进入模型提示词"},
            },
        ),
    )


def test_response_generation_context_builder_compiles_structured_sections() -> None:
    """验证回复生成上下文只编译上游结构化结果。

    :return: 无返回值；断言通过表示提示词包含稳定结构化分区与审计摘要。
    """
    builder = ResponseGenerationContextBuilder(max_prompt_chars=12_000)

    context = builder.build(_response_generation_request())

    assert "上游回答充分性已允许阶段性回答" in context.system_prompt
    assert "结构化问诊状态已足够" not in context.system_prompt
    assert "临床安全裁决" in context.user_prompt
    assert "当前任务回答充分性" in context.user_prompt
    assert "腹泻次数未确认" in context.user_prompt
    assert "服务端已验证宠物资料" in context.user_prompt
    assert "当前会话上下文" in context.user_prompt
    assert "回答相关 RAG 证据" in context.user_prompt
    assert "它今天开始腹泻，没有血便，精神食欲正常。" in context.user_prompt
    assert "当前任务专属记忆" in context.user_prompt
    assert "其他任务记忆不得进入" not in context.user_prompt
    assert "语义线索，仅作历史参考" in context.user_prompt
    assert "policy_payload" not in context.user_prompt
    assert "source_text" not in context.user_prompt
    assert "confidence" not in context.user_prompt
    assert "score" not in context.user_prompt
    assert context.metadata["consultation_ready"] is True
    assert context.metadata["answer_rag"]["retrieval"]["hit_count"] == 1


def test_response_generation_context_builder_rejects_ask_branch() -> None:
    """验证回复生成上下文编译器拒绝 ask 分支。

    :return: 无返回值；断言通过表示主链路不会绕过回答充分性裁决。
    """
    builder = ResponseGenerationContextBuilder(max_prompt_chars=12_000)
    request = _response_generation_request(
        ready=False,
        answerability_decision="ask",
    )

    with pytest.raises(ResponseGenerationContractError, match="answer decision"):
        builder.build(request)


def test_response_generation_context_builder_rejects_incomplete_answer_evidence() -> None:
    """验证回复生成上下文编译器拒绝不完整回答证据。

    :return: 无返回值；断言通过表示无效 RAG 命中不会通过占位文本进入模型。
    """
    builder = ResponseGenerationContextBuilder(max_prompt_chars=12_000)
    request = _response_generation_request()
    request.answer_rag_result.retrieval.hits[0] = KnowledgeHit(
        title="",
        summary="",
        source="",
        public_citation=False,
        score=0.5,
    )

    with pytest.raises(ResponseGenerationContractError, match="answer evidence"):
        builder.build(request)


def test_response_generation_service_fails_fast_without_model_client() -> None:
    """验证模型客户端不可用时回复生成服务不返回硬编码文本。

    :return: 无返回值；断言通过表示服务依赖失败会显式暴露。
    """
    service = ResponseGenerationService(
        qwen_client=QwenClient(Settings(litellm_api_key=None)),
        context_builder=ResponseGenerationContextBuilder(),
    )

    with pytest.raises(ResponseGenerationDependencyError, match="not ready"):
        asyncio.run(
            service.generate(
                _response_generation_request(),
                model="qwen-plus",
            )
        )

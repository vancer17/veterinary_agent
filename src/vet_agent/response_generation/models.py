"""
=============================================================================
文件：src/vet_agent/response_generation/models.py
作用：定义回复生成上下文编译链路的稳定领域模型。
范围：承载回复生成请求、编译后的提示词上下文、生成结果与可观察策略
      名称，不访问数据库、不调用模型、不执行安全裁决或问诊状态机。
说明：本文件只定义跨数据链传递的稳定结构；上下文编译依赖上游已结构化
      的临床安全、问诊状态、宠物资料、记忆与回答相关 RAG 结果。
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from vet_agent import Evidence
from vet_agent.answer_rag import AnswerRagResult
from vet_agent.clinical_safety import ClinicalSafetyEvaluationResult, ClinicalSafetySemanticResult
from vet_agent.consultation_state import ConsultationDecision
from vet_agent.memory import MemoryPromptContext
from vet_agent.services import PetContext
from vet_agent.task_routing import RoutedTask


class ResponseGenerationStrategy(StrEnum):
    """表示回复生成链路的可观察策略名称。

    :return: 无返回值；枚举值用于响应 metadata、trace 和测试断言。
    """

    QWEN_RESPONSE_GENERATION = "qwen_response_generation"


@dataclass(frozen=True)
class ResponseGenerationRequest:
    """表示回复生成阶段消费的结构化请求。

    :param task: 当前已通过任务路由准入的任务。
    :param pet_context: 服务端已验证的宠物上下文。
    :param memory_context: 已分层编译后的记忆提示词上下文。
    :param consultation_decision: 当前任务的问诊充分性与状态决策。
    :param answer_rag_result: 回答相关 RAG 生成的结构化证据上下文。
    :param clinical_safety_semantic: 临床安全结构化语义结果。
    :param clinical_safety_resolution: 临床安全裁决与显式回退结果。
    :return: 无返回值；该对象只承载回复生成所需的上游结构化输入。
    """

    task: RoutedTask
    pet_context: PetContext
    memory_context: MemoryPromptContext
    consultation_decision: ConsultationDecision
    answer_rag_result: AnswerRagResult
    clinical_safety_semantic: ClinicalSafetySemanticResult
    clinical_safety_resolution: ClinicalSafetyEvaluationResult


@dataclass(frozen=True)
class ResponseGenerationContext:
    """表示回复生成阶段编译后的提示词上下文。

    :param system_prompt: 发送给模型的系统提示词。
    :param user_prompt: 发送给模型的用户提示词。
    :param messages: OpenAI 兼容消息列表。
    :param metadata: 回复生成上下文编译审计摘要。
    :return: 无返回值；该对象只用于模型调用与审计留痕。
    """

    system_prompt: str
    user_prompt: str
    messages: tuple[dict[str, str], ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def prompt_text(self) -> str:
        """读取完整回复生成提示词文本。

        :return: 返回系统提示词与用户提示词拼接后的完整文本。
        """
        return "\n\n".join(part for part in (self.system_prompt, self.user_prompt) if part.strip())


@dataclass(frozen=True)
class ResponseGenerationResult:
    """表示回复生成服务的最终输出。

    :param text: 模型生成的原始回复文本。
    :param context: 本次生成使用的编译后提示词上下文。
    :param evidence: 进入回复生成链路的可展示证据。
    :param strategy: 回复生成策略。
    :return: 无返回值；该对象进入输出清洗与响应封存阶段。
    """

    text: str
    context: ResponseGenerationContext
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)
    strategy: ResponseGenerationStrategy = ResponseGenerationStrategy.QWEN_RESPONSE_GENERATION

    def to_metadata(self) -> dict[str, Any]:
        """转换为响应 metadata 可序列化结构。

        :return: 返回回复生成上下文与策略摘要。
        """
        return {
            "strategy": self.strategy.value,
            "context": self.context.metadata,
        }

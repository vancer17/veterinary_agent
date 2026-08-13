"""
文件：src/vet_agent/observability/agent_path.py
作用：定义 Agent 主链路审计路径节点，统一约束 metadata.multi_agent_path 的节点命名。
范围：仅承载可观察性命名契约，不参与安全裁决、临床推理或业务流程分支。
说明：策略引擎节点需带有领域语义，避免多个 OPA 裁决点在审计链路中无法区分。
"""

from __future__ import annotations

from enum import StrEnum


class AgentPathNode(StrEnum):
    """表示 Agent 主链路中的稳定审计节点名称。

    :return: 无返回值；枚举值用于 API metadata、测试断言与审计日志的统一命名契约。
    """

    INPUT_SAFETY_SERVICE = "InputSafetyService"
    INPUT_SAFETY_POLICY_OPA = "InputSafetyPolicyOPA"
    PET_CONTEXT_AGENT = "PetContextAgent"
    CLINICAL_SAFETY_SEMANTIC_EXTRACTOR_AGENT = "ClinicalSafetySemanticExtractorAgent"
    CLINICAL_SAFETY_EVALUATOR = "ClinicalSafetyEvaluator"
    CLINICAL_SAFETY_POLICY_OPA = "ClinicalSafetyPolicyOPA"
    MEMORY_AGENT = "MemoryAgent"
    TASK_ROUTER_AGENT = "TaskRouterAgent"
    CONSULTATION_SEMANTIC_EXTRACTOR_AGENT = "ConsultationSemanticExtractorAgent"
    CONSULTATION_STATE_AGENT = "ConsultationStateAgent"
    ANSWERABILITY_EVALUATOR = "AnswerabilityEvaluator"
    KNOWLEDGE_AGENT = "KnowledgeAgent"
    QUESTION_PLANNER_AGENT = "QuestionPlannerAgent"
    RAG_QUESTION_PLANNER_AGENT = "RagQuestionPlannerAgent"
    QWEN_RESPONSE_AGENT = "QwenResponseAgent"
    SAFETY_REVIEW_AGENT = "SafetyReviewAgent"


def build_agent_path(*nodes: AgentPathNode) -> list[str]:
    """构造可写入响应 metadata 的 Agent 审计路径。

    :param nodes: 按真实执行顺序排列的 Agent 审计节点。
    :return: 返回序列化后的节点名称列表。
    """
    return [node.value for node in nodes]

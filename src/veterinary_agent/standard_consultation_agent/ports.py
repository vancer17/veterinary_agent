##################################################################################################
# 文件: src/veterinary_agent/standard_consultation_agent/ports.py
# 作用: 定义 StandardConsultationAgent 使用的 RAG 与 MedicationPolicy 端口及 TODO 空壳。
# 边界: 只声明跨领域依赖契约和显式降级行为，不实现知识库检索、药物策略或医学规则库。
##################################################################################################

from typing import Protocol

from veterinary_agent.standard_consultation_agent.dto import (
    RagEvidenceBundleDto,
    StandardConsultationRequestDto,
)
from veterinary_agent.standard_consultation_agent.enums import RetrievalPurpose

TODO_STANDARD_RAG_ERROR_CODE = "STANDARD_RAG_PORT_NOT_IMPLEMENTED"
TODO_MEDICATION_POLICY_ERROR_CODE = "STANDARD_MEDICATION_POLICY_NOT_IMPLEMENTED"


class StandardRagPort(Protocol):
    """标准问诊阶段式 RAG 检索端口。"""

    async def retrieve(
        self,
        *,
        request: StandardConsultationRequestDto,
        purpose: RetrievalPurpose,
        query_text: str,
        top_k: int,
        timeout_seconds: float,
    ) -> RagEvidenceBundleDto:
        """按用途执行一次受控 RAG 检索。

        :param request: 当前标准问诊请求。
        :param purpose: 本次检索用途。
        :param query_text: 本次检索查询文本。
        :param top_k: 最大返回条数。
        :param timeout_seconds: 本次检索超时秒数。
        :return: 标准化 RAG 证据包。
        """

        ...


class StandardMedicationPolicyPort(Protocol):
    """标准问诊生成前用药策略端口。"""

    async def allows_care_plan(
        self,
        *,
        request: StandardConsultationRequestDto,
        contraindication_completeness: float,
    ) -> bool:
        """判断本轮是否允许进入 L4 护理或处置建议。

        :param request: 当前标准问诊请求。
        :param contraindication_completeness: 当前禁忌信息完整度。
        :return: 若策略允许进入 L4 则返回 True。
        """

        ...


class TodoStandardRagPort:
    """RagPlatform 尚未接入时使用的显式 TODO RAG 空壳。"""

    async def retrieve(
        self,
        *,
        request: StandardConsultationRequestDto,
        purpose: RetrievalPurpose,
        query_text: str,
        top_k: int,
        timeout_seconds: float,
    ) -> RagEvidenceBundleDto:
        """返回 RAG 尚未接入的降级证据包。

        :param request: 当前标准问诊请求；TODO 空壳不读取业务正文。
        :param purpose: 本次检索用途。
        :param query_text: 本次检索查询文本；TODO 空壳不执行检索。
        :param top_k: 最大返回条数；TODO 空壳不使用该值。
        :param timeout_seconds: 检索超时秒数；TODO 空壳不使用该值。
        :return: 标记 degraded 的空证据包。
        """

        del request, query_text, top_k, timeout_seconds
        return RagEvidenceBundleDto(
            retrieval_purpose=purpose,
            degraded=True,
            degraded_reason=TODO_STANDARD_RAG_ERROR_CODE,
        )


class TodoStandardMedicationPolicyPort:
    """MedicationPolicy 尚未接入时使用的显式 TODO 保守空壳。"""

    async def allows_care_plan(
        self,
        *,
        request: StandardConsultationRequestDto,
        contraindication_completeness: float,
    ) -> bool:
        """返回不允许进入 L4 个案护理建议的保守结果。

        :param request: 当前标准问诊请求；TODO 空壳不读取业务正文。
        :param contraindication_completeness: 当前禁忌信息完整度。
        :return: 固定返回 False，避免药物策略缺失时输出高阶建议。
        """

        del request, contraindication_completeness
        return False


__all__: tuple[str, ...] = (
    "TODO_MEDICATION_POLICY_ERROR_CODE",
    "TODO_STANDARD_RAG_ERROR_CODE",
    "StandardMedicationPolicyPort",
    "StandardRagPort",
    "TodoStandardMedicationPolicyPort",
    "TodoStandardRagPort",
)

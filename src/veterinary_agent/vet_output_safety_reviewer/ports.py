##################################################################################################
# 文件: src/veterinary_agent/vet_output_safety_reviewer/ports.py
# 作用: 定义 VetOutputSafetyReviewer 对用药策略端口与 TODO 空壳的应用内契约。
# 边界: 不实现输出安全审查、不调用模型、不读取或写入 trace，仅声明跨域依赖。
##################################################################################################

from typing import Protocol

from veterinary_agent.vet_output_safety_reviewer.dto import (
    MedicationPolicyAnalysisRequestDto,
    MedicationPolicyDecisionDto,
)
from veterinary_agent.vet_output_safety_reviewer.enums import (
    MedicationPolicyDecisionStatus,
)

TODO_MEDICATION_POLICY_ERROR_CODE = "OUTPUT_REVIEW_MEDICATION_POLICY_NOT_IMPLEMENTED"


class MedicationPolicyPort(Protocol):
    """输出安全审查使用的用药策略端口。"""

    def is_ready(self) -> bool:
        """判断用药策略端口是否可用。

        :return: 若端口可执行策略判定则返回 True。
        """

        ...

    async def analyze_medication_expression(
        self,
        request: MedicationPolicyAnalysisRequestDto,
    ) -> MedicationPolicyDecisionDto:
        """分析候选文本中的用药表达。

        :param request: 用药表达分析请求。
        :return: 标准化用药策略判定结果。
        """

        ...


class TodoMedicationPolicyPort:
    """MedicationPolicy 尚未接入时使用的显式 TODO 空壳。"""

    def is_ready(self) -> bool:
        """判断 TODO 用药策略端口是否可用。

        :return: 固定返回 False。
        """

        return False

    async def analyze_medication_expression(
        self,
        request: MedicationPolicyAnalysisRequestDto,
    ) -> MedicationPolicyDecisionDto:
        """返回用药策略未接入的显式降级结果。

        :param request: 用药表达分析请求；TODO 空壳不会解析正文。
        :return: 标记不可用的用药策略判定结果。
        """

        return MedicationPolicyDecisionDto(
            status=MedicationPolicyDecisionStatus.UNAVAILABLE,
            action="unavailable",
            policy_version=None,
            findings=[],
            rewrite_hints=[],
            fallback_required=True,
            degraded_flags=[TODO_MEDICATION_POLICY_ERROR_CODE],
            trace_patch={
                "request_id": request.request_id,
                "trace_id": request.trace_id,
                "text_source": request.text_source,
            },
        )


__all__: tuple[str, ...] = (
    "MedicationPolicyPort",
    "TODO_MEDICATION_POLICY_ERROR_CODE",
    "TodoMedicationPolicyPort",
)

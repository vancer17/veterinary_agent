##################################################################################################
# 文件: src/veterinary_agent/nonmedical_pet_care_agent/contract.py
# 作用: 定义 NonmedicalPetCareAgent 应用内服务接口，供图节点、组合根和测试替身依赖。
# 边界: 仅声明服务契约，不实现非医疗建议生成、不读取配置、不调用模型或 RAG。
##################################################################################################

from typing import Protocol

from veterinary_agent.nonmedical_pet_care_agent.dto import (
    NonmedicalAdviceDraftDto,
    NonmedicalAdviceRequestDto,
)


class NonmedicalPetCareAgent(Protocol):
    """NonmedicalPetCareAgent 应用内服务接口。"""

    def is_ready(self) -> bool:
        """判断非医疗养宠建议服务是否具备执行条件。

        :return: 若 RuntimeConfig 可读取且 NonmedicalPetCareAgent 已启用则返回 True。
        """

        ...

    async def generate_draft(
        self,
        request: NonmedicalAdviceRequestDto,
    ) -> NonmedicalAdviceDraftDto:
        """生成非医疗养宠建议结构化草稿。

        :param request: 当前非医疗建议生成请求。
        :return: 待输出安全审查的非医疗建议草稿。
        :raises NonmedicalAgentError: 当前置契约或强依赖不满足执行条件时抛出。
        """

        ...


__all__: tuple[str, ...] = ("NonmedicalPetCareAgent",)

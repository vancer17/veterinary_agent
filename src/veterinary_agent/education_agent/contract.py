##################################################################################################
# 文件: src/veterinary_agent/education_agent/contract.py
# 作用: 定义 EducationAgent 应用内服务接口，供图节点、组合根和测试替身依赖。
# 边界: 仅声明服务契约，不实现科普生成、不读取配置、不调用模型或 RAG。
##################################################################################################

from typing import Protocol

from veterinary_agent.education_agent.dto import (
    EducationDraftDto,
    EducationGenerationRequestDto,
)


class EducationAgent(Protocol):
    """EducationAgent 应用内服务接口。"""

    def is_ready(self) -> bool:
        """判断科普服务是否具备执行条件。

        :return: 若 RuntimeConfig 可读取且 EducationAgent 已启用则返回 True。
        """

        ...

    async def generate_draft(
        self,
        request: EducationGenerationRequestDto,
    ) -> EducationDraftDto:
        """生成科普结构化草稿。

        :param request: 当前科普生成请求。
        :return: 待输出安全审查的科普草稿。
        :raises EducationAgentError: 当前置契约或强依赖不满足执行条件时抛出。
        """

        ...


__all__: tuple[str, ...] = ("EducationAgent",)

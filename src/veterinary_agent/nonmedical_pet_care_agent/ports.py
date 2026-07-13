##################################################################################################
# 文件: src/veterinary_agent/nonmedical_pet_care_agent/ports.py
# 作用: 定义 NonmedicalPetCareAgent 使用的 RAG 端口及 TODO 降级空壳。
# 边界: 只声明跨领域依赖契约和显式降级行为，不实现知识库检索或来源策略管理。
##################################################################################################

from typing import Protocol

from veterinary_agent.nonmedical_pet_care_agent.dto import (
    NonmedicalAdviceRequestDto,
    NonmedicalRagResultDto,
    RetrievalFacetDto,
)

TODO_NONMEDICAL_RAG_ERROR_CODE = "NONMEDICAL_RAG_PORT_NOT_IMPLEMENTED"


class NonmedicalPetCareRagPort(Protocol):
    """非医疗养宠受控 RAG 检索端口。"""

    async def retrieve(
        self,
        *,
        request: NonmedicalAdviceRequestDto,
        facet: RetrievalFacetDto,
        timeout_seconds: float,
    ) -> NonmedicalRagResultDto:
        """按检索 facet 执行一次受控 RAG 检索。

        :param request: 当前非医疗建议生成请求。
        :param facet: 本次检索的受控 facet。
        :param timeout_seconds: 本次检索超时秒数。
        :return: 标准化非医疗 RAG 检索结果。
        """

        ...


class TodoNonmedicalPetCareRagPort:
    """RagPlatform 尚未接入时使用的显式 TODO RAG 空壳。"""

    async def retrieve(
        self,
        *,
        request: NonmedicalAdviceRequestDto,
        facet: RetrievalFacetDto,
        timeout_seconds: float,
    ) -> NonmedicalRagResultDto:
        """返回 RAG 尚未接入的降级检索结果。

        :param request: 当前非医疗建议生成请求；TODO 空壳不读取业务正文。
        :param facet: 本次检索的受控 facet。
        :param timeout_seconds: 本次检索超时秒数；TODO 空壳不使用该值。
        :return: 标记 degraded 的空检索结果。
        """

        del request, timeout_seconds
        return NonmedicalRagResultDto(
            retrieval_purpose=facet.retrieval_purpose,
            dimension_code=facet.dimension_code,
            query_hashes=list(facet.query_hashes),
            degraded=True,
            degraded_reason=TODO_NONMEDICAL_RAG_ERROR_CODE,
        )


__all__: tuple[str, ...] = (
    "NonmedicalPetCareRagPort",
    "TODO_NONMEDICAL_RAG_ERROR_CODE",
    "TodoNonmedicalPetCareRagPort",
)

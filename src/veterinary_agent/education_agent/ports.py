##################################################################################################
# 文件: src/veterinary_agent/education_agent/ports.py
# 作用: 定义 EducationAgent 使用的 RAG 端口及 TODO 降级空壳。
# 边界: 只声明跨领域依赖契约和显式降级行为，不实现知识库检索或来源策略管理。
##################################################################################################

from typing import Protocol

from veterinary_agent.education_agent.dto import (
    EducationGenerationRequestDto,
    EducationRagResultDto,
    RetrievalFacetDto,
)

TODO_EDUCATION_RAG_ERROR_CODE = "EDUCATION_RAG_PORT_NOT_IMPLEMENTED"


class EducationRagPort(Protocol):
    """科普受控 RAG 检索端口。"""

    async def retrieve(
        self,
        *,
        request: EducationGenerationRequestDto,
        facet: RetrievalFacetDto,
        timeout_seconds: float,
    ) -> EducationRagResultDto:
        """按检索 facet 执行一次受控 RAG 检索。

        :param request: 当前科普生成请求。
        :param facet: 本次检索的受控 facet。
        :param timeout_seconds: 本次检索超时秒数。
        :return: 标准化科普 RAG 检索结果。
        """

        ...


class TodoEducationRagPort:
    """RagPlatform 尚未接入时使用的显式 TODO RAG 空壳。"""

    async def retrieve(
        self,
        *,
        request: EducationGenerationRequestDto,
        facet: RetrievalFacetDto,
        timeout_seconds: float,
    ) -> EducationRagResultDto:
        """返回 RAG 尚未接入的降级检索结果。

        :param request: 当前科普生成请求；TODO 空壳不读取业务正文。
        :param facet: 本次检索的受控 facet。
        :param timeout_seconds: 本次检索超时秒数；TODO 空壳不使用该值。
        :return: 标记 degraded 的空检索结果。
        """

        del request, timeout_seconds
        return EducationRagResultDto(
            retrieval_purpose=facet.retrieval_purpose,
            dimension_code=facet.dimension_code,
            query_hashes=list(facet.query_hashes),
            degraded=True,
            degraded_reason=TODO_EDUCATION_RAG_ERROR_CODE,
        )


__all__: tuple[str, ...] = (
    "EducationRagPort",
    "TODO_EDUCATION_RAG_ERROR_CODE",
    "TodoEducationRagPort",
)

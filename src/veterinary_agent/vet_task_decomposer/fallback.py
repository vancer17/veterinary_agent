##################################################################################################
# 文件: src/veterinary_agent/vet_task_decomposer/fallback.py
# 作用: 定义 VetTaskDecomposer 本地 span fallback 端口和依赖未接入时的 TODO 空壳。
# 边界: 不加载真实预训练权重、不实现业务训练模型、不覆盖单任务原文透传兜底。
##################################################################################################

from typing import Protocol

from veterinary_agent.vet_task_decomposer.dto import (
    LocalFallbackResultDto,
    VetTaskDecomposeRequestDto,
)
from veterinary_agent.vet_task_decomposer.enums import VetTaskDecomposerErrorCode

TODO_LOCAL_FALLBACK_ERROR_CODE = "VET_TASK_LOCAL_FALLBACK_NOT_IMPLEMENTED"


class VetTaskLocalFallback(Protocol):
    """VetTaskDecomposer 本地 span fallback 端口。"""

    def is_ready(self) -> bool:
        """判断本地 fallback 是否具备执行条件。

        :return: 若本地模型或占位能力已加载并可推理则返回 True。
        """

        ...

    async def decompose(
        self,
        request: VetTaskDecomposeRequestDto,
    ) -> LocalFallbackResultDto:
        """执行本地 fallback 任务拆解。

        :param request: 本轮任务拆解请求。
        :return: 本地 fallback 候选结果；不可用时返回 available=False。
        """

        ...


class TodoVetTaskLocalFallback:
    """本地预训练 span fallback 尚未接入时使用的显式 TODO 空壳。"""

    def is_ready(self) -> bool:
        """判断 TODO fallback 是否就绪。

        :return: 固定返回 False。
        """

        return False

    async def decompose(
        self,
        request: VetTaskDecomposeRequestDto,
    ) -> LocalFallbackResultDto:
        """返回本地 fallback 尚未接入的降级结果。

        :param request: 本轮任务拆解请求；TODO 空壳不消费用户正文。
        :return: 标记本地 fallback 不可用的结果。
        """

        del request
        return LocalFallbackResultDto(
            available=False,
            error_code=(
                VetTaskDecomposerErrorCode.TASK_DECOMPOSE_LOCAL_FALLBACK_UNAVAILABLE
            ).value,
            detail=TODO_LOCAL_FALLBACK_ERROR_CODE,
        )


__all__: tuple[str, ...] = (
    "TODO_LOCAL_FALLBACK_ERROR_CODE",
    "TodoVetTaskLocalFallback",
    "VetTaskLocalFallback",
)

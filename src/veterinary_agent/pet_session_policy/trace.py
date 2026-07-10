##################################################################################################
# 文件: src/veterinary_agent/pet_session_policy/trace.py
# 作用: 定义 PetSessionPolicy 逻辑链写入协议，并提供 LogicTraceStore 尚未实现时的 TODO 空壳。
# 边界: 不实现 LogicTraceStore 持久化、不访问数据库；TODO 空壳仅显式返回 trace 降级状态。
##################################################################################################

from typing import Protocol

from veterinary_agent.pet_session_policy.dto import (
    PetSessionTraceRecordDto,
    PetSessionTraceWriteResultDto,
)
from veterinary_agent.pet_session_policy.enums import PetSessionTraceWriteStatus

TODO_TRACE_ERROR_CODE = "PET_SESSION_LOGIC_TRACE_STORE_NOT_IMPLEMENTED"


class PetSessionTraceSink(Protocol):
    """宠物会话策略逻辑链摘要写入协议。"""

    async def write_decision(
        self,
        record: PetSessionTraceRecordDto,
    ) -> PetSessionTraceWriteResultDto:
        """写入宠物会话策略判定摘要。

        :param record: 待写入的宠物会话策略 trace 摘要。
        :return: trace 摘要写入结果。
        """

        ...


class TodoPetSessionTraceSink:
    """LogicTraceStore 尚未接入时使用的 PetSessionPolicy TODO trace 空壳。"""

    async def write_decision(
        self,
        record: PetSessionTraceRecordDto,
    ) -> PetSessionTraceWriteResultDto:
        """返回 LogicTraceStore 尚未接入的显式降级结果。

        :param record: 待写入的宠物会话策略 trace 摘要；TODO 空壳不持久化该记录。
        :return: 表示 trace 写入已降级的结果 DTO。
        """

        del record
        return PetSessionTraceWriteResultDto(
            status=PetSessionTraceWriteStatus.DEGRADED,
            error_code=TODO_TRACE_ERROR_CODE,
            retryable=True,
            detail="LogicTraceStore 领域依赖尚未接入",
        )


__all__: tuple[str, ...] = (
    "TODO_TRACE_ERROR_CODE",
    "PetSessionTraceSink",
    "TodoPetSessionTraceSink",
)

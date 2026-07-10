##################################################################################################
# 文件: src/veterinary_agent/llm_gateway/trace.py
# 作用: 提供 LogicTraceStore 尚未接入时的模型调用摘要 TODO 空壳。
# 边界: 不持久化模型调用摘要、不实现业务逻辑链 schema；真实实现应通过 LlmCallTraceStore 端口注入。
##################################################################################################

from veterinary_agent.llm_gateway.dto import (
    LlmCallSummaryDto,
    LlmTraceWriteResultDto,
)
from veterinary_agent.llm_gateway.enums import LlmTraceWriteStatus


class TodoLlmCallTraceStore:
    """LogicTraceStore 尚未接入时使用的模型调用摘要 TODO 空壳。"""

    def is_ready(self) -> bool:
        """判断 TODO 模型调用摘要存储是否就绪。

        :return: 固定返回 False，表示真实 LogicTraceStore 尚未接入。
        """

        return False

    async def write_summary(
        self,
        summary: LlmCallSummaryDto,
    ) -> LlmTraceWriteResultDto:
        """返回模型调用摘要写入降级状态。

        :param summary: 本次脱敏模型调用摘要。
        :return: 标记 LogicTraceStore 尚未接入的降级结果。
        """

        del summary
        return LlmTraceWriteResultDto(
            status=LlmTraceWriteStatus.DEGRADED,
            reason="LogicTraceStore 模型调用摘要端口尚未接入",
        )


__all__: tuple[str, ...] = ("TodoLlmCallTraceStore",)

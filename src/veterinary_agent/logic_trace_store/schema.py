##################################################################################################
# 文件: src/veterinary_agent/logic_trace_store/schema.py
# 作用: 定义 LogicTraceStore 依赖的 trace patch 适配协议，并提供 VetTraceSchema 尚未接入时的 TODO 空壳。
# 边界: 不实现兽医业务 schema 规则、不执行自然语言理解，仅返回显式的降级或透传结果。
##################################################################################################

from typing import Protocol

from veterinary_agent.logic_trace_store.dto import (
    AppendTraceEventCommandDto,
    LogicTraceSchemaValidationResultDto,
)


class LogicTraceSchemaValidator(Protocol):
    """LogicTraceStore 业务 patch 校验协议。"""

    async def validate_trace_event(
        self,
        command: AppendTraceEventCommandDto,
    ) -> LogicTraceSchemaValidationResultDto:
        """校验逻辑链事件或业务 patch。

        :param command: 待校验的逻辑链事件命令。
        :return: 逻辑链事件校验结果。
        """

        ...


class TodoLogicTraceSchemaValidator:
    """VetTraceSchema 尚未接入时使用的显式 TODO 空壳。"""

    def is_ready(self) -> bool:
        """判断 TODO schema 校验器是否就绪。

        :return: 固定返回 False，表示真实 VetTraceSchema 尚未接入。
        """

        return False

    async def validate_trace_event(
        self,
        command: AppendTraceEventCommandDto,
    ) -> LogicTraceSchemaValidationResultDto:
        """返回显式的透传校验结果。

        :param command: 待校验的逻辑链事件命令；TODO 空壳不修改其业务负载。
        :return: 标记 schema 适配尚未接入的校验结果。
        """

        return LogicTraceSchemaValidationResultDto(
            valid=True,
            degraded_flags=["vet_trace_schema_not_connected"],
            normalized_business_payload=dict(command.business_payload),
            schema_ref=command.schema_ref,
            errors=[],
            warnings=[],
        )


__all__: tuple[str, ...] = (
    "LogicTraceSchemaValidator",
    "TodoLogicTraceSchemaValidator",
)

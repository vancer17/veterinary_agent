##################################################################################################
# 文件: src/veterinary_agent/guardrail_framework/trace.py
# 作用: 定义 GuardrailFramework trace sink 的 LogicTraceStore 适配器与 TODO 空壳重导出。
# 边界: 只转换并写入护栏阶段脱敏摘要，不保存完整正文、不定义 VetTraceSchema 投影。
##################################################################################################

from datetime import UTC, datetime

from veterinary_agent.guardrail_framework.dto import (
    GuardrailTraceRecordDto,
    GuardrailTraceWriteResultDto,
)
from veterinary_agent.guardrail_framework.enums import GuardrailTraceWriteStatus
from veterinary_agent.guardrail_framework.ports import (
    TODO_GUARDRAIL_TRACE_ERROR_CODE,
    TodoGuardrailTraceSink,
)
from veterinary_agent.logic_trace_store import (
    AppendTraceEventCommandDto,
    LogicTraceStore,
    LogicTraceWriteStatus,
)


class LogicTraceGuardrailTraceSink:
    """基于通用 LogicTraceStore 的 GuardrailFramework trace 适配器。"""

    def __init__(self, *, store: LogicTraceStore) -> None:
        """初始化 LogicTraceStore 护栏 trace 适配器。

        :param store: 通用 LogicTraceStore 公共契约。
        :return: None。
        """

        self._store = store

    async def write_guardrail_trace(
        self,
        record: GuardrailTraceRecordDto,
    ) -> GuardrailTraceWriteResultDto:
        """转换并写入一次护栏阶段 trace 摘要。

        :param record: GuardrailFramework 产生的护栏阶段记录。
        :return: GuardrailFramework 可消费的 trace 写入结果。
        """

        request = record.request
        result = record.result
        event_id = (
            f"{request.context.trace_id}:guardrail:{request.stage.value}:"
            f"{request.context.task_id}:{request.context.segment_id or 'task'}"
        )
        write_result = await self._store.append_trace_event(
            AppendTraceEventCommandDto(
                request_id=request.context.request_id,
                trace_id=request.context.trace_id,
                event_id=event_id,
                event_type=f"guardrail.{request.stage.value}.completed",
                source_component="GuardrailFramework",
                created_at=datetime.now(UTC),
                task_id=request.context.task_id,
                segment_id=request.context.segment_id,
                input_hash=None,
                output_hash=None,
                summary={
                    "run_id": request.context.run_id,
                    "session_id": request.context.session_id,
                    "user_id": request.context.user_id,
                    "pet_id": request.context.pet_id,
                    "generation_profile": request.context.generation_profile,
                    "params_version": request.context.params_version,
                    "status": result.status.value,
                    "publish_allowed": result.publish_allowed,
                    "fallback_triggered": result.fallback_triggered,
                    "fallback_template_version": result.fallback_template_version,
                    "degraded_mode": result.degraded_mode,
                    "error_code": (
                        result.error_code.value
                        if result.error_code is not None
                        else None
                    ),
                    "duration_ms": record.duration_ms,
                    "candidate_text_ref": request.candidate_text_ref,
                    "reviewed_text_ref": result.reviewed_text_ref,
                    "final_text_ref": result.final_text_ref,
                },
                business_payload={
                    "policies": [
                        {
                            "policy_id": policy.policy_id,
                            "policy_version": policy.policy_version,
                            "handler_ref": policy.handler_ref,
                            "stage": policy.stage.value,
                        }
                        for policy in record.policies
                    ],
                    "findings": [
                        finding.model_dump(mode="json") for finding in result.findings
                    ],
                    "actions": [
                        action.model_dump(mode="json") for action in result.actions
                    ],
                },
                schema_ref="guardrail.trace.v1",
            )
        )
        if write_result.status is LogicTraceWriteStatus.WRITTEN:
            status = GuardrailTraceWriteStatus.RECORDED
        elif write_result.status is LogicTraceWriteStatus.SKIPPED:
            status = GuardrailTraceWriteStatus.SKIPPED
        else:
            status = GuardrailTraceWriteStatus.DEGRADED
        return GuardrailTraceWriteResultDto(
            status=status,
            error_code=write_result.error_code,
            retryable=write_result.retryable,
            detail=write_result.detail,
        )


__all__: tuple[str, ...] = (
    "LogicTraceGuardrailTraceSink",
    "TODO_GUARDRAIL_TRACE_ERROR_CODE",
    "TodoGuardrailTraceSink",
)

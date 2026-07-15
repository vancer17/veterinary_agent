##################################################################################################
# 文件: src/veterinary_agent/vet_output_safety_reviewer/trace.py
# 作用: 定义 VetOutputSafetyReviewer trace 端口、LogicTraceStore 适配器与 TODO 空壳。
# 边界: 只转换并写入脱敏输出审查摘要，不保存完整正文、不执行逻辑链 schema 投影。
##################################################################################################

from datetime import UTC, datetime
from typing import Protocol

from veterinary_agent.logic_trace_store import (
    AppendTraceEventCommandDto,
    LogicTraceStore,
    LogicTraceWriteStatus,
)
from veterinary_agent.vet_output_safety_reviewer.dto import (
    OutputReviewTraceRecordDto,
    OutputReviewTraceWriteResultDto,
)
from veterinary_agent.vet_output_safety_reviewer.enums import (
    OutputReviewTraceWriteStatus,
)

TODO_OUTPUT_REVIEW_TRACE_ERROR_CODE = "OUTPUT_REVIEW_TRACE_STORE_NOT_IMPLEMENTED"


class VetOutputSafetyReviewerTraceSink(Protocol):
    """输出安全审查脱敏 trace 写入端口。"""

    async def write_output_review_trace(
        self,
        record: OutputReviewTraceRecordDto,
    ) -> OutputReviewTraceWriteResultDto:
        """写入一次输出安全审查脱敏 trace 记录。

        :param record: 待写入的输出审查摘要。
        :return: trace 写入结果。
        """

        ...


class LogicTraceVetOutputSafetyReviewerTraceSink:
    """基于通用 LogicTraceStore 的输出审查 trace 适配器。"""

    def __init__(self, *, store: LogicTraceStore) -> None:
        """初始化 LogicTraceStore 输出审查 trace 适配器。

        :param store: 通用 LogicTraceStore 公共契约。
        :return: None。
        """

        self._store = store

    async def write_output_review_trace(
        self,
        record: OutputReviewTraceRecordDto,
    ) -> OutputReviewTraceWriteResultDto:
        """转换并写入一次输出安全审查 trace 摘要。

        :param record: VetOutputSafetyReviewer 产生的脱敏 trace 摘要。
        :return: VetOutputSafetyReviewer 可消费的 trace 写入结果。
        """

        request = record.request
        result = record.result
        event_id = (
            f"{request.trace_id}:output_review:{request.task_id}:{request.segment_id}"
        )
        write_result = await self._store.append_trace_event(
            AppendTraceEventCommandDto(
                request_id=request.request_id,
                trace_id=request.trace_id,
                event_id=event_id,
                event_type="output_review",
                source_component="VetOutputSafetyReviewer",
                created_at=datetime.now(UTC),
                task_id=request.task_id,
                segment_id=request.segment_id,
                input_hash=None,
                output_hash=None,
                summary={
                    "run_id": request.run_id,
                    "session_id": request.session_id,
                    "user_id": request.user_id,
                    "pet_id": request.current_pet_id,
                    "task_id": request.task_id,
                    "segment_id": request.segment_id,
                    "generation_profile": request.generation_profile,
                    "executor_key": request.executor_key,
                    "status": result.status.value,
                    "review_confidence": result.review_confidence,
                    "fallback_recommended": result.fallback_recommended,
                    "degraded_flags": list(result.degraded_flags),
                    "trace_delivery_status": result.trace_delivery_status.value,
                    "params_version": request.params_version,
                    "config_snapshot_id": request.config_snapshot_id,
                    "reviewed_draft_ref": result.reviewed_draft_ref,
                    "draft_response_ref": request.draft_response_ref,
                },
                business_payload={
                    "patch_type": "output_review",
                    "schema_version": "vet.output-review.trace.v1",
                    "payload": {
                        "reviewed_draft_ref": result.reviewed_draft_ref,
                        "status": result.status.value,
                        "fallback_recommended": result.fallback_recommended,
                        "review_confidence": result.review_confidence,
                        "trace_patch": result.trace_patch.model_dump(mode="json"),
                        "findings": [
                            finding.model_dump(mode="json")
                            for finding in result.findings
                        ],
                        "guard_actions": [
                            action.model_dump(mode="json")
                            for action in result.guard_actions
                        ],
                        "rewrite_plan": result.rewrite_plan.model_dump(mode="json"),
                        "medication_decision": (
                            result.medication_decision.model_dump(mode="json")
                            if result.medication_decision is not None
                            else None
                        ),
                    },
                },
                schema_ref="vet.output-review.trace.v1",
            )
        )
        if write_result.status is LogicTraceWriteStatus.WRITTEN:
            status = OutputReviewTraceWriteStatus.RECORDED
        elif write_result.status is LogicTraceWriteStatus.SKIPPED:
            status = OutputReviewTraceWriteStatus.SKIPPED
        else:
            status = OutputReviewTraceWriteStatus.DEGRADED
        return OutputReviewTraceWriteResultDto(
            status=status,
            error_code=write_result.error_code,
            retryable=write_result.retryable,
            detail=write_result.detail,
        )


class TodoVetOutputSafetyReviewerTraceSink:
    """LogicTraceStore 尚未接入时使用的显式 TODO trace 空壳。"""

    async def write_output_review_trace(
        self,
        record: OutputReviewTraceRecordDto,
    ) -> OutputReviewTraceWriteResultDto:
        """返回 LogicTraceStore 尚未接入的降级结果。

        :param record: 待写入的输出审查摘要；TODO 空壳不会持久化该记录。
        :return: 标记 trace 写入已降级的结果。
        """

        del record
        return OutputReviewTraceWriteResultDto(
            status=OutputReviewTraceWriteStatus.DEGRADED,
            error_code=TODO_OUTPUT_REVIEW_TRACE_ERROR_CODE,
            retryable=True,
            detail="VetOutputSafetyReviewer LogicTraceStore 尚未接入",
        )


__all__: tuple[str, ...] = (
    "LogicTraceVetOutputSafetyReviewerTraceSink",
    "TODO_OUTPUT_REVIEW_TRACE_ERROR_CODE",
    "TodoVetOutputSafetyReviewerTraceSink",
    "VetOutputSafetyReviewerTraceSink",
)

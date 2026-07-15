##################################################################################################
# 文件: tests/vet_output_safety_reviewer/helpers.py
# 作用: 提供 VetOutputSafetyReviewer 组件测试使用的配置、请求、策略和 trace 替身构造器。
# 边界: 只通过公共包出口组装测试数据，不实现输出安全审查逻辑、不连接真实持久化或外部服务。
##################################################################################################

from typing import cast

from veterinary_agent.config import (
    GuardrailFrameworkSettings,
    GuardrailFrameworkStageSettings,
    RuntimeConfigProvider,
    create_runtime_config_provider,
)
from veterinary_agent.guardrail_framework import (
    GuardrailPolicyDto,
    GuardrailRunContextDto,
    GuardrailRunRequestDto,
    GuardrailStage,
    build_default_guardrail_policy_registry,
)
from veterinary_agent.logic_trace_store import (
    AppendTraceEventCommandDto,
    FinalizeTraceCommandDto,
    LogicTraceStore,
    LogicTraceWriteResultDto,
    LogicTraceWriteStatus,
    RecordCallSummaryCommandDto,
    RecordTraceArtifactCommandDto,
    StartTraceCommandDto,
)
from veterinary_agent.vet_output_safety_reviewer import (
    OutputReviewTraceRecordDto,
    OutputReviewTraceWriteResultDto,
    OutputReviewTraceWriteStatus,
    OutputSafetyReviewRequestDto,
    ReviewInputContextDto,
    VetOutputSafetyReviewer,
    VetOutputSafetyReviewerTraceSink,
    create_default_vet_output_safety_reviewer,
)


def build_guardrail_settings() -> GuardrailFrameworkSettings:
    """构建输出审查组件测试使用的 GuardrailFramework 配置。

    :return: 启用生成后审查阶段的 GuardrailFramework 配置。
    """

    return GuardrailFrameworkSettings(
        post_generation_review=GuardrailFrameworkStageSettings(
            policy_id="guardrail.post_generation_review.output_review.test",
            policy_version="guardrail-policy.output-review.test",
            handler_ref="vet_output_safety_reviewer_guardrail_handler",
            stage_timeout_seconds=12.0,
            handler_timeout_seconds=10.0,
            max_attempts=1,
            retry_on_timeout=False,
            retry_on_handler_error=False,
            failure_strategy="fail_closed_block",
            fallback_template_ref=None,
        )
    )


def build_provider() -> RuntimeConfigProvider:
    """构建 VetOutputSafetyReviewer 测试使用的 RuntimeConfig provider。

    :return: 仅启用 GuardrailFramework 的测试 provider。
    """

    return create_runtime_config_provider(
        guardrail_framework_settings=build_guardrail_settings()
    )


def build_post_generation_policy(provider: RuntimeConfigProvider) -> GuardrailPolicyDto:
    """读取生成后审查阶段的默认护栏策略。

    :param provider: RuntimeConfig provider。
    :return: 生成后审查阶段护栏策略。
    """

    return build_default_guardrail_policy_registry(
        provider.current_snapshot().guardrail_framework
    ).resolve_policies(stage=GuardrailStage.POST_GENERATION_REVIEW)[0]


def build_review_input_context(
    *,
    signal_codes: list[str] | None = None,
    medical_content_expected: bool | None = None,
    ocr_confirmed: bool | None = None,
    executor_key: str = "executor-test",
) -> ReviewInputContextDto:
    """构建输出安全审查上下文。

    :param signal_codes: 可选安全信号码。
    :param medical_content_expected: 可选医学内容预期标记。
    :param ocr_confirmed: 可选 OCR 确认标记。
    :param executor_key: 用于补充上下文的执行器标识。
    :return: 已校验的审查上下文。
    """

    medical_content_value = (
        False if medical_content_expected is None else medical_content_expected
    )
    return ReviewInputContextDto.model_validate(
        {
            "assessment_summary": {"executor_key": executor_key},
            "signal_codes": signal_codes or [],
            "rag_summary": {},
            "evidence_bindings": [],
            "lab_analytes": [],
            "medication_spans": [],
            "content_plan_ref": "content-plan-ref-test",
            "context_summary_ref": "context-summary-ref-test",
            "medical_content_expected": medical_content_value,
            "ocr_confirmed": ocr_confirmed,
            "metadata": {},
        }
    )


def build_output_review_request(
    *,
    provider: RuntimeConfigProvider,
    draft_text: str,
    draft_response_ref: str = "draft-ref-test",
    generation_profile: str = "standard",
    executor_key: str = "executor-test",
    task_id: str = "task-test",
    segment_id: str = "segment-test",
    signal_codes: list[str] | None = None,
    medical_content_expected: bool | None = None,
    ocr_confirmed: bool | None = None,
) -> OutputSafetyReviewRequestDto:
    """构建输出安全审查请求。

    :param provider: RuntimeConfig provider。
    :param draft_text: 待审查草稿正文。
    :param draft_response_ref: 草稿引用。
    :param generation_profile: 生成剖面。
    :param executor_key: 审查执行器标识。
    :param task_id: 子任务 ID。
    :param segment_id: segment ID。
    :param signal_codes: 可选安全信号码。
    :param medical_content_expected: 可选医学内容预期标记。
    :param ocr_confirmed: 可选 OCR 确认标记。
    :return: 输出安全审查请求。
    """

    snapshot = provider.current_snapshot()
    medical_content_value = (
        medical_content_expected
        if medical_content_expected is not None
        else generation_profile.lower() in {"standard", "safety_trigger", "education"}
    )
    return OutputSafetyReviewRequestDto(
        request_id="request-review-test",
        trace_id="trace-review-test",
        run_id="run-review-test",
        session_id="session-review-test",
        user_id="user-review-test",
        current_pet_id="pet-review-test",
        task_id=task_id,
        segment_id=segment_id,
        generation_profile=generation_profile,
        executor_key=executor_key,
        draft_response_ref=draft_response_ref,
        draft_response_text=draft_text,
        input_context=build_review_input_context(
            signal_codes=signal_codes,
            medical_content_expected=medical_content_value,
            ocr_confirmed=ocr_confirmed,
            executor_key=executor_key,
        ),
        params_version=snapshot.params_version,
        config_snapshot_id=snapshot.config_snapshot_id,
    )


def build_guardrail_request(
    *,
    provider: RuntimeConfigProvider,
    draft_text: str,
    draft_response_ref: str = "draft-ref-test",
    generation_profile: str = "standard",
    executor_key: str = "executor-test",
    task_id: str = "task-test",
    segment_id: str = "segment-test",
    signal_codes: list[str] | None = None,
    medical_content_expected: bool | None = None,
    ocr_confirmed: bool | None = None,
) -> GuardrailRunRequestDto:
    """构建生成后审查阶段的 Guardrail 请求。

    :param provider: RuntimeConfig provider。
    :param draft_text: 待审查草稿正文。
    :param draft_response_ref: 草稿引用。
    :param generation_profile: 生成剖面。
    :param executor_key: 审查执行器标识。
    :param task_id: 子任务 ID。
    :param segment_id: segment ID。
    :param signal_codes: 可选安全信号码。
    :param medical_content_expected: 可选医学内容预期标记。
    :param ocr_confirmed: 可选 OCR 确认标记。
    :return: 生成后审查阶段的 Guardrail 请求。
    """

    snapshot = provider.current_snapshot()
    medical_content_value = (
        medical_content_expected
        if medical_content_expected is not None
        else generation_profile.lower() in {"standard", "safety_trigger", "education"}
    )
    return GuardrailRunRequestDto(
        stage=GuardrailStage.POST_GENERATION_REVIEW,
        context=GuardrailRunContextDto(
            run_id="run-review-test",
            trace_id="trace-review-test",
            request_id="request-review-test",
            session_id="session-review-test",
            user_id="user-review-test",
            pet_id="pet-review-test",
            task_id=task_id,
            segment_id=segment_id,
            generation_profile=generation_profile,
            params_version=snapshot.params_version,
            config_snapshot_id=snapshot.config_snapshot_id,
        ),
        candidate_text_ref=draft_response_ref,
        task_input={
            "draft_response": draft_text,
            "draft_response_ref": draft_response_ref,
            "executor_key": executor_key,
            "input_context": {
                "signal_codes": signal_codes or [],
                "medical_content_expected": medical_content_value,
                "ocr_confirmed": ocr_confirmed,
                "executor_key": executor_key,
            },
            "medical_content_expected": medical_content_value,
        },
        runtime_metadata={"component": "vet_output_safety_reviewer_test"},
    )


def build_reviewer(
    *,
    provider: RuntimeConfigProvider,
    trace_sink: VetOutputSafetyReviewerTraceSink | None = None,
) -> VetOutputSafetyReviewer:
    """构建 VetOutputSafetyReviewer 默认服务实例。

    :param provider: RuntimeConfig provider。
    :param trace_sink: 可选输出审查 trace sink 替身。
    :return: VetOutputSafetyReviewer 服务实例。
    """

    return create_default_vet_output_safety_reviewer(
        runtime_config_provider=provider,
        trace_sink=trace_sink,
    )


def build_logic_trace_sink_store(
    store: "RecordingLogicTraceStore",
) -> LogicTraceStore:
    """将测试 LogicTraceStore 替身收窄为公共端口类型。

    :param store: 记录型 LogicTraceStore 测试替身。
    :return: 可传入输出审查 trace sink 的 LogicTraceStore 端口。
    """

    return cast(LogicTraceStore, store)


class RecordingOutputReviewTraceSink:
    """记录输出审查 trace 写入请求的测试 sink。"""

    def __init__(
        self,
        *,
        status: OutputReviewTraceWriteStatus = OutputReviewTraceWriteStatus.RECORDED,
    ) -> None:
        """初始化测试 trace sink。

        :param status: 每次写入返回的 trace 状态。
        :return: None。
        """

        self.status = status
        self.records: list[OutputReviewTraceRecordDto] = []

    async def write_output_review_trace(
        self,
        record: OutputReviewTraceRecordDto,
    ) -> OutputReviewTraceWriteResultDto:
        """记录输出审查 trace 并返回预设状态。

        :param record: 待记录的输出审查摘要。
        :return: 预设 trace 写入结果。
        """

        self.records.append(record)
        return OutputReviewTraceWriteResultDto(
            status=self.status,
            error_code=(
                "OUTPUT_REVIEW_TRACE_TEST_DEGRADED"
                if self.status is OutputReviewTraceWriteStatus.DEGRADED
                else None
            ),
            retryable=self.status is OutputReviewTraceWriteStatus.DEGRADED,
            detail="测试 trace 写入结果"
            if self.status is OutputReviewTraceWriteStatus.DEGRADED
            else None,
        )


class RecordingLogicTraceStore:
    """记录 LogicTraceStore append_trace_event 调用的测试 store。"""

    def __init__(
        self,
        *,
        status: LogicTraceWriteStatus = LogicTraceWriteStatus.WRITTEN,
    ) -> None:
        """初始化测试 LogicTraceStore。

        :param status: append_trace_event 返回的写入状态。
        :return: None。
        """

        self.status = status
        self.events: list[AppendTraceEventCommandDto] = []

    def is_ready(self) -> bool:
        """判断测试 LogicTraceStore 是否就绪。

        :return: 固定返回 True。
        """

        return True

    async def close(self) -> None:
        """关闭测试 LogicTraceStore。

        :return: None。
        """

        return None

    async def start_trace(
        self,
        command: StartTraceCommandDto,
    ) -> LogicTraceWriteResultDto:
        """记录启动 trace 请求的 TODO 降级结果。

        :param command: 启动 trace 命令；测试替身不持久化该命令。
        :return: 预设 trace 写入结果。
        """

        del command
        return LogicTraceWriteResultDto(status=self.status)

    async def append_trace_event(
        self,
        command: AppendTraceEventCommandDto,
    ) -> LogicTraceWriteResultDto:
        """记录 trace event 并返回预设状态。

        :param command: 追加 trace event 命令。
        :return: 预设 trace 写入结果。
        """

        self.events.append(command)
        return LogicTraceWriteResultDto(status=self.status)

    async def record_call_summary(
        self,
        command: RecordCallSummaryCommandDto,
    ) -> LogicTraceWriteResultDto:
        """记录调用摘要请求的 TODO 降级结果。

        :param command: 调用摘要命令；测试替身不持久化该命令。
        :return: 预设 trace 写入结果。
        """

        del command
        return LogicTraceWriteResultDto(status=self.status)

    async def record_trace_artifact(
        self,
        command: RecordTraceArtifactCommandDto,
    ) -> LogicTraceWriteResultDto:
        """记录 trace artifact 请求的 TODO 降级结果。

        :param command: trace artifact 命令；测试替身不持久化该命令。
        :return: 预设 trace 写入结果。
        """

        del command
        return LogicTraceWriteResultDto(status=self.status)

    async def finalize_trace(
        self,
        command: FinalizeTraceCommandDto,
    ) -> LogicTraceWriteResultDto:
        """记录完成 trace 请求的 TODO 降级结果。

        :param command: 完成 trace 命令；测试替身不持久化该命令。
        :return: 预设 trace 写入结果。
        """

        del command
        return LogicTraceWriteResultDto(status=self.status)


__all__: tuple[str, ...] = (
    "RecordingLogicTraceStore",
    "RecordingOutputReviewTraceSink",
    "build_guardrail_request",
    "build_guardrail_settings",
    "build_logic_trace_sink_store",
    "build_output_review_request",
    "build_post_generation_policy",
    "build_provider",
    "build_review_input_context",
    "build_reviewer",
)

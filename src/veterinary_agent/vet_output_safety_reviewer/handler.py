##################################################################################################
# 文件: src/veterinary_agent/vet_output_safety_reviewer/handler.py
# 作用: 提供 VetOutputSafetyReviewer 到 GuardrailFramework 的 handler 适配器。
# 边界: 只做 DTO 与 Guardrail 结果转换，不实现输出安全审查逻辑、不直接发布正文。
##################################################################################################

from collections.abc import Mapping

from pydantic import ValidationError

from veterinary_agent.guardrail_framework import (
    GuardActionDto,
    GuardActionType,
    GuardrailFindingDto,
    GuardrailFindingSeverity,
    GuardrailFrameworkErrorCode,
    GuardrailHandler,
    GuardrailPolicyDto,
    GuardrailRunRequestDto,
    GuardrailRunResultDto,
    GuardrailStage,
    GuardrailStatus,
)
from veterinary_agent.vet_output_safety_reviewer.dto import (
    OutputSafetyReviewRequestDto,
    ReviewInputContextDto,
)
from veterinary_agent.vet_output_safety_reviewer.enums import (
    ReviewStatus,
    VetOutputSafetyReviewerErrorCode,
    VetOutputSafetyReviewerOperation,
)
from veterinary_agent.vet_output_safety_reviewer.errors import (
    VetOutputSafetyReviewerError,
)
from veterinary_agent.vet_output_safety_reviewer.service import (
    VetOutputSafetyReviewer,
)


def _as_mapping(value: object) -> Mapping[str, object] | None:
    """将未知值安全读取为字符串键映射。

    :param value: 需要读取的未知值。
    :return: 字符串键映射；无法读取时返回 None。
    """

    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


def _as_list(value: object) -> list[object]:
    """将未知值安全读取为列表。

    :param value: 需要读取的未知值。
    :return: 若输入为列表或元组则返回普通列表，否则返回空列表。
    """

    if isinstance(value, list | tuple):
        return list(value)
    return []


def _read_string(value: object) -> str | None:
    """从未知值中读取非空字符串。

    :param value: 需要读取的未知值。
    :return: 去除首尾空白后的非空字符串；无法读取时返回 None。
    """

    if isinstance(value, str):
        stripped_value = value.strip()
        if stripped_value:
            return stripped_value
    return None


def _read_bool(value: object) -> bool | None:
    """从未知值中读取布尔值。

    :param value: 需要读取的未知值。
    :return: 若输入为布尔值则返回对应结果，否则返回 None。
    """

    if isinstance(value, bool):
        return value
    return None


def _read_first_string(*values: object) -> str | None:
    """按顺序从多个值中读取首个非空字符串。

    :param values: 按优先级排列的候选值。
    :return: 首个可用字符串；若都不可用则返回 None。
    """

    for value in values:
        text = _read_string(value)
        if text is not None:
            return text
    return None


def _read_first_bool(*values: object) -> bool | None:
    """按顺序从多个值中读取首个布尔值。

    :param values: 按优先级排列的候选值。
    :return: 首个可用布尔值；若都不可用则返回 None。
    """

    for value in values:
        flag = _read_bool(value)
        if flag is not None:
            return flag
    return None


def _strings_from_unknown_list(value: object) -> list[str]:
    """将未知值安全读取为字符串列表。

    :param value: 需要读取的未知值。
    :return: 过滤空白后的字符串列表。
    """

    strings: list[str] = []
    for item in _as_list(value):
        text = _read_string(item)
        if text is not None:
            strings.append(text)
    return strings


def _mapping_list_from_unknown_list(value: object) -> list[dict[str, object]]:
    """将未知值安全读取为字典列表。

    :param value: 需要读取的未知值。
    :return: 过滤后的字典列表。
    """

    mappings: list[dict[str, object]] = []
    for item in _as_list(value):
        item_mapping = _as_mapping(item)
        if item_mapping is not None:
            mappings.append(dict(item_mapping))
    return mappings


def _review_action_type_to_guard_action_type(
    action_type: object,
) -> GuardActionType:
    """将输出审查动作类型映射为 GuardrailFramework 动作类型。

    :param action_type: 输出审查动作类型。
    :return: GuardrailFramework 统一动作类型。
    """

    action_name = str(action_type)
    if action_name == "ALLOW":
        return GuardActionType.ALLOW
    if action_name == "BLOCK_RECOMMENDED":
        return GuardActionType.BLOCK
    if action_name == "FALLBACK_RECOMMENDED":
        return GuardActionType.FALLBACK
    if action_name == "REMOVE_SPAN":
        return GuardActionType.REWRITE
    if action_name == "REMOVE_UNSUPPORTED_CLAIM":
        return GuardActionType.REWRITE
    if action_name == "PREPEND_URGENT_CARE":
        return GuardActionType.REWRITE
    if action_name == "APPEND_DISCLAIMER":
        return GuardActionType.REWRITE
    return GuardActionType.REWRITE


def _review_status_to_guardrail_status(status: ReviewStatus) -> GuardrailStatus:
    """将输出审查状态映射为 GuardrailFramework 状态。

    :param status: 输出审查状态。
    :return: GuardrailFramework 统一状态。
    """

    if status is ReviewStatus.REVIEWED_READY:
        return GuardrailStatus.ALLOWED
    if status is ReviewStatus.REVIEWED_WITH_REWRITE:
        return GuardrailStatus.REWRITTEN
    if status is ReviewStatus.FALLBACK_RECOMMENDED:
        return GuardrailStatus.DEGRADED
    if status is ReviewStatus.BLOCK_RECOMMENDED:
        return GuardrailStatus.BLOCKED
    if status is ReviewStatus.DEGRADED_REVIEW:
        return GuardrailStatus.DEGRADED
    return GuardrailStatus.FAILED


def _segment_id_from_request(request: GuardrailRunRequestDto) -> str:
    """从护栏请求中读取稳定 segment 标识。

    :param request: 当前护栏运行请求。
    :return: 稳定 segment 标识。
    """

    return request.context.segment_id or request.context.task_id


def _short_stable_id(
    *,
    prefix: str,
    values: tuple[str, ...],
    max_length: int,
) -> str:
    """生成稳定且长度受控的标识。

    :param prefix: 业务前缀。
    :param values: 参与 hash 的稳定字段值。
    :param max_length: 最大允许长度。
    :return: 稳定短标识。
    """

    from hashlib import sha256

    digest = sha256("|".join(values).encode("utf-8")).hexdigest()[:16]
    candidate = f"{prefix}:{digest}"
    return candidate[:max_length]


class VetOutputSafetyReviewerGuardrailHandler:
    """将 VetOutputSafetyReviewer 服务接入 GuardrailFramework 的适配器。"""

    def __init__(
        self,
        *,
        reviewer: VetOutputSafetyReviewer,
        handler_ref: str = "vet_output_safety_reviewer_guardrail_handler",
    ) -> None:
        """初始化输出安全审查 Guardrail handler。

        :param reviewer: VetOutputSafetyReviewer 公共服务契约。
        :param handler_ref: 绑定到 GuardrailFramework 的稳定 handler 引用。
        :return: None。
        :raises ValueError: 当 handler_ref 为空时抛出。
        """

        if not handler_ref.strip():
            raise ValueError("handler_ref 不得为空")
        self._reviewer = reviewer
        self._handler_ref = handler_ref.strip()

    async def run_guardrail(
        self,
        *,
        policy: GuardrailPolicyDto,
        request: GuardrailRunRequestDto,
    ) -> GuardrailRunResultDto:
        """执行一次输出安全审查护栏。

        :param policy: 当前执行的护栏策略。
        :param request: 当前护栏运行请求。
        :return: GuardrailFramework 标准化护栏结果。
        """

        if request.stage is not GuardrailStage.POST_GENERATION_REVIEW:
            return self._failed_result(
                policy=policy,
                request=request,
                error_code=GuardrailFrameworkErrorCode.GUARDRAIL_STAGE_MISMATCH,
                reason_code="OUTPUT_REVIEW_STAGE_MISMATCH",
                message="输出安全审查 handler 仅接受 post_generation_review 阶段",
            )
        try:
            review_request = self._build_review_request(request=request)
            review_result = await self._reviewer.review_draft_response_safety(
                review_request
            )
        except VetOutputSafetyReviewerError as exc:
            return self._failed_result(
                policy=policy,
                request=request,
                error_code=GuardrailFrameworkErrorCode.GUARDRAIL_INTERNAL_ERROR,
                reason_code=exc.code.value,
                message=exc.error.message,
                conflict_with=exc.to_dto().model_dump(mode="json"),
            )
        except ValidationError as exc:
            return self._failed_result(
                policy=policy,
                request=request,
                error_code=GuardrailFrameworkErrorCode.GUARDRAIL_OUTPUT_SCHEMA_INVALID,
                reason_code=VetOutputSafetyReviewerErrorCode.OUTPUT_REVIEW_SCHEMA_INVALID.value,
                message="VetOutputSafetyReviewer 输出结构不符合契约",
                conflict_with={"validation_error_count": len(exc.errors())},
            )
        except Exception as exc:
            return self._failed_result(
                policy=policy,
                request=request,
                error_code=GuardrailFrameworkErrorCode.GUARDRAIL_INTERNAL_ERROR,
                reason_code=VetOutputSafetyReviewerErrorCode.OUTPUT_REVIEW_INTERNAL_ERROR.value,
                message="VetOutputSafetyReviewer 执行过程中发生未映射异常",
                conflict_with={"exception_type": type(exc).__name__},
            )
        guardrail_status = _review_status_to_guardrail_status(review_result.status)
        findings = [
            GuardrailFindingDto(
                finding_id=finding.finding_id,
                category=finding.source_review_domain.value,
                severity=GuardrailFindingSeverity(finding.severity.value),
                reason_code=finding.reason_code,
                evidence_ref=finding.evidence_ref,
                source_handler=self._handler_ref,
                metadata=dict(finding.metadata),
            )
            for finding in review_result.findings
        ]
        actions = [
            GuardActionDto(
                action_id=action.action_id,
                stage=GuardrailStage.POST_GENERATION_REVIEW,
                action_type=_review_action_type_to_guard_action_type(
                    action.action_type
                ),
                reason_code=action.reason_code,
                handler_ref=self._handler_ref,
                before_ref=action.before_ref,
                after_ref=action.after_ref,
                policy_id=policy.policy_id,
                policy_version=policy.policy_version,
                metadata=dict(action.metadata),
            )
            for action in review_result.guard_actions
        ]
        return GuardrailRunResultDto(
            status=guardrail_status,
            reviewed_text_ref=review_result.reviewed_draft_ref,
            final_text_ref=None,
            publish_allowed=False,
            fallback_triggered=False,
            fallback_template_version=None,
            findings=findings,
            actions=actions,
            degraded_mode=(
                review_result.status.value
                if review_result.status
                in {
                    ReviewStatus.DEGRADED_REVIEW,
                    ReviewStatus.FALLBACK_RECOMMENDED,
                }
                else None
            ),
            error_code=None,
            trace_degraded=review_result.trace_delivery_status.value != "recorded",
            metadata={
                "review_status": review_result.status.value,
                "fallback_recommended": review_result.fallback_recommended,
                "review_confidence": review_result.review_confidence,
                "degraded_flags": list(review_result.degraded_flags),
                "trace_delivery_status": review_result.trace_delivery_status.value,
            },
        )

    def _build_review_request(
        self,
        *,
        request: GuardrailRunRequestDto,
    ) -> OutputSafetyReviewRequestDto:
        """从 Guardrail 请求构建输出安全审查请求。

        :param request: 当前护栏运行请求。
        :return: 输出安全审查请求。
        :raises VetOutputSafetyReviewerError: 当草稿、引用或执行器标识缺失时抛出。
        """

        task_input = _as_mapping(request.task_input) or {}
        raw_input_context = _as_mapping(task_input.get("input_context")) or {}
        draft_text = _read_first_string(
            task_input.get("draft_response"),
            task_input.get("draft_response_text"),
        )
        draft_response_ref = _read_first_string(
            task_input.get("draft_response_ref"),
            request.candidate_text_ref,
        )
        generation_profile = _read_string(request.context.generation_profile)
        executor_key = _read_first_string(
            raw_input_context.get("executor_key"),
            task_input.get("executor_key"),
        )
        if draft_text is None:
            raise VetOutputSafetyReviewerError(
                code=VetOutputSafetyReviewerErrorCode.OUTPUT_REVIEW_DRAFT_MISSING,
                operation=VetOutputSafetyReviewerOperation.REVIEW_DRAFT_RESPONSE_SAFETY,
                message="Guardrail 请求缺少草稿正文",
                retryable=False,
                request_id=request.context.request_id,
                trace_id=request.context.trace_id,
                task_id=request.context.task_id,
                segment_id=_segment_id_from_request(request),
            )
        if draft_response_ref is None:
            raise VetOutputSafetyReviewerError(
                code=VetOutputSafetyReviewerErrorCode.OUTPUT_REVIEW_DRAFT_MISSING,
                operation=VetOutputSafetyReviewerOperation.REVIEW_DRAFT_RESPONSE_SAFETY,
                message="Guardrail 请求缺少草稿引用",
                retryable=False,
                request_id=request.context.request_id,
                trace_id=request.context.trace_id,
                task_id=request.context.task_id,
                segment_id=_segment_id_from_request(request),
            )
        if generation_profile is None:
            raise VetOutputSafetyReviewerError(
                code=VetOutputSafetyReviewerErrorCode.OUTPUT_REVIEW_PROFILE_MISSING,
                operation=VetOutputSafetyReviewerOperation.REVIEW_DRAFT_RESPONSE_SAFETY,
                message="Guardrail 请求缺少生成剖面",
                retryable=False,
                request_id=request.context.request_id,
                trace_id=request.context.trace_id,
                task_id=request.context.task_id,
                segment_id=_segment_id_from_request(request),
            )
        if executor_key is None:
            raise VetOutputSafetyReviewerError(
                code=VetOutputSafetyReviewerErrorCode.OUTPUT_REVIEW_ASSESSMENT_MISSING,
                operation=VetOutputSafetyReviewerOperation.REVIEW_DRAFT_RESPONSE_SAFETY,
                message="Guardrail 请求缺少 executor_key",
                retryable=False,
                request_id=request.context.request_id,
                trace_id=request.context.trace_id,
                task_id=request.context.task_id,
                segment_id=_segment_id_from_request(request),
            )
        medical_content_expected = _read_first_bool(
            raw_input_context.get("medical_content_expected"),
            task_input.get("medical_content_expected"),
        )
        if medical_content_expected is None:
            medical_content_expected = generation_profile.lower() in {
                "education",
                "safety_trigger",
                "standard",
            }
        input_context = ReviewInputContextDto.model_validate(
            {
                "assessment_summary": _as_mapping(
                    raw_input_context.get("assessment_summary")
                )
                or _as_mapping(task_input.get("assessment_summary"))
                or {},
                "signal_codes": _strings_from_unknown_list(
                    raw_input_context.get("signal_codes")
                )
                or _strings_from_unknown_list(task_input.get("signal_codes")),
                "rag_summary": _as_mapping(raw_input_context.get("rag_summary"))
                or _as_mapping(task_input.get("rag_summary"))
                or {},
                "evidence_bindings": _mapping_list_from_unknown_list(
                    raw_input_context.get("evidence_bindings")
                )
                or _mapping_list_from_unknown_list(task_input.get("evidence_bindings")),
                "lab_analytes": _mapping_list_from_unknown_list(
                    raw_input_context.get("lab_analytes")
                )
                or _mapping_list_from_unknown_list(task_input.get("lab_analytes")),
                "medication_spans": _mapping_list_from_unknown_list(
                    raw_input_context.get("medication_spans")
                )
                or _mapping_list_from_unknown_list(task_input.get("medication_spans")),
                "content_plan_ref": _read_first_string(
                    raw_input_context.get("content_plan_ref"),
                    task_input.get("content_plan_ref"),
                ),
                "context_summary_ref": _read_first_string(
                    raw_input_context.get("context_summary_ref"),
                    task_input.get("context_summary_ref"),
                ),
                "medical_content_expected": medical_content_expected,
                "ocr_confirmed": _read_first_bool(
                    raw_input_context.get("ocr_confirmed"),
                    task_input.get("ocr_confirmed"),
                ),
                "metadata": _as_mapping(raw_input_context.get("metadata"))
                or _as_mapping(task_input.get("metadata"))
                or {},
            }
        )
        return OutputSafetyReviewRequestDto(
            request_id=request.context.request_id,
            trace_id=request.context.trace_id,
            run_id=request.context.run_id,
            session_id=request.context.session_id,
            user_id=request.context.user_id,
            current_pet_id=request.context.pet_id,
            task_id=request.context.task_id,
            segment_id=_segment_id_from_request(request),
            generation_profile=generation_profile,
            executor_key=executor_key,
            draft_response_ref=draft_response_ref,
            draft_response_text=draft_text,
            input_context=input_context,
            params_version=request.context.params_version,
            config_snapshot_id=request.context.config_snapshot_id,
        )

    def _failed_result(
        self,
        *,
        policy: GuardrailPolicyDto,
        request: GuardrailRunRequestDto,
        error_code: GuardrailFrameworkErrorCode,
        reason_code: str,
        message: str,
        conflict_with: Mapping[str, object] | None = None,
    ) -> GuardrailRunResultDto:
        """构建 GuardrailFramework 失败结果。

        :param policy: 当前执行的护栏策略。
        :param request: 当前护栏运行请求。
        :param error_code: 框架级错误码。
        :param reason_code: 标准原因码。
        :param message: 失败说明。
        :param conflict_with: 可选冲突摘要。
        :return: 标记失败的护栏结果。
        """

        return GuardrailRunResultDto(
            status=GuardrailStatus.FAILED,
            findings=[
                GuardrailFindingDto(
                    finding_id=_short_stable_id(
                        prefix="output-review-failure",
                        values=(
                            request.context.run_id,
                            policy.policy_id,
                            reason_code,
                        ),
                        max_length=128,
                    ),
                    category="vet_output_safety_reviewer",
                    severity=GuardrailFindingSeverity.HIGH,
                    reason_code=reason_code,
                    evidence_ref=policy.handler_ref,
                    source_handler=self._handler_ref,
                    metadata={
                        "detail": message,
                        "conflict_with": dict(conflict_with or {}),
                    },
                )
            ],
            actions=[
                GuardActionDto(
                    action_id=_short_stable_id(
                        prefix="output-review-block",
                        values=(
                            request.context.run_id,
                            policy.policy_id,
                            reason_code,
                        ),
                        max_length=160,
                    ),
                    stage=GuardrailStage.POST_GENERATION_REVIEW,
                    action_type=GuardActionType.BLOCK,
                    reason_code=reason_code,
                    handler_ref=self._handler_ref,
                    before_ref=request.candidate_text_ref,
                    after_ref=None,
                    policy_id=policy.policy_id,
                    policy_version=policy.policy_version,
                    metadata={
                        "detail": message,
                        "conflict_with": dict(conflict_with or {}),
                    },
                )
            ],
            error_code=error_code,
            metadata={
                "policy_id": policy.policy_id,
                "policy_version": policy.policy_version,
                "handler_ref": policy.handler_ref,
                "detail": message,
                "conflict_with": dict(conflict_with or {}),
            },
        )


def create_vet_output_safety_reviewer_guardrail_handler(
    *,
    reviewer: VetOutputSafetyReviewer,
    handler_ref: str = "vet_output_safety_reviewer_guardrail_handler",
) -> GuardrailHandler:
    """创建默认输出安全审查 Guardrail handler。

    :param reviewer: VetOutputSafetyReviewer 公共服务契约。
    :param handler_ref: 绑定到 GuardrailFramework 的稳定 handler 引用。
    :return: 输出安全审查 Guardrail handler。
    """

    return VetOutputSafetyReviewerGuardrailHandler(
        reviewer=reviewer,
        handler_ref=handler_ref,
    )


__all__: tuple[str, ...] = (
    "VetOutputSafetyReviewerGuardrailHandler",
    "create_vet_output_safety_reviewer_guardrail_handler",
)

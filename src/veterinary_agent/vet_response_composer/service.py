##################################################################################################
# 文件: src/veterinary_agent/vet_response_composer/service.py
# 作用: 实现 VetResponseComposer 默认应用内服务，完成分支读取、发布资格检查、排序、落库与 trace。
# 边界: 不调用 LLM、不生成医疗正文、不执行输出安全审查，只消费上游结构化可发布结果。
##################################################################################################

import asyncio
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, TypeVar

from pydantic import ValidationError

from veterinary_agent.checkpoint_store import (
    CheckpointStore,
    CheckpointStoreError,
    LoadLatestCheckpointQueryDto,
    MarkSegmentPublishedCommandDto,
)
from veterinary_agent.config import RuntimeConfigError, RuntimeConfigProvider
from veterinary_agent.config import VetResponseComposerSettings
from veterinary_agent.conversation_store import (
    AppendAssistantSegmentCommandDto,
    ConversationStore,
    ConversationStoreError,
    CreateAssistantMessageCommandDto,
    FinalizeAssistantMessageCommandDto,
)
from veterinary_agent.vet_response_composer.dto import (
    BranchExecutionStateDto,
    ComposerTracePatchDto,
    ComposerTraceRecordDto,
    ComposerTraceWriteResultDto,
    ComposeTurnRequestDto,
    ComposeTurnResultDto,
    JsonMap,
    PublishableSegmentDto,
    ResponseSegmentDto,
    TurnCompositionStateDto,
)
from veterinary_agent.vet_response_composer.enums import (
    ComposerBranchType,
    ComposerGuardStatus,
    ComposerPublishStatus,
    ComposerTraceWriteStatus,
    VetResponseComposerErrorCode,
    VetResponseComposerOperation,
)
from veterinary_agent.vet_response_composer.errors import VetResponseComposerError
from veterinary_agent.vet_response_composer.trace import (
    TodoVetResponseComposerTraceSink,
    VetResponseComposerTraceSink,
)

_T = TypeVar("_T")

_BRANCH_STATE_KEYS: tuple[str, ...] = (
    "branch_execution_states",
    "branches",
    "triggered_branches",
)
_UNSAFE_SOURCE_STAGES: frozenset[str] = frozenset(
    {"draft", "draft_response", "reviewed_draft", "reviewed_draft_response"}
)


@dataclass(frozen=True, slots=True)
class _PublishCandidate:
    """Composer 内部使用的候选发布对象。"""

    branch: BranchExecutionStateDto
    segment: PublishableSegmentDto
    original_index: int


class VetResponseComposer(Protocol):
    """VetResponseComposer 应用内服务契约。"""

    def is_ready(self) -> bool:
        """判断 Composer 是否具备执行条件。

        :return: 若 RuntimeConfig 与强依赖均已装配则返回 True。
        """

        ...

    async def compose_turn_response(
        self,
        request: ComposeTurnRequestDto,
    ) -> ComposeTurnResultDto:
        """合成本轮用户可见回复并完成分段发布。

        :param request: 回复合成与发布请求。
        :return: 回复合成与发布结果。
        """

        ...


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
    :return: 若值为 list 或 tuple 则返回列表，否则返回空列表。
    """

    if isinstance(value, list | tuple):
        return list(value)
    return []


def _read_optional_string(value: object) -> str | None:
    """从未知值中读取非空字符串。

    :param value: 需要读取的未知值。
    :return: 去除首尾空白后的非空字符串；无法读取时返回 None。
    """

    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _read_bool(value: object, *, default: bool = False) -> bool:
    """从未知值中读取布尔值。

    :param value: 需要读取的未知值。
    :param default: 无法读取时返回的默认值。
    :return: 解析后的布尔值。
    """

    if isinstance(value, bool):
        return value
    return default


def _now_utc() -> datetime:
    """读取当前 UTC 时间。

    :return: 当前 UTC 时间。
    """

    return datetime.now(UTC)


async def _with_timeout(
    awaitable: Awaitable[_T],
    *,
    timeout_seconds: float,
) -> _T:
    """在指定超时预算内等待异步操作完成。

    :param awaitable: 需要等待的异步操作。
    :param timeout_seconds: 超时秒数。
    :return: 异步操作返回值。
    :raises TimeoutError: 当操作超过超时预算时抛出。
    """

    return await asyncio.wait_for(awaitable, timeout=timeout_seconds)


class DefaultVetResponseComposer:
    """VetResponseComposer 默认应用内实现。"""

    def __init__(
        self,
        *,
        runtime_config_provider: RuntimeConfigProvider,
        conversation_store: ConversationStore,
        checkpoint_store: CheckpointStore,
        trace_sink: VetResponseComposerTraceSink | None = None,
    ) -> None:
        """初始化 VetResponseComposer 默认实现。

        :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
        :param conversation_store: ConversationStore 用户可见消息事实存储。
        :param checkpoint_store: CheckpointStore segment 发布幂等状态存储。
        :param trace_sink: 可选 Composer trace 写入端口。
        :return: None。
        """

        self._runtime_config_provider = runtime_config_provider
        self._conversation_store = conversation_store
        self._checkpoint_store = checkpoint_store
        self._trace_sink = trace_sink or TodoVetResponseComposerTraceSink()

    def is_ready(self) -> bool:
        """判断 Composer 是否具备执行条件。

        :return: 若 RuntimeConfig 与强依赖均已装配则返回 True。
        """

        return (
            self._runtime_config_provider.is_ready()
            and self._conversation_store is not None
            and self._checkpoint_store is not None
        )

    async def compose_turn_response(
        self,
        request: ComposeTurnRequestDto,
    ) -> ComposeTurnResultDto:
        """合成本轮用户可见回复并完成分段发布。

        :param request: 回复合成与发布请求。
        :return: 回复合成与发布结果。
        :raises VetResponseComposerError: 当前置契约、存储写入或覆盖检查失败时抛出。
        """

        operation = VetResponseComposerOperation.COMPOSE_TURN_RESPONSE
        settings = self._load_settings_or_raise(request=request, operation=operation)
        if not settings.enabled:
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_NOT_READY,
                operation=operation,
                message="RuntimeConfig 禁用了 VetResponseComposer",
                request=request,
                retryable=True,
            )
        branches = self._read_branches_or_raise(request=request, operation=operation)
        published_segment_ids = await self._load_published_segment_ids(
            request=request,
            operation=operation,
            timeout_seconds=settings.timeouts.checkpoint_store_seconds,
        )
        candidates = self._build_candidates_or_raise(
            request=request,
            operation=operation,
            branches=branches,
            max_segment_chars=settings.publish.max_segment_chars,
        )
        self._ensure_unique_segment_ids(
            request=request,
            operation=operation,
            candidates=candidates,
        )
        ordered_candidates = self._order_candidates(
            candidates=candidates,
            settings=settings,
        )
        safety_lock_applied = self._ensure_safety_first_lock_released(
            request=request,
            operation=operation,
            branches=branches,
            candidates=ordered_candidates,
            published_segment_ids=published_segment_ids,
        )
        self._ensure_coverage_resolved(
            request=request,
            operation=operation,
            branches=branches,
        )
        ordered_candidates = ordered_candidates[
            : settings.publish.max_segments_per_turn
        ]
        assistant_message_id = await self._create_assistant_message(
            request=request,
            timeout_seconds=settings.timeouts.conversation_store_seconds,
            enabled=settings.publish.create_assistant_message,
        )
        response_segments = await self._publish_candidates(
            request=request,
            operation=operation,
            candidates=ordered_candidates,
            assistant_message_id=assistant_message_id,
            published_segment_ids=published_segment_ids,
            conversation_timeout_seconds=settings.timeouts.conversation_store_seconds,
            checkpoint_timeout_seconds=settings.timeouts.checkpoint_store_seconds,
        )
        final_response_text = settings.publish.final_response_separator.join(
            segment.content for segment in response_segments
        )
        await self._finalize_assistant_message(
            request=request,
            assistant_message_id=assistant_message_id,
            final_response_text=final_response_text,
            timeout_seconds=settings.timeouts.conversation_store_seconds,
            enabled=settings.publish.create_assistant_message,
        )
        turn_audit_tier = self._aggregate_turn_audit_tier(
            segments=response_segments,
            audit_tier_order=settings.audit_tier_order,
        )
        trace_patch = self._build_trace_patch(
            branches=branches,
            response_segments=response_segments,
            candidates=ordered_candidates,
            composer_version=settings.composer_version,
            safety_lock_applied=safety_lock_applied,
            turn_audit_tier=turn_audit_tier,
            trace_degraded=False,
        )
        trace_result = await self._write_trace_patch(
            request=request,
            settings=settings,
            trace_patch=trace_patch,
            timeout_seconds=settings.timeouts.trace_store_seconds,
        )
        if trace_result.status is ComposerTraceWriteStatus.DEGRADED:
            trace_patch = trace_patch.model_copy(update={"trace_degraded": True})
        turn_state = TurnCompositionStateDto(
            request_id=request.request_id,
            trace_id=request.trace_id,
            run_id=request.run_id,
            session_id=request.session_id,
            user_id=request.user_id,
            current_pet_id=request.current_pet_id,
            thread_id=request.thread_id,
            assistant_message_id=assistant_message_id,
            branches=branches,
            segments=response_segments,
            final_response_text=final_response_text,
            turn_publish_status="completed",
            turn_audit_tier=turn_audit_tier,
            trace_degraded=trace_patch.trace_degraded,
        )
        return ComposeTurnResultDto(
            output_text=final_response_text,
            segments=response_segments,
            turn_state=turn_state,
            trace_patch=trace_patch,
            trace_delivery_status=trace_result.status,
            metadata={
                "composer_version": settings.composer_version,
                "trace_degraded": trace_patch.trace_degraded,
                "assistant_message_id": assistant_message_id,
                "stream_delta_chars": settings.publish.stream_delta_chars,
            },
        )

    def _load_settings_or_raise(
        self,
        *,
        request: ComposeTurnRequestDto,
        operation: VetResponseComposerOperation,
    ) -> VetResponseComposerSettings:
        """读取 Composer RuntimeConfig 配置。

        :param request: 当前回复合成请求。
        :param operation: 当前 Composer 操作名。
        :return: VetResponseComposer RuntimeConfig。
        :raises VetResponseComposerError: 当 RuntimeConfig 不可用时抛出。
        """

        try:
            return (
                self._runtime_config_provider.current_snapshot().vet_response_composer
            )
        except RuntimeConfigError as exc:
            raise self._build_error(
                code=(VetResponseComposerErrorCode.COMPOSER_RUNTIME_CONFIG_UNAVAILABLE),
                operation=operation,
                message="VetResponseComposer 无法读取 RuntimeConfig 快照",
                request=request,
                retryable=exc.retryable,
                conflict_with={"dependency_error_code": exc.code.value},
            ) from exc

    def _read_branches_or_raise(
        self,
        *,
        request: ComposeTurnRequestDto,
        operation: VetResponseComposerOperation,
    ) -> list[BranchExecutionStateDto]:
        """从 graph state 读取已触发业务分支状态。

        :param request: 当前回复合成请求。
        :param operation: 当前 Composer 操作名。
        :return: 已归一化的业务分支状态列表。
        :raises VetResponseComposerError: 当 state 缺少分支状态或结构非法时抛出。
        """

        raw_branches: object | None = None
        for key in _BRANCH_STATE_KEYS:
            value = request.graph_state.get(key)
            if isinstance(value, list | tuple):
                raw_branches = value
                break
        if raw_branches is None:
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_BRANCH_STATE_MISSING,
                operation=operation,
                message="graph state 缺少已触发业务分支状态",
                request=request,
                retryable=False,
            )
        branches: list[BranchExecutionStateDto] = []
        for index, raw_branch in enumerate(_as_list(raw_branches)):
            branch_mapping = _as_mapping(raw_branch)
            if branch_mapping is None:
                raise self._build_error(
                    code=VetResponseComposerErrorCode.COMPOSER_BRANCH_STATE_MISSING,
                    operation=operation,
                    message="业务分支状态不是合法映射",
                    request=request,
                    retryable=False,
                    conflict_with={"branch_index": index},
                )
            branches.append(
                self._coerce_branch_state(
                    request=request,
                    operation=operation,
                    raw_branch=branch_mapping,
                    branch_index=index,
                )
            )
        if not branches:
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_BRANCH_STATE_MISSING,
                operation=operation,
                message="本轮没有任何已触发业务分支",
                request=request,
                retryable=False,
            )
        return branches

    def _coerce_branch_state(
        self,
        *,
        request: ComposeTurnRequestDto,
        operation: VetResponseComposerOperation,
        raw_branch: Mapping[str, object],
        branch_index: int,
    ) -> BranchExecutionStateDto:
        """将未知 graph branch 映射归一为严格分支 DTO。

        :param request: 当前回复合成请求。
        :param operation: 当前 Composer 操作名。
        :param raw_branch: graph state 中的原始分支映射。
        :param branch_index: 原始分支在列表中的位置。
        :return: 已归一化的业务分支状态。
        :raises VetResponseComposerError: 当分支或候选段结构非法时抛出。
        """

        branch_id = _read_optional_string(raw_branch.get("branch_id")) or (
            f"branch_{branch_index}"
        )
        task_id = _read_optional_string(raw_branch.get("task_id")) or branch_id
        branch_type = _read_optional_string(raw_branch.get("branch_type")) or (
            _read_optional_string(raw_branch.get("type"))
            or ComposerBranchType.OTHER.value
        )
        publishable_segment = self._coerce_publishable_segment(
            request=request,
            operation=operation,
            raw_branch=raw_branch,
            branch_id=branch_id,
            task_id=task_id,
        )
        try:
            return BranchExecutionStateDto(
                branch_id=branch_id,
                task_id=task_id,
                branch_type=branch_type,
                generation_profile=_read_optional_string(
                    raw_branch.get("generation_profile")
                ),
                executor_key=_read_optional_string(raw_branch.get("executor_key")),
                status=_read_optional_string(raw_branch.get("status")) or "completed",
                publishable_segment=publishable_segment,
                publishable_segment_ref=_read_optional_string(
                    raw_branch.get("publishable_segment_ref")
                ),
                failure_reason=_read_optional_string(raw_branch.get("failure_reason")),
                skip_reason=_read_optional_string(raw_branch.get("skip_reason")),
                trace_patch_ref=_read_optional_string(
                    raw_branch.get("trace_patch_ref")
                ),
            )
        except ValidationError as exc:
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_BRANCH_STATE_MISSING,
                operation=operation,
                message="业务分支状态不符合 Composer 契约",
                request=request,
                retryable=False,
                task_id=task_id,
                conflict_with={
                    "branch_id": branch_id,
                    "validation_error_count": len(exc.errors()),
                },
            ) from exc

    def _coerce_publishable_segment(
        self,
        *,
        request: ComposeTurnRequestDto,
        operation: VetResponseComposerOperation,
        raw_branch: Mapping[str, object],
        branch_id: str,
        task_id: str,
    ) -> PublishableSegmentDto | None:
        """从分支状态中读取并归一化可发布候选段。

        :param request: 当前回复合成请求。
        :param operation: 当前 Composer 操作名。
        :param raw_branch: graph state 中的原始分支映射。
        :param branch_id: 已归一化的分支 ID。
        :param task_id: 已归一化的任务 ID。
        :return: 可发布候选段；分支未产出候选段时返回 None。
        :raises VetResponseComposerError: 当候选段结构非法时抛出。
        """

        raw_segment = raw_branch.get("publishable_segment")
        segment_mapping = _as_mapping(raw_segment)
        if segment_mapping is None and "segment_id" in raw_branch:
            segment_mapping = raw_branch
        if segment_mapping is None:
            return None
        segment_id = _read_optional_string(segment_mapping.get("segment_id"))
        if segment_id is None:
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_SEGMENT_ID_MISSING,
                operation=operation,
                message="可发布 segment 缺少稳定 segment_id",
                request=request,
                retryable=False,
                task_id=task_id,
            )
        segment_type = _read_optional_string(segment_mapping.get("segment_type")) or (
            _read_optional_string(segment_mapping.get("type"))
            or ComposerBranchType.OTHER.value
        )
        try:
            return PublishableSegmentDto(
                segment_id=segment_id,
                branch_id=_read_optional_string(segment_mapping.get("branch_id"))
                or branch_id,
                task_id=_read_optional_string(segment_mapping.get("task_id"))
                or task_id,
                segment_type=segment_type,
                final_response=_read_optional_string(
                    segment_mapping.get("final_response")
                )
                or _read_optional_string(segment_mapping.get("output_text"))
                or _read_optional_string(segment_mapping.get("content")),
                final_response_ref=_read_optional_string(
                    segment_mapping.get("final_response_ref")
                ),
                title=_read_optional_string(segment_mapping.get("title")),
                guard_status=_read_optional_string(segment_mapping.get("guard_status"))
                or "",
                fallback_triggered=_read_bool(
                    segment_mapping.get("fallback_triggered")
                ),
                fallback_template_version=_read_optional_string(
                    segment_mapping.get("fallback_template_version")
                ),
                audit_tier=_read_optional_string(segment_mapping.get("audit_tier")),
                publish_allowed=_read_bool(segment_mapping.get("publish_allowed")),
                safety_direction_present=self._read_optional_bool(
                    segment_mapping.get("safety_direction_present")
                ),
                source_stage=_read_optional_string(segment_mapping.get("source_stage"))
                or "final_response",
                references=self._read_json_map_list(segment_mapping.get("references")),
                reasoning_display=dict(
                    _as_mapping(segment_mapping.get("reasoning_display")) or {}
                )
                or None,
                metadata=dict(_as_mapping(segment_mapping.get("metadata")) or {}),
            )
        except ValidationError as exc:
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_SEGMENT_NOT_GATE_PASSED,
                operation=operation,
                message="可发布 segment 不符合 Composer 契约",
                request=request,
                retryable=False,
                task_id=task_id,
                segment_id=segment_id,
                conflict_with={"validation_error_count": len(exc.errors())},
            ) from exc

    def _read_optional_bool(self, value: object) -> bool | None:
        """从未知值中读取可空布尔值。

        :param value: 需要读取的未知值。
        :return: 若输入为布尔值则返回该值，否则返回 None。
        """

        if isinstance(value, bool):
            return value
        return None

    def _read_json_map_list(self, value: object) -> list[JsonMap]:
        """从未知值中读取 JSON 映射列表。

        :param value: 需要读取的未知值。
        :return: 已过滤非映射元素的 JSON 映射列表。
        """

        return [
            dict(item)
            for item in (_as_mapping(entry) for entry in _as_list(value))
            if item is not None
        ]

    def _ensure_coverage_resolved(
        self,
        *,
        request: ComposeTurnRequestDto,
        operation: VetResponseComposerOperation,
        branches: Sequence[BranchExecutionStateDto],
    ) -> None:
        """确认本轮已触发分支均有可发布段、失败或跳过状态。

        :param request: 当前回复合成请求。
        :param operation: 当前 Composer 操作名。
        :param branches: 本轮已触发业务分支列表。
        :return: None。
        :raises VetResponseComposerError: 当存在静默未覆盖分支时抛出。
        """

        unresolved = [
            branch.branch_id
            for branch in branches
            if branch.publishable_segment is None
            and branch.failure_reason is None
            and branch.skip_reason is None
        ]
        if unresolved:
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_COVERAGE_UNRESOLVED,
                operation=operation,
                message="存在已触发但未形成发布、失败或跳过事实的业务分支",
                request=request,
                retryable=True,
                conflict_with={"unresolved_branch_ids": unresolved},
            )

    async def _load_published_segment_ids(
        self,
        *,
        request: ComposeTurnRequestDto,
        operation: VetResponseComposerOperation,
        timeout_seconds: float,
    ) -> set[str]:
        """从 CheckpointStore 读取当前 thread 已发布 segment ID。

        :param request: 当前回复合成请求。
        :param operation: 当前 Composer 操作名。
        :param timeout_seconds: CheckpointStore 读取超时秒数。
        :return: 当前 thread 已发布 segment ID 集合。
        :raises VetResponseComposerError: 当 thread_id 缺失或 CheckpointStore 读取失败时抛出。
        """

        if request.thread_id is None:
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_CHECKPOINT_READY_FAILED,
                operation=operation,
                message="Composer 缺少 CheckpointStore thread_id",
                request=request,
                retryable=True,
            )
        try:
            result = await _with_timeout(
                self._checkpoint_store.load_latest_checkpoint(
                    LoadLatestCheckpointQueryDto(
                        request_id=request.request_id,
                        trace_id=request.trace_id,
                        thread_id=request.thread_id,
                    )
                ),
                timeout_seconds=timeout_seconds,
            )
        except (CheckpointStoreError, TimeoutError) as exc:
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_CHECKPOINT_READY_FAILED,
                operation=operation,
                message="Composer 读取 segment 发布幂等状态失败",
                request=request,
                retryable=True,
                conflict_with={"dependency_error_code": type(exc).__name__},
            ) from exc
        return {
            segment.segment_id
            for segment in result.published_segments
            if segment.published_at is not None
        }

    def _build_candidates_or_raise(
        self,
        *,
        request: ComposeTurnRequestDto,
        operation: VetResponseComposerOperation,
        branches: Sequence[BranchExecutionStateDto],
        max_segment_chars: int,
    ) -> list[_PublishCandidate]:
        """从分支状态构建已通过资格检查的候选发布段。

        :param request: 当前回复合成请求。
        :param operation: 当前 Composer 操作名。
        :param branches: 本轮已触发业务分支列表。
        :param max_segment_chars: 单个 segment 最大正文字符数。
        :return: 已通过发布资格检查的候选段列表。
        :raises VetResponseComposerError: 当候选段不满足发布资格时抛出。
        """

        candidates: list[_PublishCandidate] = []
        for index, branch in enumerate(branches):
            if branch.publishable_segment is None:
                continue
            self._validate_publishable_segment(
                request=request,
                operation=operation,
                branch=branch,
                segment=branch.publishable_segment,
                max_segment_chars=max_segment_chars,
            )
            candidates.append(
                _PublishCandidate(
                    branch=branch,
                    segment=branch.publishable_segment,
                    original_index=index,
                )
            )
        if not candidates:
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_COVERAGE_UNRESOLVED,
                operation=operation,
                message="本轮没有任何具备发布资格的 segment",
                request=request,
                retryable=True,
            )
        return candidates

    def _validate_publishable_segment(
        self,
        *,
        request: ComposeTurnRequestDto,
        operation: VetResponseComposerOperation,
        branch: BranchExecutionStateDto,
        segment: PublishableSegmentDto,
        max_segment_chars: int,
    ) -> None:
        """执行单个候选段的发布资格检查。

        :param request: 当前回复合成请求。
        :param operation: 当前 Composer 操作名。
        :param branch: 候选段所属业务分支。
        :param segment: 待检查的候选可发布段。
        :param max_segment_chars: 单个 segment 最大正文字符数。
        :return: None。
        :raises VetResponseComposerError: 当候选段不满足发布资格时抛出。
        """

        if segment.source_stage in _UNSAFE_SOURCE_STAGES:
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_UNSAFE_STAGE_PUBLISH_BLOCKED,
                operation=operation,
                message="Composer 拒绝发布草稿或审查中间态文本",
                request=request,
                retryable=False,
                task_id=segment.task_id,
                segment_id=segment.segment_id,
                conflict_with={"source_stage": segment.source_stage},
            )
        allowed_guard_statuses = {item.value for item in ComposerGuardStatus}
        if (
            segment.guard_status not in allowed_guard_statuses
            or not segment.publish_allowed
        ):
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_SEGMENT_NOT_GATE_PASSED,
                operation=operation,
                message="候选 segment 未通过发布门或未获准发布",
                request=request,
                retryable=False,
                task_id=segment.task_id,
                segment_id=segment.segment_id,
                conflict_with={
                    "guard_status": segment.guard_status,
                    "publish_allowed": segment.publish_allowed,
                },
            )
        if segment.final_response is None:
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_SEGMENT_NOT_GATE_PASSED,
                operation=operation,
                message="候选 segment 缺少可直接发布的 final_response",
                request=request,
                retryable=True,
                task_id=segment.task_id,
                segment_id=segment.segment_id,
            )
        if len(segment.final_response) > max_segment_chars:
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_SEGMENT_NOT_GATE_PASSED,
                operation=operation,
                message="候选 segment 正文超过 Composer 配置上限",
                request=request,
                retryable=False,
                task_id=segment.task_id,
                segment_id=segment.segment_id,
                conflict_with={"max_segment_chars": max_segment_chars},
            )
        if self._is_safety_candidate(branch=branch, segment=segment) and (
            segment.safety_direction_present is False
        ):
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_SAFETY_DIRECTION_MISSING,
                operation=operation,
                message="急症首段缺少上游确认的就医导向标记",
                request=request,
                retryable=True,
                task_id=segment.task_id,
                segment_id=segment.segment_id,
            )

    def _ensure_unique_segment_ids(
        self,
        *,
        request: ComposeTurnRequestDto,
        operation: VetResponseComposerOperation,
        candidates: Sequence[_PublishCandidate],
    ) -> None:
        """确认本轮候选 segment ID 唯一。

        :param request: 当前回复合成请求。
        :param operation: 当前 Composer 操作名。
        :param candidates: 候选发布段列表。
        :return: None。
        :raises VetResponseComposerError: 当候选段 ID 重复时抛出。
        """

        seen: set[str] = set()
        duplicates: list[str] = []
        for candidate in candidates:
            segment_id = candidate.segment.segment_id
            if segment_id in seen:
                duplicates.append(segment_id)
            seen.add(segment_id)
        if duplicates:
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_SEGMENT_ID_MISSING,
                operation=operation,
                message="本轮候选 segment_id 存在重复",
                request=request,
                retryable=False,
                conflict_with={"duplicate_segment_ids": duplicates},
            )

    def _order_candidates(
        self,
        *,
        candidates: Sequence[_PublishCandidate],
        settings: VetResponseComposerSettings,
    ) -> list[_PublishCandidate]:
        """按业务优先级排序候选发布段。

        :param candidates: 待排序候选发布段列表。
        :param settings: VetResponseComposer RuntimeConfig。
        :return: 已按业务优先级稳定排序的候选段列表。
        """

        def sort_key(candidate: _PublishCandidate) -> tuple[int, int]:
            """构建候选段排序键。

            :param candidate: 待排序的候选段。
            :return: 由业务优先级与原始序号组成的稳定排序键。
            """

            return (
                self._priority_for_candidate(candidate=candidate, settings=settings),
                candidate.original_index,
            )

        return sorted(candidates, key=sort_key)

    def _priority_for_candidate(
        self,
        *,
        candidate: _PublishCandidate,
        settings: VetResponseComposerSettings,
    ) -> int:
        """解析单个候选段的业务排序优先级。

        :param candidate: 待解析优先级的候选段。
        :param settings: VetResponseComposer RuntimeConfig。
        :return: 数值越小越靠前的排序优先级。
        """

        normalized_branch_type = candidate.branch.branch_type.lower()
        normalized_segment_type = candidate.segment.segment_type.lower()
        if self._is_safety_candidate(
            branch=candidate.branch,
            segment=candidate.segment,
        ):
            return settings.ordering.safety_priority
        if "ocr" in normalized_branch_type or "ocr" in normalized_segment_type:
            return settings.ordering.ocr_priority
        if (
            "medical_record" in normalized_branch_type
            or "record" in normalized_segment_type
        ):
            return settings.ordering.ocr_priority
        if (
            "education" in normalized_branch_type
            or "education" in normalized_segment_type
        ):
            return settings.ordering.education_priority
        if (
            "nonmedical" in normalized_branch_type
            or "pet_care" in normalized_segment_type
        ):
            return settings.ordering.nonmedical_priority
        if "standard" in normalized_branch_type or "medical" in normalized_segment_type:
            return settings.ordering.medical_priority
        return settings.ordering.default_priority

    def _ensure_safety_first_lock_released(
        self,
        *,
        request: ComposeTurnRequestDto,
        operation: VetResponseComposerOperation,
        branches: Sequence[BranchExecutionStateDto],
        candidates: Sequence[_PublishCandidate],
        published_segment_ids: set[str],
    ) -> bool:
        """确认急症首发锁未阻塞当前发布计划。

        :param request: 当前回复合成请求。
        :param operation: 当前 Composer 操作名。
        :param branches: 本轮已触发业务分支列表。
        :param candidates: 已排序候选发布段列表。
        :param published_segment_ids: 已发布 segment ID 集合。
        :return: 若本轮存在急症分支并应用过急症首发约束则返回 True。
        :raises VetResponseComposerError: 当存在未发布急症分支且无可发布急症段时抛出。
        """

        safety_branches = [
            branch for branch in branches if self._is_safety_branch(branch.branch_type)
        ]
        if not safety_branches:
            return False
        safety_candidates = [
            candidate
            for candidate in candidates
            if self._is_safety_candidate(
                branch=candidate.branch,
                segment=candidate.segment,
            )
        ]
        already_published = any(
            candidate.segment.segment_id in published_segment_ids
            for candidate in safety_candidates
        )
        if safety_candidates or already_published:
            first_unpublished = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.segment.segment_id not in published_segment_ids
                ),
                None,
            )
            if first_unpublished is None or self._is_safety_candidate(
                branch=first_unpublished.branch,
                segment=first_unpublished.segment,
            ):
                return True
        unresolved_safety = [
            branch.branch_id
            for branch in safety_branches
            if branch.failure_reason is None and branch.skip_reason is None
        ]
        if unresolved_safety:
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_SAFETY_FIRST_LOCK_ACTIVE,
                operation=operation,
                message="存在未发布急症分支，非急症 segment 不得抢先发布",
                request=request,
                retryable=True,
                conflict_with={"unresolved_safety_branch_ids": unresolved_safety},
            )
        return True

    async def _create_assistant_message(
        self,
        *,
        request: ComposeTurnRequestDto,
        timeout_seconds: float,
        enabled: bool,
    ) -> str:
        """创建或幂等命中助手消息容器。

        :param request: 当前回复合成请求。
        :param timeout_seconds: ConversationStore 写入超时秒数。
        :param enabled: 是否由 Composer 创建助手消息容器。
        :return: 助手消息容器 ID。
        :raises VetResponseComposerError: 当 ConversationStore 写入失败时抛出。
        """

        if not enabled:
            return f"assistant_message_{request.run_id}"
        try:
            result = await _with_timeout(
                self._conversation_store.create_assistant_message(
                    CreateAssistantMessageCommandDto(
                        request_id=request.request_id,
                        trace_id=request.trace_id,
                        session_id=request.session_id,
                        user_id=request.user_id,
                        pet_id=request.current_pet_id,
                        reply_to_message_id=request.user_message_id,
                        content_type="text/plain",
                        idempotency_key=f"composer:message:{request.run_id}",
                        metadata={
                            "run_id": request.run_id,
                            "composer": "VetResponseComposer",
                        },
                    )
                ),
                timeout_seconds=timeout_seconds,
            )
        except (ConversationStoreError, TimeoutError) as exc:
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_CONVERSATION_APPEND_FAILED,
                operation=VetResponseComposerOperation.PUBLISH_SEGMENT,
                message="Composer 创建助手消息容器失败",
                request=request,
                retryable=True,
                conflict_with={"dependency_error_code": type(exc).__name__},
            ) from exc
        return result.message.message_id

    async def _publish_candidates(
        self,
        *,
        request: ComposeTurnRequestDto,
        operation: VetResponseComposerOperation,
        candidates: Sequence[_PublishCandidate],
        assistant_message_id: str,
        published_segment_ids: set[str],
        conversation_timeout_seconds: float,
        checkpoint_timeout_seconds: float,
    ) -> list[ResponseSegmentDto]:
        """按顺序发布候选 segment 并记录幂等状态。

        :param request: 当前回复合成请求。
        :param operation: 当前 Composer 操作名。
        :param candidates: 已排序候选发布段列表。
        :param assistant_message_id: 助手消息容器 ID。
        :param published_segment_ids: 已发布 segment ID 集合。
        :param conversation_timeout_seconds: ConversationStore 写入超时秒数。
        :param checkpoint_timeout_seconds: CheckpointStore 写入超时秒数。
        :return: 已发布或幂等命中的 ResponseSegment 列表。
        :raises VetResponseComposerError: 当任一强依赖发布写入失败时抛出。
        """

        response_segments: list[ResponseSegmentDto] = []
        for order_index, candidate in enumerate(candidates):
            segment = candidate.segment
            if segment.final_response is None:
                continue
            if segment.segment_id not in published_segment_ids:
                await self._append_conversation_segment(
                    request=request,
                    candidate=candidate,
                    assistant_message_id=assistant_message_id,
                    order_index=order_index,
                    timeout_seconds=conversation_timeout_seconds,
                )
                await self._mark_segment_published(
                    request=request,
                    operation=operation,
                    candidate=candidate,
                    timeout_seconds=checkpoint_timeout_seconds,
                )
                published_segment_ids.add(segment.segment_id)
            response_segments.append(
                ResponseSegmentDto(
                    segment_id=segment.segment_id,
                    task_id=segment.task_id,
                    segment_type=segment.segment_type,
                    order_index=order_index,
                    content=segment.final_response,
                    publish_status=ComposerPublishStatus.PUBLISHED,
                    is_first_segment=order_index == 0,
                    published_at=_now_utc(),
                    audit_tier=segment.audit_tier,
                    title=segment.title,
                    trace_refs=[
                        ref
                        for ref in (
                            candidate.branch.trace_patch_ref,
                            segment.final_response_ref,
                        )
                        if ref is not None
                    ],
                    metadata=self._build_response_segment_metadata(candidate=candidate),
                )
            )
        return response_segments

    def _build_response_segment_metadata(
        self,
        *,
        candidate: _PublishCandidate,
    ) -> JsonMap:
        """构建最终 ResponseSegment 的轻量元信息。

        :param candidate: 已通过发布资格检查的候选段。
        :return: 可写入图结果和应用层 segment 的轻量元信息。
        """

        segment = candidate.segment
        metadata: JsonMap = {
            **segment.metadata,
            "branch_id": candidate.branch.branch_id,
            "generation_profile": candidate.branch.generation_profile,
            "executor_key": candidate.branch.executor_key,
            "fallback_triggered": segment.fallback_triggered,
        }
        if segment.reasoning_display is not None:
            metadata["reasoning_display"] = segment.reasoning_display
        return metadata

    async def _append_conversation_segment(
        self,
        *,
        request: ComposeTurnRequestDto,
        candidate: _PublishCandidate,
        assistant_message_id: str,
        order_index: int,
        timeout_seconds: float,
    ) -> None:
        """向 ConversationStore 追加一个助手回复分段。

        :param request: 当前回复合成请求。
        :param candidate: 待发布候选段。
        :param assistant_message_id: 助手消息容器 ID。
        :param order_index: 当前 segment 零基排序索引。
        :param timeout_seconds: ConversationStore 写入超时秒数。
        :return: None。
        :raises VetResponseComposerError: 当 ConversationStore 写入失败时抛出。
        """

        segment = candidate.segment
        if segment.final_response is None:
            return
        try:
            await _with_timeout(
                self._conversation_store.append_assistant_segment(
                    AppendAssistantSegmentCommandDto(
                        request_id=request.request_id,
                        trace_id=request.trace_id,
                        session_id=request.session_id,
                        user_id=request.user_id,
                        pet_id=request.current_pet_id,
                        message_id=assistant_message_id,
                        segment_order=order_index + 1,
                        content=segment.final_response,
                        idempotency_key=(
                            f"composer:segment:{request.run_id}:{segment.segment_id}"
                        ),
                        metadata={
                            "composer_segment_id": segment.segment_id,
                            "branch_id": candidate.branch.branch_id,
                            "task_id": segment.task_id,
                            "segment_type": segment.segment_type,
                            "audit_tier": segment.audit_tier,
                        },
                    )
                ),
                timeout_seconds=timeout_seconds,
            )
        except (ConversationStoreError, TimeoutError) as exc:
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_CONVERSATION_APPEND_FAILED,
                operation=VetResponseComposerOperation.PUBLISH_SEGMENT,
                message="Composer 追加助手 segment 失败",
                request=request,
                retryable=True,
                task_id=segment.task_id,
                segment_id=segment.segment_id,
                conflict_with={"dependency_error_code": type(exc).__name__},
            ) from exc

    async def _mark_segment_published(
        self,
        *,
        request: ComposeTurnRequestDto,
        operation: VetResponseComposerOperation,
        candidate: _PublishCandidate,
        timeout_seconds: float,
    ) -> None:
        """在 CheckpointStore 中幂等标记 segment 已发布。

        :param request: 当前回复合成请求。
        :param operation: 当前 Composer 操作名。
        :param candidate: 已写入 ConversationStore 的候选段。
        :param timeout_seconds: CheckpointStore 写入超时秒数。
        :return: None。
        :raises VetResponseComposerError: 当 CheckpointStore 写入失败时抛出。
        """

        if request.thread_id is None:
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_CHECKPOINT_PUBLISHED_FAILED,
                operation=operation,
                message="Composer 缺少 thread_id，无法标记 segment 已发布",
                request=request,
                retryable=True,
                segment_id=candidate.segment.segment_id,
            )
        try:
            await _with_timeout(
                self._checkpoint_store.mark_segment_published(
                    MarkSegmentPublishedCommandDto(
                        request_id=request.request_id,
                        trace_id=request.trace_id,
                        thread_id=request.thread_id,
                        run_id=request.run_id,
                        segment_id=candidate.segment.segment_id,
                        task_id=candidate.segment.task_id,
                        published_at=_now_utc(),
                        metadata={
                            "source_component": "VetResponseComposer",
                            "branch_id": candidate.branch.branch_id,
                            "segment_type": candidate.segment.segment_type,
                            "audit_tier": candidate.segment.audit_tier,
                        },
                    )
                ),
                timeout_seconds=timeout_seconds,
            )
        except (CheckpointStoreError, TimeoutError) as exc:
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_CHECKPOINT_PUBLISHED_FAILED,
                operation=operation,
                message="Composer 标记 segment 已发布失败",
                request=request,
                retryable=True,
                task_id=candidate.segment.task_id,
                segment_id=candidate.segment.segment_id,
                conflict_with={"dependency_error_code": type(exc).__name__},
            ) from exc

    async def _finalize_assistant_message(
        self,
        *,
        request: ComposeTurnRequestDto,
        assistant_message_id: str,
        final_response_text: str,
        timeout_seconds: float,
        enabled: bool,
    ) -> None:
        """完成助手消息容器并写入整轮最终正文。

        :param request: 当前回复合成请求。
        :param assistant_message_id: 助手消息容器 ID。
        :param final_response_text: 已按业务顺序拼接的最终正文。
        :param timeout_seconds: ConversationStore 写入超时秒数。
        :param enabled: 是否由 Composer 完成助手消息容器。
        :return: None。
        :raises VetResponseComposerError: 当 ConversationStore 完成消息失败时抛出。
        """

        if not enabled:
            return
        try:
            await _with_timeout(
                self._conversation_store.finalize_assistant_message(
                    FinalizeAssistantMessageCommandDto(
                        request_id=request.request_id,
                        trace_id=request.trace_id,
                        session_id=request.session_id,
                        user_id=request.user_id,
                        pet_id=request.current_pet_id,
                        message_id=assistant_message_id,
                        final_content=final_response_text,
                        metadata_patch={
                            "composer": "VetResponseComposer",
                            "run_id": request.run_id,
                        },
                    )
                ),
                timeout_seconds=timeout_seconds,
            )
        except (ConversationStoreError, TimeoutError) as exc:
            raise self._build_error(
                code=VetResponseComposerErrorCode.COMPOSER_CONVERSATION_APPEND_FAILED,
                operation=VetResponseComposerOperation.FINALIZE_TURN_COMPOSITION,
                message="Composer 完成助手消息失败",
                request=request,
                retryable=True,
                conflict_with={"dependency_error_code": type(exc).__name__},
            ) from exc

    def _aggregate_turn_audit_tier(
        self,
        *,
        segments: Sequence[ResponseSegmentDto],
        audit_tier_order: Sequence[str],
    ) -> str | None:
        """按配置顺序聚合整轮 audit_tier。

        :param segments: 已发布 segment 列表。
        :param audit_tier_order: 配置中的 audit_tier 风险顺序。
        :return: 聚合后的最高 audit_tier；没有任何 tier 时返回 None。
        """

        ranked = {tier.upper(): index for index, tier in enumerate(audit_tier_order)}
        best_tier: str | None = None
        best_rank = -1
        for segment in segments:
            if segment.audit_tier is None:
                continue
            normalized = segment.audit_tier.upper()
            rank = ranked.get(normalized, -1)
            if rank > best_rank:
                best_tier = segment.audit_tier
                best_rank = rank
        return best_tier

    def _build_trace_patch(
        self,
        *,
        branches: Sequence[BranchExecutionStateDto],
        response_segments: Sequence[ResponseSegmentDto],
        candidates: Sequence[_PublishCandidate],
        composer_version: str,
        safety_lock_applied: bool,
        turn_audit_tier: str | None,
        trace_degraded: bool,
    ) -> ComposerTracePatchDto:
        """构建 Composer trace patch。

        :param branches: 本轮已触发业务分支列表。
        :param response_segments: 本轮已发布 segment 列表。
        :param candidates: 本轮候选发布段列表。
        :param composer_version: Composer 业务版本。
        :param safety_lock_applied: 本轮是否应用急症首发锁。
        :param turn_audit_tier: 整轮聚合 audit_tier。
        :param trace_degraded: trace 写入是否已知降级。
        :return: 可写入逻辑链的 Composer trace patch。
        """

        published_ids = [segment.segment_id for segment in response_segments]
        first_segment_type = (
            response_segments[0].segment_type if response_segments else None
        )
        fallback_ids = [
            candidate.segment.segment_id
            for candidate in candidates
            if candidate.segment.fallback_triggered
        ]
        failed_branch_ids = [
            branch.branch_id for branch in branches if branch.failure_reason is not None
        ]
        skipped_branch_ids = [
            branch.branch_id for branch in branches if branch.skip_reason is not None
        ]
        return ComposerTracePatchDto(
            triggered_branch_ids=[branch.branch_id for branch in branches],
            published_segment_ids=published_ids,
            first_segment_type=first_segment_type,
            safety_first_lock_applied=safety_lock_applied,
            delayed_segment_ids=[],
            fallback_segment_ids=fallback_ids,
            failed_branch_ids=failed_branch_ids,
            skipped_branch_ids=skipped_branch_ids,
            turn_audit_tier=turn_audit_tier,
            composer_version=composer_version,
            trace_degraded=trace_degraded,
        )

    async def _write_trace_patch(
        self,
        *,
        request: ComposeTurnRequestDto,
        settings: VetResponseComposerSettings,
        trace_patch: ComposerTracePatchDto,
        timeout_seconds: float,
    ) -> ComposerTraceWriteResultDto:
        """写入 Composer trace patch。

        :param request: 当前回复合成请求。
        :param settings: VetResponseComposer RuntimeConfig。
        :param trace_patch: 待写入的 Composer trace patch。
        :param timeout_seconds: trace 写入超时秒数。
        :return: Composer trace 写入结果。
        """

        try:
            return await _with_timeout(
                self._trace_sink.write_composer_trace(
                    ComposerTraceRecordDto(
                        request_id=request.request_id,
                        trace_id=request.trace_id,
                        run_id=request.run_id,
                        session_id=request.session_id,
                        user_id=request.user_id,
                        current_pet_id=request.current_pet_id,
                        params_version=request.params_version,
                        config_snapshot_id=request.config_snapshot_id,
                        trace_schema_version=settings.trace_schema_version,
                        capture_policy_version=settings.capture_policy_version,
                        trace_patch=trace_patch,
                    )
                ),
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            return self._build_trace_degraded_result()

    def _build_trace_degraded_result(self) -> ComposerTraceWriteResultDto:
        """构建 trace 写入异常时的降级结果。

        :return: 标记 Composer trace 写入降级的结果。
        """

        return ComposerTraceWriteResultDto(
            status=ComposerTraceWriteStatus.DEGRADED,
            error_code=VetResponseComposerErrorCode.COMPOSER_TRACE_DEGRADED.value,
            retryable=True,
            detail="Composer trace 写入发生未映射异常",
        )

    def _is_safety_branch(self, branch_type: str) -> bool:
        """判断业务分支是否为急症分支。

        :param branch_type: 待判断的业务分支类型。
        :return: 若分支类型表示急症链路则返回 True。
        """

        return branch_type.lower() == ComposerBranchType.SAFETY_TRIGGER.value

    def _is_safety_candidate(
        self,
        *,
        branch: BranchExecutionStateDto,
        segment: PublishableSegmentDto,
    ) -> bool:
        """判断候选段是否属于急症首发范围。

        :param branch: 候选段所属业务分支。
        :param segment: 待判断候选段。
        :return: 若候选段属于急症链路则返回 True。
        """

        return self._is_safety_branch(branch.branch_type) or (
            segment.segment_type.lower() == ComposerBranchType.SAFETY_TRIGGER.value
        )

    def _build_error(
        self,
        *,
        code: VetResponseComposerErrorCode,
        operation: VetResponseComposerOperation,
        message: str,
        request: ComposeTurnRequestDto,
        retryable: bool | None = None,
        task_id: str | None = None,
        segment_id: str | None = None,
        conflict_with: JsonMap | None = None,
    ) -> VetResponseComposerError:
        """构建 VetResponseComposer 领域异常。

        :param code: 稳定错误码。
        :param operation: 当前操作名。
        :param message: 面向工程排障的错误说明。
        :param request: 当前回复合成请求。
        :param retryable: 可选重试标记。
        :param task_id: 可选任务 ID。
        :param segment_id: 可选 segment ID。
        :param conflict_with: 可选冲突摘要。
        :return: Composer 领域异常。
        """

        return VetResponseComposerError(
            code=code,
            operation=operation,
            message=message,
            retryable=retryable,
            request_id=request.request_id,
            trace_id=request.trace_id,
            run_id=request.run_id,
            task_id=task_id,
            segment_id=segment_id,
            conflict_with=conflict_with,
        )


def create_default_vet_response_composer(
    *,
    runtime_config_provider: RuntimeConfigProvider,
    conversation_store: ConversationStore,
    checkpoint_store: CheckpointStore,
    trace_sink: VetResponseComposerTraceSink | None = None,
) -> VetResponseComposer:
    """创建默认 VetResponseComposer 服务。

    :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
    :param conversation_store: ConversationStore 用户可见消息事实存储。
    :param checkpoint_store: CheckpointStore segment 发布幂等状态存储。
    :param trace_sink: 可选 Composer trace 写入端口。
    :return: 已装配默认依赖的 VetResponseComposer 服务。
    """

    return DefaultVetResponseComposer(
        runtime_config_provider=runtime_config_provider,
        conversation_store=conversation_store,
        checkpoint_store=checkpoint_store,
        trace_sink=trace_sink,
    )


__all__: tuple[str, ...] = (
    "DefaultVetResponseComposer",
    "VetResponseComposer",
    "create_default_vet_response_composer",
)

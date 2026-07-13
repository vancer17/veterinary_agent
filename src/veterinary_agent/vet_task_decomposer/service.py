##################################################################################################
# 文件: src/veterinary_agent/vet_task_decomposer/service.py
# 作用: 实现 VetTaskDecomposer 应用内服务，编排 LLM 拆解、有限审查修复、本地 fallback、单任务透传和观测留痕。
# 边界: 不推断宠物归属、不做 SAF 判定、不读取宠物画像或 RAG、不执行 OCR、不生成用户可见回复。
##################################################################################################

import asyncio
from collections.abc import Mapping
from time import perf_counter
from typing import Protocol

from pydantic import ValidationError

from veterinary_agent.agent_runner import (
    AgentRunRequestDto,
    AgentRunResultDto,
    AgentRunStatus,
    AgentRunner,
    AgentRunnerError,
)
from veterinary_agent.config import (
    RuntimeConfigError,
    RuntimeConfigProvider,
    RuntimeConfigSnapshot,
    VetTaskDecomposerSettings,
)
from veterinary_agent.observability import (
    MetricType,
    ObservabilityProvider,
    StructuredLogLevel,
)
from veterinary_agent.vet_task_decomposer.dto import (
    AttachmentBindingDto,
    DecompositionTraceSummaryDto,
    JsonMap,
    LocalFallbackResultDto,
    TextSpanDto,
    VetSubTaskDto,
    VetTaskDecomposeRequestDto,
    VetTaskDecomposeResultDto,
    VetTaskDecomposeTraceRecordDto,
    VetTaskTraceWriteResultDto,
    build_text_hash,
)
from veterinary_agent.vet_task_decomposer.enums import (
    AttachmentRole,
    DecompositionMethod,
    DecompositionStatus,
    TaskPriorityHint,
    VetTaskDecomposerErrorCode,
    VetTaskDecomposerOperation,
    VetTaskTraceWriteStatus,
    VetTaskType,
)
from veterinary_agent.vet_task_decomposer.errors import VetTaskDecomposerError
from veterinary_agent.vet_task_decomposer.fallback import (
    TodoVetTaskLocalFallback,
    VetTaskLocalFallback,
)
from veterinary_agent.vet_task_decomposer.trace import (
    TodoVetTaskDecomposerTraceSink,
    VetTaskDecomposerTraceSink,
)

_COMPONENT_NAME = "vet_task_decomposer"


class VetTaskDecomposer(Protocol):
    """VetTaskDecomposer 应用内服务接口。"""

    def is_ready(self) -> bool:
        """判断任务拆解服务是否具备执行条件。

        :return: 若 RuntimeConfig 可用且组件启用则返回 True。
        """

        ...

    async def decompose(
        self,
        request: VetTaskDecomposeRequestDto,
    ) -> VetTaskDecomposeResultDto:
        """将单轮用户输入拆解为当前宠物下的业务子任务。

        :param request: 严格任务拆解请求。
        :return: 至少包含一个子任务的拆解结果。
        :raises VetTaskDecomposerError: 当前置契约或强依赖不满足执行条件时抛出。
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

    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _read_bool(value: object, *, default: bool) -> bool:
    """从未知值中读取布尔值。

    :param value: 需要读取的未知值。
    :param default: 无法读取布尔值时使用的默认值。
    :return: 解析后的布尔值或默认值。
    """

    if isinstance(value, bool):
        return value
    return default


def _read_float(value: object, *, default: float) -> float:
    """从未知值中读取 0 到 1 之间的浮点数。

    :param value: 需要读取的未知值。
    :param default: 无法读取浮点数时使用的默认值。
    :return: 归一化到 0 到 1 区间内的浮点数。
    """

    if isinstance(value, int | float):
        return max(0.0, min(1.0, float(value)))
    return default


def _average_confidence(tasks: list[VetSubTaskDto]) -> float:
    """计算任务列表的平均置信度。

    :param tasks: 已归一化的子任务列表。
    :return: 子任务平均置信度；列表为空时返回 0。
    """

    if not tasks:
        return 0.0
    return sum(task.confidence for task in tasks) / len(tasks)


class DefaultVetTaskDecomposer:
    """VetTaskDecomposer 默认应用内实现。"""

    def __init__(
        self,
        *,
        runtime_config_provider: RuntimeConfigProvider,
        agent_runner: AgentRunner | None = None,
        local_fallback: VetTaskLocalFallback | None = None,
        trace_sink: VetTaskDecomposerTraceSink | None = None,
        observability_provider: ObservabilityProvider | None = None,
    ) -> None:
        """初始化 VetTaskDecomposer 默认实现。

        :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
        :param agent_runner: 可选 AgentRunner 结构化拆解端口；缺失时进入降级。
        :param local_fallback: 可选本地 span fallback 端口；缺失时使用 TODO 空壳。
        :param trace_sink: 可选拆解摘要写入端口；缺失时使用 TODO 空壳。
        :param observability_provider: 可选 Observability provider。
        :return: None。
        """

        self._runtime_config_provider = runtime_config_provider
        self._agent_runner = agent_runner
        self._local_fallback = local_fallback or TodoVetTaskLocalFallback()
        self._trace_sink = trace_sink or TodoVetTaskDecomposerTraceSink()
        self._observability_provider = observability_provider

    def is_ready(self) -> bool:
        """判断任务拆解服务是否具备执行条件。

        :return: 若 RuntimeConfig 可读取且 VetTaskDecomposer 已启用则返回 True。
        """

        if not self._runtime_config_provider.is_ready():
            return False
        try:
            snapshot = self._runtime_config_provider.current_snapshot()
        except RuntimeConfigError:
            return False
        return snapshot.vet_task_decomposer.enabled

    async def decompose(
        self,
        request: VetTaskDecomposeRequestDto,
    ) -> VetTaskDecomposeResultDto:
        """将单轮用户输入拆解为当前宠物下的业务子任务。

        :param request: 严格任务拆解请求。
        :return: 至少包含一个子任务的拆解结果。
        :raises VetTaskDecomposerError: 当前置契约或强依赖不满足执行条件时抛出。
        """

        started_monotonic = perf_counter()
        result: VetTaskDecomposeResultDto | None = None
        try:
            snapshot = self._load_config_snapshot(request=request)
            settings = snapshot.vet_task_decomposer
            self._validate_input_or_raise(request=request, settings=settings)
            tasks, method, llm_unavailable = await self._decompose_with_dependencies(
                request=request,
                settings=settings,
            )
            if not tasks:
                tasks = self._build_single_passthrough_task(request=request)
                method = DecompositionMethod.SINGLE_PASSTHROUGH
            result = self._build_result(
                request=request,
                settings=settings,
                tasks=tasks,
                method=method,
                llm_unavailable=llm_unavailable,
            )
            trace_result = await self._write_trace_safely(
                request=request,
                result=result,
            )
            result = result.model_copy(
                update={"trace_delivery_status": trace_result.status}
            )
            return result
        except VetTaskDecomposerError:
            raise
        except RuntimeConfigError as exc:
            raise VetTaskDecomposerError(
                code=(
                    VetTaskDecomposerErrorCode.TASK_DECOMPOSE_RUNTIME_CONFIG_UNAVAILABLE
                ),
                operation=VetTaskDecomposerOperation.DECOMPOSE_TASKS,
                message="VetTaskDecomposer 无法读取有效 RuntimeConfig 快照",
                retryable=exc.retryable,
                request_id=request.request_id,
                trace_id=request.trace_id,
                conflict_with={"runtime_config_error_code": exc.code.value},
            ) from exc
        except Exception as exc:
            raise VetTaskDecomposerError(
                code=VetTaskDecomposerErrorCode.TASK_DECOMPOSE_INTERNAL_ERROR,
                operation=VetTaskDecomposerOperation.DECOMPOSE_TASKS,
                message="VetTaskDecomposer 发生未映射内部错误",
                retryable=True,
                request_id=request.request_id,
                trace_id=request.trace_id,
                conflict_with={"exception_type": type(exc).__name__},
            ) from exc
        finally:
            self._record_observability(
                request=request,
                result=result,
                duration_seconds=perf_counter() - started_monotonic,
            )

    def _load_config_snapshot(
        self,
        *,
        request: VetTaskDecomposeRequestDto,
    ) -> RuntimeConfigSnapshot:
        """读取并校验当前拆解请求使用的配置快照。

        :param request: 当前拆解请求。
        :return: 与请求版本一致且启用 VetTaskDecomposer 的配置快照。
        :raises VetTaskDecomposerError: 当配置快照不可用、版本不一致或组件禁用时抛出。
        """

        try:
            snapshot = self._runtime_config_provider.current_snapshot()
        except RuntimeConfigError as exc:
            raise VetTaskDecomposerError(
                code=(
                    VetTaskDecomposerErrorCode.TASK_DECOMPOSE_RUNTIME_CONFIG_UNAVAILABLE
                ),
                operation=VetTaskDecomposerOperation.DECOMPOSE_TASKS,
                message="VetTaskDecomposer 读取 RuntimeConfig 快照失败",
                retryable=exc.retryable,
                request_id=request.request_id,
                trace_id=request.trace_id,
                conflict_with={"runtime_config_error_code": exc.code.value},
            ) from exc
        if not snapshot.vet_task_decomposer.enabled:
            raise VetTaskDecomposerError(
                code=VetTaskDecomposerErrorCode.TASK_DECOMPOSE_NOT_READY,
                operation=VetTaskDecomposerOperation.DECOMPOSE_TASKS,
                message="RuntimeConfig 禁用了 VetTaskDecomposer",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                conflict_with={"enabled": False},
            )
        if request.config_snapshot_id != snapshot.config_snapshot_id:
            raise VetTaskDecomposerError(
                code=VetTaskDecomposerErrorCode.TASK_DECOMPOSE_INVALID_REQUEST,
                operation=VetTaskDecomposerOperation.VALIDATE_INPUT,
                message="拆解请求绑定的 config_snapshot_id 与当前快照不一致",
                retryable=True,
                request_id=request.request_id,
                trace_id=request.trace_id,
                conflict_with={
                    "request_config_snapshot_id": request.config_snapshot_id,
                    "current_config_snapshot_id": snapshot.config_snapshot_id,
                },
            )
        if request.params_version != snapshot.params_version:
            raise VetTaskDecomposerError(
                code=VetTaskDecomposerErrorCode.TASK_DECOMPOSE_INVALID_REQUEST,
                operation=VetTaskDecomposerOperation.VALIDATE_INPUT,
                message="拆解请求绑定的 params_version 与当前快照不一致",
                retryable=True,
                request_id=request.request_id,
                trace_id=request.trace_id,
                conflict_with={
                    "request_params_version": request.params_version,
                    "current_params_version": snapshot.params_version,
                },
            )
        return snapshot

    def _validate_input_or_raise(
        self,
        *,
        request: VetTaskDecomposeRequestDto,
        settings: VetTaskDecomposerSettings,
    ) -> None:
        """校验拆解输入契约。

        :param request: 当前拆解请求。
        :param settings: 当前组件运行配置。
        :return: None。
        :raises VetTaskDecomposerError: 当缺少当前宠物、用户原文为空或文本过长时抛出。
        """

        if not request.current_pet_id:
            raise VetTaskDecomposerError(
                code=(VetTaskDecomposerErrorCode.TASK_DECOMPOSE_MISSING_CURRENT_PET_ID),
                operation=VetTaskDecomposerOperation.VALIDATE_INPUT,
                message="VetTaskDecomposer 缺少 PetSessionPolicy 产出的 current_pet_id",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                conflict_with={"field": "current_pet_id"},
            )
        if not request.user_message.strip():
            raise VetTaskDecomposerError(
                code=VetTaskDecomposerErrorCode.TASK_DECOMPOSE_EMPTY_MESSAGE,
                operation=VetTaskDecomposerOperation.VALIDATE_INPUT,
                message="VetTaskDecomposer 收到空用户原文",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                conflict_with={"field": "user_message"},
            )
        if len(request.user_message) > settings.max_user_message_chars:
            raise VetTaskDecomposerError(
                code=VetTaskDecomposerErrorCode.TASK_DECOMPOSE_INVALID_REQUEST,
                operation=VetTaskDecomposerOperation.VALIDATE_INPUT,
                message="用户原文长度超过 VetTaskDecomposer 配置上限",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                conflict_with={
                    "max_user_message_chars": settings.max_user_message_chars,
                    "actual_chars": len(request.user_message),
                },
            )

    async def _decompose_with_dependencies(
        self,
        *,
        request: VetTaskDecomposeRequestDto,
        settings: VetTaskDecomposerSettings,
    ) -> tuple[list[VetSubTaskDto], DecompositionMethod, bool]:
        """按 LLM、审查修复、本地 fallback 的顺序尝试拆解。

        :param request: 当前拆解请求。
        :param settings: 当前组件运行配置。
        :return: 子任务列表、拆解方法和 LLM 不可用标记。
        """

        llm_unavailable = False
        if settings.llm_enabled:
            tasks, method, llm_unavailable = await self._try_llm_decomposition(
                request=request,
                settings=settings,
            )
            if tasks:
                return tasks, method, llm_unavailable
        if settings.local_fallback_enabled:
            fallback_result = await self._try_local_fallback(
                request=request,
                settings=settings,
            )
            if fallback_result.available and fallback_result.tasks:
                return (
                    fallback_result.tasks,
                    DecompositionMethod.LOCAL_FALLBACK,
                    llm_unavailable,
                )
        return [], DecompositionMethod.SINGLE_PASSTHROUGH, llm_unavailable

    async def _try_llm_decomposition(
        self,
        *,
        request: VetTaskDecomposeRequestDto,
        settings: VetTaskDecomposerSettings,
    ) -> tuple[list[VetSubTaskDto], DecompositionMethod, bool]:
        """尝试使用 AgentRunner 执行 LLM 结构化拆解。

        :param request: 当前拆解请求。
        :param settings: 当前组件运行配置。
        :return: 子任务列表、拆解方法和 LLM 不可用标记。
        """

        if self._agent_runner is None or not self._agent_runner.is_ready():
            return [], DecompositionMethod.SINGLE_PASSTHROUGH, True
        try:
            agent_result = await self._run_agent(
                request=request,
                settings=settings,
                agent_id=settings.decompose_agent_id,
                agent_version=settings.decompose_agent_version,
                timeout_seconds=settings.timeouts.llm_seconds,
                task_input=self._build_llm_task_input(request=request),
            )
        except (AgentRunnerError, TimeoutError, asyncio.TimeoutError):
            return [], DecompositionMethod.SINGLE_PASSTHROUGH, True
        tasks = self._normalize_agent_result(
            request=request,
            settings=settings,
            agent_result=agent_result,
            default_confidence=0.7,
        )
        if self._tasks_meet_confidence(
            tasks=tasks,
            min_confidence=settings.confidence.min_llm_confidence,
        ):
            return tasks, DecompositionMethod.LLM, False
        if not settings.review_repair_enabled:
            return [], DecompositionMethod.SINGLE_PASSTHROUGH, False
        reviewed_tasks = await self._try_llm_review(
            request=request,
            settings=settings,
            agent_result=agent_result,
        )
        if self._tasks_meet_confidence(
            tasks=reviewed_tasks,
            min_confidence=settings.confidence.min_llm_confidence,
        ):
            return reviewed_tasks, DecompositionMethod.LLM_REVIEW_REPAIRED, False
        return [], DecompositionMethod.SINGLE_PASSTHROUGH, False

    async def _try_llm_review(
        self,
        *,
        request: VetTaskDecomposeRequestDto,
        settings: VetTaskDecomposerSettings,
        agent_result: AgentRunResultDto,
    ) -> list[VetSubTaskDto]:
        """尝试使用审查修复 Agent 修正低置信或非法候选。

        :param request: 当前拆解请求。
        :param settings: 当前组件运行配置。
        :param agent_result: 主拆解 Agent 返回的原始结果。
        :return: 审查修复后通过归一化的子任务列表。
        """

        if self._agent_runner is None or not self._agent_runner.is_ready():
            return []
        try:
            review_result = await self._run_agent(
                request=request,
                settings=settings,
                agent_id=settings.review_agent_id,
                agent_version=settings.review_agent_version,
                timeout_seconds=settings.timeouts.review_seconds,
                task_input={
                    **self._build_llm_task_input(request=request),
                    "candidate_output": agent_result.parsed_output,
                    "candidate_schema_valid": agent_result.schema_valid,
                    "candidate_validation_errors": [
                        error.model_dump(mode="json")
                        for error in agent_result.validation_errors
                    ],
                },
            )
        except (AgentRunnerError, TimeoutError, asyncio.TimeoutError):
            return []
        return self._normalize_agent_result(
            request=request,
            settings=settings,
            agent_result=review_result,
            default_confidence=0.6,
        )

    async def _run_agent(
        self,
        *,
        request: VetTaskDecomposeRequestDto,
        settings: VetTaskDecomposerSettings,
        agent_id: str,
        agent_version: str,
        timeout_seconds: float,
        task_input: JsonMap,
    ) -> AgentRunResultDto:
        """通过 AgentRunner 调用指定任务拆解 Agent。

        :param request: 当前拆解请求。
        :param settings: 当前组件运行配置。
        :param agent_id: 需要调用的 Agent ID。
        :param agent_version: 需要调用的 Agent 版本。
        :param timeout_seconds: 本次 Agent 调用总超时。
        :param task_input: 传递给 AgentRunner 的业务任务输入。
        :return: AgentRunner 标准运行结果。
        :raises AgentRunnerError: 当 AgentRunner 运行失败时抛出。
        :raises TimeoutError: 当调用超过组件配置超时时抛出。
        """

        del settings
        if self._agent_runner is None:
            raise TimeoutError("AgentRunner 未接入")
        async with asyncio.timeout(timeout_seconds):
            return await self._agent_runner.run_agent(
                AgentRunRequestDto(
                    run_id=f"{request.run_id}:{agent_id}",
                    trace_id=request.trace_id,
                    request_id=request.request_id,
                    session_id=request.session_id,
                    user_id=request.user_id,
                    agent_id=agent_id,
                    agent_version=agent_version,
                    task_input=task_input,
                    runtime_options={
                        "component": _COMPONENT_NAME,
                        "params_version": request.params_version,
                        "config_snapshot_id": request.config_snapshot_id,
                    },
                )
            )

    def _build_llm_task_input(
        self,
        *,
        request: VetTaskDecomposeRequestDto,
    ) -> JsonMap:
        """构建传给任务拆解 Agent 的受控输入。

        :param request: 当前拆解请求。
        :return: 不含外部二进制内容的任务拆解输入。
        """

        return {
            "current_pet_id": request.current_pet_id,
            "user_message": request.user_message,
            "attachments": [
                attachment.model_dump(mode="json") for attachment in request.attachments
            ],
            "allowed_task_types": [task_type.value for task_type in VetTaskType],
            "allowed_attachment_roles": [role.value for role in AttachmentRole],
            "contract_rules": [
                "所有子任务 current_pet_id 必须等于输入 current_pet_id",
                "不得输出多宠或它宠识别字段",
                "source_span 必须回指 user_message 原文",
                "附件 ID 只能来自本轮 attachments",
            ],
        }

    def _normalize_agent_result(
        self,
        *,
        request: VetTaskDecomposeRequestDto,
        settings: VetTaskDecomposerSettings,
        agent_result: AgentRunResultDto,
        default_confidence: float,
    ) -> list[VetSubTaskDto]:
        """归一化 AgentRunner 结构化输出。

        :param request: 当前拆解请求。
        :param settings: 当前组件运行配置。
        :param agent_result: AgentRunner 返回结果。
        :param default_confidence: 候选缺少置信度时使用的默认值。
        :return: 通过契约校验的子任务列表；无法归一化时返回空列表。
        """

        if agent_result.status is not AgentRunStatus.SUCCEEDED:
            return []
        output = _as_mapping(agent_result.parsed_output)
        if output is None:
            return []
        raw_tasks = _as_list(output.get("tasks") or output.get("sub_tasks"))
        if not raw_tasks:
            return []
        attachment_ids = {
            attachment.attachment_id for attachment in request.attachments
        }
        normalized_tasks: list[VetSubTaskDto] = []
        for index, raw_task in enumerate(raw_tasks[: settings.max_tasks_per_turn]):
            task_map = _as_mapping(raw_task)
            if task_map is None:
                continue
            task = self._normalize_task_candidate(
                request=request,
                index=index,
                candidate=task_map,
                attachment_ids=attachment_ids,
                default_confidence=default_confidence,
            )
            if task is not None:
                normalized_tasks.append(task)
        return normalized_tasks

    def _normalize_task_candidate(
        self,
        *,
        request: VetTaskDecomposeRequestDto,
        index: int,
        candidate: Mapping[str, object],
        attachment_ids: set[str],
        default_confidence: float,
    ) -> VetSubTaskDto | None:
        """归一化单个 LLM 或 fallback 候选任务。

        :param request: 当前拆解请求。
        :param index: 候选任务在本轮输出中的序号。
        :param candidate: 候选任务原始映射。
        :param attachment_ids: 本轮合法附件 ID 集合。
        :param default_confidence: 候选缺少置信度时使用的默认值。
        :return: 归一化后的子任务；候选不满足契约时返回 None。
        """

        task_type = self._read_task_type(candidate.get("task_type"))
        if task_type is None:
            return None
        span = self._resolve_source_span(
            request=request,
            candidate=candidate,
        )
        if span is None:
            return None
        span_text = request.user_message[span.start_offset : span.end_offset]
        normalized_query = _read_string(candidate.get("normalized_query")) or span_text
        if not normalized_query:
            return None
        bindings = self._read_attachment_bindings(
            candidate=candidate,
            attachment_ids=attachment_ids,
        )
        confidence = _read_float(
            candidate.get("confidence"), default=default_confidence
        )
        task_id = self._build_task_id(
            request=request,
            index=index,
            task_type=task_type,
            span=span,
            normalized_query=normalized_query,
        )
        try:
            return VetSubTaskDto(
                task_id=task_id,
                task_type=task_type,
                current_pet_id=request.current_pet_id or "",
                source_span=span,
                normalized_query=normalized_query,
                attachment_bindings=bindings,
                priority_hint=self._read_priority(candidate.get("priority_hint")),
                coverage_required=_read_bool(
                    candidate.get("coverage_required"),
                    default=True,
                ),
                requires_independent_segment=_read_bool(
                    candidate.get("requires_independent_segment"),
                    default=True,
                ),
                confidence=confidence,
            )
        except ValidationError:
            return None

    def _read_task_type(self, value: object) -> VetTaskType | None:
        """读取并校验受控任务类型。

        :param value: 原始任务类型值。
        :return: 合法 VetTaskType；无法解析时返回 None。
        """

        task_type = _read_string(value)
        if task_type is None:
            return None
        try:
            return VetTaskType(task_type)
        except ValueError:
            return None

    def _read_priority(self, value: object) -> TaskPriorityHint:
        """读取任务优先级提示。

        :param value: 原始优先级提示值。
        :return: 合法优先级提示；无法解析时返回 unknown。
        """

        priority = _read_string(value)
        if priority is None:
            return TaskPriorityHint.UNKNOWN
        try:
            return TaskPriorityHint(priority)
        except ValueError:
            return TaskPriorityHint.UNKNOWN

    def _resolve_source_span(
        self,
        *,
        request: VetTaskDecomposeRequestDto,
        candidate: Mapping[str, object],
    ) -> TextSpanDto | None:
        """解析并校验候选任务的 source span。

        :param request: 当前拆解请求。
        :param candidate: 候选任务原始映射。
        :return: 可回指用户原文的 source span；无法校验时返回 None。
        """

        span_map = _as_mapping(candidate.get("source_span"))
        if span_map is not None:
            span = self._span_from_offsets(
                user_message=request.user_message,
                start_value=span_map.get("start_offset"),
                end_value=span_map.get("end_offset"),
            )
            if span is not None:
                return span
        source_text = (
            _read_string(candidate.get("source_text"))
            or _read_string(candidate.get("text"))
            or _read_string(candidate.get("normalized_query"))
        )
        if source_text is None:
            return None
        return self._span_from_text(
            user_message=request.user_message,
            source_text=source_text,
        )

    def _span_from_offsets(
        self,
        *,
        user_message: str,
        start_value: object,
        end_value: object,
    ) -> TextSpanDto | None:
        """根据候选偏移构建 source span。

        :param user_message: 本轮用户原文。
        :param start_value: 候选起始偏移。
        :param end_value: 候选结束偏移。
        :return: 通过边界校验的 source span；偏移非法时返回 None。
        """

        if not isinstance(start_value, int) or not isinstance(end_value, int):
            return None
        if start_value < 0 or end_value > len(user_message) or end_value <= start_value:
            return None
        span_text = user_message[start_value:end_value]
        if not span_text:
            return None
        try:
            return TextSpanDto(
                start_offset=start_value,
                end_offset=end_value,
                text_hash=build_text_hash(span_text),
            )
        except ValidationError:
            return None

    def _span_from_text(
        self,
        *,
        user_message: str,
        source_text: str,
    ) -> TextSpanDto | None:
        """根据候选原文片段定位 source span。

        :param user_message: 本轮用户原文。
        :param source_text: 候选 source text。
        :return: 定位成功后的 source span；无法定位时返回 None。
        """

        start_offset = user_message.find(source_text)
        if start_offset < 0:
            return None
        end_offset = start_offset + len(source_text)
        return self._span_from_offsets(
            user_message=user_message,
            start_value=start_offset,
            end_value=end_offset,
        )

    def _read_attachment_bindings(
        self,
        *,
        candidate: Mapping[str, object],
        attachment_ids: set[str],
    ) -> list[AttachmentBindingDto]:
        """读取并归一化候选任务的附件绑定。

        :param candidate: 候选任务原始映射。
        :param attachment_ids: 本轮合法附件 ID 集合。
        :return: 去重且只包含合法附件 ID 的附件绑定列表。
        """

        bindings: list[AttachmentBindingDto] = []
        seen_attachment_ids: set[str] = set()
        for raw_binding in _as_list(candidate.get("attachment_bindings")):
            binding_map = _as_mapping(raw_binding)
            if binding_map is None:
                continue
            attachment_id = _read_string(binding_map.get("attachment_id"))
            if attachment_id is None or attachment_id not in attachment_ids:
                continue
            if attachment_id in seen_attachment_ids:
                continue
            role = self._read_attachment_role(binding_map.get("attachment_role"))
            if role is AttachmentRole.NONE:
                continue
            try:
                bindings.append(
                    AttachmentBindingDto(
                        attachment_id=attachment_id,
                        attachment_role=role,
                    )
                )
                seen_attachment_ids.add(attachment_id)
            except ValidationError:
                continue
        return bindings

    def _read_attachment_role(self, value: object) -> AttachmentRole:
        """读取附件角色枚举。

        :param value: 原始附件角色值。
        :return: 合法附件角色；无法解析时返回 unknown。
        """

        role = _read_string(value)
        if role is None:
            return AttachmentRole.UNKNOWN
        try:
            return AttachmentRole(role)
        except ValueError:
            return AttachmentRole.UNKNOWN

    def _build_task_id(
        self,
        *,
        request: VetTaskDecomposeRequestDto,
        index: int,
        task_type: VetTaskType,
        span: TextSpanDto,
        normalized_query: str,
    ) -> str:
        """构建确定性子任务 ID。

        :param request: 当前拆解请求。
        :param index: 当前任务序号。
        :param task_type: 当前任务类型。
        :param span: 当前任务 source span。
        :param normalized_query: 当前任务规范化文本。
        :return: 带 task_ 前缀的稳定子任务 ID。
        """

        basis = "|".join(
            (
                request.request_id,
                request.current_pet_id or "",
                str(index),
                task_type.value,
                str(span.start_offset),
                str(span.end_offset),
                normalized_query,
            )
        )
        return f"task_{build_text_hash(basis)[:16]}"

    def _tasks_meet_confidence(
        self,
        *,
        tasks: list[VetSubTaskDto],
        min_confidence: float,
    ) -> bool:
        """判断任务列表是否满足最低置信度要求。

        :param tasks: 已归一化的子任务列表。
        :param min_confidence: 最低允许整体置信度。
        :return: 若存在任务且平均置信度不低于阈值则返回 True。
        """

        return bool(tasks) and _average_confidence(tasks) >= min_confidence

    async def _try_local_fallback(
        self,
        *,
        request: VetTaskDecomposeRequestDto,
        settings: VetTaskDecomposerSettings,
    ) -> LocalFallbackResultDto:
        """尝试调用本地 span fallback。

        :param request: 当前拆解请求。
        :param settings: 当前组件运行配置。
        :return: 本地 fallback 候选结果；不可用时返回 available=False。
        """

        if not self._local_fallback.is_ready():
            return LocalFallbackResultDto(
                available=False,
                error_code=(
                    VetTaskDecomposerErrorCode.TASK_DECOMPOSE_LOCAL_FALLBACK_UNAVAILABLE
                ).value,
                detail="local_fallback_not_ready",
            )
        try:
            async with asyncio.timeout(settings.timeouts.local_fallback_seconds):
                fallback_result = await self._local_fallback.decompose(request)
        except (TimeoutError, asyncio.TimeoutError):
            return LocalFallbackResultDto(
                available=False,
                error_code=(
                    VetTaskDecomposerErrorCode.TASK_DECOMPOSE_LOCAL_FALLBACK_UNAVAILABLE
                ).value,
                detail="local_fallback_timeout",
            )
        if (
            not fallback_result.available
            or fallback_result.confidence
            < settings.confidence.min_local_fallback_confidence
        ):
            return LocalFallbackResultDto(
                available=False,
                error_code=fallback_result.error_code,
                detail=fallback_result.detail or "local_fallback_low_confidence",
            )
        valid_tasks = self._filter_contract_valid_tasks(
            request=request,
            tasks=fallback_result.tasks,
        )
        if not valid_tasks:
            return LocalFallbackResultDto(
                available=False,
                error_code=VetTaskDecomposerErrorCode.TASK_DECOMPOSE_EMPTY_RESULT.value,
                detail="local_fallback_contract_invalid",
            )
        return fallback_result.model_copy(update={"tasks": valid_tasks})

    def _filter_contract_valid_tasks(
        self,
        *,
        request: VetTaskDecomposeRequestDto,
        tasks: list[VetSubTaskDto],
    ) -> list[VetSubTaskDto]:
        """过滤不满足当前宠物和 source span 契约的外部候选任务。

        :param request: 当前拆解请求。
        :param tasks: 外部 fallback 返回的候选任务列表。
        :return: 满足当前宠物归属和 source span 回指契约的任务列表。
        """

        valid_tasks: list[VetSubTaskDto] = []
        for task in tasks:
            if task.current_pet_id != request.current_pet_id:
                continue
            span = task.source_span
            if span.end_offset > len(request.user_message):
                continue
            span_text = request.user_message[span.start_offset : span.end_offset]
            if build_text_hash(span_text) != span.text_hash:
                continue
            valid_tasks.append(task)
        return valid_tasks

    def _build_single_passthrough_task(
        self,
        *,
        request: VetTaskDecomposeRequestDto,
    ) -> list[VetSubTaskDto]:
        """构建完整覆盖用户原文的单任务透传结果。

        :param request: 当前拆解请求。
        :return: 只包含一个 UNDECOMPOSED 子任务的列表。
        """

        span = TextSpanDto(
            start_offset=0,
            end_offset=len(request.user_message),
            text_hash=build_text_hash(request.user_message),
        )
        bindings = [
            AttachmentBindingDto(
                attachment_id=attachment.attachment_id,
                attachment_role=AttachmentRole.UNKNOWN,
            )
            for attachment in request.attachments
        ]
        return [
            VetSubTaskDto(
                task_id=self._build_task_id(
                    request=request,
                    index=0,
                    task_type=VetTaskType.UNDECOMPOSED,
                    span=span,
                    normalized_query=request.user_message,
                ),
                task_type=VetTaskType.UNDECOMPOSED,
                current_pet_id=request.current_pet_id or "",
                source_span=span,
                normalized_query=request.user_message,
                attachment_bindings=bindings,
                priority_hint=TaskPriorityHint.UNKNOWN,
                coverage_required=True,
                requires_independent_segment=True,
                confidence=0.0,
            )
        ]

    def _build_result(
        self,
        *,
        request: VetTaskDecomposeRequestDto,
        settings: VetTaskDecomposerSettings,
        tasks: list[VetSubTaskDto],
        method: DecompositionMethod,
        llm_unavailable: bool,
    ) -> VetTaskDecomposeResultDto:
        """构建标准拆解结果。

        :param request: 当前拆解请求。
        :param settings: 当前组件运行配置。
        :param tasks: 已归一化的任务列表。
        :param method: 本次采用的拆解方法。
        :param llm_unavailable: LLM 主路径是否不可用。
        :return: VetTaskDecomposer 标准结果。
        """

        del request
        fallback_used = method in {
            DecompositionMethod.LOCAL_FALLBACK,
            DecompositionMethod.SINGLE_PASSTHROUGH,
        }
        return VetTaskDecomposeResultDto(
            tasks=tasks,
            trace_summary=DecompositionTraceSummaryDto(
                decomposer_version=settings.decomposer_version,
                method=method,
                task_count=len(tasks),
                task_types=[task.task_type for task in tasks],
                llm_unavailable=llm_unavailable,
                fallback_used=fallback_used,
                confidence=_average_confidence(tasks),
            ),
            status=(
                DecompositionStatus.DEGRADED
                if fallback_used or llm_unavailable
                else DecompositionStatus.SUCCEEDED
            ),
        )

    async def _write_trace_safely(
        self,
        *,
        request: VetTaskDecomposeRequestDto,
        result: VetTaskDecomposeResultDto,
    ) -> VetTaskTraceWriteResultDto:
        """安全写入任务拆解 trace 摘要。

        :param request: 当前拆解请求。
        :param result: 当前拆解结果。
        :return: trace sink 返回的写入结果；异常时返回降级状态对象。
        """

        try:
            return await self._trace_sink.write_decomposition_summary(
                VetTaskDecomposeTraceRecordDto(
                    request_id=request.request_id,
                    trace_id=request.trace_id,
                    run_id=request.run_id,
                    session_id=request.session_id,
                    user_id=request.user_id,
                    current_pet_id=request.current_pet_id or "",
                    input_text_hash=build_text_hash(request.user_message),
                    attachment_count=len(request.attachments),
                    trace_summary=result.trace_summary,
                    params_version=request.params_version,
                    config_snapshot_id=request.config_snapshot_id,
                )
            )
        except Exception:
            return VetTaskTraceWriteResultDto(
                status=VetTaskTraceWriteStatus.DEGRADED,
                error_code="VET_TASK_DECOMPOSER_TRACE_WRITE_FAILED",
                retryable=True,
                detail="VetTaskDecomposer trace 写入发生未映射异常",
            )

    def _record_observability(
        self,
        *,
        request: VetTaskDecomposeRequestDto,
        result: VetTaskDecomposeResultDto | None,
        duration_seconds: float,
    ) -> None:
        """记录任务拆解指标与结构化事件。

        :param request: 当前拆解请求。
        :param result: 当前拆解结果；失败时为空。
        :param duration_seconds: 本次拆解耗时，单位为秒。
        :return: None。
        """

        if self._observability_provider is None:
            return
        status = result.status.value if result is not None else "failed"
        self._observability_provider.record_metric(
            metric_name="vet_task_decomposer_total",
            value=1,
            metric_type=MetricType.COUNTER,
            labels={"status": status},
            description="VetTaskDecomposer 拆解请求总数。",
        )
        self._observability_provider.record_metric(
            metric_name="vet_task_decomposer_duration_ms",
            value=duration_seconds * 1000,
            metric_type=MetricType.HISTOGRAM,
            labels={"status": status},
            description="VetTaskDecomposer 拆解耗时，单位为毫秒。",
        )
        if result is None:
            self._observability_provider.record_event(
                event_name="vet_task_decomposer.failed",
                component=_COMPONENT_NAME,
                level=StructuredLogLevel.ERROR,
                safe_fields={"request_id": request.request_id},
            )
            return
        self._observability_provider.record_metric(
            metric_name="vet_task_decomposer_task_count",
            value=len(result.tasks),
            metric_type=MetricType.HISTOGRAM,
            labels={"method": result.trace_summary.method.value},
            description="VetTaskDecomposer 每轮子任务数量分布。",
        )
        for task in result.tasks:
            self._observability_provider.record_metric(
                metric_name="vet_task_decomposer_task_type_total",
                value=1,
                metric_type=MetricType.COUNTER,
                labels={"task_type": task.task_type.value},
                description="VetTaskDecomposer 按任务类型统计的子任务数量。",
            )
        if result.trace_summary.fallback_used:
            self._observability_provider.record_metric(
                metric_name="vet_task_decomposer_single_passthrough_total",
                value=(
                    1
                    if result.trace_summary.method
                    is DecompositionMethod.SINGLE_PASSTHROUGH
                    else 0
                ),
                metric_type=MetricType.COUNTER,
                labels={"method": result.trace_summary.method.value},
                description="VetTaskDecomposer 单任务原文透传次数。",
            )


def create_default_vet_task_decomposer(
    *,
    runtime_config_provider: RuntimeConfigProvider,
    agent_runner: AgentRunner | None = None,
    local_fallback: VetTaskLocalFallback | None = None,
    trace_sink: VetTaskDecomposerTraceSink | None = None,
    observability_provider: ObservabilityProvider | None = None,
) -> DefaultVetTaskDecomposer:
    """创建默认 VetTaskDecomposer 服务。

    :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
    :param agent_runner: 可选 AgentRunner 结构化拆解端口。
    :param local_fallback: 可选本地 span fallback 端口。
    :param trace_sink: 可选拆解摘要 trace sink。
    :param observability_provider: 可选 Observability provider。
    :return: 已装配默认 TODO 降级依赖的 VetTaskDecomposer 服务。
    """

    return DefaultVetTaskDecomposer(
        runtime_config_provider=runtime_config_provider,
        agent_runner=agent_runner,
        local_fallback=local_fallback,
        trace_sink=trace_sink,
        observability_provider=observability_provider,
    )


__all__: tuple[str, ...] = (
    "DefaultVetTaskDecomposer",
    "VetTaskDecomposer",
    "create_default_vet_task_decomposer",
)

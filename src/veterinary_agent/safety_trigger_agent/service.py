##################################################################################################
# 文件: src/veterinary_agent/safety_trigger_agent/service.py
# 作用: 实现 SafetyTriggerAgent 应用内服务，编排急症 brief、RAG 禁用证明、确认规划、写作、自检与兜底。
# 边界: 不执行输入侧 SAF 判决、不调用 RAG、不直接发布用户回复、不替代输出安全审查或确定性兜底门。
##################################################################################################

import asyncio
from collections.abc import Mapping
from hashlib import sha256
import json
import re
from time import perf_counter
from typing import Protocol

from veterinary_agent.agent_runner import (
    AgentRunRequestDto,
    AgentRunResultDto,
    AgentRunStatus,
    AgentRunner,
)
from veterinary_agent.config import (
    RuntimeConfigError,
    RuntimeConfigProvider,
    RuntimeConfigSnapshot,
    SafetyTriggerAgentSettings,
)
from veterinary_agent.observability import (
    MetricType,
    ObservabilityProvider,
    StructuredLogLevel,
)
from veterinary_agent.safety_trigger_agent.dto import (
    EmergencyBriefDto,
    KeyConfirmationPlanDto,
    SafetyRagPolicySummaryDto,
    SafetyRequirementSetDto,
    SafetySignalSummaryDto,
    SafetyTraceWriteResultDto,
    SafetyTriggerDraftDto,
    SafetyTriggerRequestDto,
    SafetyTriggerSelfCheckSummaryDto,
    SafetyTriggerTracePatchDto,
    SafetyTriggerTraceRecordDto,
)
from veterinary_agent.safety_trigger_agent.enums import (
    ConfirmationMode,
    EmergencyHintCode,
    SafetyTraceWriteStatus,
    SafetyTriggerDraftStatus,
    SafetyTriggerErrorCode,
    SafetyTriggerOperation,
)
from veterinary_agent.safety_trigger_agent.errors import SafetyTriggerError
from veterinary_agent.safety_trigger_agent.ports import (
    SafetyToolPermissionPort,
    TodoSafetyToolPermissionPort,
)
from veterinary_agent.safety_trigger_agent.trace import (
    SafetyTriggerTraceSink,
    TodoSafetyTriggerTraceSink,
)
from veterinary_agent.vet_context_builder import (
    ContextCompressionStrategy,
    VetExecutorKey,
    VetGenerationProfile,
    VetPromptBlockType,
    to_agent_prompt_blocks,
)

_COMPONENT_NAME = "safety_trigger_agent"
_SAFETY_PROFILE = VetGenerationProfile.SAFETY_TRIGGER.value
_SAFETY_EXECUTOR = VetExecutorKey.SAFETY_TRIGGER.value
_T4_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mg|毫克|ml|毫升|片|粒|iu|μg|ug)\s*(?:/|每)?\s*(?:kg|公斤)?",
    re.IGNORECASE,
)
_RAG_TEXT_MARKERS: tuple[str, ...] = (
    "rag",
    "知识库",
    "检索结果",
    "参考文献",
    "参考资料",
    "来源：",
    "http://",
    "https://",
)


class SafetyTriggerAgent(Protocol):
    """SafetyTriggerAgent 应用内服务接口。"""

    def is_ready(self) -> bool:
        """判断急症组件是否具备执行条件。

        :return: 若 RuntimeConfig 可读取且组件已启用则返回 True。
        """

        ...

    async def generate_draft(
        self,
        request: SafetyTriggerRequestDto,
    ) -> SafetyTriggerDraftDto:
        """生成急症简版结构化草稿。

        :param request: 当前急症生成请求。
        :return: 待输出安全审查的急症草稿。
        """

        ...


def _elapsed_ms(started_at: float) -> int:
    """计算从单调时钟起点到当前的毫秒数。

    :param started_at: ``perf_counter`` 记录的起点。
    :return: 四舍五入后的非负毫秒数。
    """

    return max(0, round((perf_counter() - started_at) * 1000))


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

    return value if isinstance(value, bool) else default


def _stable_hash(value: str) -> str:
    """计算稳定文本 hash。

    :param value: 待计算 hash 的文本。
    :return: 带 sha256 前缀的十六进制摘要。
    """

    return f"sha256:{sha256(value.encode('utf-8')).hexdigest()}"


def _parse_json_mapping(value: str) -> Mapping[str, object] | None:
    """将 JSON 文本安全解析为映射。

    :param value: 待解析的 JSON 文本。
    :return: 字符串键映射；解析失败或类型不符时返回 None。
    """

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return _as_mapping(parsed)


def _unique_strings(values: list[str]) -> list[str]:
    """按首次出现顺序去重字符串列表。

    :param values: 原始字符串列表。
    :return: 去重后的字符串列表。
    """

    return list(dict.fromkeys(value for value in values if value.strip()))


class DefaultSafetyTriggerAgent:
    """SafetyTriggerAgent 默认应用内实现。"""

    def __init__(
        self,
        *,
        runtime_config_provider: RuntimeConfigProvider,
        agent_runner: AgentRunner | None = None,
        tool_permission_port: SafetyToolPermissionPort | None = None,
        trace_sink: SafetyTriggerTraceSink | None = None,
        observability_provider: ObservabilityProvider | None = None,
    ) -> None:
        """初始化 SafetyTriggerAgent 默认实现。

        :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
        :param agent_runner: 可选 AgentRunner 端口；缺失时进入保守兜底草稿。
        :param tool_permission_port: 可选工具权限证明端口；缺失时使用 TODO 降级空壳。
        :param trace_sink: 可选急症 trace 写入端口；缺失时使用 TODO 空壳。
        :param observability_provider: 可选 Observability provider。
        :return: None。
        """

        self._runtime_config_provider = runtime_config_provider
        self._agent_runner = agent_runner
        self._tool_permission_port = (
            tool_permission_port or TodoSafetyToolPermissionPort()
        )
        self._trace_sink = trace_sink or TodoSafetyTriggerTraceSink()
        self._observability_provider = observability_provider

    def is_ready(self) -> bool:
        """判断急症组件是否具备执行条件。

        :return: 若 RuntimeConfig 可读取且 SafetyTriggerAgent 已启用则返回 True。
        """

        if not self._runtime_config_provider.is_ready():
            return False
        try:
            snapshot = self._runtime_config_provider.current_snapshot()
        except RuntimeConfigError:
            return False
        return snapshot.safety_trigger.enabled

    async def generate_draft(
        self,
        request: SafetyTriggerRequestDto,
    ) -> SafetyTriggerDraftDto:
        """生成急症简版结构化草稿。

        :param request: 当前急症生成请求。
        :return: 待输出安全审查的急症草稿。
        :raises SafetyTriggerError: 当前置契约或强依赖不满足执行条件时抛出。
        """

        started_at = perf_counter()
        draft: SafetyTriggerDraftDto | None = None
        try:
            snapshot = self._load_config_snapshot(request=request)
            settings = snapshot.safety_trigger
            self._validate_request_or_raise(request=request, settings=settings)
            requirements = self._build_requirement_set(settings=settings)
            brief = self._build_emergency_brief(request=request, settings=settings)
            rag_policy = await self._verify_rag_disabled(
                request=request,
                settings=settings,
            )
            degraded_flags = self._rag_degraded_flags(rag_policy=rag_policy)
            confirmation_plan = await self._plan_confirmation(
                request=request,
                brief=brief,
                requirements=requirements,
                settings=settings,
                allow_agent=rag_policy.verified,
                degraded_flags=degraded_flags,
            )
            writer_output = await self._run_writer(
                request=request,
                brief=brief,
                confirmation_plan=confirmation_plan,
                requirements=requirements,
                settings=settings,
                allow_agent=rag_policy.verified,
                degraded_flags=degraded_flags,
            )
            draft = self._build_draft_from_writer_or_fallback(
                request=request,
                brief=brief,
                confirmation_plan=confirmation_plan,
                requirements=requirements,
                settings=settings,
                writer_output=writer_output,
                degraded_flags=degraded_flags,
            )
            trace_result = await self._write_trace_safely(
                request=request,
                draft=draft,
            )
            draft = draft.model_copy(
                update={"trace_delivery_status": trace_result.status}
            )
            return draft
        except SafetyTriggerError:
            raise
        except RuntimeConfigError as exc:
            raise SafetyTriggerError(
                code=(SafetyTriggerErrorCode.SAFETY_TRIGGER_RUNTIME_CONFIG_UNAVAILABLE),
                operation=SafetyTriggerOperation.GENERATE_DRAFT,
                message="SafetyTriggerAgent 无法读取有效 RuntimeConfig 快照",
                retryable=exc.retryable,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={"runtime_config_error_code": exc.code.value},
            ) from exc
        except Exception as exc:
            raise SafetyTriggerError(
                code=SafetyTriggerErrorCode.SAFETY_TRIGGER_INTERNAL_ERROR,
                operation=SafetyTriggerOperation.GENERATE_DRAFT,
                message="SafetyTriggerAgent 发生未映射内部错误",
                retryable=True,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={"exception_type": type(exc).__name__},
            ) from exc
        finally:
            self._record_observability(
                request=request,
                draft=draft,
                duration_ms=_elapsed_ms(started_at),
            )

    def _load_config_snapshot(
        self,
        *,
        request: SafetyTriggerRequestDto,
    ) -> RuntimeConfigSnapshot:
        """读取并校验当前急症请求使用的配置快照。

        :param request: 当前急症请求。
        :return: 与请求版本一致且启用 SafetyTriggerAgent 的配置快照。
        :raises SafetyTriggerError: 当配置不可用、未启用或版本不一致时抛出。
        """

        if not self._runtime_config_provider.is_ready():
            raise SafetyTriggerError(
                code=SafetyTriggerErrorCode.SAFETY_TRIGGER_NOT_READY,
                operation=SafetyTriggerOperation.GENERATE_DRAFT,
                message="RuntimeConfig provider 未就绪",
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )
        snapshot = self._runtime_config_provider.current_snapshot()
        if not snapshot.safety_trigger.enabled:
            raise SafetyTriggerError(
                code=SafetyTriggerErrorCode.SAFETY_TRIGGER_NOT_READY,
                operation=SafetyTriggerOperation.GENERATE_DRAFT,
                message="SafetyTriggerAgent 已被配置关闭",
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )
        if request.params_version != snapshot.params_version:
            raise SafetyTriggerError(
                code=SafetyTriggerErrorCode.SAFETY_TRIGGER_CONTEXT_MISSING,
                operation=SafetyTriggerOperation.GENERATE_DRAFT,
                message="请求参数版本与当前 RuntimeConfig 不一致",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={
                    "request_params_version": request.params_version,
                    "snapshot_params_version": snapshot.params_version,
                },
            )
        return snapshot

    def _validate_request_or_raise(
        self,
        *,
        request: SafetyTriggerRequestDto,
        settings: SafetyTriggerAgentSettings,
    ) -> None:
        """校验急症请求剖面、宠物作用域、上下文和 RAG 禁令。

        :param request: 当前急症请求。
        :param settings: 当前急症配置；用于证明配置已解析。
        :return: None。
        :raises SafetyTriggerError: 当前置契约不满足时抛出稳定错误。
        """

        del settings
        if request.current_pet_id is None:
            raise SafetyTriggerError(
                code=SafetyTriggerErrorCode.SAFETY_TRIGGER_MISSING_CURRENT_PET_ID,
                operation=SafetyTriggerOperation.GENERATE_DRAFT,
                message="急症请求缺少 current_pet_id",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )
        if request.generation_profile != _SAFETY_PROFILE:
            raise SafetyTriggerError(
                code=SafetyTriggerErrorCode.SAFETY_TRIGGER_PROFILE_MISMATCH,
                operation=SafetyTriggerOperation.GENERATE_DRAFT,
                message="急症组件仅接受 generation_profile=safety_trigger",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={"generation_profile": request.generation_profile},
            )
        self._validate_context_or_raise(request=request)
        self._validate_assessment_or_raise(request=request)
        self._validate_no_inbound_rag_or_raise(request=request)

    def _validate_context_or_raise(self, *, request: SafetyTriggerRequestDto) -> None:
        """校验急症上下文 bundle 与请求作用域一致。

        :param request: 当前急症请求。
        :return: None。
        :raises SafetyTriggerError: 上下文缺失或作用域不一致时抛出。
        """

        context_profile = (
            request.context.generation_profile.value
            if request.context.generation_profile is not None
            else None
        )
        if context_profile != _SAFETY_PROFILE:
            raise SafetyTriggerError(
                code=SafetyTriggerErrorCode.SAFETY_TRIGGER_PROFILE_MISMATCH,
                operation=SafetyTriggerOperation.GENERATE_DRAFT,
                message="急症上下文不是 safety_trigger 剖面",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={"context_generation_profile": context_profile},
            )
        if request.executor_key != _SAFETY_EXECUTOR:
            raise SafetyTriggerError(
                code=SafetyTriggerErrorCode.SAFETY_TRIGGER_PROFILE_MISMATCH,
                operation=SafetyTriggerOperation.GENERATE_DRAFT,
                message="急症组件仅接受 safety_trigger 执行器",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={"executor_key": request.executor_key},
            )
        if request.context.executor_key is not VetExecutorKey.SAFETY_TRIGGER:
            raise SafetyTriggerError(
                code=SafetyTriggerErrorCode.SAFETY_TRIGGER_PROFILE_MISMATCH,
                operation=SafetyTriggerOperation.GENERATE_DRAFT,
                message="急症上下文执行器不是 safety_trigger",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )
        if (
            request.context.compression_audit.compression_strategy
            is not ContextCompressionStrategy.SAFETY_MINIMAL
        ):
            raise SafetyTriggerError(
                code=SafetyTriggerErrorCode.SAFETY_TRIGGER_CONTEXT_MISSING,
                operation=SafetyTriggerOperation.GENERATE_DRAFT,
                message="急症上下文必须使用 safety_minimal 压缩策略",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )
        if request.current_pet_id != request.context.current_pet_id:
            raise SafetyTriggerError(
                code=SafetyTriggerErrorCode.SAFETY_TRIGGER_PET_CONTEXT_INVALID,
                operation=SafetyTriggerOperation.GENERATE_DRAFT,
                message="急症请求宠物 ID 与上下文宠物 ID 不一致",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={
                    "request_pet_id": request.current_pet_id,
                    "context_pet_id": request.context.current_pet_id,
                },
            )
        if request.task_id != request.context.task_id:
            raise SafetyTriggerError(
                code=SafetyTriggerErrorCode.SAFETY_TRIGGER_CONTEXT_MISSING,
                operation=SafetyTriggerOperation.GENERATE_DRAFT,
                message="急症请求 task_id 与上下文 task_id 不一致",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )

    def _validate_assessment_or_raise(
        self, *, request: SafetyTriggerRequestDto
    ) -> None:
        """校验急症请求包含上游输入安全信号摘要。

        :param request: 当前急症请求。
        :return: None。
        :raises SafetyTriggerError: 急症信号缺失时抛出。
        """

        if self._signal_items(request=request):
            return
        intent = _read_string(request.assessment_summary.get("intent"))
        if intent == "ACUTE_EVENT":
            return
        raise SafetyTriggerError(
            code=SafetyTriggerErrorCode.SAFETY_TRIGGER_SIGNAL_MISSING,
            operation=SafetyTriggerOperation.GENERATE_DRAFT,
            message="急症请求缺少 SAF 或 ACUTE_EVENT 信号摘要",
            retryable=False,
            request_id=request.request_id,
            trace_id=request.trace_id,
            task_id=request.task_id,
        )

    def _validate_no_inbound_rag_or_raise(
        self,
        *,
        request: SafetyTriggerRequestDto,
    ) -> None:
        """校验入参没有携带 RAG 证据或检索引用。

        :param request: 当前急症请求。
        :return: None。
        :raises SafetyTriggerError: 检出 RAG 证据时抛出。
        """

        retrieval_ids = _as_list(request.assessment_summary.get("retrieval_ids"))
        rag_invoked = _read_bool(
            request.assessment_summary.get("rag_invoked"),
            default=False,
        )
        if retrieval_ids or rag_invoked:
            raise SafetyTriggerError(
                code=SafetyTriggerErrorCode.SAFETY_TRIGGER_RAG_FORBIDDEN,
                operation=SafetyTriggerOperation.GENERATE_DRAFT,
                message="急症剖面入参不得携带 RAG 证据或检索引用",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={
                    "retrieval_id_count": len(retrieval_ids),
                    "rag_invoked": rag_invoked,
                },
            )

    def _build_requirement_set(
        self,
        *,
        settings: SafetyTriggerAgentSettings,
    ) -> SafetyRequirementSetDto:
        """构建急症生成最小安全要求集合。

        :param settings: 当前急症配置。
        :return: 急症安全要求 DTO。
        """

        return SafetyRequirementSetDto(
            require_disclaimer=settings.requirements.require_disclaimer,
            max_confirmation_count=settings.requirements.max_confirmation_count,
            forbidden_content_tags=list(settings.requirements.forbidden_content_tags),
        )

    def _build_emergency_brief(
        self,
        *,
        request: SafetyTriggerRequestDto,
        settings: SafetyTriggerAgentSettings,
    ) -> EmergencyBriefDto:
        """构建急症写作使用的最小 brief。

        :param request: 当前急症请求。
        :param settings: 当前急症配置。
        :return: 急症最小 brief。
        """

        del settings
        signals = self._signal_summaries(request=request)
        hints = self._hint_codes(signals=signals, request=request)
        return EmergencyBriefDto(
            user_text_ref=f"{request.task_id}:current_task",
            species_scope=self._species_scope(request=request),
            signal_summaries=signals,
            realtime_markers=self._realtime_markers(request=request),
            risk_entity_summaries=self._risk_entities(request=request),
            emergency_hint_codes=hints,
            multi_task_first_segment_required=True,
            generation_constraints=[
                "首段必须先给就医或联系急诊兽医导向",
                "最多 0-1 个关键确认或记录项",
                "不得调用 RAG 或输出文献引用",
                "不得输出完整鉴别诊断或处方级用药方案",
                "SAF-01 必须点名风险物；未知时使用疑似风险物泛化表述",
            ],
        )

    async def _verify_rag_disabled(
        self,
        *,
        request: SafetyTriggerRequestDto,
        settings: SafetyTriggerAgentSettings,
    ) -> SafetyRagPolicySummaryDto:
        """确认急症链路没有 RAG 工具权限。

        :param request: 当前急症请求。
        :param settings: 当前急症配置。
        :return: RAG 禁用证明摘要。
        """

        agent_ids = [
            settings.confirmation_planner_agent_id,
            settings.writer_agent_id,
        ]
        try:
            return await self._tool_permission_port.verify_no_rag_tools(
                request=request,
                agent_ids=agent_ids,
            )
        except Exception as exc:
            return SafetyRagPolicySummaryDto(
                verified=False,
                degraded_reason=f"SAFETY_RAG_PERMISSION_ERROR:{type(exc).__name__}",
            )

    def _rag_degraded_flags(
        self,
        *,
        rag_policy: SafetyRagPolicySummaryDto,
    ) -> list[str]:
        """根据 RAG 权限摘要构建降级标记。

        :param rag_policy: RAG 禁用证明摘要。
        :return: 当前 RAG 权限证明产生的降级标记。
        """

        if rag_policy.verified:
            return []
        return [rag_policy.degraded_reason or "rag_permission_unverified"]

    async def _plan_confirmation(
        self,
        *,
        request: SafetyTriggerRequestDto,
        brief: EmergencyBriefDto,
        requirements: SafetyRequirementSetDto,
        settings: SafetyTriggerAgentSettings,
        allow_agent: bool,
        degraded_flags: list[str],
    ) -> KeyConfirmationPlanDto:
        """规划急症关键确认或记录项。

        :param request: 当前急症请求。
        :param brief: 急症最小 brief。
        :param requirements: 急症安全要求集合。
        :param settings: 当前急症配置。
        :param allow_agent: 是否允许调用规划 Agent。
        :param degraded_flags: 本轮可追加的降级标记列表。
        :return: 已归一化的关键确认计划。
        """

        deterministic = self._deterministic_confirmation_plan(brief=brief)
        if (
            not allow_agent
            or self._agent_runner is None
            or not self._agent_runner.is_ready()
        ):
            degraded_flags.append("confirmation_planner_unavailable")
            return deterministic
        try:
            result = await asyncio.wait_for(
                self._agent_runner.run_agent(
                    AgentRunRequestDto(
                        run_id=f"{request.run_id}:safety_confirmation_planner",
                        trace_id=request.trace_id,
                        request_id=request.request_id,
                        session_id=request.session_id,
                        user_id=request.user_id,
                        agent_id=settings.confirmation_planner_agent_id,
                        agent_version=settings.confirmation_planner_agent_version,
                        task_input={
                            "emergency_brief": brief.model_dump(mode="json"),
                            "requirements": requirements.model_dump(mode="json"),
                        },
                        prompt_blocks=to_agent_prompt_blocks(request.context),
                        runtime_options={
                            "generation_profile": _SAFETY_PROFILE,
                            "stage": "safety_confirmation_planner",
                            "rag_allowed": False,
                        },
                    )
                ),
                timeout=settings.timeouts.planner_seconds,
            )
        except TimeoutError:
            degraded_flags.append("confirmation_planner_timeout")
            return deterministic
        if result.status is not AgentRunStatus.SUCCEEDED or not result.schema_valid:
            degraded_flags.append("confirmation_planner_schema_invalid")
            return deterministic
        parsed = self._confirmation_plan_from_output(result=result)
        if parsed is None:
            degraded_flags.append("confirmation_planner_output_invalid")
            return deterministic
        return self._normalize_confirmation_plan(
            plan=parsed,
            requirements=requirements,
        )

    def _deterministic_confirmation_plan(
        self,
        *,
        brief: EmergencyBriefDto,
    ) -> KeyConfirmationPlanDto:
        """构建保守确定性关键确认计划。

        :param brief: 急症最小 brief。
        :return: 不阻塞就医导向的确认或记录计划。
        """

        if self._is_toxic_brief(brief=brief):
            entity = (
                brief.risk_entity_summaries[0] if brief.risk_entity_summaries else None
            )
            text = (
                f"如果能安全做到，请带上 {entity} 的包装、照片、摄入时间和大概量。"
                if entity is not None
                else "如果能安全做到，请带上疑似风险物或药物的包装、照片、摄入时间和大概量。"
            )
            return KeyConfirmationPlanDto(
                mode=ConfirmationMode.RECORD_AND_GO,
                confirmation_text=text,
                blocks_vet_direction=False,
                reason_code="saf01_record_packaging",
            )
        if brief.emergency_hint_codes and (
            EmergencyHintCode.SEIZURE_HINT in brief.emergency_hint_codes
        ):
            return KeyConfirmationPlanDto(
                mode=ConfirmationMode.RECORD_AND_GO,
                confirmation_text="如果方便，请记录发作开始时间；能安全拍短视频也可以带给兽医看。",
                blocks_vet_direction=False,
                reason_code="record_event_time",
            )
        return KeyConfirmationPlanDto(
            mode=ConfirmationMode.NO_QUESTION,
            confirmation_text=None,
            blocks_vet_direction=False,
            reason_code="urgent_direction_first",
        )

    def _confirmation_plan_from_output(
        self,
        *,
        result: AgentRunResultDto,
    ) -> KeyConfirmationPlanDto | None:
        """从确认规划 Agent 输出中解析计划。

        :param result: AgentRunner 结构化结果。
        :return: 可用确认计划；无法解析时返回 None。
        """

        raw_plan = _as_mapping(result.parsed_output.get("confirmation_plan"))
        source = raw_plan if raw_plan is not None else result.parsed_output
        mode_value = _read_string(source.get("mode"))
        if mode_value is None:
            return None
        try:
            mode = ConfirmationMode(mode_value)
        except ValueError:
            return None
        try:
            return KeyConfirmationPlanDto(
                mode=mode,
                confirmation_text=_read_string(source.get("confirmation_text")),
                blocks_vet_direction=_read_bool(
                    source.get("blocks_vet_direction"),
                    default=False,
                ),
                reason_code=_read_string(source.get("reason_code")) or "agent_planned",
            )
        except ValueError:
            return None

    def _normalize_confirmation_plan(
        self,
        *,
        plan: KeyConfirmationPlanDto,
        requirements: SafetyRequirementSetDto,
    ) -> KeyConfirmationPlanDto:
        """归一化确认计划到急症问题预算内。

        :param plan: 原始确认计划。
        :param requirements: 急症安全要求集合。
        :return: 合规的确认计划。
        """

        if requirements.max_confirmation_count <= 0:
            return KeyConfirmationPlanDto(
                mode=ConfirmationMode.NO_QUESTION,
                confirmation_text=None,
                blocks_vet_direction=False,
                reason_code="question_budget_zero",
            )
        if plan.blocks_vet_direction:
            return KeyConfirmationPlanDto(
                mode=ConfirmationMode.NO_QUESTION,
                confirmation_text=None,
                blocks_vet_direction=False,
                reason_code="planner_blocked_vet_direction",
            )
        return plan

    async def _run_writer(
        self,
        *,
        request: SafetyTriggerRequestDto,
        brief: EmergencyBriefDto,
        confirmation_plan: KeyConfirmationPlanDto,
        requirements: SafetyRequirementSetDto,
        settings: SafetyTriggerAgentSettings,
        allow_agent: bool,
        degraded_flags: list[str],
    ) -> AgentRunResultDto | None:
        """执行急症写作 Agent。

        :param request: 当前急症请求。
        :param brief: 急症最小 brief。
        :param confirmation_plan: 关键确认计划。
        :param requirements: 急症安全要求集合。
        :param settings: 当前急症配置。
        :param allow_agent: 是否允许调用写作 Agent。
        :param degraded_flags: 本轮可追加的降级标记列表。
        :return: 成功写作结果；不可用或不合格时返回 None。
        """

        if not allow_agent:
            degraded_flags.append("writer_blocked_by_rag_permission")
            return None
        if self._agent_runner is None or not self._agent_runner.is_ready():
            degraded_flags.append("writer_unavailable")
            return None
        try:
            result = await asyncio.wait_for(
                self._agent_runner.run_agent(
                    AgentRunRequestDto(
                        run_id=f"{request.run_id}:safety_writer",
                        trace_id=request.trace_id,
                        request_id=request.request_id,
                        session_id=request.session_id,
                        user_id=request.user_id,
                        agent_id=settings.writer_agent_id,
                        agent_version=settings.writer_agent_version,
                        task_input={
                            "task_id": request.task_id,
                            "current_pet_id": request.current_pet_id,
                            "normalized_query": request.normalized_query,
                            "emergency_brief": brief.model_dump(mode="json"),
                            "confirmation_plan": confirmation_plan.model_dump(
                                mode="json"
                            ),
                            "requirements": requirements.model_dump(mode="json"),
                            "rag_invoked": False,
                            "retrieval_ids": [],
                        },
                        prompt_blocks=to_agent_prompt_blocks(request.context),
                        runtime_options={
                            "generation_profile": _SAFETY_PROFILE,
                            "stage": "safety_trigger_writer",
                            "rag_allowed": False,
                        },
                    )
                ),
                timeout=settings.timeouts.writer_seconds,
            )
        except TimeoutError:
            degraded_flags.append("writer_timeout")
            return None
        if result.status is not AgentRunStatus.SUCCEEDED or not result.schema_valid:
            degraded_flags.append("writer_schema_invalid")
            return None
        return result

    def _build_draft_from_writer_or_fallback(
        self,
        *,
        request: SafetyTriggerRequestDto,
        brief: EmergencyBriefDto,
        confirmation_plan: KeyConfirmationPlanDto,
        requirements: SafetyRequirementSetDto,
        settings: SafetyTriggerAgentSettings,
        writer_output: AgentRunResultDto | None,
        degraded_flags: list[str],
    ) -> SafetyTriggerDraftDto:
        """从写作输出构建草稿，必要时切换兜底草稿。

        :param request: 当前急症请求。
        :param brief: 急症最小 brief。
        :param confirmation_plan: 关键确认计划。
        :param requirements: 急症安全要求集合。
        :param settings: 当前急症配置。
        :param writer_output: 可选写作 Agent 输出。
        :param degraded_flags: 本轮降级标记。
        :return: 最终急症草稿 DTO。
        """

        if writer_output is None:
            return self._build_fallback_draft(
                request=request,
                brief=brief,
                confirmation_plan=confirmation_plan,
                settings=settings,
                degraded_flags=[*degraded_flags, "fallback_writer_unavailable"],
            )
        draft = self._draft_from_writer_output(
            request=request,
            brief=brief,
            confirmation_plan=confirmation_plan,
            settings=settings,
            writer_output=writer_output,
            degraded_flags=degraded_flags,
        )
        if draft is None:
            return self._build_fallback_draft(
                request=request,
                brief=brief,
                confirmation_plan=confirmation_plan,
                settings=settings,
                degraded_flags=[*degraded_flags, "fallback_writer_output_invalid"],
            )
        self_check = self._self_check(
            draft_text=draft.draft_response,
            brief=brief,
            confirmation_plan=confirmation_plan,
            requirements=requirements,
            settings=settings,
        )
        if self_check.fallback_recommended:
            return self._build_fallback_draft(
                request=request,
                brief=brief,
                confirmation_plan=confirmation_plan,
                settings=settings,
                degraded_flags=[*degraded_flags, *self_check.issue_codes],
            )
        return draft.model_copy(update={"self_check": self_check})

    def _draft_from_writer_output(
        self,
        *,
        request: SafetyTriggerRequestDto,
        brief: EmergencyBriefDto,
        confirmation_plan: KeyConfirmationPlanDto,
        settings: SafetyTriggerAgentSettings,
        writer_output: AgentRunResultDto,
        degraded_flags: list[str],
    ) -> SafetyTriggerDraftDto | None:
        """从写作 Agent 输出构建急症草稿。

        :param request: 当前急症请求。
        :param brief: 急症最小 brief。
        :param confirmation_plan: 关键确认计划。
        :param settings: 当前急症配置。
        :param writer_output: 写作 Agent 结构化输出。
        :param degraded_flags: 本轮降级标记。
        :return: 急症草稿；输出缺少正文时返回 None。
        """

        text = _read_string(writer_output.parsed_output.get("draft_response"))
        if text is None:
            return None
        trace_patch = self._trace_patch(
            settings=settings,
            brief=brief,
            confirmation_plan=confirmation_plan,
            template_fallback_used=False,
            degraded_flags=degraded_flags,
        )
        return SafetyTriggerDraftDto(
            task_id=request.task_id,
            current_pet_id=request.current_pet_id or request.context.current_pet_id,
            status=SafetyTriggerDraftStatus.DRAFT_READY,
            draft_response=text[: settings.requirements.max_draft_chars],
            draft_response_ref=f"draft:{request.trace_id}:{request.task_id}",
            emergency_brief=brief,
            confirmation_plan=confirmation_plan,
            urgency_statement=(
                _read_string(writer_output.parsed_output.get("urgency_statement"))
                or "当前描述存在急症风险。"
            ),
            vet_direction=(
                _read_string(writer_output.parsed_output.get("vet_direction"))
                or "建议尽快联系附近宠物医院或急诊兽医。"
            ),
            safe_actions=self._list_strings(
                writer_output.parsed_output.get("safe_actions")
            ),
            forbidden_actions=self._list_strings(
                writer_output.parsed_output.get("forbidden_actions")
            ),
            info_to_prepare=self._list_strings(
                writer_output.parsed_output.get("info_to_prepare")
            ),
            rag_invoked=False,
            retrieval_ids=[],
            self_check=self._passing_self_check(),
            trace_patch=trace_patch,
        )

    def _build_fallback_draft(
        self,
        *,
        request: SafetyTriggerRequestDto,
        brief: EmergencyBriefDto,
        confirmation_plan: KeyConfirmationPlanDto,
        settings: SafetyTriggerAgentSettings,
        degraded_flags: list[str],
    ) -> SafetyTriggerDraftDto:
        """构建保守急症兜底草稿。

        :param request: 当前急症请求。
        :param brief: 急症最小 brief。
        :param confirmation_plan: 关键确认计划。
        :param settings: 当前急症配置。
        :param degraded_flags: 本轮降级标记。
        :return: 急症兜底草稿。
        """

        risk_phrase = self._fallback_risk_phrase(brief=brief)
        vet_direction = "建议现在就联系附近宠物医院或急诊兽医，并尽快线下就医。"
        urgency = f"当前描述可能涉及急症风险，{risk_phrase} 需要由线下兽医尽快评估。"
        safe_actions = [
            "保持环境安静，避免奔跑、喂食或强行活动。",
            "转运途中注意保暖和通风，尽量减少搬动造成的二次伤害。",
        ]
        forbidden_actions = [
            "不要自行喂人用药或处方药。",
            "不要自行催吐、灌水、灌食或等待线上确认后再行动。",
        ]
        info_to_prepare = [
            "准备症状开始时间、变化过程、已接触物品或药物包装。",
            "如有视频、照片、化验单或既往用药记录，请带给兽医查看。",
        ]
        if confirmation_plan.confirmation_text is not None:
            info_to_prepare.append(confirmation_plan.confirmation_text)
        draft_response = "\n\n".join(
            [
                f"{vet_direction}{urgency}",
                "在联系或前往医院途中，可以做的低风险准备：" + "；".join(safe_actions),
                "请避免：" + "；".join(forbidden_actions),
                "给兽医准备：" + "；".join(info_to_prepare),
                "以上只能作为线上安全提示，不能替代线下兽医检查和处置。",
            ]
        )
        trace_patch = self._trace_patch(
            settings=settings,
            brief=brief,
            confirmation_plan=confirmation_plan,
            template_fallback_used=True,
            degraded_flags=[*degraded_flags, "template_fallback_used"],
        )
        self_check = self._self_check(
            draft_text=draft_response,
            brief=brief,
            confirmation_plan=confirmation_plan,
            requirements=self._build_requirement_set(settings=settings),
            settings=settings,
        )
        return SafetyTriggerDraftDto(
            task_id=request.task_id,
            current_pet_id=request.current_pet_id or request.context.current_pet_id,
            status=SafetyTriggerDraftStatus.FALLBACK_READY,
            draft_response=draft_response,
            draft_response_ref=f"draft:{request.trace_id}:{request.task_id}",
            emergency_brief=brief,
            confirmation_plan=confirmation_plan,
            urgency_statement=urgency,
            vet_direction=vet_direction,
            safe_actions=safe_actions,
            forbidden_actions=forbidden_actions,
            info_to_prepare=info_to_prepare,
            rag_invoked=False,
            retrieval_ids=[],
            self_check=self_check,
            trace_patch=trace_patch,
        )

    def _self_check(
        self,
        *,
        draft_text: str,
        brief: EmergencyBriefDto,
        confirmation_plan: KeyConfirmationPlanDto,
        requirements: SafetyRequirementSetDto,
        settings: SafetyTriggerAgentSettings,
    ) -> SafetyTriggerSelfCheckSummaryDto:
        """执行急症草稿确定性自检。

        :param draft_text: 待检查的草稿正文。
        :param brief: 急症最小 brief。
        :param confirmation_plan: 关键确认计划。
        :param requirements: 急症安全要求集合。
        :param settings: 当前急症配置。
        :return: 急症草稿自检摘要。
        """

        issue_codes: list[str] = []
        vet_direction_present = self._vet_direction_present(draft_text=draft_text)
        if not vet_direction_present:
            issue_codes.append("vet_direction_missing")
        confirmation_count_valid = self._confirmation_count_valid(
            draft_text=draft_text,
            confirmation_plan=confirmation_plan,
            requirements=requirements,
        )
        if not confirmation_count_valid:
            issue_codes.append("confirmation_limit_exceeded")
        rag_invocation_absent = self._rag_invocation_absent(draft_text=draft_text)
        if not rag_invocation_absent:
            issue_codes.append("rag_reference_detected")
        t4_risk_detected = bool(_T4_PATTERN.search(draft_text))
        if t4_risk_detected:
            issue_codes.append("t4_risk_detected")
        differential_overexpanded = self._differential_overexpanded(
            draft_text=draft_text,
            settings=settings,
        )
        if differential_overexpanded:
            issue_codes.append("differential_overexpanded")
        disclaimer_present = self._disclaimer_present(draft_text=draft_text)
        if requirements.require_disclaimer and not disclaimer_present:
            issue_codes.append("disclaimer_missing")
        saf01_risk_entity_named = self._saf01_risk_entity_named(
            draft_text=draft_text,
            brief=brief,
        )
        if not saf01_risk_entity_named:
            issue_codes.append("saf01_risk_entity_missing")
        fallback_recommended = bool(issue_codes)
        return SafetyTriggerSelfCheckSummaryDto(
            vet_direction_present=vet_direction_present,
            confirmation_count_valid=confirmation_count_valid,
            rag_invocation_absent=rag_invocation_absent,
            t4_risk_detected=t4_risk_detected,
            differential_overexpanded=differential_overexpanded,
            saf01_risk_entity_named=saf01_risk_entity_named,
            disclaimer_present=disclaimer_present,
            fallback_recommended=fallback_recommended,
            issue_codes=_unique_strings(issue_codes),
        )

    def _passing_self_check(self) -> SafetyTriggerSelfCheckSummaryDto:
        """构建临时通过状态自检摘要。

        :return: 默认通过的自检摘要；正式自检会在草稿合成后覆盖。
        """

        return SafetyTriggerSelfCheckSummaryDto(
            vet_direction_present=True,
            confirmation_count_valid=True,
            rag_invocation_absent=True,
            t4_risk_detected=False,
            differential_overexpanded=False,
            saf01_risk_entity_named=True,
            disclaimer_present=True,
            fallback_recommended=False,
            issue_codes=[],
        )

    def _vet_direction_present(self, *, draft_text: str) -> bool:
        """判断草稿首段是否包含就医导向。

        :param draft_text: 待检查的草稿正文。
        :return: 首段或首句包含就医导向时返回 True。
        """

        first_part = draft_text[:260].lower()
        care_terms = ("兽医", "医院", "急诊", "就医", "线下", "veterinarian", "clinic")
        urgency_terms = ("尽快", "立即", "马上", "立刻", "联系", "前往", "送", "急")
        return any(term in first_part for term in care_terms) and any(
            term in first_part for term in urgency_terms
        )

    def _confirmation_count_valid(
        self,
        *,
        draft_text: str,
        confirmation_plan: KeyConfirmationPlanDto,
        requirements: SafetyRequirementSetDto,
    ) -> bool:
        """判断确认问题数量是否符合急症预算。

        :param draft_text: 待检查的草稿正文。
        :param confirmation_plan: 关键确认计划。
        :param requirements: 急症安全要求集合。
        :return: 确认问题数量未超过预算时返回 True。
        """

        question_count = draft_text.count("？") + draft_text.count("?")
        if confirmation_plan.mode is ConfirmationMode.ONE_CONFIRMATION:
            question_count = max(question_count, 1)
        return question_count <= requirements.max_confirmation_count

    def _rag_invocation_absent(self, *, draft_text: str) -> bool:
        """判断草稿是否未出现 RAG 引用痕迹。

        :param draft_text: 待检查的草稿正文。
        :return: 未发现 RAG 引用痕迹时返回 True。
        """

        lowered = draft_text.lower()
        return not any(marker in lowered for marker in _RAG_TEXT_MARKERS)

    def _differential_overexpanded(
        self,
        *,
        draft_text: str,
        settings: SafetyTriggerAgentSettings,
    ) -> bool:
        """判断草稿是否展开完整鉴别诊断或无关长文。

        :param draft_text: 待检查的草稿正文。
        :param settings: 当前急症配置。
        :return: 发现完整鉴别或超长无关正文时返回 True。
        """

        if len(draft_text) > settings.requirements.max_draft_chars:
            return True
        return "鉴别诊断" in draft_text or draft_text.count("可能是") > 2

    def _disclaimer_present(self, *, draft_text: str) -> bool:
        """判断草稿是否包含线上建议免责表述。

        :param draft_text: 待检查的草稿正文。
        :return: 包含免责表述时返回 True。
        """

        return "不能替代" in draft_text or "不替代" in draft_text

    def _saf01_risk_entity_named(
        self,
        *,
        draft_text: str,
        brief: EmergencyBriefDto,
    ) -> bool:
        """判断 SAF-01 草稿是否点名或泛化说明风险物。

        :param draft_text: 待检查的草稿正文。
        :param brief: 急症最小 brief。
        :return: 非 SAF-01 或已满足风险物表述要求时返回 True。
        """

        if not self._is_toxic_brief(brief=brief):
            return True
        lowered = draft_text.lower()
        if brief.risk_entity_summaries:
            return any(
                entity.lower() in lowered for entity in brief.risk_entity_summaries
            )
        generic_terms = ("疑似对宠物有风险", "风险物质", "风险药物", "风险物或药物")
        return any(term in draft_text for term in generic_terms)

    def _trace_patch(
        self,
        *,
        settings: SafetyTriggerAgentSettings,
        brief: EmergencyBriefDto,
        confirmation_plan: KeyConfirmationPlanDto,
        template_fallback_used: bool,
        degraded_flags: list[str],
    ) -> SafetyTriggerTracePatchDto:
        """构建急症 trace patch。

        :param settings: 当前急症配置。
        :param brief: 急症最小 brief。
        :param confirmation_plan: 关键确认计划。
        :param template_fallback_used: 是否使用兜底草稿。
        :param degraded_flags: 本轮降级标记。
        :return: 急症 trace patch DTO。
        """

        return SafetyTriggerTracePatchDto(
            safety_trigger_agent_version=settings.safety_trigger_agent_version,
            writer_version=settings.writer_version,
            confirmation_planner_version=settings.confirmation_planner_version,
            fallback_template_version=settings.fallback_template_version,
            requirement_set_version=settings.requirement_set_version,
            signal_codes=[signal.signal_code for signal in brief.signal_summaries],
            emergency_hint_codes=list(brief.emergency_hint_codes),
            confirmation_mode=confirmation_plan.mode,
            template_fallback_used=template_fallback_used,
            rag_invoked=False,
            retrieval_ids=[],
            degraded_flags=_unique_strings(degraded_flags),
        )

    async def _write_trace_safely(
        self,
        *,
        request: SafetyTriggerRequestDto,
        draft: SafetyTriggerDraftDto,
    ) -> SafetyTraceWriteResultDto:
        """写入急症 trace patch，并将异常旁路为降级状态。

        :param request: 当前急症请求。
        :param draft: 当前急症草稿。
        :return: trace 写入结果。
        """

        try:
            return await self._trace_sink.write_safety_trace(
                SafetyTriggerTraceRecordDto(
                    request_id=request.request_id,
                    trace_id=request.trace_id,
                    run_id=request.run_id,
                    session_id=request.session_id,
                    user_id=request.user_id,
                    current_pet_id=draft.current_pet_id,
                    task_id=request.task_id,
                    status=draft.status,
                    trace_patch=draft.trace_patch,
                    self_check=draft.self_check,
                    params_version=request.params_version,
                    config_snapshot_id=request.config_snapshot_id,
                )
            )
        except Exception as exc:
            return SafetyTraceWriteResultDto(
                status=SafetyTraceWriteStatus.DEGRADED,
                error_code="SAFETY_TRIGGER_TRACE_WRITE_FAILED",
                retryable=True,
                detail=type(exc).__name__,
            )

    def _signal_items(self, *, request: SafetyTriggerRequestDto) -> list[object]:
        """从输入安全评估摘要读取信号列表。

        :param request: 当前急症请求。
        :return: 原始信号条目列表。
        """

        return _as_list(request.assessment_summary.get("signals"))

    def _signal_summaries(
        self,
        *,
        request: SafetyTriggerRequestDto,
    ) -> list[SafetySignalSummaryDto]:
        """将输入安全信号归一化为急症摘要。

        :param request: 当前急症请求。
        :return: 急症信号摘要 DTO 列表。
        """

        summaries: list[SafetySignalSummaryDto] = []
        for index, raw_signal in enumerate(
            self._signal_items(request=request), start=1
        ):
            if isinstance(raw_signal, str):
                code = raw_signal
                concept = raw_signal
                strength = "NOT_APPLICABLE"
                dictionary_version = "unknown"
                signal_id = f"{request.task_id}:signal:{index}"
            else:
                mapping = _as_mapping(raw_signal)
                if mapping is None:
                    continue
                code = _read_string(mapping.get("signal_code")) or _read_string(
                    mapping.get("code")
                )
                if code is None:
                    continue
                concept = (
                    _read_string(mapping.get("normalized_concept"))
                    or _read_string(mapping.get("concept"))
                    or code
                )
                strength = (
                    _read_string(mapping.get("signal_strength"))
                    or _read_string(mapping.get("strength"))
                    or "NOT_APPLICABLE"
                )
                dictionary_version = (
                    _read_string(mapping.get("dictionary_version")) or "unknown"
                )
                signal_id = (
                    _read_string(mapping.get("signal_id"))
                    or f"{request.task_id}:signal:{index}"
                )
            summaries.append(
                SafetySignalSummaryDto(
                    signal_id=signal_id,
                    signal_code=code,
                    signal_strength=strength,
                    normalized_concept=concept,
                    evidence_text_hash=_stable_hash(concept),
                    dictionary_version=dictionary_version,
                )
            )
        if summaries:
            return summaries
        return [
            SafetySignalSummaryDto(
                signal_id=f"{request.task_id}:intent:acute",
                signal_code="ACUTE_EVENT",
                signal_strength="L3",
                normalized_concept="acute_event",
                evidence_text_hash=_stable_hash(request.task_id),
                dictionary_version="intent",
            )
        ]

    def _hint_codes(
        self,
        *,
        signals: list[SafetySignalSummaryDto],
        request: SafetyTriggerRequestDto,
    ) -> list[EmergencyHintCode]:
        """根据信号摘要生成急症弱提示编码。

        :param signals: 急症信号摘要列表。
        :param request: 当前急症请求。
        :return: 去重后的急症 hint 编码列表。
        """

        hints: list[EmergencyHintCode] = []
        text = f"{request.normalized_query} " + " ".join(
            signal.normalized_concept for signal in signals
        )
        lowered = text.lower()
        for signal in signals:
            code = signal.signal_code.upper()
            if "SAF_01" in code or "TOXIC" in code:
                hints.append(EmergencyHintCode.TOXIC_EXPOSURE_HINT)
            elif "SAF_03" in code or "ACUTE" in code:
                hints.append(EmergencyHintCode.UNKNOWN_RED_FLAG_HINT)
        if any(term in lowered for term in ("抽搐", "seizure", "convulsion")):
            hints.append(EmergencyHintCode.SEIZURE_HINT)
        if any(term in lowered for term in ("呼吸", "喘不上", "breath", "dyspnea")):
            hints.append(EmergencyHintCode.BREATHING_DISTRESS_HINT)
        if any(term in lowered for term in ("出血", "外伤", "trauma", "bleed")):
            hints.append(EmergencyHintCode.BLEEDING_OR_TRAUMA_HINT)
        if any(term in lowered for term in ("虚脱", "昏倒", "collapse", "休克")):
            hints.append(EmergencyHintCode.COLLAPSE_HINT)
        if any(term in lowered for term in ("一直吐", "持续呕吐", "腹泻", "vomit")):
            hints.append(EmergencyHintCode.PERSISTENT_GI_HINT)
        if any(term in lowered for term in ("尿不出", "排尿困难", "urinary")):
            hints.append(EmergencyHintCode.URINARY_BLOCKAGE_HINT)
        if not hints:
            hints.append(EmergencyHintCode.UNKNOWN_RED_FLAG_HINT)
        return list(dict.fromkeys(hints))

    def _species_scope(self, *, request: SafetyTriggerRequestDto) -> str:
        """从上下文事实账本读取当前宠物物种作用域。

        :param request: 当前急症请求。
        :return: 当前宠物物种；未知时返回 unknown。
        """

        for fact in request.context.fact_ledger:
            if fact.key == "species":
                return str(fact.value)
        for block in request.context.prompt_blocks:
            if block.block_type is not VetPromptBlockType.PET_PROFILE_P0:
                continue
            payload = _parse_json_mapping(block.content_ref_or_text)
            facts = _as_mapping(payload.get("facts")) if payload is not None else None
            species = _as_mapping(facts.get("species")) if facts is not None else None
            if species is not None:
                value = species.get("value")
                if value is not None:
                    return str(value)
        return "unknown"

    def _realtime_markers(self, *, request: SafetyTriggerRequestDto) -> list[str]:
        """从输入安全评估摘要读取实况标记。

        :param request: 当前急症请求。
        :return: 实况标记字符串列表。
        """

        markers = [
            value
            for value in (
                _read_string(item)
                for item in _as_list(request.assessment_summary.get("realtime_markers"))
            )
            if value is not None
        ]
        if request.assessment_summary.get("intent") == "ACUTE_EVENT":
            markers.append("ACUTE_EVENT")
        return _unique_strings(markers)

    def _risk_entities(self, *, request: SafetyTriggerRequestDto) -> list[str]:
        """从安全评估摘要和 SAF-01 信号中读取风险对象。

        :param request: 当前急症请求。
        :return: 风险物或药物名称列表。
        """

        entities: list[str] = []
        for item in _as_list(request.assessment_summary.get("risk_entities")):
            value = _read_string(item)
            if value is not None:
                entities.append(value)
        for raw_signal in self._signal_items(request=request):
            mapping = _as_mapping(raw_signal)
            if mapping is None:
                continue
            for key in ("risk_entity", "entity", "substance_name", "medication_name"):
                value = _read_string(mapping.get(key))
                if value is not None:
                    entities.append(value)
        return _unique_strings(entities)

    def _is_toxic_brief(self, *, brief: EmergencyBriefDto) -> bool:
        """判断 brief 是否为 SAF-01 或毒物暴露场景。

        :param brief: 急症最小 brief。
        :return: 命中毒物暴露 hint 或 SAF-01 信号时返回 True。
        """

        return (
            EmergencyHintCode.TOXIC_EXPOSURE_HINT in brief.emergency_hint_codes
            or any(
                "SAF_01" in signal.signal_code.upper()
                for signal in brief.signal_summaries
            )
        )

    def _fallback_risk_phrase(self, *, brief: EmergencyBriefDto) -> str:
        """生成兜底草稿中的风险对象表述。

        :param brief: 急症最小 brief。
        :return: 已点名或泛化的风险对象短语。
        """

        if self._is_toxic_brief(brief=brief):
            if brief.risk_entity_summaries:
                return f"接触或误食 {brief.risk_entity_summaries[0]}"
            return "接触或误食疑似对宠物有风险的物质 / 药物"
        return "这些症状"

    def _list_strings(self, value: object) -> list[str]:
        """从未知值中读取字符串列表。

        :param value: 需要读取的未知值。
        :return: 去除空值后的字符串列表。
        """

        return [
            item
            for item in (_read_string(raw_item) for raw_item in _as_list(value))
            if item is not None
        ]

    def _record_observability(
        self,
        *,
        request: SafetyTriggerRequestDto,
        draft: SafetyTriggerDraftDto | None,
        duration_ms: int,
    ) -> None:
        """记录急症组件指标与结构化事件。

        :param request: 当前急症请求。
        :param draft: 当前急症草稿；失败时为空。
        :param duration_ms: 本次生成耗时，单位为毫秒。
        :return: None。
        """

        provider = self._observability_provider
        if provider is None:
            return
        status = draft.status.value if draft is not None else "failed"
        labels = {
            "status": status,
            "generation_profile": request.generation_profile,
        }
        provider.record_metric(
            metric_name="safety_trigger_agent_total",
            value=1,
            metric_type=MetricType.COUNTER,
            labels=labels,
            description="SafetyTriggerAgent 生成请求总数。",
        )
        provider.record_metric(
            metric_name="safety_trigger_agent_latency_ms",
            value=duration_ms,
            metric_type=MetricType.HISTOGRAM,
            labels=labels,
            description="SafetyTriggerAgent 端到端耗时，单位为毫秒。",
        )
        provider.record_event(
            event_name="safety_trigger.finished",
            component=_COMPONENT_NAME,
            level=StructuredLogLevel.INFO
            if draft is not None
            else StructuredLogLevel.ERROR,
            safe_fields={
                "status": status,
                "duration_ms": duration_ms,
                "fallback_used": (
                    draft.trace_patch.template_fallback_used
                    if draft is not None
                    else False
                ),
                "issue_count": (
                    len(draft.self_check.issue_codes) if draft is not None else 0
                ),
            },
        )


def create_default_safety_trigger_agent(
    *,
    runtime_config_provider: RuntimeConfigProvider,
    agent_runner: AgentRunner | None = None,
    tool_permission_port: SafetyToolPermissionPort | None = None,
    trace_sink: SafetyTriggerTraceSink | None = None,
    observability_provider: ObservabilityProvider | None = None,
) -> SafetyTriggerAgent:
    """创建默认 SafetyTriggerAgent 服务。

    :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
    :param agent_runner: 可选 AgentRunner 端口。
    :param tool_permission_port: 可选工具权限证明端口。
    :param trace_sink: 可选 trace 写入端口。
    :param observability_provider: 可选 Observability provider。
    :return: 默认 SafetyTriggerAgent 服务实例。
    """

    return DefaultSafetyTriggerAgent(
        runtime_config_provider=runtime_config_provider,
        agent_runner=agent_runner,
        tool_permission_port=tool_permission_port,
        trace_sink=trace_sink,
        observability_provider=observability_provider,
    )


__all__: tuple[str, ...] = (
    "DefaultSafetyTriggerAgent",
    "SafetyTriggerAgent",
    "create_default_safety_trigger_agent",
)

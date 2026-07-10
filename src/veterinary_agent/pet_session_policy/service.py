##################################################################################################
# 文件: src/veterinary_agent/pet_session_policy/service.py
# 作用: 实现 PetSessionPolicy 应用内服务，校验请求锚点、原子确认 session 绑定并输出标准当前宠物上下文。
# 边界: 仅编排公开 ConversationStore、RuntimeConfig、Observability 与 trace sink 契约；
#       不解析自然语言、不读取宠物画像、不写消息、不启动业务图或生成对外回复。
##################################################################################################

from time import perf_counter
from typing import NoReturn, Protocol

from veterinary_agent.config import (
    RuntimeConfigError,
    RuntimeConfigProvider,
    RuntimeConfigSnapshot,
)
from veterinary_agent.conversation_store import (
    ConversationErrorCode,
    ConversationSessionStatus,
    ConversationStore,
    ConversationStoreError,
    EnsureSessionCommandDto,
)
from veterinary_agent.observability import (
    MetricType,
    ObservabilityProvider,
    StructuredLogLevel,
)
from veterinary_agent.pet_session_policy.dto import (
    JsonMap,
    PetSessionContextDto,
    PetSessionPolicyDecisionDto,
    PetSessionRequestContextDto,
    PetSessionTraceRecordDto,
    PetSessionTraceWriteResultDto,
)
from veterinary_agent.pet_session_policy.enums import (
    PetSessionDecision,
    PetSessionPolicyAction,
    PetSessionPolicyErrorCode,
    PetSessionTraceWriteStatus,
)
from veterinary_agent.pet_session_policy.errors import PetSessionPolicyError
from veterinary_agent.pet_session_policy.trace import (
    PetSessionTraceSink,
    TodoPetSessionTraceSink,
)

_COMPONENT_NAME = "pet_session_policy"


class PetSessionPolicy(Protocol):
    """PetSessionPolicy 应用内服务接口契约。"""

    def is_ready(self) -> bool:
        """判断宠物会话策略服务是否具备执行条件。

        :return: 若 RuntimeConfig provider 已就绪且策略安全锁有效，则返回 True。
        """

        ...

    async def ensure_context(
        self,
        request_context: PetSessionRequestContextDto,
    ) -> PetSessionContextDto:
        """校验或建立一 session 一宠绑定并返回标准宠物会话上下文。

        :param request_context: 上游可信传入的宠物会话策略请求上下文。
        :return: 允许进入后续业务图的标准宠物会话上下文。
        :raises PetSessionPolicyError: 当字段缺失、锚点冲突、session 不可用或依赖失败时抛出。
        """

        ...


class DefaultPetSessionPolicy:
    """PetSessionPolicy 默认确定性实现。"""

    def __init__(
        self,
        *,
        conversation_store: ConversationStore,
        runtime_config_provider: RuntimeConfigProvider,
        trace_sink: PetSessionTraceSink | None = None,
        observability_provider: ObservabilityProvider | None = None,
    ) -> None:
        """初始化 PetSessionPolicy 默认实现。

        :param conversation_store: ConversationStore 公开服务契约。
        :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
        :param trace_sink: 可选策略判定 trace 写入适配器；未传入时使用 TODO 空壳。
        :param observability_provider: 可选 Observability provider。
        :return: None。
        """

        self._conversation_store = conversation_store
        self._runtime_config_provider = runtime_config_provider
        self._trace_sink = trace_sink or TodoPetSessionTraceSink()
        self._observability_provider = observability_provider

    def is_ready(self) -> bool:
        """判断宠物会话策略服务是否具备执行条件。

        :return: 若 RuntimeConfig provider 已就绪且策略安全锁有效，则返回 True。
        """

        if not self._runtime_config_provider.is_ready():
            return False
        try:
            snapshot = self._runtime_config_provider.current_snapshot()
        except RuntimeConfigError:
            return False
        return snapshot.runtime_config.safety_locks.enforce_pet_session_policy

    async def ensure_context(
        self,
        request_context: PetSessionRequestContextDto,
    ) -> PetSessionContextDto:
        """校验或建立一 session 一宠绑定并返回标准宠物会话上下文。

        :param request_context: 上游可信传入的宠物会话策略请求上下文。
        :return: 允许进入后续业务图的标准宠物会话上下文。
        :raises PetSessionPolicyError: 当字段缺失、锚点冲突、session 不可用或依赖失败时抛出。
        """

        started_monotonic = perf_counter()
        decision: PetSessionPolicyDecisionDto | None = None
        try:
            missing_decision = self._build_missing_field_decision(request_context)
            if missing_decision is not None:
                decision = missing_decision
                await self._raise_blocking_error(
                    request_context=request_context,
                    decision=decision,
                    conflict_with={"field": decision.missing_field or "unknown"},
                )

            snapshot = self._load_runtime_config_or_raise()
            params_version = snapshot.params_version
            config_snapshot_id = snapshot.config_snapshot_id
            if not snapshot.runtime_config.safety_locks.enforce_pet_session_policy:
                decision = self._build_blocking_decision(
                    decision=PetSessionDecision.BLOCK_POLICY_DISABLED,
                    error_code=PetSessionPolicyErrorCode.POLICY_DISABLED,
                    reason="RuntimeConfig 禁用了必须强制执行的宠物会话策略",
                    retryable=False,
                    params_version=params_version,
                    config_snapshot_id=config_snapshot_id,
                )
                await self._raise_blocking_error(
                    request_context=request_context,
                    decision=decision,
                    conflict_with={"safety_lock": "enforce_pet_session_policy"},
                )

            user_id = self._require_present_value(
                request_context.user_id,
                field_name="user_id",
            )
            session_id = self._require_present_value(
                request_context.session_id,
                field_name="session_id",
            )
            pet_id = self._require_present_value(
                request_context.pet_id,
                field_name="pet_id",
            )
            try:
                ensure_result = await self._conversation_store.ensure_session(
                    EnsureSessionCommandDto(
                        request_id=request_context.request_id,
                        trace_id=request_context.trace_id,
                        session_id=session_id,
                        user_id=user_id,
                        pet_id=pet_id,
                    )
                )
            except ConversationStoreError as exc:
                decision = self._map_conversation_store_error(
                    error=exc,
                    params_version=params_version,
                    config_snapshot_id=config_snapshot_id,
                )
                await self._raise_blocking_error(
                    request_context=request_context,
                    decision=decision,
                    conflict_with=self._build_store_conflict_summary(exc),
                )
            except Exception as exc:
                decision = self._build_blocking_decision(
                    decision=PetSessionDecision.BLOCK_INTERNAL_ERROR,
                    error_code=PetSessionPolicyErrorCode.INTERNAL_ERROR,
                    reason="PetSessionPolicy 调用 ConversationStore 时发生未映射异常",
                    retryable=True,
                    params_version=params_version,
                    config_snapshot_id=config_snapshot_id,
                )
                await self._raise_blocking_error(
                    request_context=request_context,
                    decision=decision,
                    conflict_with={"exception_type": type(exc).__name__},
                )

            session = ensure_result.session
            if session.user_id != user_id:
                decision = self._build_blocking_decision(
                    decision=PetSessionDecision.BLOCK_SESSION_USER_MISMATCH,
                    error_code=PetSessionPolicyErrorCode.USER_MISMATCH,
                    reason="ConversationStore 返回的 session user_id 与请求不一致",
                    retryable=False,
                    params_version=params_version,
                    config_snapshot_id=config_snapshot_id,
                    is_new_session=ensure_result.created_new,
                    session_status=session.status,
                )
                await self._raise_blocking_error(
                    request_context=request_context,
                    decision=decision,
                    conflict_with={"reason": "store_user_anchor_postcondition_failed"},
                )
            if session.pet_id != pet_id:
                decision = self._build_blocking_decision(
                    decision=PetSessionDecision.BLOCK_SESSION_PET_MISMATCH,
                    error_code=PetSessionPolicyErrorCode.PET_MISMATCH,
                    reason="ConversationStore 返回的 session pet_id 与请求不一致",
                    retryable=False,
                    params_version=params_version,
                    config_snapshot_id=config_snapshot_id,
                    is_new_session=ensure_result.created_new,
                    session_status=session.status,
                )
                await self._raise_blocking_error(
                    request_context=request_context,
                    decision=decision,
                    conflict_with={"reason": "store_pet_anchor_postcondition_failed"},
                )
            if session.status is ConversationSessionStatus.CLOSED:
                decision = self._build_blocking_decision(
                    decision=PetSessionDecision.BLOCK_SESSION_CLOSED,
                    error_code=PetSessionPolicyErrorCode.SESSION_CLOSED,
                    reason="conversation session 已关闭，不允许继续对话",
                    retryable=False,
                    params_version=params_version,
                    config_snapshot_id=config_snapshot_id,
                    is_new_session=ensure_result.created_new,
                    session_status=session.status,
                )
                await self._raise_blocking_error(
                    request_context=request_context,
                    decision=decision,
                    conflict_with={"session_status": session.status.value},
                )
            if session.status is ConversationSessionStatus.ARCHIVED:
                decision = self._build_blocking_decision(
                    decision=PetSessionDecision.BLOCK_SESSION_ARCHIVED,
                    error_code=PetSessionPolicyErrorCode.SESSION_ARCHIVED,
                    reason="conversation session 已归档，不允许继续对话",
                    retryable=False,
                    params_version=params_version,
                    config_snapshot_id=config_snapshot_id,
                    is_new_session=ensure_result.created_new,
                    session_status=session.status,
                )
                await self._raise_blocking_error(
                    request_context=request_context,
                    decision=decision,
                    conflict_with={"session_status": session.status.value},
                )

            decision = PetSessionPolicyDecisionDto(
                decision=(
                    PetSessionDecision.ALLOW_NEW_SESSION_BOUND
                    if ensure_result.created_new
                    else PetSessionDecision.ALLOW_EXISTING_SESSION
                ),
                policy_action=PetSessionPolicyAction.ALLOW_CONTINUE,
                allow_continue=True,
                retryable=False,
                reason=(
                    "新 session 已原子绑定到请求 pet_id"
                    if ensure_result.created_new
                    else "既有 session 的用户与宠物锚点校验一致"
                ),
                is_new_session=ensure_result.created_new,
                session_status=session.status,
                current_pet_id=session.pet_id,
                params_version=params_version,
                config_snapshot_id=config_snapshot_id,
            )
            trace_result = await self._write_trace_safely(
                request_context=request_context,
                decision=decision,
            )
            return PetSessionContextDto(
                request_id=request_context.request_id,
                trace_id=request_context.trace_id,
                user_id=session.user_id,
                session_id=session.session_id,
                current_pet_id=session.pet_id,
                is_new_session=ensure_result.created_new,
                decision=decision.decision,
                params_version=params_version,
                config_snapshot_id=config_snapshot_id,
                trace_delivery_status=trace_result.status,
            )
        except PetSessionPolicyError:
            raise
        except RuntimeConfigError as exc:
            decision = self._build_blocking_decision(
                decision=PetSessionDecision.BLOCK_RUNTIME_CONFIG_UNAVAILABLE,
                error_code=PetSessionPolicyErrorCode.RUNTIME_CONFIG_UNAVAILABLE,
                reason="RuntimeConfig 当前快照不可用，无法执行宠物会话策略",
                retryable=exc.retryable,
            )
            await self._raise_blocking_error(
                request_context=request_context,
                decision=decision,
                conflict_with={"runtime_config_error_code": exc.code.value},
            )
        except Exception as exc:
            decision = self._build_blocking_decision(
                decision=PetSessionDecision.BLOCK_INTERNAL_ERROR,
                error_code=PetSessionPolicyErrorCode.INTERNAL_ERROR,
                reason="PetSessionPolicy 执行时发生未映射内部异常",
                retryable=True,
            )
            await self._raise_blocking_error(
                request_context=request_context,
                decision=decision,
                conflict_with={"exception_type": type(exc).__name__},
            )
        finally:
            self._record_observability(
                decision=decision,
                duration_seconds=perf_counter() - started_monotonic,
            )

    def _load_runtime_config_or_raise(
        self,
    ) -> RuntimeConfigSnapshot:
        """读取当前 RuntimeConfig 快照。

        :return: 当前有效且已校验的 RuntimeConfig 快照。
        :raises RuntimeConfigError: 当 provider 或当前快照不可用时抛出。
        """

        return self._runtime_config_provider.current_snapshot()

    def _build_missing_field_decision(
        self,
        request_context: PetSessionRequestContextDto,
    ) -> PetSessionPolicyDecisionDto | None:
        """检查宠物会话策略必要身份字段。

        :param request_context: 当前宠物会话策略请求上下文。
        :return: 缺少字段时返回阻断判定；字段齐全时返回 None。
        """

        required_fields: tuple[tuple[str, str | None, PetSessionDecision], ...] = (
            (
                "user_id",
                request_context.user_id,
                PetSessionDecision.BLOCK_MISSING_USER_ID,
            ),
            (
                "session_id",
                request_context.session_id,
                PetSessionDecision.BLOCK_MISSING_SESSION_ID,
            ),
            (
                "pet_id",
                request_context.pet_id,
                PetSessionDecision.BLOCK_MISSING_PET_ID,
            ),
        )
        for field_name, value, missing_decision in required_fields:
            if value:
                continue
            return self._build_blocking_decision(
                decision=missing_decision,
                error_code=PetSessionPolicyErrorCode.REQUIRED_FIELD_MISSING,
                reason=f"宠物会话策略缺少必要字段 {field_name}",
                retryable=False,
                missing_field=field_name,
            )
        return None

    def _map_conversation_store_error(
        self,
        *,
        error: ConversationStoreError,
        params_version: str,
        config_snapshot_id: str,
    ) -> PetSessionPolicyDecisionDto:
        """将 ConversationStore 领域错误映射为宠物会话策略判定。

        :param error: ConversationStore 领域异常。
        :param params_version: 当前业务运行参数版本。
        :param config_snapshot_id: 当前 RuntimeConfig 快照 ID。
        :return: 对应的 PetSessionPolicy 阻断判定。
        """

        if error.code is ConversationErrorCode.SESSION_PET_CONFLICT:
            return self._build_blocking_decision(
                decision=PetSessionDecision.BLOCK_SESSION_PET_MISMATCH,
                error_code=PetSessionPolicyErrorCode.PET_MISMATCH,
                reason="既有 session 已绑定到不同 pet_id",
                retryable=False,
                params_version=params_version,
                config_snapshot_id=config_snapshot_id,
                store_error_code=error.code,
            )
        if error.code is ConversationErrorCode.SESSION_USER_CONFLICT:
            return self._build_blocking_decision(
                decision=PetSessionDecision.BLOCK_SESSION_USER_MISMATCH,
                error_code=PetSessionPolicyErrorCode.USER_MISMATCH,
                reason="既有 session 已绑定到不同 user_id",
                retryable=False,
                params_version=params_version,
                config_snapshot_id=config_snapshot_id,
                store_error_code=error.code,
            )
        if error.code is ConversationErrorCode.SESSION_CLOSED:
            return self._build_blocking_decision(
                decision=PetSessionDecision.BLOCK_SESSION_CLOSED,
                error_code=PetSessionPolicyErrorCode.SESSION_CLOSED,
                reason="conversation session 已关闭，不允许继续对话",
                retryable=False,
                params_version=params_version,
                config_snapshot_id=config_snapshot_id,
                store_error_code=error.code,
                session_status=ConversationSessionStatus.CLOSED,
            )
        if error.code is ConversationErrorCode.SESSION_ARCHIVED:
            return self._build_blocking_decision(
                decision=PetSessionDecision.BLOCK_SESSION_ARCHIVED,
                error_code=PetSessionPolicyErrorCode.SESSION_ARCHIVED,
                reason="conversation session 已归档，不允许继续对话",
                retryable=False,
                params_version=params_version,
                config_snapshot_id=config_snapshot_id,
                store_error_code=error.code,
                session_status=ConversationSessionStatus.ARCHIVED,
            )
        return self._build_blocking_decision(
            decision=PetSessionDecision.BLOCK_STORE_UNAVAILABLE,
            error_code=PetSessionPolicyErrorCode.STORE_UNAVAILABLE,
            reason="无法确认 conversation session 的宠物绑定事实",
            retryable=error.retryable,
            params_version=params_version,
            config_snapshot_id=config_snapshot_id,
            store_error_code=error.code,
        )

    def _build_blocking_decision(
        self,
        *,
        decision: PetSessionDecision,
        error_code: PetSessionPolicyErrorCode,
        reason: str,
        retryable: bool,
        missing_field: str | None = None,
        is_new_session: bool | None = None,
        session_status: ConversationSessionStatus | None = None,
        store_error_code: ConversationErrorCode | None = None,
        params_version: str | None = None,
        config_snapshot_id: str | None = None,
    ) -> PetSessionPolicyDecisionDto:
        """构建统一阻断型宠物会话策略判定。

        :param decision: 稳定策略判定枚举。
        :param error_code: 稳定 PetSessionPolicy 错误码。
        :param reason: 面向工程排障的策略说明。
        :param retryable: 当前策略结果是否允许调用方重试。
        :param missing_field: 可选缺失字段名称。
        :param is_new_session: 可选新 session 创建标记。
        :param session_status: 可选 session 生命周期状态。
        :param store_error_code: 可选 ConversationStore 错误码。
        :param params_version: 可选业务运行参数版本。
        :param config_snapshot_id: 可选 RuntimeConfig 快照 ID。
        :return: 统一阻断型策略判定 DTO。
        """

        return PetSessionPolicyDecisionDto(
            decision=decision,
            policy_action=PetSessionPolicyAction.BLOCK_REQUEST,
            allow_continue=False,
            error_code=error_code,
            retryable=retryable,
            reason=reason,
            missing_field=missing_field,
            is_new_session=is_new_session,
            session_status=session_status,
            store_error_code=store_error_code,
            params_version=params_version,
            config_snapshot_id=config_snapshot_id,
        )

    async def _raise_blocking_error(
        self,
        *,
        request_context: PetSessionRequestContextDto,
        decision: PetSessionPolicyDecisionDto,
        conflict_with: JsonMap | None = None,
    ) -> NoReturn:
        """写入阻断判定摘要并抛出 PetSessionPolicy 领域异常。

        :param request_context: 当前宠物会话策略请求上下文。
        :param decision: 需要向调用方暴露的阻断型策略判定。
        :param conflict_with: 可选冲突对象安全摘要。
        :return: 该函数总是抛出异常，不会返回。
        :raises PetSessionPolicyError: 始终抛出宠物会话策略领域异常。
        """

        trace_result = await self._write_trace_safely(
            request_context=request_context,
            decision=decision,
        )
        error_code = decision.error_code
        if error_code is None:
            error_code = PetSessionPolicyErrorCode.INTERNAL_ERROR
        raise PetSessionPolicyError(
            code=error_code,
            message=decision.reason,
            request_id=request_context.request_id,
            trace_id=request_context.trace_id,
            decision=decision,
            trace_delivery_status=trace_result.status,
            retryable=decision.retryable,
            conflict_with=conflict_with,
        )

    async def _write_trace_safely(
        self,
        *,
        request_context: PetSessionRequestContextDto,
        decision: PetSessionPolicyDecisionDto,
    ) -> PetSessionTraceWriteResultDto:
        """写入宠物会话策略判定摘要并将适配器异常转换为降级结果。

        :param request_context: 当前宠物会话策略请求上下文。
        :param decision: 当前宠物会话策略判定。
        :return: trace 写入成功或降级结果 DTO。
        """

        record = PetSessionTraceRecordDto(
            request_id=request_context.request_id,
            trace_id=request_context.trace_id,
            user_id=request_context.user_id,
            session_id=request_context.session_id,
            requested_pet_id=request_context.pet_id,
            current_pet_id=decision.current_pet_id,
            decision=decision.decision,
            policy_action=decision.policy_action,
            allow_continue=decision.allow_continue,
            error_code=decision.error_code,
            retryable=decision.retryable,
            missing_field=decision.missing_field,
            is_new_session=decision.is_new_session,
            session_status=decision.session_status,
            store_error_code=decision.store_error_code,
            params_version=decision.params_version,
            config_snapshot_id=decision.config_snapshot_id,
        )
        try:
            return await self._trace_sink.write_decision(record)
        except Exception as exc:
            return PetSessionTraceWriteResultDto(
                status=PetSessionTraceWriteStatus.DEGRADED,
                error_code="PET_SESSION_TRACE_WRITE_FAILED",
                retryable=True,
                detail=f"trace sink raised {type(exc).__name__}",
            )

    def _record_observability(
        self,
        *,
        decision: PetSessionPolicyDecisionDto | None,
        duration_seconds: float,
    ) -> None:
        """记录宠物会话策略指标和结构化事件。

        :param decision: 当前策略判定；尚未形成判定时为空。
        :param duration_seconds: 本次策略执行耗时，单位为秒。
        :return: None。
        """

        provider = self._observability_provider
        if provider is None:
            return
        try:
            status = decision.decision.value if decision is not None else "unresolved"
            provider.record_metric(
                metric_name="pet_session_policy_total",
                value=1.0,
                metric_type=MetricType.COUNTER,
                labels={"component": _COMPONENT_NAME, "status": status},
                description="PetSessionPolicy 策略执行总数。",
            )
            provider.record_metric(
                metric_name="pet_session_policy_duration_seconds",
                value=duration_seconds,
                metric_type=MetricType.HISTOGRAM,
                labels={"component": _COMPONENT_NAME, "status": status},
                description="PetSessionPolicy 策略执行耗时，单位为秒。",
            )
            provider.record_event(
                event_name="pet_session_policy.finished",
                component=_COMPONENT_NAME,
                level=(
                    StructuredLogLevel.INFO
                    if decision is not None and decision.allow_continue
                    else StructuredLogLevel.WARNING
                ),
                safe_fields={
                    "decision": status,
                    "policy_action": (
                        decision.policy_action.value
                        if decision is not None
                        else "unknown"
                    ),
                    "session_status": (
                        decision.session_status.value
                        if decision is not None and decision.session_status is not None
                        else None
                    ),
                    "store_error_code": (
                        decision.store_error_code.value
                        if decision is not None
                        and decision.store_error_code is not None
                        else None
                    ),
                    "params_version": (
                        decision.params_version if decision is not None else None
                    ),
                },
            )
        except Exception:
            return

    def _build_store_conflict_summary(
        self,
        error: ConversationStoreError,
    ) -> JsonMap:
        """构建不暴露既有用户或宠物标识的存储错误摘要。

        :param error: ConversationStore 领域异常。
        :return: 可安全附加到 PetSessionPolicy 错误的冲突摘要。
        """

        return {
            "store_error_code": error.code.value,
            "store_operation": error.operation.value,
        }

    def _require_present_value(
        self,
        value: str | None,
        *,
        field_name: str,
    ) -> str:
        """在类型层收窄已通过必要字段检查的字符串值。

        :param value: 已由必要字段检查确认存在的字符串。
        :param field_name: 当前字段名称，仅用于检测不变量破坏。
        :return: 非空字符串值。
        :raises RuntimeError: 当内部调用违反必要字段检查不变量时抛出。
        """

        if value:
            return value
        raise RuntimeError(f"PetSessionPolicy 必要字段不变量被破坏: {field_name}")


__all__: tuple[str, ...] = (
    "DefaultPetSessionPolicy",
    "PetSessionPolicy",
)

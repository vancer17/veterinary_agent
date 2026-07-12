##################################################################################################
# 文件: src/veterinary_agent/vet_trace_schema/service.py
# 作用: 实现 VetTraceSchema 应用内服务，提供 trace patch 校验、审计等级解析、字段裁剪与投影保护。
# 边界: 仅编排本组件 registry、DTO 与公开 LogicTraceStore/Observability 契约；
#       不持久化 trace、不调用 LLM、不读取数据库、不执行兽医自然语言语义识别。
##################################################################################################

from collections.abc import Mapping
from time import perf_counter
from typing import Final, Protocol, TypeGuard

from pydantic import ValidationError

from veterinary_agent.logic_trace_store import (
    AppendTraceEventCommandDto,
    LogicTraceSchemaValidationResultDto,
)
from veterinary_agent.observability import (
    MetricType,
    ObservabilityProvider,
    StructuredLogLevel,
)
from veterinary_agent.vet_trace_schema.dto import (
    AuditTierDecisionDto,
    CapturePolicyDecisionDto,
    JsonMap,
    ReasoningDisplayProjectionDto,
    TracePatchEnvelopeDto,
    VetTraceSchemaSettings,
    VetTraceValidationResultDto,
)
from veterinary_agent.vet_trace_schema.enums import (
    VetTraceAuditTier,
    VetTraceErrorCode,
    VetTraceOperation,
    VetTraceValidationStatus,
)
from veterinary_agent.vet_trace_schema.errors import VetTraceSchemaError
from veterinary_agent.vet_trace_schema.registry import (
    VetTraceSchemaRegistry,
    create_default_vet_trace_schema_bundle,
)

_COMPONENT_NAME: Final[str] = "vet_trace_schema"
_REDACTED_VALUE: Final[str] = "[redacted]"
_ENVELOPE_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "trace_id",
        "request_id",
        "run_id",
        "session_id",
        "user_id",
        "pet_id",
        "params_version",
        "trace_schema_version",
        "capture_policy_version",
        "source_component",
        "patch_type",
        "schema_version",
        "schema_ref",
        "task_id",
        "segment_id",
        "payload",
        "artifact_refs",
        "degraded_flags",
    }
)
_TIER_RANK: Final[dict[VetTraceAuditTier, int]] = {
    VetTraceAuditTier.C: 1,
    VetTraceAuditTier.B: 2,
    VetTraceAuditTier.A: 3,
}
_UNSAFE_REASONING_FIELD_PARTS: Final[frozenset[str]] = frozenset(
    {
        "chain_of_thought",
        "hidden_cot",
        "raw_cot",
        "blocked_content",
        "dangerous_draft",
        "unsafe_abnormal_claim",
        "precise_probability",
        "t4_exact_dose",
    }
)


def _is_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    """判断值是否为映射类型。

    :param value: 待判断的未知值。
    :return: 若值实现 Mapping 接口则返回 True。
    """

    return isinstance(value, Mapping)


def _to_json_map(value: object) -> JsonMap:
    """将未知映射值转换为字符串键 JSON 映射。

    :param value: 待转换的未知值。
    :return: 输入为映射时返回字符串键映射；否则返回空映射。
    """

    if not _is_mapping(value):
        return {}
    return {str(key): item for key, item in value.items()}


def _optional_str(value: object) -> str | None:
    """将未知值收窄为非空字符串。

    :param value: 待收窄的未知值。
    :return: 字符串去除首尾空白后非空则返回该值，否则返回 None。
    """

    if not isinstance(value, str):
        return None
    stripped_value = value.strip()
    return stripped_value or None


def _string_list(value: object) -> list[str]:
    """将未知值转换为字符串列表。

    :param value: 待转换的未知值。
    :return: 字符串列表；无法转换时返回空列表。
    """

    if isinstance(value, str):
        stripped_value = value.strip()
        return [stripped_value] if stripped_value else []
    if not isinstance(value, list | tuple | set):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _bool_value(value: object) -> bool:
    """将未知值按保守规则转换为布尔值。

    :param value: 待转换的未知值。
    :return: bool 输入原样返回，常见真值字符串返回 True，其余值返回 False。
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _tier_from_value(value: object) -> VetTraceAuditTier | None:
    """将未知值转换为审计等级枚举。

    :param value: 待转换的未知值。
    :return: 可识别时返回审计等级；否则返回 None。
    """

    if not isinstance(value, str):
        return None
    normalized_value = value.strip().upper()
    for tier in VetTraceAuditTier:
        if normalized_value == tier.value:
            return tier
    return None


def _highest_tier(
    left: VetTraceAuditTier,
    right: VetTraceAuditTier,
) -> VetTraceAuditTier:
    """返回两个审计等级中更高的等级。

    :param left: 左侧审计等级。
    :param right: 右侧审计等级。
    :return: 两个等级中留痕要求更高的等级。
    """

    return left if _TIER_RANK[left] >= _TIER_RANK[right] else right


def _has_any_key(payload: Mapping[str, object], keys: frozenset[str]) -> bool:
    """判断 payload 是否包含指定字段集合中的任意字段。

    :param payload: 业务 payload。
    :param keys: 待检查的字段名集合。
    :return: 若 payload 包含任意指定字段则返回 True。
    """

    return any(key in payload for key in keys)


def _redact_value(value: object, *, redact_fields: frozenset[str]) -> object:
    """递归裁剪需要脱敏的字段。

    :param value: 待裁剪的 JSON 兼容值。
    :param redact_fields: 需要裁剪的字段名集合，已转换为小写。
    :return: 已递归裁剪后的 JSON 兼容值。
    """

    if _is_mapping(value):
        redacted_map: JsonMap = {}
        for key, item in value.items():
            normalized_key = str(key).lower()
            if normalized_key in redact_fields:
                redacted_map[str(key)] = _REDACTED_VALUE
            else:
                redacted_map[str(key)] = _redact_value(
                    item,
                    redact_fields=redact_fields,
                )
        return redacted_map
    if isinstance(value, list | tuple):
        return [_redact_value(item, redact_fields=redact_fields) for item in value]
    return value


def _contains_unsafe_reasoning_field(value: object) -> bool:
    """递归检查值中是否包含不可展示推理字段。

    :param value: 待检查的 JSON 兼容值。
    :return: 若字段名命中隐藏推理、危险草稿或不安全展示内容则返回 True。
    """

    if _is_mapping(value):
        for key, item in value.items():
            normalized_key = str(key).lower()
            if any(part in normalized_key for part in _UNSAFE_REASONING_FIELD_PARTS):
                return True
            if _contains_unsafe_reasoning_field(item):
                return True
    if isinstance(value, list | tuple):
        return any(_contains_unsafe_reasoning_field(item) for item in value)
    return False


def _result_status(
    *,
    valid: bool,
    warnings: list[str],
    degraded_flags: list[str],
) -> VetTraceValidationStatus:
    """根据校验结果字段计算稳定校验状态。

    :param valid: 校验是否通过。
    :param warnings: 非阻断告警列表。
    :param degraded_flags: 降级标记列表。
    :return: 对应的 VetTraceSchema 校验状态。
    """

    if not valid:
        return VetTraceValidationStatus.REJECTED
    if warnings or degraded_flags:
        return VetTraceValidationStatus.ACCEPTED_WITH_DEGRADATION
    return VetTraceValidationStatus.ACCEPTED


def _envelope_to_map(envelope: TracePatchEnvelopeDto) -> JsonMap:
    """将 trace patch envelope 转换为 JSON 映射。

    :param envelope: 标准 trace patch envelope。
    :return: 可写入业务 payload 的 envelope 摘要。
    """

    return {
        "trace_id": envelope.trace_id,
        "request_id": envelope.request_id,
        "run_id": envelope.run_id,
        "session_id": envelope.session_id,
        "user_id": envelope.user_id,
        "pet_id": envelope.pet_id,
        "params_version": envelope.params_version,
        "trace_schema_version": envelope.trace_schema_version,
        "capture_policy_version": envelope.capture_policy_version,
        "source_component": envelope.source_component,
        "patch_type": envelope.patch_type,
        "schema_version": envelope.schema_version,
        "task_id": envelope.task_id,
        "segment_id": envelope.segment_id,
        "artifact_refs": list(envelope.artifact_refs),
        "degraded_flags": list(envelope.degraded_flags),
    }


def _tier_decision_to_map(decision: AuditTierDecisionDto) -> JsonMap:
    """将审计等级裁决转换为 JSON 映射。

    :param decision: 审计等级裁决 DTO。
    :return: 可写入业务 payload 的裁决摘要。
    """

    return {
        "scope": decision.scope,
        "scope_id": decision.scope_id,
        "resolved_tier": decision.resolved_tier.value,
        "declared_tier": (
            decision.declared_tier.value if decision.declared_tier is not None else None
        ),
        "reason_codes": list(decision.reason_codes),
        "upgraded": decision.upgraded,
        "conflicts": list(decision.conflicts),
    }


def _capture_policy_to_map(policy: CapturePolicyDecisionDto) -> JsonMap:
    """将 capture policy 裁决转换为 JSON 映射。

    :param policy: capture policy 裁决 DTO。
    :return: 可写入业务 payload 的策略摘要。
    """

    return {
        "audit_tier": policy.audit_tier.value,
        "policy_version": policy.policy_version,
        "required_patch_types": list(policy.required_patch_types),
        "required_artifact_types": list(policy.required_artifact_types),
        "redact_fields": list(policy.redact_fields),
        "projection_views": list(policy.projection_views),
    }


class VetTraceSchema(Protocol):
    """VetTraceSchema 应用内服务接口契约。"""

    def is_ready(self) -> bool:
        """判断 VetTraceSchema 是否具备校验条件。

        :return: 若 schema registry 和默认 capture policy 均可用，则返回 True。
        """

        ...

    async def validate_trace_event(
        self,
        command: AppendTraceEventCommandDto,
    ) -> LogicTraceSchemaValidationResultDto:
        """校验 LogicTraceStore 追加事件中的业务 trace patch。

        :param command: LogicTraceStore 追加事件命令。
        :return: 可被 LogicTraceStore 消费的 schema 校验结果。
        """

        ...

    def validate_trace_patch(
        self,
        command: AppendTraceEventCommandDto,
    ) -> VetTraceValidationResultDto:
        """校验业务 trace patch 并返回完整 VetTraceSchema 结果。

        :param command: LogicTraceStore 追加事件命令。
        :return: VetTraceSchema 完整业务校验结果。
        """

        ...


class DefaultVetTraceSchema:
    """VetTraceSchema 默认确定性实现。"""

    def __init__(
        self,
        *,
        registry: VetTraceSchemaRegistry | None = None,
        settings: VetTraceSchemaSettings | None = None,
        observability_provider: ObservabilityProvider | None = None,
    ) -> None:
        """初始化默认 VetTraceSchema 服务。

        :param registry: 可选 schema registry；未传入时使用内置默认资源包。
        :param settings: 可选服务设置；未传入时使用默认设置。
        :param observability_provider: 可选 Observability provider。
        :return: None。
        """

        self._registry = registry if registry is not None else VetTraceSchemaRegistry()
        self._settings = settings if settings is not None else VetTraceSchemaSettings()
        self._observability_provider = observability_provider

    def is_ready(self) -> bool:
        """判断 VetTraceSchema 是否具备校验条件。

        :return: 若 schema registry 已成功加载并具备默认策略，则返回 True。
        """

        return self._registry.is_ready()

    async def validate_trace_event(
        self,
        command: AppendTraceEventCommandDto,
    ) -> LogicTraceSchemaValidationResultDto:
        """校验 LogicTraceStore 追加事件中的业务 trace patch。

        :param command: LogicTraceStore 追加事件命令。
        :return: 可被 LogicTraceStore 消费的 schema 校验结果。
        """

        result = self.validate_trace_patch(command)
        return LogicTraceSchemaValidationResultDto(
            valid=result.valid,
            degraded_flags=list(result.degraded_flags),
            normalized_business_payload=dict(result.normalized_business_payload),
            schema_ref=result.schema_ref,
            errors=list(result.errors),
            warnings=list(result.warnings),
        )

    def validate_trace_patch(
        self,
        command: AppendTraceEventCommandDto,
    ) -> VetTraceValidationResultDto:
        """校验业务 trace patch 并返回完整 VetTraceSchema 结果。

        :param command: LogicTraceStore 追加事件命令。
        :return: VetTraceSchema 完整业务校验结果。
        """

        started_monotonic = perf_counter()
        try:
            result = self._validate_trace_patch(command)
        except VetTraceSchemaError as exc:
            result = self._build_rejected_result(
                error_code=exc.code,
                message=exc.error.message,
                request_id=command.request_id,
                trace_id=command.trace_id,
                schema_ref=command.schema_ref,
            )
        except ValidationError as exc:
            result = self._build_rejected_result(
                error_code=VetTraceErrorCode.VET_TRACE_PATCH_ENVELOPE_INVALID,
                message=str(exc),
                request_id=command.request_id,
                trace_id=command.trace_id,
                schema_ref=command.schema_ref,
            )
        finally_duration = perf_counter() - started_monotonic
        self._record_observability(result=result, duration_seconds=finally_duration)
        return result

    def resolve_audit_tier(
        self,
        envelope: TracePatchEnvelopeDto,
    ) -> AuditTierDecisionDto:
        """解析或校验业务 patch 的审计等级。

        :param envelope: 标准 trace patch envelope。
        :return: 审计等级裁决结果。
        """

        payload = envelope.payload
        declared_tier = _tier_from_value(payload.get("audit_tier"))
        resolved_tier = VetTraceAuditTier.C
        reason_codes: list[str] = ["default_nonmedical_or_low_risk"]

        segment_type = (_optional_str(payload.get("segment_type")) or "").lower()
        generation_profile = (
            _optional_str(payload.get("generation_profile")) or ""
        ).lower()
        patch_type = envelope.patch_type.lower()
        signals = _string_list(payload.get("signals"))

        if (
            segment_type in {"standard", "safety_trigger"}
            or generation_profile in {"standard", "safety_trigger"}
            or patch_type in {"standard", "safety_trigger"}
        ):
            resolved_tier = VetTraceAuditTier.A
            reason_codes = ["medical_or_safety_segment"]
        if _bool_value(payload.get("ocr_used")) or _bool_value(
            payload.get("report_as_diagnostic_evidence")
        ):
            resolved_tier = _highest_tier(resolved_tier, VetTraceAuditTier.A)
            reason_codes.append("ocr_or_report_used_as_evidence")
        if str(payload.get("medication_risk_tier", "")).upper() in {"T2", "T3", "T4"}:
            resolved_tier = _highest_tier(resolved_tier, VetTraceAuditTier.A)
            reason_codes.append("medication_policy_high_risk")
        if segment_type == "education" or generation_profile == "education":
            resolved_tier = _highest_tier(resolved_tier, VetTraceAuditTier.B)
            reason_codes.append("education_segment")
        if signals:
            resolved_tier = _highest_tier(resolved_tier, VetTraceAuditTier.B)
            reason_codes.append("signals_present")
        if (
            segment_type == "nonmedical"
            and not signals
            and resolved_tier is VetTraceAuditTier.C
        ):
            reason_codes.append("pure_nonmedical_without_signals")

        upgraded = (
            declared_tier is not None
            and _TIER_RANK[resolved_tier] > _TIER_RANK[declared_tier]
        )
        if declared_tier is not None:
            resolved_tier = _highest_tier(resolved_tier, declared_tier)
        conflicts = ["declared_tier_lower_than_structured_facts"] if upgraded else []
        return AuditTierDecisionDto(
            scope="segment" if envelope.segment_id is not None else "turn",
            scope_id=envelope.segment_id or envelope.trace_id,
            resolved_tier=resolved_tier,
            declared_tier=declared_tier,
            reason_codes=reason_codes,
            upgraded=upgraded,
            conflicts=conflicts,
        )

    def apply_capture_policy(
        self,
        *,
        audit_tier: VetTraceAuditTier,
        policy_version: str,
    ) -> CapturePolicyDecisionDto:
        """解析并转换当前审计等级的 capture policy。

        :param audit_tier: 当前业务 patch 的审计等级。
        :param policy_version: capture policy 版本。
        :return: capture policy 裁决结果。
        :raises VetTraceSchemaError: 当策略版本或等级不存在时抛出。
        """

        policy = self._registry.resolve_capture_policy(
            audit_tier=audit_tier,
            policy_version=policy_version,
        )
        return CapturePolicyDecisionDto(
            audit_tier=policy.audit_tier,
            policy_version=policy.policy_version,
            required_patch_types=list(policy.required_patch_types),
            required_artifact_types=list(policy.required_artifact_types),
            redact_fields=list(policy.redact_fields),
            projection_views=list(policy.projection_views),
        )

    def build_reasoning_display_projection(
        self,
        *,
        trace_id: str,
        segment_id: str | None,
        candidate: Mapping[str, object],
    ) -> ReasoningDisplayProjectionDto:
        """构建用户可见推理摘要候选投影。

        :param trace_id: 关联逻辑链 ID。
        :param segment_id: 可选 segment ID。
        :param candidate: 上游提供的候选投影字段。
        :return: 经过安全字段检查的 reasoning display 投影 DTO。
        :raises VetTraceSchemaError: 当投影开关关闭或候选字段不安全时抛出。
        """

        if not self._settings.enable_reasoning_display_projection:
            raise VetTraceSchemaError(
                code=VetTraceErrorCode.VET_TRACE_PROJECTION_BUILD_FAILED,
                operation=VetTraceOperation.BUILD_REASONING_DISPLAY_PROJECTION,
                message="ReasoningDisplayProjection 当前未启用",
                retryable=False,
                trace_id=trace_id,
                conflict_with={"enabled": False},
            )
        if _contains_unsafe_reasoning_field(candidate):
            raise VetTraceSchemaError(
                code=VetTraceErrorCode.VET_TRACE_REASONING_DISPLAY_UNSAFE,
                operation=VetTraceOperation.BUILD_REASONING_DISPLAY_PROJECTION,
                message="用户可见推理摘要候选字段包含不可展示内容",
                retryable=False,
                trace_id=trace_id,
            )
        return ReasoningDisplayProjectionDto(
            projection_id=(
                _optional_str(candidate.get("projection_id"))
                or f"reasoning_display_{trace_id}"
            ),
            trace_id=trace_id,
            segment_id=segment_id,
            display_safety_level=(
                _optional_str(candidate.get("display_safety_level")) or "guard_required"
            ),
            understood_question_ref=_optional_str(
                candidate.get("understood_question_ref")
            ),
            reasoning_summary_ref=_optional_str(candidate.get("reasoning_summary_ref")),
            considered_domains=_string_list(candidate.get("considered_domains")),
            missing_information=_string_list(candidate.get("missing_information")),
            evidence_refs=_string_list(candidate.get("evidence_refs")),
            requires_output_guard=True,
        )

    def _validate_trace_patch(
        self,
        command: AppendTraceEventCommandDto,
    ) -> VetTraceValidationResultDto:
        """执行 VetTraceSchema 核心校验流程。

        :param command: LogicTraceStore 追加事件命令。
        :return: VetTraceSchema 完整业务校验结果。
        :raises VetTraceSchemaError: 当 schema 资源不可用时抛出。
        """

        if not self.is_ready():
            raise VetTraceSchemaError(
                code=VetTraceErrorCode.VET_TRACE_SCHEMA_RESOURCE_UNAVAILABLE,
                operation=VetTraceOperation.VALIDATE_TRACE_PATCH,
                message="VetTraceSchema schema registry 未就绪",
                retryable=True,
                request_id=command.request_id,
                trace_id=command.trace_id,
            )

        envelope = self._build_envelope(command)
        schema_ref = self._registry.resolve_schema_ref(
            schema_ref=envelope.schema_version,
            patch_type=envelope.patch_type,
        )
        payload_errors = self._registry.validate_payload(
            schema_ref=schema_ref,
            payload=envelope.payload,
        )
        errors = [
            f"{VetTraceErrorCode.VET_TRACE_PATCH_PAYLOAD_INVALID.value}: {error}"
            for error in payload_errors
        ]
        warnings = self._validate_envelope_consistency(
            command=command,
            envelope=envelope,
        )
        tier_decision = self.resolve_audit_tier(envelope)
        capture_policy = self.apply_capture_policy(
            audit_tier=tier_decision.resolved_tier,
            policy_version=envelope.capture_policy_version,
        )
        errors.extend(
            self._validate_policy_requirements(
                envelope=envelope,
                tier_decision=tier_decision,
                capture_policy=capture_policy,
            )
        )
        if tier_decision.upgraded:
            warnings.append("audit_tier_upgraded_by_structured_facts")
        degraded_flags = list(envelope.degraded_flags)
        if tier_decision.upgraded:
            degraded_flags.append("audit_tier_upgraded")
        if errors and not self._settings.strict_mode:
            warnings.extend(errors)
            degraded_flags.append("vet_trace_schema_non_strict_acceptance")
            errors = []
        normalized_payload = self._build_normalized_payload(
            envelope=envelope,
            schema_ref=schema_ref,
            tier_decision=tier_decision,
            capture_policy=capture_policy,
        )
        valid = not errors
        return VetTraceValidationResultDto(
            valid=valid,
            validation_status=_result_status(
                valid=valid,
                warnings=warnings,
                degraded_flags=degraded_flags,
            ),
            schema_ref=schema_ref,
            errors=errors,
            warnings=warnings,
            degraded_flags=degraded_flags,
            normalized_business_payload=normalized_payload,
            tier_decision=tier_decision,
            capture_policy=capture_policy,
        )

    def _build_envelope(
        self,
        command: AppendTraceEventCommandDto,
    ) -> TracePatchEnvelopeDto:
        """从 LogicTraceStore 事件命令构建标准 trace patch envelope。

        :param command: LogicTraceStore 追加事件命令。
        :return: 标准 trace patch envelope。
        :raises VetTraceSchemaError: 当必填 envelope 字段缺失或冲突时抛出。
        """

        raw_payload = dict(command.business_payload)
        payload = self._extract_payload(raw_payload)
        pet_id = _optional_str(raw_payload.get("pet_id"))
        params_version = _optional_str(raw_payload.get("params_version"))
        if pet_id is None:
            raise VetTraceSchemaError(
                code=VetTraceErrorCode.VET_TRACE_PATCH_ENVELOPE_INVALID,
                operation=VetTraceOperation.VALIDATE_TRACE_PATCH,
                message="业务 trace patch 缺少 pet_id",
                retryable=False,
                request_id=command.request_id,
                trace_id=command.trace_id,
            )
        if params_version is None:
            raise VetTraceSchemaError(
                code=VetTraceErrorCode.VET_TRACE_PATCH_ENVELOPE_INVALID,
                operation=VetTraceOperation.VALIDATE_TRACE_PATCH,
                message="业务 trace patch 缺少 params_version",
                retryable=False,
                request_id=command.request_id,
                trace_id=command.trace_id,
            )
        artifact_refs = _string_list(raw_payload.get("artifact_refs"))
        if len(artifact_refs) > self._settings.max_artifact_refs:
            raise VetTraceSchemaError(
                code=VetTraceErrorCode.VET_TRACE_PATCH_ENVELOPE_INVALID,
                operation=VetTraceOperation.VALIDATE_TRACE_PATCH,
                message="业务 trace patch artifact 引用数量超过上限",
                retryable=False,
                request_id=command.request_id,
                trace_id=command.trace_id,
                conflict_with={"max_artifact_refs": self._settings.max_artifact_refs},
            )
        return TracePatchEnvelopeDto(
            trace_id=command.trace_id,
            request_id=command.request_id,
            run_id=_optional_str(raw_payload.get("run_id")),
            session_id=_optional_str(raw_payload.get("session_id")),
            user_id=_optional_str(raw_payload.get("user_id")),
            pet_id=pet_id,
            params_version=params_version,
            trace_schema_version=(
                _optional_str(raw_payload.get("trace_schema_version"))
                or self._settings.default_trace_schema_version
            ),
            capture_policy_version=(
                _optional_str(raw_payload.get("capture_policy_version"))
                or self._settings.default_capture_policy_version
            ),
            source_component=(
                _optional_str(raw_payload.get("source_component"))
                or command.source_component
            ),
            patch_type=(
                _optional_str(raw_payload.get("patch_type")) or command.event_type
            ),
            schema_version=(
                _optional_str(raw_payload.get("schema_version"))
                or command.schema_ref
                or self._registry.bundle.default_schema_ref
            ),
            task_id=_optional_str(raw_payload.get("task_id")) or command.task_id,
            segment_id=(
                _optional_str(raw_payload.get("segment_id")) or command.segment_id
            ),
            payload=payload,
            artifact_refs=artifact_refs,
            degraded_flags=[
                *_string_list(raw_payload.get("degraded_flags")),
                *_string_list(command.summary.get("degraded_flags")),
            ],
        )

    def _extract_payload(self, raw_payload: Mapping[str, object]) -> JsonMap:
        """从业务 payload 根对象中提取私有 payload。

        :param raw_payload: LogicTraceStore 事件中的 business_payload。
        :return: 业务组件私有 payload；没有显式 payload 字段时返回剥离 envelope 字段后的根对象。
        """

        nested_payload = raw_payload.get("payload")
        if _is_mapping(nested_payload):
            return _to_json_map(nested_payload)
        return {
            str(key): item
            for key, item in raw_payload.items()
            if str(key) not in _ENVELOPE_FIELD_NAMES
        }

    def _validate_envelope_consistency(
        self,
        *,
        command: AppendTraceEventCommandDto,
        envelope: TracePatchEnvelopeDto,
    ) -> list[str]:
        """校验事件命令与业务 envelope 的局部一致性。

        :param command: LogicTraceStore 追加事件命令。
        :param envelope: 标准 trace patch envelope。
        :return: 非阻断一致性告警列表。
        """

        warnings: list[str] = []
        payload_pet_id = _optional_str(envelope.payload.get("pet_id"))
        if payload_pet_id is not None and payload_pet_id != envelope.pet_id:
            warnings.append(
                f"{VetTraceErrorCode.VET_TRACE_PET_CONFLICT.value}: payload.pet_id 与 envelope.pet_id 不一致"
            )
        if command.task_id is not None and envelope.task_id != command.task_id:
            warnings.append(
                f"{VetTraceErrorCode.VET_TRACE_SEGMENT_INCONSISTENT.value}: task_id 与事件命令不一致"
            )
        if command.segment_id is not None and envelope.segment_id != command.segment_id:
            warnings.append(
                f"{VetTraceErrorCode.VET_TRACE_SEGMENT_INCONSISTENT.value}: segment_id 与事件命令不一致"
            )
        return warnings

    def _validate_policy_requirements(
        self,
        *,
        envelope: TracePatchEnvelopeDto,
        tier_decision: AuditTierDecisionDto,
        capture_policy: CapturePolicyDecisionDto,
    ) -> list[str]:
        """校验 capture policy 对当前 patch 的阻断级要求。

        :param envelope: 标准 trace patch envelope。
        :param tier_decision: 审计等级裁决结果。
        :param capture_policy: capture policy 裁决结果。
        :return: 阻断级错误列表。
        """

        errors: list[str] = []
        payload = envelope.payload
        if (
            tier_decision.resolved_tier is VetTraceAuditTier.A
            and capture_policy.required_artifact_types
            and not envelope.artifact_refs
            and not envelope.degraded_flags
        ):
            errors.append(
                f"{VetTraceErrorCode.VET_TRACE_REQUIRED_ARTIFACT_MISSING.value}: A 级 patch 缺少三联稿或显式 artifact 降级标记"
            )
        final_response_keys = frozenset({"final_response", "final_response_ref"})
        guard_keys = frozenset(
            {
                "guard_actions",
                "guard_chain_ref",
                "output_review_ref",
                "deterministic_gate_ref",
            }
        )
        if _has_any_key(payload, final_response_keys) and not _has_any_key(
            payload,
            guard_keys,
        ):
            errors.append(
                f"{VetTraceErrorCode.VET_TRACE_GUARD_CHAIN_INCOMPLETE.value}: 用户可见 final_response 缺少输出安全审查或兜底门引用"
            )
        if (
            envelope.patch_type == "reasoning_display"
            or _has_any_key(payload, frozenset({"reasoning_display"}))
        ) and _contains_unsafe_reasoning_field(payload):
            errors.append(
                f"{VetTraceErrorCode.VET_TRACE_REASONING_DISPLAY_UNSAFE.value}: reasoning display 包含不可展示字段"
            )
        return errors

    def _build_normalized_payload(
        self,
        *,
        envelope: TracePatchEnvelopeDto,
        schema_ref: str,
        tier_decision: AuditTierDecisionDto,
        capture_policy: CapturePolicyDecisionDto,
    ) -> JsonMap:
        """构建可写入 LogicTraceStore 的标准业务 payload。

        :param envelope: 标准 trace patch envelope。
        :param schema_ref: 生效 payload schema 引用。
        :param tier_decision: 审计等级裁决结果。
        :param capture_policy: capture policy 裁决结果。
        :return: 已应用字段裁剪和策略摘要的业务 payload。
        """

        redact_fields = frozenset(
            field.lower() for field in capture_policy.redact_fields
        )
        redacted_payload = _redact_value(envelope.payload, redact_fields=redact_fields)
        return {
            "trace_patch_envelope": _envelope_to_map(envelope),
            "payload": redacted_payload,
            "schema_ref": schema_ref,
            "audit_tier_decision": _tier_decision_to_map(tier_decision),
            "capture_policy": _capture_policy_to_map(capture_policy),
        }

    def _build_rejected_result(
        self,
        *,
        error_code: VetTraceErrorCode,
        message: str,
        request_id: str | None,
        trace_id: str | None,
        schema_ref: str | None,
    ) -> VetTraceValidationResultDto:
        """构建阻断级校验结果。

        :param error_code: VetTraceSchema 稳定错误码。
        :param message: 面向工程排障的错误说明。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次逻辑链 ID。
        :param schema_ref: 可选 schema 引用。
        :return: VetTraceSchema 阻断级校验结果。
        """

        del request_id, trace_id
        return VetTraceValidationResultDto(
            valid=False,
            validation_status=VetTraceValidationStatus.REJECTED,
            schema_ref=schema_ref,
            errors=[f"{error_code.value}: {message}"],
            warnings=[],
            degraded_flags=[],
            normalized_business_payload={},
            tier_decision=None,
            capture_policy=None,
        )

    def _record_observability(
        self,
        *,
        result: VetTraceValidationResultDto,
        duration_seconds: float,
    ) -> None:
        """记录 VetTraceSchema 校验指标和结构化事件。

        :param result: 当前校验结果。
        :param duration_seconds: 本次校验耗时，单位为秒。
        :return: None。
        """

        provider = self._observability_provider
        if provider is None:
            return
        try:
            status = result.validation_status.value
            provider.record_metric(
                metric_name="vet_trace_schema_validation_total",
                value=1.0,
                metric_type=MetricType.COUNTER,
                labels={"component": _COMPONENT_NAME, "status": status},
                description="VetTraceSchema 校验总数。",
            )
            provider.record_metric(
                metric_name="vet_trace_schema_validation_duration_seconds",
                value=duration_seconds,
                metric_type=MetricType.HISTOGRAM,
                labels={"component": _COMPONENT_NAME, "status": status},
                description="VetTraceSchema 校验耗时，单位为秒。",
            )
            provider.record_event(
                event_name="vet_trace_schema.validated",
                component=_COMPONENT_NAME,
                level=(
                    StructuredLogLevel.INFO
                    if result.valid
                    else StructuredLogLevel.WARNING
                ),
                safe_fields={
                    "status": status,
                    "schema_ref": result.schema_ref,
                    "degraded": bool(result.degraded_flags),
                    "error_count": len(result.errors),
                    "warning_count": len(result.warnings),
                },
            )
        except Exception:
            return


def create_default_vet_trace_schema(
    *,
    observability_provider: ObservabilityProvider | None = None,
) -> DefaultVetTraceSchema:
    """创建默认 VetTraceSchema 应用内服务。

    :param observability_provider: 可选 Observability provider。
    :return: 已装配内置 schema bundle 的默认 VetTraceSchema 服务。
    """

    registry = VetTraceSchemaRegistry(bundle=create_default_vet_trace_schema_bundle())
    return DefaultVetTraceSchema(
        registry=registry,
        observability_provider=observability_provider,
    )


__all__: tuple[str, ...] = (
    "DefaultVetTraceSchema",
    "VetTraceSchema",
    "create_default_vet_trace_schema",
)

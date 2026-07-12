##################################################################################################
# 文件: tests/vet_trace_schema/test_component_contract.py
# 作用: 验证 VetTraceSchema 默认组件的 schema 校验、capture policy、裁剪和 LogicTraceStore 适配契约。
# 边界: 仅测试 VetTraceSchema 应用内服务，不访问数据库、不启动 FastAPI、不执行真实业务图。
##################################################################################################

import asyncio
from datetime import UTC, datetime
from typing import cast

import pytest

from veterinary_agent.logic_trace_store import AppendTraceEventCommandDto
from veterinary_agent.observability import ObservabilityProvider
from veterinary_agent.vet_trace_schema import (
    DefaultVetTraceSchema,
    VetTraceAuditTier,
    VetTraceCapturePolicyDto,
    VetTraceErrorCode,
    VetTraceOperation,
    VetTracePatchSchemaDto,
    VetTraceSchemaError,
    VetTraceSchemaRegistry,
    VetTraceSchemaSettings,
    VetTraceValidationStatus,
    create_default_vet_trace_schema_bundle,
    create_default_vet_trace_schema,
)


def _now() -> datetime:
    """读取测试用当前 UTC 时间。

    :return: 当前 UTC 时间。
    """

    return datetime.now(UTC)


def _build_event_command(
    *,
    business_payload: dict[str, object],
    schema_ref: str = "vet.output-review.trace.v1",
    event_type: str = "output_review",
    task_id: str | None = "task_vet_trace",
    segment_id: str | None = "segment_vet_trace",
) -> AppendTraceEventCommandDto:
    """构建测试用 LogicTraceStore 追加事件命令。

    :param business_payload: 业务 trace patch payload。
    :param schema_ref: 当前事件声明的 schema 引用。
    :param event_type: 当前事件类型。
    :param task_id: 可选任务 ID。
    :param segment_id: 可选 segment ID。
    :return: 可传给 VetTraceSchema 的追加事件命令。
    """

    return AppendTraceEventCommandDto(
        request_id="req_vet_trace",
        trace_id="trace_vet_trace",
        event_id="event_vet_trace",
        event_type=event_type,
        source_component="VetOutputSafetyReviewer",
        task_id=task_id,
        segment_id=segment_id,
        business_payload=business_payload,
        schema_ref=schema_ref,
        created_at=_now(),
    )


def _base_business_payload(
    *,
    segment_type: str,
    patch_type: str = "output_review",
    schema_version: str = "vet.output-review.trace.v1",
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    """构建带标准 envelope 字段的测试业务 payload。

    :param segment_type: 当前业务 segment 类型。
    :param patch_type: 当前业务 patch 类型。
    :param schema_version: 当前 payload schema 版本。
    :param payload: 可选业务私有 payload 覆盖。
    :return: 可放入 AppendTraceEventCommandDto 的业务 payload。
    """

    resolved_payload: dict[str, object] = {"segment_type": segment_type}
    if payload is not None:
        resolved_payload.update(payload)
    return {
        "pet_id": "pet_1",
        "params_version": "params.v1",
        "patch_type": patch_type,
        "schema_version": schema_version,
        "payload": resolved_payload,
    }


class RaisingObservabilityProvider:
    """测试用 Observability 空壳，模拟观测依赖异常。"""

    def record_metric(
        self,
        *,
        metric_name: str,
        value: float,
        metric_type: object,
        labels: dict[str, str],
        description: str,
    ) -> None:
        """模拟指标记录异常。

        :param metric_name: 指标名称。
        :param value: 指标值。
        :param metric_type: 指标类型。
        :param labels: 指标低基数标签。
        :param description: 指标描述。
        :return: None。
        :raises RuntimeError: 固定抛出以验证业务校验不被观测异常阻断。
        """

        del metric_name, value, metric_type, labels, description
        raise RuntimeError("observability unavailable")

    def record_event(
        self,
        *,
        event_name: str,
        component: str,
        level: object,
        safe_fields: dict[str, object],
    ) -> None:
        """模拟结构化事件记录异常。

        :param event_name: 事件名称。
        :param component: 组件名。
        :param level: 事件级别。
        :param safe_fields: 允许进入日志的安全字段。
        :return: None。
        :raises RuntimeError: 固定抛出以验证业务校验不被观测异常阻断。
        """

        del event_name, component, level, safe_fields
        raise RuntimeError("observability unavailable")


def test_default_vet_trace_schema_public_contract_is_ready() -> None:
    """验证默认组件通过公开出口创建后具备基础校验能力。

    :return: None。
    """

    schema = create_default_vet_trace_schema()
    bundle = create_default_vet_trace_schema_bundle()

    assert schema.is_ready()
    assert bundle.trace_schema_version == "vet-trace-schema.v1"
    assert {policy.audit_tier for policy in bundle.capture_policies} == {
        VetTraceAuditTier.A,
        VetTraceAuditTier.B,
        VetTraceAuditTier.C,
    }


def test_default_vet_trace_schema_accepts_and_normalizes_a_tier_patch() -> None:
    """验证默认组件可接受 A 级输出审查 patch 并写入标准化负载。

    :return: None。
    """

    schema = create_default_vet_trace_schema()
    command = _build_event_command(
        business_payload={
            "pet_id": "pet_1",
            "params_version": "params.v1",
            "patch_type": "output_review",
            "schema_version": "vet.output-review.trace.v1",
            "artifact_refs": ["artifact_guard_triple_1"],
            "payload": {
                "segment_type": "standard",
                "audit_tier": "B",
                "guard_actions": [{"action_type": "soften"}],
                "final_response_ref": "artifact_final_1",
                "draft_response": "原始草稿不应进入标准化 payload。",
            },
        }
    )

    result = schema.validate_trace_patch(command)

    assert result.valid
    assert (
        result.validation_status is VetTraceValidationStatus.ACCEPTED_WITH_DEGRADATION
    )
    assert result.tier_decision is not None
    assert result.tier_decision.resolved_tier.value == "A"
    assert "audit_tier_upgraded" in result.degraded_flags
    normalized_payload = result.normalized_business_payload["payload"]
    assert isinstance(normalized_payload, dict)
    assert normalized_payload["draft_response"] == "[redacted]"


def test_default_vet_trace_schema_recursively_redacts_sensitive_payload_fields() -> (
    None
):
    """验证 capture policy 会递归裁剪敏感业务字段。

    :return: None。
    """

    schema = create_default_vet_trace_schema()
    command = _build_event_command(
        business_payload={
            "pet_id": "pet_1",
            "params_version": "params.v1",
            "patch_type": "output_review",
            "schema_version": "vet.output-review.trace.v1",
            "artifact_refs": ["artifact_guard_triple_1"],
            "payload": {
                "segment_type": "standard",
                "guard_actions": [{"action_type": "replace"}],
                "final_response_ref": "artifact_final_1",
                "nested": {
                    "chain_of_thought": "隐藏推理",
                    "safe_summary": "允许保留的摘要",
                },
                "items": [{"raw_prompt": "内部提示词"}],
            },
        }
    )

    result = schema.validate_trace_patch(command)

    assert result.valid
    payload = result.normalized_business_payload["payload"]
    assert isinstance(payload, dict)
    nested = payload["nested"]
    items = payload["items"]
    assert isinstance(nested, dict)
    assert isinstance(items, list)
    assert nested["chain_of_thought"] == "[redacted]"
    assert nested["safe_summary"] == "允许保留的摘要"
    assert isinstance(items[0], dict)
    assert items[0]["raw_prompt"] == "[redacted]"


@pytest.mark.parametrize(
    ("business_payload", "expected_error"),
    [
        (
            {"params_version": "params.v1", "payload": {"segment_type": "education"}},
            VetTraceErrorCode.VET_TRACE_PATCH_ENVELOPE_INVALID,
        ),
        (
            {"pet_id": "pet_1", "payload": {"segment_type": "education"}},
            VetTraceErrorCode.VET_TRACE_PATCH_ENVELOPE_INVALID,
        ),
    ],
)
def test_default_vet_trace_schema_rejects_missing_required_envelope_fields(
    business_payload: dict[str, object],
    expected_error: VetTraceErrorCode,
) -> None:
    """验证缺少必填 envelope 字段时返回稳定错误码。

    :param business_payload: 待校验的业务 payload。
    :param expected_error: 期望命中的稳定错误码。

    :return: None。
    """

    schema = create_default_vet_trace_schema()
    command = _build_event_command(business_payload=business_payload)

    result = schema.validate_trace_patch(command)

    assert not result.valid
    assert result.validation_status is VetTraceValidationStatus.REJECTED
    assert result.errors[0].startswith(expected_error.value)


def test_default_vet_trace_schema_reports_segment_consistency_warnings() -> None:
    """验证 task_id 与 segment_id 不一致时产生非阻断一致性告警。

    :return: None。
    """

    schema = create_default_vet_trace_schema()
    command = _build_event_command(
        business_payload={
            **_base_business_payload(
                segment_type="education",
                payload={"signals": ["SAF_HINT"]},
            ),
            "task_id": "task_from_payload",
            "segment_id": "segment_from_payload",
        },
        task_id="task_from_command",
        segment_id="segment_from_command",
    )

    result = schema.validate_trace_patch(command)

    assert result.valid
    assert any("task_id" in warning for warning in result.warnings)
    assert any("segment_id" in warning for warning in result.warnings)


def test_registry_rejects_unknown_schema_when_patch_type_has_no_fallback() -> None:
    """验证未知 schema 且 patch_type 无回退规则时返回稳定错误码。

    :return: None。
    """

    schema = create_default_vet_trace_schema()
    command = _build_event_command(
        business_payload=_base_business_payload(
            segment_type="education",
            patch_type="unknown_patch",
            schema_version="unknown.schema.v1",
        ),
        schema_ref="unknown.schema.v1",
        event_type="unknown_patch",
    )

    result = schema.validate_trace_patch(command)

    assert not result.valid
    assert result.errors[0].startswith(
        VetTraceErrorCode.VET_TRACE_SCHEMA_VERSION_NOT_FOUND.value
    )


def test_registry_rejects_payload_that_violates_json_schema() -> None:
    """验证 JSON Schema payload 校验失败时返回 payload 错误码。

    :return: None。
    """

    bundle = create_default_vet_trace_schema_bundle()
    bundle = bundle.model_copy(
        update={
            "patch_schemas": [
                *bundle.patch_schemas,
                VetTracePatchSchemaDto(
                    schema_ref="vet.strict.trace.v1",
                    patch_type="strict_patch",
                    schema_version="vet.strict.trace.v1",
                    json_schema={
                        "$schema": "https://json-schema.org/draft/2020-12/schema",
                        "type": "object",
                        "required": ["required_flag"],
                        "properties": {"required_flag": {"type": "boolean"}},
                        "additionalProperties": True,
                    },
                ),
            ]
        }
    )
    schema = DefaultVetTraceSchema(registry=VetTraceSchemaRegistry(bundle=bundle))
    command = _build_event_command(
        business_payload=_base_business_payload(
            segment_type="education",
            patch_type="strict_patch",
            schema_version="vet.strict.trace.v1",
            payload={"required_flag": "not_boolean"},
        ),
        schema_ref="vet.strict.trace.v1",
        event_type="strict_patch",
    )

    result = schema.validate_trace_patch(command)

    assert not result.valid
    assert any(
        error.startswith(VetTraceErrorCode.VET_TRACE_PATCH_PAYLOAD_INVALID.value)
        for error in result.errors
    )


@pytest.mark.parametrize(
    ("payload", "expected_tier", "expected_reason"),
    [
        (
            {"segment_type": "standard"},
            VetTraceAuditTier.A,
            "medical_or_safety_segment",
        ),
        (
            {"segment_type": "safety_trigger"},
            VetTraceAuditTier.A,
            "medical_or_safety_segment",
        ),
        ({"ocr_used": True}, VetTraceAuditTier.A, "ocr_or_report_used_as_evidence"),
        (
            {"medication_risk_tier": "T3"},
            VetTraceAuditTier.A,
            "medication_policy_high_risk",
        ),
        ({"segment_type": "education"}, VetTraceAuditTier.B, "education_segment"),
        ({"signals": ["SAF_HINT"]}, VetTraceAuditTier.B, "signals_present"),
        (
            {"segment_type": "nonmedical"},
            VetTraceAuditTier.C,
            "pure_nonmedical_without_signals",
        ),
    ],
)
def test_audit_tier_resolver_maps_structured_facts(
    payload: dict[str, object],
    expected_tier: VetTraceAuditTier,
    expected_reason: str,
) -> None:
    """验证 audit tier resolver 按结构化事实解析 A/B/C 等级。

    :param payload: 业务私有 payload。
    :param expected_tier: 期望解析出的审计等级。
    :param expected_reason: 期望包含的解析原因码。
    :return: None。
    """

    schema = create_default_vet_trace_schema()
    command = _build_event_command(
        business_payload={
            **_base_business_payload(
                segment_type=str(payload.get("segment_type", "nonmedical")),
                payload=payload,
            ),
            "artifact_refs": ["artifact_guard_triple_1"],
        }
    )

    result = schema.validate_trace_patch(command)

    assert result.valid
    assert result.tier_decision is not None
    assert result.tier_decision.resolved_tier is expected_tier
    assert expected_reason in result.tier_decision.reason_codes


def test_audit_tier_resolver_never_downgrades_declared_higher_tier() -> None:
    """验证上游声明更高审计等级时不会被结构化事实降级。

    :return: None。
    """

    schema = create_default_vet_trace_schema()
    command = _build_event_command(
        business_payload={
            **_base_business_payload(
                segment_type="nonmedical",
                payload={"segment_type": "nonmedical", "audit_tier": "A"},
            ),
            "artifact_refs": ["artifact_guard_triple_1"],
        }
    )

    result = schema.validate_trace_patch(command)

    assert result.valid
    assert result.tier_decision is not None
    assert result.tier_decision.resolved_tier is VetTraceAuditTier.A
    assert not result.tier_decision.upgraded


def test_capture_policy_rejects_a_tier_patch_without_artifact_or_degradation() -> None:
    """验证 A 级 patch 缺少 artifact 且无显式降级时被拒绝。

    :return: None。
    """

    schema = create_default_vet_trace_schema()
    command = _build_event_command(
        business_payload=_base_business_payload(
            segment_type="standard",
            payload={"segment_type": "standard"},
        )
    )

    result = schema.validate_trace_patch(command)

    assert not result.valid
    assert any(
        error.startswith(VetTraceErrorCode.VET_TRACE_REQUIRED_ARTIFACT_MISSING.value)
        for error in result.errors
    )


def test_capture_policy_allows_a_tier_patch_with_explicit_artifact_degradation() -> (
    None
):
    """验证 A 级 patch 有显式 artifact 降级标记时允许降级写入。

    :return: None。
    """

    schema = create_default_vet_trace_schema()
    command = _build_event_command(
        business_payload={
            **_base_business_payload(
                segment_type="standard",
                payload={"segment_type": "standard"},
            ),
            "degraded_flags": ["guard_triple_artifact_unavailable"],
        }
    )

    result = schema.validate_trace_patch(command)

    assert result.valid
    assert (
        result.validation_status is VetTraceValidationStatus.ACCEPTED_WITH_DEGRADATION
    )
    assert "guard_triple_artifact_unavailable" in result.degraded_flags


def test_final_response_without_guard_chain_is_rejected() -> None:
    """验证用户可见 final response 缺少 guard chain 时被拒绝。

    :return: None。
    """

    schema = create_default_vet_trace_schema()
    command = _build_event_command(
        business_payload={
            **_base_business_payload(
                segment_type="standard",
                payload={
                    "segment_type": "standard",
                    "final_response_ref": "artifact_final_1",
                },
            ),
            "artifact_refs": ["artifact_guard_triple_1"],
        }
    )

    result = schema.validate_trace_patch(command)

    assert not result.valid
    assert any(
        error.startswith(VetTraceErrorCode.VET_TRACE_GUARD_CHAIN_INCOMPLETE.value)
        for error in result.errors
    )


def test_non_strict_mode_converts_blocking_errors_to_degraded_acceptance() -> None:
    """验证非严格模式会把阻断级策略错误转换为降级接受。

    :return: None。
    """

    schema = DefaultVetTraceSchema(
        settings=VetTraceSchemaSettings(strict_mode=False),
    )
    command = _build_event_command(
        business_payload=_base_business_payload(
            segment_type="standard",
            payload={"segment_type": "standard"},
        )
    )

    result = schema.validate_trace_patch(command)

    assert result.valid
    assert (
        result.validation_status is VetTraceValidationStatus.ACCEPTED_WITH_DEGRADATION
    )
    assert "vet_trace_schema_non_strict_acceptance" in result.degraded_flags
    assert any(
        warning.startswith(VetTraceErrorCode.VET_TRACE_REQUIRED_ARTIFACT_MISSING.value)
        for warning in result.warnings
    )


def test_registry_rejects_missing_capture_policy() -> None:
    """验证 capture policy 资源缺失时返回稳定错误码。

    :return: None。
    """

    bundle = create_default_vet_trace_schema_bundle()
    bundle = bundle.model_copy(
        update={
            "capture_policies": [
                policy
                for policy in bundle.capture_policies
                if policy.audit_tier is not VetTraceAuditTier.B
            ]
        }
    )
    schema = DefaultVetTraceSchema(registry=VetTraceSchemaRegistry(bundle=bundle))
    command = _build_event_command(
        business_payload=_base_business_payload(
            segment_type="education",
            payload={"segment_type": "education"},
        )
    )

    result = schema.validate_trace_patch(command)

    assert not result.valid
    assert result.errors[0].startswith(
        VetTraceErrorCode.VET_TRACE_SCHEMA_RESOURCE_UNAVAILABLE.value
    )


def test_registry_initialization_rejects_invalid_json_schema_resource() -> None:
    """验证非法 JSON Schema 资源会在 registry 初始化阶段被拒绝。

    :return: None。
    """

    bundle = create_default_vet_trace_schema_bundle()
    invalid_bundle = bundle.model_copy(
        update={
            "patch_schemas": [
                *bundle.patch_schemas,
                VetTracePatchSchemaDto(
                    schema_ref="vet.invalid.trace.v1",
                    patch_type="invalid_patch",
                    schema_version="vet.invalid.trace.v1",
                    json_schema={"type": 123},
                ),
            ]
        }
    )

    with pytest.raises(VetTraceSchemaError) as exc_info:
        VetTraceSchemaRegistry(bundle=invalid_bundle)

    assert (
        exc_info.value.code is VetTraceErrorCode.VET_TRACE_SCHEMA_RESOURCE_UNAVAILABLE
    )
    assert exc_info.value.operation is VetTraceOperation.LOAD_SCHEMA_BUNDLE


def test_capture_policy_resolver_rejects_unknown_policy_version() -> None:
    """验证未知 capture policy 版本会被策略解析器拒绝。

    :return: None。
    """

    schema = create_default_vet_trace_schema()

    with pytest.raises(VetTraceSchemaError) as exc_info:
        schema.apply_capture_policy(
            audit_tier=VetTraceAuditTier.A,
            policy_version="unknown-policy.v1",
        )

    assert exc_info.value.code is VetTraceErrorCode.VET_TRACE_CAPTURE_POLICY_NOT_FOUND


def test_reasoning_display_projection_accepts_safe_candidate() -> None:
    """验证安全的用户可见推理摘要候选投影可构建且仍要求输出审查。

    :return: None。
    """

    schema = create_default_vet_trace_schema()

    projection = schema.build_reasoning_display_projection(
        trace_id="trace_vet_trace",
        segment_id="segment_vet_trace",
        candidate={
            "projection_id": "reasoning_1",
            "reasoning_summary_ref": "artifact_reasoning_summary_1",
            "considered_domains": ["nutrition", "triage"],
            "missing_information": ["体重"],
            "evidence_refs": ["rag_1"],
        },
    )

    assert projection.projection_id == "reasoning_1"
    assert projection.requires_output_guard
    assert projection.considered_domains == ["nutrition", "triage"]


def test_reasoning_display_projection_can_be_disabled_by_settings() -> None:
    """验证关闭 reasoning display 投影开关后候选投影被拒绝。

    :return: None。
    """

    schema = DefaultVetTraceSchema(
        settings=VetTraceSchemaSettings(enable_reasoning_display_projection=False),
    )

    with pytest.raises(VetTraceSchemaError) as exc_info:
        schema.build_reasoning_display_projection(
            trace_id="trace_vet_trace",
            segment_id=None,
            candidate={"projection_id": "reasoning_1"},
        )

    assert exc_info.value.code is VetTraceErrorCode.VET_TRACE_PROJECTION_BUILD_FAILED


def test_default_vet_trace_schema_adapts_logic_trace_validator_protocol() -> None:
    """验证默认组件可作为 LogicTraceStore schema validator 使用。

    :return: None。
    """

    schema = create_default_vet_trace_schema()
    command = _build_event_command(
        business_payload={
            "pet_id": "pet_1",
            "params_version": "params.v1",
            "patch_type": "input_safety",
            "schema_version": "vet.input-safety.trace.v1",
            "payload": {
                "segment_type": "education",
                "signals": ["SAF_HINT"],
            },
        },
        schema_ref="vet.input-safety.trace.v1",
    )

    result = asyncio.run(schema.validate_trace_event(command))

    assert result.valid
    assert result.schema_ref == "vet.input-safety.trace.v1"
    assert "trace_patch_envelope" in result.normalized_business_payload


def test_observability_failure_does_not_block_validation() -> None:
    """验证 Observability 异常不会阻断 VetTraceSchema 主校验流程。

    :return: None。
    """

    schema = DefaultVetTraceSchema(
        observability_provider=cast(
            ObservabilityProvider,
            RaisingObservabilityProvider(),
        )
    )
    command = _build_event_command(
        business_payload={
            **_base_business_payload(
                segment_type="education",
                payload={"segment_type": "education"},
            ),
            "artifact_refs": [],
        },
        schema_ref="vet.output-review.trace.v1",
    )

    result = schema.validate_trace_patch(command)

    assert result.valid
    assert result.tier_decision is not None
    assert result.tier_decision.resolved_tier is VetTraceAuditTier.B


def test_reasoning_display_projection_rejects_hidden_reasoning_fields() -> None:
    """验证用户可见推理摘要候选投影会拒绝隐藏推理字段。

    :return: None。
    """

    schema = create_default_vet_trace_schema()

    with pytest.raises(VetTraceSchemaError) as exc_info:
        schema.build_reasoning_display_projection(
            trace_id="trace_vet_trace",
            segment_id="segment_vet_trace",
            candidate={
                "projection_id": "reasoning_1",
                "hidden_chain_of_thought": "不允许展示的内部推理。",
            },
        )

    assert exc_info.value.code is VetTraceErrorCode.VET_TRACE_REASONING_DISPLAY_UNSAFE


def test_registry_default_bundle_contains_expected_capture_views() -> None:
    """验证默认 A/B/C capture policy 包含预期投影视图。

    :return: None。
    """

    bundle = create_default_vet_trace_schema_bundle()
    policies: dict[VetTraceAuditTier, VetTraceCapturePolicyDto] = {
        policy.audit_tier: policy for policy in bundle.capture_policies
    }

    assert "artifact_view" in policies[VetTraceAuditTier.A].projection_views
    assert "reasoning_display" in policies[VetTraceAuditTier.B].projection_views
    assert policies[VetTraceAuditTier.C].projection_views == ["timeline_view"]

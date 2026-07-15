##################################################################################################
# 文件: tests/agent_spec_registry/test_default_registry.py
# 作用: 验证默认 AgentSpecRegistry 可从 RuntimeConfig 派生真实业务图所需的子 Agent 规格。
# 边界: 仅做配置到规格表的组合测试；不调用真实模型、不启动 FastAPI、不执行完整业务图。
##################################################################################################

import pytest

from veterinary_agent.agent_runner import (
    AgentResponseFormat,
    AgentRunnerError,
    AgentRunnerErrorCode,
)
from veterinary_agent.agent_spec_registry import create_default_agent_spec_registry
from veterinary_agent.config import (
    ApiIngressSettings,
    CheckpointStoreSettings,
    LlmGatewaySettings,
    RuntimeConfigSettings,
    RuntimeConfigSnapshot,
    StandardConsultationAgentSettings,
    VetInputSafetyAssessorSettings,
    build_runtime_config_snapshot,
)

from tests.llm_gateway import build_test_settings


def _build_snapshot(
    *,
    llm_gateway_settings: LlmGatewaySettings,
    standard_consultation_settings: StandardConsultationAgentSettings | None = None,
    vet_input_safety_assessor_settings: VetInputSafetyAssessorSettings | None = None,
) -> RuntimeConfigSnapshot:
    """构建 AgentSpecRegistry 测试用 RuntimeConfig 快照。

    :param llm_gateway_settings: 测试用 LlmGateway 配置。
    :param standard_consultation_settings: 可选标准问诊配置覆盖。
    :param vet_input_safety_assessor_settings: 可选输入安全评估配置覆盖。
    :return: 可用于创建默认 AgentSpecRegistry 的 RuntimeConfig 快照。
    """

    return build_runtime_config_snapshot(
        runtime_config_settings=RuntimeConfigSettings(
            params_version="params.agent-spec-registry.test"
        ),
        api_ingress_settings=ApiIngressSettings(),
        checkpoint_store_settings=CheckpointStoreSettings(),
        llm_gateway_settings=llm_gateway_settings,
        standard_consultation_settings=standard_consultation_settings,
        vet_input_safety_assessor_settings=vet_input_safety_assessor_settings,
    )


def test_default_registry_is_empty_when_llm_gateway_disabled() -> None:
    """验证 LlmGateway 禁用时默认注册表保持空目录且可读取。

    :return: None。
    """

    snapshot = _build_snapshot(
        llm_gateway_settings=LlmGatewaySettings(
            enabled=False,
            provider_routes=[],
            model_profiles=[],
        )
    )

    registry = create_default_agent_spec_registry(snapshot)

    assert registry.is_ready()
    assert registry.list_specs() == []


def test_default_registry_builds_real_business_specs_when_llm_gateway_enabled() -> None:
    """验证 LlmGateway 启用时默认注册表补齐真实业务图引用的核心规格。

    :return: None。
    """

    snapshot = _build_snapshot(llm_gateway_settings=build_test_settings())

    registry = create_default_agent_spec_registry(snapshot)
    specs = registry.list_specs()
    keys = {(spec.agent_id, spec.agent_version) for spec in specs}
    expected_keys = {
        (
            snapshot.vet_task_decomposer.decompose_agent_id,
            snapshot.vet_task_decomposer.decompose_agent_version,
        ),
        (
            snapshot.vet_task_decomposer.review_agent_id,
            snapshot.vet_task_decomposer.review_agent_version,
        ),
        (
            snapshot.standard_consultation.question_collector_agent_id,
            snapshot.standard_consultation.question_collector_agent_version,
        ),
        (
            snapshot.standard_consultation.synthesizer_agent_id,
            snapshot.standard_consultation.synthesizer_agent_version,
        ),
        (
            snapshot.safety_trigger.writer_agent_id,
            snapshot.safety_trigger.writer_agent_version,
        ),
        (
            snapshot.education_agent.writer_agent_id,
            snapshot.education_agent.writer_agent_version,
        ),
        (
            snapshot.nonmedical_pet_care.writer_agent_id,
            snapshot.nonmedical_pet_care.writer_agent_version,
        ),
    }

    assert expected_keys <= keys
    assert len(specs) == len(keys)
    assert specs
    assert all(spec.model_profile == "profile_primary" for spec in specs)
    assert all(spec.response_format is AgentResponseFormat.TEXT for spec in specs)
    assert all(spec.output_schema is not None for spec in specs)
    assert all(spec.tool_policy.allowed_tools == [] for spec in specs)


def test_default_registry_resolves_synthesizer_spec() -> None:
    """验证默认注册表可解析标准问诊草稿合成规格。

    :return: None。
    """

    snapshot = _build_snapshot(llm_gateway_settings=build_test_settings())
    registry = create_default_agent_spec_registry(snapshot)

    spec = registry.resolve_spec(
        agent_id=snapshot.standard_consultation.synthesizer_agent_id,
        agent_version=snapshot.standard_consultation.synthesizer_agent_version,
    )

    assert spec.prompt_template_ref == "inline.standard.synthesizer.v1"
    assert spec.output_schema is not None
    properties = spec.output_schema["properties"]
    assert isinstance(properties, dict)
    assert "draft_response" in properties


def test_default_registry_includes_optional_input_safety_arbitrator() -> None:
    """验证输入安全 LLM 仲裁开启时会注册对应可选规格。

    :return: None。
    """

    safety_settings = VetInputSafetyAssessorSettings(llm_arbitration_enabled=True)
    snapshot = _build_snapshot(
        llm_gateway_settings=build_test_settings(),
        vet_input_safety_assessor_settings=safety_settings,
    )

    registry = create_default_agent_spec_registry(snapshot)
    spec = registry.resolve_spec(
        agent_id=snapshot.vet_input_safety_assessor.arbitration_agent_id,
        agent_version=snapshot.vet_input_safety_assessor.arbitration_agent_version,
    )

    assert spec.agent_type.value == "input_safety"
    assert spec.output_schema_ref == "vet_input_safety.arbitration.v1"


def test_default_registry_rejects_duplicate_specs() -> None:
    """验证默认目录在配置产生重复 Agent ID 与版本时会启动期失败。

    :return: None。
    """

    duplicate_standard_settings = StandardConsultationAgentSettings(
        question_collector_agent_id="standard_duplicate",
        triage_agent_id="standard_duplicate",
    )
    snapshot = _build_snapshot(
        llm_gateway_settings=build_test_settings(),
        standard_consultation_settings=duplicate_standard_settings,
    )

    with pytest.raises(AgentRunnerError) as exc_info:
        create_default_agent_spec_registry(snapshot)

    assert exc_info.value.code is AgentRunnerErrorCode.AGENT_SPEC_VERSION_UNAVAILABLE

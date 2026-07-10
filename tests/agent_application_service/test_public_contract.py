##################################################################################################
# 文件: tests/agent_application_service/test_public_contract.py
# 作用: 验证 AgentApplicationService 组件包和项目根包稳定暴露组件公共契约。
# 边界: 仅检查包级导出，不引用组件内部实现模块、不执行服务编排逻辑。
##################################################################################################

import veterinary_agent
import veterinary_agent.agent_application_service as agent_application_service


def test_agent_application_service_package_exposes_public_contract() -> None:
    """验证 AgentApplicationService 组件包公开导出完整稳定契约。

    :return: None。
    """

    expected_names = (
        "TODO_GRAPH_RUNTIME_ERROR_CODE",
        "TODO_TRACE_STORE_ERROR_CODE",
        "AgentApplicationDto",
        "AgentApplicationErrorCode",
        "AgentApplicationErrorDto",
        "AgentApplicationOperation",
        "AgentApplicationPhase",
        "AgentApplicationService",
        "AgentApplicationServiceError",
        "AgentCancelTurnCommandDto",
        "AgentCancelTurnResultDto",
        "AgentGraphEventDto",
        "AgentGraphRuntime",
        "AgentGraphRuntimeUnavailableError",
        "AgentGraphTurnRequestDto",
        "AgentGraphTurnResultDto",
        "AgentLogicTraceStore",
        "AgentReasoningDisplayDto",
        "AgentReferenceDto",
        "AgentResponseSegmentDto",
        "AgentResumeTurnCommandDto",
        "AgentTraceDeliveryStatus",
        "AgentTraceFinalStatus",
        "AgentTraceFinalizeCommandDto",
        "AgentTraceStartCommandDto",
        "AgentTraceWriteResultDto",
        "AgentTurnAttachmentDto",
        "AgentTurnDiagnosticsDto",
        "AgentTurnEventDto",
        "AgentTurnExecutionContextDto",
        "AgentTurnExecutionOptionsDto",
        "AgentTurnInputAttachmentDto",
        "AgentTurnInputContentDto",
        "AgentTurnInputItemDto",
        "AgentTurnInputTextDto",
        "AgentTurnOptionsDto",
        "AgentTurnPublishCapabilitiesDto",
        "AgentTurnRequestCommandDto",
        "AgentTurnRequestContextDto",
        "AgentTurnResultDto",
        "AgentTurnStatus",
        "AgentTurnTrustedIdentityDto",
        "AgentVetResultDto",
        "DefaultAgentApplicationService",
        "JsonMap",
        "TodoAgentGraphRuntime",
        "TodoAgentLogicTraceStore",
        "build_agent_application_error_dto",
        "is_agent_application_error_retryable_by_default",
    )

    assert tuple(agent_application_service.__all__) == expected_names
    for public_name in expected_names:
        assert hasattr(agent_application_service, public_name)


def test_project_root_reexports_agent_application_service_contract() -> None:
    """验证项目根包重新暴露 AgentApplicationService 公共契约。

    :return: None。
    """

    for public_name in agent_application_service.__all__:
        assert hasattr(veterinary_agent, public_name)

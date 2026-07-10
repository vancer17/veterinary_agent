##################################################################################################
# 文件: tests/pet_session_policy/test_public_contract.py
# 作用: 验证 PetSessionPolicy 组件包初始化文件完整暴露稳定公共契约。
# 边界: 仅检查包级公开符号；不导入内部实现模块、不执行策略或访问任何外部依赖。
##################################################################################################

import veterinary_agent.pet_session_policy as pet_session_policy


def test_pet_session_policy_package_exposes_public_contract() -> None:
    """验证 PetSessionPolicy 公共能力均可从组件包顶层导入。

    :return: None。
    """

    expected_names: tuple[str, ...] = (
        "TODO_TRACE_ERROR_CODE",
        "DefaultPetSessionPolicy",
        "JsonMap",
        "PetSessionContextDto",
        "PetSessionDecision",
        "PetSessionPolicy",
        "PetSessionPolicyAction",
        "PetSessionPolicyDecisionDto",
        "PetSessionPolicyDto",
        "PetSessionPolicyError",
        "PetSessionPolicyErrorCode",
        "PetSessionPolicyErrorDto",
        "PetSessionRequestContextDto",
        "PetSessionTraceRecordDto",
        "PetSessionTraceSink",
        "PetSessionTraceWriteResultDto",
        "PetSessionTraceWriteStatus",
        "TodoPetSessionTraceSink",
        "build_pet_session_policy_error_dto",
        "is_pet_session_policy_error_retryable_by_default",
    )

    assert tuple(pet_session_policy.__all__) == expected_names
    for name in expected_names:
        assert hasattr(pet_session_policy, name)

##################################################################################################
# 文件: tests/vet_input_safety_assessor/test_runtime_config.py
# 作用: 验证 VetInputSafetyAssessor 的 RuntimeConfig 命名空间、点路径读取和 trace-safe 摘要。
# 边界: 只验证配置聚合契约，不创建真实弱依赖、不执行完整业务图。
##################################################################################################

import asyncio

import pytest

from tests.vet_input_safety_assessor.helpers import (
    UnreadyLexicalSignalMatcher,
    build_batch_request,
    build_provider,
)
from veterinary_agent.config import (
    RuntimeConfigNamespace,
    VetInputSafetyAssessorSettings,
)
from veterinary_agent.vet_input_safety_assessor import (
    VetInputSafetyAssessorError,
    VetInputSafetyAssessorErrorCode,
    create_default_vet_input_safety_assessor,
)


def test_runtime_config_namespace_and_value_lookup() -> None:
    """验证 RuntimeConfig 可按命名空间和点路径读取输入安全配置。

    :return: None。
    """

    provider = build_provider()
    namespace = provider.get_namespace(RuntimeConfigNamespace.VET_INPUT_SAFETY_ASSESSOR)

    assert isinstance(namespace, VetInputSafetyAssessorSettings)
    assert provider.get_value(key="vet_input_safety_assessor.enabled") is True
    assert (
        provider.get_value(key="vet_input_safety_assessor.dictionary_version")
        == "vet-input-safety-dictionary.v1"
    )


def test_trace_safe_summary_contains_input_safety_namespace() -> None:
    """验证 trace-safe 摘要包含输入安全配置且不包含用户正文类字段。

    :return: None。
    """

    provider = build_provider()
    summary = provider.trace_safe_summary()
    input_safety_summary = summary["vet_input_safety_assessor"]

    assert isinstance(input_safety_summary, dict)
    assert input_safety_summary["assessor_version"] == "vet-input-safety-assessor.v1"
    rendered = str(input_safety_summary)
    assert "original_user_message" not in rendered
    assert "prompt" not in rendered.lower()


def test_unready_signal_dictionary_blocks_service() -> None:
    """验证 SAF 词库匹配器不可用时服务不可 ready 且调用阻断。

    :return: None。
    """

    provider = build_provider()
    assessor = create_default_vet_input_safety_assessor(
        runtime_config_provider=provider,
        signal_matcher=UnreadyLexicalSignalMatcher(),
    )

    assert assessor.is_ready() is False
    with pytest.raises(VetInputSafetyAssessorError) as exc_info:
        asyncio.run(assessor.batch_assess(build_batch_request(provider)))

    assert (
        exc_info.value.code
        is VetInputSafetyAssessorErrorCode.INPUT_ASSESS_SIGNAL_DICTIONARY_UNAVAILABLE
    )

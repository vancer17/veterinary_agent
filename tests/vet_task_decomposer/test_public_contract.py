##################################################################################################
# 文件: tests/vet_task_decomposer/test_public_contract.py
# 作用: 验证 VetTaskDecomposer 组件公共导出、DTO 严格契约、RuntimeConfig 接入和输入阻断行为。
# 边界: 只通过一级包出口导入生产对象，不接入真实 LLM、本地预训练模型、OCR、RAG 或后续业务图。
##################################################################################################

import asyncio

import pytest
from pydantic import ValidationError

from tests.vet_task_decomposer.helpers import build_provider, build_request
from veterinary_agent.config import VetTaskDecomposerSettings
from veterinary_agent.vet_task_decomposer import (
    AttachmentBindingDto,
    AttachmentRole,
    DecompositionMethod,
    DecompositionStatus,
    DecompositionTraceSummaryDto,
    TextSpanDto,
    VetSubTaskDto,
    VetTaskDecomposeResultDto,
    VetTaskDecomposerError,
    VetTaskDecomposerErrorCode,
    VetTaskTraceWriteStatus,
    VetTaskType,
    build_text_hash,
    create_default_vet_task_decomposer,
)


def test_runtime_config_exposes_vet_task_decomposer_settings() -> None:
    """验证 RuntimeConfig 快照暴露 VetTaskDecomposer 配置命名空间。

    :return: None。
    """

    provider = build_provider()
    snapshot = provider.current_snapshot()

    assert snapshot.vet_task_decomposer.enabled is True
    assert snapshot.vet_task_decomposer.config_version == (
        "vet-task-decomposer-config.v1"
    )
    assert create_default_vet_task_decomposer(
        runtime_config_provider=provider,
    ).is_ready()


def test_disabled_runtime_config_marks_service_not_ready() -> None:
    """验证配置关闭组件时服务 readiness 为 False 且调用被阻断。

    :return: None。
    """

    provider = build_provider(settings=VetTaskDecomposerSettings(enabled=False))
    decomposer = create_default_vet_task_decomposer(
        runtime_config_provider=provider,
    )

    assert decomposer.is_ready() is False
    with pytest.raises(VetTaskDecomposerError) as exc_info:
        asyncio.run(decomposer.decompose(build_request(provider)))

    assert exc_info.value.code is VetTaskDecomposerErrorCode.TASK_DECOMPOSE_NOT_READY


def test_text_span_rejects_invalid_offsets() -> None:
    """验证 TextSpanDto 拒绝不可回指的非法偏移。

    :return: None。
    """

    with pytest.raises(ValidationError):
        TextSpanDto(
            start_offset=5,
            end_offset=5,
            text_hash=build_text_hash(""),
        )


def test_attachment_binding_rejects_none_role() -> None:
    """验证真实附件绑定不能使用 none 角色。

    :return: None。
    """

    with pytest.raises(ValidationError):
        AttachmentBindingDto(
            attachment_id="att_1",
            attachment_role=AttachmentRole.NONE,
        )


def test_result_rejects_mixed_current_pet_ids() -> None:
    """验证拆解结果中不允许混用不同 current_pet_id。

    :return: None。
    """

    provider = build_provider()
    request = build_request(provider)
    span = TextSpanDto(
        start_offset=0,
        end_offset=len(request.user_message),
        text_hash=build_text_hash(request.user_message),
    )

    with pytest.raises(ValidationError):
        VetTaskDecomposeResultDto(
            tasks=[
                VetSubTaskDto(
                    task_id="task_1",
                    task_type=VetTaskType.TRIAGE,
                    current_pet_id="pet_1",
                    source_span=span,
                    normalized_query=request.user_message,
                    confidence=0.8,
                ),
                VetSubTaskDto(
                    task_id="task_2",
                    task_type=VetTaskType.CARE,
                    current_pet_id="pet_2",
                    source_span=span,
                    normalized_query=request.user_message,
                    confidence=0.8,
                ),
            ],
            trace_summary=DecompositionTraceSummaryDto(
                decomposer_version="test",
                method=DecompositionMethod.LLM,
                task_count=2,
                task_types=[VetTaskType.TRIAGE, VetTaskType.CARE],
                llm_unavailable=False,
                fallback_used=False,
                confidence=0.8,
            ),
            status=DecompositionStatus.SUCCEEDED,
            trace_delivery_status=VetTaskTraceWriteStatus.SKIPPED,
        )


def test_empty_user_message_blocks_decomposition() -> None:
    """验证空用户原文按稳定错误码阻断。

    :return: None。
    """

    provider = build_provider()
    decomposer = create_default_vet_task_decomposer(
        runtime_config_provider=provider,
    )

    with pytest.raises(VetTaskDecomposerError) as exc_info:
        asyncio.run(
            decomposer.decompose(
                build_request(provider, user_message="   "),
            )
        )

    assert (
        exc_info.value.code is VetTaskDecomposerErrorCode.TASK_DECOMPOSE_EMPTY_MESSAGE
    )

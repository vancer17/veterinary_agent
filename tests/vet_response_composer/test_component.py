##################################################################################################
# 文件: tests/vet_response_composer/test_component.py
# 作用: 验证 VetResponseComposer 的安全优先发布、发布资格拒绝、trace 写入和图节点适配。
# 边界: 使用内存存储替身覆盖 Composer 自身行为，不连接真实 ConversationStore 或 CheckpointStore。
##################################################################################################

import asyncio

import pytest

from veterinary_agent.graph_runtime import GraphState
from veterinary_agent.vet_response_composer import (
    ComposerBranchType,
    ComposerSegmentType,
    ComposerTraceWriteStatus,
    VetResponseComposerError,
    VetResponseComposerErrorCode,
    VetResponseComposerGraphNode,
    create_default_vet_response_composer,
)

from .helpers import (
    InMemoryComposerCheckpointStore,
    InMemoryComposerConversationStore,
    RecordingComposerTraceSink,
    as_checkpoint_store,
    as_conversation_store,
    build_graph_context,
    build_provider,
    build_request,
    graph_state_for_branches,
    publishable_branch,
    unresolved_branch,
)


def test_composer_orders_safety_before_nonmedical_and_persists_segments() -> None:
    """验证急症 segment 会先于非医疗 segment 发布并写入强依赖。

    :return: None。
    """

    provider = build_provider()
    conversation_store = InMemoryComposerConversationStore()
    checkpoint_store = InMemoryComposerCheckpointStore()
    trace_sink = RecordingComposerTraceSink()
    composer = create_default_vet_response_composer(
        runtime_config_provider=provider,
        conversation_store=as_conversation_store(conversation_store),
        checkpoint_store=as_checkpoint_store(checkpoint_store),
        trace_sink=trace_sink,
    )
    branches = [
        publishable_branch(
            branch_id="nonmedical",
            branch_type=ComposerBranchType.NONMEDICAL_PET_CARE.value,
            segment_id="seg_nonmedical",
            segment_type=ComposerSegmentType.NONMEDICAL.value,
            final_response="可以先少量多次喂水，并观察精神状态。",
            audit_tier="C",
            title="居家护理",
        ),
        publishable_branch(
            branch_id="safety",
            branch_type=ComposerBranchType.SAFETY_TRIGGER.value,
            segment_id="seg_safety",
            segment_type=ComposerSegmentType.SAFETY.value,
            final_response="如果出现持续抽搐、呼吸困难或意识异常，请立刻联系急诊兽医。",
            audit_tier="A",
            title="急症提醒",
            safety_direction_present=True,
        ),
    ]

    result = asyncio.run(
        composer.compose_turn_response(build_request(provider, branches=branches))
    )

    assert [segment.segment_id for segment in result.segments] == [
        "seg_safety",
        "seg_nonmedical",
    ]
    assert [
        call.metadata["composer_segment_id"] for call in conversation_store.append_calls
    ] == [
        "seg_safety",
        "seg_nonmedical",
    ]
    assert [call.segment_order for call in conversation_store.append_calls] == [1, 2]
    assert [call.segment_id for call in checkpoint_store.mark_calls] == [
        "seg_safety",
        "seg_nonmedical",
    ]
    assert result.trace_delivery_status is ComposerTraceWriteStatus.RECORDED
    assert trace_sink.records[0].trace_patch.safety_first_lock_applied is True
    assert trace_sink.records[0].trace_patch.first_segment_type == (
        ComposerSegmentType.SAFETY.value
    )


def test_composer_blocks_non_safety_when_safety_branch_unresolved() -> None:
    """验证急症分支未形成事实时非急症 segment 不得抢先发布。

    :return: None。
    """

    provider = build_provider()
    conversation_store = InMemoryComposerConversationStore()
    checkpoint_store = InMemoryComposerCheckpointStore()
    composer = create_default_vet_response_composer(
        runtime_config_provider=provider,
        conversation_store=as_conversation_store(conversation_store),
        checkpoint_store=as_checkpoint_store(checkpoint_store),
        trace_sink=RecordingComposerTraceSink(),
    )
    branches = [
        unresolved_branch(
            branch_id="safety",
            branch_type=ComposerBranchType.SAFETY_TRIGGER.value,
        ),
        publishable_branch(
            branch_id="nonmedical",
            branch_type=ComposerBranchType.NONMEDICAL_PET_CARE.value,
            segment_id="seg_nonmedical",
            segment_type=ComposerSegmentType.NONMEDICAL.value,
            final_response="今天先减少运动量，继续观察饮水和食欲。",
            audit_tier="C",
        ),
    ]

    with pytest.raises(VetResponseComposerError) as exc_info:
        asyncio.run(
            composer.compose_turn_response(build_request(provider, branches=branches))
        )

    assert exc_info.value.code is (
        VetResponseComposerErrorCode.COMPOSER_SAFETY_FIRST_LOCK_ACTIVE
    )
    assert conversation_store.append_calls == []
    assert checkpoint_store.mark_calls == []


def test_composer_rejects_draft_source_stage() -> None:
    """验证 Composer 拒绝发布草稿或审查中间态文本。

    :return: None。
    """

    provider = build_provider()
    conversation_store = InMemoryComposerConversationStore()
    checkpoint_store = InMemoryComposerCheckpointStore()
    composer = create_default_vet_response_composer(
        runtime_config_provider=provider,
        conversation_store=as_conversation_store(conversation_store),
        checkpoint_store=as_checkpoint_store(checkpoint_store),
        trace_sink=RecordingComposerTraceSink(),
    )
    branches = [
        publishable_branch(
            branch_id="medical",
            branch_type=ComposerBranchType.STANDARD_CONSULTATION.value,
            segment_id="seg_medical",
            segment_type=ComposerSegmentType.MEDICAL.value,
            final_response="需要结合年龄、疫苗史和发作持续时间继续判断。",
            audit_tier="B",
            source_stage="draft_response",
        )
    ]

    with pytest.raises(VetResponseComposerError) as exc_info:
        asyncio.run(
            composer.compose_turn_response(build_request(provider, branches=branches))
        )

    assert exc_info.value.code is (
        VetResponseComposerErrorCode.COMPOSER_UNSAFE_STAGE_PUBLISH_BLOCKED
    )
    assert conversation_store.append_calls == []
    assert checkpoint_store.mark_calls == []


def test_composer_skips_store_writes_for_already_published_segment() -> None:
    """验证 checkpoint 已发布 segment 不会重复写入会话或发布标记。

    :return: None。
    """

    provider = build_provider()
    conversation_store = InMemoryComposerConversationStore()
    checkpoint_store = InMemoryComposerCheckpointStore(
        published_segment_ids=("seg_safety",)
    )
    composer = create_default_vet_response_composer(
        runtime_config_provider=provider,
        conversation_store=as_conversation_store(conversation_store),
        checkpoint_store=as_checkpoint_store(checkpoint_store),
        trace_sink=RecordingComposerTraceSink(),
    )
    branches = [
        publishable_branch(
            branch_id="safety",
            branch_type=ComposerBranchType.SAFETY_TRIGGER.value,
            segment_id="seg_safety",
            segment_type=ComposerSegmentType.SAFETY.value,
            final_response="若症状持续，请联系急诊兽医。",
            audit_tier="A",
            safety_direction_present=True,
        )
    ]

    result = asyncio.run(
        composer.compose_turn_response(build_request(provider, branches=branches))
    )

    assert [segment.segment_id for segment in result.segments] == ["seg_safety"]
    assert conversation_store.append_calls == []
    assert checkpoint_store.mark_calls == []
    assert len(checkpoint_store.load_calls) == 1


def test_composer_marks_trace_degraded_without_blocking_publish() -> None:
    """验证 trace 写入降级不会阻断用户可见 segment 发布。

    :return: None。
    """

    provider = build_provider()
    conversation_store = InMemoryComposerConversationStore()
    checkpoint_store = InMemoryComposerCheckpointStore()
    composer = create_default_vet_response_composer(
        runtime_config_provider=provider,
        conversation_store=as_conversation_store(conversation_store),
        checkpoint_store=as_checkpoint_store(checkpoint_store),
        trace_sink=RecordingComposerTraceSink(status=ComposerTraceWriteStatus.DEGRADED),
    )
    branches = [
        publishable_branch(
            branch_id="medical",
            branch_type=ComposerBranchType.STANDARD_CONSULTATION.value,
            segment_id="seg_medical",
            segment_type=ComposerSegmentType.MEDICAL.value,
            final_response="建议记录发作时长、频率和伴随症状，必要时就诊。",
            audit_tier="B",
        )
    ]

    result = asyncio.run(
        composer.compose_turn_response(build_request(provider, branches=branches))
    )

    assert result.trace_delivery_status is ComposerTraceWriteStatus.DEGRADED
    assert result.trace_patch.trace_degraded is True
    assert [segment.segment_id for segment in result.segments] == ["seg_medical"]
    assert len(conversation_store.append_calls) == 1
    assert len(checkpoint_store.mark_calls) == 1


def test_graph_node_writes_result_patch_and_stream_events() -> None:
    """验证 Composer 图节点写回最终结果并产生段级流式事件。

    :return: None。
    """

    provider = build_provider(stream_delta_chars=6)
    conversation_store = InMemoryComposerConversationStore()
    checkpoint_store = InMemoryComposerCheckpointStore()
    composer = create_default_vet_response_composer(
        runtime_config_provider=provider,
        conversation_store=as_conversation_store(conversation_store),
        checkpoint_store=as_checkpoint_store(checkpoint_store),
        trace_sink=RecordingComposerTraceSink(),
    )
    node = VetResponseComposerGraphNode(composer=composer)
    branches = [
        publishable_branch(
            branch_id="safety",
            branch_type=ComposerBranchType.SAFETY_TRIGGER.value,
            segment_id="seg_safety",
            segment_type=ComposerSegmentType.SAFETY.value,
            final_response="请尽快联系急诊兽医。",
            audit_tier="A",
            title="急症提醒",
            safety_direction_present=True,
        )
    ]

    result = asyncio.run(
        node(
            GraphState(graph_state_for_branches(branches)),
            build_graph_context(provider),
        )
    )

    graph_result = result.state_patch["result"]
    segments = result.state_patch["segments"]
    event_types = [event.event_type for event in result.events]
    assert isinstance(graph_result, dict)
    assert isinstance(segments, list)
    assert graph_result["output_text"] == "请尽快联系急诊兽医。"
    assert segments[0]["segment_id"] == "seg_safety"
    assert result.state_patch["composer_trace_delivery_status"] == (
        ComposerTraceWriteStatus.RECORDED.value
    )
    assert "segment.delta" in event_types
    assert event_types[0] == "turn.started"
    assert event_types[-1] == "turn.completed"

##################################################################################################
# 文件: tests/vet_response_composer/helpers.py
# 作用: 提供 VetResponseComposer 组件测试使用的 RuntimeConfig、graph context、分支状态和存储替身。
# 边界: 只通过生产包一级出口导入公共契约，不连接真实数据库、不实现其他 L2 业务领域。
##################################################################################################

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast

from veterinary_agent.checkpoint_store import (
    CheckpointStore,
    LoadLatestCheckpointQueryDto,
    LoadLatestCheckpointResultDto,
    MarkSegmentPublishedCommandDto,
    MarkSegmentPublishedResultDto,
    SegmentPublishStateDto,
    SegmentPublishStatus,
)
from veterinary_agent.config import (
    RuntimeConfigProvider,
    VetResponseComposerPublishConfig,
    VetResponseComposerSettings,
    create_runtime_config_provider,
)
from veterinary_agent.conversation_store import (
    AppendAssistantSegmentCommandDto,
    AppendAssistantSegmentResultDto,
    ConversationMessageDto,
    ConversationMessageRole,
    ConversationMessageStatus,
    ConversationStore,
    CreateAssistantMessageCommandDto,
    CreateAssistantMessageResultDto,
    FinalizeAssistantMessageCommandDto,
    FinalizeAssistantMessageResultDto,
    MessageSegmentDto,
)
from veterinary_agent.graph_runtime import GraphNodeExecutionContext
from veterinary_agent.vet_response_composer import (
    ComposerTraceRecordDto,
    ComposerTraceWriteResultDto,
    ComposerTraceWriteStatus,
    ComposeTurnRequestDto,
)


class InMemoryComposerConversationStore:
    """Composer 组件测试使用的内存 ConversationStore。"""

    def __init__(self) -> None:
        """初始化内存 ConversationStore。

        :return: None。
        """

        self.create_calls: list[CreateAssistantMessageCommandDto] = []
        self.append_calls: list[AppendAssistantSegmentCommandDto] = []
        self.finalize_calls: list[FinalizeAssistantMessageCommandDto] = []
        self.messages: dict[str, ConversationMessageDto] = {}
        self.segments: list[MessageSegmentDto] = []

    async def create_assistant_message(
        self,
        command: CreateAssistantMessageCommandDto,
    ) -> CreateAssistantMessageResultDto:
        """记录助手消息创建命令并返回内存消息容器。

        :param command: 创建助手消息容器命令。
        :return: 已创建的助手消息容器结果。
        """

        self.create_calls.append(command)
        message_id = f"assistant_message_{len(self.create_calls)}"
        message = self._build_message(
            command=command,
            message_id=message_id,
            content="",
            status=ConversationMessageStatus.STREAMING,
            finalized_at=None,
        )
        self.messages[message_id] = message
        return CreateAssistantMessageResultDto(message=message, idempotent=False)

    async def append_assistant_segment(
        self,
        command: AppendAssistantSegmentCommandDto,
    ) -> AppendAssistantSegmentResultDto:
        """记录助手分段追加命令并返回内存分段事实。

        :param command: 追加助手回复分段命令。
        :return: 已创建的助手分段结果。
        """

        self.append_calls.append(command)
        segment = MessageSegmentDto(
            segment_id=f"conversation_segment_{len(self.append_calls)}",
            message_id=command.message_id,
            session_id=command.session_id,
            pet_id=command.pet_id,
            segment_order=command.segment_order,
            content=command.content,
            idempotency_key=command.idempotency_key,
            metadata=dict(command.metadata),
            published_at=datetime.now(UTC),
        )
        self.segments.append(segment)
        return AppendAssistantSegmentResultDto(segment=segment, idempotent=False)

    async def finalize_assistant_message(
        self,
        command: FinalizeAssistantMessageCommandDto,
    ) -> FinalizeAssistantMessageResultDto:
        """记录助手消息完成命令并返回完成后的内存消息。

        :param command: 完成助手消息命令。
        :return: 已完成的助手消息结果。
        """

        self.finalize_calls.append(command)
        existing = self.messages[command.message_id]
        message = existing.model_copy(
            update={
                "content": command.final_content or existing.content,
                "status": ConversationMessageStatus.FINALIZED,
                "finalized_at": datetime.now(UTC),
                "metadata": {**existing.metadata, **command.metadata_patch},
                "segments": list(self.segments),
            }
        )
        self.messages[command.message_id] = message
        return FinalizeAssistantMessageResultDto(message=message, idempotent=False)

    def _build_message(
        self,
        *,
        command: CreateAssistantMessageCommandDto,
        message_id: str,
        content: str,
        status: ConversationMessageStatus,
        finalized_at: datetime | None,
    ) -> ConversationMessageDto:
        """构建内存助手消息 DTO。

        :param command: 创建助手消息容器命令。
        :param message_id: 待写入的助手消息 ID。
        :param content: 助手消息当前聚合正文。
        :param status: 助手消息状态。
        :param finalized_at: 助手消息完成时间；未完成时为空。
        :return: 可返回给 Composer 的助手消息 DTO。
        """

        return ConversationMessageDto(
            message_id=message_id,
            session_id=command.session_id,
            user_id=command.user_id,
            pet_id=command.pet_id,
            role=ConversationMessageRole.ASSISTANT,
            content_type=command.content_type,
            content=content,
            sequence_no=len(self.messages) + 1,
            status=status,
            reply_to_message_id=command.reply_to_message_id,
            idempotency_key=command.idempotency_key,
            metadata=dict(command.metadata),
            created_at=datetime.now(UTC),
            finalized_at=finalized_at,
        )


class InMemoryComposerCheckpointStore:
    """Composer 组件测试使用的内存 CheckpointStore。"""

    def __init__(
        self,
        *,
        published_segment_ids: Sequence[str] = (),
    ) -> None:
        """初始化内存 CheckpointStore。

        :param published_segment_ids: 初始已发布 segment ID 列表。
        :return: None。
        """

        self.load_calls: list[LoadLatestCheckpointQueryDto] = []
        self.mark_calls: list[MarkSegmentPublishedCommandDto] = []
        self.published_segments: dict[str, SegmentPublishStateDto] = {
            segment_id: SegmentPublishStateDto(
                segment_id=segment_id,
                task_id=None,
                status=SegmentPublishStatus.PUBLISHED,
                published_at=datetime.now(UTC),
            )
            for segment_id in published_segment_ids
        }

    async def load_latest_checkpoint(
        self,
        query: LoadLatestCheckpointQueryDto,
    ) -> LoadLatestCheckpointResultDto:
        """返回当前内存中的已发布 segment 摘要。

        :param query: 读取最新 checkpoint 的查询。
        :return: 包含已发布 segment 摘要的空 checkpoint 结果。
        """

        self.load_calls.append(query)
        return LoadLatestCheckpointResultDto(
            thread_id=query.thread_id,
            latest_version=0,
            checkpoint=None,
            published_segments=list(self.published_segments.values()),
        )

    async def mark_segment_published(
        self,
        command: MarkSegmentPublishedCommandDto,
    ) -> MarkSegmentPublishedResultDto:
        """幂等记录 segment 已发布状态。

        :param command: 标记 segment 已发布命令。
        :return: 已发布 segment 状态结果。
        """

        self.mark_calls.append(command)
        idempotent = command.segment_id in self.published_segments
        segment = self.published_segments.get(command.segment_id)
        if segment is None:
            segment = SegmentPublishStateDto(
                segment_id=command.segment_id,
                task_id=command.task_id,
                status=SegmentPublishStatus.PUBLISHED,
                published_at=command.published_at,
                metadata=dict(command.metadata),
            )
            self.published_segments[command.segment_id] = segment
        return MarkSegmentPublishedResultDto(segment=segment, idempotent=idempotent)


class RecordingComposerTraceSink:
    """记录 Composer trace 摘要的测试 sink。"""

    def __init__(
        self,
        *,
        status: ComposerTraceWriteStatus = ComposerTraceWriteStatus.RECORDED,
    ) -> None:
        """初始化测试 trace sink。

        :param status: 每次写入返回的 trace 状态。
        :return: None。
        """

        self.status = status
        self.records: list[ComposerTraceRecordDto] = []

    async def write_composer_trace(
        self,
        record: ComposerTraceRecordDto,
    ) -> ComposerTraceWriteResultDto:
        """记录 Composer trace 摘要并返回预设状态。

        :param record: 待记录的 Composer trace 摘要。
        :return: 使用预设状态构建的 trace 写入结果。
        """

        self.records.append(record)
        return ComposerTraceWriteResultDto(status=self.status)


def build_provider(
    *,
    stream_delta_chars: int = 8,
) -> RuntimeConfigProvider:
    """构建测试使用的 RuntimeConfig provider。

    :param stream_delta_chars: Composer 图节点流式 delta 切片字符数。
    :return: 已注入 Composer 测试配置的 RuntimeConfig provider。
    """

    return create_runtime_config_provider(
        vet_response_composer_settings=VetResponseComposerSettings(
            publish=VetResponseComposerPublishConfig(
                stream_delta_chars=stream_delta_chars
            )
        )
    )


def as_conversation_store(
    store: InMemoryComposerConversationStore,
) -> ConversationStore:
    """将内存替身声明为 ConversationStore 契约对象。

    :param store: 内存 ConversationStore 替身。
    :return: 可传入 Composer 工厂的 ConversationStore 契约对象。
    """

    return cast(ConversationStore, store)


def as_checkpoint_store(
    store: InMemoryComposerCheckpointStore,
) -> CheckpointStore:
    """将内存替身声明为 CheckpointStore 契约对象。

    :param store: 内存 CheckpointStore 替身。
    :return: 可传入 Composer 工厂的 CheckpointStore 契约对象。
    """

    return cast(CheckpointStore, store)


def build_graph_context(provider: RuntimeConfigProvider) -> GraphNodeExecutionContext:
    """构建测试使用的 GraphNodeExecutionContext。

    :param provider: 已加载测试配置的 RuntimeConfig provider。
    :return: 可传给 Composer 图节点的执行上下文。
    """

    snapshot = provider.current_snapshot()
    return GraphNodeExecutionContext(
        request_id="req_composer_1",
        trace_id="trace_composer_1",
        run_id="run_composer_1",
        graph_id="vet_conversation_graph",
        graph_version="test",
        node_id="vet_response_composer",
        session_id="session_1",
        user_id="user_1",
        current_pet_id="pet_1",
        params_version=snapshot.params_version,
        config_snapshot_id=snapshot.config_snapshot_id,
        thread_id="checkpoint_thread_1",
    )


def build_request(
    provider: RuntimeConfigProvider,
    *,
    branches: Sequence[dict[str, object]],
) -> ComposeTurnRequestDto:
    """构建 Composer 测试请求。

    :param provider: 已加载测试配置的 RuntimeConfig provider。
    :param branches: 待放入 graph state 的业务分支列表。
    :return: 可直接传给 Composer 服务的请求 DTO。
    """

    snapshot = provider.current_snapshot()
    return ComposeTurnRequestDto(
        request_id="req_composer_1",
        trace_id="trace_composer_1",
        run_id="run_composer_1",
        session_id="session_1",
        user_id="user_1",
        current_pet_id="pet_1",
        user_message_id="user_message_1",
        thread_id="checkpoint_thread_1",
        params_version=snapshot.params_version,
        config_snapshot_id=snapshot.config_snapshot_id,
        graph_state=graph_state_for_branches(branches),
    )


def graph_state_for_branches(
    branches: Sequence[dict[str, object]],
) -> dict[str, object]:
    """构建包含 Composer 分支状态的 graph state。

    :param branches: 业务分支状态列表。
    :return: 可传给图节点或 Composer 请求的 graph state。
    """

    return {
        "request": {"user_message_id": "user_message_1"},
        "branch_execution_states": list(branches),
    }


def publishable_branch(
    *,
    branch_id: str,
    branch_type: str,
    segment_id: str,
    segment_type: str,
    final_response: str,
    audit_tier: str,
    title: str | None = None,
    source_stage: str = "final_response",
    safety_direction_present: bool | None = None,
) -> dict[str, object]:
    """构建已产出可发布 segment 的分支状态。

    :param branch_id: 业务分支 ID。
    :param branch_type: 业务分支类型。
    :param segment_id: 可发布 segment ID。
    :param segment_type: 可发布 segment 类型。
    :param final_response: 已通过安全链路的用户可见正文。
    :param audit_tier: segment 审计等级。
    :param title: 可选 segment 标题。
    :param source_stage: 候选文本来源阶段。
    :param safety_direction_present: 急症 segment 是否已包含就医导向。
    :return: 可放入 graph state 的分支状态映射。
    """

    return {
        "branch_id": branch_id,
        "task_id": f"task_{branch_id}",
        "branch_type": branch_type,
        "status": "completed",
        "publishable_segment": {
            "segment_id": segment_id,
            "branch_id": branch_id,
            "task_id": f"task_{branch_id}",
            "segment_type": segment_type,
            "final_response": final_response,
            "title": title,
            "guard_status": "gate_passed",
            "publish_allowed": True,
            "audit_tier": audit_tier,
            "source_stage": source_stage,
            "safety_direction_present": safety_direction_present,
            "reasoning_display": {
                "projection_id": f"reasoning_{segment_id}",
                "segment_id": segment_id,
                "text": "已按安全发布链路确认。",
            },
        },
    }


def unresolved_branch(
    *,
    branch_id: str,
    branch_type: str,
) -> dict[str, object]:
    """构建未产生发布、失败或跳过事实的分支状态。

    :param branch_id: 业务分支 ID。
    :param branch_type: 业务分支类型。
    :return: 可放入 graph state 的未完成分支状态映射。
    """

    return {
        "branch_id": branch_id,
        "task_id": f"task_{branch_id}",
        "branch_type": branch_type,
        "status": "running",
    }

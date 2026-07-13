##################################################################################################
# 文件: src/veterinary_agent/vet_context_builder/ports.py
# 作用: 定义上下文来源端口，并提供 ConversationStore、CheckpointStore 与未实现领域来源适配器。
# 边界: 只将外部公共契约规范化为来源结果，不执行事实优先级合并、slot 计算或 prompt 裁剪。
##################################################################################################

from typing import Literal, Protocol, cast

from veterinary_agent.checkpoint_store import (
    CheckpointStore,
    CheckpointStoreError,
    LoadSessionStateQueryDto,
    build_checkpoint_thread_id,
)
from veterinary_agent.conversation_store import (
    ConversationMessageRole,
    ConversationMessageStatus,
    ConversationStore,
    ConversationStoreError,
    GetRecentMessagesQueryDto,
)
from veterinary_agent.vet_context_builder.dto import (
    ContextMessageDto,
    ContextSourceLoadRequestDto,
    ContextSourceReadResultDto,
    ContextSourceRefDto,
    JsonMap,
    SessionContextStateDto,
)
from veterinary_agent.vet_context_builder.enums import (
    ContextSourceFreshness,
    ContextSourceStatus,
    ContextSourceType,
)

_TODO_SOURCE_ERROR_CODE = "CONTEXT_SOURCE_NOT_IMPLEMENTED"


class ContextSourcePort(Protocol):
    """单类上下文来源读取端口。"""

    @property
    def source_type(self) -> ContextSourceType:
        """读取当前端口负责的来源类型。

        :return: 当前端口唯一负责的上下文来源类型。
        """

        ...

    async def load(
        self,
        request: ContextSourceLoadRequestDto,
    ) -> ContextSourceReadResultDto:
        """读取并规范化单类上下文来源。

        :param request: 统一来源读取请求。
        :return: 不包含未受控原始响应的标准来源结果。
        """

        ...


class TodoContextSourcePort:
    """领域来源尚未接入时使用的显式 TODO 端口。"""

    def __init__(self, *, source_type: ContextSourceType) -> None:
        """初始化指定来源类型的 TODO 端口。

        :param source_type: 当前 TODO 端口代表的来源类型。
        :return: None。
        :raises ValueError: 当尝试为 current_task 创建来源端口时抛出。
        """

        if source_type is ContextSourceType.CURRENT_TASK:
            raise ValueError("current_task 由 Builder 内部构建，不允许注册 TODO 端口")
        self._source_type = source_type

    @property
    def source_type(self) -> ContextSourceType:
        """读取当前 TODO 端口代表的来源类型。

        :return: 当前 TODO 端口的来源类型。
        """

        return self._source_type

    async def load(
        self,
        request: ContextSourceLoadRequestDto,
    ) -> ContextSourceReadResultDto:
        """返回领域来源尚未实现的显式降级结果。

        :param request: 统一来源读取请求。
        :return: 状态为 unavailable 且不包含业务正文的来源结果。
        """

        source_ref = ContextSourceRefDto(
            source_type=self._source_type,
            source_id=f"todo:{self._source_type.value}",
            pet_id=(
                None
                if self._source_type is ContextSourceType.OWNER_PREFERENCE
                else request.current_pet_id
            ),
            freshness=ContextSourceFreshness.UNKNOWN,
            status=ContextSourceStatus.UNAVAILABLE,
        )
        return ContextSourceReadResultDto(
            source_type=self._source_type,
            status=ContextSourceStatus.UNAVAILABLE,
            source_refs=[source_ref],
            error_code=_TODO_SOURCE_ERROR_CODE,
            detail="对应领域来源尚未接入",
        )


class ConversationStoreContextSourcePort:
    """基于 ConversationStore 的近期消息来源适配器。"""

    def __init__(self, *, conversation_store: ConversationStore) -> None:
        """初始化 ConversationStore 来源适配器。

        :param conversation_store: ConversationStore 公共服务契约。
        :return: None。
        """

        self._conversation_store = conversation_store

    @property
    def source_type(self) -> ContextSourceType:
        """读取当前适配器负责的来源类型。

        :return: 固定返回 conversation。
        """

        return ContextSourceType.CONVERSATION

    async def load(
        self,
        request: ContextSourceLoadRequestDto,
    ) -> ContextSourceReadResultDto:
        """读取已完成的用户与助手近期消息。

        :param request: 统一来源读取请求。
        :return: 按消息序号升序排列的标准消息来源结果。
        """

        try:
            result = await self._conversation_store.get_recent_messages(
                GetRecentMessagesQueryDto(
                    request_id=request.request_id,
                    trace_id=request.trace_id,
                    session_id=request.session_id,
                    user_id=request.user_id,
                    pet_id=request.current_pet_id,
                    limit=request.recent_message_limit,
                    include_segments=False,
                    include_attachments=False,
                )
            )
        except ConversationStoreError as exc:
            return ContextSourceReadResultDto(
                source_type=self.source_type,
                status=ContextSourceStatus.UNAVAILABLE,
                error_code=exc.code.value,
                detail="ConversationStore 近期消息读取失败",
            )

        messages: list[ContextMessageDto] = []
        source_refs: list[ContextSourceRefDto] = []
        role_map: dict[
            ConversationMessageRole,
            Literal["user", "assistant", "system"],
        ] = {
            ConversationMessageRole.USER: "user",
            ConversationMessageRole.ASSISTANT: "assistant",
            ConversationMessageRole.SYSTEM: "system",
        }
        for message in result.items:
            if message.status is not ConversationMessageStatus.FINALIZED:
                continue
            role = role_map.get(message.role)
            if role is None:
                continue
            source_ref = ContextSourceRefDto(
                source_type=self.source_type,
                source_id=message.message_id,
                pet_id=message.pet_id,
                version=str(message.sequence_no),
                freshness=ContextSourceFreshness.FRESH,
                status=ContextSourceStatus.AVAILABLE,
            )
            source_refs.append(source_ref)
            messages.append(
                ContextMessageDto(
                    message_id=message.message_id,
                    pet_id=message.pet_id,
                    role=role,
                    content=message.content,
                    sequence_no=message.sequence_no,
                    source_ref=source_ref,
                )
            )
        status = (
            ContextSourceStatus.AVAILABLE if messages else ContextSourceStatus.EMPTY
        )
        return ContextSourceReadResultDto(
            source_type=self.source_type,
            status=status,
            source_refs=source_refs,
            messages=messages,
        )


class CheckpointStoreContextSourcePort:
    """基于 CheckpointStore 的 session 短期状态来源适配器。"""

    def __init__(self, *, checkpoint_store: CheckpointStore) -> None:
        """初始化 CheckpointStore 来源适配器。

        :param checkpoint_store: CheckpointStore 公共服务契约。
        :return: None。
        """

        self._checkpoint_store = checkpoint_store

    @property
    def source_type(self) -> ContextSourceType:
        """读取当前适配器负责的来源类型。

        :return: 固定返回 checkpoint。
        """

        return ContextSourceType.CHECKPOINT

    async def load(
        self,
        request: ContextSourceLoadRequestDto,
    ) -> ContextSourceReadResultDto:
        """读取 session 短期业务状态摘要。

        :param request: 统一来源读取请求。
        :return: 规范化 checkpoint 状态来源结果。
        """

        thread_id = build_checkpoint_thread_id(session_id=request.session_id)
        try:
            result = await self._checkpoint_store.load_session_state(
                LoadSessionStateQueryDto(
                    request_id=request.request_id,
                    trace_id=request.trace_id,
                    thread_id=thread_id,
                    session_id=request.session_id,
                )
            )
        except CheckpointStoreError as exc:
            return ContextSourceReadResultDto(
                source_type=self.source_type,
                status=ContextSourceStatus.UNAVAILABLE,
                error_code=exc.code.value,
                detail="CheckpointStore session 状态读取失败",
            )

        state = result.state
        resolved_pet_id = state.pet_id or request.current_pet_id
        source_ref = ContextSourceRefDto(
            source_type=self.source_type,
            source_id=result.latest_checkpoint_id or thread_id,
            pet_id=resolved_pet_id,
            version=str(result.latest_version),
            freshness=ContextSourceFreshness.FRESH,
            status=ContextSourceStatus.AVAILABLE,
        )
        session_state = SessionContextStateDto(
            pet_id=resolved_pet_id,
            current_complaint_type=state.current_complaint_type,
            slot_progress=cast(JsonMap, state.slot_progress),
            rolling_summary_ref=state.rolling_summary_ref,
            checkpoint_id=result.latest_checkpoint_id,
            checkpoint_version=result.latest_version,
            source_ref=source_ref,
        )
        return ContextSourceReadResultDto(
            source_type=self.source_type,
            status=ContextSourceStatus.AVAILABLE,
            source_refs=[source_ref],
            session_state=session_state,
        )


def build_todo_context_source_ports() -> tuple[ContextSourcePort, ...]:
    """构建当前尚无领域实现的默认 TODO 来源端口集合。

    :return: 核心事实、宠物画像、确认化验和主人偏好的 TODO 端口元组。
    """

    source_types = (
        ContextSourceType.CORE_FACT_SNAPSHOT,
        ContextSourceType.PET_PROFILE,
        ContextSourceType.CONFIRMED_LAB,
        ContextSourceType.OWNER_PREFERENCE,
    )
    return tuple(TodoContextSourcePort(source_type=value) for value in source_types)


def build_default_context_source_ports(
    *,
    conversation_store: ConversationStore | None = None,
    checkpoint_store: CheckpointStore | None = None,
) -> tuple[ContextSourcePort, ...]:
    """构建包含真实存储适配器和 TODO 领域端口的默认来源集合。

    :param conversation_store: 可选 ConversationStore；为空时使用 TODO 端口。
    :param checkpoint_store: 可选 CheckpointStore；为空时使用 TODO 端口。
    :return: 覆盖 Builder 读取计划全部来源类型的来源端口元组。
    """

    ports: list[ContextSourcePort] = [*build_todo_context_source_ports()]
    if conversation_store is None:
        ports.append(TodoContextSourcePort(source_type=ContextSourceType.CONVERSATION))
    else:
        ports.append(
            ConversationStoreContextSourcePort(
                conversation_store=conversation_store,
            )
        )
    if checkpoint_store is None:
        ports.append(TodoContextSourcePort(source_type=ContextSourceType.CHECKPOINT))
    else:
        ports.append(
            CheckpointStoreContextSourcePort(checkpoint_store=checkpoint_store)
        )
    return tuple(ports)


__all__: tuple[str, ...] = (
    "CheckpointStoreContextSourcePort",
    "ContextSourcePort",
    "ConversationStoreContextSourcePort",
    "TodoContextSourcePort",
    "build_default_context_source_ports",
    "build_todo_context_source_ports",
)

##################################################################################################
# 文件: src/veterinary_agent/conversation_store/dto.py
# 作用: 定义 ConversationStore 接口契约使用的 DTO，覆盖 session、message、segment、附件引用与分页读取。
# 边界: 仅描述对话事实存储契约的数据承载结构，不包含数据库、RuntimeConfig 或业务组件实现逻辑。
##################################################################################################

from datetime import datetime
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from veterinary_agent.conversation_store.enums import (
    ConversationMessageRole,
    ConversationMessageStatus,
    ConversationSessionStatus,
)

JsonMap: TypeAlias = dict[str, object]


class ConversationStoreDto(BaseModel):
    """ConversationStore DTO 基类。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=False,
        validate_assignment=True,
    )


class ConversationRequestContextDto(ConversationStoreDto):
    """ConversationStore 通用请求上下文 DTO。"""

    request_id: str = Field(
        min_length=1,
        description="本次请求 ID，用于日志、指标和错误排障关联。",
    )
    trace_id: str = Field(
        min_length=1,
        description="本次全链路追踪 ID，用于跨组件串联调用链。",
    )


class ConversationSessionDto(ConversationStoreDto):
    """conversation session 摘要 DTO。"""

    session_id: str = Field(
        min_length=1,
        description="上游可信传入的会话 ID。",
    )
    user_id: str = Field(
        min_length=1,
        description="上游可信传入的用户 ID；ConversationStore 不执行用户鉴权。",
    )
    pet_id: str = Field(
        min_length=1,
        description="session 锚定的宠物 ID；创建后普通写路径不得改写。",
    )
    status: ConversationSessionStatus = Field(
        description="conversation session 生命周期状态。",
    )
    metadata: JsonMap = Field(
        default_factory=dict,
        description="session 轻量元信息；不得承载完整业务逻辑链或长期记忆。",
    )
    created_at: datetime = Field(
        description="session 创建时间。",
    )
    updated_at: datetime = Field(
        description="session 最近更新时间。",
    )
    last_message_at: datetime | None = Field(
        default=None,
        description="session 最近一条消息写入时间；尚无消息时为空。",
    )
    next_sequence_no: int = Field(
        ge=1,
        description="下一条 message 将分配的 session 内序号。",
    )


class MessageAttachmentRefDto(ConversationStoreDto):
    """消息附件引用 DTO。"""

    attachment_ref_id: str = Field(
        min_length=1,
        description="ConversationStore 内部附件引用记录 ID。",
    )
    attachment_id: str = Field(
        min_length=1,
        description="附件服务或对象存储中的附件 ID。",
    )
    message_id: str = Field(
        min_length=1,
        description="附件引用所属消息 ID。",
    )
    session_id: str = Field(
        min_length=1,
        description="附件引用所属 session ID。",
    )
    pet_id: str = Field(
        min_length=1,
        description="附件引用所属宠物 ID，必须与 message 和 session 一致。",
    )
    attachment_type: str = Field(
        min_length=1,
        description="附件类型，例如 image、lab_report 或 document。",
    )
    metadata: JsonMap = Field(
        default_factory=dict,
        description="附件轻量元信息；附件文件本体、OCR 原文和化验结构化结果不存于本组件。",
    )
    created_at: datetime = Field(
        description="附件引用创建时间。",
    )


class MessageAttachmentRefInputDto(ConversationStoreDto):
    """消息写入命令中的附件引用输入 DTO。"""

    attachment_id: str = Field(
        min_length=1,
        description="附件服务或对象存储中的附件 ID。",
    )
    attachment_type: str = Field(
        min_length=1,
        description="附件类型，例如 image、lab_report 或 document。",
    )
    metadata: JsonMap = Field(
        default_factory=dict,
        description="附件轻量元信息；不得承载附件本体、OCR 原文或化验结构化结果。",
    )


class MessageSegmentDto(ConversationStoreDto):
    """助手消息分段发布事实 DTO。"""

    segment_id: str = Field(
        min_length=1,
        description="助手消息分段 ID。",
    )
    message_id: str = Field(
        min_length=1,
        description="分段所属助手消息 ID。",
    )
    session_id: str = Field(
        min_length=1,
        description="分段所属 session ID。",
    )
    pet_id: str = Field(
        min_length=1,
        description="分段所属宠物 ID，必须与 message 和 session 一致。",
    )
    segment_order: int = Field(
        ge=1,
        description="分段在所属助手消息内的业务排序。",
    )
    content: str = Field(
        description="已发布给用户的助手回复分段正文。",
    )
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        description="分段写入幂等键；重复写入返回既有分段。",
    )
    metadata: JsonMap = Field(
        default_factory=dict,
        description="分段轻量元信息；不得承载完整逻辑链、RAG 片段或审查稿。",
    )
    published_at: datetime = Field(
        description="分段持久化发布时间。",
    )


class ConversationMessageDto(ConversationStoreDto):
    """conversation message 事实 DTO。"""

    message_id: str = Field(
        min_length=1,
        description="消息 ID。",
    )
    session_id: str = Field(
        min_length=1,
        description="消息所属 session ID。",
    )
    user_id: str = Field(
        min_length=1,
        description="消息所属用户 ID，必须与 session 一致。",
    )
    pet_id: str = Field(
        min_length=1,
        description="消息所属宠物 ID，必须与 session 一致。",
    )
    role: ConversationMessageRole = Field(
        description="消息角色。",
    )
    content_type: str = Field(
        min_length=1,
        description="消息正文类型，例如 text/plain 或 application/json。",
    )
    content: str = Field(
        description="可回放的用户可见或系统必要消息正文。",
    )
    sequence_no: int = Field(
        ge=1,
        description="session 内单调递增消息序号。",
    )
    status: ConversationMessageStatus = Field(
        description="消息生命周期状态。",
    )
    reply_to_message_id: str | None = Field(
        default=None,
        min_length=1,
        description="助手消息回复的用户消息 ID；无直接回复关系时为空。",
    )
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        description="消息写入幂等键；重复写入返回既有消息。",
    )
    metadata: JsonMap = Field(
        default_factory=dict,
        description="消息轻量元信息；不得承载完整逻辑链、RAG 片段或长期记忆。",
    )
    created_at: datetime = Field(
        description="消息创建时间。",
    )
    finalized_at: datetime | None = Field(
        default=None,
        description="消息完成时间；流式助手消息未完成时为空。",
    )
    segments: list[MessageSegmentDto] = Field(
        default_factory=list,
        description="助手消息分段列表；普通消息默认为空。",
    )
    attachments: list[MessageAttachmentRefDto] = Field(
        default_factory=list,
        description="消息附件引用列表。",
    )


class EnsureSessionCommandDto(ConversationRequestContextDto):
    """创建或确认 conversation session 的命令 DTO。"""

    session_id: str = Field(
        min_length=1,
        description="上游可信传入的会话 ID。",
    )
    user_id: str = Field(
        min_length=1,
        description="上游可信传入的用户 ID。",
    )
    pet_id: str = Field(
        min_length=1,
        description="上游可信传入的宠物 ID。",
    )
    metadata: JsonMap = Field(
        default_factory=dict,
        description="创建 session 时保存的轻量元信息；命中既有 session 时不覆盖。",
    )


class EnsureSessionResultDto(ConversationStoreDto):
    """创建或确认 conversation session 的结果 DTO。"""

    session: ConversationSessionDto = Field(
        description="当前请求命中的 conversation session。",
    )
    created_new: bool = Field(
        description="本次调用是否新建了 conversation session。",
    )


class GetSessionQueryDto(ConversationRequestContextDto):
    """读取 conversation session 的查询 DTO。"""

    session_id: str = Field(
        min_length=1,
        description="需要读取的 session ID。",
    )
    user_id: str | None = Field(
        default=None,
        min_length=1,
        description="可选用户 ID；传入时用于一致性校验。",
    )
    pet_id: str | None = Field(
        default=None,
        min_length=1,
        description="可选宠物 ID；传入时用于一致性校验。",
    )


class AppendMessageCommandDto(ConversationRequestContextDto):
    """追加用户、系统或工具消息的命令 DTO。"""

    session_id: str = Field(
        min_length=1,
        description="消息所属 session ID。",
    )
    user_id: str = Field(
        min_length=1,
        description="消息所属用户 ID。",
    )
    pet_id: str = Field(
        min_length=1,
        description="消息所属宠物 ID。",
    )
    role: ConversationMessageRole = Field(
        description="消息角色；普通追加接口不接受 assistant 角色。",
    )
    content_type: str = Field(
        default="text/plain",
        min_length=1,
        description="消息正文类型。",
    )
    content: str = Field(
        description="消息正文。",
    )
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        description="消息写入幂等键。",
    )
    metadata: JsonMap = Field(
        default_factory=dict,
        description="消息轻量元信息。",
    )
    attachments: list[MessageAttachmentRefInputDto] = Field(
        default_factory=list,
        description="本消息携带的附件引用列表。",
    )


class AppendMessageResultDto(ConversationStoreDto):
    """追加消息的结果 DTO。"""

    message: ConversationMessageDto = Field(
        description="已写入或幂等命中的消息。",
    )
    idempotent: bool = Field(
        description="本次调用是否命中既有消息。",
    )


class CreateAssistantMessageCommandDto(ConversationRequestContextDto):
    """创建助手消息容器的命令 DTO。"""

    session_id: str = Field(
        min_length=1,
        description="助手消息所属 session ID。",
    )
    user_id: str = Field(
        min_length=1,
        description="助手消息所属用户 ID。",
    )
    pet_id: str = Field(
        min_length=1,
        description="助手消息所属宠物 ID。",
    )
    reply_to_message_id: str | None = Field(
        default=None,
        min_length=1,
        description="助手消息回复的用户消息 ID；无直接回复关系时为空。",
    )
    content_type: str = Field(
        default="text/plain",
        min_length=1,
        description="助手最终正文类型。",
    )
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        description="助手消息容器写入幂等键。",
    )
    metadata: JsonMap = Field(
        default_factory=dict,
        description="助手消息轻量元信息。",
    )


class CreateAssistantMessageResultDto(ConversationStoreDto):
    """创建助手消息容器的结果 DTO。"""

    message: ConversationMessageDto = Field(
        description="已创建或幂等命中的助手消息容器。",
    )
    idempotent: bool = Field(
        description="本次调用是否命中既有助手消息容器。",
    )


class AppendAssistantSegmentCommandDto(ConversationRequestContextDto):
    """追加助手回复分段的命令 DTO。"""

    session_id: str = Field(
        min_length=1,
        description="分段所属 session ID。",
    )
    user_id: str = Field(
        min_length=1,
        description="分段所属用户 ID。",
    )
    pet_id: str = Field(
        min_length=1,
        description="分段所属宠物 ID。",
    )
    message_id: str = Field(
        min_length=1,
        description="分段所属助手消息 ID。",
    )
    segment_order: int = Field(
        ge=1,
        description="分段在助手消息内的业务排序。",
    )
    content: str = Field(
        description="助手回复分段正文。",
    )
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        description="分段写入幂等键。",
    )
    metadata: JsonMap = Field(
        default_factory=dict,
        description="分段轻量元信息。",
    )


class AppendAssistantSegmentResultDto(ConversationStoreDto):
    """追加助手回复分段的结果 DTO。"""

    segment: MessageSegmentDto = Field(
        description="已写入或幂等命中的助手回复分段。",
    )
    idempotent: bool = Field(
        description="本次调用是否命中既有分段。",
    )


class FinalizeAssistantMessageCommandDto(ConversationRequestContextDto):
    """完成助手消息的命令 DTO。"""

    session_id: str = Field(
        min_length=1,
        description="助手消息所属 session ID。",
    )
    user_id: str = Field(
        min_length=1,
        description="助手消息所属用户 ID。",
    )
    pet_id: str = Field(
        min_length=1,
        description="助手消息所属宠物 ID。",
    )
    message_id: str = Field(
        min_length=1,
        description="需要完成的助手消息 ID。",
    )
    final_content: str | None = Field(
        default=None,
        description="可选最终聚合正文；未传入时由已保存 segments 按顺序拼接。",
    )
    metadata_patch: JsonMap = Field(
        default_factory=dict,
        description="完成时追加或覆盖的轻量元信息。",
    )


class FinalizeAssistantMessageResultDto(ConversationStoreDto):
    """完成助手消息的结果 DTO。"""

    message: ConversationMessageDto = Field(
        description="完成后的助手消息。",
    )
    idempotent: bool = Field(
        description="本次调用是否命中已完成消息。",
    )


class ListMessagesBySessionQueryDto(ConversationRequestContextDto):
    """按 session 分页查询消息的查询 DTO。"""

    session_id: str = Field(
        min_length=1,
        description="需要查询消息历史的 session ID。",
    )
    user_id: str = Field(
        min_length=1,
        description="请求方用户 ID，用于一致性校验。",
    )
    pet_id: str = Field(
        min_length=1,
        description="请求方宠物 ID，用于一致性校验。",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="本次查询最多返回的消息数量。",
    )
    cursor: str | None = Field(
        default=None,
        min_length=1,
        description="分页游标；当前实现使用上一页最后一条消息的 sequence_no 字符串。",
    )
    include_segments: bool = Field(
        default=True,
        description="是否随消息返回助手 segments。",
    )
    include_attachments: bool = Field(
        default=True,
        description="是否随消息返回附件引用。",
    )


class ListMessagesBySessionResultDto(ConversationStoreDto):
    """按 session 分页查询消息的结果 DTO。"""

    session_id: str = Field(
        min_length=1,
        description="已查询的 session ID。",
    )
    items: list[ConversationMessageDto] = Field(
        default_factory=list,
        description="按 sequence_no 升序返回的消息列表。",
    )
    next_cursor: str | None = Field(
        default=None,
        min_length=1,
        description="下一页分页游标；无更多消息时为空。",
    )


class GetRecentMessagesQueryDto(ConversationRequestContextDto):
    """读取最近消息的查询 DTO。"""

    session_id: str = Field(
        min_length=1,
        description="需要读取最近消息的 session ID。",
    )
    user_id: str = Field(
        min_length=1,
        description="请求方用户 ID，用于一致性校验。",
    )
    pet_id: str = Field(
        min_length=1,
        description="请求方宠物 ID，用于一致性校验。",
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="最近消息最大返回数量。",
    )
    include_segments: bool = Field(
        default=True,
        description="是否随消息返回助手 segments。",
    )
    include_attachments: bool = Field(
        default=True,
        description="是否随消息返回附件引用。",
    )


class GetRecentMessagesResultDto(ConversationStoreDto):
    """读取最近消息的结果 DTO。"""

    session_id: str = Field(
        min_length=1,
        description="已查询的 session ID。",
    )
    items: list[ConversationMessageDto] = Field(
        default_factory=list,
        description="按 sequence_no 升序返回的最近消息列表。",
    )


class CloseSessionCommandDto(ConversationRequestContextDto):
    """关闭 conversation session 的命令 DTO。"""

    session_id: str = Field(
        min_length=1,
        description="需要关闭的 session ID。",
    )
    user_id: str = Field(
        min_length=1,
        description="请求方用户 ID，用于一致性校验。",
    )
    pet_id: str = Field(
        min_length=1,
        description="请求方宠物 ID，用于一致性校验。",
    )
    metadata_patch: JsonMap = Field(
        default_factory=dict,
        description="关闭时追加或覆盖的 session 轻量元信息。",
    )


class ArchiveSessionCommandDto(ConversationRequestContextDto):
    """归档 conversation session 的命令 DTO。"""

    session_id: str = Field(
        min_length=1,
        description="需要归档的 session ID。",
    )
    user_id: str = Field(
        min_length=1,
        description="请求方用户 ID，用于一致性校验。",
    )
    pet_id: str = Field(
        min_length=1,
        description="请求方宠物 ID，用于一致性校验。",
    )
    metadata_patch: JsonMap = Field(
        default_factory=dict,
        description="归档时追加或覆盖的 session 轻量元信息。",
    )


class UpdateSessionStatusResultDto(ConversationStoreDto):
    """更新 conversation session 状态的结果 DTO。"""

    session: ConversationSessionDto = Field(
        description="更新后的 conversation session。",
    )
    idempotent: bool = Field(
        description="本次调用是否命中既有目标状态。",
    )


__all__: tuple[str, ...] = (
    "AppendAssistantSegmentCommandDto",
    "AppendAssistantSegmentResultDto",
    "AppendMessageCommandDto",
    "AppendMessageResultDto",
    "ArchiveSessionCommandDto",
    "CloseSessionCommandDto",
    "ConversationMessageDto",
    "ConversationRequestContextDto",
    "ConversationSessionDto",
    "ConversationStoreDto",
    "CreateAssistantMessageCommandDto",
    "CreateAssistantMessageResultDto",
    "EnsureSessionCommandDto",
    "EnsureSessionResultDto",
    "FinalizeAssistantMessageCommandDto",
    "FinalizeAssistantMessageResultDto",
    "GetRecentMessagesQueryDto",
    "GetRecentMessagesResultDto",
    "GetSessionQueryDto",
    "JsonMap",
    "ListMessagesBySessionQueryDto",
    "ListMessagesBySessionResultDto",
    "MessageAttachmentRefDto",
    "MessageAttachmentRefInputDto",
    "MessageSegmentDto",
    "UpdateSessionStatusResultDto",
)

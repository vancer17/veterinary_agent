##################################################################################################
# 文件: src/veterinary_agent/checkpoint_store/dto.py
# 作用: 定义 CheckpointStore 接口契约使用的 DTO，覆盖 thread、锁、checkpoint、session 状态和 segment 发布幂等。
# 边界: 仅描述状态持久化契约的数据承载结构，不包含数据库、LangGraph、RuntimeConfig 或 Observability 的实现逻辑。
##################################################################################################

from datetime import datetime
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from veterinary_agent.checkpoint_store.enums import (
    CheckpointRecordStatus,
    CheckpointThreadStatus,
    SegmentPublishStatus,
)

JsonMap: TypeAlias = dict[str, object]


class CheckpointStoreDto(BaseModel):
    """CheckpointStore DTO 基类。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=False,
        validate_assignment=True,
    )


class CheckpointRequestContextDto(CheckpointStoreDto):
    """CheckpointStore 通用请求上下文 DTO。"""

    request_id: str = Field(
        min_length=1,
        description="本次请求 ID，用于日志、指标和错误排障关联。",
    )
    trace_id: str = Field(
        min_length=1,
        description="本次全链路追踪 ID，用于跨组件串联调用链。",
    )


class CheckpointThreadDto(CheckpointStoreDto):
    """checkpoint thread 摘要 DTO。"""

    thread_id: str = Field(
        min_length=1,
        description="可恢复执行线程 ID；兽医 Agent 默认由 session_id 派生。",
    )
    session_id: str = Field(
        min_length=1,
        description="上游可信传入的会话 ID。",
    )
    user_id: str = Field(
        min_length=1,
        description="上游可信传入的用户 ID；CheckpointStore 不执行用户鉴权。",
    )
    pet_id: str | None = Field(
        default=None,
        min_length=1,
        description="线程锚定的宠物 ID；为空表示线程尚未锚定宠物。",
    )
    status: CheckpointThreadStatus = Field(
        description="checkpoint thread 生命周期状态。",
    )
    latest_version: int = Field(
        ge=0,
        description="当前 thread 最新 checkpoint 版本号。",
    )
    latest_checkpoint_id: str | None = Field(
        default=None,
        min_length=1,
        description="当前 thread 最新 checkpoint ID；尚未保存 checkpoint 时为空。",
    )
    created_at: datetime = Field(
        description="thread 创建时间。",
    )
    updated_at: datetime = Field(
        description="thread 最近更新时间。",
    )


class EnsureThreadCommandDto(CheckpointRequestContextDto):
    """获取或创建 checkpoint thread 的命令 DTO。"""

    session_id: str = Field(
        min_length=1,
        description="上游可信传入的会话 ID。",
    )
    user_id: str = Field(
        min_length=1,
        description="上游可信传入的用户 ID。",
    )
    pet_id: str | None = Field(
        default=None,
        min_length=1,
        description="本轮请求关联的宠物 ID；可为空但不能与既有 thread 锚点冲突。",
    )


class EnsureThreadResultDto(CheckpointStoreDto):
    """获取或创建 checkpoint thread 的结果 DTO。"""

    thread: CheckpointThreadDto = Field(
        description="当前请求命中的 checkpoint thread。",
    )
    created_new: bool = Field(
        description="本次调用是否新建了 checkpoint thread。",
    )


class AcquireRunLockCommandDto(CheckpointRequestContextDto):
    """获取运行锁的命令 DTO。"""

    thread_id: str = Field(
        min_length=1,
        description="需要获取运行锁的 checkpoint thread ID。",
    )
    run_id: str = Field(
        min_length=1,
        description="当前图执行轮次 ID。",
    )
    lock_ttl_seconds: float = Field(
        gt=0,
        description="运行锁 TTL，单位为秒。",
    )


class AcquireRunLockResultDto(CheckpointStoreDto):
    """获取运行锁的结果 DTO。"""

    thread_id: str = Field(
        min_length=1,
        description="已获取运行锁的 checkpoint thread ID。",
    )
    run_id: str = Field(
        min_length=1,
        description="当前持锁的图执行轮次 ID。",
    )
    lock_acquired: bool = Field(
        description="本次调用是否已获得运行锁。",
    )
    expires_at: datetime = Field(
        description="运行锁过期时间。",
    )
    idempotent: bool = Field(
        description="本次调用是否命中了同一 run_id 的幂等获取。",
    )
    stale_lock_replaced: bool = Field(
        description="本次调用是否替换了已过期运行锁。",
    )


class ReleaseRunLockCommandDto(CheckpointRequestContextDto):
    """释放运行锁的命令 DTO。"""

    thread_id: str = Field(
        min_length=1,
        description="需要释放运行锁的 checkpoint thread ID。",
    )
    run_id: str = Field(
        min_length=1,
        description="请求释放锁的图执行轮次 ID。",
    )


class ReleaseRunLockResultDto(CheckpointStoreDto):
    """释放运行锁的结果 DTO。"""

    thread_id: str = Field(
        min_length=1,
        description="已处理释放请求的 checkpoint thread ID。",
    )
    run_id: str = Field(
        min_length=1,
        description="请求释放锁的图执行轮次 ID。",
    )
    released: bool = Field(
        description="本次调用是否实际释放了运行锁。",
    )
    idempotent: bool = Field(
        description="本次调用是否命中了锁已不存在的幂等释放。",
    )


class GraphExecutionStateDto(CheckpointStoreDto):
    """图编排恢复状态 DTO。"""

    current_node: str | None = Field(
        default=None,
        min_length=1,
        description="当前或最近保存的图节点名称。",
    )
    completed_nodes: list[str] = Field(
        default_factory=list,
        description="已完成节点名称列表。",
    )
    pending_nodes: list[str] = Field(
        default_factory=list,
        description="待执行节点名称列表。",
    )
    node_outputs: JsonMap = Field(
        default_factory=dict,
        description="节点输出摘要、引用或哈希；不得承载完整医疗正文、prompt 或审查稿。",
    )
    retry_state: JsonMap = Field(
        default_factory=dict,
        description="节点重试状态摘要。",
    )
    recoverable_from: str | None = Field(
        default=None,
        min_length=1,
        description="最近可恢复节点锚点。",
    )


class TaskExecutionStateDto(CheckpointStoreDto):
    """业务子任务执行状态 DTO。"""

    task_id: str = Field(
        min_length=1,
        description="业务子任务 ID。",
    )
    task_type: str = Field(
        min_length=1,
        description="业务子任务类型；CheckpointStore 不解释其兽医语义。",
    )
    generation_profile: str | None = Field(
        default=None,
        min_length=1,
        description="生成剖面摘要；具体判定由业务组件负责。",
    )
    status: str = Field(
        min_length=1,
        description="业务子任务状态；CheckpointStore 仅存储不解释。",
    )


class SegmentPublishStateDto(CheckpointStoreDto):
    """segment 发布状态 DTO。"""

    segment_id: str = Field(
        min_length=1,
        description="业务分段 ID；恢复时用于防止重复发布。",
    )
    task_id: str | None = Field(
        default=None,
        min_length=1,
        description="segment 关联的业务子任务 ID。",
    )
    status: SegmentPublishStatus = Field(
        description="segment 发布幂等状态。",
    )
    published_at: datetime | None = Field(
        default=None,
        description="segment 成功发布的时间；未发布时为空。",
    )
    metadata: JsonMap = Field(
        default_factory=dict,
        description="segment 发布普通元信息；不得承载完整响应正文。",
    )


class SessionBusinessStateDto(CheckpointStoreDto):
    """session 短期业务状态摘要 DTO。"""

    params_version: str | None = Field(
        default=None,
        min_length=1,
        description="本轮或最近一次执行使用的运行参数版本。",
    )
    pet_id: str | None = Field(
        default=None,
        min_length=1,
        description="session 状态锚定的宠物 ID。",
    )
    current_complaint_type: str | None = Field(
        default=None,
        min_length=1,
        description="当前主诉类型摘要；CheckpointStore 不解释其业务语义。",
    )
    slot_progress: JsonMap = Field(
        default_factory=dict,
        description="问诊槽位进度摘要。",
    )
    tasks: list[TaskExecutionStateDto] = Field(
        default_factory=list,
        description="业务子任务执行状态列表。",
    )
    segments: list[SegmentPublishStateDto] = Field(
        default_factory=list,
        description="session 内 segment 发布状态摘要列表。",
    )
    rolling_summary_ref: str | None = Field(
        default=None,
        min_length=1,
        description="滚动摘要引用 ID；正文由其他存储组件负责。",
    )


class CheckpointSnapshotDto(CheckpointStoreDto):
    """checkpoint 快照 DTO。"""

    checkpoint_id: str = Field(
        min_length=1,
        description="checkpoint 快照 ID。",
    )
    thread_id: str = Field(
        min_length=1,
        description="checkpoint 所属 thread ID。",
    )
    run_id: str = Field(
        min_length=1,
        description="生成该 checkpoint 的图执行轮次 ID。",
    )
    version: int = Field(
        ge=1,
        description="checkpoint 在当前 thread 内的递增版本号。",
    )
    graph_name: str = Field(
        min_length=1,
        description="图编排名称。",
    )
    graph_version: str = Field(
        min_length=1,
        description="图编排版本。",
    )
    state_schema_version: str = Field(
        min_length=1,
        description="checkpoint 状态 schema 版本。",
    )
    status: CheckpointRecordStatus = Field(
        description="checkpoint 快照状态。",
    )
    current_node: str | None = Field(
        default=None,
        min_length=1,
        description="保存该 checkpoint 时的当前节点。",
    )
    graph_state: GraphExecutionStateDto = Field(
        description="图编排恢复状态。",
    )
    business_state: SessionBusinessStateDto = Field(
        description="session 短期业务状态摘要。",
    )
    metadata: JsonMap = Field(
        default_factory=dict,
        description="checkpoint 普通元信息；仅允许摘要、引用或哈希。",
    )
    published_segments: list[SegmentPublishStateDto] = Field(
        default_factory=list,
        description="保存快照时已知的已发布 segment 摘要列表。",
    )
    state_size_bytes: int = Field(
        ge=0,
        description="checkpoint 状态体序列化后的字节数。",
    )
    created_at: datetime = Field(
        description="checkpoint 创建时间。",
    )


class LoadLatestCheckpointQueryDto(CheckpointRequestContextDto):
    """读取最新 checkpoint 的查询 DTO。"""

    thread_id: str = Field(
        min_length=1,
        description="需要读取最新 checkpoint 的 thread ID。",
    )


class LoadLatestCheckpointResultDto(CheckpointStoreDto):
    """读取最新 checkpoint 的结果 DTO。"""

    thread_id: str = Field(
        min_length=1,
        description="已查询的 checkpoint thread ID。",
    )
    latest_version: int = Field(
        ge=0,
        description="当前 thread 最新 checkpoint 版本；无 checkpoint 时为 0。",
    )
    checkpoint: CheckpointSnapshotDto | None = Field(
        default=None,
        description="最新 checkpoint 快照；thread 尚未保存 checkpoint 时为空。",
    )
    published_segments: list[SegmentPublishStateDto] = Field(
        default_factory=list,
        description="当前 thread 已发布 segment 摘要列表。",
    )


class GetCheckpointQueryDto(CheckpointRequestContextDto):
    """读取指定 checkpoint 的查询 DTO。"""

    thread_id: str = Field(
        min_length=1,
        description="checkpoint 所属 thread ID。",
    )
    checkpoint_id: str = Field(
        min_length=1,
        description="需要读取的 checkpoint ID。",
    )


class ListCheckpointsQueryDto(CheckpointRequestContextDto):
    """查询 checkpoint 历史的查询 DTO。"""

    thread_id: str = Field(
        min_length=1,
        description="需要查询 checkpoint 历史的 thread ID。",
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="本次查询最多返回的 checkpoint 摘要数量。",
    )
    cursor: str | None = Field(
        default=None,
        min_length=1,
        description="分页游标；为空表示从最新位置开始查询。",
    )
    status: CheckpointRecordStatus | None = Field(
        default=None,
        description="可选 checkpoint 状态过滤条件。",
    )
    created_after: datetime | None = Field(
        default=None,
        description="可选创建时间下界。",
    )
    created_before: datetime | None = Field(
        default=None,
        description="可选创建时间上界。",
    )


class CheckpointSummaryDto(CheckpointStoreDto):
    """checkpoint 历史摘要 DTO。"""

    checkpoint_id: str = Field(
        min_length=1,
        description="checkpoint 快照 ID。",
    )
    version: int = Field(
        ge=1,
        description="checkpoint 在当前 thread 内的递增版本号。",
    )
    status: CheckpointRecordStatus = Field(
        description="checkpoint 快照状态。",
    )
    current_node: str | None = Field(
        default=None,
        min_length=1,
        description="保存该 checkpoint 时的当前节点。",
    )
    graph_version: str = Field(
        min_length=1,
        description="图编排版本。",
    )
    state_schema_version: str = Field(
        min_length=1,
        description="checkpoint 状态 schema 版本。",
    )
    created_at: datetime = Field(
        description="checkpoint 创建时间。",
    )


class ListCheckpointsResultDto(CheckpointStoreDto):
    """查询 checkpoint 历史的结果 DTO。"""

    thread_id: str = Field(
        min_length=1,
        description="已查询的 checkpoint thread ID。",
    )
    items: list[CheckpointSummaryDto] = Field(
        default_factory=list,
        description="checkpoint 历史摘要列表；默认不包含完整状态体。",
    )
    next_cursor: str | None = Field(
        default=None,
        min_length=1,
        description="下一页分页游标；无更多数据时为空。",
    )


class SaveCheckpointCommandDto(CheckpointRequestContextDto):
    """保存 checkpoint 的命令 DTO。"""

    session_id: str = Field(
        min_length=1,
        description="上游可信传入的会话 ID。",
    )
    thread_id: str = Field(
        min_length=1,
        description="需要保存 checkpoint 的 thread ID。",
    )
    run_id: str = Field(
        min_length=1,
        description="当前图执行轮次 ID。",
    )
    expected_version: int = Field(
        ge=0,
        description="调用方预期的 thread 最新版本，用于乐观锁校验。",
    )
    graph_name: str = Field(
        min_length=1,
        description="图编排名称。",
    )
    graph_version: str = Field(
        min_length=1,
        description="图编排版本。",
    )
    state_schema_version: str = Field(
        min_length=1,
        description="checkpoint 状态 schema 版本。",
    )
    status: CheckpointRecordStatus = Field(
        description="本次保存的 checkpoint 状态。",
    )
    current_node: str | None = Field(
        default=None,
        min_length=1,
        description="本次保存时的当前节点。",
    )
    graph_state: GraphExecutionStateDto = Field(
        description="图编排恢复状态。",
    )
    business_state: SessionBusinessStateDto = Field(
        description="session 短期业务状态摘要。",
    )
    metadata: JsonMap = Field(
        default_factory=dict,
        description="checkpoint 普通元信息；仅允许恢复所需摘要、引用或哈希。",
    )
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        description="可选写入幂等键；具体策略由后续实现层决定。",
    )


class SaveCheckpointResultDto(CheckpointStoreDto):
    """保存 checkpoint 的结果 DTO。"""

    checkpoint_id: str = Field(
        min_length=1,
        description="新保存的 checkpoint ID。",
    )
    thread_id: str = Field(
        min_length=1,
        description="checkpoint 所属 thread ID。",
    )
    new_version: int = Field(
        ge=1,
        description="保存成功后的最新 checkpoint 版本号。",
    )
    status: CheckpointRecordStatus = Field(
        description="保存成功后的 checkpoint 状态。",
    )
    state_size_bytes: int = Field(
        ge=0,
        description="checkpoint 状态体序列化后的字节数。",
    )
    saved_at: datetime = Field(
        description="checkpoint 保存时间。",
    )


class MarkSegmentPublishedCommandDto(CheckpointRequestContextDto):
    """标记 segment 已发布的命令 DTO。"""

    thread_id: str = Field(
        min_length=1,
        description="segment 所属 checkpoint thread ID。",
    )
    run_id: str = Field(
        min_length=1,
        description="发布 segment 的图执行轮次 ID。",
    )
    segment_id: str = Field(
        min_length=1,
        description="已发布的业务分段 ID。",
    )
    task_id: str | None = Field(
        default=None,
        min_length=1,
        description="segment 关联的业务子任务 ID。",
    )
    published_at: datetime = Field(
        description="segment 成功发布的时间。",
    )
    metadata: JsonMap = Field(
        default_factory=dict,
        description="segment 发布普通元信息；不得承载完整响应正文。",
    )


class MarkSegmentPublishedResultDto(CheckpointStoreDto):
    """标记 segment 已发布的结果 DTO。"""

    segment: SegmentPublishStateDto = Field(
        description="已发布 segment 的幂等状态。",
    )
    idempotent: bool = Field(
        description="本次调用是否命中既有发布记录。",
    )


class LoadSessionStateQueryDto(CheckpointRequestContextDto):
    """读取 session 状态摘要的查询 DTO。"""

    thread_id: str = Field(
        min_length=1,
        description="需要读取 session 状态摘要的 thread ID。",
    )
    session_id: str | None = Field(
        default=None,
        min_length=1,
        description="可选会话 ID，用于后续实现层进行一致性校验。",
    )


class LoadSessionStateResultDto(CheckpointStoreDto):
    """读取 session 状态摘要的结果 DTO。"""

    thread_id: str = Field(
        min_length=1,
        description="已查询的 checkpoint thread ID。",
    )
    session_id: str = Field(
        min_length=1,
        description="thread 绑定的会话 ID。",
    )
    latest_checkpoint_id: str | None = Field(
        default=None,
        min_length=1,
        description="提供该 session 状态的最新 checkpoint ID。",
    )
    latest_version: int = Field(
        ge=0,
        description="提供该 session 状态的最新 checkpoint 版本。",
    )
    state: SessionBusinessStateDto = Field(
        description="session 短期业务状态摘要。",
    )


__all__: tuple[str, ...] = (
    "AcquireRunLockCommandDto",
    "AcquireRunLockResultDto",
    "CheckpointRequestContextDto",
    "CheckpointSnapshotDto",
    "CheckpointStoreDto",
    "CheckpointSummaryDto",
    "CheckpointThreadDto",
    "EnsureThreadCommandDto",
    "EnsureThreadResultDto",
    "GetCheckpointQueryDto",
    "GraphExecutionStateDto",
    "JsonMap",
    "ListCheckpointsQueryDto",
    "ListCheckpointsResultDto",
    "LoadLatestCheckpointQueryDto",
    "LoadLatestCheckpointResultDto",
    "LoadSessionStateQueryDto",
    "LoadSessionStateResultDto",
    "MarkSegmentPublishedCommandDto",
    "MarkSegmentPublishedResultDto",
    "ReleaseRunLockCommandDto",
    "ReleaseRunLockResultDto",
    "SaveCheckpointCommandDto",
    "SaveCheckpointResultDto",
    "SegmentPublishStateDto",
    "SessionBusinessStateDto",
    "TaskExecutionStateDto",
)

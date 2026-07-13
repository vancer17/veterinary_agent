##################################################################################################
# 文件: src/veterinary_agent/vet_task_decomposer/dto.py
# 作用: 定义 VetTaskDecomposer 请求、附件引用、source span、子任务、降级候选、结果与 trace DTO。
# 边界: 仅承载严格结构化数据和跨字段契约，不调用 LLM、不执行 fallback、不写入 LogicTraceStore。
##################################################################################################

from hashlib import sha256
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from veterinary_agent.vet_task_decomposer.enums import (
    AttachmentRole,
    DecompositionMethod,
    DecompositionStatus,
    TaskPriorityHint,
    VetTaskTraceWriteStatus,
    VetTaskType,
)

JsonMap: TypeAlias = dict[str, object]


class VetTaskDecomposerDto(BaseModel):
    """VetTaskDecomposer DTO 严格模型基类。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=False,
        validate_assignment=True,
    )

    @field_validator("*", mode="before")
    @classmethod
    def _strip_string_value(cls, value: Any) -> Any:
        """清理字符串字段值。

        :param value: 原始 DTO 字段值。
        :return: 字符串去除首尾空白后的值，或原始非字符串值。
        """

        if isinstance(value, str):
            return value.strip()
        return value


class AttachmentRefDto(VetTaskDecomposerDto):
    """本轮输入附件引用。"""

    attachment_id: str = Field(min_length=1, max_length=256, description="附件 ID。")
    mime_type: str = Field(min_length=1, max_length=256, description="附件 MIME 类型。")
    declared_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="入口或客户端声明的附件用途或类型。",
    )
    upload_order: int = Field(ge=0, description="附件在本轮请求中的顺序。")


class TextSpanDto(VetTaskDecomposerDto):
    """子任务回指用户原文的 source span。"""

    start_offset: int = Field(ge=0, description="原文起始字符偏移，闭区间。")
    end_offset: int = Field(ge=0, description="原文结束字符偏移，开区间。")
    text_hash: str = Field(
        min_length=1,
        max_length=128,
        description="span 原文片段的 sha256 摘要。",
    )

    @model_validator(mode="after")
    def _validate_span_offsets(self) -> "TextSpanDto":
        """校验 source span 偏移关系。

        :return: 已通过偏移校验的 source span。
        :raises ValueError: 当结束偏移不大于起始偏移时抛出。
        """

        if self.end_offset <= self.start_offset:
            raise ValueError("source_span.end_offset 必须大于 start_offset")
        return self


class AttachmentBindingDto(VetTaskDecomposerDto):
    """子任务与附件之间的绑定关系。"""

    attachment_id: str = Field(min_length=1, max_length=256, description="附件 ID。")
    attachment_role: AttachmentRole = Field(description="附件在子任务中的角色。")

    @model_validator(mode="after")
    def _validate_binding_role(self) -> "AttachmentBindingDto":
        """校验绑定记录不得使用 none 角色。

        :return: 已通过角色校验的附件绑定。
        :raises ValueError: 当绑定记录使用 none 角色时抛出。
        """

        if self.attachment_role is AttachmentRole.NONE:
            raise ValueError("无附件关系应使用空 attachment_bindings，而不是 none 绑定")
        return self


class VetSubTaskDto(VetTaskDecomposerDto):
    """后续安全评估使用的当前宠物子任务。"""

    task_id: str = Field(min_length=1, max_length=128, description="确定性子任务 ID。")
    task_type: VetTaskType = Field(description="受控任务类型。")
    current_pet_id: str = Field(
        min_length=1,
        max_length=128,
        description="PetSessionPolicy 确认的当前宠物 ID。",
    )
    source_span: TextSpanDto = Field(description="子任务对应的用户原文片段。")
    normalized_query: str = Field(
        min_length=1,
        max_length=32768,
        description="供后续节点使用的规范化查询文本。",
    )
    attachment_bindings: list[AttachmentBindingDto] = Field(
        default_factory=list,
        description="当前子任务关联的附件绑定列表。",
    )
    priority_hint: TaskPriorityHint = Field(
        default=TaskPriorityHint.UNKNOWN,
        description="拆解阶段的初始优先级提示。",
    )
    coverage_required: bool = Field(
        default=True,
        description="后续回复是否必须覆盖该子任务。",
    )
    requires_independent_segment: bool = Field(
        default=True,
        description="该子任务是否倾向独立生成回复段。",
    )
    confidence: float = Field(ge=0.0, le=1.0, description="拆解置信度。")


class VetTaskDecomposeRequestDto(VetTaskDecomposerDto):
    """VetTaskDecomposer 应用内拆解请求。"""

    request_id: str = Field(min_length=1, max_length=128, description="入口请求 ID。")
    trace_id: str = Field(min_length=1, max_length=128, description="全链路追踪 ID。")
    run_id: str = Field(
        min_length=1, max_length=128, description="GraphRuntime 运行 ID。"
    )
    session_id: str = Field(min_length=1, max_length=128, description="会话 ID。")
    user_id: str = Field(min_length=1, max_length=128, description="用户 ID。")
    current_pet_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="PetSessionPolicy 确认的当前宠物 ID。",
    )
    user_message: str = Field(
        default="",
        max_length=100000,
        description="本轮用户原文，允许 service 层映射空文本错误。",
    )
    attachments: list[AttachmentRefDto] = Field(
        default_factory=list,
        description="本轮附件引用。",
    )
    params_version: str = Field(
        min_length=1,
        max_length=128,
        description="本轮业务参数版本。",
    )
    config_snapshot_id: str = Field(
        min_length=1,
        max_length=256,
        description="本轮 RuntimeConfig 快照 ID。",
    )


class LocalFallbackResultDto(VetTaskDecomposerDto):
    """本地 span fallback 返回的占位候选结果。"""

    available: bool = Field(description="本地 fallback 是否成功产出候选。")
    tasks: list[VetSubTaskDto] = Field(
        default_factory=list,
        description="本地 fallback 产出的候选子任务。",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="本地 fallback 整体置信度。",
    )
    error_code: str | None = Field(
        default=None,
        min_length=1,
        description="本地 fallback 不可用时的稳定错误码。",
    )
    detail: str | None = Field(
        default=None,
        min_length=1,
        description="不含用户正文的 fallback 状态说明。",
    )

    @model_validator(mode="after")
    def _validate_available_result(self) -> "LocalFallbackResultDto":
        """校验本地 fallback 可用状态与任务列表一致。

        :return: 已通过关系校验的本地 fallback 结果。
        :raises ValueError: 当 available 为 True 但没有候选任务时抛出。
        """

        if self.available and not self.tasks:
            raise ValueError("available=True 时必须包含至少一个 fallback task")
        return self


class DecompositionTraceSummaryDto(VetTaskDecomposerDto):
    """VetTaskDecomposer 输出给业务 trace 的脱敏摘要。"""

    decomposer_version: str = Field(
        min_length=1,
        max_length=128,
        description="拆解器业务版本。",
    )
    method: DecompositionMethod = Field(description="本次采用的拆解方法。")
    task_count: int = Field(ge=1, description="归一化后的子任务数量。")
    task_types: list[VetTaskType] = Field(min_length=1, description="子任务类型列表。")
    llm_unavailable: bool = Field(description="LLM 主路径是否不可用。")
    fallback_used: bool = Field(description="是否使用本地 fallback 或单任务透传。")
    confidence: float = Field(ge=0.0, le=1.0, description="拆解整体置信度。")


class VetTaskDecomposeTraceRecordDto(VetTaskDecomposerDto):
    """可写入 LogicTraceStore 的 VetTaskDecomposer 摘要记录。"""

    schema_version: Literal["vet-task-decomposer.trace.v1"] = Field(
        default="vet-task-decomposer.trace.v1",
        description="任务拆解 trace 摘要结构版本。",
    )
    request_id: str = Field(min_length=1, description="入口请求 ID。")
    trace_id: str = Field(min_length=1, description="全链路追踪 ID。")
    run_id: str = Field(min_length=1, description="GraphRuntime 运行 ID。")
    session_id: str = Field(min_length=1, description="会话 ID。")
    user_id: str = Field(min_length=1, description="用户 ID。")
    current_pet_id: str = Field(min_length=1, description="当前宠物 ID。")
    input_text_hash: str = Field(
        min_length=1,
        max_length=128,
        description="用户原文全文 hash。",
    )
    attachment_count: int = Field(ge=0, description="本轮附件数量。")
    trace_summary: DecompositionTraceSummaryDto = Field(description="拆解摘要。")
    params_version: str = Field(min_length=1, description="业务参数版本。")
    config_snapshot_id: str = Field(min_length=1, description="配置快照 ID。")


class VetTaskTraceWriteResultDto(VetTaskDecomposerDto):
    """任务拆解 trace 写入结果。"""

    status: VetTaskTraceWriteStatus = Field(description="trace 摘要写入状态。")
    error_code: str | None = Field(
        default=None,
        min_length=1,
        description="trace 写入降级时的稳定错误码。",
    )
    retryable: bool = Field(default=False, description="是否允许稍后补偿重试。")
    detail: str | None = Field(
        default=None,
        min_length=1,
        description="不含业务正文的 trace 写入状态说明。",
    )


class VetTaskDecomposeResultDto(VetTaskDecomposerDto):
    """VetTaskDecomposer 标准拆解结果。"""

    tasks: list[VetSubTaskDto] = Field(min_length=1, description="拆解后的子任务列表。")
    trace_summary: DecompositionTraceSummaryDto = Field(description="拆解摘要。")
    status: DecompositionStatus = Field(description="拆解结果状态。")
    trace_delivery_status: VetTaskTraceWriteStatus = Field(
        default=VetTaskTraceWriteStatus.SKIPPED,
        description="拆解摘要 trace 写入状态。",
    )

    @model_validator(mode="after")
    def _validate_tasks_have_same_pet(self) -> "VetTaskDecomposeResultDto":
        """校验结果中全部子任务使用同一个当前宠物 ID。

        :return: 已通过宠物归属一致性校验的拆解结果。
        :raises ValueError: 当任务列表中存在不同 current_pet_id 时抛出。
        """

        pet_ids = {task.current_pet_id for task in self.tasks}
        if len(pet_ids) != 1:
            raise ValueError("所有 VetSubTask.current_pet_id 必须一致")
        return self


def build_text_hash(text: str) -> str:
    """构建用户原文或 span 片段的稳定 hash。

    :param text: 需要计算摘要的文本。
    :return: sha256 十六进制摘要。
    """

    return sha256(text.encode("utf-8")).hexdigest()


__all__: tuple[str, ...] = (
    "AttachmentBindingDto",
    "AttachmentRefDto",
    "DecompositionTraceSummaryDto",
    "JsonMap",
    "LocalFallbackResultDto",
    "TextSpanDto",
    "VetSubTaskDto",
    "VetTaskDecomposeRequestDto",
    "VetTaskDecomposeResultDto",
    "VetTaskDecomposeTraceRecordDto",
    "VetTaskDecomposerDto",
    "VetTaskTraceWriteResultDto",
    "build_text_hash",
)

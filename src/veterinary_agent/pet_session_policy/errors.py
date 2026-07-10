##################################################################################################
# 文件: src/veterinary_agent/pet_session_policy/errors.py
# 作用: 定义 PetSessionPolicy 统一错误 DTO、领域异常与错误码默认重试策略。
# 边界: 仅封装 L2 宠物会话策略错误语义，不执行存储调用、HTTP 映射或最终用户文案生成。
##################################################################################################

from typing import Final

from pydantic import Field

from veterinary_agent.pet_session_policy.dto import (
    JsonMap,
    PetSessionPolicyDecisionDto,
    PetSessionPolicyDto,
)
from veterinary_agent.pet_session_policy.enums import (
    PetSessionPolicyErrorCode,
    PetSessionTraceWriteStatus,
)

_DEFAULT_RETRYABLE_BY_CODE: Final[dict[PetSessionPolicyErrorCode, bool]] = {
    PetSessionPolicyErrorCode.REQUIRED_FIELD_MISSING: False,
    PetSessionPolicyErrorCode.PET_MISMATCH: False,
    PetSessionPolicyErrorCode.USER_MISMATCH: False,
    PetSessionPolicyErrorCode.SESSION_CLOSED: False,
    PetSessionPolicyErrorCode.SESSION_ARCHIVED: False,
    PetSessionPolicyErrorCode.STORE_UNAVAILABLE: True,
    PetSessionPolicyErrorCode.RUNTIME_CONFIG_UNAVAILABLE: True,
    PetSessionPolicyErrorCode.POLICY_DISABLED: False,
    PetSessionPolicyErrorCode.INTERNAL_ERROR: True,
}


class PetSessionPolicyErrorDto(PetSessionPolicyDto):
    """PetSessionPolicy 统一错误 DTO。"""

    code: PetSessionPolicyErrorCode = Field(
        description="PetSessionPolicy 稳定错误码。",
    )
    message: str = Field(
        min_length=1,
        description="面向工程排障的简短错误说明；不作为最终用户文案。",
    )
    retryable: bool = Field(
        description="调用方是否可以稍后或修正请求后重试。",
    )
    request_id: str = Field(
        min_length=1,
        description="本次请求 ID。",
    )
    trace_id: str = Field(
        min_length=1,
        description="本次全链路追踪 ID。",
    )
    decision: PetSessionPolicyDecisionDto = Field(
        description="触发当前错误的完整策略判定。",
    )
    trace_delivery_status: PetSessionTraceWriteStatus = Field(
        description="阻断判定摘要的逻辑链写入状态。",
    )
    conflict_with: JsonMap | None = Field(
        default=None,
        description="冲突对象的安全摘要；不得包含既有用户或宠物真实标识。",
    )


def is_pet_session_policy_error_retryable_by_default(
    code: PetSessionPolicyErrorCode,
) -> bool:
    """判断指定 PetSessionPolicy 错误码默认是否可重试。

    :param code: PetSessionPolicy 稳定错误码。
    :return: 若调用方默认可以重试，则返回 True。
    """

    return _DEFAULT_RETRYABLE_BY_CODE[code]


def build_pet_session_policy_error_dto(
    *,
    code: PetSessionPolicyErrorCode,
    message: str,
    request_id: str,
    trace_id: str,
    decision: PetSessionPolicyDecisionDto,
    trace_delivery_status: PetSessionTraceWriteStatus,
    retryable: bool | None = None,
    conflict_with: JsonMap | None = None,
) -> PetSessionPolicyErrorDto:
    """构建 PetSessionPolicy 统一错误 DTO。

    :param code: PetSessionPolicy 稳定错误码。
    :param message: 面向工程排障的简短错误说明。
    :param request_id: 本次请求 ID。
    :param trace_id: 本次全链路追踪 ID。
    :param decision: 触发当前错误的完整策略判定。
    :param trace_delivery_status: 策略判定摘要的逻辑链写入状态。
    :param retryable: 是否覆盖错误码默认重试策略。
    :param conflict_with: 冲突对象的安全摘要。
    :return: 已按默认重试策略补齐的 PetSessionPolicy 错误 DTO。
    """

    resolved_retryable = (
        is_pet_session_policy_error_retryable_by_default(code)
        if retryable is None
        else retryable
    )
    return PetSessionPolicyErrorDto(
        code=code,
        message=message,
        retryable=resolved_retryable,
        request_id=request_id,
        trace_id=trace_id,
        decision=decision,
        trace_delivery_status=trace_delivery_status,
        conflict_with=conflict_with,
    )


class PetSessionPolicyError(Exception):
    """PetSessionPolicy 领域异常。"""

    def __init__(
        self,
        *,
        code: PetSessionPolicyErrorCode,
        message: str,
        request_id: str,
        trace_id: str,
        decision: PetSessionPolicyDecisionDto,
        trace_delivery_status: PetSessionTraceWriteStatus,
        retryable: bool | None = None,
        conflict_with: JsonMap | None = None,
    ) -> None:
        """初始化 PetSessionPolicy 领域异常。

        :param code: PetSessionPolicy 稳定错误码。
        :param message: 面向工程排障的简短错误说明。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param decision: 触发当前错误的完整策略判定。
        :param trace_delivery_status: 策略判定摘要的逻辑链写入状态。
        :param retryable: 是否覆盖错误码默认重试策略。
        :param conflict_with: 冲突对象的安全摘要。
        :return: None。
        """

        self.error = build_pet_session_policy_error_dto(
            code=code,
            message=message,
            request_id=request_id,
            trace_id=trace_id,
            decision=decision,
            trace_delivery_status=trace_delivery_status,
            retryable=retryable,
            conflict_with=conflict_with,
        )
        super().__init__(self.error.message)

    @property
    def code(self) -> PetSessionPolicyErrorCode:
        """读取 PetSessionPolicy 稳定错误码。

        :return: 当前异常对应的 PetSessionPolicy 错误码。
        """

        return self.error.code

    @property
    def retryable(self) -> bool:
        """读取当前错误是否允许调用方重试。

        :return: 若当前错误允许重试，则返回 True。
        """

        return self.error.retryable

    def to_dto(self) -> PetSessionPolicyErrorDto:
        """转换为 PetSessionPolicy 统一错误 DTO。

        :return: 当前异常携带的错误 DTO。
        """

        return self.error

    def __str__(self) -> str:
        """转换为便于日志记录的简短字符串。

        :return: 包含策略判定、错误码与错误说明的字符串。
        """

        return f"{self.error.decision.decision}:{self.error.code}:{self.error.message}"


__all__: tuple[str, ...] = (
    "PetSessionPolicyError",
    "PetSessionPolicyErrorDto",
    "build_pet_session_policy_error_dto",
    "is_pet_session_policy_error_retryable_by_default",
)

"""
公共基础设施错误契约。

该模块为 ``core.llm`` 与后续核心模块提供统一的错误码和错误信封。错误类型
只描述工程执行状态，不描述医学语义缺陷，也不把失败转换为空业务结果。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum


class CoreErrorCode(StrEnum):
    """公共基础设施稳定错误码。

    这些错误码用于审计、日志和上层状态映射；调用方不得将错误码解释为
    兽医语义结论。
    """

    #: 配置缺失、非法或模块间绑定不一致。
    CONFIGURATION_INVALID = "configuration_invalid"

    #: 外部依赖当前不可用或返回不可恢复传输状态。
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"

    #: 外部依赖调用超时。
    DEPENDENCY_TIMEOUT = "dependency_timeout"

    #: 外部依赖认证失败。
    AUTHENTICATION_FAILED = "authentication_failed"

    #: 外部依赖授权拒绝。
    AUTHORIZATION_DENIED = "authorization_denied"

    #: 外部依赖触发限流。
    RATE_LIMITED = "rate_limited"

    #: 模型传输层调用失败。
    LLM_TRANSPORT_FAILED = "llm_transport_failed"

    #: 模型响应不符合 OpenAI-compatible 完成协议。
    LLM_PROTOCOL_INVALID = "llm_protocol_invalid"

    #: 模型调用预算或并发治理超限。
    LLM_BUDGET_EXCEEDED = "llm_budget_exceeded"

    #: 模型输出不是严格 JSON 或包含多个 JSON 值。
    STRICT_JSON_INVALID = "strict_json_invalid"

    #: 模型输出未通过严格 schema 校验。
    SCHEMA_INVALID = "schema_invalid"

    #: Grouped Review 输出未通过 group 专属闭合校验。
    REVIEW_GROUP_SCHEMA_INVALID = "review_group_schema_invalid"

    #: 合并后的 Review Delta 触发语义层声明的确定性冲突规则。
    REVIEW_CONFLICT = "review_conflict"

    #: Targeted Repair 计划、任务绑定或渲染变量配置非法。
    REPAIR_PLAN_INVALID = "repair_plan_invalid"

    #: Targeted Repair 输出未通过严格 JSON 或 strict schema 校验。
    REPAIR_SCHEMA_INVALID = "repair_schema_invalid"

    #: Targeted Repair 输出未通过语义层确定性输出校验。
    REPAIR_OUTPUT_INVALID = "repair_output_invalid"

    #: Targeted Repair 输出试图影响未授权 target 或 selector。
    REPAIR_UNAUTHORIZED_TARGET = "repair_unauthorized_target"

    #: Targeted Repair 输出与授权 target 当前值完全相同。
    REPAIR_NO_CHANGE = "repair_no_change"

    #: Targeted Repair 任务数、attempt 或执行预算超限。
    REPAIR_BUDGET_EXCEEDED = "repair_budget_exceeded"

    #: Targeted Repair 执行失败或产生未分类异常。
    REPAIR_FAILED = "repair_failed"

    #: 模型执行审计写入失败。
    LLM_AUDIT_FAILED = "llm_audit_failed"

    #: 核心基础设施出现未分类内部错误。
    CORE_INTERNAL_ERROR = "core_internal_error"


class CoreError(Exception):
    """公共基础设施异常基类。

    所有核心错误均携带稳定错误码、脱敏上下文和传输层可重试标记。异常本身
    不保存 API key、完整 prompt、完整模型输出或数据库连接串。
    """

    def __init__(
        self,
        *,
        error_code: CoreErrorCode,
        message: str,
        retryable: bool = False,
        context: Mapping[str, str] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        """初始化公共错误信封。

        :param error_code: 稳定机器可读错误码。
        :param message: 面向工程排查的脱敏错误消息。
        :param retryable: 是否表示传输层可重试；不表示语义可修复。
        :param context: 已脱敏的定位上下文。
        :param cause: 可选底层异常，保留调用栈但不直接透出给用户。
        :return: 无返回值；构造异常信封对象。
        """
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.retryable = retryable
        self.context = dict(context or {})
        self.cause = cause


class ConfigurationError(CoreError):
    """配置缺失、非法或模块绑定不一致。"""


class DependencyUnavailableError(CoreError):
    """外部依赖不可用或返回不可恢复传输状态。"""


class DependencyTimeoutError(CoreError):
    """外部依赖调用超时。"""


class AuthenticationError(CoreError):
    """外部依赖认证失败。"""


class AuthorizationDeniedError(CoreError):
    """外部依赖授权拒绝。"""


class RateLimitError(CoreError):
    """外部依赖限流，可由传输层按策略重试。"""


class LLMTransportError(CoreError):
    """模型传输层失败，例如网络中断、408、429 或 5xx。"""


class LLMProtocolError(CoreError):
    """模型完成响应协议非法，例如空内容、截断、refusal 或 tool call。"""


class LLMBudgetExceededError(CoreError):
    """模型请求次数、token 或并发预算超限。"""


class StrictJSONValidationError(CoreError):
    """模型输出不是可接受的单一严格 JSON 对象。"""


class SchemaValidationError(CoreError):
    """模型输出未通过 strict Pydantic schema 校验。"""


class ReviewGroupSchemaError(CoreError):
    """Grouped Review 输出违反 group 专属协议。"""


class ReviewConflictError(CoreError):
    """同一 target 上的缺陷码触发显式冲突规则。"""


class RepairPlanError(CoreError):
    """Targeted Repair 计划或任务绑定非法。"""


class RepairSchemaError(CoreError):
    """Targeted Repair 输出未通过严格结构校验。"""


class RepairOutputError(CoreError):
    """Targeted Repair 输出未通过语义层确定性校验。"""


class RepairUnauthorizedTargetError(CoreError):
    """Targeted Repair 输出影响未授权 target 或 selector。"""


class RepairNoChangeError(CoreError):
    """Targeted Repair 输出未改变授权 target 当前值。"""


class RepairBudgetExceededError(CoreError):
    """Targeted Repair 任务或 attempt 预算超限。"""


class RepairFailedError(CoreError):
    """Targeted Repair 执行失败或出现未分类异常。"""


class LLMAuditError(CoreError):
    """模型执行审计事件写入失败。"""


__all__ = [
    "AuthenticationError",
    "AuthorizationDeniedError",
    "ConfigurationError",
    "CoreError",
    "CoreErrorCode",
    "DependencyTimeoutError",
    "DependencyUnavailableError",
    "LLMAuditError",
    "LLMBudgetExceededError",
    "LLMProtocolError",
    "LLMTransportError",
    "RateLimitError",
    "RepairBudgetExceededError",
    "RepairFailedError",
    "RepairNoChangeError",
    "RepairOutputError",
    "RepairPlanError",
    "RepairSchemaError",
    "RepairUnauthorizedTargetError",
    "ReviewConflictError",
    "ReviewGroupSchemaError",
    "SchemaValidationError",
    "StrictJSONValidationError",
]

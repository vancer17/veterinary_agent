"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/errors.py
作用：定义受限语义协作 DAG 的 Skill、TurnSnapshot、Plan 与调度错误类型。
范围：覆盖 SkillSpec 校验、SkillCatalog 注册、投影一致性、快照构建、
      上下文预算、digest 校验、Plan 编译、模型适配、结构化选择失败、
      DAG 状态仓储访问、run 租约冲突与任务执行端口失败。
说明：本文件只承载错误语义，不访问数据库、不调用模型、不提供任何回退路径。
=============================================================================
"""

from __future__ import annotations

from typing import ClassVar


class SemanticCollaborationError(Exception):
    """表示受限语义协作生产链路的基础错误。

    :return: 无返回值；该类型用于保留统一异常族和稳定调用栈。
    """


class SkillContractError(SemanticCollaborationError):
    """表示单个 SkillSpec 违反机器可读契约。

    :return: 无返回值；该错误用于阻断非法 SKILL 进入生产目录。
    """


class SkillCatalogError(SemanticCollaborationError):
    """表示 SkillCatalog 全局一致性或解析失败。

    :return: 无返回值；该错误用于启动期 Fail Fast 与运行期显式解析失败。
    """


class SkillProjectionError(SemanticCollaborationError):
    """表示 SKILL.md 提示词投影与权威 manifest 不一致。

    :return: 无返回值；该错误只用于启动期投影审计，不作为运行时契约来源。
    """


class TurnSnapshotError(SemanticCollaborationError):
    """表示 TurnSnapshot 构建、投影或校验失败。

    :return: 无返回值；该类型用于保留 M02 输入前置链路的统一异常族。
    """


class TurnSnapshotBudgetExceededError(TurnSnapshotError):
    """表示 TurnSnapshot 或 SKILL 可见上下文超过硬预算。

    :return: 无返回值；该错误禁止通过截断、丢弃或压缩权威上下文来恢复。
    """

    failure_code: ClassVar[str] = "context_budget_exceeded"

    def __init__(
        self,
        message: str,
        *,
        budget_name: str,
        used: int,
        limit: int,
    ) -> None:
        """初始化带稳定审计字段的上下文预算错误。

        :param message: 面向工程排障的错误说明。
        :param budget_name: 超出预算的稳定资源名称。
        :param used: 当前确定性计数结果。
        :param limit: 当前硬预算上限。
        :return: 无返回值。
        """
        super().__init__(message)
        self.budget_name = budget_name
        self.used = used
        self.limit = limit


class TurnSnapshotDigestMismatchError(TurnSnapshotError):
    """表示任务 envelope 与 TurnSnapshot digest 不一致。

    :return: 无返回值；该错误阻止生成、审查和修复任务混用上下文版本。
    """

    failure_code: ClassVar[str] = "context_digest_mismatch"


class TurnSnapshotSourceUnavailableError(TurnSnapshotError):
    """表示受限上下文来源读取失败或尚未接入。

    :return: 无返回值；该错误禁止把来源失败转换成空历史或空事实。
    """

    failure_code: ClassVar[str] = "snapshot_source_unavailable"

    def __init__(self, message: str, *, source_name: str) -> None:
        """初始化带来源标识的上下文来源失败。

        :param message: 面向工程排障的错误说明。
        :param source_name: 稳定的受限来源名称。
        :return: 无返回值。
        """
        super().__init__(message)
        self.source_name = source_name


class TurnSnapshotContextPolicyViolationError(TurnSnapshotError):
    """表示 SkillSpec 声明了 TurnSnapshot 无法满足的上下文策略。

    :return: 无返回值；该错误用于阻断越权或非法资源投影。
    """


class UnsupportedTurnInputError(TurnSnapshotError):
    """表示当前 TurnSnapshot 契约不支持该输入形态。

    :return: 无返回值；该错误禁止静默忽略附件或非文本输入。
    """


class PlanError(SemanticCollaborationError):
    """表示 M03 计划选择、编译或模型适配失败。

    :return: 无返回值；该类型禁止计划失败转换为空任务或默认计划。
    """


class PlanCompilationError(PlanError):
    """表示确定性 Plan 编译输入违反生产策略或权威目录。

    :return: 无返回值；该错误在进入 Plan Validator 前显式阻断非法选择。
    """

    failure_code: str

    def __init__(self, message: str, *, failure_code: str) -> None:
        """初始化带稳定失败码的计划编译错误。

        :param message: 面向工程排障的错误说明。
        :param failure_code: 稳定计划编译失败码。
        :return: 无返回值。
        """
        super().__init__(message)
        self.failure_code = failure_code


class PlanModelClientError(PlanError):
    """表示任务规划结构化模型客户端调用失败。

    :return: 无返回值；该错误不触发旧问诊语义链路回退。
    """

    failure_code: ClassVar[str] = "plan_model_call_failed"


class PlanSelectionSchemaError(PlanError):
    """表示任务规划模型返回值未通过 PlanSelection 契约。

    :return: 无返回值；该错误不进行宽松 JSON 文本修复。
    """
    failure_code: ClassVar[str] = "plan_response_parse_failed"


class SchedulerError(SemanticCollaborationError):
    """表示 M04 Temporal-first 调度契约或 workflow 输入失败。

    :return: 无返回值；该错误不允许被转换成空任务或默认计划。
    """


class DAGProjectionRepositoryError(SchedulerError):
    """表示语义协作 DAG 只读投影仓储访问或契约冲突。

    :return: 无返回值；投影失败不触发内存调度或旧语义链路回退。
    """


class SemanticTaskExecutionError(SchedulerError):
    """表示受限语义任务执行端口发生不可恢复失败。

    :return: 无返回值；该错误不触发旧问诊语义链路回退。
    """

    failure_code: str

    def __init__(
        self,
        message: str,
        *,
        failure_code: str,
    ) -> None:
        """初始化带稳定失败码的任务执行错误。

        :param message: 面向工程排障的错误说明。
        :param failure_code: 稳定任务端口失败码。
        :return: 无返回值。
        """
        super().__init__(message)
        self.failure_code = failure_code

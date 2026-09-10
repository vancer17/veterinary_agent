# =============================================================================
# 文件: src/vet_agent/core/repair/contracts.py
# 作用: 定义 Targeted Repair 的任务、计划、执行结果与语义层输出校验端口。
# 范围: 覆盖修复任务授权范围、attempt 预算、原始快照摘要、prompt 绑定、
#       严格输出 schema 与修复结果的公共数据形态及启动期闭合校验。
# 说明: 本文件只承载工程协议，不理解缺陷码含义，不推导修复动作，不应用
#       patch，也不把本地 selector 当作长期身份或权威事实 ID。
# =============================================================================
"""Targeted Repair 公共工程契约。

这些对象位于缺陷簇聚合之后、语义层确定性 patch 之前。语义层负责路由缺陷
并构造修复任务；本模块只保证任务结构、授权范围和执行预算在进入模型调用前
已经闭合。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, TypeVar

from vet_agent.core import errors as core_errors
from vet_agent.core.llm import LLMResponseMetadata
from vet_agent.core.review import ReviewTargetKind
from vet_agent.core.schema import StrictModel

T = TypeVar("T", bound=StrictModel)
TValidator_contra = TypeVar(
    "TValidator_contra",
    bound=StrictModel,
    contravariant=True,
)

_TARGET_PATTERNS: dict[ReviewTargetKind, str] = {
    ReviewTargetKind.TURN: r"t0",
    ReviewTargetKind.CONTROL: r"c(?:0|[1-9][0-9]*)",
    ReviewTargetKind.REQUEST_ITEM: r"r(?:0|[1-9][0-9]*)",
    ReviewTargetKind.ANCHOR: r"a(?:0|[1-9][0-9]*)",
    ReviewTargetKind.PET_FACT: r"p(?:0|[1-9][0-9]*)",
    ReviewTargetKind.SCOPE: r"s(?:0|[1-9][0-9]*)",
    ReviewTargetKind.PROPOSITION: r"q(?:0|[1-9][0-9]*)",
}

_SELECTOR_PATTERN = (
    r"(?:t0|c(?:0|[1-9][0-9]*)|r(?:0|[1-9][0-9]*)|"
    r"a(?:0|[1-9][0-9]*)|p(?:0|[1-9][0-9]*)|"
    r"s(?:0|[1-9][0-9]*)|q(?:0|[1-9][0-9]*))"
)


def validate_repair_target(
    *,
    target: str,
    target_kind: ReviewTargetKind,
) -> None:
    """校验修复 target 与工程分类的 namespace 绑定。

    该函数位于 RepairTask 构造阶段，用于保证系统传入的本地 selector 与
    ``ReviewTargetKind`` 一致，防止模型调用或后续 patch 定位到错误 namespace。

    :param target: 系统分配的本地修复 target selector。
    :param target_kind: 语义层声明的 target 工程分类。
    :return: 无返回值；绑定合法时允许构造修复任务。
    :raises vet_agent.core.errors.RepairPlanError: 当 target 为空、格式非法
        或与 target kind 不匹配。
    """
    if target == "" or re.fullmatch(_TARGET_PATTERNS[target_kind], target) is None:
        raise core_errors.RepairPlanError(
            error_code=core_errors.CoreErrorCode.REPAIR_PLAN_INVALID,
            message="Repair target 与 target kind 不匹配",
            context={
                "target": target or "<empty>",
                "target_kind": target_kind.value,
            },
        )


def _validate_authorized_selectors(
    *,
    authorized_selectors: tuple[str, ...],
) -> None:
    """校验修复任务可引用的本地 selector 集合。

    该函数只检查 selector 语法和唯一性，不判断语义上下文是否足够，也不推断
    scope 或 proposition 的医学关系。

    :param authorized_selectors: 系统为当前任务绑定的授权 selector 集合。
    :return: 无返回值；集合合法时允许进入调度器。
    :raises vet_agent.core.errors.RepairPlanError: 当 selector 为空、格式非法
        或重复。
    """
    invalid_selectors = [
        selector
        for selector in authorized_selectors
        if re.fullmatch(_SELECTOR_PATTERN, selector) is None
    ]
    if (
        not authorized_selectors
        or invalid_selectors
        or len(authorized_selectors) != len(set(authorized_selectors))
    ):
        raise core_errors.RepairPlanError(
            error_code=core_errors.CoreErrorCode.REPAIR_PLAN_INVALID,
            message="Repair 授权 selector 集合非法",
            context={
                "selectors": ",".join(invalid_selectors),
                "reasons": (
                    "authorized_selectors_empty"
                    if not authorized_selectors
                    else "authorized_selectors_invalid_or_duplicated"
                ),
            },
        )


def _is_sha256_hex(value: str) -> bool:
    """判断字符串是否为 64 位十六进制 SHA-256 摘要。

    该函数用于绑定同一轮修复的原始快照摘要，防止多个修复任务基于不同快照
    的结果被误加入同一事务式 join。

    :param value: 待检查的摘要字符串。
    :return: 符合 SHA-256 十六进制格式时返回 ``True``。
    """
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value.lower()
    )


@dataclass(frozen=True, slots=True)
class RepairTask[T: StrictModel]:
    """一个 target 级 Targeted Repair 的系统任务。

    语义层完成缺陷路由后构造该对象。任务中的缺陷短语只描述观察现象，缺陷码
    或短语均不由 core 解释为删除、替换、拆分或合并动作。
    """

    #: 系统生成的修复任务工程标识，不由模型生成。
    repair_task_id: str

    #: 当前快照中的本地 target selector，不是长期身份。
    target: str

    #: target 的工程分类，用于 namespace 与调度审计。
    target_kind: ReviewTargetKind

    #: 人工维护且进入 Repair prompt 的完整缺陷短语簇。
    defect_phrases: tuple[str, ...]

    #: 本次修复允许引用的本地 selector 集合，由系统绑定。
    authorized_selectors: tuple[str, ...]

    #: exact version prompt 标识。
    prompt_id: str

    #: exact version prompt 版本，不允许 latest。
    prompt_version: str

    #: 与语义层 Generator / Repair 薄契约一致的严格输出 schema。
    output_schema: type[T]

    #: 当前逻辑修复轮次；初始实现固定为 1。
    attempt: int

    def __post_init__(self) -> None:
        """校验修复任务的身份、授权范围、prompt 与 schema 绑定。

        :return: 无返回值；任一工程绑定不闭合时在构造阶段显式失败。
        :raises vet_agent.core.errors.RepairPlanError: 当任务标识、target、
           缺陷短语、授权 selector、prompt 版本或输出 schema 非法。
        """
        invalid_reasons: list[str] = []
        if not self.repair_task_id:
            invalid_reasons.append("repair_task_id_empty")
        validate_repair_target(
            target=self.target,
            target_kind=self.target_kind,
        )
        _validate_authorized_selectors(
            authorized_selectors=self.authorized_selectors,
        )
        if (
            not self.defect_phrases
            or any(not phrase.strip() for phrase in self.defect_phrases)
            or len(self.defect_phrases) != len(set(self.defect_phrases))
        ):
            invalid_reasons.append("defect_phrases_invalid")
        if not self.prompt_id:
            invalid_reasons.append("prompt_id_empty")
        if (
            not self.prompt_version
            or self.prompt_version.casefold() == "latest"
        ):
            invalid_reasons.append("prompt_version_not_exact")
        if self.attempt <= 0:
            invalid_reasons.append("attempt_not_positive")
        if invalid_reasons:
            raise core_errors.RepairPlanError(
                error_code=core_errors.CoreErrorCode.REPAIR_PLAN_INVALID,
                message="RepairTask 配置非法",
                context={
                    "repair_task_id": self.repair_task_id or "<empty>",
                    "reasons": ",".join(invalid_reasons),
                },
            )


@dataclass(frozen=True, slots=True)
class RepairPlanSpec[T: StrictModel]:
    """一轮基于同一原始快照的修复计划。

    计划中的所有任务必须共享同一个 base snapshot digest，并由调度器统一
    收集输出；任一任务失败时，整轮计划不向语义层 patch 提交部分结果。
    """

    #: 待执行的 target 级修复任务集合。
    tasks: tuple[RepairTask[T], ...]

    #: 本轮所有任务共同基于的原始快照 SHA-256 摘要。
    base_snapshot_digest: str

    #: 同一 target 允许的最大逻辑修复轮次；初始实现建议为 1。
    max_attempts_per_target: int = 1

    #: 一轮修复允许的最大任务数量。
    max_total_tasks: int = 32

    #: 修复任务最大并发数；为 1 时表示串行执行。
    max_concurrency: int = 4

    def __post_init__(self) -> None:
        """校验修复计划的预算、身份唯一性与快照一致性。

        :return: 无返回值；计划非法时在模型调用前显式失败。
        :raises vet_agent.core.errors.RepairPlanError: 当任务为空、重复、
            超预算、并发配置非法或快照摘要非法。
        """
        invalid_reasons: list[str] = []
        task_ids = [task.repair_task_id for task in self.tasks]
        targets = [task.target for task in self.tasks]
        if not self.tasks:
            invalid_reasons.append("tasks_empty")
        if len(task_ids) != len(set(task_ids)):
            invalid_reasons.append("repair_task_ids_duplicated")
        if len(targets) != len(set(targets)):
            invalid_reasons.append("targets_duplicated")
        if any(
            task.output_schema is not self.tasks[0].output_schema
            for task in self.tasks
        ):
            invalid_reasons.append("output_schemas_mixed")
        if not _is_sha256_hex(self.base_snapshot_digest):
            invalid_reasons.append("base_snapshot_digest_invalid")
        if self.max_attempts_per_target <= 0:
            invalid_reasons.append("max_attempts_not_positive")
        if self.max_total_tasks <= 0:
            invalid_reasons.append("max_total_tasks_not_positive")
        if self.max_concurrency <= 0:
            invalid_reasons.append("max_concurrency_not_positive")
        if invalid_reasons:
            raise core_errors.RepairPlanError(
                error_code=core_errors.CoreErrorCode.REPAIR_PLAN_INVALID,
                message="RepairPlan 配置非法",
                context={"reasons": ",".join(invalid_reasons)},
            )
        if len(self.tasks) > self.max_total_tasks:
            raise core_errors.RepairBudgetExceededError(
                error_code=core_errors.CoreErrorCode.REPAIR_BUDGET_EXCEEDED,
                message="Repair 任务数量超过预算",
                context={
                    "task_count": str(len(self.tasks)),
                    "max_total_tasks": str(self.max_total_tasks),
                },
            )
        if any(
            task.attempt > self.max_attempts_per_target
            for task in self.tasks
        ):
            raise core_errors.RepairBudgetExceededError(
                error_code=core_errors.CoreErrorCode.REPAIR_BUDGET_EXCEEDED,
                message="Repair attempt 超过预算",
                context={"reasons": "attempt_budget_exceeded"},
            )


@dataclass(frozen=True, slots=True)
class RepairTaskResult[T: StrictModel]:
    """一个已通过工程校验的修复任务结果。

    该结果仍是语义层 patch 的候选输入，不代表最终语义对象已被接受；最终
    patch、join、renumber 和修复后 Review 均由语义层实现。
    """

    #: 修复任务工程标识。
    repair_task_id: str

    #: 当前原始快照中的本地 target selector。
    target: str

    #: target 的工程分类。
    target_kind: ReviewTargetKind

    #: 已通过 Strict JSON Gateway、strict schema 与语义层 validator 的输出。
    output: T

    #: 唯一模型入口返回的脱敏执行 metadata。
    llm_metadata: LLMResponseMetadata

    #: typed 输出的确定性 SHA-256 摘要。
    output_digest: str


class RepairOutputValidator(Protocol[TValidator_contra]):
    """语义层提供给 core.repair 的确定性输出校验端口。

    core 只负责在严格 schema 校验之后调用该端口；输出是否 no change、是否
    越权、是否允许空数组以及数量限制，均由对应语义层实现。
    """

    def validate(
        self,
        *,
        task: RepairTask[TValidator_contra],
        output: TValidator_contra,
    ) -> None:
        """校验一个修复输出是否满足该语义域的授权与形态边界。

        :param task: 当前正在执行的 target 级修复任务。
        :param output: 已通过严格 schema 校验的 typed 模型输出。
        :return: 无返回值；输出合法时不抛出异常。
        :raises vet_agent.core.errors.CoreError: 当输出无效、无变化或越权时
            抛出对应 repair 错误。
        """


__all__ = [
    "RepairOutputValidator",
    "RepairPlanSpec",
    "RepairTask",
    "RepairTaskResult",
    "validate_repair_target",
]

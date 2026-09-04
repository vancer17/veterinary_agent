"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/plan_contracts.py
作用：定义受限语义协作 DAG 的 M03 PlanPolicy 与 Root Plan IR 契约。
范围：覆盖确定性生产计划策略、任务与 envelope 展开结果、schema 引用、
      依赖边、canonical plan 身份以及显式校验失败状态。
说明：本文件是纯契约层，不调用模型、不访问数据库、不执行 DAG 调度，也不提供
      默认计划、旧链路回退或宽松 JSON 修复路径。
=============================================================================
"""

from __future__ import annotations

import json
from enum import StrEnum
from graphlib import CycleError, TopologicalSorter
from hashlib import sha256
from typing import Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import SchemaContract

PLAN_IR_VERSION: Final = "1.0.0"
PLAN_POLICY_VERSION: Final = "3.0.0"


class PlanEnvelopeKind(StrEnum):
    """表示计划中可执行的受限语义 envelope 类型。

    :return: 无返回值；该枚举防止规划器发明非生产 envelope 类型。
    """

    TURN = "turn"
    CLAIM = "claim"


class PlanSkillRequirementMode(StrEnum):
    """表示生产 PlanPolicy 中单个 SKILL 的选择模式。

    :return: 无返回值；该枚举把必选、条件必选、可选与禁止显式分开。
    """

    ALWAYS = "always"
    WHEN_CLAIM_ENVELOPE_PRESENT = "when_claim_envelope_present"
    OPTIONAL = "optional"
    FORBIDDEN = "forbidden"


class PlanDependencyScope(StrEnum):
    """表示计划依赖规则解析依赖任务时使用的 envelope 范围。

    :return: 无返回值；该枚举避免依赖目标被解释成自由任务查找。
    """

    ROOT_ENVELOPE = "root_envelope"
    SAME_ENVELOPE = "same_envelope"


class PlanTaskSelectionSource(StrEnum):
    """表示计划任务由确定性系统规则产生。

    :return: 无返回值；所有任务来源均不允许由模型自选。
    """

    PLAN_POLICY = "plan_policy"
    DETERMINISTIC_REVIEW_EXPANSION = "deterministic_review_expansion"
    DETERMINISTIC_REPAIR_EXPANSION = "deterministic_repair_expansion"


class PlanValidationFailureCode(StrEnum):
    """表示 Plan IR 进入调度前的稳定准入失败编码。

    :return: 无返回值；该枚举确保失败不会被转换成空计划或 unknown。
    """

    PLAN_SCHEMA_INVALID = "plan_schema_invalid"
    PLAN_BUDGET_EXCEEDED = "plan_budget_exceeded"
    UNKNOWN_SKILL_SELECTED = "unknown_skill_selected"
    SKILL_VERSION_INVALID = "skill_version_invalid"
    DUPLICATE_TASK_ID = "duplicate_task_id"
    DEPENDENCY_NOT_FOUND = "dependency_not_found"
    SELF_DEPENDENCY_DETECTED = "self_dependency_detected"
    DUPLICATE_DEPENDENCY = "duplicate_dependency"
    DEPENDENCY_CYCLE_DETECTED = "dependency_cycle_detected"
    ENVELOPE_NOT_FOUND = "envelope_not_found"
    DUPLICATE_ENVELOPE = "duplicate_envelope"
    ENVELOPE_POLICY_VIOLATION = "envelope_policy_violation"
    CONTEXT_POLICY_VIOLATION = "context_policy_violation"
    OUTPUT_SCHEMA_MISMATCH = "output_schema_mismatch"
    PLAN_POLICY_VIOLATION = "plan_policy_violation"
    MANDATORY_TASK_MISSING = "mandatory_task_missing"
    FORBIDDEN_SKILL_SELECTED = "forbidden_skill_selected"
    SNAPSHOT_DIGEST_MISMATCH = "snapshot_digest_mismatch"
    TURN_ID_MISMATCH = "turn_id_mismatch"
    SKILL_CATALOG_DIGEST_MISMATCH = "skill_catalog_digest_mismatch"
    PLAN_ID_INVALID = "plan_id_invalid"
    EMPTY_PLAN = "empty_plan"
    NON_CANONICAL_ORDER = "non_canonical_order"


class PlanDependencyRule(BaseModel):
    """表示 PlanPolicy 声明的一条静态依赖边规则。

    :return: 无返回值；依赖由确定性编译层生成，不由模型手写。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    dependency_skill_id: str = Field(
        min_length=1,
        max_length=120,
        description="被依赖 SKILL 的稳定标识。",
    )
    dependency_scope: PlanDependencyScope = Field(
        description="依赖任务解析时使用的 envelope 范围。",
    )


class PlanSkillRule(BaseModel):
    """表示单个生产 SKILL 在初始 Turn Plan 中的准入与依赖规则。

    :return: 无返回值；该规则是 PlanPolicy 的最小组成单元。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    skill_id: str = Field(
        min_length=1,
        max_length=120,
        description="生产 SkillCatalog 中已注册的 SKILL 标识。",
    )
    skill_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$",
        description="生产 PlanPolicy 绑定的精确 SKILL 版本。",
    )
    target_envelope_kind: PlanEnvelopeKind = Field(
        description="该 SKILL 任务允许绑定的 envelope 类型。",
    )
    requirement: PlanSkillRequirementMode = Field(
        description="该 SKILL 在初始 Turn Plan 中的选择模式。",
    )
    depends_on: tuple[PlanDependencyRule, ...] = Field(
        default=(),
        description="确定性编译时展开的依赖规则集合。",
    )

    @model_validator(mode="after")
    def validate_dependency_rules(self) -> Self:
        """校验单条 SKILL 规则内依赖不重复。

        :return: 返回通过基础闭合校验的 SKILL 规则。
        :raises ValueError: 依赖目标重复声明时抛出。
        """
        dependency_ids = [
            dependency.dependency_skill_id
            for dependency in self.depends_on
        ]
        if len(dependency_ids) != len(set(dependency_ids)):
            raise ValueError("duplicate plan dependency rule")
        return self


class PlanPolicySpec(BaseModel):
    """表示初始 Turn Plan 的版本化生产计划策略。

    :return: 无返回值；该对象由系统持有，模型不可见、不可修改。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    policy_id: str = Field(
        min_length=1,
        max_length=160,
        description="生产计划策略稳定标识。",
    )
    policy_version: Literal["3.0.0"] = Field(
        description="PlanPolicy 契约版本。",
    )
    max_task_count: int = Field(
        ge=1,
        le=128,
        description="单次 Turn Plan 允许的最大任务数量。",
    )
    skills: tuple[PlanSkillRule, ...] = Field(
        min_length=1,
        description="初始 Turn Plan 可消费的完整 SKILL 规则集合。",
    )

    @model_validator(mode="after")
    def validate_policy_closure(self) -> Self:
        """校验计划策略内 SKILL 与依赖规则的闭合性。

        :return: 返回通过闭合校验的生产策略。
        :raises ValueError: 存在重复 SKILL、缺失依赖或非法选择模式时抛出。
        """
        skill_ids = [rule.skill_id for rule in self.skills]
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("duplicate plan skill rule")
        known_skill_ids = set(skill_ids)
        always_count = sum(
            rule.requirement == PlanSkillRequirementMode.ALWAYS
            for rule in self.skills
        )
        if always_count == 0:
            raise ValueError("plan policy requires at least one always task")
        for rule in self.skills:
            if (
                rule.requirement == PlanSkillRequirementMode.ALWAYS
                and rule.target_envelope_kind != PlanEnvelopeKind.TURN
            ):
                raise ValueError("always-required skill must target the turn envelope")
            if (
                rule.requirement
                == PlanSkillRequirementMode.WHEN_CLAIM_ENVELOPE_PRESENT
                and rule.target_envelope_kind != PlanEnvelopeKind.CLAIM
            ):
                raise ValueError("conditional skill must target a claim envelope")
            if rule.requirement == PlanSkillRequirementMode.FORBIDDEN and rule.depends_on:
                raise ValueError("forbidden skill cannot declare dependencies")
            for dependency in rule.depends_on:
                if dependency.dependency_skill_id not in known_skill_ids:
                    raise ValueError("plan dependency skill is not declared")
                if (
                    dependency.dependency_scope == PlanDependencyScope.SAME_ENVELOPE
                    and rule.target_envelope_kind != PlanEnvelopeKind.CLAIM
                ):
                    raise ValueError(
                        "same-envelope dependency is only valid for claim skills",
                    )
        minimum_task_count = always_count
        if self.max_task_count < minimum_task_count:
            raise ValueError("plan policy task budget cannot cover required tasks")
        policy_graph = {
            rule.skill_id: {
                dependency.dependency_skill_id
                for dependency in rule.depends_on
            }
            for rule in self.skills
        }
        try:
            TopologicalSorter(policy_graph).prepare()
        except CycleError as error:
            raise ValueError("plan policy dependency graph contains a cycle") from error
        return self

    def canonical_json(self) -> str:
        """生成 PlanPolicy 的稳定 canonical JSON。

        :return: 返回用于策略摘要与版本审计的 JSON 字符串。
        """
        return canonical_plan_json(self.model_dump(mode="json"))


class PlanEnvelope(BaseModel):
    """表示确定性编译层生成的计划执行 envelope。

    :return: 无返回值；envelope 只承载执行范围，不承载医学语义。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    envelope_id: str = Field(
        min_length=1,
        max_length=240,
        description="系统生成的稳定 envelope 标识。",
    )
    kind: PlanEnvelopeKind = Field(
        description="envelope 的生产类型。",
    )
    parent_envelope_id: str | None = Field(
        default=None,
        description="父 envelope 标识；claim envelope 必须绑定 turn root。",
    )
    ordinal: int | None = Field(
        default=None,
        ge=0,
        description="同类型 envelope 的系统生成零基顺序。",
    )


class SchemaContractReference(BaseModel):
    """表示任务输出 schema 的权威引用。

    :return: 无返回值；完整 schema 保存在 SkillCatalog，模型不能声明该值。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    schema_id: str = Field(
        min_length=1,
        max_length=200,
        description="SkillCatalog 输出契约稳定标识。",
    )
    schema_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$",
        description="输出契约语义化版本。",
    )
    schema_digest: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="输出契约 canonical JSON 的 SHA-256 digest。",
    )

    @classmethod
    def from_contract(cls, contract: SchemaContract) -> SchemaContractReference:
        """从权威 SchemaContract 生成不可变引用。

        :param contract: SkillSpec 声明的输入或输出 schema 契约。
        :return: 返回包含身份与内容摘要的 schema 引用。
        """
        canonical = contract.canonical_json()
        return cls(
            schema_id=contract.schema_id,
            schema_version=contract.schema_version,
            schema_digest=sha256(canonical.encode("utf-8")).hexdigest(),
        )


class PlanTask(BaseModel):
    """表示确定性编译后进入 Plan IR 的单个可调度任务。

    :return: 无返回值；任务身份、schema 与依赖均由系统权威填充。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    task_id: str = Field(
        min_length=1,
        max_length=360,
        description="由回合、envelope 与 SKILL 身份派生的稳定任务标识。",
    )
    skill_id: str = Field(
        min_length=1,
        max_length=120,
        description="任务执行的已注册 SKILL 标识。",
    )
    skill_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$",
        description="任务执行的精确 SKILL 版本。",
    )
    target_envelope_id: str = Field(
        min_length=1,
        max_length=240,
        description="任务绑定的计划 envelope 标识。",
    )
    depends_on: tuple[str, ...] = Field(
        description="任务依赖的完整 task_id 集合。",
    )
    expected_output_schema: SchemaContractReference = Field(
        description="从 SkillCatalog 注入的权威输出 schema 引用。",
    )
    selection_source: PlanTaskSelectionSource = Field(
        description="任务由生产策略强制或模型选择产生的审计来源。",
    )


class PlanDependency(BaseModel):
    """表示 Plan IR 中一条 canonical 依赖边。

    :return: 无返回值；依赖边是调度前的可审计权威投影。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    task_id: str = Field(
        min_length=1,
        max_length=360,
        description="依赖发起任务的稳定标识。",
    )
    depends_on_task_id: str = Field(
        min_length=1,
        max_length=360,
        description="被依赖任务的稳定标识。",
    )


class PlanIR(BaseModel):
    """表示通过确定性编译生成的完整不可变 Turn Plan IR。

    :return: 无返回值；该对象仍需 PlanValidator 准入后才可进入调度。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    plan_id: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="权威计划内容的 canonical SHA-256 身份。",
    )
    plan_version: Literal["1.0.0"] = Field(
        description="Plan IR 契约版本。",
    )
    turn_id: str = Field(
        min_length=1,
        max_length=240,
        description="当前计划绑定的 TurnSnapshot 回合标识。",
    )
    snapshot_digest: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="当前计划绑定的 TurnSnapshot canonical digest。",
    )
    skill_catalog_digest: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="计划创建时绑定的 SkillCatalog 契约 digest。",
    )
    plan_policy_digest: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="计划创建时绑定的 PlanPolicy digest。",
    )
    envelopes: tuple[PlanEnvelope, ...] = Field(
        description="系统生成的 turn 与 claim envelope 集合。",
    )
    tasks: tuple[PlanTask, ...] = Field(
        description="系统展开后的完整任务集合。",
    )
    dependencies: tuple[PlanDependency, ...] = Field(
        description="系统展开后的 canonical 依赖边集合。",
    )

    def canonical_authoritative_json(self) -> str:
        """生成排除 plan_id 后的权威计划 canonical JSON。

        :return: 返回排序键、紧凑且保留 Unicode 的 JSON 字符串。
        """
        return canonical_plan_json(
            self.model_dump(
                mode="json",
                exclude={"plan_id"},
            ),
        )

    def plan_digest(self) -> str:
        """计算当前权威计划内容的 SHA-256 digest。

        :return: 返回与 plan_id 相同的 64 位小写 hex digest。
        """
        return compute_plan_digest(
            self.model_dump(
                mode="json",
                exclude={"plan_id"},
            ),
        )


class ValidatedPlan(BaseModel):
    """表示已通过 M03 全部准入门禁的计划消费门面。

    :return: 无返回值；M04 DAGScheduler 只能消费该对象。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    plan: PlanIR = Field(
        description="通过 schema、policy、context、依赖与身份校验的 Plan IR。",
    )


class PlanValidationFailure(BaseModel):
    """表示 Plan Validator 输出的单条稳定失败记录。

    :return: 无返回值；失败记录不包含用户原文或模型原始响应。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    code: PlanValidationFailureCode = Field(
        description="稳定计划准入失败编码。",
    )
    path: str = Field(
        min_length=1,
        max_length=360,
        description="失败所在的计划字段路径。",
    )
    message: str = Field(
        min_length=1,
        max_length=500,
        description="面向工程排障的稳定技术说明。",
    )
    task_id: str | None = Field(
        default=None,
        description="关联任务标识；无任务关联时为空。",
    )
    envelope_id: str | None = Field(
        default=None,
        description="关联 envelope 标识；无 envelope 关联时为空。",
    )


class PlanValidationResult(BaseModel):
    """表示 Plan Validator 的显式终态结果。

    :return: 无返回值；blocked 状态不得被转换成空计划或默认计划。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    validated_plan: ValidatedPlan | None = Field(
        description="全部校验通过时返回的消费门面，否则为 None。",
    )
    failures: tuple[PlanValidationFailure, ...] = Field(
        default=(),
        description="按稳定键排序的全部准入失败集合。",
    )


def canonical_plan_json(payload: dict[str, Any]) -> str:
    """生成计划契约使用的 canonical JSON。

    :param payload: 待序列化的权威计划字段字典。
    :return: 返回排序键、紧凑分隔且保留 Unicode 的 JSON 字符串。
    """
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def compute_plan_digest(payload: dict[str, Any]) -> str:
    """计算 Plan 权威字段的 SHA-256 digest。

    :param payload: 排除 plan_id 后的权威计划字段字典。
    :return: 返回 64 位小写 hex digest。
    """
    material = f"semantic-collaboration.plan.v1\n{canonical_plan_json(payload)}"
    return sha256(material.encode("utf-8")).hexdigest()


def compute_plan_policy_digest(policy: PlanPolicySpec) -> str:
    """计算 PlanPolicy 的带命名空间 SHA-256 digest。

    :param policy: 已冻结的生产计划策略。
    :return: 返回用于绑定计划与策略版本的 64 位小写 hex digest。
    """
    material = (
        "semantic-collaboration.plan-policy.v1\n"
        + policy.canonical_json()
    )
    return sha256(material.encode("utf-8")).hexdigest()


def schema_reference_matches(
    reference: SchemaContractReference,
    contract: SchemaContract,
) -> bool:
    """判断计划内 schema 引用是否与权威契约完全一致。

    :param reference: Plan Task 中系统注入的 schema 引用。
    :param contract: SkillCatalog 中的权威 schema 契约。
    :return: 身份、版本与内容摘要均一致时返回 True。
    """
    expected = SchemaContractReference.from_contract(contract)
    return reference == expected

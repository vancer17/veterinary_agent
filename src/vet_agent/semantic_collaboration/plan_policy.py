"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/plan_policy.py
作用：声明受限语义协作 DAG 的 M03 初始 Turn Plan 生产策略组合根。
范围：覆盖必选根任务、claim 级语义 lane、禁止初始修复任务和静态依赖规则。
说明：本文件只建立系统侧计划策略，不调用 LLM、不展开 Plan IR、不执行调度，
      也不把 review / repair / patch 开放给任务规划模型自由选择。
=============================================================================
"""

from __future__ import annotations

from .catalog import SkillRegistry
from .contracts import SkillSpec
from .plan_contracts import (
    PLAN_POLICY_VERSION,
    PlanDependencyRule,
    PlanDependencyScope,
    PlanEnvelopeKind,
    PlanPolicySpec,
    PlanSkillRequirementMode,
    PlanSkillRule,
)
from .production import (
    CANONICAL_DESCRIPTOR_SPEC,
    CLAIM_INVENTORY_SPEC,
    MEASUREMENT_PHRASE_SPEC,
    PARTICIPANT_PHRASE_SPEC,
    PATCH_APPLIER_SPEC,
    SEMANTIC_REPAIR_SPEC,
    SEMANTIC_REVIEW_SPEC,
    STATEMENT_SEMANTICS_SPEC,
    TEMPORAL_PHRASE_SPEC,
    TURN_INTENT_SPEC,
)

PRODUCTION_PLAN_POLICY_ID = "production-semantic-turn-generation"


def _root_dependency(skill_id: str) -> PlanDependencyRule:
    """构造指向 turn root 任务的依赖规则。

    :param skill_id: 被依赖 SKILL 的稳定标识。
    :return: 返回 root envelope 范围内的依赖规则。
    """
    return PlanDependencyRule(
        dependency_skill_id=skill_id,
        dependency_scope=PlanDependencyScope.ROOT_ENVELOPE,
    )


def _skill_rule(
    spec: SkillSpec,
    *,
    requirement: PlanSkillRequirementMode,
    target_envelope_kind: PlanEnvelopeKind,
    depends_on: tuple[PlanDependencyRule, ...] = (),
) -> PlanSkillRule:
    """从权威 SkillSpec 构造生产计划规则。

    :param spec: 生产 SkillCatalog 中的权威 SKILL 契约。
    :param requirement: SKILL 在初始 Turn Plan 中的选择模式。
    :param target_envelope_kind: SKILL 允许绑定的 envelope 类型。
    :param depends_on: 确定性依赖规则集合。
    :return: 返回通过契约校验的计划规则。
    """
    return PlanSkillRule(
        skill_id=spec.skill_id,
        skill_version=spec.skill_version,
        target_envelope_kind=target_envelope_kind,
        requirement=requirement,
        depends_on=depends_on,
    )


def build_production_plan_policy(registry: SkillRegistry) -> PlanPolicySpec:
    """构建并校验当前生产环境的初始 Turn Plan 策略。

    :param registry: 已冻结的 SkillCatalog 只读门面。
    :return: 返回绑定精确 SKILL 版本的生产 PlanPolicy。
    :raises Exception: 任一策略引用的 SKILL 或版本缺失时按启动错误失败。
    """
    rules = (
        _skill_rule(
            TURN_INTENT_SPEC,
            requirement=PlanSkillRequirementMode.ALWAYS,
            target_envelope_kind=PlanEnvelopeKind.TURN,
        ),
        _skill_rule(
            CLAIM_INVENTORY_SPEC,
            requirement=PlanSkillRequirementMode.ALWAYS,
            target_envelope_kind=PlanEnvelopeKind.TURN,
            depends_on=(_root_dependency(TURN_INTENT_SPEC.skill_id),),
        ),
        _skill_rule(
            STATEMENT_SEMANTICS_SPEC,
            requirement=PlanSkillRequirementMode.WHEN_CLAIM_ENVELOPE_PRESENT,
            target_envelope_kind=PlanEnvelopeKind.CLAIM,
            depends_on=(
                _root_dependency(CLAIM_INVENTORY_SPEC.skill_id),
            ),
        ),
        _skill_rule(
            PARTICIPANT_PHRASE_SPEC,
            requirement=PlanSkillRequirementMode.OPTIONAL,
            target_envelope_kind=PlanEnvelopeKind.CLAIM,
            depends_on=(
                _root_dependency(CLAIM_INVENTORY_SPEC.skill_id),
            ),
        ),
        _skill_rule(
            TEMPORAL_PHRASE_SPEC,
            requirement=PlanSkillRequirementMode.OPTIONAL,
            target_envelope_kind=PlanEnvelopeKind.CLAIM,
            depends_on=(
                _root_dependency(CLAIM_INVENTORY_SPEC.skill_id),
            ),
        ),
        _skill_rule(
            MEASUREMENT_PHRASE_SPEC,
            requirement=PlanSkillRequirementMode.OPTIONAL,
            target_envelope_kind=PlanEnvelopeKind.CLAIM,
            depends_on=(
                _root_dependency(CLAIM_INVENTORY_SPEC.skill_id),
            ),
        ),
        _skill_rule(
            CANONICAL_DESCRIPTOR_SPEC,
            requirement=PlanSkillRequirementMode.OPTIONAL,
            target_envelope_kind=PlanEnvelopeKind.CLAIM,
            depends_on=(
                _root_dependency(CLAIM_INVENTORY_SPEC.skill_id),
            ),
        ),
        _skill_rule(
            SEMANTIC_REVIEW_SPEC,
            requirement=PlanSkillRequirementMode.FORBIDDEN,
            target_envelope_kind=PlanEnvelopeKind.CLAIM,
        ),
        _skill_rule(
            SEMANTIC_REPAIR_SPEC,
            requirement=PlanSkillRequirementMode.FORBIDDEN,
            target_envelope_kind=PlanEnvelopeKind.CLAIM,
        ),
        _skill_rule(
            PATCH_APPLIER_SPEC,
            requirement=PlanSkillRequirementMode.FORBIDDEN,
            target_envelope_kind=PlanEnvelopeKind.CLAIM,
        ),
    )
    policy = PlanPolicySpec(
        policy_id=PRODUCTION_PLAN_POLICY_ID,
        policy_version=PLAN_POLICY_VERSION,
        max_claim_envelope_count=8,
        max_task_count=42,
        skills=rules,
    )
    for rule in policy.skills:
        registry.require(rule.skill_id, rule.skill_version)
    return policy

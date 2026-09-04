"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/plan_policy.py
作用：声明受限语义协作 DAG 的 M03 初始 Turn Plan 生产策略组合根。
范围：覆盖必选并行根任务、禁止初始修复任务和静态依赖规则。
说明：本文件只建立系统侧计划策略，不调用 LLM、不展开 Plan IR、不执行调度，
      不预估 claim 数量，也不把 review / repair / patch 开放给模型选择。
=============================================================================
"""

from __future__ import annotations

from .catalog import SkillRegistry
from .contracts import SkillSpec
from .plan_contracts import (
    PLAN_POLICY_VERSION,
    PlanDependencyRule,
    PlanEnvelopeKind,
    PlanPolicySpec,
    PlanSkillRequirementMode,
    PlanSkillRule,
)
from .production import (
    CLAIM_COVERAGE_REVIEW_SPEC,
    CLAIM_FAITHFULNESS_REVIEW_SPEC,
    CLAIM_INVENTORY_REPAIR_SPEC,
    CLAIM_INVENTORY_SPEC,
    CLAIM_PROPOSITION_REPAIR_SPEC,
    PATCH_APPLIER_SPEC,
    TURN_INTENT_SPEC,
)

PRODUCTION_PLAN_POLICY_ID = "production-semantic-turn-generation"


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
        ),
        _skill_rule(
            CLAIM_COVERAGE_REVIEW_SPEC,
            requirement=PlanSkillRequirementMode.FORBIDDEN,
            target_envelope_kind=PlanEnvelopeKind.TURN,
        ),
        _skill_rule(
            CLAIM_FAITHFULNESS_REVIEW_SPEC,
            requirement=PlanSkillRequirementMode.FORBIDDEN,
            target_envelope_kind=PlanEnvelopeKind.CLAIM,
        ),
        _skill_rule(
            CLAIM_INVENTORY_REPAIR_SPEC,
            requirement=PlanSkillRequirementMode.FORBIDDEN,
            target_envelope_kind=PlanEnvelopeKind.TURN,
        ),
        _skill_rule(
            CLAIM_PROPOSITION_REPAIR_SPEC,
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
        max_task_count=2,
        skills=rules,
    )
    for rule in policy.skills:
        registry.require(rule.skill_id, rule.skill_version)
    return policy

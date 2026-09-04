"""
=============================================================================
文件：tests/test_semantic_collaboration_repair_planner.py
作用：验证受限语义协作 DAG M09 Repair Planner 的生产契约与路由。
范围：覆盖 Coverage / Faithfulness 通用修复 lane、clarification gap 透传、
      未分类问题阻断、disagreement、inventory 修复优先、预算、计划验证
      与 M11 TODO 绑定空壳。
说明：本测试复用 M08 进程内测试替身，不访问 LiteLLM、Temporal、OPA、
      PostgreSQL 或任何 input_preprocessing 历史 experiment runner。
=============================================================================
"""

from __future__ import annotations

import asyncio

from tests.test_semantic_collaboration_review import (
    SequentialReviewTransport,
    _empty_coverage_payload,
    _empty_faithfulness_payload,
    _faithfulness_payload,
    _review_runner,
    _snapshot,
    _source_proposal,
)
from vet_agent.semantic_collaboration import (
    ClaimFaithfulnessSkipReason,
    GenerationVerificationState,
    SemanticGenerationVerifier,
    SemanticRepairHumanReviewReason,
    SemanticRepairLane,
    SemanticRepairPlanFailureCode,
    SemanticRepairPlanner,
    SemanticRepairPlanRoute,
    SemanticRepairPlanVerificationState,
    SemanticRepairPlanVerifier,
    SemanticReviewBundle,
    SemanticReviewOutcome,
    build_production_skill_catalog,
)


def _bundle(
    payloads: tuple[dict[str, object], ...],
) -> SemanticReviewBundle:
    """构造已完成 M08 审查与确定性派生的测试聚合。

    :param payloads: 依次包含 Claim Inventory、Coverage 与 Faithfulness 的模型 payload。
    :return: 返回 M08 Runner 生成的 Review Bundle。
    """
    snapshot = _snapshot()
    transport = SequentialReviewTransport(payloads=payloads)
    proposal = _source_proposal(snapshot, transport)
    verification = SemanticGenerationVerifier().verify(proposal)
    assert verification.state is GenerationVerificationState.ACCEPTED
    return asyncio.run(
        _review_runner(snapshot, transport).review(proposal, verification),
    )


def _planner() -> SemanticRepairPlanner:
    """构造绑定生产 SkillCatalog 的 M09 修复规划器。

    :return: 返回使用默认预算策略的确定性 Repair Planner。
    """
    catalog = build_production_skill_catalog()
    return SemanticRepairPlanner(registry=catalog.registry())


def _supported_bundle() -> SemanticReviewBundle:
    """构造全部审查矩阵为 false 的 M08 聚合。

    :return: 返回 semantic_review_supported 的 Review Bundle。
    """
    return _bundle(
        (
            {"claims": ["英短没有呕吐"]},
            _empty_coverage_payload(),
            _empty_faithfulness_payload(),
        ),
    )


def test_supported_review_requires_no_repair() -> None:
    """验证全部 false 的 M08 聚合不会生成修复任务。

    :return: 无返回值。
    """
    plan = _planner().plan(_supported_bundle())

    assert plan.route is SemanticRepairPlanRoute.NO_REPAIR_REQUIRED
    assert plan.repair_tasks == ()
    assert plan.active_clarification_gaps == ()
    assert plan.human_review_reasons == ()


def test_known_coverage_dimensions_use_single_inventory_repair_lane() -> None:
    """验证 Coverage 已知问题统一进入通用 inventory 修复 lane。

    :return: 无返回值。
    """
    coverage_payload = _empty_coverage_payload()
    coverage_matrix = coverage_payload["coverage_matrix"]
    if isinstance(coverage_matrix, dict):
        coverage_matrix["存在漏抽显式事实"] = True
        coverage_matrix["存在多事实合并"] = True
    coverage_payload["missing_claim_candidates"] = [  # type: ignore[assignment]
        "英短没有呕吐",
        "英短精神状态良好",
    ]
    bundle = _bundle(
        (
            {"claims": ["英短大便偏软"]},
            coverage_payload,
        ),
    )

    plan = _planner().plan(bundle)
    task = plan.repair_tasks[0]

    assert bundle.aggregate_outcome is SemanticReviewOutcome.REPAIR_REQUIRED
    assert plan.route is SemanticRepairPlanRoute.REPAIR_REQUIRED
    assert len(plan.repair_tasks) == 1
    assert task.repair_lane is SemanticRepairLane.CLAIM_INVENTORY_REPAIR
    assert task.repair_skill_id == "claim_inventory_repair"
    assert task.target_claim_index is None
    assert task.repair_hints == ("英短没有呕吐", "英短精神状态良好")
    assert task.review_dimensions[0].value == "存在漏抽显式事实"
    assert all(
        record.skip_reason is ClaimFaithfulnessSkipReason.INVENTORY_REPAIR_REQUIRED
        for record in bundle.faithfulness_reviews
    )


def test_known_faithfulness_dimensions_use_proposition_repair_lane() -> None:
    """验证单条 claim 的已知漂移统一进入通用 proposition 修复 lane。

    :return: 无返回值。
    """
    bundle = _bundle(
        (
            {"claims": ["英短精神状态被否认"]},
            _empty_coverage_payload(),
            _faithfulness_payload(正常状态误写为否认=True),
        ),
    )

    plan = _planner().plan(bundle)
    task = plan.repair_tasks[0]

    assert bundle.aggregate_outcome is SemanticReviewOutcome.REPAIR_REQUIRED
    assert plan.route is SemanticRepairPlanRoute.REPAIR_REQUIRED
    assert len(plan.repair_tasks) == 1
    assert task.repair_lane is SemanticRepairLane.CLAIM_PROPOSITION_REPAIR
    assert task.target_claim_index == 0
    assert task.target_claim_proposition == "英短精神状态被否认"
    assert task.review_dimensions[0].value == "正常状态误写为否认"
    assert task.repair_hints == ()


def test_source_binding_gap_does_not_enter_repair() -> None:
    """验证来源绑定缺失只透传 clarification gap。

    :return: 无返回值。
    """
    bundle = _bundle(
        (
            {"claims": ["它的精神状态不明"]},
            _empty_coverage_payload(),
            _faithfulness_payload(指代对象不明=True),
        ),
    )

    plan = _planner().plan(bundle)

    assert bundle.aggregate_outcome is (SemanticReviewOutcome.CLARIFICATION_REQUIRED)
    assert plan.route is SemanticRepairPlanRoute.CLARIFICATION_REQUIRED
    assert plan.repair_tasks == ()
    assert plan.active_clarification_gaps[0].claim_proposition == ("它的精神状态不明")


def test_repair_then_clarification_preserves_gap() -> None:
    """验证漂移修复与来源绑定缺失会形成组合路由。

    :return: 无返回值。
    """
    bundle = _bundle(
        (
            {"claims": ["英短精神状态被否认"]},
            _empty_coverage_payload(),
            _faithfulness_payload(
                正常状态误写为否认=True,
                指代对象不明=True,
            ),
        ),
    )

    plan = _planner().plan(bundle)

    assert bundle.aggregate_outcome is (
        SemanticReviewOutcome.REPAIR_THEN_CLARIFICATION_REQUIRED
    )
    assert plan.route is (SemanticRepairPlanRoute.REPAIR_THEN_CLARIFICATION_REQUIRED)
    assert len(plan.repair_tasks) == 1
    assert len(plan.active_clarification_gaps) == 1
    assert plan.active_clarification_gaps[0].model_overreach_repaired is False


def test_unclassified_coverage_issue_requires_human_review() -> None:
    """验证未分类覆盖问题不会被通用 Repair LLM 自动解释。

    :return: 无返回值。
    """
    coverage_payload = _empty_coverage_payload()
    coverage_matrix = coverage_payload["coverage_matrix"]
    if isinstance(coverage_matrix, dict):
        coverage_matrix["未分类覆盖问题"] = True
    bundle = _bundle(
        (
            {"claims": ["英短没有呕吐"]},
            coverage_payload,
        ),
    )

    plan = _planner().plan(bundle)

    assert plan.route is SemanticRepairPlanRoute.HUMAN_REVIEW_REQUIRED
    assert plan.repair_tasks == ()
    assert plan.human_review_reasons == (
        SemanticRepairHumanReviewReason.UNCLASSIFIED_COVERAGE_ISSUE,
    )


def test_unclassified_semantic_change_requires_human_review() -> None:
    """验证未分类语义漂移不会被通用 Repair LLM 自动重写。

    :return: 无返回值。
    """
    bundle = _bundle(
        (
            {"claims": ["英短没有呕吐"]},
            _empty_coverage_payload(),
            _faithfulness_payload(未分类语义改变=True),
        ),
    )

    plan = _planner().plan(bundle)

    assert plan.route is SemanticRepairPlanRoute.HUMAN_REVIEW_REQUIRED
    assert plan.repair_tasks == ()
    assert plan.human_review_reasons == (
        SemanticRepairHumanReviewReason.UNCLASSIFIED_SEMANTIC_CHANGE,
    )


def test_review_disagreement_does_not_select_repair() -> None:
    """验证 Coverage 与 Faithfulness 冲突时不默认任一方正确。

    :return: 无返回值。
    """
    coverage_payload = _empty_coverage_payload()
    coverage_matrix = coverage_payload["coverage_matrix"]
    if isinstance(coverage_matrix, dict):
        coverage_matrix["存在原文不支持的claim"] = True
    bundle = _bundle(
        (
            {"claims": ["英短没有呕吐"]},
            coverage_payload,
            _empty_faithfulness_payload(),
        ),
    )

    plan = _planner().plan(bundle)

    assert plan.route is SemanticRepairPlanRoute.DISAGREEMENT
    assert plan.repair_tasks == ()
    assert plan.human_review_reasons == (
        SemanticRepairHumanReviewReason.REVIEW_DISAGREEMENT,
    )


def test_inventory_repair_suppresses_stale_proposition_repairs() -> None:
    """验证 inventory 修复优先并显式保留被抑制的 claim 修复目标。

    :return: 无返回值。
    """
    coverage_payload = _empty_coverage_payload()
    coverage_matrix = coverage_payload["coverage_matrix"]
    if isinstance(coverage_matrix, dict):
        coverage_matrix["存在原文不支持的claim"] = True
    bundle = _bundle(
        (
            {"claims": ["英短推断呕吐", "英短精神状态被否认"]},
            coverage_payload,
            _faithfulness_payload(医学推断或建议添加=True),
            _faithfulness_payload(正常状态误写为否认=True),
        ),
    )

    plan = _planner().plan(bundle)

    assert plan.route is SemanticRepairPlanRoute.REPAIR_REQUIRED
    assert len(plan.repair_tasks) == 1
    assert plan.repair_tasks[0].repair_lane is (
        SemanticRepairLane.CLAIM_INVENTORY_REPAIR
    )
    assert len(plan.suppressed_claim_repairs) == 2
    assert len(plan.stale_review_task_ids) == 2
    assert all(item.repair_dimensions for item in plan.suppressed_claim_repairs)


def test_claim_repair_budget_exceeds_human_review() -> None:
    """验证超过单 claim 修复任务预算时不会全局重写。

    :return: 无返回值。
    """
    bundle = _bundle(
        (
            {
                "claims": [
                    "英短精神状态被否认一",
                    "英短精神状态被否认二",
                    "英短精神状态被否认三",
                ],
            },
            _empty_coverage_payload(),
            _faithfulness_payload(正常状态误写为否认=True),
            _faithfulness_payload(正常状态误写为否认=True),
            _faithfulness_payload(正常状态误写为否认=True),
        ),
    )

    plan = _planner().plan(bundle)

    assert plan.route is SemanticRepairPlanRoute.HUMAN_REVIEW_REQUIRED
    assert plan.repair_tasks == ()
    assert plan.human_review_reasons == (
        SemanticRepairHumanReviewReason.REPAIR_BUDGET_EXCEEDED,
    )


def test_repair_plan_is_deterministic_and_verifier_blocks_mutation() -> None:
    """验证修复计划可复算且计划漂移无法进入 M10。

    :return: 无返回值。
    """
    bundle = _bundle(
        (
            {"claims": ["英短精神状态被否认"]},
            _empty_coverage_payload(),
            _faithfulness_payload(正常状态误写为否认=True),
        ),
    )
    catalog = build_production_skill_catalog()
    planner = SemanticRepairPlanner(registry=catalog.registry())
    verifier = SemanticRepairPlanVerifier(registry=catalog.registry())
    plan = planner.plan(bundle)
    same_plan = planner.plan(bundle)
    mutated_plan = plan.model_copy(
        update={
            "human_review_reasons": (
                SemanticRepairHumanReviewReason.HUMAN_REVIEW_REQUESTED,
            ),
        },
    )

    assert plan == same_plan
    assert plan.plan_id == same_plan.plan_id
    assert verifier.verify(plan, bundle).state is (
        SemanticRepairPlanVerificationState.ACCEPTED
    )
    mutation_result = verifier.verify(mutated_plan, bundle)
    assert mutation_result.state is SemanticRepairPlanVerificationState.BLOCKED
    assert mutation_result.failure_code is (
        SemanticRepairPlanFailureCode.PLAN_PAYLOAD_MISMATCH
    )


def test_repair_plan_verifier_blocks_identity_mismatch() -> None:
    """验证身份漂移的修复计划在复算前即被阻断。

    :return: 无返回值。
    """
    bundle = _bundle(
        (
            {"claims": ["英短没有呕吐"]},
            _empty_coverage_payload(),
            _empty_faithfulness_payload(),
        ),
    )
    catalog = build_production_skill_catalog()
    verifier = SemanticRepairPlanVerifier(registry=catalog.registry())
    plan = verifier.planner.plan(bundle)
    mutated_plan = plan.model_copy(update={"run_id": "another-run"})

    result = verifier.verify(mutated_plan, bundle)

    assert result.state is SemanticRepairPlanVerificationState.BLOCKED
    assert result.failure_code is SemanticRepairPlanFailureCode.IDENTITY_MISMATCH

"""
文件：tests/integration/semantic_collaboration/__init__.py
作用：作为受限语义协作 DAG Pre-M11 集成测试组件的稳定测试包入口。
范围：暴露测试配置、fixture、测试-only 端口、生产 runner 组合辅助与报告写入器。
说明：本包只允许 tests/integration 使用，不得被 src 生产包导入；跨包测试代码
      应通过本入口取用能力，不直接引用内部模块。
"""

from .contracts import (
    ExternalSemanticTestConfig,
    SemanticCaseFixture,
    SemanticFixtureFile,
    SemanticIntegrationReportWriter,
    load_external_semantic_test_config,
    load_semantic_fixture_file,
    require_semantic_fixture,
)
from .harness import (
    FixedPayloadTransport,
    FixedRepairTargetSnapshotResolver,
    FixedReviewPlanCase,
    GenerationRunResult,
    RepairTestCase,
    SchedulerExecutorMode,
    SchedulerOnlySemanticTaskExecutor,
    StaticHistoryReader,
    StaticPetContextReader,
    StaticPriorFactReader,
    StaticTurnSnapshotReader,
    build_claim_inventory_proposal,
    build_fixed_review_plan_case,
    build_plan,
    build_qwen_transport,
    build_real_repair_case,
    build_real_review_bundle,
    build_repair_runner,
    build_repair_target_snapshot,
    build_review_runner,
    build_snapshot,
    run_generation,
)

__all__ = [
    "ExternalSemanticTestConfig",
    "FixedPayloadTransport",
    "FixedRepairTargetSnapshotResolver",
    "FixedReviewPlanCase",
    "GenerationRunResult",
    "RepairTestCase",
    "SchedulerExecutorMode",
    "SchedulerOnlySemanticTaskExecutor",
    "SemanticCaseFixture",
    "SemanticFixtureFile",
    "SemanticIntegrationReportWriter",
    "StaticHistoryReader",
    "StaticPetContextReader",
    "StaticPriorFactReader",
    "StaticTurnSnapshotReader",
    "build_claim_inventory_proposal",
    "build_fixed_review_plan_case",
    "build_plan",
    "build_qwen_transport",
    "build_real_repair_case",
    "build_real_review_bundle",
    "build_repair_runner",
    "build_repair_target_snapshot",
    "build_review_runner",
    "build_snapshot",
    "load_external_semantic_test_config",
    "load_semantic_fixture_file",
    "require_semantic_fixture",
    "run_generation",
]

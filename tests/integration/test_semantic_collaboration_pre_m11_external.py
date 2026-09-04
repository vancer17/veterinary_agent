"""
=============================================================================
文件：tests/integration/test_semantic_collaboration_pre_m11_external.py
作用：通过真实 LiteLLM、Temporal 与 PostgreSQL 验证受限语义协作 DAG M02～M10。
范围：覆盖外部依赖健康、Root Plan、真实生成与结构验证、M08 语义校准、
      M09 路由、M10 typed patch preview、M04 workflow / activity、投影仓储、
      TODO Fail Fast 与领域隔离。
说明：本测试仅在 RUN_SEMANTIC_COLLABORATION_EXTERNAL_TEST 显式开启时执行；
      M11 未实现，因此不得生成权威 artifact 或 repair_verified。
=============================================================================
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from temporalio.exceptions import WorkflowAlreadyStartedError

from tests.integration.semantic_collaboration import (
    ExternalSemanticTestConfig,
    FixedReviewPlanCase,
    GenerationRunResult,
    RepairTestCase,
    SchedulerExecutorMode,
    SchedulerOnlySemanticTaskExecutor,
    SemanticCaseFixture,
    SemanticFixtureFile,
    SemanticIntegrationReportWriter,
    build_claim_inventory_proposal,
    build_fixed_review_plan_case,
    build_plan,
    build_qwen_transport,
    build_real_repair_case,
    build_real_review_bundle,
    build_repair_runner,
    build_review_runner,
    load_external_semantic_test_config,
    load_semantic_fixture_file,
    require_semantic_fixture,
    run_generation,
)
from vet_agent.db import make_engine, make_session_factory
from vet_agent.semantic_collaboration import (
    DAGExecutionPolicy,
    DAGRunProjectionInitializeRequest,
    DAGRunStatus,
    DAGTaskExecutionResult,
    DAGTaskTerminalState,
    GenerationVerificationState,
    InMemorySemanticDAGProjectionRepository,
    PatchApplicationState,
    PostgresSemanticDAGProjectionRepository,
    SemanticGenerationVerificationResult,
    SemanticGenerationVerifier,
    SemanticModelProposal,
    SemanticRepairExecutionResult,
    SemanticRepairExecutionState,
    SemanticRepairLane,
    SemanticRepairPlanRoute,
    SemanticReviewBundle,
    SkillRegistry,
    TemporalSemanticDAGScheduler,
    TODORepairPatchStore,
    TODORepairTargetSnapshotResolver,
    TODOSemanticTaskExecutor,
    TODOTurnSnapshotReader,
    build_dag_task_policies,
    build_production_skill_catalog,
    build_temporal_semantic_dag_worker,
    connect_temporal_semantic_client,
    semantic_dag_run_id,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORT_DIRECTORY = (
    PROJECT_ROOT
    / ".data"
    / "evaluations"
    / "semantic-collaboration-integration"
)

pytestmark = pytest.mark.integration
FIXTURE_DIRECTORY = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "semantic-collaboration"
)


def _external_test_enabled() -> bool:
    """读取 Pre-M11 外部集成测试显式开关。

    :return: 开关为真值时返回 True。
    """
    return os.getenv(
        "RUN_SEMANTIC_COLLABORATION_EXTERNAL_TEST",
        "",
    ).lower() in {"1", "true", "yes", "on"}


def _semantic_test_enabled() -> bool:
    """读取真实模型语义验证显式开关。

    :return: 开关为真值时返回 True。
    """
    return os.getenv(
        "RUN_SEMANTIC_COLLABORATION_SEMANTIC_TEST",
        "",
    ).lower() in {"1", "true", "yes", "on"}


@pytest.fixture(scope="module")
def report_writer(
    external_config: ExternalSemanticTestConfig,
) -> Iterator[SemanticIntegrationReportWriter]:
    """创建模块级 Pre-M11 集成测试报告写入器。

    :return: 返回脱敏 JSON 报告写入器。
    """
    if not _external_test_enabled():
        pytest.skip("未开启 RUN_SEMANTIC_COLLABORATION_EXTERNAL_TEST")
    mode = "semantic" if _semantic_test_enabled() else "external"
    writer = SemanticIntegrationReportWriter(
        execution_mode=mode,
        config=external_config,
    )
    yield writer
    output_path = writer.finalize(output_directory=REPORT_DIRECTORY)
    print(f"\nsemantic pre-M11 integration report: {output_path}")


@pytest.fixture(scope="module")
def external_config() -> ExternalSemanticTestConfig:
    """加载外部语义集成测试配置。

    :return: 返回真实 LiteLLM、Temporal 与 PostgreSQL 配置。
    """
    if not _external_test_enabled():
        pytest.skip("未开启 RUN_SEMANTIC_COLLABORATION_EXTERNAL_TEST")
    return load_external_semantic_test_config()


@pytest.fixture(scope="module")
def semantic_fixture_file() -> SemanticFixtureFile:
    """装载人工审核语义集成测试 fixture。

    :return: 返回严格校验后的 fixture 文件对象。
    """
    return load_semantic_fixture_file(
        FIXTURE_DIRECTORY / "semantic-regression-v1.json",
    )


@pytest.fixture(scope="module")
def engineering_fixture_file() -> SemanticFixtureFile:
    """装载工程链路集成测试 fixture。

    :return: 返回严格校验后的 fixture 文件对象。
    """
    return load_semantic_fixture_file(
        FIXTURE_DIRECTORY / "engineering-turns-v1.json",
    )


def _true_dimensions(
    bundle: SemanticReviewBundle,
) -> set[str]:
    """提取 M08 Review Bundle 中的全部 true dimension。

    :param bundle: M08 确定性审查聚合。
    :return: 返回 Coverage 与 Faithfulness 的中文维度字符串集合。
    """
    dimensions = {
        dimension.value
        for dimension in (
            bundle.coverage_review.derived.true_dimensions
            if bundle.coverage_review.derived is not None
            else ()
        )
    }
    for record in bundle.faithfulness_reviews:
        if record.derived is not None:
            dimensions.update(
                dimension.value
                for dimension in record.derived.true_dimensions
            )
    return dimensions


def _assert_dimension_expectations(
    fixture: SemanticCaseFixture,
    dimensions: set[str],
) -> None:
    """校验 fixture 声明的 M08 语义维度预期。

    :param fixture: 人工审核测试用例。
    :param dimensions: 实际 M08 true dimension 集合。
    :return: 无返回值。
    :raises AssertionError: 必需维度缺失、任一维度缺失或禁止维度出现时抛出。
    """
    if fixture.expected_dimensions:
        missing = set(fixture.expected_dimensions) - dimensions
        assert not missing, f"expected M08 dimensions are missing: {missing}"
    if fixture.expected_any_dimensions:
        present = set(fixture.expected_any_dimensions) & dimensions
        assert present, (
            "none of the expected M08 dimensions appeared: "
            f"{fixture.expected_any_dimensions}"
        )
    forbidden = set(fixture.forbidden_dimensions) & dimensions
    assert not forbidden, f"forbidden M08 dimensions appeared: {forbidden}"


def _record_generation(
    record: dict[str, object],
    result: GenerationRunResult,
) -> None:
    """将真实 M06/M07 结果写入测试报告记录。

    :param record: 当前 case 的报告记录。
    :param result: M06 生成与 M07 验证组合结果。
    :return: 无返回值。
    """
    record["turn_snapshot_digest"] = result.snapshot.context_digest
    record["plan_id"] = result.validated_plan.plan.plan_id
    record["task_id"] = result.claim_proposal.execution.task.task_id
    record["skill_id"] = result.claim_proposal.metadata.skill_id
    record["skill_version"] = result.claim_proposal.metadata.skill_version
    record["prompt_hash"] = result.claim_proposal.metadata.prompt_hash
    record["proposal_digest"] = result.claim_proposal.proposal_digest
    record["requested_model"] = result.claim_proposal.metadata.requested_model
    record["response_model"] = result.claim_proposal.metadata.response_model
    record["response_id"] = result.claim_proposal.metadata.response_id
    record["finish_reason"] = result.claim_proposal.metadata.finish_reason
    record["latency_ms"] = result.claim_proposal.metadata.latency_ms
    record["prompt_tokens"] = result.claim_proposal.metadata.prompt_tokens
    record["completion_tokens"] = result.claim_proposal.metadata.completion_tokens
    record["total_tokens"] = result.claim_proposal.metadata.total_tokens
    record["usage_available"] = result.claim_proposal.metadata.usage_available
    record["m07_state"] = result.claim_verification.state.value


def _record_review(
    record: dict[str, object],
    bundle: SemanticReviewBundle,
) -> None:
    """将 M08 审查结果写入测试报告记录。

    :param record: 当前 case 的报告记录。
    :param bundle: M08 Review Bundle。
    :return: 无返回值。
    """
    record["review_outcome"] = bundle.aggregate_outcome.value
    record["true_dimensions"] = sorted(_true_dimensions(bundle))


def _record_repair(
    record: dict[str, object],
    result: SemanticRepairExecutionResult,
) -> None:
    """将 M10 patch preview 结果写入测试报告记录。

    :param record: 当前 case 的报告记录。
    :param result: M10 修复执行结果。
    :return: 无返回值。
    """
    record["patch_state"] = result.state.value
    if result.preview is not None:
        record["preview_claim_count"] = len(result.preview.claims)


def test_env_001_external_dependencies_are_ready(
    report_writer: SemanticIntegrationReportWriter,
    external_config: ExternalSemanticTestConfig,
) -> None:
    """验证 LiteLLM、Temporal 与 PostgreSQL 外部依赖可用。

    :param report_writer: 集成测试报告写入器。
    :param external_config: 外部集成测试配置。
    :return: 无返回值。
    """
    with report_writer.case(
        case_id="ENV-001",
        lane="environment",
        priority="P0",
    ):
        response = httpx.get(
            f"{external_config.litellm_base_url}/models",
            headers={
                "Authorization": f"Bearer {external_config.litellm_api_key}",
            },
            timeout=15,
        )
        response.raise_for_status()
        models = response.json()
        assert external_config.model in {
            item.get("id")
            for item in models.get("data", [])
        }
        asyncio.run(
            connect_temporal_semantic_client(
                external_config.temporal_address,
                namespace=external_config.temporal_namespace,
            ),
        )
        engine = make_engine(external_config.database_url)
        with engine.connect() as connection:
            assert connection.scalar(text("select 1")) == 1
        engine.dispose()
        report_writer.mark_environment_ready(
            litellm=True,
            temporal=True,
            postgres=True,
        )


def test_eng_001_contract_composition_builds_valid_root_plan(
    report_writer: SemanticIntegrationReportWriter,
) -> None:
    """验证 M02 TurnSnapshot 与 M03 Root Plan 契约组合。

    :param report_writer: 集成测试报告写入器。
    :return: 无返回值。
    """
    with report_writer.case(
        case_id="ENG-001",
        lane="contract_composition",
        priority="P0",
    ):
        snapshot, validated_plan = asyncio.run(
            build_plan(
                "我家英短精神正常，没有呕吐。",
                identity_suffix=uuid4().hex,
            ),
        )
        assert snapshot.context_digest == validated_plan.plan.snapshot_digest
        assert validated_plan.plan.turn_id == snapshot.turn_id
        assert {
            task.skill_id
            for task in validated_plan.plan.tasks
        } == {"turn_intent", "claim_inventory"}
        record = report_writer.last_record()
        record["turn_snapshot_digest"] = snapshot.context_digest
        record["plan_id"] = validated_plan.plan.plan_id


def test_eng_011_real_litellm_generation_and_verifier_close(
    report_writer: SemanticIntegrationReportWriter,
    external_config: ExternalSemanticTestConfig,
    engineering_fixture_file: SemanticFixtureFile,
) -> None:
    """验证真实 LiteLLM 下 M06 输出与 M07 结构验证可闭合。

    :param report_writer: 集成测试报告写入器。
    :param external_config: 外部集成测试配置。
    :param engineering_fixture_file: 工程链路集成测试 fixture。
    :return: 无返回值。
    """
    fixture = require_semantic_fixture(
        engineering_fixture_file,
        "ENG-011",
    )
    with report_writer.case(
        case_id="ENG-011",
        lane="real_litellm",
        priority="P0",
    ):
        result = asyncio.run(
            run_generation(
                fixture.current_turn,
                config=external_config,
                identity_suffix=uuid4().hex,
            ),
        )
        assert result.intent_verification.state is GenerationVerificationState.ACCEPTED
        assert result.claim_verification.state is GenerationVerificationState.ACCEPTED
        assert result.intent_proposal.metadata.requested_model == external_config.model
        assert result.claim_proposal.metadata.requested_model == external_config.model
        assert result.intent_proposal.metadata.finish_reason in {None, "stop"}
        assert result.claim_proposal.metadata.finish_reason in {None, "stop"}
        assert result.intent_proposal.metadata.prompt_hash
        assert result.claim_proposal.metadata.prompt_hash
        _record_generation(report_writer.last_record(), result)


def build_production_registry() -> SkillRegistry:
    """读取生产 SkillRegistry 的测试辅助别名。

    :return: 返回已冻结的生产 SkillRegistry。
    """
    return build_production_skill_catalog().registry()


async def _run_temporal_workflow(
    config: ExternalSemanticTestConfig,
) -> tuple[str, str]:
    """执行一次 scheduler-only Temporal workflow 并返回身份。

    :param config: 外部集成测试配置。
    :return: 返回 run_id 与 workflow 终态。
    :raises AssertionError: workflow 任务终态不完整时抛出。
    """
    _snapshot, validated_plan = await build_plan(
        "我家英短精神正常，没有呕吐。",
        identity_suffix=uuid4().hex,
    )
    repository = InMemorySemanticDAGProjectionRepository()
    client = await connect_temporal_semantic_client(
        config.temporal_address,
        namespace=config.temporal_namespace,
    )
    worker = build_temporal_semantic_dag_worker(
        client=client,
        task_queue=config.temporal_task_queue,
        repository=repository,
        executor=SchedulerOnlySemanticTaskExecutor(
            mode=SchedulerExecutorMode.VERIFIED_TEST_RESULT,
        ),
    )
    scheduler = TemporalSemanticDAGScheduler(
        client=client,
        task_queue=config.temporal_task_queue,
        registry=build_production_registry(),
    )
    async with worker:
        handle = await scheduler.start(
            validated_plan,
            DAGExecutionPolicy(
                task_timeout_seconds=10,
                run_timeout_seconds=30,
            ),
        )
        projection = await handle.result(timeout_seconds=25)
        with pytest.raises(WorkflowAlreadyStartedError):
            await scheduler.start(validated_plan)
    assert projection.status is DAGRunStatus.COMPLETED
    assert all(
        task.terminal_state is DAGTaskTerminalState.VERIFIED
        for task in projection.tasks
    )
    return validated_plan.plan.plan_id, projection.status.value


def test_eng_023_temporal_workflow_completes_all_tasks(
    report_writer: SemanticIntegrationReportWriter,
    external_config: ExternalSemanticTestConfig,
) -> None:
    """验证真实 Temporal 可调度 Root Plan 并形成完整任务终态。

    :param report_writer: 集成测试报告写入器。
    :param external_config: 外部集成测试配置。
    :return: 无返回值。
    """
    with report_writer.case(
        case_id="ENG-023",
        lane="temporal_scheduler",
        priority="P0",
    ):
        plan_id, status = asyncio.run(_run_temporal_workflow(external_config))
        record = report_writer.last_record()
        record["plan_id"] = plan_id
        record["review_outcome"] = status


def test_eng_032_postgres_projection_matches_terminal_result(
    report_writer: SemanticIntegrationReportWriter,
    external_config: ExternalSemanticTestConfig,
) -> None:
    """验证 PostgreSQL M04 投影仓储可初始化、记录与查询终态。

    :param report_writer: 集成测试报告写入器。
    :param external_config: 外部集成测试配置。
    :return: 无返回值。
    """
    with report_writer.case(
        case_id="ENG-032",
        lane="postgres_projection",
        priority="P0",
    ):
        _, validated_plan = asyncio.run(
            build_plan(
                "我家英短精神正常，没有呕吐。",
                identity_suffix=uuid4().hex,
            ),
        )
        repository = PostgresSemanticDAGProjectionRepository(
            make_session_factory(external_config.database_url),
        )
        run_id = semantic_dag_run_id(validated_plan.plan.plan_id)
        request = DAGRunProjectionInitializeRequest(
            run_id=run_id,
            workflow_id=run_id,
            validated_plan=validated_plan,
            policy=DAGExecutionPolicy(),
            task_policies=build_dag_task_policies(
                registry=build_production_registry(),
                validated_plan=validated_plan,
            ),
        )
        initialized = repository.initialize_run(request)
        assert initialized.status is DAGRunStatus.RUNNING
        assert len(initialized.tasks) == len(validated_plan.plan.tasks)
        for task in validated_plan.plan.tasks:
            repository.record_task_result(
                run_id,
                DAGTaskExecutionResult(
                    task_id=task.task_id,
                    terminal_state=DAGTaskTerminalState.VERIFIED,
                    artifact_reference=(
                        f"integration-test://semantic-proposal/{run_id}/{task.task_id}"
                    ),
                ),
            )
        finished = repository.finish_run(run_id, DAGRunStatus.COMPLETED)
        loaded = repository.load_run(run_id)
        assert finished.status is DAGRunStatus.COMPLETED
        assert loaded is not None
        assert loaded.plan_id == validated_plan.plan.plan_id
        record = report_writer.last_record()
        record["plan_id"] = validated_plan.plan.plan_id
        record["review_outcome"] = loaded.status.value


@pytest.mark.parametrize(
    "case_id",
    [
        "GEN-001",
        "GEN-002",
        "GEN-003",
        "GEN-005",
        "GEN-006",
        "GEN-007",
        "GEN-008",
        "GEN-010",
    ],
)
def test_gen_semantic_fidelity_through_real_review(
    report_writer: SemanticIntegrationReportWriter,
    external_config: ExternalSemanticTestConfig,
    semantic_fixture_file: SemanticFixtureFile,
    case_id: str,
) -> None:
    """验证真实 M06 输出经过 M08 审查后保留关键语义状态。

    :param report_writer: 集成测试报告写入器。
    :param external_config: 外部集成测试配置。
    :param semantic_fixture_file: 语义集成测试 fixture。
    :param case_id: 语义用例标识。
    :return: 无返回值。
    """
    if not _semantic_test_enabled():
        pytest.skip("未开启 RUN_SEMANTIC_COLLABORATION_SEMANTIC_TEST")
    fixture = require_semantic_fixture(semantic_fixture_file, case_id)
    with report_writer.case(
        case_id=fixture.case_id,
        lane="semantic_generation",
        priority=fixture.priority,
    ):
        result = asyncio.run(
            run_generation(
                fixture.current_turn,
                config=external_config,
                identity_suffix=uuid4().hex,
            ),
        )
        assert result.intent_verification.state is GenerationVerificationState.ACCEPTED
        assert result.claim_verification.state is GenerationVerificationState.ACCEPTED
        for field, expected in fixture.expected_intent.items():
            assert result.intent_proposal.payload.get(field) is expected
        if fixture.minimum_claim_count is not None:
            claims = result.claim_proposal.payload.get("claims")
            assert isinstance(claims, list)
            assert len(claims) >= fixture.minimum_claim_count
        bundle = asyncio.run(
            build_review_runner(
                result.snapshot,
                transport=build_qwen_transport(external_config),
                config=external_config,
            ).review(
                result.claim_proposal,
                result.claim_verification,
            ),
        )
        _assert_dimension_expectations(fixture, _true_dimensions(bundle))
        record = report_writer.last_record()
        _record_generation(record, result)
        _record_review(record, bundle)


@pytest.mark.parametrize(
    "case_id",
    [
        "REV-001",
        "REV-002",
        "REV-005",
        "REV-006",
        "REV-101",
        "REV-102",
        "REV-103",
        "REV-105",
        "REV-107",
        "REV-109",
        "REV-110",
        "REV-111",
    ],
)
def test_rev_real_review_detects_calibrated_semantic_issues(
    report_writer: SemanticIntegrationReportWriter,
    external_config: ExternalSemanticTestConfig,
    semantic_fixture_file: SemanticFixtureFile,
    case_id: str,
) -> None:
    """用固定 claims 直接校准真实 M08 Coverage / Faithfulness Review。

    :param report_writer: 集成测试报告写入器。
    :param external_config: 外部集成测试配置。
    :param semantic_fixture_file: 语义集成测试 fixture。
    :param case_id: Review 校准用例标识。
    :return: 无返回值。
    """
    if not _semantic_test_enabled():
        pytest.skip("未开启 RUN_SEMANTIC_COLLABORATION_SEMANTIC_TEST")
    fixture = require_semantic_fixture(semantic_fixture_file, case_id)
    with report_writer.case(
        case_id=fixture.case_id,
        lane="semantic_review_calibration",
        priority=fixture.priority,
    ):
        _, proposal, bundle = asyncio.run(
            build_real_review_bundle(
                fixture.current_turn,
                fixture.claims,
                config=external_config,
                identity_suffix=uuid4().hex,
            ),
        )
        dimensions = _true_dimensions(bundle)
        _assert_dimension_expectations(fixture, dimensions)
        record = report_writer.last_record()
        record["proposal_digest"] = proposal.proposal_digest
        _record_review(record, bundle)


@pytest.mark.parametrize(
    "case_id",
    [
        "PLAN-001",
        "PLAN-002",
        "PLAN-003",
        "PLAN-005",
        "PLAN-006",
        "PLAN-007",
        "PLAN-008",
        "PLAN-009",
        "PLAN-010",
        "PLAN-011",
        "PLAN-012",
    ],
)
def test_plan_deterministic_router_matches_review_dimensions(
    report_writer: SemanticIntegrationReportWriter,
    external_config: ExternalSemanticTestConfig,
    semantic_fixture_file: SemanticFixtureFile,
    case_id: str,
) -> None:
    """用固定 M08 矩阵验证 M09 只按 true dimension 路由。

    :param report_writer: 集成测试报告写入器。
    :param external_config: 外部集成测试配置。
    :param semantic_fixture_file: 语义集成测试 fixture。
    :param case_id: M09 路由用例标识。
    :return: 无返回值。
    """
    fixture = require_semantic_fixture(semantic_fixture_file, case_id)
    with report_writer.case(
        case_id=fixture.case_id,
        lane="repair_planning",
        priority=fixture.priority,
    ):
        context: FixedReviewPlanCase = asyncio.run(
            build_fixed_review_plan_case(
                fixture.current_turn,
                fixture.claims,
                config=external_config,
                coverage_true_dimensions=fixture.coverage_true_dimensions,
                faithfulness_true_dimensions=(
                    fixture.faithfulness_true_dimensions
                ),
            ),
        )
        assert fixture.expected_route is not None
        assert context.plan.route.value == fixture.expected_route
        if context.plan.route is SemanticRepairPlanRoute.REPAIR_REQUIRED:
            assert context.plan.repair_tasks
            expected_lane = (
                SemanticRepairLane.CLAIM_INVENTORY_REPAIR
                if fixture.coverage_true_dimensions
                else SemanticRepairLane.CLAIM_PROPOSITION_REPAIR
            )
            assert all(
                task.repair_lane is expected_lane
                for task in context.plan.repair_tasks
            )
        else:
            assert not context.plan.repair_tasks
        record = report_writer.last_record()
        record["proposal_digest"] = context.proposal.proposal_digest
        record["repair_route"] = context.plan.route.value
        _record_review(record, context.bundle)


@pytest.fixture(scope="module")
def proposition_repair_context(
    external_config: ExternalSemanticTestConfig,
    semantic_fixture_file: SemanticFixtureFile,
) -> tuple[RepairTestCase, SemanticRepairExecutionResult]:
    """构造并执行一次真实 proposition repair 供 M10 与 TODO 负例复用。

    :param external_config: 外部集成测试配置。
    :param semantic_fixture_file: 语义集成测试 fixture。
    :return: 返回修复测试上下文与 M10 执行结果。
    """
    if not _semantic_test_enabled():
        pytest.skip("未开启 RUN_SEMANTIC_COLLABORATION_SEMANTIC_TEST")
    fixture = require_semantic_fixture(semantic_fixture_file, "FIX-001")
    context = asyncio.run(
        build_real_repair_case(
            fixture.current_turn,
            fixture.claims,
            config=external_config,
            faithfulness_overrides={"正常状态误写为否认": True},
        ),
    )
    result = asyncio.run(
        build_repair_runner(
            context.snapshot,
            context.target_snapshot,
            transport=build_qwen_transport(external_config),
            config=external_config,
        ).repair(context.plan, context.bundle),
    )
    return context, result


def test_fix_001_real_proposition_repair_patch_and_probe(
    report_writer: SemanticIntegrationReportWriter,
    external_config: ExternalSemanticTestConfig,
    semantic_fixture_file: SemanticFixtureFile,
    proposition_repair_context: tuple[
        RepairTestCase,
        SemanticRepairExecutionResult,
    ],
) -> None:
    """验证真实 M10 proposition repair 可生成 patch 并通过测试观察器。

    :param report_writer: 集成测试报告写入器。
    :param external_config: 外部集成测试配置。
    :param semantic_fixture_file: 语义集成测试 fixture。
    :param proposition_repair_context: 模块级 proposition 修复上下文。
    :return: 无返回值。
    """
    fixture = require_semantic_fixture(semantic_fixture_file, "FIX-001")
    context, result = proposition_repair_context
    with report_writer.case(
        case_id="FIX-001",
        lane="semantic_repair",
        priority="P0",
    ):
        assert result.state is SemanticRepairExecutionState.PATCH_READY
        assert result.patch_set is not None
        assert result.preview is not None
        assert result.preview.state is PatchApplicationState.PREVIEW_READY
        assert context.plan.repair_tasks[0].repair_lane is (
            SemanticRepairLane.CLAIM_PROPOSITION_REPAIR
        )
        probe_proposal = asyncio.run(
            build_claim_inventory_proposal(
                context.snapshot,
                result.preview.claims,
                model=external_config.model,
            ),
        )
        probe_verification = _verify_proposal(probe_proposal)
        probe_bundle = asyncio.run(
            build_review_runner(
                context.snapshot,
                transport=build_qwen_transport(external_config),
                config=external_config,
            ).review(probe_proposal, probe_verification),
        )
        dimensions = _true_dimensions(probe_bundle)
        original = set(fixture.expected_dimensions)
        assert not dimensions & original
        record = report_writer.last_record()
        record["repair_route"] = context.plan.route.value
        record["repair_lane"] = (
            context.plan.repair_tasks[0].repair_lane.value
        )
        _record_repair(record, result)
        record["post_patch_probe_state"] = probe_bundle.aggregate_outcome.value
        record["original_dimension_resolved"] = True
        record["new_dimension_introduced"] = bool(dimensions - original)


def _verify_proposal(
    proposal: SemanticModelProposal,
) -> SemanticGenerationVerificationResult:
    """对测试 probe proposal 执行 M07 结构验证。

    :param proposal: M05 返回的 Claim Inventory proposal。
    :return: 返回 M07 结构验证结果。
    """
    return SemanticGenerationVerifier().verify(proposal)


def test_fix_003_real_inventory_repair_adds_missing_denial(
    report_writer: SemanticIntegrationReportWriter,
    external_config: ExternalSemanticTestConfig,
    semantic_fixture_file: SemanticFixtureFile,
) -> None:
    """验证真实 M10 inventory repair 可通过稀疏 delta 补充漏抽否定事实。

    :param report_writer: 集成测试报告写入器。
    :param external_config: 外部集成测试配置。
    :param semantic_fixture_file: 语义集成测试 fixture。
    :return: 无返回值。
    """
    if not _semantic_test_enabled():
        pytest.skip("未开启 RUN_SEMANTIC_COLLABORATION_SEMANTIC_TEST")
    fixture = require_semantic_fixture(semantic_fixture_file, "FIX-003")
    with report_writer.case(
        case_id="FIX-003",
        lane="semantic_repair",
        priority="P0",
    ):
        context = asyncio.run(
            build_real_repair_case(
                fixture.current_turn,
                fixture.claims,
                config=external_config,
                coverage_overrides={"存在漏抽显式事实": True},
                missing_claim_candidates=("我家英短没有血便",),
            ),
        )
        assert context.plan.route is SemanticRepairPlanRoute.REPAIR_REQUIRED
        assert context.plan.repair_tasks[0].repair_lane is (
            SemanticRepairLane.CLAIM_INVENTORY_REPAIR
        )
        result = asyncio.run(
            build_repair_runner(
                context.snapshot,
                context.target_snapshot,
                transport=build_qwen_transport(external_config),
                config=external_config,
            ).repair(context.plan, context.bundle),
        )
        assert result.state is SemanticRepairExecutionState.PATCH_READY
        assert result.preview is not None
        assert len(result.preview.claims) > len(context.bundle.claims)
        record = report_writer.last_record()
        record["repair_route"] = context.plan.route.value
        record["repair_lane"] = (
            context.plan.repair_tasks[0].repair_lane.value
        )
        _record_repair(record, result)


def test_neg_010_todo_snapshot_and_m11_resolver_fail_fast(
    report_writer: SemanticIntegrationReportWriter,
) -> None:
    """验证 Persistent snapshot 与 M11 snapshot TODO 端口显式 Fail Fast。

    :param report_writer: 集成测试报告写入器。
    :return: 无返回值。
    """
    with report_writer.case(
        case_id="NEG-010",
        lane="fail_fast",
        priority="P0",
    ):
        snapshot = asyncio.run(
            build_plan(
                "TODO Fail Fast 检查。",
                identity_suffix=uuid4().hex,
            ),
        )[0]
        with pytest.raises(NotImplementedError):
            asyncio.run(TODOTurnSnapshotReader().load(snapshot.context_digest))
        with pytest.raises(NotImplementedError):
            asyncio.run(
                TODORepairTargetSnapshotResolver().load(
                    "a" * 64,
                    "b" * 64,
                ),
            )


def test_neg_012_todo_patch_store_fails_fast(
    report_writer: SemanticIntegrationReportWriter,
    proposition_repair_context: tuple[
        RepairTestCase,
        SemanticRepairExecutionResult,
    ],
) -> None:
    """验证 M11 patch store TODO 不生成伪 artifact。

    :param report_writer: 集成测试报告写入器。
    :param proposition_repair_context: 已生成 patch preview 的修复上下文。
    :return: 无返回值。
    """
    if not _semantic_test_enabled():
        pytest.skip("未开启 RUN_SEMANTIC_COLLABORATION_SEMANTIC_TEST")
    with report_writer.case(
        case_id="NEG-012",
        lane="fail_fast",
        priority="P0",
    ):
        _context, result = proposition_repair_context
        if (
            result.patch_set is not None
            and result.preview is not None
        ):
            with pytest.raises(NotImplementedError):
                asyncio.run(
                    TODORepairPatchStore().commit(
                        result.patch_set,
                        result.preview,
                    ),
                )


def test_neg_013_todo_semantic_task_executor_fails_fast(
    report_writer: SemanticIntegrationReportWriter,
) -> None:
    """验证 M04 TODO SemanticTaskExecutor 不生成伪执行结果。

    :param report_writer: 集成测试报告写入器。
    :return: 无返回值。
    """
    with report_writer.case(
        case_id="NEG-013",
        lane="fail_fast",
        priority="P0",
    ):
        snapshot, validated_plan = asyncio.run(
            build_plan(
                "TODO executor 检查。",
                identity_suffix=uuid4().hex,
            ),
        )
        task = validated_plan.plan.tasks[0]
        from vet_agent.semantic_collaboration import SemanticTaskExecutionRequest

        request = SemanticTaskExecutionRequest(
            run_id=validated_plan.plan.plan_id,
            attempt_number=1,
            task=task,
            turn_snapshot_digest=snapshot.context_digest,
            dependency_artifacts={},
        )
        with pytest.raises(NotImplementedError):
            asyncio.run(TODOSemanticTaskExecutor().execute(request))


def test_iso_001_semantic_preprocessing_stays_domain_isolated(
    report_writer: SemanticIntegrationReportWriter,
) -> None:
    """验证语义协作生产与测试组件未引用下游领域或历史实验实现。

    :param report_writer: 集成测试报告写入器。
    :return: 无返回值。
    """
    with report_writer.case(
        case_id="ISO-001",
        lane="domain_isolation",
        priority="P0",
    ):
        forbidden_markers = (
            "from vet_agent.consultation",
            "from vet_agent.clinical_safety",
            "from vet_agent.services",
            "from vet_agent.agents",
            "from vet_agent.input_preprocessing",
            "import vet_agent.consultation",
            "import vet_agent.clinical_safety",
            "import vet_agent.input_preprocessing",
        )
        source_paths = (
            *(
                PROJECT_ROOT
                / "src"
                / "vet_agent"
                / "semantic_collaboration"
            ).rglob("*.py"),
            *(
                PROJECT_ROOT
                / "tests"
                / "integration"
                / "semantic_collaboration"
            ).rglob("*.py"),
        )
        for path in source_paths:
            source = path.read_text(encoding="utf-8")
            markers = [
                marker
                for marker in forbidden_markers
                if marker in source
            ]
            assert not markers, f"{path} references forbidden modules: {markers}"

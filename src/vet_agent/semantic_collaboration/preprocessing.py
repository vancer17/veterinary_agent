"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/preprocessing.py
作用：组合受限语义协作 DAG M02～M13 的问诊语义预处理主链。
范围：覆盖 TurnSnapshot 持久化、Plan 校验、M06 双任务生成、M07 验证、
      M08 Review、M09/M10 修复、M08 re-review、M11 版本提交、M12 Graph 与
      M13 问诊投影。
说明：本文件不接入 VetOrchestrator、不写问诊状态、不调用临床安全或长期记忆、
      不实现 M14 指标、不在请求内回退旧问诊抽取器，也不把失败转为空 facts。
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass

from .artifact_contracts import SemanticArtifactRepository
from .artifact_store import PostgresSemanticArtifactStore
from .catalog import SkillRegistry
from .claim_graph_builder import SemanticClaimGraphBuilder
from .claim_graph_contracts import (
    SemanticClaimGraph,
    SemanticGraphBuildPolicy,
    SemanticGraphBuildResult,
    SemanticGraphTerminalState,
)
from .consultation_projection import ConsultationProjectionAdapter
from .domain_projection_contracts import (
    SemanticProjectionExecutionState,
    SemanticProjectionResult,
)
from .errors import SemanticCollaborationError, SemanticTaskExecutionError
from .gateway import StructuredLLMGateway
from .gateway_contracts import (
    SemanticModelProposal,
    StructuredModelTransport,
)
from .generation import (
    SemanticGenerationModelPolicy,
    StructuredGenerationSkillRunner,
    validate_generation_configuration,
)
from .plan_compiler import DeterministicPlanCompiler
from .plan_contracts import PlanTask, ValidatedPlan
from .plan_policy import build_production_plan_policy
from .plan_validator import PlanValidator
from .preprocessing_contracts import (
    SemanticConsultationPreprocessingRequest,
    SemanticConsultationPreprocessingResult,
    SemanticPreprocessingExecutionState,
    SemanticPreprocessingFailureCode,
)
from .production import build_production_skill_catalog
from .prompt_renderer import build_production_prompt_renderer_registry
from .repair_planner import SemanticRepairPlanner
from .repair_runner import (
    StructuredRepairSkillRunner,
    validate_repair_configuration,
)
from .review_contracts import (
    SemanticClaimReviewSource,
    SemanticReviewBundle,
    SemanticReviewOutcome,
)
from .review_runner import (
    StructuredReviewSkillRunner,
    validate_review_configuration,
)
from .scheduler_contracts import (
    SemanticTaskExecutionRequest,
    semantic_dag_run_id,
)
from .snapshot import TurnSnapshotBuilder, TurnSnapshotProjector
from .snapshot_contracts import TurnSnapshot
from .snapshot_store import PersistentTurnSnapshotReader, SemanticTurnSnapshotStore
from .verifier import (
    GenerationVerificationState,
    SemanticGenerationVerificationResult,
    SemanticGenerationVerifier,
)


@dataclass
class _PreprocessingRunIdentity:
    """表示主链执行过程中逐步闭合的身份字段。"""

    stage: str = "snapshot"
    turn_id: str | None = None
    turn_index: int | None = None
    turn_snapshot_digest: str | None = None
    plan_id: str | None = None
    run_id: str | None = None
    artifact_reference: str | None = None
    artifact_version: int | None = None
    graph_id: str | None = None
    graph_digest: str | None = None
    graph_terminal_state: SemanticGraphTerminalState | None = None


class SemanticConsultationPreprocessingService:
    """表示 M02～M13 问诊语义预处理应用服务。

    :return: 无返回值；该服务输出 M13 问诊投影，不写任何下游领域状态。
    """

    def __init__(
        self,
        *,
        snapshot_builder: TurnSnapshotBuilder,
        snapshot_store: SemanticTurnSnapshotStore,
        registry: SkillRegistry,
        generation_runner: StructuredGenerationSkillRunner,
        review_runner: StructuredReviewSkillRunner,
        repair_runner: StructuredRepairSkillRunner,
        repair_planner: SemanticRepairPlanner,
        artifact_store: PostgresSemanticArtifactStore,
        graph_builder: SemanticClaimGraphBuilder,
        consultation_adapter: ConsultationProjectionAdapter,
        graph_policy: SemanticGraphBuildPolicy | None = None,
    ) -> None:
        """初始化语义预处理主链的封闭依赖集合。

        :param snapshot_builder: M02 TurnSnapshot 构建器。
        :param snapshot_store: M02 不可变快照存档。
        :param registry: 已冻结的生产 SkillRegistry。
        :param generation_runner: M06 结构化生成执行器。
        :param review_runner: M08 Coverage / Faithfulness Review 执行器。
        :param repair_runner: M10 typed Repair 执行器。
        :param repair_planner: M09 确定性修复规划器。
        :param artifact_store: M11 回合级权威 Artifact Store。
        :param graph_builder: M12 Claim Graph 构建器。
        :param consultation_adapter: M13 问诊投影 Adapter。
        :param graph_policy: 可选 M12 graph 消费策略；默认允许空图与 partial gaps。
        :return: 无返回值。
        """
        self.snapshot_builder = snapshot_builder
        self.snapshot_store = snapshot_store
        self.registry = registry
        self.generation_runner = generation_runner
        self.review_runner = review_runner
        self.repair_runner = repair_runner
        self.repair_planner = repair_planner
        self.artifact_store = artifact_store
        self.graph_builder = graph_builder
        self.consultation_adapter = consultation_adapter
        self.graph_policy = graph_policy or SemanticGraphBuildPolicy(
            allow_partial_with_gaps=True,
            allow_empty_graph=True,
        )
        self.plan_policy = build_production_plan_policy(registry)
        self.plan_compiler = DeterministicPlanCompiler(
            registry=registry,
            policy=self.plan_policy,
        )
        self.plan_validator = PlanValidator(
            registry=registry,
            policy=self.plan_policy,
        )
        self.generation_verifier = SemanticGenerationVerifier()

    async def preprocess(
        self,
        request: SemanticConsultationPreprocessingRequest,
    ) -> SemanticConsultationPreprocessingResult:
        """执行一次当前回合的问诊语义预处理主链。

        :param request: 当前回合预处理请求。
        :return: 返回 verified / partial / blocked / failed 显式结果信封。
        """
        identity = _PreprocessingRunIdentity()
        try:
            return await self._preprocess(request, identity)
        except SemanticCollaborationError as error:
            return self._failure(
                identity,
                SemanticPreprocessingExecutionState.FAILED,
                self._exception_failure_code(identity, error),
                str(error) or error.__class__.__name__,
            )

    async def _preprocess(
        self,
        request: SemanticConsultationPreprocessingRequest,
        identity: _PreprocessingRunIdentity,
    ) -> SemanticConsultationPreprocessingResult:
        """执行无兜底的主链内部流程。

        :param request: 当前回合预处理请求。
        :param identity: 逐步闭合的身份上下文。
        :return: 返回显式主链结果。
        """
        snapshot_request = request.snapshot_request
        built = await self.snapshot_builder.build(snapshot_request)
        snapshot = built.snapshot
        identity.turn_id = snapshot.turn_id
        identity.turn_index = snapshot.turn_index
        identity.turn_snapshot_digest = snapshot.context_digest
        self.snapshot_store.save(snapshot, scope=snapshot_request.scope)

        identity.stage = "plan"
        candidate_plan = self.plan_compiler.compile(snapshot)
        validation = self.plan_validator.validate(candidate_plan, snapshot)
        if validation.validated_plan is None:
            return self._failure(
                identity,
                SemanticPreprocessingExecutionState.FAILED,
                SemanticPreprocessingFailureCode.PLAN_FAILED,
                "semantic preprocessing Root Plan is invalid",
            )
        validated_plan = validation.validated_plan
        identity.plan_id = validated_plan.plan.plan_id
        identity.run_id = semantic_dag_run_id(validated_plan.plan.plan_id)

        identity.stage = "generation"
        intent_proposal, intent_verification = await self._generate(
            snapshot,
            validated_plan,
            identity.run_id,
            "turn_intent",
        )
        claim_proposal, claim_verification = await self._generate(
            snapshot,
            validated_plan,
            identity.run_id,
            "claim_inventory",
        )
        for verification in (intent_verification, claim_verification):
            if verification.state is not GenerationVerificationState.ACCEPTED:
                return self._failure(
                    identity,
                    SemanticPreprocessingExecutionState.BLOCKED,
                    SemanticPreprocessingFailureCode.GENERATION_BLOCKED,
                    verification.failure_message
                    or "semantic generation proposal is structurally blocked",
                )

        identity.stage = "review"
        initial_bundle = await self.review_runner.review(
            claim_proposal,
            claim_verification,
        )
        identity.stage = "artifact"
        final_reference = await self.artifact_store.commit_semantic_turn_result(
            review_bundle=initial_bundle,
            turn_intent_proposal=intent_proposal,
        )

        if initial_bundle.aggregate_outcome in {
            SemanticReviewOutcome.REPAIR_REQUIRED,
            SemanticReviewOutcome.REPAIR_THEN_CLARIFICATION_REQUIRED,
        }:
            repaired_result = await self._repair_and_re_review(
                identity=identity,
                initial_bundle=initial_bundle,
                claim_proposal=claim_proposal,
            )
            if isinstance(repaired_result, SemanticConsultationPreprocessingResult):
                return repaired_result
            final_reference = repaired_result

        self._record_artifact_identity(identity, final_reference)
        identity.stage = "graph"
        graph_result = self.graph_builder.build(
            final_reference,
            self.graph_policy,
        )
        if graph_result.graph is not None:
            identity.graph_id = graph_result.graph.identity.graph_id
            identity.graph_digest = graph_result.graph.identity.graph_digest
            identity.graph_terminal_state = graph_result.terminal_state
        return self._project(graph_result, identity)

    async def _generate(
        self,
        snapshot: TurnSnapshot,
        validated_plan: ValidatedPlan,
        run_id: str,
        skill_id: str,
    ) -> tuple[
        SemanticModelProposal,
        SemanticGenerationVerificationResult,
    ]:
        """执行一个 M06 根任务并立即进行 M07 结构验证。

        :param snapshot: 当前回合权威快照。
        :param validated_plan: 已通过 M03 校验的 Root Plan。
        :param run_id: 语义 DAG workflow 稳定标识。
        :param skill_id: 待执行的生产生成 SKILL 标识。
        :return: 返回模型 proposal 与 M07 验证结果。
        """
        execution = self._execution_request(
            snapshot=snapshot,
            validated_plan=validated_plan,
            run_id=run_id,
            skill_id=skill_id,
        )
        proposal = await self.generation_runner.generate(execution)
        return proposal, self.generation_verifier.verify(proposal)

    async def _repair_and_re_review(
        self,
        *,
        identity: _PreprocessingRunIdentity,
        initial_bundle: SemanticReviewBundle,
        claim_proposal: SemanticModelProposal,
    ) -> str | SemanticConsultationPreprocessingResult:
        """执行 M09 / M10 / M11 / M08 repair 与 re-review 闭环。

        :param identity: 当前主链身份上下文。
        :param initial_bundle: 产生 repair_required 的 M08 Review Bundle。
        :param claim_proposal: 原始 M06 Claim Inventory proposal。
        :return: 返回最终 artifact 引用；修复预算耗尽时返回显式失败结果。
        """
        identity.stage = "repair"
        repair_plan = self.repair_planner.plan(initial_bundle)
        if not repair_plan.repair_tasks:
            current_reference = self._current_reference(initial_bundle)
            if current_reference:
                self._record_artifact_identity(identity, current_reference)
            return self._failure(
                identity,
                SemanticPreprocessingExecutionState.BLOCKED,
                SemanticPreprocessingFailureCode.REPAIR_EXHAUSTED,
                "semantic repair plan does not contain executable repair tasks",
            )
        repair_execution = await self.repair_runner.repair(
            repair_plan,
            initial_bundle,
        )
        if (
            repair_execution.patch_set is None
            or repair_execution.preview is None
        ):
            self._record_artifact_identity(
                identity,
                self._current_reference(initial_bundle),
            )
            return self._failure(
                identity,
                SemanticPreprocessingExecutionState.FAILED,
                SemanticPreprocessingFailureCode.REPAIR_FAILED,
                repair_execution.failure_message
                or "semantic repair execution is blocked",
            )

        identity.stage = "artifact"
        patched_reference = await self.artifact_store.commit(
            repair_execution.patch_set,
            repair_execution.preview,
        )
        self._record_artifact_identity(identity, patched_reference)

        identity.stage = "review"
        re_review_bundle = await self.review_runner.review_claims(
            source=SemanticClaimReviewSource(
                execution=claim_proposal.execution,
                source_proposal_digest=claim_proposal.proposal_digest,
            ),
            claims=repair_execution.preview.claims,
        )
        if re_review_bundle.aggregate_outcome in {
            SemanticReviewOutcome.REPAIR_REQUIRED,
            SemanticReviewOutcome.REPAIR_THEN_CLARIFICATION_REQUIRED,
        }:
            return self._failure_after_re_review(
                identity,
                re_review_bundle,
            )

        identity.stage = "artifact"
        final_reference = await self.artifact_store.commit_review_bundle(
            re_review_bundle,
        )
        self._record_artifact_identity(identity, final_reference)
        return final_reference

    def _project(
        self,
        graph_result: SemanticGraphBuildResult,
        identity: _PreprocessingRunIdentity,
    ) -> SemanticConsultationPreprocessingResult:
        """执行 M12 终态到 M13 问诊投影的最终分流。

        :param graph_result: M12 graph 构建结果。
        :param identity: 当前主链身份上下文。
        :return: 返回可消费投影或显式 blocked / failed 信封。
        """
        if graph_result.terminal_state is SemanticGraphTerminalState.GRAPH_DISAGREEMENT:
            return self._failure(
                identity,
                SemanticPreprocessingExecutionState.BLOCKED,
                SemanticPreprocessingFailureCode.GRAPH_DISAGREEMENT,
                graph_result.failure_message or "semantic graph disagreement",
            )
        if graph_result.terminal_state is SemanticGraphTerminalState.GRAPH_BLOCKED:
            return self._failure(
                identity,
                SemanticPreprocessingExecutionState.BLOCKED,
                SemanticPreprocessingFailureCode.GRAPH_BLOCKED,
                graph_result.failure_message or "semantic graph is blocked",
            )

        identity.stage = "projection"
        projection_result = self.consultation_adapter.project(graph_result)
        if projection_result.execution_state is SemanticProjectionExecutionState.REJECTED:
            return self._failure(
                identity,
                SemanticPreprocessingExecutionState.BLOCKED,
                SemanticPreprocessingFailureCode.PROJECTION_REJECTED,
                projection_result.failure_message
                or "consultation projection is rejected",
            )
        graph = graph_result.graph
        if graph is None:
            return self._failure(
                identity,
                SemanticPreprocessingExecutionState.BLOCKED,
                SemanticPreprocessingFailureCode.PROJECTION_REJECTED,
                "accepted semantic graph result does not carry a graph",
            )
        return self._success(graph, projection_result, identity)

    def _success(
        self,
        graph: SemanticClaimGraph,
        projection_result: SemanticProjectionResult,
        identity: _PreprocessingRunIdentity,
    ) -> SemanticConsultationPreprocessingResult:
        """构造可消费的语义预处理成功信封。

        :param graph: 当前 M12 Claim Graph。
        :param projection_result: M13 问诊投影结果信封。
        :param identity: 当前主链身份上下文。
        :return: 返回 verified 或 partial with gaps 结果。
        """
        projection = projection_result.projection
        if projection is None or projection_result.projection_id is None:
            raise SemanticTaskExecutionError(
                "consultation projection payload is missing",
                failure_code="projection_payload_missing",
            )
        identity.graph_id = graph.identity.graph_id
        identity.graph_digest = graph.identity.graph_digest
        identity.graph_terminal_state = graph.terminal_state
        return SemanticConsultationPreprocessingResult(
            execution_state=(
                SemanticPreprocessingExecutionState.COMPLETED_PARTIAL_WITH_GAPS
                if graph.terminal_state
                is SemanticGraphTerminalState.GRAPH_PARTIAL_WITH_GAPS
                else SemanticPreprocessingExecutionState.COMPLETED_VERIFIED
            ),
            turn_id=identity.turn_id,
            turn_index=identity.turn_index,
            turn_snapshot_digest=identity.turn_snapshot_digest,
            run_id=identity.run_id,
            plan_id=identity.plan_id,
            artifact_reference=graph.identity.artifact_reference,
            artifact_version=graph.identity.artifact_version,
            graph_id=graph.identity.graph_id,
            graph_digest=graph.identity.graph_digest,
            graph_terminal_state=graph.terminal_state,
            projection_id=projection_result.projection_id,
            consultation_projection=projection,
        )

    def _execution_request(
        self,
        *,
        snapshot: TurnSnapshot,
        validated_plan: ValidatedPlan,
        run_id: str,
        skill_id: str,
    ) -> SemanticTaskExecutionRequest:
        """从权威 Plan 中解析一个生成任务执行请求。

        :param snapshot: 当前回合快照。
        :param validated_plan: 已通过校验的 Root Plan。
        :param run_id: 语义 DAG workflow 稳定标识。
        :param skill_id: 生产生成 SKILL 标识。
        :return: 返回身份闭合的 M06 执行请求。
        :raises SemanticTaskExecutionError: Plan 中 SKILL 任务缺失或重复时抛出。
        """
        task = self._require_task(validated_plan, skill_id)
        return SemanticTaskExecutionRequest(
            run_id=run_id,
            attempt_number=1,
            task=task,
            turn_snapshot_digest=snapshot.context_digest,
            dependency_artifacts={},
        )

    def _require_task(
        self,
        validated_plan: ValidatedPlan,
        skill_id: str,
    ) -> PlanTask:
        """读取唯一的生产生成任务。

        :param validated_plan: 已通过校验的 Root Plan。
        :param skill_id: 生产生成 SKILL 标识。
        :return: 返回权威 PlanTask。
        :raises SemanticTaskExecutionError: 任务缺失或重复时抛出。
        """
        tasks = [
            task
            for task in validated_plan.plan.tasks
            if task.skill_id == skill_id
        ]
        if len(tasks) != 1:
            raise SemanticTaskExecutionError(
                "semantic preprocessing plan task is not unique",
                failure_code="plan_task_not_unique",
            )
        return tasks[0]

    def _record_artifact_identity(
        self,
        identity: _PreprocessingRunIdentity,
        artifact_reference: str,
    ) -> None:
        """记录当前 M11 artifact 引用与版本。

        :param identity: 当前主链身份上下文。
        :param artifact_reference: M11 artifact 引用。
        :return: 无返回值。
        """
        identity.artifact_reference = artifact_reference
        artifact = self.artifact_store.repository.get_artifact(artifact_reference)
        identity.artifact_version = None if artifact is None else artifact.version

    def _current_reference(
        self,
        bundle: SemanticReviewBundle,
    ) -> str:
        """读取当前 Review Bundle 对应的 M11 artifact 引用。

        :param bundle: M08 Review Bundle。
        :return: 返回当前 artifact 引用。
        """
        artifact = self.artifact_store.get_current_artifact(
            run_id=bundle.run_id,
            source_task_id=bundle.source_task_id,
            turn_snapshot_digest=bundle.turn_snapshot_digest,
        )
        return "" if artifact is None else artifact.artifact_reference

    def _failure_after_re_review(
        self,
        identity: _PreprocessingRunIdentity,
        bundle: SemanticReviewBundle,
    ) -> SemanticConsultationPreprocessingResult:
        """构造修复后仍需修复的显式预算耗尽结果。

        :param identity: 当前主链身份上下文。
        :param bundle: 修复后 re-review Bundle。
        :return: 返回不携带问诊投影的失败信封。
        """
        reference = self._current_reference(bundle)
        if reference:
            self._record_artifact_identity(identity, reference)
        return self._failure(
            identity,
            SemanticPreprocessingExecutionState.FAILED,
            SemanticPreprocessingFailureCode.REPAIR_EXHAUSTED,
            "semantic repair depth is exhausted after re-review",
        )

    def _failure(
        self,
        identity: _PreprocessingRunIdentity,
        execution_state: SemanticPreprocessingExecutionState,
        failure_code: SemanticPreprocessingFailureCode,
        failure_message: str,
    ) -> SemanticConsultationPreprocessingResult:
        """构造显式失败信封。

        :param identity: 当前主链身份上下文。
        :param execution_state: blocked 或 failed 状态。
        :param failure_code: 稳定失败码。
        :param failure_message: 工程排障说明。
        :return: 返回不携带 ConsultationProjection 的结果。
        """
        return SemanticConsultationPreprocessingResult(
            execution_state=execution_state,
            turn_id=identity.turn_id,
            turn_index=identity.turn_index,
            turn_snapshot_digest=identity.turn_snapshot_digest,
            run_id=identity.run_id,
            plan_id=identity.plan_id,
            artifact_reference=identity.artifact_reference,
            artifact_version=identity.artifact_version,
            graph_id=identity.graph_id,
            graph_digest=identity.graph_digest,
            graph_terminal_state=identity.graph_terminal_state,
            failure_code=failure_code,
            failure_message=failure_message[:1000],
        )

    def _exception_failure_code(
        self,
        identity: _PreprocessingRunIdentity,
        error: SemanticCollaborationError,
    ) -> SemanticPreprocessingFailureCode:
        """把主链阶段异常映射为稳定失败码。

        :param identity: 当前主链身份上下文。
        :param error: 语义协作异常。
        :return: 返回稳定失败码。
        """
        mapping = {
            "snapshot": SemanticPreprocessingFailureCode.TURN_SNAPSHOT_FAILED,
            "plan": SemanticPreprocessingFailureCode.PLAN_FAILED,
            "generation": SemanticPreprocessingFailureCode.GENERATION_FAILED,
            "review": SemanticPreprocessingFailureCode.REVIEW_FAILED,
            "repair": SemanticPreprocessingFailureCode.REPAIR_FAILED,
            "artifact": SemanticPreprocessingFailureCode.ARTIFACT_COMMIT_FAILED,
            "graph": SemanticPreprocessingFailureCode.GRAPH_BLOCKED,
            "projection": SemanticPreprocessingFailureCode.PROJECTION_REJECTED,
        }
        return mapping.get(
            identity.stage,
            SemanticPreprocessingFailureCode.PIPELINE_FAILED,
        )


def build_semantic_consultation_preprocessing_service(
    *,
    snapshot_builder: TurnSnapshotBuilder,
    snapshot_store: SemanticTurnSnapshotStore,
    artifact_repository: SemanticArtifactRepository,
    transport: StructuredModelTransport,
    generation_model_policy: SemanticGenerationModelPolicy,
    review_model_policy: SemanticGenerationModelPolicy,
    repair_model_policy: SemanticGenerationModelPolicy,
    graph_policy: SemanticGraphBuildPolicy | None = None,
) -> SemanticConsultationPreprocessingService:
    """组合当前生产语义预处理主链。

    :param snapshot_builder: M02 TurnSnapshot 构建器。
    :param snapshot_store: M02 TurnSnapshot 存档。
    :param artifact_repository: M11 权威 artifact 仓储。
    :param transport: 不重试、不 fallback 的结构化模型传输端口。
    :param generation_model_policy: M06 两个生成 SKILL 的精确模型策略。
    :param review_model_policy: M08 两个 Review SKILL 的精确模型策略。
    :param repair_model_policy: M10 两个 Repair SKILL 的精确模型策略。
    :param graph_policy: 可选 M12 graph 消费策略。
    :return: 返回问诊语义预处理应用服务。
    """
    catalog = build_production_skill_catalog()
    registry = catalog.registry()
    renderer_registry = build_production_prompt_renderer_registry()
    specs = catalog.list_specs()
    validate_generation_configuration(
        specs=specs,
        renderer_registry=renderer_registry,
        model_policy=generation_model_policy,
    )
    validate_review_configuration(
        specs=specs,
        renderer_registry=renderer_registry,
        model_policy=review_model_policy,
    )
    validate_repair_configuration(
        specs=specs,
        renderer_registry=renderer_registry,
        model_policy=repair_model_policy,
    )
    gateway = StructuredLLMGateway(
        registry=registry,
        transport=transport,
    )
    snapshot_reader = PersistentTurnSnapshotReader(snapshot_store)
    projector = TurnSnapshotProjector()
    artifact_store = PostgresSemanticArtifactStore(artifact_repository)
    generation_runner = StructuredGenerationSkillRunner(
        registry=registry,
        renderer_registry=renderer_registry,
        snapshot_reader=snapshot_reader,
        projector=projector,
        gateway=gateway,
        model_policy=generation_model_policy,
    )
    review_runner = StructuredReviewSkillRunner(
        registry=registry,
        renderer_registry=renderer_registry,
        snapshot_reader=snapshot_reader,
        projector=projector,
        gateway=gateway,
        model_policy=review_model_policy,
    )
    repair_runner = StructuredRepairSkillRunner(
        registry=registry,
        renderer_registry=renderer_registry,
        snapshot_reader=snapshot_reader,
        target_snapshot_resolver=artifact_store,
        projector=projector,
        gateway=gateway,
        model_policy=repair_model_policy,
    )
    return SemanticConsultationPreprocessingService(
        snapshot_builder=snapshot_builder,
        snapshot_store=snapshot_store,
        registry=registry,
        generation_runner=generation_runner,
        review_runner=review_runner,
        repair_runner=repair_runner,
        repair_planner=SemanticRepairPlanner(registry=registry),
        artifact_store=artifact_store,
        graph_builder=SemanticClaimGraphBuilder(artifact_repository),
        consultation_adapter=ConsultationProjectionAdapter(),
        graph_policy=graph_policy,
    )


__all__ = [
    "SemanticConsultationPreprocessingService",
    "build_semantic_consultation_preprocessing_service",
]

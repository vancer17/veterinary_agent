"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/repair_runner.py
作用：实现受限语义协作 DAG M10 的两个通用 Repair SKILL 执行器。
范围：覆盖 accepted M09 plan 验证、M11 base snapshot 读取、TurnSnapshot digest
      校验、受限 prompt 渲染、M05 结构化调用、稀疏输出解析、typed patch 编译、
      patch verifier、patch set verifier 与确定性应用预览。
说明：本文件不直接修改 claims、不提交 M11 artifact、不做语义 re-review、
      不调用问诊或临床安全领域，也不提供旧链路或关键词回退。
=============================================================================
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import ValidationError

from .catalog import SkillRegistry
from .contracts import (
    SkillExecutionFamily,
    SkillSpec,
)
from .errors import (
    SemanticRepairExecutionError,
    SemanticRepairPlanError,
)
from .gateway import StructuredLLMGateway
from .gateway_contracts import (
    SemanticModelProposal,
    StructuredLLMCallRequest,
)
from .generation import (
    SemanticGenerationModelPolicy,
    TurnSnapshotReader,
)
from .patch_applier import DeterministicPatchApplier
from .patch_compiler import SemanticPatchCompiler
from .patch_contracts import (
    ClaimInventoryRepairOutput,
    ClaimPropositionRepairOutput,
    PatchApplicationState,
    RepairTargetArtifactSnapshot,
    RepairTargetSnapshotResolver,
    SemanticPatchFailureCode,
    SemanticPatchProposal,
    SemanticPatchSet,
    SemanticPatchVerificationState,
    SemanticRepairExecutionResult,
    SemanticRepairExecutionState,
    build_blocked_repair_execution,
)
from .patch_verifier import (
    SemanticPatchSetVerifier,
    SemanticPatchVerifier,
)
from .plan_contracts import (
    PlanTask,
    PlanTaskSelectionSource,
    SchemaContractReference,
)
from .prompt_renderer import (
    SkillPromptRendererRegistry,
    SkillPromptRenderRequest,
    SkillPromptRepairContext,
)
from .repair_contracts import (
    SemanticRepairLane,
    SemanticRepairPlan,
    SemanticRepairTask,
)
from .repair_planner import SemanticRepairPlanVerifier
from .review_contracts import SemanticReviewBundle
from .scheduler_contracts import SemanticTaskExecutionRequest
from .snapshot import TurnSnapshotProjector


class StructuredRepairSkillRunner:
    """表示 M10 Claim Inventory / Proposition Repair 的生产执行器。

    :return: 无返回值；执行器返回 verified patch set 预览，不产生权威 artifact。
    """

    def __init__(
        self,
        *,
        registry: SkillRegistry,
        renderer_registry: SkillPromptRendererRegistry,
        snapshot_reader: TurnSnapshotReader,
        target_snapshot_resolver: RepairTargetSnapshotResolver,
        projector: TurnSnapshotProjector,
        gateway: StructuredLLMGateway,
        model_policy: SemanticGenerationModelPolicy,
        patch_compiler: SemanticPatchCompiler | None = None,
        patch_verifier: SemanticPatchVerifier | None = None,
        patch_set_verifier: SemanticPatchSetVerifier | None = None,
        plan_verifier: SemanticRepairPlanVerifier | None = None,
    ) -> None:
        """初始化 M10 Repair Runner 的封闭依赖集合。

        :param registry: 已冻结的生产 SkillCatalog 只读门面。
        :param renderer_registry: 启动期闭合的版本化 Repair renderer 目录。
        :param snapshot_reader: 按 digest 读取权威 TurnSnapshot 的端口。
        :param target_snapshot_resolver: M11 base artifact 快照读取端口。
        :param projector: 按 SkillSpec 生成受限上下文投影的投影器。
        :param gateway: M05 单次结构化模型网关。
        :param model_policy: 两个 Repair SKILL 的精确模型策略。
        :param patch_compiler: 可选确定性 patch compiler。
        :param patch_verifier: 可选单 patch 验证器。
        :param patch_set_verifier: 可选 patch set 验证器。
        :param plan_verifier: 可选 M09 plan 复算验证器。
        :return: 无返回值。
        """
        self.registry = registry
        self.renderer_registry = renderer_registry
        self.snapshot_reader = snapshot_reader
        self.target_snapshot_resolver = target_snapshot_resolver
        self.projector = projector
        self.gateway = gateway
        self.model_policy = model_policy
        self.patch_compiler = patch_compiler or SemanticPatchCompiler()
        self.patch_verifier = patch_verifier or SemanticPatchVerifier()
        self.patch_set_verifier = patch_set_verifier or SemanticPatchSetVerifier()
        self.plan_verifier = plan_verifier or SemanticRepairPlanVerifier(
            registry=registry,
        )

    async def repair(
        self,
        plan: SemanticRepairPlan,
        bundle: SemanticReviewBundle,
    ) -> SemanticRepairExecutionResult:
        """执行一个 accepted M09 repair plan 并生成原子 patch 预览。

        :param plan: M09 生成的确定性修复计划。
        :param bundle: 产生该计划的 M08 Review Bundle。
        :return: 返回 patch ready 或 blocked 的 M10 执行结果。
        :raises SemanticRepairExecutionError: 输入计划、SKILL 或模型策略非法时抛出。
        """
        plan_verification = self.plan_verifier.verify(plan, bundle)
        if plan_verification.state.value != "accepted":
            return build_blocked_repair_execution(
                repair_plan_id=plan.plan_id,
                failure_code=SemanticPatchFailureCode.IDENTITY_MISMATCH,
                failure_message="repair plan is not accepted by deterministic verifier",
            )
        if not plan.repair_tasks:
            raise SemanticRepairExecutionError("repair plan does not contain tasks")
        snapshot = await self.target_snapshot_resolver.load(
            plan.source_proposal_digest,
            plan.review_bundle_digest,
        )
        turn_snapshot = await self.snapshot_reader.load(plan.turn_snapshot_digest)
        turn_snapshot.verify_digest(plan.turn_snapshot_digest)
        patches: list[SemanticPatchProposal] = []
        for task in plan.repair_tasks:
            patches.append(
                await self._run_repair_task(
                    repair_plan_id=plan.plan_id,
                    task=task,
                    plan=plan,
                    snapshot=snapshot,
                ),
            )
        patch_set = SemanticPatchSet(
            repair_plan_id=plan.plan_id,
            patches=tuple(patches),
            artifact_reference=snapshot.artifact_reference,
            base_version=snapshot.base_version,
        )
        set_result = self.patch_set_verifier.verify(
            patch_set,
            plan=plan,
            snapshot=snapshot,
        )
        if set_result.state is not SemanticPatchVerificationState.ACCEPTED:
            return build_blocked_repair_execution(
                repair_plan_id=plan.plan_id,
                failure_code=set_result.failure_code
                or SemanticPatchFailureCode.RESULT_INVALID,
                failure_message=set_result.failure_message
                or "semantic patch set verification failed",
            )
        preview = DeterministicPatchApplier().preview(patch_set, snapshot)
        if preview.state is not PatchApplicationState.PREVIEW_READY:
            return build_blocked_repair_execution(
                repair_plan_id=plan.plan_id,
                failure_code=preview.failure_code
                or SemanticPatchFailureCode.RESULT_INVALID,
                failure_message=preview.failure_message
                or "semantic patch application preview failed",
            )
        return SemanticRepairExecutionResult(
            repair_plan_id=plan.plan_id,
            state=SemanticRepairExecutionState.PATCH_READY,
            patch_set=patch_set,
            preview=preview,
        )

    async def _run_repair_task(
        self,
        *,
        repair_plan_id: str,
        task: SemanticRepairTask,
        plan: SemanticRepairPlan,
        snapshot: RepairTargetArtifactSnapshot,
    ) -> SemanticPatchProposal:
        """执行单个 M09 修复任务并编译为 typed patch。

        :param repair_plan_id: M09 repair plan 稳定身份。
        :param task: 当前待执行的 M09 修复任务。
        :param plan: 当前 accepted M09 修复计划。
        :param snapshot: M11 base artifact 快照。
        :return: 返回单个 patch proposal；语义成功仍待 M08 re-review。
        :raises SemanticRepairExecutionError: SKILL、上下文或输出契约非法时抛出。
        """
        spec = self._resolve_spec(task)
        execution = self._build_execution(task, spec)
        turn_snapshot = await self.snapshot_reader.load(execution.turn_snapshot_digest)
        turn_snapshot.verify_digest(execution.turn_snapshot_digest)
        projection = self.projector.project(turn_snapshot, spec.context_contract)
        renderer = self.renderer_registry.require(
            spec.skill_id,
            spec.skill_version,
        )
        prompt = renderer.render(
            SkillPromptRenderRequest(
                execution=execution,
                spec=spec,
                projection=projection,
                repair_context=self._repair_context(task, snapshot),
            ),
        )
        model_rule = self.model_policy.require(
            spec.skill_id,
            spec.skill_version,
        )
        model_proposal = await self.gateway.generate(
            StructuredLLMCallRequest(
                execution=execution,
                prompt=prompt,
                model=model_rule.model,
                temperature=model_rule.temperature,
                timeout_seconds=model_rule.timeout_seconds,
            ),
        )
        return self._compile_model_proposal(
            repair_plan_id=repair_plan_id,
            task=task,
            plan=plan,
            snapshot=snapshot,
            spec=spec,
            model_proposal=model_proposal,
        )

    def _repair_context(
        self,
        task: SemanticRepairTask,
        snapshot: RepairTargetArtifactSnapshot,
    ) -> SkillPromptRepairContext:
        """构造当前修复任务的受限任务内上下文。

        :param task: 当前 M09 修复任务。
        :param snapshot: M11 base artifact 快照。
        :return: 还认 Claim candidates 或单 claim 修复上下文。
        :raises SemanticRepairExecutionError: 修复 lane不支持时抛出。
        """
        if task.repair_lane is SemanticRepairLane.CLAIM_INVENTORY_REPAIR:
            return SkillPromptRepairContext(
                claim_candidates=snapshot.claims,
                repair_dimensions=task.review_dimensions,
                repair_hints=task.repair_hints,
            )
        target_claim = task.target_claim_proposition
        if target_claim is None:
            raise SemanticRepairExecutionError("proposition repair target is missing")
        return SkillPromptRepairContext(
            target_claim=target_claim,
            repair_dimensions=task.review_dimensions,
        )

    def _build_execution(
        self,
        task: SemanticRepairTask,
        spec: SkillSpec,
    ) -> SemanticTaskExecutionRequest:
        """构造 M10 动态修复任务的权威执行请求。

        :param task: 当前 M09 修复任务。
        :param spec: SkillCatalog 解析出的 Repair SkillSpec。
        :return: 返回绑定 TurnSnapshot 与权威 schema 的执行请求。
        """
        plan_task = PlanTask(
            task_id=task.repair_task_id,
            skill_id=spec.skill_id,
            skill_version=spec.skill_version,
            target_envelope_id=(
                f"repair:{task.repair_lane.value}:"
                f"{task.target_claim_digest or task.review_bundle_digest[:16]}"
            ),
            depends_on=(task.source_task_id,),
            expected_output_schema=SchemaContractReference.from_contract(
                spec.output_contract,
            ),
            selection_source=PlanTaskSelectionSource.DETERMINISTIC_REPAIR_EXPANSION,
        )
        return SemanticTaskExecutionRequest(
            run_id=task.run_id,
            attempt_number=1,
            task=plan_task,
            turn_snapshot_digest=task.turn_snapshot_digest,
            dependency_artifacts={},
        )

    def _resolve_spec(
        self,
        task: SemanticRepairTask,
    ) -> SkillSpec:
        """解析并校验当前修复任务绑定的 Repair SkillSpec。

        :param task: 当前 M09 修复任务。
        :return: 返回生产 SkillCatalog 中的 Repair 契约。
        :raises SemanticRepairExecutionError: SKILL 缺失或执行家族非法时抛出。
        """
        try:
            spec = self.registry.require(
                task.repair_skill_id,
                task.repair_skill_version,
            )
        except Exception as error:
            raise SemanticRepairExecutionError(
                "semantic repair skill is not registered",
            ) from error
        if spec.execution_family is not SkillExecutionFamily.STRUCTURED_REPAIR:
            raise SemanticRepairExecutionError(
                "semantic repair runner requires structured repair skill",
            )
        return spec

    def _compile_model_proposal(
        self,
        *,
        repair_plan_id: str,
        task: SemanticRepairTask,
        plan: SemanticRepairPlan,
        snapshot: RepairTargetArtifactSnapshot,
        spec: SkillSpec,
        model_proposal: SemanticModelProposal,
    ) -> SemanticPatchProposal:
        """解析极薄模型输出并编译为系统 typed patch。

        :param repair_plan_id: M09 repair plan 稳定身份。
        :param task: 当前 M09 修复任务。
        :param plan: 当前 accepted M09 修复计划。
        :param snapshot: M11 base artifact 快照。
        :param spec: 当前 Repair SkillSpec。
        :param model_proposal: M05 返回的结构化模型 proposal。
        :return: 返回身份闭合的 typed patch proposal。
        :raises SemanticRepairExecutionError: 模型输出形态或身份非法时抛出。
        """
        self._validate_model_proposal_identity(
            task=task,
            spec=spec,
            model_proposal=model_proposal,
        )
        try:
            if task.repair_lane is SemanticRepairLane.CLAIM_PROPOSITION_REPAIR:
                proposition_output = ClaimPropositionRepairOutput.model_validate(
                    model_proposal.payload,
                )
                proposal = self.patch_compiler.compile_proposition_repair(
                    repair_plan_id=repair_plan_id,
                    task=task,
                    snapshot=snapshot,
                    output=proposition_output,
                    model_metadata=model_proposal.metadata,
                )
            else:
                inventory_output = ClaimInventoryRepairOutput.model_validate(
                    model_proposal.payload,
                )
                proposal = self.patch_compiler.compile_inventory_repair(
                    repair_plan_id=repair_plan_id,
                    task=task,
                    snapshot=snapshot,
                    output=inventory_output,
                    model_metadata=model_proposal.metadata,
                )
        except (ValidationError, SemanticRepairPlanError) as error:
            raise SemanticRepairExecutionError(
                "semantic repair model output or target contract is invalid",
            ) from error
        verification = self.patch_verifier.verify(
            proposal,
            plan=plan,
            snapshot=snapshot,
        )
        if verification.state is not SemanticPatchVerificationState.ACCEPTED:
            raise SemanticRepairExecutionError(
                verification.failure_message
                or "semantic patch proposal verification failed",
            )
        return proposal

    def _validate_model_proposal_identity(
        self,
        *,
        task: SemanticRepairTask,
        spec: SkillSpec,
        model_proposal: SemanticModelProposal,
    ) -> None:
        """校验模型 proposal 与动态修复任务身份完全一致。

        :param task: 当前 M09 修复任务。
        :param spec: 当前 Repair SkillSpec。
        :param model_proposal: M05 返回的结构化模型 proposal。
        :return: 无返回值。
        :raises SemanticRepairExecutionError: 任务、SKILL 或上下文身份不一致时抛出。
        """
        execution = model_proposal.execution
        if (
            execution.task.task_id,
            execution.task.skill_id,
            execution.task.skill_version,
            execution.turn_snapshot_digest,
            model_proposal.metadata.turn_snapshot_digest,
            model_proposal.metadata.skill_id,
            model_proposal.metadata.skill_version,
        ) != (
            task.repair_task_id,
            spec.skill_id,
            spec.skill_version,
            task.turn_snapshot_digest,
            task.turn_snapshot_digest,
            spec.skill_id,
            spec.skill_version,
        ):
            raise SemanticRepairExecutionError(
                "semantic repair model proposal identity mismatch",
            )


def validate_repair_configuration(
    *,
    specs: Iterable[SkillSpec],
    renderer_registry: SkillPromptRendererRegistry,
    model_policy: SemanticGenerationModelPolicy,
) -> None:
    """校验 M10 SkillCatalog、renderer 目录与精确模型策略闭合。

    :param specs: 生产 SkillCatalog 中的全部 SkillSpec。
    :param renderer_registry: 当前生产 renderer 目录。
    :param model_policy: 覆盖两个 Repair SKILL 的精确模型策略。
    :return: 无返回值。
    :raises SemanticRepairExecutionError: 任一 Repair 配置缺失或多余时抛出。
    """
    materialized_specs = tuple(specs)
    renderer_registry.validate_catalog(materialized_specs)
    repair_identities = {
        (spec.skill_id, spec.skill_version)
        for spec in materialized_specs
        if spec.execution_family is SkillExecutionFamily.STRUCTURED_REPAIR
    }
    policy_identities = {
        (rule.skill_id, rule.skill_version) for rule in model_policy.rules
    }
    if policy_identities != repair_identities:
        raise SemanticRepairExecutionError(
            "semantic repair model policy is not closed to catalog",
        )
    for identity in repair_identities:
        model_policy.require(*identity)


__all__ = [
    "StructuredRepairSkillRunner",
    "validate_repair_configuration",
]

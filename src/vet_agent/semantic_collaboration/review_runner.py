"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/review_runner.py
作用：实现受限语义协作 DAG M08 的结构化 Review 执行器。
范围：覆盖 M07 accepted Claim Inventory 后置审查、TurnSnapshot digest 校验、
      Coverage-first 路由、单 claim Faithfulness fan-out、M05 Gateway 调用、
      deterministic verifier 与 outcome 聚合。
说明：本文件不修改 claims、不做 evidence binding、不提交 M11 artifact、
      不生成修复 patch、不调用问诊或临床安全领域，也不提供隐藏回退。
=============================================================================
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import ValidationError

from .catalog import SkillRegistry
from .contracts import SkillExecutionFamily, SkillSpec
from .errors import SemanticReviewContractError
from .gateway import StructuredLLMGateway
from .gateway_contracts import (
    SemanticModelProposal,
    StructuredLLMCallRequest,
)
from .generation import (
    SemanticGenerationModelPolicy,
    TurnSnapshotReader,
)
from .production import (
    CLAIM_COVERAGE_REVIEW_SPEC,
    CLAIM_FAITHFULNESS_REVIEW_SPEC,
)
from .prompt_renderer import (
    SkillPromptRendererRegistry,
    SkillPromptRenderRequest,
    SkillPromptReviewContext,
)
from .review_contracts import (
    ClaimCoverageReviewDimension,
    ClaimCoverageReviewRecord,
    ClaimFaithfulnessExecutionState,
    ClaimFaithfulnessReviewRecord,
    ClaimFaithfulnessSkipReason,
    SemanticReviewBundle,
    SemanticReviewOutcome,
    build_claim_coverage_review_execution,
    build_claim_faithfulness_review_execution,
    compute_claim_digest,
)
from .review_outcome import ReviewOutcomeDeriver
from .review_verifier import SemanticReviewVerifier
from .scheduler_contracts import SemanticTaskExecutionRequest
from .snapshot import TurnSnapshotProjector
from .verifier import (
    ClaimInventoryProposalShape,
    GenerationVerificationState,
    SemanticGenerationVerificationResult,
)


class StructuredReviewSkillRunner:
    """表示 M08 Coverage / Faithfulness Review 的生产执行器。

    :return: 无返回值；执行器返回审查聚合，不产生权威 verified artifact。
    """

    def __init__(
        self,
        *,
        registry: SkillRegistry,
        renderer_registry: SkillPromptRendererRegistry,
        snapshot_reader: TurnSnapshotReader,
        projector: TurnSnapshotProjector,
        gateway: StructuredLLMGateway,
        model_policy: SemanticGenerationModelPolicy,
        verifier: SemanticReviewVerifier | None = None,
        outcome_deriver: ReviewOutcomeDeriver | None = None,
    ) -> None:
        """初始化 M08 Review 执行器的封闭依赖集合。

        :param registry: 已冻结的生产 SkillCatalog 只读门面。
        :param renderer_registry: 启动期闭合的版本化 Review renderer 目录。
        :param snapshot_reader: 按 digest 读取权威 TurnSnapshot 的端口。
        :param projector: 按 SkillSpec 生成受限上下文投影的投影器。
        :param gateway: M05 单次结构化模型网关。
        :param model_policy: Review SKILL 的精确模型策略。
        :param verifier: 可选 M08 结构验证器；为空时使用生产默认实例。
        :param outcome_deriver: 可选确定性结果派生器。
        :return: 无返回值。
        """
        self.registry = registry
        self.renderer_registry = renderer_registry
        self.snapshot_reader = snapshot_reader
        self.projector = projector
        self.gateway = gateway
        self.model_policy = model_policy
        self.verifier = verifier or SemanticReviewVerifier()
        self.outcome_deriver = outcome_deriver or ReviewOutcomeDeriver()

    async def review(
        self,
        proposal: SemanticModelProposal,
        verification: SemanticGenerationVerificationResult,
    ) -> SemanticReviewBundle:
        """审查一个 M07 accepted 的 Claim Inventory proposal。

        :param proposal: M05 返回且通过 M07 结构验证的模型 proposal。
        :param verification: M07 对同一 proposal 的权威结构验证结果。
        :return: 返回包含 Coverage、Faithfulness 和确定性路由的审查聚合。
        :raises SemanticReviewContractError: 来源身份或上下文契约非法时抛出。
        """
        claims = self._validate_source(proposal, verification)
        snapshot = await self.snapshot_reader.load(
            proposal.execution.turn_snapshot_digest,
        )
        snapshot.verify_digest(proposal.execution.turn_snapshot_digest)
        coverage_record = await self._run_coverage_review(proposal, claims)
        faithfulness_records = await self._run_faithfulness_reviews(
            proposal,
            claims,
            coverage_record,
        )
        preliminary = SemanticReviewBundle(
            run_id=proposal.execution.run_id,
            source_task_id=proposal.execution.task.task_id,
            source_attempt_number=proposal.execution.attempt_number,
            turn_snapshot_digest=proposal.execution.turn_snapshot_digest,
            source_proposal_digest=proposal.proposal_digest,
            claims=claims,
            coverage_review=coverage_record,
            faithfulness_reviews=faithfulness_records,
            clarification_gaps=(),
            aggregate_outcome=SemanticReviewOutcome.REVIEW_FAILED,
        )
        return self.outcome_deriver.derive_bundle(preliminary)

    async def _run_coverage_review(
        self,
        proposal: SemanticModelProposal,
        claims: tuple[str, ...],
    ) -> ClaimCoverageReviewRecord:
        """执行一次回合级 Coverage Review 模型调用。

        :param proposal: M07 accepted 的 Claim Inventory proposal。
        :param claims: 系统 append 的不可变 claim 元组。
        :return: 返回 Coverage Review 的验证与派生记录。
        """
        spec = self._resolve_spec(
            CLAIM_COVERAGE_REVIEW_SPEC.skill_id,
            CLAIM_COVERAGE_REVIEW_SPEC.skill_version,
        )
        execution = build_claim_coverage_review_execution(proposal, spec)
        review_proposal = await self._call_review_model(
            execution=execution,
            spec=spec,
            generated_claims=claims,
            claim_proposition=None,
        )
        verification = self.verifier.verify_coverage(
            review_proposal,
            execution,
        )
        derived = (
            None
            if verification.output is None
            else self.outcome_deriver.derive_coverage(
                verification.output,
                claim_count=len(claims),
            )
        )
        return ClaimCoverageReviewRecord(
            review_task_id=execution.task.task_id,
            skill_id=spec.skill_id,
            skill_version=spec.skill_version,
            turn_snapshot_digest=execution.turn_snapshot_digest,
            source_proposal_digest=proposal.proposal_digest,
            verification=verification,
            derived=derived,
            metadata=review_proposal.metadata,
        )

    async def _run_faithfulness_reviews(
        self,
        proposal: SemanticModelProposal,
        claims: tuple[str, ...],
        coverage_record: ClaimCoverageReviewRecord,
    ) -> tuple[ClaimFaithfulnessReviewRecord, ...]:
        """按 Coverage 结果执行有界单 claim Faithfulness Review。

        :param proposal: M07 accepted 的 Claim Inventory proposal。
        :param claims: 系统 append 的不可变 claim 元组。
        :param coverage_record: 已完成的 Coverage Review 记录。
        :return: 返回与 claim 索引一一对应的 Faithfulness 审查记录。
        """
        skip_reason = self._faithfulness_skip_reason(coverage_record, claims)
        if skip_reason is not None:
            return tuple(
                self._skipped_faithfulness_record(
                    proposal=proposal,
                    claim_index=claim_index,
                    claim=claim,
                    skip_reason=skip_reason,
                )
                for claim_index, claim in enumerate(claims)
            )
        records: list[ClaimFaithfulnessReviewRecord] = []
        for claim_index, claim in enumerate(claims):
            records.append(
                await self._run_faithfulness_review(
                    proposal=proposal,
                    claim_index=claim_index,
                    claim=claim,
                ),
            )
        return tuple(records)

    def _faithfulness_skip_reason(
        self,
        coverage_record: ClaimCoverageReviewRecord,
        claims: tuple[str, ...],
    ) -> ClaimFaithfulnessSkipReason | None:
        """根据 Coverage 结果决定是否跳过 claim 级审查。

        :param coverage_record: 当前 Coverage Review 权威记录。
        :param claims: 当前 Claim Inventory 的不可变 claim 元组。
        :return: 返回稳定跳过原因；需要执行单 claim 审查时返回 None。
        """
        if not claims:
            return ClaimFaithfulnessSkipReason.EMPTY_CLAIM_INVENTORY
        derived = coverage_record.derived
        if derived is None or coverage_record.verification.state.value == "blocked":
            return ClaimFaithfulnessSkipReason.COVERAGE_REVIEW_FAILED
        if derived.outcome is SemanticReviewOutcome.HUMAN_REVIEW_REQUIRED:
            return ClaimFaithfulnessSkipReason.HUMAN_REVIEW_REQUIRED
        if derived.outcome is not SemanticReviewOutcome.REPAIR_REQUIRED:
            return None
        claim_level_dimensions = {
            ClaimCoverageReviewDimension.UNSUPPORTED_CLAIM,
            ClaimCoverageReviewDimension.NON_SELF_CONTAINED_PROPOSITION,
        }
        if any(
            dimension in claim_level_dimensions for dimension in derived.true_dimensions
        ):
            return None
        return ClaimFaithfulnessSkipReason.INVENTORY_REPAIR_REQUIRED

    def _skipped_faithfulness_record(
        self,
        *,
        proposal: SemanticModelProposal,
        claim_index: int,
        claim: str,
        skip_reason: ClaimFaithfulnessSkipReason,
    ) -> ClaimFaithfulnessReviewRecord:
        """构造未执行单 claim 审查的显式 skipped 记录。

        :param proposal: M07 accepted 的 Claim Inventory proposal。
        :param claim_index: 当前 claim 的确定性数组序号。
        :param claim: 当前未执行 Faithfulness Review 的 proposition。
        :param skip_reason: 确定性跳过原因。
        :return: 返回不携带模型 metadata 的 skipped 审查记录。
        """
        spec = self._resolve_spec(
            CLAIM_FAITHFULNESS_REVIEW_SPEC.skill_id,
            CLAIM_FAITHFULNESS_REVIEW_SPEC.skill_version,
        )
        execution = build_claim_faithfulness_review_execution(
            proposal,
            spec,
            claim_index=claim_index,
            claim_proposition=claim,
        )
        return ClaimFaithfulnessReviewRecord(
            review_task_id=execution.task.task_id,
            skill_id=spec.skill_id,
            skill_version=spec.skill_version,
            turn_snapshot_digest=execution.turn_snapshot_digest,
            source_proposal_digest=proposal.proposal_digest,
            claim_index=claim_index,
            claim_digest=compute_claim_digest(claim),
            claim_proposition=claim,
            execution_state=ClaimFaithfulnessExecutionState.SKIPPED,
            skip_reason=skip_reason,
        )

    async def _run_faithfulness_review(
        self,
        *,
        proposal: SemanticModelProposal,
        claim_index: int,
        claim: str,
    ) -> ClaimFaithfulnessReviewRecord:
        """执行单条 claim 的 Faithfulness Review 模型调用。

        :param proposal: M07 accepted 的 Claim Inventory proposal。
        :param claim_index: 当前 claim 的确定性数组序号。
        :param claim: 当前唯一待审查 proposition。
        :return: 返回单条 claim 的验证与派生记录。
        """
        spec = self._resolve_spec(
            CLAIM_FAITHFULNESS_REVIEW_SPEC.skill_id,
            CLAIM_FAITHFULNESS_REVIEW_SPEC.skill_version,
        )
        execution = build_claim_faithfulness_review_execution(
            proposal,
            spec,
            claim_index=claim_index,
            claim_proposition=claim,
        )
        review_proposal = await self._call_review_model(
            execution=execution,
            spec=spec,
            generated_claims=None,
            claim_proposition=claim,
        )
        verification = self.verifier.verify_faithfulness(
            review_proposal,
            execution,
            claim_index=claim_index,
            claim_proposition=claim,
        )
        derived = (
            None
            if verification.output is None
            else self.outcome_deriver.derive_faithfulness_matrix(
                verification.output.faithfulness_matrix,
            )
        )
        return ClaimFaithfulnessReviewRecord(
            review_task_id=execution.task.task_id,
            skill_id=spec.skill_id,
            skill_version=spec.skill_version,
            turn_snapshot_digest=execution.turn_snapshot_digest,
            source_proposal_digest=proposal.proposal_digest,
            claim_index=claim_index,
            claim_digest=compute_claim_digest(claim),
            claim_proposition=claim,
            execution_state=(
                ClaimFaithfulnessExecutionState.COMPLETED
                if verification.state.value == "accepted"
                else ClaimFaithfulnessExecutionState.BLOCKED
            ),
            verification=verification,
            derived=derived,
            metadata=review_proposal.metadata,
        )

    async def _call_review_model(
        self,
        *,
        execution: SemanticTaskExecutionRequest,
        spec: SkillSpec,
        generated_claims: tuple[str, ...] | None,
        claim_proposition: str | None,
    ) -> SemanticModelProposal:
        """组装受限 Review prompt 并执行一次 M05 结构化调用。

        :param execution: M08 构造的权威动态任务请求。
        :param spec: 当前 Review SKILL 的权威契约。
        :param generated_claims: Coverage Review 的待审查 claims。
        :param claim_proposition: Faithfulness Review 的唯一 proposition。
        :return: 返回尚未通过 M08 verifier 的模型 proposal。
        :raises SemanticReviewContractError: renderer、上下文或模型策略缺失时抛出。
        """
        if (generated_claims is None) == (claim_proposition is None):
            raise SemanticReviewContractError(
                "review model call requires exactly one review subject",
            )
        snapshot = await self.snapshot_reader.load(execution.turn_snapshot_digest)
        snapshot.verify_digest(execution.turn_snapshot_digest)
        projection = self.projector.project(snapshot, spec.context_contract)
        renderer = self.renderer_registry.require(
            spec.skill_id,
            spec.skill_version,
        )
        if generated_claims is not None:
            request = SkillPromptRenderRequest(
                execution=execution,
                spec=spec,
                projection=projection,
                review_context=SkillPromptReviewContext(
                    generated_claims=generated_claims,
                ),
            )
        else:
            if claim_proposition is None:
                raise SemanticReviewContractError(
                    "faithfulness review claim proposition is missing",
                )
            request = SkillPromptRenderRequest(
                execution=execution,
                spec=spec,
                projection=projection,
                review_context=SkillPromptReviewContext(
                    claim_proposition=claim_proposition,
                ),
            )
        prompt = renderer.render(request)
        model_rule = self.model_policy.require(
            spec.skill_id,
            spec.skill_version,
        )
        return await self.gateway.generate(
            StructuredLLMCallRequest(
                execution=execution,
                prompt=prompt,
                model=model_rule.model,
                temperature=model_rule.temperature,
                timeout_seconds=model_rule.timeout_seconds,
            ),
        )

    def _resolve_spec(
        self,
        skill_id: str,
        skill_version: str,
    ) -> SkillSpec:
        """解析并校验当前任务绑定的权威 Review SkillSpec。

        :param skill_id: Review SKILL 稳定标识。
        :param skill_version: Review SKILL 精确版本。
        :return: 返回生产 SkillCatalog 中的 Review 契约。
        :raises SemanticReviewContractError: SKILL 缺失或非结构化审查任务时抛出。
        """
        try:
            spec = self.registry.require(skill_id, skill_version)
        except Exception as error:
            raise SemanticReviewContractError(
                "semantic review skill is not registered",
            ) from error
        if spec.execution_family is not SkillExecutionFamily.STRUCTURED_REVIEW:
            raise SemanticReviewContractError(
                "semantic review runner requires structured review skill",
            )
        return spec

    def _validate_source(
        self,
        proposal: SemanticModelProposal,
        verification: SemanticGenerationVerificationResult,
    ) -> tuple[str, ...]:
        """验证待审查来源是 M07 accepted 的 Claim Inventory。

        :param proposal: M05 返回的生成 proposal。
        :param verification: M07 对同一 proposal 的结构验证结果。
        :return: 返回从权威 payload 提取的不可变 claim 元组。
        :raises SemanticReviewContractError: 来源身份、验证状态或 claim 形态非法时抛出。
        """
        task = proposal.execution.task
        if task.skill_id != "claim_inventory":
            raise SemanticReviewContractError(
                "semantic review source is not claim inventory",
            )
        if (
            verification.task_id != task.task_id
            or verification.skill_id != task.skill_id
            or verification.state is not GenerationVerificationState.ACCEPTED
        ):
            raise SemanticReviewContractError(
                "semantic review source is not accepted by M07",
            )
        try:
            shape = ClaimInventoryProposalShape.model_validate(proposal.payload)
        except ValidationError as error:
            raise SemanticReviewContractError(
                "semantic review source claim shape is invalid",
            ) from error
        claims = tuple(shape.claims)
        if verification.claim_count != len(claims):
            raise SemanticReviewContractError(
                "semantic review source claim count mismatch",
            )
        return claims


def validate_review_configuration(
    *,
    specs: Iterable[SkillSpec],
    renderer_registry: SkillPromptRendererRegistry,
    model_policy: SemanticGenerationModelPolicy,
) -> None:
    """校验 M08 SkillCatalog、renderer 与精确模型策略闭合。

    :param specs: 生产 SkillCatalog 中的全部 SkillSpec。
    :param renderer_registry: 当前生产 renderer 目录。
    :param model_policy: 覆盖 Review SKILL 的精确模型策略。
    :return: 无返回值。
    :raises SemanticReviewContractError: 任一 Review 配置缺失或多余时抛出。
    """
    materialized_specs = tuple(specs)
    renderer_registry.validate_catalog(materialized_specs)
    review_identities = {
        (spec.skill_id, spec.skill_version)
        for spec in materialized_specs
        if spec.execution_family is SkillExecutionFamily.STRUCTURED_REVIEW
    }
    policy_identities = {
        (rule.skill_id, rule.skill_version) for rule in model_policy.rules
    }
    if policy_identities != review_identities:
        raise SemanticReviewContractError(
            "semantic review model policy is not closed to catalog",
        )
    for spec in materialized_specs:
        if spec.execution_family is not SkillExecutionFamily.STRUCTURED_REVIEW:
            continue
        model_policy.require(spec.skill_id, spec.skill_version)

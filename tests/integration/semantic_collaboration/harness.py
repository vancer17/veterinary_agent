"""
=============================================================================
文件：tests/integration/semantic_collaboration/harness.py
作用：组装受限语义协作 DAG Pre-M11 集成测试的测试-only 组件与生产 runner。
范围：覆盖 TurnSnapshot fixture 构建、M03 Plan IR、M05/M06/M08/M10 runner 组合、
      固定 proposal 构造、M09 修复计划、M11 base snapshot 显式替身和 M04 调度执行器。
说明：本模块不得进入 src 生产包；所有 TODO 均保持 Fail Fast 语义，不伪装权威
      artifact，不访问问诊状态、临床安全、长期记忆或历史实验 runner。
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

from vet_agent import Settings
from vet_agent.runtime import QwenClient, StructuredChatResponse
from vet_agent.semantic_collaboration import (
    CLAIM_COVERAGE_REVIEW_SPEC,
    CLAIM_FAITHFULNESS_REVIEW_SPEC,
    CLAIM_INVENTORY_REPAIR_SPEC,
    CLAIM_INVENTORY_SPEC,
    CLAIM_PROPOSITION_REPAIR_SPEC,
    TURN_INTENT_SPEC,
    BoundedConversationHistoryReader,
    BoundedHistoryReadResult,
    BoundedHistoryReadStatus,
    DAGTaskExecutionResult,
    DAGTaskTerminalState,
    DeterministicPlanCompiler,
    GenerationVerificationState,
    OriginalTextExtractionPolicy,
    OriginalUserText,
    PlanValidator,
    RepairTargetArtifactSnapshot,
    RepairTargetSnapshotResolver,
    SemanticGenerationModelPolicy,
    SemanticGenerationModelRule,
    SemanticGenerationVerificationResult,
    SemanticGenerationVerifier,
    SemanticModelProposal,
    SemanticRepairPlan,
    SemanticRepairPlanner,
    SemanticRepairPlanRoute,
    SemanticReviewBundle,
    SemanticTaskExecutionRequest,
    SemanticTaskExecutor,
    SkillFailureCode,
    StructuredGenerationSkillRunner,
    StructuredLLMGateway,
    StructuredRepairSkillRunner,
    StructuredReviewSkillRunner,
    TrustedPetContext,
    TrustedPetContextReader,
    TrustedPetContextSource,
    TrustedPetProfile,
    TurnSnapshot,
    TurnSnapshotBudget,
    TurnSnapshotBudgetUnit,
    TurnSnapshotBuilder,
    TurnSnapshotBuildRequest,
    TurnSnapshotProjector,
    TurnSnapshotReader,
    TurnSnapshotSourceRequest,
    TurnSnapshotSourceScope,
    ValidatedPlan,
    VerifiedPriorFactReader,
    VerifiedPriorFactSummary,
    VerifiedPriorFactSummaryStatus,
    build_production_plan_policy,
    build_production_prompt_renderer_registry,
    build_production_skill_catalog,
    compute_review_bundle_digest,
)

from .contracts import ExternalSemanticTestConfig


class StaticHistoryReader(BoundedConversationHistoryReader):
    """提供固定无上一轮追问的 TurnSnapshot 历史来源。

    :return: 无返回值；该测试组件不读取会话数据库或问诊状态。
    """

    async def read(
        self,
        request: TurnSnapshotSourceRequest,
    ) -> BoundedHistoryReadResult:
        """返回显式无上一轮历史的受限结果。

        :param request: TurnSnapshot 来源读取请求。
        :return: 返回 no_previous_turn 历史结果。
        """
        return BoundedHistoryReadResult(
            status=BoundedHistoryReadStatus.NO_PREVIOUS_TURN,
        )


class StaticPriorFactReader(VerifiedPriorFactReader):
    """提供固定无已验证事实摘要的 TurnSnapshot 来源。

    :return: 无返回值；M11 未实现前不伪造历史 verified claims。
    """

    async def read(
        self,
        request: TurnSnapshotSourceRequest,
    ) -> VerifiedPriorFactSummary:
        """返回显式无已验证 claim 的摘要状态。

        :param request: TurnSnapshot 来源读取请求。
        :return: 返回 no_verified_claims 事实摘要。
        """
        return VerifiedPriorFactSummary(
            status=VerifiedPriorFactSummaryStatus.NO_VERIFIED_CLAIMS,
        )


class StaticPetContextReader(TrustedPetContextReader):
    """提供固定英短猫画像的测试可信上下文来源。

    :return: 无返回值；该组件不读取请求侧自报宠物资料。
    """

    async def read(
        self,
        request: TurnSnapshotSourceRequest,
    ) -> TrustedPetContext:
        """返回白名单可信宠物上下文。

        :param request: TurnSnapshot 来源读取请求。
        :return: 返回英短猫测试画像。
        """
        return TrustedPetContext(
            source=TrustedPetContextSource.SCOPE_CONTEXT_SERVICE,
            profile=TrustedPetProfile(
                species="cat",
                breed="British Shorthair",
                age_text="2 years",
                weight_kg=4.5,
                sex="female",
                neutered=True,
            ),
        )


class StaticTurnSnapshotReader(TurnSnapshotReader):
    """按 digest 返回固定 TurnSnapshot 的集成测试读取器。

    :return: 无返回值；该组件不是持久化 TurnSnapshot reader 的生产实现。
    """

    def __init__(self, snapshot: TurnSnapshot) -> None:
        """初始化固定快照读取器。

        :param snapshot: 当前测试用不可变 TurnSnapshot。
        :return: 无返回值。
        """
        self.snapshot = snapshot

    async def load(self, turn_snapshot_digest: str) -> TurnSnapshot:
        """读取并校验指定摘要的 TurnSnapshot。

        :param turn_snapshot_digest: 任务绑定的上下文摘要。
        :return: 返回 digest 匹配的固定 TurnSnapshot。
        :raises ValueError: 请求摘要与固定快照不一致时抛出。
        """
        if turn_snapshot_digest != self.snapshot.context_digest:
            raise ValueError("unexpected integration TurnSnapshot digest")
        return self.snapshot


@dataclass
class FixedPayloadTransport:
    """按调用顺序返回固定结构化 payload 的测试传输组件。

    :return: 无返回值；该组件不发起网络请求、不重试、不清洗响应。
    """

    payloads: tuple[dict[str, object], ...]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def structured_once(
        self,
        messages: list[dict[str, str]],
        *,
        json_schema: dict[str, Any],
        schema_name: str,
        model: str,
        temperature: float,
        timeout_seconds: float | None,
    ) -> StructuredChatResponse:
        """记录一次调用并返回下一个固定 payload。

        :param messages: OpenAI 兼容消息列表。
        :param json_schema: 权威输出 JSON Schema。
        :param schema_name: 稳定 schema 名称。
        :param model: 精确请求模型名。
        :param temperature: 采样温度。
        :param timeout_seconds: 可选调用超时。
        :return: 返回当前调用的固定结构化响应。
        :raises AssertionError: 固定 payload 序列耗尽时抛出。
        """
        self.calls.append(
            {
                "messages": messages,
                "json_schema": json_schema,
                "schema_name": schema_name,
                "model": model,
                "temperature": temperature,
                "timeout_seconds": timeout_seconds,
            },
        )
        if not self.payloads:
            raise AssertionError("fixed payload transport sequence is exhausted")
        payload, *remaining = self.payloads
        self.payloads = tuple(remaining)
        return StructuredChatResponse(
            content=payload,
            requested_model=model,
            response_model=model,
            response_id=f"fixed-response-{len(self.calls)}",
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            usage_available=True,
        )


def build_qwen_transport(
    config: ExternalSemanticTestConfig,
) -> QwenClient:
    """构造真实 LiteLLM 单次结构化传输客户端。

    :param config: 外部集成测试配置。
    :return: 返回无 fallback 的 QwenClient。
    :raises RuntimeError: LiteLLM 配置不可用时抛出。
    """
    settings = Settings(
        litellm_api_key=config.litellm_api_key,
        litellm_base_url=config.litellm_base_url,
        request_timeout_seconds=config.model_timeout_seconds,
        qwen_fallback_models=(),
        qwen_max_retries=0,
    )
    client = QwenClient(settings)
    if not client.available:
        raise RuntimeError("LiteLLM transport is not configured")
    return client


async def build_snapshot(
    current_turn: str,
    *,
    identity_suffix: str | None = None,
) -> TurnSnapshot:
    """构建测试专用的不可变 TurnSnapshot。

    :param current_turn: 当前用户回合原文。
    :param identity_suffix: 可选唯一身份后缀，用于避免 Temporal workflow 重复。
    :return: 返回通过 digest 校验的 TurnSnapshot。
    """
    suffix = identity_suffix or uuid4().hex
    builder = TurnSnapshotBuilder(
        history_reader=StaticHistoryReader(),
        prior_fact_reader=StaticPriorFactReader(),
        pet_context_reader=StaticPetContextReader(),
        budget=TurnSnapshotBudget(
            budget_unit=TurnSnapshotBudgetUnit.UNICODE_CODEPOINTS,
            max_original_user_text_chars=1000,
            max_last_question_chars=1000,
            max_verified_prior_fact_chars=1000,
            max_trusted_pet_context_chars=1000,
            max_total_context_chars=4000,
        ),
    )
    result = await builder.build(
        TurnSnapshotBuildRequest(
            scope=TurnSnapshotSourceScope(
                user_id=f"integration-user-{suffix}",
                session_id=f"integration-session-{suffix}",
                pet_id=f"integration-pet-{suffix}",
            ),
            turn_id=f"integration-turn-{suffix}",
            turn_index=0,
            original_user_text=OriginalUserText(
                text=current_turn,
                input_item_count=1,
                extraction_policy=OriginalTextExtractionPolicy.SINGLE_MESSAGE,
            ),
        ),
    )
    return result.snapshot


def _validated_plan_for_snapshot(
    snapshot: TurnSnapshot,
) -> ValidatedPlan:
    """为既有 TurnSnapshot 编译并校验权威 Root Plan。

    :param snapshot: 当前回合不可变 TurnSnapshot。
    :return: 返回与该快照身份闭合的 ValidatedPlan。
    :raises RuntimeError: M03 校验失败时抛出。
    """
    catalog = build_production_skill_catalog()
    registry = catalog.registry()
    policy = build_production_plan_policy(registry)
    plan = DeterministicPlanCompiler(
        registry=registry,
        policy=policy,
    ).compile(snapshot)
    validation = PlanValidator(
        registry=registry,
        policy=policy,
    ).validate(plan, snapshot)
    if validation.validated_plan is None:
        raise RuntimeError(f"integration Root Plan is invalid: {validation.failures}")
    return validation.validated_plan


async def build_plan(
    current_turn: str,
    *,
    identity_suffix: str | None = None,
) -> tuple[TurnSnapshot, ValidatedPlan]:
    """构建并校准当前生产 Root Plan。

    :param current_turn: 当前用户回合原文。
    :param identity_suffix: 可选唯一身份后缀。
    :return: 返回 TurnSnapshot 与通过 M03 校验的权威计划。
    """
    snapshot = await build_snapshot(current_turn, identity_suffix=identity_suffix)
    catalog = build_production_skill_catalog()
    registry = catalog.registry()
    policy = build_production_plan_policy(registry)
    plan = DeterministicPlanCompiler(
        registry=registry,
        policy=policy,
    ).compile(snapshot)
    validation = PlanValidator(
        registry=registry,
        policy=policy,
    ).validate(plan, snapshot)
    if validation.validated_plan is None:
        raise RuntimeError(f"integration Root Plan is invalid: {validation.failures}")
    return snapshot, validation.validated_plan


def _execution_request(
    snapshot: TurnSnapshot,
    validated_plan: ValidatedPlan,
    skill_id: str,
) -> SemanticTaskExecutionRequest:
    """从权威计划中解析指定 SKILL 的执行请求。

    :param snapshot: 当前回合 TurnSnapshot。
    :param validated_plan: 通过 M03 校验的 Root Plan。
    :param skill_id: 生产 SKILL 标识。
    :return: 返回绑定任务身份与 digest 的执行请求。
    :raises RuntimeError: 计划中找不到指定 SKILL 时抛出。
    """
    task = next(
        (
            item
            for item in validated_plan.plan.tasks
            if item.skill_id == skill_id
        ),
        None,
    )
    if task is None:
        raise RuntimeError(f"Root Plan does not contain skill: {skill_id}")
    return SemanticTaskExecutionRequest(
        run_id=validated_plan.plan.plan_id,
        attempt_number=1,
        task=task,
        turn_snapshot_digest=snapshot.context_digest,
        dependency_artifacts={},
    )


def _generation_model_policy(
    config: ExternalSemanticTestConfig,
) -> SemanticGenerationModelPolicy:
    """构造 M06 两个生成 SKILL 的精确模型策略。

    :param config: 外部集成测试配置。
    :return: 返回无 fallback 的生成模型策略。
    """
    return SemanticGenerationModelPolicy(
        rules=(
            SemanticGenerationModelRule(
                skill_id=TURN_INTENT_SPEC.skill_id,
                skill_version=TURN_INTENT_SPEC.skill_version,
                model=config.model,
                timeout_seconds=config.model_timeout_seconds,
            ),
            SemanticGenerationModelRule(
                skill_id=CLAIM_INVENTORY_SPEC.skill_id,
                skill_version=CLAIM_INVENTORY_SPEC.skill_version,
                model=config.model,
                timeout_seconds=config.model_timeout_seconds,
            ),
        ),
    )


def _review_model_policy(
    config: ExternalSemanticTestConfig,
) -> SemanticGenerationModelPolicy:
    """构造 M08 两个 Review SKILL 的精确模型策略。

    :param config: 外部集成测试配置。
    :return: 返回无 fallback 的审查模型策略。
    """
    return SemanticGenerationModelPolicy(
        rules=(
            SemanticGenerationModelRule(
                skill_id=CLAIM_COVERAGE_REVIEW_SPEC.skill_id,
                skill_version=CLAIM_COVERAGE_REVIEW_SPEC.skill_version,
                model=config.model,
                timeout_seconds=config.model_timeout_seconds,
            ),
            SemanticGenerationModelRule(
                skill_id=CLAIM_FAITHFULNESS_REVIEW_SPEC.skill_id,
                skill_version=CLAIM_FAITHFULNESS_REVIEW_SPEC.skill_version,
                model=config.model,
                timeout_seconds=config.model_timeout_seconds,
            ),
        ),
    )


def _repair_model_policy(
    config: ExternalSemanticTestConfig,
) -> SemanticGenerationModelPolicy:
    """构造 M10 两个 Repair SKILL 的精确模型策略。

    :param config: 外部集成测试配置。
    :return: 返回无 fallback 的修复模型策略。
    """
    return SemanticGenerationModelPolicy(
        rules=(
            SemanticGenerationModelRule(
                skill_id=CLAIM_INVENTORY_REPAIR_SPEC.skill_id,
                skill_version=CLAIM_INVENTORY_REPAIR_SPEC.skill_version,
                model=config.model,
                timeout_seconds=config.model_timeout_seconds,
            ),
            SemanticGenerationModelRule(
                skill_id=CLAIM_PROPOSITION_REPAIR_SPEC.skill_id,
                skill_version=CLAIM_PROPOSITION_REPAIR_SPEC.skill_version,
                model=config.model,
                timeout_seconds=config.model_timeout_seconds,
            ),
        ),
    )


@dataclass(frozen=True)
class GenerationRunResult:
    """表示一次真实 M06 生成与 M07 验证的组合结果。

    :return: 无返回值；该结果不是 artifact，也不能进入领域投影。
    """

    snapshot: TurnSnapshot
    validated_plan: ValidatedPlan
    intent_proposal: SemanticModelProposal
    claim_proposal: SemanticModelProposal
    intent_verification: SemanticGenerationVerificationResult
    claim_verification: SemanticGenerationVerificationResult


async def run_generation(
    current_turn: str,
    *,
    config: ExternalSemanticTestConfig,
    identity_suffix: str | None = None,
) -> GenerationRunResult:
    """执行真实 M06 Turn Intent 与 Claim Inventory 生成。

    :param current_turn: 当前用户回合原文。
    :param config: 外部集成测试配置。
    :param identity_suffix: 可选唯一身份后缀。
    :return: 返回两个模型 proposal 及对应 M07 结构验证结果。
    """
    snapshot, validated_plan = await build_plan(
        current_turn,
        identity_suffix=identity_suffix,
    )
    catalog = build_production_skill_catalog()
    transport = build_qwen_transport(config)
    runner = StructuredGenerationSkillRunner(
        registry=catalog.registry(),
        renderer_registry=build_production_prompt_renderer_registry(),
        snapshot_reader=StaticTurnSnapshotReader(snapshot),
        projector=TurnSnapshotProjector(),
        gateway=StructuredLLMGateway(
            registry=catalog.registry(),
            transport=transport,
        ),
        model_policy=_generation_model_policy(config),
    )
    intent_proposal = await runner.generate(
        _execution_request(snapshot, validated_plan, TURN_INTENT_SPEC.skill_id),
    )
    claim_proposal = await runner.generate(
        _execution_request(snapshot, validated_plan, CLAIM_INVENTORY_SPEC.skill_id),
    )
    verifier = SemanticGenerationVerifier()
    return GenerationRunResult(
        snapshot=snapshot,
        validated_plan=validated_plan,
        intent_proposal=intent_proposal,
        claim_proposal=claim_proposal,
        intent_verification=verifier.verify(intent_proposal),
        claim_verification=verifier.verify(claim_proposal),
    )


async def build_claim_inventory_proposal(
    snapshot: TurnSnapshot,
    claims: tuple[str, ...],
    *,
    model: str = "qwen-plus",
) -> SemanticModelProposal:
    """用固定 claims 构造 M05 Claim Inventory proposal。

    :param snapshot: 当前回合 TurnSnapshot。
    :param claims: 人工审核 fixture 中的固定 claim 集合。
    :param model: 固定传输使用的模型名。
    :return: 返回通过 M05 schema 与身份校验的模型 proposal。
    """
    validated_plan = _validated_plan_for_snapshot(snapshot)
    catalog = build_production_skill_catalog()
    transport = FixedPayloadTransport(payloads=({"claims": list(claims)},))
    runner = StructuredGenerationSkillRunner(
        registry=catalog.registry(),
        renderer_registry=build_production_prompt_renderer_registry(),
        snapshot_reader=StaticTurnSnapshotReader(snapshot),
        projector=TurnSnapshotProjector(),
        gateway=StructuredLLMGateway(
            registry=catalog.registry(),
            transport=transport,
        ),
        model_policy=SemanticGenerationModelPolicy(
            rules=(
                SemanticGenerationModelRule(
                    skill_id=CLAIM_INVENTORY_SPEC.skill_id,
                    skill_version=CLAIM_INVENTORY_SPEC.skill_version,
                    model=model,
                ),
            ),
        ),
    )
    return await runner.generate(
        _execution_request(snapshot, validated_plan, CLAIM_INVENTORY_SPEC.skill_id),
    )


def build_review_runner(
    snapshot: TurnSnapshot,
    *,
    transport: QwenClient | FixedPayloadTransport,
    config: ExternalSemanticTestConfig,
) -> StructuredReviewSkillRunner:
    """构造绑定真实或固定传输的 M08 Review Runner。

    :param snapshot: 当前回合 TurnSnapshot。
    :param transport: 真实 QwenClient 或固定 payload 传输。
    :param config: 外部集成测试配置。
    :return: 返回可执行 M08 审查的生产 Runner。
    """
    catalog = build_production_skill_catalog()
    return StructuredReviewSkillRunner(
        registry=catalog.registry(),
        renderer_registry=build_production_prompt_renderer_registry(),
        snapshot_reader=StaticTurnSnapshotReader(snapshot),
        projector=TurnSnapshotProjector(),
        gateway=StructuredLLMGateway(
            registry=catalog.registry(),
            transport=transport,
        ),
        model_policy=_review_model_policy(config),
    )


def _empty_coverage_payload() -> dict[str, object]:
    """构造全部 false 的 Coverage Review payload。

    :return: 返回符合 M08 权威 schema 的矩阵。
    """
    return {
        "coverage_matrix": {
            "存在漏抽显式事实": False,
            "存在多事实合并": False,
            "存在重复claim": False,
            "存在原文不支持的claim": False,
            "存在非自包含proposition": False,
            "存在shared scope拆分错误": False,
            "未分类覆盖问题": False,
        },
        "missing_claim_candidates": [],
    }


def _empty_faithfulness_payload() -> dict[str, object]:
    """构造全部 false 的 Faithfulness Review payload。

    :return: 返回符合 M08 权威 schema 的矩阵。
    """
    return {
        "faithfulness_matrix": {
            "主体或指代范围改变": False,
            "否定方向改变": False,
            "否定范围改变": False,
            "正常状态误写为否认": False,
            "事实类型改变": False,
            "时间范围改变": False,
            "频率或数量改变": False,
            "程度或强度改变": False,
            "确定性改变": False,
            "因果关系改变": False,
            "医学推断或建议添加": False,
            "命题不自包含": False,
            "指代对象不明": False,
            "时间基准不明": False,
            "否定范围不明": False,
            "比较基线不明": False,
            "未分类语义改变": False,
        },
    }


def _coverage_payload(
    **overrides: bool,
) -> dict[str, object]:
    """构造带 true 维度的 Coverage Review payload。

    :param overrides: 需要置为 true 的中文维度。
    :return: 返回覆盖矩阵 payload。
    """
    payload = _empty_coverage_payload()
    matrix = payload["coverage_matrix"]
    if isinstance(matrix, dict):
        matrix.update(overrides)
    return payload


def _faithfulness_payload(
    **overrides: bool,
) -> dict[str, object]:
    """构造带 true 维度的 Faithfulness Review payload。

    :param overrides: 需要置为 true 的中文维度。
    :return: 返回语义漂移矩阵 payload。
    """
    payload = _empty_faithfulness_payload()
    matrix = payload["faithfulness_matrix"]
    if isinstance(matrix, dict):
        matrix.update(overrides)
    return payload


async def build_real_review_bundle(
    current_turn: str,
    claims: tuple[str, ...],
    *,
    config: ExternalSemanticTestConfig,
    identity_suffix: str | None = None,
) -> tuple[TurnSnapshot, SemanticModelProposal, SemanticReviewBundle]:
    """用固定 claims 与真实 M08 模型构造 Review Bundle。

    :param current_turn: 当前用户回合原文。
    :param claims: 固定 claim 集合。
    :param config: 外部集成测试配置。
    :param identity_suffix: 可选唯一身份后缀。
    :return: 返回 TurnSnapshot、source proposal 与 M08 Review Bundle。
    """
    snapshot = await build_snapshot(current_turn, identity_suffix=identity_suffix)
    proposal = await build_claim_inventory_proposal(snapshot, claims, model=config.model)
    verification = SemanticGenerationVerifier().verify(proposal)
    if verification.state is not GenerationVerificationState.ACCEPTED:
        raise RuntimeError("fixed claim inventory proposal is structurally invalid")
    bundle = await build_review_runner(
        snapshot,
        transport=build_qwen_transport(config),
        config=config,
    ).review(proposal, verification)
    return snapshot, proposal, bundle


@dataclass(frozen=True)
class FixedReviewPlanCase:
    """表示用固定 M08 矩阵校准 M09 路由的测试上下文。

    :return: 无返回值；该上下文不调用真实模型，也不生成权威 artifact。
    """

    snapshot: TurnSnapshot
    proposal: SemanticModelProposal
    bundle: SemanticReviewBundle
    plan: SemanticRepairPlan


async def build_fixed_review_plan_case(
    current_turn: str,
    claims: tuple[str, ...],
    *,
    config: ExternalSemanticTestConfig,
    coverage_true_dimensions: tuple[str, ...] = (),
    faithfulness_true_dimensions: tuple[str, ...] = (),
) -> FixedReviewPlanCase:
    """用固定 M08 布尔矩阵构造 M09 路由校准上下文。

    :param current_turn: 当前用户回合原文。
    :param claims: 固定 claim proposition 集合。
    :param config: 外部集成测试配置，仅用于闭合模型策略身份。
    :param coverage_true_dimensions: 需要置 true 的 Coverage 维度。
    :param faithfulness_true_dimensions: 需要置 true 的 Faithfulness 维度。
    :return: 返回 M08 Bundle 与 M09 Repair Plan。
    """
    snapshot = await build_snapshot(current_turn)
    proposal = await build_claim_inventory_proposal(
        snapshot,
        claims,
        model=config.model,
    )
    verification = SemanticGenerationVerifier().verify(proposal)
    if verification.state is not GenerationVerificationState.ACCEPTED:
        raise RuntimeError("fixed review planning proposal is structurally invalid")

    coverage_payload = _coverage_payload(
        **{dimension: True for dimension in coverage_true_dimensions},
    )
    payloads: list[dict[str, object]] = [coverage_payload]
    if not coverage_true_dimensions:
        payloads.extend(
            _faithfulness_payload(
                **{dimension: True for dimension in faithfulness_true_dimensions},
            )
            for _ in claims
        )
    transport = FixedPayloadTransport(payloads=tuple(payloads))
    bundle = await build_review_runner(
        snapshot,
        transport=transport,
        config=config,
    ).review(proposal, verification)
    plan = SemanticRepairPlanner(
        registry=build_production_skill_catalog().registry(),
    ).plan(bundle)
    return FixedReviewPlanCase(
        snapshot=snapshot,
        proposal=proposal,
        bundle=bundle,
        plan=plan,
    )


def build_repair_runner(
    snapshot: TurnSnapshot,
    target_snapshot: RepairTargetArtifactSnapshot,
    *,
    transport: QwenClient,
    config: ExternalSemanticTestConfig,
) -> StructuredRepairSkillRunner:
    """构造绑定真实 LiteLLM 的 M10 Repair Runner。

    :param snapshot: 当前回合 TurnSnapshot。
    :param target_snapshot: 显式 M11 base snapshot 测试替身。
    :param transport: 真实 QwenClient。
    :param config: 外部集成测试配置。
    :return: 返回可执行 M10 修复的生产 Runner。
    """
    catalog = build_production_skill_catalog()
    return StructuredRepairSkillRunner(
        registry=catalog.registry(),
        renderer_registry=build_production_prompt_renderer_registry(),
        snapshot_reader=StaticTurnSnapshotReader(snapshot),
        target_snapshot_resolver=FixedRepairTargetSnapshotResolver(target_snapshot),
        projector=TurnSnapshotProjector(),
        gateway=StructuredLLMGateway(
            registry=catalog.registry(),
            transport=transport,
        ),
        model_policy=_repair_model_policy(config),
    )


class FixedRepairTargetSnapshotResolver(RepairTargetSnapshotResolver):
    """按固定身份返回 M11 base snapshot 的集成测试替身。

    :return: 无返回值；该组件不执行 M11 append-only commit。
    """

    def __init__(self, snapshot: RepairTargetArtifactSnapshot) -> None:
        """初始化固定 base snapshot resolver。

        :param snapshot: 显式构造的 M11 base artifact 快照替身。
        :return: 无返回值。
        """
        self.snapshot = snapshot

    async def load(
        self,
        source_proposal_digest: str,
        review_bundle_digest: str,
    ) -> RepairTargetArtifactSnapshot:
        """读取身份匹配的固定 base snapshot。

        :param source_proposal_digest: Claim Inventory proposal 摘要。
        :param review_bundle_digest: M08 Review Bundle 摘要。
        :return: 返回身份匹配的 M11 base snapshot 替身。
        :raises ValueError: 请求身份不匹配时抛出。
        """
        if (
            source_proposal_digest,
            review_bundle_digest,
        ) != (
            self.snapshot.source_proposal_digest,
            self.snapshot.review_bundle_digest,
        ):
            raise ValueError("unexpected integration repair target identity")
        return self.snapshot


def build_repair_target_snapshot(
    *,
    source_proposal_digest: str,
    review_bundle_digest: str,
    turn_snapshot_digest: str,
    claims: tuple[str, ...],
) -> RepairTargetArtifactSnapshot:
    """构造与 M08 / M09 身份闭合的 M11 base snapshot 替身。

    :param source_proposal_digest: Claim Inventory proposal 摘要。
    :param review_bundle_digest: M08 Review Bundle 摘要。
    :param turn_snapshot_digest: TurnSnapshot digest。
    :param claims: base claims 集合。
    :return: 返回显式测试替身，不表示权威 artifact。
    """
    return RepairTargetArtifactSnapshot(
        source_proposal_digest=source_proposal_digest,
        review_bundle_digest=review_bundle_digest,
        turn_snapshot_digest=turn_snapshot_digest,
        artifact_reference="integration-test://semantic-collaboration/base",
        base_version=1,
        claims=claims,
    )


@dataclass(frozen=True)
class RepairTestCase:
    """表示可直接进入真实 M10 调用的确定性修复上下文。

    :return: 无返回值；该上下文不包含 M10 模型输出。
    """

    snapshot: TurnSnapshot
    bundle: SemanticReviewBundle
    plan: SemanticRepairPlan
    target_snapshot: RepairTargetArtifactSnapshot


async def build_real_repair_case(
    current_turn: str,
    claims: tuple[str, ...],
    *,
    config: ExternalSemanticTestConfig,
    coverage_overrides: dict[str, bool] | None = None,
    faithfulness_overrides: dict[str, bool] | None = None,
    missing_claim_candidates: tuple[str, ...] = (),
) -> RepairTestCase:
    """构造固定 M08/M09 输入并保留真实 M10 调用边界。

    :param current_turn: 当前用户回合原文。
    :param claims: 固定 base claims。
    :param config: 外部集成测试配置。
    :param coverage_overrides: Coverage true 维度。
    :param faithfulness_overrides: Faithfulness true 维度。
    :param missing_claim_candidates: 非权威缺失事实提示。
    :return: 返回 M10 可消费的确定性修复测试上下文。
    """
    snapshot = await build_snapshot(current_turn)
    proposal = await build_claim_inventory_proposal(
        snapshot,
        claims,
        model=config.model,
    )
    verification = SemanticGenerationVerifier().verify(proposal)
    if verification.state is not GenerationVerificationState.ACCEPTED:
        raise RuntimeError("repair source proposal is structurally invalid")

    coverage_payload = _coverage_payload(**(coverage_overrides or {}))
    if missing_claim_candidates:
        coverage_payload["missing_claim_candidates"] = list(missing_claim_candidates)
    review_payloads: list[dict[str, object]] = [coverage_payload]
    if not coverage_overrides:
        review_payloads.extend(
            _faithfulness_payload(**(faithfulness_overrides or {}))
            for _ in claims
        )
    transport = FixedPayloadTransport(payloads=tuple(review_payloads))
    bundle = await build_review_runner(
        snapshot,
        transport=transport,
        config=config,
    ).review(proposal, verification)
    if bundle.aggregate_outcome.value != SemanticRepairPlanRoute.REPAIR_REQUIRED.value:
        raise RuntimeError(
            f"repair fixture did not derive repair_required: {bundle.aggregate_outcome}",
        )
    catalog = build_production_skill_catalog()
    plan = SemanticRepairPlanner(registry=catalog.registry()).plan(bundle)
    if plan.route is not SemanticRepairPlanRoute.REPAIR_REQUIRED or not plan.repair_tasks:
        raise RuntimeError(f"repair fixture did not create repair task: {plan.route}")
    target_snapshot = build_repair_target_snapshot(
        source_proposal_digest=bundle.source_proposal_digest,
        review_bundle_digest=compute_review_bundle_digest(bundle),
        turn_snapshot_digest=snapshot.context_digest,
        claims=bundle.claims,
    )
    return RepairTestCase(
        snapshot=snapshot,
        bundle=bundle,
        plan=plan,
        target_snapshot=target_snapshot,
    )


class SchedulerExecutorMode(StrEnum):
    """表示 M04 scheduler-only 测试执行器的显式终态模式。

    :return: 无返回值；该枚举不描述真实语义验证结论。
    """

    VERIFIED_TEST_RESULT = "verified_test_result"
    BLOCKED_TEST_RESULT = "blocked_test_result"


class SchedulerOnlySemanticTaskExecutor(SemanticTaskExecutor):
    """为 M04 Temporal 调度测试返回确定性任务终态。

    :return: 无返回值；该执行器不调用模型，也不生成权威 artifact。
    """

    def __init__(
        self,
        *,
        mode: SchedulerExecutorMode = SchedulerExecutorMode.VERIFIED_TEST_RESULT,
    ) -> None:
        """初始化 scheduler-only 执行器。

        :param mode: 当前测试终态模式。
        :return: 无返回值。
        """
        self.mode = mode

    async def execute(
        self,
        request: SemanticTaskExecutionRequest,
    ) -> DAGTaskExecutionResult:
        """返回与当前模式匹配的确定性任务终态。

        :param request: M04 activity 传入的任务执行请求。
        :return: 返回测试专用成功或失败终态。
        """
        if self.mode is SchedulerExecutorMode.BLOCKED_TEST_RESULT:
            return DAGTaskExecutionResult(
                task_id=request.task.task_id,
                terminal_state=DAGTaskTerminalState.BLOCKED,
                failure_code=SkillFailureCode.VERIFIER_FAILED,
                failure_message="scheduler-only integration test blocked result",
            )
        return DAGTaskExecutionResult(
            task_id=request.task.task_id,
            terminal_state=DAGTaskTerminalState.VERIFIED,
            artifact_reference=(
                f"integration-test://semantic-proposal/{request.run_id}/{request.task.task_id}"
            ),
        )

"""
=============================================================================
文件：tests/test_semantic_collaboration_repair_skill.py
作用：验证受限语义协作 DAG M10 Repair SKILL 与 typed patch 生产契约。
范围：覆盖 SkillCatalog / renderer 闭合、稀疏模型输出、系统 patch compiler、
      verifier、patch set 原子应用、M11 TODO 与领域隔离负例。
说明：本测试只使用进程内测试替身，不访问 LiteLLM、Temporal、OPA、
      PostgreSQL 或任何 input_preprocessing 历史 experiment runner。
=============================================================================
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import ValidationError

from tests.test_semantic_collaboration_repair_planner import (
    _bundle,
    _planner,
)
from tests.test_semantic_collaboration_review import (
    StubSnapshotReader,
    StubStructuredResponse,
    _empty_coverage_payload,
    _faithfulness_payload,
    _snapshot,
)
from vet_agent.semantic_collaboration import (
    CLAIM_INVENTORY_REPAIR_SPEC,
    CLAIM_PROPOSITION_REPAIR_SPEC,
    ClaimInventoryRepairOutput,
    ClaimPropositionRepairOutput,
    PatchApplicationState,
    RepairTargetArtifactSnapshot,
    SemanticGenerationModelPolicy,
    SemanticGenerationModelRule,
    SemanticPatchCompiler,
    SemanticPatchOperation,
    SemanticPatchOperationType,
    SemanticPatchVerificationState,
    SemanticPatchVerifier,
    SemanticRepairExecutionError,
    SemanticRepairExecutionState,
    SemanticRepairPlan,
    SemanticRepairPlanVerifier,
    SemanticReviewBundle,
    StructuredLLMGateway,
    StructuredRepairSkillRunner,
    TODORepairPatchStore,
    TODORepairTargetSnapshotResolver,
    TurnSnapshot,
    TurnSnapshotProjector,
    apply_operations_to_claims,
    build_production_prompt_renderer_registry,
    build_production_skill_catalog,
    compute_review_bundle_digest,
    validate_repair_configuration,
)


def _repair_model_policy() -> SemanticGenerationModelPolicy:
    """构造只覆盖两个 M10 Repair SKILL 的精确模型策略。

    :return: 返回无 fallback 的 qwen-plus Repair 模型策略。
    """
    return SemanticGenerationModelPolicy(
        rules=(
            SemanticGenerationModelRule(
                skill_id=CLAIM_INVENTORY_REPAIR_SPEC.skill_id,
                skill_version=CLAIM_INVENTORY_REPAIR_SPEC.skill_version,
                model="qwen-plus",
            ),
            SemanticGenerationModelRule(
                skill_id=CLAIM_PROPOSITION_REPAIR_SPEC.skill_id,
                skill_version=CLAIM_PROPOSITION_REPAIR_SPEC.skill_version,
                model="qwen-plus",
            ),
        ),
    )


@dataclass
class StubTargetSnapshotResolver:
    """提供按身份返回固定 M11 base snapshot 的进程内替身。

    :return: 无返回值；该替身不访问持久化 artifact 存储。
    """

    snapshot: RepairTargetArtifactSnapshot

    async def load(
        self,
        source_proposal_digest: str,
        review_bundle_digest: str,
    ) -> RepairTargetArtifactSnapshot:
        """返回身份匹配的固定 base artifact 快照。

        :param source_proposal_digest: Claim Inventory proposal 摘要。
        :param review_bundle_digest: M08 Review Bundle 摘要。
        :return: 返回测试固定快照。
        :raises ValueError: 请求身份与快照不一致时抛出。
        """
        if (
            source_proposal_digest,
            review_bundle_digest,
        ) != (
            self.snapshot.source_proposal_digest,
            self.snapshot.review_bundle_digest,
        ):
            raise ValueError("unexpected repair target identity")
        return self.snapshot


@dataclass
class SequentialRepairTransport:
    """提供按调用顺序返回固定 Repair 输出的测试替身。

    :return: 无返回值；该替身不重试、不修复响应、不切换模型。
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
    ) -> StubStructuredResponse:
        """记录一次结构化 Repair 调用并返回下一个固定 payload。

        :param messages: OpenAI 兼容消息列表。
        :param json_schema: 权威输出 JSON Schema。
        :param schema_name: 稳定 schema 名称。
        :param model: 精确模型名。
        :param temperature: 采样温度。
        :param timeout_seconds: 可选调用超时。
        :return: 返回当前调用对应的结构化响应。
        :raises AssertionError: 测试 payload 序列耗尽时抛出。
        """
        self.calls.append(
            {
                "messages": messages,
                "schema_name": schema_name,
                "model": model,
            },
        )
        if not self.payloads:
            raise AssertionError("repair transport payload sequence is exhausted")
        payload, *remaining = self.payloads
        self.payloads = tuple(remaining)
        return StubStructuredResponse(
            payload,
            requested_model=model,
            response_model=model,
            response_id=f"repair-response-{len(self.calls)}",
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            usage_available=True,
        )


def _target_snapshot(
    snapshot: TurnSnapshot,
    *,
    source_proposal_digest: str,
    review_bundle_digest: str,
    claims: tuple[str, ...],
) -> RepairTargetArtifactSnapshot:
    """构造与 M08 / M09 身份闭合的 M11 base snapshot 测试替身。

    :param snapshot: 当前回合权威 TurnSnapshot。
    :param source_proposal_digest: 被修复 Claim Inventory proposal 摘要。
    :param review_bundle_digest: M08 Review Bundle 摘要。
    :param claims: base artifact 中的 claim 集合。
    :return: 返回通过契约校验的 base artifact 快照。
    """
    return RepairTargetArtifactSnapshot(
        source_proposal_digest=source_proposal_digest,
        review_bundle_digest=review_bundle_digest,
        turn_snapshot_digest=snapshot.context_digest,
        artifact_reference="artifact://semantic-collaboration/base",
        base_version=1,
        claims=claims,
    )


def _repair_runner(
    turn_snapshot: TurnSnapshot,
    target_snapshot: RepairTargetArtifactSnapshot,
    transport: SequentialRepairTransport,
) -> StructuredRepairSkillRunner:
    """构造绑定进程内替身的 M10 Repair Runner。

    :param turn_snapshot: 当前回合权威 TurnSnapshot。
    :param target_snapshot: M11 base artifact 快照替身。
    :param transport: 顺序响应结构化传输替身。
    :return: 返回可执行的 M10 Repair Runner。
    """
    catalog = build_production_skill_catalog()
    return StructuredRepairSkillRunner(
        registry=catalog.registry(),
        renderer_registry=build_production_prompt_renderer_registry(),
        snapshot_reader=StubSnapshotReader(turn_snapshot),
        target_snapshot_resolver=StubTargetSnapshotResolver(target_snapshot),
        projector=TurnSnapshotProjector(),
        gateway=StructuredLLMGateway(
            registry=catalog.registry(),
            transport=transport,
        ),
        model_policy=_repair_model_policy(),
        plan_verifier=SemanticRepairPlanVerifier(registry=catalog.registry()),
    )


def _proposition_case() -> tuple[
    SemanticRepairPlan,
    SemanticReviewBundle,
    RepairTargetArtifactSnapshot,
    TurnSnapshot,
]:
    """构造单条 claim 漂移修复的 M08/M09 测试上下文。

    :return: 返回 M09 plan、M08 bundle、base snapshot 与 TurnSnapshot。
    """
    bundle = _bundle(
        (
            {"claims": ["英短精神状态被否认"]},
            _empty_coverage_payload(),
            _faithfulness_payload(正常状态误写为否认=True),
        ),
    )
    plan = _planner().plan(bundle)
    turn_snapshot = _snapshot()
    target_snapshot = _target_snapshot(
        turn_snapshot,
        source_proposal_digest=bundle.source_proposal_digest,
        review_bundle_digest=compute_review_bundle_digest(bundle),
        claims=bundle.claims,
    )
    return plan, bundle, target_snapshot, turn_snapshot


def test_repair_configuration_is_closed() -> None:
    """验证 M10 SkillCatalog、renderer 与模型策略一一闭合。

    :return: 无返回值。
    """
    catalog = build_production_skill_catalog()

    validate_repair_configuration(
        specs=catalog.list_specs(),
        renderer_registry=build_production_prompt_renderer_registry(),
        model_policy=_repair_model_policy(),
    )

    broken_policy = SemanticGenerationModelPolicy(
        rules=(_repair_model_policy().rules[0],),
    )
    with pytest.raises(Exception, match="semantic repair model policy"):
        validate_repair_configuration(
            specs=catalog.list_specs(),
            renderer_registry=build_production_prompt_renderer_registry(),
            model_policy=broken_policy,
        )


def test_proposition_repair_outputs_only_replacement_proposition() -> None:
    """验证单 claim 修复模型输出不携带 operation 或自证维度。

    :return: 无返回值。
    """
    output = ClaimPropositionRepairOutput.model_validate(
        {"proposition": "英短精神状态正常"},
    )

    assert output.model_dump() == {"proposition": "英短精神状态正常"}
    with pytest.raises(ValidationError):
        ClaimPropositionRepairOutput.model_validate(
            {
                "proposition": "英短精神状态正常",
                "addresses_dimensions": ["正常状态误写为否认"],
            },
        )


def test_inventory_repair_accepts_sparse_delta_only() -> None:
    """验证 inventory 修复输出只能是稀疏 delta 而不是完整 claims。

    :return: 无返回值。
    """
    output = ClaimInventoryRepairOutput.model_validate(
        {
            "modified_claims": [
                {
                    "target": "c0",
                    "propositions": ["英短进食正常", "英短饮水正常"],
                }
            ],
            "added_claims": ["英短没有血便"],
        },
    )

    assert output.modified_claims[0].target == "c0"
    with pytest.raises(ValidationError):
        ClaimInventoryRepairOutput.model_validate(
            {
                "modified_claims": [],
                "added_claims": [],
                "claims": ["英短没有呕吐"],
            },
        )


def test_proposition_repair_runner_builds_verified_patch_preview() -> None:
    """验证 proposition lane 生成系统 replace patch 与原子应用预览。

    :return: 无返回值。
    """
    plan, bundle, target_snapshot, turn_snapshot = _proposition_case()
    transport = SequentialRepairTransport(
        payloads=({"proposition": "英短精神状态正常"},),
    )

    result = asyncio.run(
        _repair_runner(turn_snapshot, target_snapshot, transport).repair(
            plan,
            bundle,
        ),
    )
    prompt_text = "\n".join(
        str(message.get("content", ""))
        for call in transport.calls
        for message in call["messages"]
    )

    assert result.state is SemanticRepairExecutionState.PATCH_READY
    assert result.patch_set is not None
    assert result.preview is not None
    assert result.preview.state is PatchApplicationState.PREVIEW_READY
    assert result.preview.claims == ("英短精神状态正常",)
    assert result.preview.next_version == 2
    assert result.patch_set.patches[0].operations[0].operation is (
        SemanticPatchOperationType.REPLACE_CLAIM
    )
    assert result.patch_set.patches[0].repair_dimensions[0].value == (
        "正常状态误写为否认"
    )
    assert "<target_claim>" in prompt_text
    assert "<repair_dimensions>" in prompt_text
    assert plan.plan_id not in prompt_text
    assert target_snapshot.artifact_reference not in prompt_text


def test_inventory_repair_runner_compiles_sparse_delta() -> None:
    """验证 inventory lane 由系统推导 split/add operations。

    :return: 无返回值。
    """
    coverage_payload = _empty_coverage_payload()
    coverage_matrix = coverage_payload["coverage_matrix"]
    if isinstance(coverage_matrix, dict):
        coverage_matrix["存在shared scope拆分错误"] = True
        coverage_matrix["存在漏抽显式事实"] = True
    coverage_payload["missing_claim_candidates"] = [  # type: ignore[assignment]
        "英短没有血便"
    ]
    bundle = _bundle(
        (
            {
                "claims": [
                    "英短进食和饮水都正常",
                    "英短没有呕吐",
                    "英短大便偏软",
                ],
            },
            coverage_payload,
        ),
    )
    plan = _planner().plan(bundle)
    turn_snapshot = _snapshot()
    target_snapshot = _target_snapshot(
        turn_snapshot,
        source_proposal_digest=bundle.source_proposal_digest,
        review_bundle_digest=compute_review_bundle_digest(bundle),
        claims=bundle.claims,
    )
    transport = SequentialRepairTransport(
        payloads=(
            {
                "modified_claims": [
                    {
                        "target": "c0",
                        "propositions": ["英短进食正常", "英短饮水正常"],
                    }
                ],
                "added_claims": ["英短没有血便"],
            },
        ),
    )

    result = asyncio.run(
        _repair_runner(turn_snapshot, target_snapshot, transport).repair(
            plan,
            bundle,
        ),
    )
    operations = result.patch_set.patches[0].operations if result.patch_set else ()

    assert result.state is SemanticRepairExecutionState.PATCH_READY
    assert operations[0].operation is (
        SemanticPatchOperationType.REPLACE_CLAIM_WITH_CLAIMS
    )
    assert operations[1].operation is SemanticPatchOperationType.ADD_CLAIM
    assert result.preview is not None
    assert result.preview.claims == (
        "英短进食正常",
        "英短饮水正常",
        "英短没有呕吐",
        "英短大便偏软",
        "英短没有血便",
    )
    assert "operation" not in str(transport.calls[0]["messages"][-1])


def test_suspicious_empty_inventory_can_add_first_claim() -> None:
    """验证空 claim inventory 修复无需模型复述任何既有 claim。

    :return: 无返回值。
    """
    coverage_payload = _empty_coverage_payload()
    coverage_matrix = coverage_payload["coverage_matrix"]
    if isinstance(coverage_matrix, dict):
        coverage_matrix["存在漏抽显式事实"] = True
    coverage_payload["missing_claim_candidates"] = [  # type: ignore[assignment]
        "英短没有呕吐"
    ]
    bundle = _bundle(
        (
            {"claims": []},
            coverage_payload,
        ),
    )
    plan = _planner().plan(bundle)
    turn_snapshot = _snapshot()
    target_snapshot = _target_snapshot(
        turn_snapshot,
        source_proposal_digest=bundle.source_proposal_digest,
        review_bundle_digest=compute_review_bundle_digest(bundle),
        claims=(),
    )
    transport = SequentialRepairTransport(
        payloads=(
            {
                "modified_claims": [],
                "added_claims": ["英短没有呕吐"],
            },
        ),
    )

    result = asyncio.run(
        _repair_runner(turn_snapshot, target_snapshot, transport).repair(
            plan,
            bundle,
        ),
    )
    prompt_text = str(transport.calls[0]["messages"][-1]["content"])

    assert result.state is SemanticRepairExecutionState.PATCH_READY
    assert result.preview is not None
    assert result.preview.claims == ("英短没有呕吐",)
    assert "<claim_candidates>\nnone\n</claim_candidates>" in prompt_text


def test_no_op_replacement_is_blocked() -> None:
    """验证 proposition 修复不能用原文冒充变更。

    :return: 无返回值。
    """
    plan, bundle, target_snapshot, turn_snapshot = _proposition_case()
    transport = SequentialRepairTransport(
        payloads=({"proposition": "英短精神状态被否认"},),
    )

    with pytest.raises(SemanticRepairExecutionError):
        asyncio.run(
            _repair_runner(turn_snapshot, target_snapshot, transport).repair(
                plan,
                bundle,
            ),
        )


def test_two_proposition_repairs_apply_atomically_on_same_base_version() -> None:
    """验证多个 proposition patch 共享一个 base version 并原子应用。

    :return: 无返回值。
    """
    bundle = _bundle(
        (
            {
                "claims": [
                    "英短精神状态被否认",
                    "英短软便确定由换粮引起",
                ],
            },
            _empty_coverage_payload(),
            _faithfulness_payload(正常状态误写为否认=True),
            _faithfulness_payload(确定性改变=True),
        ),
    )
    plan = _planner().plan(bundle)
    turn_snapshot = _snapshot()
    target_snapshot = _target_snapshot(
        turn_snapshot,
        source_proposal_digest=bundle.source_proposal_digest,
        review_bundle_digest=compute_review_bundle_digest(bundle),
        claims=bundle.claims,
    )
    transport = SequentialRepairTransport(
        payloads=(
            {"proposition": "英短精神状态正常"},
            {"proposition": "英短软便可能与换粮有关"},
        ),
    )

    result = asyncio.run(
        _repair_runner(turn_snapshot, target_snapshot, transport).repair(
            plan,
            bundle,
        ),
    )

    assert len(plan.repair_tasks) == 2
    assert len(transport.calls) == 2
    assert result.state is SemanticRepairExecutionState.PATCH_READY
    assert result.patch_set is not None
    assert len(result.patch_set.patches) == 2
    assert result.preview is not None
    assert result.preview.claims == (
        "英短精神状态正常",
        "英短软便可能与换粮有关",
    )


def test_patch_verifier_blocks_snapshot_identity_drift() -> None:
    """验证 patch 信封身份漂移无法进入 M11。

    :return: 无返回值。
    """
    plan, bundle, target_snapshot, turn_snapshot = _proposition_case()
    transport = SequentialRepairTransport(
        payloads=({"proposition": "英短精神状态正常"},),
    )
    result = asyncio.run(
        _repair_runner(turn_snapshot, target_snapshot, transport).repair(
            plan,
            bundle,
        ),
    )
    assert result.patch_set is not None
    patch = result.patch_set.patches[0]
    drifted_patch = patch.model_copy(
        update={"artifact_reference": "artifact://wrong/base"},
    )

    verification = SemanticPatchVerifier().verify(
        drifted_patch,
        plan=plan,
        snapshot=target_snapshot,
    )

    assert verification.state is SemanticPatchVerificationState.BLOCKED


def test_patch_application_rejects_duplicate_targets() -> None:
    """验证多个 operation 不能覆盖同一个 base claim 目标。

    :return: 无返回值。
    """
    operations = (
        SemanticPatchOperation(
            operation=SemanticPatchOperationType.REPLACE_CLAIM,
            target_claim_index=0,
            base_claim_digest="0" * 64,
            proposition="英短精神状态正常",
        ),
        SemanticPatchOperation(
            operation=SemanticPatchOperationType.REMOVE_CLAIM,
            target_claim_index=0,
            base_claim_digest="0" * 64,
        ),
    )

    with pytest.raises(ValueError, match="patch target is duplicate"):
        apply_operations_to_claims(("英短没有呕吐",), operations)


def test_patch_compiler_rejects_missing_local_selector_target() -> None:
    """验证稀疏 selector 指向不存在 claim 时 Fail Fast。

    :return: 无返回值。
    """
    plan, _, target_snapshot, _ = _proposition_case()
    task = plan.repair_tasks[0]
    # 构造 inventory task 需要重新规划；本用例直接验证 proposition 目标 digest。
    with pytest.raises(Exception, match="proposition repair target"):
        SemanticPatchCompiler().compile_proposition_repair(
            repair_plan_id=plan.plan_id,
            task=task.model_copy(
                update={"target_claim_digest": "0" * 64},
            ),
            snapshot=target_snapshot,
            output=ClaimPropositionRepairOutput(
                proposition="英短精神状态正常",
            ),
            model_metadata=object(),  # type: ignore[arg-type]
        )


def test_todo_m11_repair_ports_fail_fast() -> None:
    """验证 M11 未接入时不伪造 snapshot 或 artifact 引用。

    :return: 无返回值。
    """
    with pytest.raises(NotImplementedError, match="M11 repair target snapshot"):
        asyncio.run(
            TODORepairTargetSnapshotResolver().load("0" * 64, "1" * 64),
        )
    with pytest.raises(NotImplementedError, match="M11 repair patch store"):
        asyncio.run(TODORepairPatchStore().commit(None, None))  # type: ignore[arg-type]

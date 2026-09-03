"""
=============================================================================
文件：tests/test_semantic_collaboration_review.py
作用：验证受限语义协作 DAG M08 Coverage / Faithfulness Review 生产契约。
范围：覆盖固定布尔矩阵、严格 schema、Review Runner、M05 Gateway 组合、
      确定性 outcome 派生、clarification gap、suspicious empty 和 M11 TODO。
说明：本测试只使用进程内测试替身，不访问 LiteLLM、Temporal、OPA、
      PostgreSQL 或任何 input_preprocessing 历史 experiment runner。
=============================================================================
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from pydantic import ValidationError

from tests.test_semantic_collaboration_generation import (
    _execution,
    _snapshot,
)
from vet_agent.semantic_collaboration import (
    CLAIM_COVERAGE_REVIEW_SPEC,
    CLAIM_FAITHFULNESS_REVIEW_SPEC,
    CLAIM_INVENTORY_SPEC,
    ClaimCoverageReviewOutput,
    ClaimFaithfulnessReviewOutput,
    GenerationVerificationState,
    ReviewOutcomeDeriver,
    ReviewVerificationState,
    SemanticGenerationModelPolicy,
    SemanticGenerationModelRule,
    SemanticGenerationVerifier,
    SemanticModelProposal,
    SemanticReviewBundle,
    SemanticReviewContractError,
    SemanticReviewOutcome,
    SkillPromptRenderRequest,
    StructuredLLMCallRequest,
    StructuredLLMGateway,
    StructuredLLMSchemaError,
    StructuredReviewSkillRunner,
    TODOReviewArtifactStore,
    TurnSnapshot,
    TurnSnapshotProjector,
    build_production_prompt_renderer_registry,
    build_production_skill_catalog,
    validate_review_configuration,
)


def _empty_coverage_payload() -> dict[str, object]:
    """构造全部 false 的 Coverage Review payload。

    :return: 返回符合 M08 权威 schema 的覆盖矩阵。
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

    :return: 返回符合 M08 权威 schema 的语义漂移矩阵。
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


def _faithfulness_payload(**overrides: bool) -> dict[str, object]:
    """构造带指定 true 维度的 Faithfulness Review payload。

    :param overrides: 需要覆盖的中文矩阵字段。
    :return: 返回符合权威 schema 的测试矩阵。
    """
    payload = _empty_faithfulness_payload()
    matrix = payload["faithfulness_matrix"]
    if isinstance(matrix, dict):
        matrix.update(overrides)
    return payload


@dataclass
class StubSnapshotReader:
    """提供按摘要返回固定 TurnSnapshot 的进程内测试替身。

    :return: 无返回值；该替身不访问持久化存储。
    """

    snapshot: TurnSnapshot

    async def load(self, turn_snapshot_digest: str) -> TurnSnapshot:
        """返回 digest 匹配的固定 TurnSnapshot。

        :param turn_snapshot_digest: 任务绑定的上下文摘要。
        :return: 返回测试固定快照。
        :raises ValueError: 摘要不匹配时抛出。
        """
        if turn_snapshot_digest != self.snapshot.context_digest:
            raise ValueError("unexpected snapshot digest")
        return self.snapshot


@dataclass
class StubStructuredResponse:
    """提供结构化模型传输所需的单次响应属性。

    :return: 无返回值；该替身不隐藏 finish reason 或 usage。
    """

    content: object
    requested_model: str
    response_model: str | None
    response_id: str | None
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    usage_available: bool


@dataclass
class SequentialReviewTransport:
    """提供按调用顺序返回固定结构化响应的测试替身。

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
        """记录一次结构化模型调用并返回下一个固定 payload。

        :param messages: OpenAI 兼容消息列表。
        :param json_schema: 权威输出 JSON Schema。
        :param schema_name: 稳定 schema 名称。
        :param model: 精确模型名。
        :param temperature: 采样温度。
        :param timeout_seconds: 可选调用超时。
        :return: 返回当前调用对应的固定结构化响应。
        :raises AssertionError: 测试 payload 序列耗尽时抛出。
        """
        self.calls.append(
            {
                "messages": messages,
                "schema_name": schema_name,
                "model": model,
                "temperature": temperature,
            },
        )
        if not self.payloads:
            raise AssertionError("review transport payload sequence is exhausted")
        payload, *remaining = self.payloads
        self.payloads = tuple(remaining)
        return StubStructuredResponse(
            payload,
            requested_model=model,
            response_model=model,
            response_id=f"response-{len(self.calls)}",
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            usage_available=True,
        )


def _review_model_policy() -> SemanticGenerationModelPolicy:
    """构造只覆盖 M08 Review SKILL 的精确模型策略。

    :return: 返回无 fallback 的 qwen-plus Review 模型策略。
    """
    return SemanticGenerationModelPolicy(
        rules=(
            SemanticGenerationModelRule(
                skill_id=CLAIM_COVERAGE_REVIEW_SPEC.skill_id,
                skill_version=CLAIM_COVERAGE_REVIEW_SPEC.skill_version,
                model="qwen-plus",
            ),
            SemanticGenerationModelRule(
                skill_id=CLAIM_FAITHFULNESS_REVIEW_SPEC.skill_id,
                skill_version=CLAIM_FAITHFULNESS_REVIEW_SPEC.skill_version,
                model="qwen-plus",
            ),
        ),
    )


def _review_runner(
    snapshot: TurnSnapshot,
    transport: SequentialReviewTransport,
) -> StructuredReviewSkillRunner:
    """构造绑定进程内传输替身的 M08 Review Runner。

    :param snapshot: 当前回合权威 TurnSnapshot。
    :param transport: 按调用顺序返回 payload 的结构化传输替身。
    :return: 返回可执行的 M08 Review Runner。
    """
    catalog = build_production_skill_catalog()
    return StructuredReviewSkillRunner(
        registry=catalog.registry(),
        renderer_registry=build_production_prompt_renderer_registry(),
        snapshot_reader=StubSnapshotReader(snapshot),
        projector=TurnSnapshotProjector(),
        gateway=StructuredLLMGateway(
            registry=catalog.registry(),
            transport=transport,
        ),
        model_policy=_review_model_policy(),
    )


def _source_proposal(
    snapshot: TurnSnapshot,
    transport: SequentialReviewTransport,
) -> SemanticModelProposal:
    """通过 M05 测试替身生成一个 Claim Inventory proposal。

    :param snapshot: 当前回合权威 TurnSnapshot。
    :param transport: 顺序响应传输替身；首个 payload 为 claims。
    :return: 返回 M05 生成且尚未 M08 审查的模型 proposal。
    """
    execution = _execution(snapshot, skill_id="claim_inventory")
    catalog = build_production_skill_catalog()
    gateway = StructuredLLMGateway(
        registry=catalog.registry(),
        transport=transport,
    )
    prompt = (
        build_production_prompt_renderer_registry()
        .require(
            CLAIM_INVENTORY_SPEC.skill_id,
            CLAIM_INVENTORY_SPEC.skill_version,
        )
        .render(
            SkillPromptRenderRequest(
                execution=execution,
                spec=CLAIM_INVENTORY_SPEC,
                projection=TurnSnapshotProjector().project(
                    snapshot,
                    CLAIM_INVENTORY_SPEC.context_contract,
                ),
            ),
        )
    )
    return asyncio.run(
        gateway.generate(
            StructuredLLMCallRequest(
                execution=execution,
                prompt=prompt,
                model="qwen-plus",
            ),
        ),
    )


def test_review_configuration_is_closed() -> None:
    """验证 M08 SkillCatalog、renderer 与精确模型策略一一闭合。

    :return: 无返回值。
    """
    catalog = build_production_skill_catalog()

    validate_review_configuration(
        specs=catalog.list_specs(),
        renderer_registry=build_production_prompt_renderer_registry(),
        model_policy=_review_model_policy(),
    )

    broken_policy = SemanticGenerationModelPolicy(
        rules=(_review_model_policy().rules[0],),
    )
    with pytest.raises(SemanticReviewContractError):
        validate_review_configuration(
            specs=catalog.list_specs(),
            renderer_registry=build_production_prompt_renderer_registry(),
            model_policy=broken_policy,
        )


def test_review_runner_returns_supported_bundle_without_verified_artifact() -> None:
    """验证 M08 Runner 可执行 Coverage 与逐 claim Faithfulness 审查。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    transport = SequentialReviewTransport(
        payloads=(
            {
                "claims": [
                    "英短没有呕吐",
                    "英短大便没有血",
                ],
            },
            _empty_coverage_payload(),
            _empty_faithfulness_payload(),
            _empty_faithfulness_payload(),
        ),
    )
    proposal = _source_proposal(snapshot, transport)
    verification = SemanticGenerationVerifier().verify(proposal)
    assert verification.state is GenerationVerificationState.ACCEPTED

    bundle = asyncio.run(
        _review_runner(snapshot, transport).review(proposal, verification),
    )
    prompt_text = "\n".join(
        str(message.get("content", ""))
        for call in transport.calls
        for message in call["messages"]
    )

    assert bundle.aggregate_outcome is SemanticReviewOutcome.SEMANTIC_REVIEW_SUPPORTED
    assert bundle.claims == ("英短没有呕吐", "英短大便没有血")
    assert bundle.coverage_review.verification.state is ReviewVerificationState.ACCEPTED
    assert all(
        record.execution_state.value == "completed"
        for record in bundle.faithfulness_reviews
    )
    assert bundle.clarification_gaps == ()
    assert len(transport.calls) == 4
    assert transport.calls[1]["schema_name"] == "claim_coverage_review_1_0_0_output"
    assert transport.calls[2]["schema_name"] == (
        "claim_faithfulness_review_1_0_0_output"
    )
    assert "<generated_claims>" in prompt_text
    assert "<claim_proposition>" in prompt_text
    assert proposal.execution.task.task_id not in prompt_text
    assert bundle.run_id == proposal.execution.run_id


def test_review_runner_routes_repair_then_clarification_and_gap() -> None:
    """验证模型漂移与来源绑定缺失会生成显式 clarification gap。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    changed = _faithfulness_payload(
        正常状态误写为否认=True,
        指代对象不明=True,
    )
    transport = SequentialReviewTransport(
        payloads=(
            {"claims": ["英短没有呕吐"]},
            _empty_coverage_payload(),
            changed,
        ),
    )
    proposal = _source_proposal(snapshot, transport)
    verification = SemanticGenerationVerifier().verify(proposal)

    bundle = asyncio.run(
        _review_runner(snapshot, transport).review(proposal, verification),
    )

    assert bundle.aggregate_outcome is (
        SemanticReviewOutcome.REPAIR_THEN_CLARIFICATION_REQUIRED
    )
    assert bundle.clarification_gaps[0].claim_proposition == "英短没有呕吐"
    assert bundle.clarification_gaps[0].required_binding_type.value == (
        "subject_reference"
    )
    assert bundle.clarification_gaps[0].model_overreach_repaired is False


def test_review_runner_preserves_suspicious_empty_without_inventing_claims() -> None:
    """验证多事实输入下的空 claim 集合进入 suspicious_empty 修复路由。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    coverage_payload = _empty_coverage_payload()
    coverage_matrix = coverage_payload["coverage_matrix"]
    if isinstance(coverage_matrix, dict):
        coverage_matrix["存在漏抽显式事实"] = True
    coverage_payload["missing_claim_candidates"] = ["英短没有呕吐"]  # type: ignore[assignment]
    transport = SequentialReviewTransport(
        payloads=(
            {"claims": []},
            coverage_payload,
        ),
    )
    proposal = _source_proposal(snapshot, transport)
    verification = SemanticGenerationVerifier().verify(proposal)

    bundle = asyncio.run(
        _review_runner(snapshot, transport).review(proposal, verification),
    )

    assert bundle.aggregate_outcome is SemanticReviewOutcome.REPAIR_REQUIRED
    assert bundle.coverage_review.derived is not None
    assert bundle.coverage_review.derived.suspicious_empty is True
    assert bundle.coverage_review.derived.no_explicit_fact is False
    assert bundle.coverage_review.derived.missing_claim_candidates == (
        ("英短没有呕吐",)
    )
    assert bundle.claims == ()
    assert bundle.faithfulness_reviews == ()


def test_review_runner_preserves_coverage_faithfulness_disagreement() -> None:
    """验证 Coverage 与 Faithfulness 冲突时保留 disagreement 终态。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    coverage_payload = _empty_coverage_payload()
    coverage_matrix = coverage_payload["coverage_matrix"]
    if isinstance(coverage_matrix, dict):
        coverage_matrix["存在原文不支持的claim"] = True
    transport = SequentialReviewTransport(
        payloads=(
            {"claims": ["英短没有呕吐"]},
            coverage_payload,
            _empty_faithfulness_payload(),
        ),
    )
    proposal = _source_proposal(snapshot, transport)
    verification = SemanticGenerationVerifier().verify(proposal)

    bundle = asyncio.run(
        _review_runner(snapshot, transport).review(proposal, verification),
    )

    assert bundle.aggregate_outcome is SemanticReviewOutcome.DISAGREEMENT
    assert bundle.clarification_gaps == ()


def test_coverage_candidates_without_missing_fact_are_internal_disagreement() -> None:
    """验证 missing hint 与覆盖矩阵自相矛盾时保留 disagreement。

    :return: 无返回值。
    """
    payload = _empty_coverage_payload()
    payload["missing_claim_candidates"] = ["英短没有呕吐"]  # type: ignore[assignment]
    output = ClaimCoverageReviewOutput.model_validate(payload)

    derived = ReviewOutcomeDeriver().derive_coverage(
        output,
        claim_count=1,
    )

    assert derived.outcome is SemanticReviewOutcome.DISAGREEMENT


def test_review_runner_fails_fast_on_invalid_coverage_schema() -> None:
    """验证 Coverage schema 失败不会被转换成审查通过。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    invalid_coverage = _empty_coverage_payload()
    coverage_matrix = invalid_coverage["coverage_matrix"]
    if isinstance(coverage_matrix, dict):
        coverage_matrix["verdict"] = "unsupported"
    transport = SequentialReviewTransport(
        payloads=(
            {"claims": ["英短没有呕吐"]},
            invalid_coverage,
        ),
    )
    proposal = _source_proposal(snapshot, transport)
    verification = SemanticGenerationVerifier().verify(proposal)

    with pytest.raises(StructuredLLMSchemaError):
        asyncio.run(_review_runner(snapshot, transport).review(proposal, verification))

    assert len(transport.calls) == 2


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            _empty_faithfulness_payload(),
            SemanticReviewOutcome.SEMANTIC_REVIEW_SUPPORTED,
        ),
        (
            _faithfulness_payload(正常状态误写为否认=True),
            SemanticReviewOutcome.REPAIR_REQUIRED,
        ),
        (
            _faithfulness_payload(时间基准不明=True),
            SemanticReviewOutcome.CLARIFICATION_REQUIRED,
        ),
        (
            _faithfulness_payload(未分类语义改变=True),
            SemanticReviewOutcome.HUMAN_REVIEW_REQUIRED,
        ),
    ],
)
def test_faithfulness_outcome_derivation_is_deterministic(
    payload: dict[str, object],
    expected: SemanticReviewOutcome,
) -> None:
    """验证 Faithfulness 布尔矩阵只由确定性规则路由。

    :param payload: 当前 Faithfulness Review payload。
    :param expected: 期望的 M08 派生终态。
    :return: 无返回值。
    """
    output = ClaimFaithfulnessReviewOutput.model_validate(payload)
    derived = ReviewOutcomeDeriver().derive_faithfulness_matrix(
        output.faithfulness_matrix,
    )

    assert derived.outcome is expected


def test_review_matrix_rejects_extra_and_non_boolean_fields() -> None:
    """验证 Review 矩阵拒绝 extra field、reason 和非 boolean 值。

    :return: 无返回值。
    """
    payload = _faithfulness_payload(正常状态误写为否认=True)
    matrix = payload["faithfulness_matrix"]
    if isinstance(matrix, dict):
        matrix["reason"] = "不应存在的自由理由"
        matrix["确定性改变"] = "false"

    with pytest.raises(ValidationError):
        ClaimFaithfulnessReviewOutput.model_validate(payload)


def test_todo_review_artifact_store_fails_fast() -> None:
    """验证 M11 未接入时不伪造权威 artifact 引用。

    :return: 无返回值。
    """
    unavailable_bundle = cast(SemanticReviewBundle, None)
    with pytest.raises(NotImplementedError, match="M11 review artifact store"):
        asyncio.run(
            TODOReviewArtifactStore().commit_review_bundle(unavailable_bundle),
        )

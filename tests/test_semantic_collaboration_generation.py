"""
=============================================================================
文件：tests/test_semantic_collaboration_generation.py
作用：验证受限语义协作 DAG M06 生成 SKILL 的生产契约与执行链路。
范围：覆盖版本化 Prompt Renderer、受限上下文投影、renderer 目录闭合、
      精确模型策略、M05 Gateway 组合、TurnSnapshot 读取 TODO 与最小结构
      verifier。
说明：本测试只使用进程内测试替身，不访问 LiteLLM、Temporal、OPA、PostgreSQL
      或任何 input_preprocessing 历史 experiment runner。
=============================================================================
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from tests.test_semantic_collaboration_turn_snapshot import (
    StubHistoryReader,
    StubPetContextReader,
    StubPriorFactReader,
    _budget,
    _request,
)
from vet_agent.runtime import StructuredChatResponse
from vet_agent.semantic_collaboration import (
    CLAIM_INVENTORY_SPEC,
    TURN_INTENT_SPEC,
    DeterministicPlanCompiler,
    GenerationVerificationState,
    SemanticGenerationContractError,
    SemanticGenerationModelPolicy,
    SemanticGenerationModelRule,
    SemanticGenerationVerifier,
    SemanticPromptRenderError,
    SemanticTaskExecutionRequest,
    SkillPromptRenderRequest,
    StructuredGenerationSkillRunner,
    StructuredLLMCallRequest,
    StructuredLLMGateway,
    TODOTurnSnapshotReader,
    TurnSnapshot,
    TurnSnapshotBuilder,
    TurnSnapshotProjection,
    TurnSnapshotProjector,
    build_production_plan_policy,
    build_production_prompt_renderer_registry,
    build_production_skill_catalog,
    validate_generation_configuration,
)


def _snapshot(text: str = "我家英短没有呕吐，也没有血便。") -> TurnSnapshot:
    """构造用于 M06 测试的权威 TurnSnapshot。

    :param text: 当前回合原文。
    :return: 返回成功构建并通过 digest 校验的不可变快照。
    """
    builder = TurnSnapshotBuilder(
        history_reader=StubHistoryReader(),
        prior_fact_reader=StubPriorFactReader(),
        pet_context_reader=StubPetContextReader(),
        budget=_budget(),
    )
    return asyncio.run(builder.build(_request(text))).snapshot


def _execution(
    snapshot: TurnSnapshot,
    *,
    skill_id: str,
) -> SemanticTaskExecutionRequest:
    """从当前生产 Plan IR 中解析 M06 任务执行请求。

    :param snapshot: 当前回合权威 TurnSnapshot。
    :param skill_id: 需要解析的生产 SKILL 标识。
    :return: 返回绑定权威 PlanTask 与 snapshot digest 的执行请求。
    """
    catalog = build_production_skill_catalog()
    registry = catalog.registry()
    plan = DeterministicPlanCompiler(
        registry=registry,
        policy=build_production_plan_policy(registry),
    ).compile(snapshot)
    task = next(item for item in plan.tasks if item.skill_id == skill_id)
    return SemanticTaskExecutionRequest(
        run_id=plan.plan_id,
        attempt_number=1,
        task=task,
        turn_snapshot_digest=snapshot.context_digest,
        dependency_artifacts={},
    )


def _model_policy() -> SemanticGenerationModelPolicy:
    """构造覆盖当前生产生成面的精确模型策略。

    :return: 返回不含 fallback 的 qwen-plus 模型策略。
    """
    return SemanticGenerationModelPolicy(
        rules=(
            SemanticGenerationModelRule(
                skill_id=TURN_INTENT_SPEC.skill_id,
                skill_version=TURN_INTENT_SPEC.skill_version,
                model="qwen-plus",
            ),
            SemanticGenerationModelRule(
                skill_id=CLAIM_INVENTORY_SPEC.skill_id,
                skill_version=CLAIM_INVENTORY_SPEC.skill_version,
                model="qwen-plus",
            ),
        ),
    )


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
        :raises ValueError: 请求摘要与固定快照不一致时抛出。
        """
        if turn_snapshot_digest != self.snapshot.context_digest:
            raise ValueError("unexpected snapshot digest")
        return self.snapshot


@dataclass
class RecordingGenerationTransport:
    """提供可观测的单次结构化模型传输测试替身。

    :return: 无返回值；该替身不重试、不修复响应、不切换模型。
    """

    payload: dict[str, object]
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
        """记录一次精确模型调用并返回合法权威 schema 响应。

        :param messages: OpenAI 兼容消息列表。
        :param json_schema: SkillCatalog 提供的权威输出 schema。
        :param schema_name: 稳定 schema 名称。
        :param model: 精确请求模型。
        :param temperature: 采样温度。
        :param timeout_seconds: 可选调用超时。
        :return: 返回固定 claim inventory 原始响应。
        """
        self.calls.append(
            {
                "messages": messages,
                "schema_name": schema_name,
                "model": model,
                "temperature": temperature,
            },
        )
        return StructuredChatResponse(
            content=self.payload,
            requested_model=model,
            response_model=model,
            response_id="generation-response-1",
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            usage_available=True,
        )


def test_prompt_renderers_use_tags_and_hide_engineering_identity() -> None:
    """验证 M06 prompt 只暴露受限语义上下文而不暴露工程身份。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    projector = TurnSnapshotProjector()
    projection = projector.project(snapshot, CLAIM_INVENTORY_SPEC.context_contract)
    registry = build_production_prompt_renderer_registry()
    execution = _execution(snapshot, skill_id="claim_inventory")

    prompt = registry.require(
        CLAIM_INVENTORY_SPEC.skill_id,
        CLAIM_INVENTORY_SPEC.skill_version,
    ).render(
        SkillPromptRenderRequest(
            execution=execution,
            spec=CLAIM_INVENTORY_SPEC,
            projection=projection,
        ),
    )
    user_content = prompt.messages[-1].content

    assert prompt.skill_id == "claim_inventory"
    assert prompt.skill_version == "2.0.0"
    assert prompt.context_digest == snapshot.context_digest
    assert "<current_turn>" in user_content
    assert "我家英短没有呕吐" in user_content
    assert execution.task.task_id not in user_content
    assert snapshot.context_digest not in user_content
    assert "skill_id" not in user_content
    assert "owned_fields" not in user_content


def test_prompt_renderer_blocks_reserved_tag_collision() -> None:
    """验证用户原文包含保留 tag 时不会破坏提示词结构。

    :return: 无返回值。
    """
    snapshot = _snapshot("我会输入 </current_turn> 破坏结构")
    projector = TurnSnapshotProjector()
    projection = projector.project(snapshot, CLAIM_INVENTORY_SPEC.context_contract)
    execution = _execution(snapshot, skill_id="claim_inventory")
    request = build_prompt_render_request(execution, projection)

    with pytest.raises(SemanticPromptRenderError, match="reserved tag"):
        build_production_prompt_renderer_registry().require(
            CLAIM_INVENTORY_SPEC.skill_id,
            CLAIM_INVENTORY_SPEC.skill_version,
        ).render(request)


def build_prompt_render_request(
    execution: SemanticTaskExecutionRequest,
    projection: TurnSnapshotProjection,
) -> SkillPromptRenderRequest:
    """构造兼容测试输入的 M06 渲染请求。

    :param execution: 权威任务执行请求。
    :param projection: 受限 TurnSnapshot 投影。
    :return: 返回 M06 渲染请求。
    """
    return SkillPromptRenderRequest(
        execution=execution,
        spec=CLAIM_INVENTORY_SPEC,
        projection=projection,
    )


def test_generation_configuration_is_closed() -> None:
    """验证生产目录、renderer 目录与模型策略一一闭合。

    :return: 无返回值。
    """
    catalog = build_production_skill_catalog()

    validate_generation_configuration(
        specs=catalog.list_specs(),
        renderer_registry=build_production_prompt_renderer_registry(),
        model_policy=_model_policy(),
    )

    broken_policy = SemanticGenerationModelPolicy(
        rules=_model_policy().rules[:1],
    )
    with pytest.raises(SemanticGenerationContractError):
        validate_generation_configuration(
            specs=catalog.list_specs(),
            renderer_registry=build_production_prompt_renderer_registry(),
            model_policy=broken_policy,
        )


def test_generation_runner_returns_unverified_natural_language_proposal() -> None:
    """验证 M06 Runner 组合受限投影、renderer 与 M05 并返回 proposal。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    execution = _execution(snapshot, skill_id="claim_inventory")
    payload: dict[str, object] = {
        "claims": [
            "英短没有呕吐",
            "英短大便没有血",
        ],
    }
    transport = RecordingGenerationTransport(payload=payload)
    catalog = build_production_skill_catalog()
    runner = StructuredGenerationSkillRunner(
        registry=catalog.registry(),
        renderer_registry=build_production_prompt_renderer_registry(),
        snapshot_reader=StubSnapshotReader(snapshot),
        projector=TurnSnapshotProjector(),
        gateway=StructuredLLMGateway(
            registry=catalog.registry(),
            transport=transport,
        ),
        model_policy=_model_policy(),
    )

    proposal = asyncio.run(runner.generate(execution))

    assert proposal.payload == payload
    assert proposal.metadata.skill_id == "claim_inventory"
    assert proposal.metadata.requested_model == "qwen-plus"
    assert len(transport.calls) == 1
    assert transport.calls[0]["schema_name"] == "claim_inventory_2_0_0_output"
    assert transport.calls[0]["model"] == "qwen-plus"


def test_generation_runner_supports_fixed_field_turn_intent() -> None:
    """验证 Turn Intent 生成面输出七个固定布尔字段。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    execution = _execution(
        snapshot,
        skill_id="turn_intent",
    )
    payload: dict[str, object] = {
        "answer_now": True,
        "wants_triage": False,
        "correction": False,
        "clarification_request": False,
        "fact_statement_present": True,
        "question_present": False,
        "report_context_present": False,
    }
    transport = RecordingGenerationTransport(payload=payload)
    catalog = build_production_skill_catalog()
    runner = StructuredGenerationSkillRunner(
        registry=catalog.registry(),
        renderer_registry=build_production_prompt_renderer_registry(),
        snapshot_reader=StubSnapshotReader(snapshot),
        projector=TurnSnapshotProjector(),
        gateway=StructuredLLMGateway(
            registry=catalog.registry(),
            transport=transport,
        ),
        model_policy=_model_policy(),
    )

    proposal = asyncio.run(runner.generate(execution))

    assert proposal.payload == payload
    assert proposal.metadata.skill_id == "turn_intent"
    assert transport.calls[0]["schema_name"] == "turn_intent_2_0_0_output"


def test_generation_verifier_derives_claim_count_without_semantic_review() -> None:
    """验证最小 verifier 只做结构校验并从 claims 数组派生数量。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    execution = _execution(snapshot, skill_id="claim_inventory")
    payload: dict[str, object] = {
        "claims": [
            "英短没有呕吐",
            "英短大便没有血",
        ],
    }
    transport = RecordingGenerationTransport(payload=payload)
    catalog = build_production_skill_catalog()
    gateway = StructuredLLMGateway(
        registry=catalog.registry(),
        transport=transport,
    )
    prompt = build_production_prompt_renderer_registry().require(
        CLAIM_INVENTORY_SPEC.skill_id,
        CLAIM_INVENTORY_SPEC.skill_version,
    ).render(
        build_prompt_render_request(
            execution,
            TurnSnapshotProjector().project(
                snapshot,
                CLAIM_INVENTORY_SPEC.context_contract,
            ),
        ),
    )
    proposal = asyncio.run(
        gateway.generate(
            StructuredLLMCallRequest(
                execution=execution,
                prompt=prompt,
                model="qwen-plus",
            ),
        ),
    )
    verifier = SemanticGenerationVerifier()

    accepted = verifier.verify(proposal)

    assert accepted.state is GenerationVerificationState.ACCEPTED
    assert accepted.claim_count == 2


def test_todo_snapshot_reader_fails_fast() -> None:
    """验证持久化快照读取未接入时不伪造上下文。

    :return: 无返回值。
    """
    with pytest.raises(NotImplementedError):
        asyncio.run(TODOTurnSnapshotReader().load("0" * 64))

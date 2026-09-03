"""
=============================================================================
文件：tests/test_semantic_collaboration_gateway.py
作用：验证受限语义协作 DAG M05 结构化 LLM Gateway 的生产契约。
范围：覆盖 Skill 契约绑定、schema digest、prompt digest、单次传输、
      严格 JSON 解析、extra field 阻断、usage 观测、失败分类和底层 Qwen
      单次调用不重试、不 fallback 的边界。
说明：本测试不依赖数据库、LiteLLM、OPA、Mem0、Temporal 或任何历史实验 runner。
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
)
from tests.test_semantic_collaboration_turn_snapshot import (
    _request as _snapshot_request,
)
from vet_agent import Settings
from vet_agent.runtime import QwenClient, StructuredChatResponse
from vet_agent.semantic_collaboration import (
    DeterministicPlanCompiler,
    SemanticChatMessage,
    SemanticTaskExecutionRequest,
    SkillPromptProjection,
    StructuredLLMCallRequest,
    StructuredLLMGateway,
    StructuredLLMGatewayContractError,
    StructuredLLMModelCallError,
    StructuredLLMResponseParseError,
    StructuredLLMSchemaError,
    StructuredModelTransport,
    TurnSnapshot,
    TurnSnapshotBuilder,
    build_production_plan_policy,
    build_production_skill_catalog,
    compute_gateway_digest,
)


@dataclass
class RecordingTransport:
    """提供可观测的单次结构化模型传输测试替身。

    :return: 无返回值；该替身不访问外部服务，也不修改响应内容。
    """

    content: object | None
    calls: list[dict[str, Any]] = field(default_factory=list)
    failure: Exception | None = None
    finish_reason: str | None = "stop"
    usage_available: bool = True

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
        """记录一次调用并返回固定原始结构化响应。

        :param messages: OpenAI 兼容消息列表。
        :param json_schema: 权威输出 JSON Schema。
        :param schema_name: 稳定 schema 名称。
        :param model: 精确请求模型。
        :param temperature: 采样温度。
        :param timeout_seconds: 可选调用超时。
        :return: 返回原始内容和完整调用 metadata。
        :raises Exception: 测试构造的传输失败。
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
        if self.failure is not None:
            raise self.failure
        prompt_tokens, completion_tokens, total_tokens = (
            (10, 20, 30) if self.usage_available else (None, None, None)
        )
        return StructuredChatResponse(
            content=self.content,
            requested_model=model,
            response_model=f"{model}:snapshot",
            response_id="response-1",
            finish_reason=self.finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            usage_available=self.usage_available,
        )


class RecordingQwenClient(QwenClient):
    """记录底层原始请求的 QwenClient 测试替身。

    :return: 无返回值；该替身不发起 HTTP 请求。
    """

    def __init__(self) -> None:
        """初始化带精确模型和 fallback 配置的测试客户端。

        :return: 无返回值。
        """
        super().__init__(
            Settings(
                litellm_api_key="test-key",
                litellm_base_url="http://litellm.test/v1",
                qwen_fallback_models=("qwen-fallback",),
                qwen_max_retries=5,
                qwen_min_interval_seconds=0.0,
            ),
        )
        self.raw_calls: list[dict[str, Any]] = []

    async def _send_raw_structured_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        json_schema: dict[str, Any],
        schema_name: str,
        model: str,
        temperature: float,
        timeout_seconds: float | None,
    ) -> StructuredChatResponse:
        """记录一次原始结构化请求并返回固定响应。

        :param messages: OpenAI 兼容消息列表。
        :param json_schema: 权威 JSON Schema。
        :param schema_name: 稳定 schema 名称。
        :param model: 精确模型名称。
        :param temperature: 采样温度。
        :param timeout_seconds: 可选超时时间。
        :return: 返回固定原始响应。
        """
        self.raw_calls.append({"model": model})
        return StructuredChatResponse(
            content={"value": True},
            requested_model=model,
            response_model=model,
            response_id="response-1",
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=2,
            total_tokens=3,
            usage_available=True,
        )


def _snapshot() -> TurnSnapshot:
    """构造绑定受限来源的当前回合 TurnSnapshot。

    :return: 返回成功构建并通过 digest 校验的快照。
    """
    builder = TurnSnapshotBuilder(
        history_reader=StubHistoryReader(),
        prior_fact_reader=StubPriorFactReader(),
        pet_context_reader=StubPetContextReader(),
        budget=_budget(),
    )
    return asyncio.run(builder.build(_snapshot_request())).snapshot


def _turn_intent_execution(snapshot: TurnSnapshot) -> SemanticTaskExecutionRequest:
    """构造生产 Plan IR 中的 turn intent 任务执行请求。

    :param snapshot: 当前回合 TurnSnapshot。
    :return: 返回绑定权威 PlanTask 的执行请求。
    """
    catalog = build_production_skill_catalog()
    registry = catalog.registry()
    compiler = DeterministicPlanCompiler(
        registry=registry,
        policy=build_production_plan_policy(registry),
    )
    plan = compiler.compile(snapshot)
    task = next(item for item in plan.tasks if item.skill_id == "turn_intent")
    return SemanticTaskExecutionRequest(
        run_id=plan.plan_id,
        attempt_number=1,
        task=task,
        turn_snapshot_digest=snapshot.context_digest,
        dependency_artifacts={},
    )


def _prompt(
    execution: SemanticTaskExecutionRequest,
    *,
    context_digest: str | None = None,
) -> SkillPromptProjection:
    """构造与 turn intent 任务绑定的测试提示词投影。

    :param execution: 当前任务执行请求。
    :param context_digest: 可选覆盖的上下文 digest。
    :return: 返回通过身份契约校验或测试错配场景的提示词投影。
    """
    return SkillPromptProjection(
        skill_id=execution.task.skill_id,
        skill_version=execution.task.skill_version,
        prompt_version="1.0.0",
        context_digest=context_digest or execution.turn_snapshot_digest,
        messages=(
            SemanticChatMessage(
                role="system",
                content="你是受限语义协作 SKILL。",
            ),
            SemanticChatMessage(
                role="user",
                content="请按契约输出当前回合意图。",
            ),
        ),
    )


def _request(
    execution: SemanticTaskExecutionRequest,
    prompt: SkillPromptProjection | None = None,
) -> StructuredLLMCallRequest:
    """构造 M05 单次结构化调用请求。

    :param execution: 当前任务执行请求。
    :param prompt: 可选覆盖的提示词投影。
    :return: 返回使用精确模型的调用请求。
    """
    return StructuredLLMCallRequest(
        execution=execution,
        prompt=prompt or _prompt(execution),
        model="qwen-plus",
        temperature=0.0,
        timeout_seconds=12.0,
    )


def _gateway(transport: RecordingTransport) -> StructuredLLMGateway:
    """构造绑定生产 SkillCatalog 与测试传输端口的网关。

    :param transport: 单次结构化传输测试替身。
    :return: 返回生产契约 M05 网关。
    """
    catalog = build_production_skill_catalog()
    return StructuredLLMGateway(
        registry=catalog.registry(),
        transport=transport,
    )


def _valid_payload() -> dict[str, object]:
    """构造符合 turn intent 权威 schema 的模型 payload。

    :return: 返回包含全部已声明叶子字段的 JSON object。
    """
    return {
        "answer_now": False,
        "wants_triage": False,
        "correction": False,
        "clarification_request": False,
        "fact_statement_present": True,
        "question_present": False,
        "report_context_present": False,
    }


def test_gateway_returns_unverified_proposal_with_attempt_metadata() -> None:
    """验证网关成功返回 proposal 且保留模型调用审计元数据。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    execution = _turn_intent_execution(snapshot)
    transport = RecordingTransport(content=_valid_payload())
    request = _request(execution)
    proposal = asyncio.run(_gateway(transport).generate(request))

    assert proposal.payload == _valid_payload()
    assert proposal.proposal_digest == compute_gateway_digest(proposal.payload)
    assert proposal.metadata.run_id == execution.run_id
    assert proposal.metadata.attempt_number == 1
    assert proposal.metadata.requested_model == "qwen-plus"
    assert proposal.metadata.response_model == "qwen-plus:snapshot"
    assert proposal.metadata.usage_available is True
    assert proposal.metadata.total_tokens == 30
    assert len(transport.calls) == 1
    assert transport.calls[0]["schema_name"] == "turn_intent_2_0_0_output"
    assert transport.calls[0]["json_schema"]["additionalProperties"] is False


def test_gateway_blocks_prompt_context_digest_mismatch_before_model_call() -> None:
    """验证提示词上下文错配在模型调用前显式阻断。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    execution = _turn_intent_execution(snapshot)
    transport = RecordingTransport(content=_valid_payload())
    request = _request(
        execution,
        _prompt(execution, context_digest="0" * 64),
    )

    with pytest.raises(StructuredLLMGatewayContractError):
        asyncio.run(_gateway(transport).generate(request))
    assert transport.calls == []


def test_gateway_blocks_task_schema_digest_mismatch_before_model_call() -> None:
    """验证任务 schema digest 错配在模型调用前显式阻断。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    execution = _turn_intent_execution(snapshot)
    schema = execution.task.expected_output_schema.model_copy(
        update={"schema_digest": "0" * 64},
    )
    execution = execution.model_copy(
        update={
            "task": execution.task.model_copy(
                update={"expected_output_schema": schema},
            ),
        },
    )
    transport = RecordingTransport(content=_valid_payload())

    with pytest.raises(StructuredLLMGatewayContractError):
        asyncio.run(_gateway(transport).generate(_request(execution)))
    assert transport.calls == []


def test_gateway_rejects_deterministic_execution_family() -> None:
    """验证 deterministic SKILL 不能通过结构化模型网关执行。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    execution = _turn_intent_execution(snapshot)
    catalog = build_production_skill_catalog()
    patch_spec = catalog.require("patch_applier")
    execution = execution.model_copy(
        update={
            "task": execution.task.model_copy(
                update={
                    "skill_id": patch_spec.skill_id,
                    "skill_version": patch_spec.skill_version,
                },
            ),
        },
    )
    transport = RecordingTransport(content=_valid_payload())

    with pytest.raises(StructuredLLMGatewayContractError):
        asyncio.run(_gateway(transport).generate(_request(execution)))
    assert transport.calls == []


@pytest.mark.parametrize(
    ("content", "expected_type"),
    [
        ("not a json object", StructuredLLMResponseParseError),
        ('{"answer_now":"yes"}', StructuredLLMSchemaError),
    ],
)
def test_gateway_separates_parse_and_schema_failures(
    content: object,
    expected_type: type[Exception],
) -> None:
    """验证网关区分严格解析失败与权威 schema 失败。

    :param content: 底层传输返回的原始内容。
    :param expected_type: 预期稳定异常类型。
    :return: 无返回值。
    """
    snapshot = _snapshot()
    execution = _turn_intent_execution(snapshot)
    transport = RecordingTransport(content=content)

    with pytest.raises(expected_type):
        asyncio.run(_gateway(transport).generate(_request(execution)))
    assert len(transport.calls) == 1


def test_gateway_blocks_duplicate_json_key_and_extra_field() -> None:
    """验证重复 JSON key 与 extra field 均不被清洗或放行。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    execution = _turn_intent_execution(snapshot)
    duplicate_transport = RecordingTransport(
        content='{"answer_now":false, "answer_now":true}',
    )
    with pytest.raises(StructuredLLMResponseParseError):
        asyncio.run(_gateway(duplicate_transport).generate(_request(execution)))

    payload = _valid_payload()
    payload["forbidden"] = "extra"
    extra_transport = RecordingTransport(content=payload)
    with pytest.raises(StructuredLLMSchemaError) as error:
        asyncio.run(_gateway(extra_transport).generate(_request(execution)))
    assert error.value.schema_path == "$"


def test_gateway_keeps_missing_usage_observable_and_blocks_bad_finish() -> None:
    """验证 usage 缺失可观测且非 stop 响应不会伪装成功。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    execution = _turn_intent_execution(snapshot)
    missing_usage_transport = RecordingTransport(
        content=_valid_payload(),
        usage_available=False,
    )
    proposal = asyncio.run(
        _gateway(missing_usage_transport).generate(_request(execution)),
    )
    assert proposal.metadata.usage_available is False
    assert proposal.metadata.total_tokens is None

    finish_transport = RecordingTransport(
        content=_valid_payload(),
        finish_reason="length",
    )
    with pytest.raises(StructuredLLMModelCallError) as error:
        asyncio.run(_gateway(finish_transport).generate(_request(execution)))
    assert error.value.metadata is not None
    assert error.value.metadata.finish_reason == "length"


def test_gateway_wraps_transport_failure_with_metadata() -> None:
    """验证底层模型传输失败保留 attempt metadata 且不重试。

    :return: 无返回值。
    """
    snapshot = _snapshot()
    execution = _turn_intent_execution(snapshot)
    transport = RecordingTransport(
        content=None,
        failure=TimeoutError("model transport timeout"),
    )

    with pytest.raises(StructuredLLMModelCallError) as error:
        asyncio.run(_gateway(transport).generate(_request(execution)))
    assert error.value.metadata is not None
    assert error.value.metadata.attempt_number == 1
    assert error.value.metadata.response_model is None
    assert len(transport.calls) == 1


def test_qwen_one_shot_transport_uses_exact_model_without_retry_or_fallback() -> None:
    """验证 Qwen 单次结构化传输不触发内部重试或隐藏 fallback。

    :return: 无返回值。
    """
    client = RecordingQwenClient()
    response = asyncio.run(
        client.structured_once(
            [{"role": "user", "content": "输出 JSON"}],
            json_schema={
                "type": "object",
                "properties": {"value": {"type": "boolean"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            schema_name="test_schema",
            model="qwen-plus",
            temperature=0.0,
            timeout_seconds=1.0,
        ),
    )
    transport: StructuredModelTransport = client

    assert response.requested_model == "qwen-plus"
    assert response.content == {"value": True}
    assert client.raw_calls == [{"model": "qwen-plus"}]
    assert transport is client

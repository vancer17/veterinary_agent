##################################################################################################
# 文件: tests/vet_context_builder/test_service.py
# 作用: 验证 VetContextBuilder 完整构建、宠物隔离、TODO 降级、预算裁剪和配置快照约束。
# 边界: 使用进程内假端口，不访问数据库、网络、真实 LangGraph checkpointer 或 LogicTraceStore。
##################################################################################################

import asyncio

from pydantic import JsonValue
import pytest

from veterinary_agent.config import (
    VetContextBudgetConfig,
    VetContextBuilderSettings,
    VetContextTimeoutConfig,
)
from veterinary_agent.vet_context_builder import (
    ContextBuildStatus,
    ContextCompressionStrategy,
    ContextFactDto,
    ContextSourcePort,
    ContextSourceStatus,
    ContextSourceType,
    ContextTraceWriteStatus,
    DefaultVetContextBuilder,
    VetContextBuilderError,
    VetExecutorKey,
    VetGenerationProfile,
    VetPromptBlockType,
    build_default_context_source_ports,
)
from tests.vet_context_builder.helpers import (
    FakeContextSourcePort,
    RecordingContextTraceSink,
    as_source_ports,
    build_conversation_source_result,
    build_empty_source_result,
    build_fact_source_result,
    build_lab_source_result,
    build_request,
    build_runtime_provider,
    build_session_state,
    build_source_ref,
)


def _full_source_ports(*, pet_id: str) -> tuple[ContextSourcePort, ...]:
    """构建标准问诊完整来源测试端口。

    :param pet_id: 全部宠物级来源绑定的宠物 ID。
    :return: 覆盖 single_full 读取计划的测试端口元组。
    """

    return as_source_ports(
        build_fact_source_result(
            source_type=ContextSourceType.CORE_FACT_SNAPSHOT,
            pet_id=pet_id,
            facts={
                "species": "dog",
                "age": 5,
                "weight_kg": 12.5,
                "appetite": "reduced",
                "hydration": "normal",
                "energy_level": "reduced",
            },
            source_id="snapshot_1",
            version="snapshot.v3",
        ),
        build_empty_source_result(
            source_type=ContextSourceType.PET_PROFILE,
            pet_id=pet_id,
        ),
        build_conversation_source_result(pet_id=pet_id),
        build_lab_source_result(pet_id=pet_id),
        build_fact_source_result(
            source_type=ContextSourceType.OWNER_PREFERENCE,
            pet_id=None,
            facts={"communication_style": "concise"},
        ),
    )


def test_standard_build_produces_full_bundle_and_trace() -> None:
    """验证完整来源可生成带 P0、槽位、化验和压缩审计的 full bundle。

    :return: None。
    """

    provider = build_runtime_provider()
    task_ref = build_source_ref(
        source_type=ContextSourceType.CURRENT_TASK,
        source_id="task_context_1",
        pet_id="pet_context_1",
    )
    request = build_request(
        provider=provider,
        session_state_snapshot=build_session_state(pet_id="pet_context_1"),
        observed_facts=[
            ContextFactDto(
                key="symptom_frequency",
                value="3 times today",
                source_ref=task_ref,
            )
        ],
    )
    trace_sink = RecordingContextTraceSink()
    builder = DefaultVetContextBuilder(
        runtime_config_provider=provider,
        source_ports=_full_source_ports(pet_id="pet_context_1"),
        trace_sink=trace_sink,
    )

    bundle = asyncio.run(builder.build(request))

    block_types = {block.block_type for block in bundle.prompt_blocks}
    assert bundle.status is ContextBuildStatus.FULL
    assert bundle.adapter_invoked is True
    assert bundle.trace_delivery_status is ContextTraceWriteStatus.RECORDED
    assert bundle.core_fact_snapshot_version == "snapshot.v3"
    assert VetPromptBlockType.TASK_INPUT in block_types
    assert VetPromptBlockType.PET_PROFILE_P0 in block_types
    assert VetPromptBlockType.SLOT_COVERAGE in block_types
    assert VetPromptBlockType.CONFIRMED_LAB_SUMMARY in block_types
    assert bundle.slot_coverage.known_slots["symptom_frequency"] == "3 times today"
    assert len(trace_sink.records) == 1


def test_pet_mismatch_source_is_dropped_and_marked_degraded() -> None:
    """验证错宠来源正文被丢弃且 bundle 明确标记降级。

    :return: None。
    """

    provider = build_runtime_provider()
    source_ports = as_source_ports(
        build_fact_source_result(
            source_type=ContextSourceType.CORE_FACT_SNAPSHOT,
            pet_id="pet_other",
            facts={"species": "cat"},
        ),
        build_fact_source_result(
            source_type=ContextSourceType.PET_PROFILE,
            pet_id="pet_context_1",
            facts={"species": "dog", "age": 4},
        ),
        build_empty_source_result(
            source_type=ContextSourceType.CONVERSATION,
            pet_id="pet_context_1",
        ),
        build_empty_source_result(
            source_type=ContextSourceType.CONFIRMED_LAB,
            pet_id="pet_context_1",
        ),
        build_empty_source_result(
            source_type=ContextSourceType.OWNER_PREFERENCE,
            pet_id=None,
        ),
    )
    builder = DefaultVetContextBuilder(
        runtime_config_provider=provider,
        source_ports=source_ports,
    )
    request = build_request(
        provider=provider,
        session_state_snapshot=build_session_state(pet_id="pet_context_1"),
    )

    bundle = asyncio.run(builder.build(request))

    species_fact = next(fact for fact in bundle.fact_ledger if fact.key == "species")
    assert species_fact.value == "dog"
    assert bundle.status is ContextBuildStatus.DEGRADED
    assert any("pet_mismatch" in reason for reason in bundle.degraded_reasons)
    assert any(
        source_ref.status is ContextSourceStatus.PET_MISMATCH
        for source_ref in bundle.source_refs
    )


def test_safety_build_continues_with_all_domain_sources_todo() -> None:
    """验证安全路径在领域来源均为 TODO 时仍返回最小上下文。

    :return: None。
    """

    provider = build_runtime_provider()
    builder = DefaultVetContextBuilder(
        runtime_config_provider=provider,
        source_ports=build_default_context_source_ports(),
    )
    request = build_request(
        provider=provider,
        generation_profile=VetGenerationProfile.SAFETY_TRIGGER,
        route="safety_trigger",
        executor_key=VetExecutorKey.SAFETY_TRIGGER,
        compression_strategy=ContextCompressionStrategy.SAFETY_MINIMAL,
        audit_tier="A",
        assessment_summary={"signals": ["SAF-01"], "risk_level": "high"},
    )

    bundle = asyncio.run(builder.build(request))

    block_types = {block.block_type for block in bundle.prompt_blocks}
    assert bundle.status is ContextBuildStatus.MINIMAL
    assert VetPromptBlockType.TASK_INPUT in block_types
    assert VetPromptBlockType.PET_PROFILE_P0 in block_types
    assert VetPromptBlockType.SAFETY_ASSESSMENT in block_types
    assert bundle.trace_delivery_status is ContextTraceWriteStatus.DEGRADED


def test_safety_build_degrades_timed_out_source_without_blocking() -> None:
    """验证安全路径单来源超时后仍使用其余来源生成最小上下文。

    :return: None。
    """

    settings = VetContextBuilderSettings(
        timeouts=VetContextTimeoutConfig(
            total_seconds=0.1,
            source_seconds=0.05,
            safety_total_seconds=0.05,
            safety_source_seconds=0.01,
        )
    )
    provider = build_runtime_provider(settings)
    slow_snapshot = FakeContextSourcePort(
        result=build_fact_source_result(
            source_type=ContextSourceType.CORE_FACT_SNAPSHOT,
            pet_id="pet_context_1",
            facts={"species": "dog"},
        ),
        delay_seconds=0.05,
    )
    profile = FakeContextSourcePort(
        result=build_fact_source_result(
            source_type=ContextSourceType.PET_PROFILE,
            pet_id="pet_context_1",
            facts={"species": "dog", "age": 5},
        )
    )
    builder = DefaultVetContextBuilder(
        runtime_config_provider=provider,
        source_ports=(slow_snapshot, profile),
    )
    request = build_request(
        provider=provider,
        generation_profile=VetGenerationProfile.SAFETY_TRIGGER,
        route="safety_trigger",
        executor_key=VetExecutorKey.SAFETY_TRIGGER,
        compression_strategy=ContextCompressionStrategy.SAFETY_MINIMAL,
        assessment_summary={"signals": ["SAF-01"], "risk_level": "high"},
    )

    bundle = asyncio.run(builder.build(request))

    assert bundle.status is ContextBuildStatus.DEGRADED
    assert VetPromptBlockType.PET_PROFILE_P0 in {
        block.block_type for block in bundle.prompt_blocks
    }
    assert any(
        source_ref.status is ContextSourceStatus.TIMEOUT
        for source_ref in bundle.source_refs
    )
    assert any(
        "core_fact_snapshot:timeout" in reason for reason in bundle.degraded_reasons
    )


def test_trace_sink_exception_does_not_fail_context_build() -> None:
    """验证 trace sink 抛出异常时 bundle 仍成功返回并标记 trace 降级。

    :return: None。
    """

    provider = build_runtime_provider()
    trace_sink = RecordingContextTraceSink(exception=RuntimeError("trace unavailable"))
    builder = DefaultVetContextBuilder(
        runtime_config_provider=provider,
        source_ports=_full_source_ports(pet_id="pet_context_1"),
        trace_sink=trace_sink,
    )
    request = build_request(
        provider=provider,
        session_state_snapshot=build_session_state(pet_id="pet_context_1"),
    )

    bundle = asyncio.run(builder.build(request))

    assert bundle.status is ContextBuildStatus.FULL
    assert bundle.trace_delivery_status is ContextTraceWriteStatus.DEGRADED
    assert len(trace_sink.records) == 1


def test_budget_trim_drops_large_optional_block_but_keeps_p0() -> None:
    """验证 token 预算裁剪会丢弃大块但不会移除 P0。

    :return: None。
    """

    settings = VetContextBuilderSettings(
        budgets=VetContextBudgetConfig(
            single_full_tokens=1024,
            safety_minimal_tokens=1024,
            education_light_tokens=1024,
        ),
        recent_message_token_budget=128,
        max_task_input_chars=256,
    )
    provider = build_runtime_provider(settings)
    large_facts: dict[str, JsonValue] = {
        "species": "dog",
        "age": 5,
        **{f"historical_fact_{index}": "x" * 600 for index in range(20)},
    }
    source_ports = as_source_ports(
        build_fact_source_result(
            source_type=ContextSourceType.CORE_FACT_SNAPSHOT,
            pet_id="pet_context_1",
            facts=large_facts,
        ),
        build_empty_source_result(
            source_type=ContextSourceType.PET_PROFILE,
            pet_id="pet_context_1",
        ),
        build_conversation_source_result(
            pet_id="pet_context_1",
            message_count=8,
            content_size=300,
        ),
        build_empty_source_result(
            source_type=ContextSourceType.CONFIRMED_LAB,
            pet_id="pet_context_1",
        ),
        build_empty_source_result(
            source_type=ContextSourceType.OWNER_PREFERENCE,
            pet_id=None,
        ),
    )
    builder = DefaultVetContextBuilder(
        runtime_config_provider=provider,
        source_ports=source_ports,
    )
    request = build_request(
        provider=provider,
        session_state_snapshot=build_session_state(pet_id="pet_context_1"),
    )

    bundle = asyncio.run(builder.build(request))

    block_types = {block.block_type for block in bundle.prompt_blocks}
    assert VetPromptBlockType.PET_PROFILE_P0 in block_types
    assert bundle.compression_audit.trim_applied is True
    assert bundle.compression_audit.estimated_tokens <= (
        bundle.compression_audit.token_budget
    )
    assert any(
        "core_fact_snapshot" in block_id
        for block_id in bundle.compression_audit.dropped_block_ids
    )


def test_build_rejects_request_from_different_config_snapshot() -> None:
    """验证 Builder 拒绝与当前配置快照版本不一致的请求。

    :return: None。
    """

    first_provider = build_runtime_provider()
    request = build_request(provider=first_provider)
    second_provider = build_runtime_provider(
        VetContextBuilderSettings(max_prompt_blocks=15)
    )
    builder = DefaultVetContextBuilder(
        runtime_config_provider=second_provider,
        source_ports=build_default_context_source_ports(),
    )

    with pytest.raises(VetContextBuilderError) as exc_info:
        asyncio.run(builder.build(request))

    assert exc_info.value.code.value == "CONTEXT_INVALID_REQUEST"

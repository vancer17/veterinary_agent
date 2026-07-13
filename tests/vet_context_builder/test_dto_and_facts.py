##################################################################################################
# 文件: tests/vet_context_builder/test_dto_and_facts.py
# 作用: 验证 VetContextBuilder 请求关系、事实来源优先级、冲突处理与槽位覆盖契约。
# 边界: 仅测试纯 DTO 和事实规则，不调用来源端口、LangGraph、数据库或 trace sink。
##################################################################################################

from pydantic import ValidationError
import pytest

from veterinary_agent.vet_context_builder import (
    ContextCompressionStrategy,
    ContextFactDto,
    ContextFactState,
    ContextSourceFreshness,
    ContextSourceType,
    VetExecutorKey,
    VetGenerationProfile,
    evaluate_slot_coverage,
    resolve_context_facts,
)
from tests.vet_context_builder.helpers import (
    build_request,
    build_runtime_provider,
    build_source_ref,
)


def test_build_request_rejects_invalid_profile_strategy_relation() -> None:
    """验证 standard 执行器不能使用轻量教育压缩策略。

    :return: None。
    """

    provider = build_runtime_provider()

    with pytest.raises(ValidationError):
        build_request(
            provider=provider,
            compression_strategy=ContextCompressionStrategy.EDUCATION_LIGHT,
        )


def test_nonmedical_request_allows_empty_generation_profile() -> None:
    """验证纯非医疗执行器允许 generation_profile 为空。

    :return: None。
    """

    provider = build_runtime_provider()

    request = build_request(
        provider=provider,
        generation_profile=None,
        executor_key=VetExecutorKey.NONMEDICAL_PET_CARE,
        compression_strategy=ContextCompressionStrategy.EDUCATION_LIGHT,
    )

    assert request.generation_profile is None


def test_same_priority_fact_conflict_becomes_pending_confirmation() -> None:
    """验证同优先级来源给出不同值时事实进入待确认状态。

    :return: None。
    """

    first_ref = build_source_ref(
        source_type=ContextSourceType.CORE_FACT_SNAPSHOT,
        source_id="snapshot_1",
        pet_id="pet_context_1",
    )
    second_ref = build_source_ref(
        source_type=ContextSourceType.CORE_FACT_SNAPSHOT,
        source_id="snapshot_2",
        pet_id="pet_context_1",
    )

    ledger = resolve_context_facts(
        [
            ContextFactDto(key="weight_kg", value=12, source_ref=first_ref),
            ContextFactDto(key="weight_kg", value=13, source_ref=second_ref),
        ]
    )

    assert ledger[0].state is ContextFactState.PENDING_CONFIRMATION
    assert ledger[0].conflict is True


def test_current_task_fact_overrides_snapshot_without_false_conflict() -> None:
    """验证本轮确认事实覆盖旧快照时不会产生同级冲突。

    :return: None。
    """

    task_ref = build_source_ref(
        source_type=ContextSourceType.CURRENT_TASK,
        source_id="task_context_1",
        pet_id="pet_context_1",
    )
    snapshot_ref = build_source_ref(
        source_type=ContextSourceType.CORE_FACT_SNAPSHOT,
        source_id="snapshot_1",
        pet_id="pet_context_1",
    )

    ledger = resolve_context_facts(
        [
            ContextFactDto(key="weight_kg", value=13, source_ref=task_ref),
            ContextFactDto(key="weight_kg", value=12, source_ref=snapshot_ref),
        ]
    )
    coverage = evaluate_slot_coverage(
        task_id="task_context_1",
        task_type="TRIAGE",
        fact_ledger=ledger,
    )

    assert ledger[0].state is ContextFactState.KNOWN
    assert ledger[0].value == 13
    assert coverage.known_slots["weight_kg"] == 13
    assert "species" in coverage.missing_slots


def test_stale_fact_is_reported_separately_from_missing_slots() -> None:
    """验证过期事实进入 stale 槽位且不会被误判为缺失。

    :return: None。
    """

    stale_ref = build_source_ref(
        source_type=ContextSourceType.CORE_FACT_SNAPSHOT,
        source_id="snapshot_stale",
        pet_id="pet_context_1",
        freshness=ContextSourceFreshness.STALE,
    )

    ledger = resolve_context_facts(
        [ContextFactDto(key="species", value="dog", source_ref=stale_ref)]
    )
    coverage = evaluate_slot_coverage(
        task_id="task_context_1",
        task_type="GENERAL_QA",
        fact_ledger=ledger,
    )

    assert coverage.stale_slots == {"species": "dog"}
    assert "species" not in coverage.missing_slots


def test_education_request_requires_education_profile() -> None:
    """验证 education 执行器必须使用 education 生成剖面。

    :return: None。
    """

    provider = build_runtime_provider()

    with pytest.raises(ValidationError):
        build_request(
            provider=provider,
            executor_key=VetExecutorKey.EDUCATION,
            generation_profile=VetGenerationProfile.STANDARD,
            compression_strategy=ContextCompressionStrategy.EDUCATION_LIGHT,
        )

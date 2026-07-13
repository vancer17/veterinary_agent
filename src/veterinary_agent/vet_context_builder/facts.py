##################################################################################################
# 文件: src/veterinary_agent/vet_context_builder/facts.py
# 作用: 实现上下文事实来源优先级合并、同级冲突检测、checkpoint 槽位投影与槽位覆盖计算。
# 边界: 只处理已规范化事实，不读取外部来源、不渲染 prompt、不执行模型推理或持久化。
##################################################################################################

from collections import defaultdict
import json
from typing import Final

from pydantic import JsonValue

from veterinary_agent.vet_context_builder.dto import (
    ContextFactDto,
    ContextSourceRefDto,
    ResolvedContextFactDto,
    SessionContextStateDto,
    SlotCoverageDto,
)
from veterinary_agent.vet_context_builder.enums import (
    ContextFactState,
    ContextSourceFreshness,
    ContextSourceType,
)

_SOURCE_PRIORITY: Final[dict[ContextSourceType, int]] = {
    ContextSourceType.CURRENT_TASK: 0,
    ContextSourceType.CONFIRMED_LAB: 1,
    ContextSourceType.CORE_FACT_SNAPSHOT: 2,
    ContextSourceType.PET_PROFILE: 3,
    ContextSourceType.CHECKPOINT: 4,
    ContextSourceType.OWNER_PREFERENCE: 5,
    ContextSourceType.CONVERSATION: 6,
}

_TASK_REQUIRED_SLOTS: Final[dict[str, tuple[str, ...]]] = {
    "TRIAGE": (
        "species",
        "age",
        "weight_kg",
        "symptom_duration",
        "symptom_frequency",
        "appetite",
        "hydration",
        "energy_level",
    ),
    "NUTRITION": (
        "species",
        "age",
        "weight_kg",
        "current_diet",
        "body_condition",
    ),
    "BEHAVIOR": (
        "species",
        "age",
        "neutered",
        "behavior_duration",
        "behavior_triggers",
    ),
    "CARE": (
        "species",
        "age",
        "living_environment",
    ),
    "REPORT_OCR": ("species",),
    "RECORD_PARSE": ("species",),
    "GENERAL_QA": ("species",),
    "UNDECOMPOSED": ("species",),
    "EDUCATION_QA": (),
}


def _canonical_json(value: JsonValue) -> str:
    """将 JSON 值序列化为稳定比较文本。

    :param value: 待序列化的 JSON 值。
    :return: 按 key 排序且不含多余空白的 JSON 文本。
    """

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _fact_sort_key(fact: ContextFactDto) -> tuple[int, int, float]:
    """构建事实优先级排序键。

    :param fact: 待排序的规范化事实。
    :return: 来源优先级、确认优先级和反向时间戳组成的排序键。
    """

    observed_timestamp = fact.observed_at.timestamp() if fact.observed_at else 0.0
    return (
        _SOURCE_PRIORITY.get(fact.source_ref.source_type, 100),
        0 if fact.confirmed else 1,
        -observed_timestamp,
    )


def _resolve_fact_group(facts: list[ContextFactDto]) -> ResolvedContextFactDto:
    """合并同一事实键的全部候选值。

    :param facts: 具有相同事实键的候选事实列表。
    :return: 已应用来源优先级、冲突与新鲜度规则的事实账本条目。
    :raises ValueError: 当候选事实列表为空时抛出。
    """

    if not facts:
        raise ValueError("事实候选列表不得为空")
    ordered = sorted(facts, key=_fact_sort_key)
    winner = ordered[0]
    winner_priority = _SOURCE_PRIORITY.get(winner.source_ref.source_type, 100)
    same_priority = [
        fact
        for fact in ordered
        if _SOURCE_PRIORITY.get(fact.source_ref.source_type, 100) == winner_priority
    ]
    conflict = len({_canonical_json(fact.value) for fact in same_priority}) > 1
    if winner.source_ref.freshness is ContextSourceFreshness.STALE:
        state = ContextFactState.STALE
    elif not winner.confirmed or conflict:
        state = ContextFactState.PENDING_CONFIRMATION
    else:
        state = ContextFactState.KNOWN
    source_refs = list(
        {
            (source_ref.source_type, source_ref.source_id): source_ref
            for source_ref in (fact.source_ref for fact in ordered)
        }.values()
    )
    return ResolvedContextFactDto(
        key=winner.key,
        value=winner.value,
        state=state,
        source_refs=source_refs,
        conflict=conflict,
    )


def resolve_context_facts(
    facts: list[ContextFactDto],
) -> list[ResolvedContextFactDto]:
    """将规范化事实合并为稳定事实账本。

    :param facts: 已通过宠物边界校验的规范化事实列表。
    :return: 按事实键排序的最终事实账本。
    """

    grouped: defaultdict[str, list[ContextFactDto]] = defaultdict(list)
    for fact in facts:
        grouped[fact.key].append(fact)
    return [_resolve_fact_group(grouped[key]) for key in sorted(grouped)]


def facts_from_session_state(
    state: SessionContextStateDto,
) -> list[ContextFactDto]:
    """将 checkpoint 槽位进度投影为规范化事实。

    :param state: 已通过宠物边界校验的 session 状态快照。
    :return: 从 slot_progress 提取的事实列表。
    """

    facts: list[ContextFactDto] = []
    for key, raw_value in state.slot_progress.items():
        value: JsonValue = raw_value
        confirmed = True
        source_ref: ContextSourceRefDto = state.source_ref
        if isinstance(raw_value, dict) and "value" in raw_value:
            value = raw_value["value"]
            status = raw_value.get("status")
            confirmed = status in {None, "known", "answered", "confirmed"}
        facts.append(
            ContextFactDto(
                key=key,
                value=value,
                source_ref=source_ref,
                confirmed=confirmed,
            )
        )
    return facts


def required_slots_for_task(task_type: str) -> tuple[str, ...]:
    """解析指定任务类型的确定性必需槽位。

    :param task_type: 上游受控任务类型。
    :return: 当前任务类型对应的必需槽位元组；未知类型保守要求 species。
    """

    return _TASK_REQUIRED_SLOTS.get(task_type.strip().upper(), ("species",))


def evaluate_slot_coverage(
    *,
    task_id: str,
    task_type: str,
    fact_ledger: list[ResolvedContextFactDto],
) -> SlotCoverageDto:
    """根据事实账本计算当前子任务槽位覆盖。

    :param task_id: 当前子任务 ID。
    :param task_type: 当前受控任务类型。
    :param fact_ledger: 已完成优先级合并的事实账本。
    :return: known、missing、stale 与 pending 四类槽位覆盖结果。
    """

    by_key = {fact.key: fact for fact in fact_ledger}
    known_slots: dict[str, JsonValue] = {}
    missing_slots: list[str] = []
    stale_slots: dict[str, JsonValue] = {}
    pending_slots: dict[str, JsonValue] = {}
    for slot in required_slots_for_task(task_type):
        fact = by_key.get(slot)
        if fact is None:
            missing_slots.append(slot)
        elif fact.state is ContextFactState.KNOWN:
            known_slots[slot] = fact.value
        elif fact.state is ContextFactState.STALE:
            stale_slots[slot] = fact.value
        else:
            pending_slots[slot] = fact.value
    return SlotCoverageDto(
        task_id=task_id,
        known_slots=known_slots,
        missing_slots=missing_slots,
        stale_slots=stale_slots,
        pending_confirmation_slots=pending_slots,
    )


__all__: tuple[str, ...] = (
    "evaluate_slot_coverage",
    "facts_from_session_state",
    "required_slots_for_task",
    "resolve_context_facts",
)

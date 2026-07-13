##################################################################################################
# 文件: src/veterinary_agent/nonmedical_pet_care_agent/briefing.py
# 作用: 提供 NonmedicalPetCareAgent 的 brief 构建、输入信号读取和上下文个性化因子提取函数。
# 边界: 不调用模型、不执行 RAG、不写 trace；仅消费 VetContextBuilder 与输入安全摘要的结构化结果。
##################################################################################################

from collections.abc import Sequence

from veterinary_agent.nonmedical_pet_care_agent.dto import (
    InputSafetySignalDto,
    NonmedicalAdviceRequestDto,
    PersonalizationFactorDto,
    PetCareBriefDto,
)
from veterinary_agent.nonmedical_pet_care_agent.enums import CareDomain
from veterinary_agent.nonmedical_pet_care_agent.rules import (
    as_list,
    as_mapping,
    care_domain_from_task_and_text,
    is_hard_escalation_signal,
    read_string,
    requires_body_boundary,
    signal_code,
    signal_strength,
)


def build_brief(*, request: NonmedicalAdviceRequestDto) -> PetCareBriefDto:
    """基于轻量上下文构建非医疗养宠 brief。

    :param request: 当前非医疗建议生成请求。
    :return: 本轮非医疗养宠建议主轴和可用上下文视图。
    """

    care_domain = care_domain_from_task_and_text(
        request.task_type,
        request.normalized_query,
    )
    available_refs = [
        block.block_id
        for block in request.context.prompt_blocks
        if block.required
        or block.block_type.value in {"task_input", "owner_preference"}
    ]
    return PetCareBriefDto(
        main_request=request.normalized_query,
        advice_axis=advice_axis_from_query(request.normalized_query),
        care_domain=care_domain,
        species_scope=species_scope_from_context(request=request),
        consumed_signals=signals_from_assessment(request=request),
        available_pet_context_refs=available_refs,
        missing_personalization_fields=missing_personalization_fields(
            request=request,
            care_domain=care_domain,
        ),
    )


def signals_from_assessment(
    *,
    request: NonmedicalAdviceRequestDto,
) -> list[InputSafetySignalDto]:
    """从输入安全评估摘要读取可消费信号。

    :param request: 当前非医疗建议生成请求。
    :return: 已校验的信号摘要列表。
    """

    signals: list[InputSafetySignalDto] = []
    for index, item in enumerate(as_list(request.assessment_summary.get("signals"))):
        item_map = as_mapping(item)
        if item_map is None:
            continue
        code = signal_code(item_map)
        strength = signal_strength(item_map)
        signals.append(
            InputSafetySignalDto(
                signal_id=read_string(item_map.get("signal_id"))
                or f"signal_{index + 1}",
                code=code,
                strength=strength,
                normalized_concept=read_string(item_map.get("normalized_concept")),
                confidence=confidence_from_value(item_map.get("confidence")),
            )
        )
    return signals


def confidence_from_value(value: object) -> float:
    """从未知值中读取置信度。

    :param value: 需要读取的未知值。
    :return: 归一到 0 到 1 之间的置信度。
    """

    if isinstance(value, int | float):
        return max(0.0, min(1.0, float(value)))
    return 1.0


def species_scope_from_context(*, request: NonmedicalAdviceRequestDto) -> str:
    """从上下文事实账本提取物种范围。

    :param request: 当前非医疗建议生成请求。
    :return: 可用于建议适配的物种范围。
    """

    for fact in request.context.fact_ledger:
        if fact.key == "species":
            value = read_string(fact.value)
            if value is not None:
                return value
    value = read_string(request.context.slot_coverage.known_slots.get("species"))
    return value or "unknown"


def advice_axis_from_query(query: str) -> str:
    """从当前问题提取非医疗建议主轴摘要。

    :param query: 当前规范化问题。
    :return: 不超过 120 字的建议主轴。
    """

    normalized = " ".join(query.split())
    if len(normalized) <= 120:
        return normalized
    return f"{normalized[:117]}..."


def missing_personalization_fields(
    *,
    request: NonmedicalAdviceRequestDto,
    care_domain: CareDomain,
) -> list[str]:
    """计算当前领域缺失且不得编造的个性化字段。

    :param request: 当前非医疗建议生成请求。
    :param care_domain: 当前护理领域。
    :return: 缺失字段列表。
    """

    known = set(request.context.slot_coverage.known_slots)
    for fact in request.context.fact_ledger:
        known.add(fact.key)
    required = {"species", "age"}
    if care_domain in {CareDomain.NUTRITION, CareDomain.WEIGHT_MANAGEMENT}:
        required.update({"weight_kg", "current_diet"})
    if care_domain is CareDomain.EXERCISE:
        required.update({"activity_level", "weight_kg"})
    if care_domain is CareDomain.ENVIRONMENT:
        required.add("living_environment")
    return sorted(field for field in required if field not in known)


def personalization_factors_from_context(
    *,
    request: NonmedicalAdviceRequestDto,
) -> list[PersonalizationFactorDto]:
    """从 VetContextBuilder 输出中提取可用个性化因子。

    :param request: 当前非医疗建议生成请求。
    :return: 可供建议规划和写作使用的个性化因子列表。
    """

    factors: list[PersonalizationFactorDto] = []
    allowed_keys = {
        "species",
        "age",
        "life_stage",
        "weight_kg",
        "sex",
        "neutered",
        "activity_level",
        "current_diet",
        "living_environment",
    }
    for fact in request.context.fact_ledger:
        if fact.key not in allowed_keys:
            continue
        factors.append(
            PersonalizationFactorDto(
                factor_code=fact.key,
                value_summary=str(fact.value),
                source_ref=first_source_ref(fact.source_refs),
                confidence=1.0,
            )
        )
    for block in request.context.prompt_blocks:
        if block.block_type.value != "owner_preference":
            continue
        factors.append(
            PersonalizationFactorDto(
                factor_code="owner_preference",
                value_summary="已读取主人偏好摘要",
                source_ref=block.block_id,
                confidence=0.8,
            )
        )
    return factors


def first_source_ref(source_refs: Sequence[object]) -> str:
    """读取事实来源引用中的第一个 source_id。

    :param source_refs: 上下文事实携带的来源引用列表。
    :return: 第一个来源 ID；缺失时返回 context。
    """

    if not source_refs:
        return "context"
    source_ref = source_refs[0]
    source_id = getattr(source_ref, "source_id", None)
    return source_id if isinstance(source_id, str) and source_id else "context"


def requires_safety_escalation(*, brief: PetCareBriefDto) -> bool:
    """判断本轮是否必须停止普通非医疗建议。

    :param brief: 本轮非医疗 brief。
    :return: 若包含 L3 或 SAF-01 等硬升级信号则返回 True。
    """

    return any(
        is_hard_escalation_signal(signal.code, signal.strength)
        for signal in brief.consumed_signals
    )


def has_body_boundary_signal(*, brief: PetCareBriefDto) -> bool:
    """判断本轮是否需要正文嵌入风险边界。

    :param brief: 本轮非医疗 brief。
    :return: 若存在 L1 或 L2 信号则返回 True。
    """

    return any(
        requires_body_boundary(signal.strength) for signal in brief.consumed_signals
    )


__all__: tuple[str, ...] = (
    "advice_axis_from_query",
    "build_brief",
    "confidence_from_value",
    "first_source_ref",
    "has_body_boundary_signal",
    "missing_personalization_fields",
    "personalization_factors_from_context",
    "requires_safety_escalation",
    "signals_from_assessment",
    "species_scope_from_context",
)

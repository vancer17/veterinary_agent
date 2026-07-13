##################################################################################################
# 文件: src/veterinary_agent/nonmedical_pet_care_agent/drafting.py
# 作用: 提供 NonmedicalPetCareAgent 的草稿构造、自检扫描和草稿状态判定辅助函数。
# 边界: 不调用模型、不执行 RAG、不写 trace；仅处理已编排完成的非医疗结构化数据。
##################################################################################################

from veterinary_agent.config import NonmedicalPetCareAgentSettings
from veterinary_agent.nonmedical_pet_care_agent.dto import (
    AdviceConstraintDto,
    AdviceDimensionDto,
    AdvicePlanDto,
    NonmedicalAdviceDraftDto,
    NonmedicalAdviceRequestDto,
    NonmedicalTracePatchDto,
    PersonalizationFactorDto,
    PersonalizationPlanDto,
    PetCareBriefDto,
    RagUsageSummaryDto,
    SafetySelfCheckSummaryDto,
)
from veterinary_agent.nonmedical_pet_care_agent.enums import (
    AdviceDimensionCode,
    CareDomain,
    NonmedicalDraftStatus,
    PersonalizationLevel,
)
from veterinary_agent.nonmedical_pet_care_agent.rules import (
    EXTREME_DIET_TERMS,
    MEDICATION_TERMS,
    OVERPROMISE_TERMS,
    PUNITIVE_TRAINING_TERMS,
    contains_any,
)


def build_conservative_response(
    *,
    brief: PetCareBriefDto,
    personalization_plan: PersonalizationPlanDto,
) -> str:
    """构建依赖降级时的保守非医疗建议正文。

    :param brief: 本轮非医疗 brief。
    :param personalization_plan: 个性化计划。
    :return: 可进入输出安全审查的保守草稿正文。
    """

    species = brief.species_scope if brief.species_scope != "unknown" else "当前宠物"
    context_note = (
        "目前可用画像有限，以下先按通用原则处理；"
        if personalization_plan.personalization_level
        in {PersonalizationLevel.MINIMAL, PersonalizationLevel.UNAVAILABLE}
        else "结合已知画像，可以先按保守步骤处理；"
    )
    return (
        f"关于“{brief.advice_axis}”，{context_note}"
        f"建议围绕 {species} 的日常状态做小幅、可回退的调整："
        "先保持饮食、作息和环境稳定，再一次只改一个变量，观察精神、食欲、排便、活动量和行为变化。"
        "如果过程中出现持续恶化、明显疼痛、呼吸异常、抽搐、疑似误食或其他健康异常，"
        "这就不应继续按普通养宠问题处理，应及时联系线下兽医。"
    )


def deterministic_self_check(
    *,
    draft_response: str,
) -> SafetySelfCheckSummaryDto:
    """用轻量确定性扫描构建安全实用性自检摘要。

    :param draft_response: 待检查的非医疗草稿正文。
    :return: 安全实用性自检摘要。
    """

    extreme_diet = contains_any(draft_response, EXTREME_DIET_TERMS)
    punitive_training = contains_any(draft_response, PUNITIVE_TRAINING_TERMS)
    medication_boundary = contains_any(draft_response, MEDICATION_TERMS)
    overpromise = contains_any(draft_response, OVERPROMISE_TERMS)
    risk_flags: list[str] = []
    if extreme_diet:
        risk_flags.append("extreme_diet_detected")
    if punitive_training:
        risk_flags.append("punitive_training_detected")
    if medication_boundary:
        risk_flags.append("medication_boundary_detected")
    if overpromise:
        risk_flags.append("overpromise_detected")
    return SafetySelfCheckSummaryDto(
        passed=not risk_flags,
        risk_flags=risk_flags,
        extreme_diet_detected=extreme_diet,
        punitive_training_detected=punitive_training,
        medication_boundary_detected=medication_boundary,
        overpromise_detected=overpromise,
    )


def status_for_draft(
    *,
    has_body_boundary_signal: bool,
    brief: PetCareBriefDto,
    rag_summary: RagUsageSummaryDto,
    personalization_plan: PersonalizationPlanDto,
    self_check: SafetySelfCheckSummaryDto,
) -> NonmedicalDraftStatus:
    """根据风险信号、RAG、个性化和自检选择草稿状态。

    :param has_body_boundary_signal: 本轮是否存在 L1/L2 正文边界信号。
    :param brief: 本轮非医疗 brief。
    :param rag_summary: 本轮 RAG 使用摘要。
    :param personalization_plan: 个性化计划。
    :param self_check: 安全实用性自检摘要。
    :return: 非医疗草稿状态。
    """

    if not self_check.passed:
        return NonmedicalDraftStatus.KNOWLEDGE_DEGRADED_CONSERVATIVE
    if has_body_boundary_signal:
        return NonmedicalDraftStatus.CONSERVATIVE_WITH_SIGNAL
    if rag_summary.degraded:
        return NonmedicalDraftStatus.KNOWLEDGE_DEGRADED_CONSERVATIVE
    if (
        personalization_plan.personalization_level is PersonalizationLevel.UNAVAILABLE
        and brief.care_domain
        in {CareDomain.NUTRITION, CareDomain.WEIGHT_MANAGEMENT, CareDomain.EXERCISE}
    ):
        return NonmedicalDraftStatus.INSUFFICIENT_CONTEXT
    return NonmedicalDraftStatus.DRAFT_READY


def build_escalation_advice_plan(
    *,
    brief: PetCareBriefDto,
    personalization_factors: list[PersonalizationFactorDto],
    generation_constraints: list[str],
    safety_boundary_hints: list[str],
) -> AdvicePlanDto:
    """构建 SAF-01 或 L3 误入时使用的升级建议计划。

    :param brief: 本轮非医疗 brief。
    :param personalization_factors: 可用个性化因子。
    :param generation_constraints: 默认生成约束。
    :param safety_boundary_hints: 默认安全边界提示。
    :return: 只包含风险边界和专业介入维度的建议计划。
    """

    return AdvicePlanDto(
        advice_axis=brief.advice_axis,
        dimensions=[
            AdviceDimensionDto(
                dimension_code=AdviceDimensionCode.RISK_BOUNDARY,
                priority=1,
                required=True,
                evidence_requirement="硬安全信号要求升级，不允许普通非医疗建议。",
                prohibited_advice=["普通养宠建议替代安全处理"],
            ),
            AdviceDimensionDto(
                dimension_code=AdviceDimensionCode.PROFESSIONAL_ESCALATION,
                priority=2,
                required=True,
                evidence_requirement="需要提示联系线下兽医或急症路径。",
                prohibited_advice=["延迟就医"],
            ),
        ],
        personalization_factors=personalization_factors,
        generation_constraints=generation_constraints,
        safety_boundary_hints=safety_boundary_hints,
    )


def build_escalation_draft(
    *,
    request: NonmedicalAdviceRequestDto,
    brief: PetCareBriefDto,
    plan: AdvicePlanDto,
    personalization_plan: PersonalizationPlanDto,
    trace_patch: NonmedicalTracePatchDto,
    settings: NonmedicalPetCareAgentSettings,
) -> NonmedicalAdviceDraftDto:
    """构建 SAF-01 或 L3 误入时的升级草稿结果。

    :param request: 当前非医疗建议生成请求。
    :param brief: 本轮非医疗 brief。
    :param plan: 升级建议计划。
    :param personalization_plan: 个性化计划。
    :param trace_patch: 已构建的 trace patch。
    :param settings: 当前非医疗配置。
    :return: 高风险升级状态草稿。
    """

    constraints = [
        AdviceConstraintDto(
            constraint_id="safety_escalation_required",
            constraint_type="hard_safety_boundary",
            constraint_summary="输入包含 SAF-01 或 L3 强信号，非医疗链路不得输出普通养宠建议。",
            evidence_card_ids=[],
            hard_boundary=True,
        )
    ]
    response = (
        f"关于“{brief.advice_axis}”，当前输入里有较强安全信号，"
        "不适合按普通养宠建议继续处理。请优先联系线下兽医或急症渠道，"
        "并说明宠物物种、体重、接触到的物品或异常表现、发生时间和当前精神状态。"
    )
    return NonmedicalAdviceDraftDto(
        task_id=request.task_id,
        current_pet_id=request.current_pet_id or request.context.current_pet_id,
        status=NonmedicalDraftStatus.NEEDS_SAFETY_ESCALATION,
        draft_response=response[: settings.max_draft_chars],
        draft_response_ref=f"draft:{request.trace_id}:{request.task_id}",
        advice_plan=plan,
        advice_constraints=constraints,
        personalization_plan=personalization_plan,
        rag_summary=RagUsageSummaryDto(),
        self_check=SafetySelfCheckSummaryDto(
            passed=True,
            risk_flags=["safety_escalation_required"],
        ),
        trace_patch=trace_patch,
    )


__all__: tuple[str, ...] = (
    "build_conservative_response",
    "build_escalation_advice_plan",
    "build_escalation_draft",
    "deterministic_self_check",
    "status_for_draft",
)

##################################################################################################
# 文件: src/veterinary_agent/agent_spec_registry/catalog.py
# 作用: 从 RuntimeConfig 快照派生默认 AgentSpec 目录，为真实业务图提供可解析的版本化子 Agent 规格。
# 边界: 不读取额外配置文件、不调用模型、不实现领域业务；其他依赖为空时仅保持无工具、无 RAG 的受控 TODO 边界。
##################################################################################################

from typing import Final

from veterinary_agent.agent_runner import (
    AgentResponseFormat,
    AgentRetryPolicyDto,
    AgentSpecDto,
    AgentTimeoutPolicyDto,
    AgentToolPolicyDto,
    AgentTracePolicyDto,
    AgentType,
    JsonMap,
)
from veterinary_agent.config import RuntimeConfigSnapshot

from .schemas import (
    build_draft_response_schema,
    build_education_planner_schema,
    build_grounding_checker_schema,
    build_input_safety_arbitration_schema,
    build_nonmedical_planner_schema,
    build_nonmedical_self_checker_schema,
    build_retrieval_planner_schema,
    build_safety_confirmation_schema,
    build_standard_care_schema,
    build_standard_differential_schema,
    build_standard_direction_schema,
    build_standard_question_schema,
    build_standard_triage_schema,
    build_task_decomposition_schema,
)

DEFAULT_AGENT_SPEC_CATALOG_VERSION: Final[str] = "agent-spec-catalog.v1"
_DEFAULT_GENERATION_PARAMS: Final[JsonMap] = {
    "temperature": 0.0,
    "top_p": 1.0,
}


def _prompt_template(
    *,
    role: str,
    output_contract: str,
) -> str:
    """构建默认内联 prompt 模板。

    :param role: 当前子 Agent 的受控角色说明。
    :param output_contract: 当前子 Agent 的输出契约说明。
    :return: 可由 AgentRunner 安全渲染的 Jinja2 prompt 模板。
    """

    return (
        f"你是兽医系统内部受控子 Agent：{role}。\n"
        "当前规格：{{ agent_id }}@{{ agent_version }}。\n"
        "你只能基于任务输入、上游上下文块和运行选项作答；"
        "如果证据、RAG 摘要或工具结果为空，不得伪造引用、指南、剂量或检查结果。\n"
        "任务输入(JSON)：\n"
        "{{ task_input_json }}\n\n"
        "上游上下文块：\n"
        "{{ prompt_blocks_text }}\n\n"
        "运行选项(JSON)：\n"
        "{{ runtime_options_json }}\n\n"
        f"输出契约：{output_contract}\n"
        "只输出一个合法 JSON 对象；不要 Markdown 代码块；不要解释；"
        "不要输出未被 schema 或上游契约需要的敏感信息。"
    )


def _default_metadata(
    *,
    component: str,
    stage: str,
) -> JsonMap:
    """构建 Agent 规格默认元信息。

    :param component: 业务组件名。
    :param stage: 子 Agent 阶段名。
    :return: 可写入 AgentSpecDto 的普通元信息。
    """

    return {
        "catalog_version": DEFAULT_AGENT_SPEC_CATALOG_VERSION,
        "component": component,
        "stage": stage,
        "tooling_boundary": "no_tools_until_tool_registry_ready",
    }


def _build_spec(
    *,
    agent_id: str,
    agent_version: str,
    agent_type: AgentType,
    model_profile: str,
    prompt_template_ref: str,
    role: str,
    output_contract: str,
    output_schema_ref: str,
    output_schema: JsonMap,
    timeout_seconds: float,
    component: str,
    stage: str,
    metadata: JsonMap | None = None,
) -> AgentSpecDto:
    """构建单个默认 Agent 规格。

    :param agent_id: Agent ID。
    :param agent_version: Agent 版本。
    :param agent_type: Agent 类型。
    :param model_profile: LlmGateway 模型 profile ID。
    :param prompt_template_ref: prompt 模板引用。
    :param role: 当前子 Agent 角色说明。
    :param output_contract: 输出契约说明。
    :param output_schema_ref: 输出 schema 引用。
    :param output_schema: 输出 JSON Schema。
    :param timeout_seconds: AgentRunner 单次运行超时。
    :param component: 业务组件名。
    :param stage: 子 Agent 阶段名。
    :param metadata: 可选额外元信息。
    :return: 可注册到 AgentSpecRegistry 的 AgentSpecDto。
    """

    resolved_metadata = _default_metadata(component=component, stage=stage)
    if metadata is not None:
        resolved_metadata.update(metadata)
    return AgentSpecDto(
        agent_id=agent_id,
        agent_version=agent_version,
        agent_type=agent_type,
        model_profile=model_profile,
        prompt_template_ref=prompt_template_ref,
        prompt_template=_prompt_template(
            role=role,
            output_contract=output_contract,
        ),
        output_schema_ref=output_schema_ref,
        output_schema=output_schema,
        output_schema_description=output_contract,
        response_format=AgentResponseFormat.TEXT,
        tool_policy=AgentToolPolicyDto(),
        timeout_policy=AgentTimeoutPolicyDto(
            total_timeout_seconds=timeout_seconds,
        ),
        retry_policy=AgentRetryPolicyDto(max_format_repair_attempts=1),
        trace_policy=AgentTracePolicyDto(
            emit_run_summary=True,
            persist_prompt=False,
            persist_raw_output=False,
        ),
        generation_params=dict(_DEFAULT_GENERATION_PARAMS),
        metadata=resolved_metadata,
    )


def _select_default_model_profile(snapshot: RuntimeConfigSnapshot) -> str:
    """选择默认模型 profile。

    :param snapshot: 当前 RuntimeConfig 快照。
    :return: 默认模型 profile ID。
    :raises ValueError: 当 LlmGateway 已启用但没有 model profile 时抛出。
    """

    first_profile = next(iter(snapshot.llm_gateway.model_profiles), None)
    if first_profile is None:
        raise ValueError("LlmGateway 已启用但没有可用于 AgentSpec 的 model profile")
    return first_profile.model_profile_id


def _build_task_decomposer_specs(
    *,
    snapshot: RuntimeConfigSnapshot,
    model_profile: str,
) -> tuple[AgentSpecDto, ...]:
    """构建 VetTaskDecomposer 相关 Agent 规格。

    :param snapshot: 当前 RuntimeConfig 快照。
    :param model_profile: 默认模型 profile ID。
    :return: VetTaskDecomposer 相关 Agent 规格元组。
    """

    settings = snapshot.vet_task_decomposer
    if not settings.enabled or not settings.llm_enabled:
        return ()
    specs = [
        _build_spec(
            agent_id=settings.decompose_agent_id,
            agent_version=settings.decompose_agent_version,
            agent_type=AgentType.GENERIC,
            model_profile=model_profile,
            prompt_template_ref="inline.vet_task_decomposer.decompose.v1",
            role="负责将用户单轮输入拆解为可路由的兽医业务任务。",
            output_contract="输出 tasks 数组，每个任务需包含类型、原文 span、归一化问题和置信度。",
            output_schema_ref="vet_task_decomposer.tasks.v1",
            output_schema=build_task_decomposition_schema(),
            timeout_seconds=settings.timeouts.llm_seconds,
            component="vet_task_decomposer",
            stage="decompose",
        )
    ]
    if settings.review_repair_enabled:
        specs.append(
            _build_spec(
                agent_id=settings.review_agent_id,
                agent_version=settings.review_agent_version,
                agent_type=AgentType.GENERIC,
                model_profile=model_profile,
                prompt_template_ref="inline.vet_task_decomposer.review.v1",
                role="负责审查并修复任务拆解结果的格式、覆盖度和置信度。",
                output_contract="输出修复后的 tasks 数组，并可附加 repair_notes。",
                output_schema_ref="vet_task_decomposer.review.v1",
                output_schema=build_task_decomposition_schema(),
                timeout_seconds=settings.timeouts.review_seconds,
                component="vet_task_decomposer",
                stage="review",
            )
        )
    return tuple(specs)


def _build_input_safety_specs(
    *,
    snapshot: RuntimeConfigSnapshot,
    model_profile: str,
) -> tuple[AgentSpecDto, ...]:
    """构建 VetInputSafetyAssessor 相关 Agent 规格。

    :param snapshot: 当前 RuntimeConfig 快照。
    :param model_profile: 默认模型 profile ID。
    :return: VetInputSafetyAssessor 相关 Agent 规格元组。
    """

    settings = snapshot.vet_input_safety_assessor
    if not settings.enabled or not settings.llm_arbitration_enabled:
        return ()
    return (
        _build_spec(
            agent_id=settings.arbitration_agent_id,
            agent_version=settings.arbitration_agent_version,
            agent_type=AgentType.INPUT_SAFETY,
            model_profile=model_profile,
            prompt_template_ref="inline.vet_input_safety.arbitration.v1",
            role="负责对低置信安全意图进行受控仲裁。",
            output_contract="输出 intent、route、executor_key、compression_strategy 和 reason_code。",
            output_schema_ref="vet_input_safety.arbitration.v1",
            output_schema=build_input_safety_arbitration_schema(),
            timeout_seconds=settings.timeouts.llm_arbitration_seconds,
            component="vet_input_safety_assessor",
            stage="arbitration",
        ),
    )


def _build_standard_consultation_specs(
    *,
    snapshot: RuntimeConfigSnapshot,
    model_profile: str,
) -> tuple[AgentSpecDto, ...]:
    """构建 StandardConsultationAgent 相关 Agent 规格。

    :param snapshot: 当前 RuntimeConfig 快照。
    :param model_profile: 默认模型 profile ID。
    :return: StandardConsultationAgent 相关 Agent 规格元组。
    """

    settings = snapshot.standard_consultation
    if not settings.enabled:
        return ()
    timeout_seconds = settings.timeouts.sub_agent_seconds
    return (
        _build_spec(
            agent_id=settings.question_collector_agent_id,
            agent_version=settings.question_collector_agent_version,
            agent_type=AgentType.STANDARD,
            model_profile=model_profile,
            prompt_template_ref="inline.standard.question_collector.v1",
            role="负责生成标准问诊的候选追问。",
            output_contract="输出 candidate_questions 数组，问题必须围绕缺失事实和风险边界。",
            output_schema_ref="standard.question_collector.v1",
            output_schema=build_standard_question_schema(),
            timeout_seconds=timeout_seconds,
            component="standard_consultation",
            stage="question_collector",
        ),
        _build_spec(
            agent_id=settings.triage_agent_id,
            agent_version=settings.triage_agent_version,
            agent_type=AgentType.STANDARD,
            model_profile=model_profile,
            prompt_template_ref="inline.standard.triage.v1",
            role="负责生成标准问诊分诊摘要。",
            output_contract="输出 triage_summary，不得替代线下兽医诊断。",
            output_schema_ref="standard.triage.v1",
            output_schema=build_standard_triage_schema(),
            timeout_seconds=timeout_seconds,
            component="standard_consultation",
            stage="triage",
        ),
        _build_spec(
            agent_id=settings.direction_agent_id,
            agent_version=settings.direction_agent_version,
            agent_type=AgentType.STANDARD,
            model_profile=model_profile,
            prompt_template_ref="inline.standard.direction.v1",
            role="负责生成就诊和观察方向提示。",
            output_contract="输出 direction_hints 数组，描述可解释的方向而非确诊结论。",
            output_schema_ref="standard.direction.v1",
            output_schema=build_standard_direction_schema(),
            timeout_seconds=timeout_seconds,
            component="standard_consultation",
            stage="direction",
        ),
        _build_spec(
            agent_id=settings.differential_agent_id,
            agent_version=settings.differential_agent_version,
            agent_type=AgentType.STANDARD,
            model_profile=model_profile,
            prompt_template_ref="inline.standard.differential.v1",
            role="负责生成受控鉴别方向。",
            output_contract="输出 differential_hypotheses 数组，必须保持可能性和边界表述。",
            output_schema_ref="standard.differential.v1",
            output_schema=build_standard_differential_schema(),
            timeout_seconds=timeout_seconds,
            component="standard_consultation",
            stage="differential",
        ),
        _build_spec(
            agent_id=settings.care_agent_id,
            agent_version=settings.care_agent_version,
            agent_type=AgentType.STANDARD,
            model_profile=model_profile,
            prompt_template_ref="inline.standard.care.v1",
            role="负责生成低风险护理和就医准备建议。",
            output_contract="输出 care_suggestions 数组，不得包含处方、精确剂量或延误就医建议。",
            output_schema_ref="standard.care.v1",
            output_schema=build_standard_care_schema(),
            timeout_seconds=timeout_seconds,
            component="standard_consultation",
            stage="care",
        ),
        _build_spec(
            agent_id=settings.synthesizer_agent_id,
            agent_version=settings.synthesizer_agent_version,
            agent_type=AgentType.STANDARD,
            model_profile=model_profile,
            prompt_template_ref="inline.standard.synthesizer.v1",
            role="负责合成标准问诊最终草稿。",
            output_contract="输出 draft_response，并可输出 evidence_bindings。",
            output_schema_ref="standard.synthesizer.v1",
            output_schema=build_draft_response_schema(),
            timeout_seconds=timeout_seconds,
            component="standard_consultation",
            stage="synthesizer",
        ),
    )


def _build_safety_trigger_specs(
    *,
    snapshot: RuntimeConfigSnapshot,
    model_profile: str,
) -> tuple[AgentSpecDto, ...]:
    """构建 SafetyTriggerAgent 相关 Agent 规格。

    :param snapshot: 当前 RuntimeConfig 快照。
    :param model_profile: 默认模型 profile ID。
    :return: SafetyTriggerAgent 相关 Agent 规格元组。
    """

    settings = snapshot.safety_trigger
    if not settings.enabled:
        return ()
    return (
        _build_spec(
            agent_id=settings.confirmation_planner_agent_id,
            agent_version=settings.confirmation_planner_agent_version,
            agent_type=AgentType.SAFETY_TRIGGER,
            model_profile=model_profile,
            prompt_template_ref="inline.safety_trigger.confirmation_planner.v1",
            role="负责为急症场景规划最多一个关键确认问题。",
            output_contract="输出 confirmation_plan；急症方向不得被追问阻塞。",
            output_schema_ref="safety_trigger.confirmation_planner.v1",
            output_schema=build_safety_confirmation_schema(),
            timeout_seconds=settings.timeouts.planner_seconds,
            component="safety_trigger",
            stage="confirmation_planner",
        ),
        _build_spec(
            agent_id=settings.writer_agent_id,
            agent_version=settings.writer_agent_version,
            agent_type=AgentType.SAFETY_TRIGGER,
            model_profile=model_profile,
            prompt_template_ref="inline.safety_trigger.writer.v1",
            role="负责生成急症安全回复草稿。",
            output_contract="输出 draft_response，必须优先给出就医方向和安全行动边界。",
            output_schema_ref="safety_trigger.writer.v1",
            output_schema=build_draft_response_schema(),
            timeout_seconds=settings.timeouts.writer_seconds,
            component="safety_trigger",
            stage="writer",
        ),
    )


def _build_education_specs(
    *,
    snapshot: RuntimeConfigSnapshot,
    model_profile: str,
) -> tuple[AgentSpecDto, ...]:
    """构建 EducationAgent 相关 Agent 规格。

    :param snapshot: 当前 RuntimeConfig 快照。
    :param model_profile: 默认模型 profile ID。
    :return: EducationAgent 相关 Agent 规格元组。
    """

    settings = snapshot.education_agent
    if not settings.enabled:
        return ()
    return (
        _build_spec(
            agent_id=settings.planner_agent_id,
            agent_version=settings.planner_agent_version,
            agent_type=AgentType.EDUCATION,
            model_profile=model_profile,
            prompt_template_ref="inline.education.planner.v1",
            role="负责规划科普解释维度。",
            output_contract="输出 dimensions 数组，维度必须来自运行选项允许集合。",
            output_schema_ref="education.planner.v1",
            output_schema=build_education_planner_schema(),
            timeout_seconds=settings.timeouts.planner_seconds,
            component="education_agent",
            stage="planner",
        ),
        _build_spec(
            agent_id=settings.retrieval_planner_agent_id,
            agent_version=settings.retrieval_planner_agent_version,
            agent_type=AgentType.EDUCATION,
            model_profile=model_profile,
            prompt_template_ref="inline.education.retrieval_planner.v1",
            role="负责规划科普知识检索查询。",
            output_contract="输出 search_queries 数组；无 RAG 结果时不得伪造来源。",
            output_schema_ref="education.retrieval_planner.v1",
            output_schema=build_retrieval_planner_schema(),
            timeout_seconds=settings.timeouts.retrieval_planner_seconds,
            component="education_agent",
            stage="retrieval_planner",
        ),
        _build_spec(
            agent_id=settings.writer_agent_id,
            agent_version=settings.writer_agent_version,
            agent_type=AgentType.EDUCATION,
            model_profile=model_profile,
            prompt_template_ref="inline.education.writer.v1",
            role="负责生成科普回复草稿。",
            output_contract="输出 draft_response，并可输出 evidence_bindings 和 section_titles。",
            output_schema_ref="education.writer.v1",
            output_schema=build_draft_response_schema(),
            timeout_seconds=settings.timeouts.writer_seconds,
            component="education_agent",
            stage="writer",
        ),
        _build_spec(
            agent_id=settings.grounding_checker_agent_id,
            agent_version=settings.grounding_checker_agent_version,
            agent_type=AgentType.EDUCATION,
            model_profile=model_profile,
            prompt_template_ref="inline.education.grounding_checker.v1",
            role="负责检查科普草稿的接地性和格式边界。",
            output_contract="输出 passed 与风险标记，不得改写原草稿正文。",
            output_schema_ref="education.grounding_checker.v1",
            output_schema=build_grounding_checker_schema(),
            timeout_seconds=settings.timeouts.grounding_seconds,
            component="education_agent",
            stage="grounding_checker",
        ),
    )


def _build_nonmedical_specs(
    *,
    snapshot: RuntimeConfigSnapshot,
    model_profile: str,
) -> tuple[AgentSpecDto, ...]:
    """构建 NonmedicalPetCareAgent 相关 Agent 规格。

    :param snapshot: 当前 RuntimeConfig 快照。
    :param model_profile: 默认模型 profile ID。
    :return: NonmedicalPetCareAgent 相关 Agent 规格元组。
    """

    settings = snapshot.nonmedical_pet_care
    if not settings.enabled:
        return ()
    return (
        _build_spec(
            agent_id=settings.planner_agent_id,
            agent_version=settings.planner_agent_version,
            agent_type=AgentType.NONMEDICAL,
            model_profile=model_profile,
            prompt_template_ref="inline.nonmedical.planner.v1",
            role="负责规划非医疗护理建议维度。",
            output_contract="输出 dimensions 数组，内容必须保持非医疗边界。",
            output_schema_ref="nonmedical.planner.v1",
            output_schema=build_nonmedical_planner_schema(),
            timeout_seconds=settings.timeouts.planner_seconds,
            component="nonmedical_pet_care",
            stage="planner",
        ),
        _build_spec(
            agent_id=settings.retrieval_planner_agent_id,
            agent_version=settings.retrieval_planner_agent_version,
            agent_type=AgentType.NONMEDICAL,
            model_profile=model_profile,
            prompt_template_ref="inline.nonmedical.retrieval_planner.v1",
            role="负责规划非医疗知识检索查询。",
            output_contract="输出 search_queries 数组；无 RAG 结果时不得伪造来源。",
            output_schema_ref="nonmedical.retrieval_planner.v1",
            output_schema=build_retrieval_planner_schema(),
            timeout_seconds=settings.timeouts.retrieval_planner_seconds,
            component="nonmedical_pet_care",
            stage="retrieval_planner",
        ),
        _build_spec(
            agent_id=settings.writer_agent_id,
            agent_version=settings.writer_agent_version,
            agent_type=AgentType.NONMEDICAL,
            model_profile=model_profile,
            prompt_template_ref="inline.nonmedical.writer.v1",
            role="负责生成非医疗护理建议草稿。",
            output_contract="输出 draft_response，不得给出处方、诊断或高风险医疗建议。",
            output_schema_ref="nonmedical.writer.v1",
            output_schema=build_draft_response_schema(),
            timeout_seconds=settings.timeouts.writer_seconds,
            component="nonmedical_pet_care",
            stage="writer",
        ),
        _build_spec(
            agent_id=settings.self_checker_agent_id,
            agent_version=settings.self_checker_agent_version,
            agent_type=AgentType.NONMEDICAL,
            model_profile=model_profile,
            prompt_template_ref="inline.nonmedical.self_checker.v1",
            role="负责检查非医疗建议草稿的安全性和实用性。",
            output_contract="输出 passed 与风险标记，不得改写原草稿正文。",
            output_schema_ref="nonmedical.self_checker.v1",
            output_schema=build_nonmedical_self_checker_schema(),
            timeout_seconds=settings.timeouts.self_check_seconds,
            component="nonmedical_pet_care",
            stage="self_checker",
        ),
    )


def build_default_agent_specs(
    snapshot: RuntimeConfigSnapshot,
) -> tuple[AgentSpecDto, ...]:
    """从 RuntimeConfig 快照构建默认 Agent 规格目录。

    :param snapshot: 当前 RuntimeConfig 快照。
    :return: 默认 Agent 规格元组；当 LlmGateway 未启用时返回空元组。
    :raises ValueError: 当 LlmGateway 已启用但没有 model profile 时抛出。
    """

    if not snapshot.llm_gateway.enabled:
        return ()
    model_profile = _select_default_model_profile(snapshot)
    specs: list[AgentSpecDto] = []
    specs.extend(
        _build_task_decomposer_specs(
            snapshot=snapshot,
            model_profile=model_profile,
        )
    )
    specs.extend(
        _build_input_safety_specs(
            snapshot=snapshot,
            model_profile=model_profile,
        )
    )
    specs.extend(
        _build_standard_consultation_specs(
            snapshot=snapshot,
            model_profile=model_profile,
        )
    )
    specs.extend(
        _build_safety_trigger_specs(
            snapshot=snapshot,
            model_profile=model_profile,
        )
    )
    specs.extend(
        _build_education_specs(
            snapshot=snapshot,
            model_profile=model_profile,
        )
    )
    specs.extend(
        _build_nonmedical_specs(
            snapshot=snapshot,
            model_profile=model_profile,
        )
    )
    return tuple(specs)


__all__: tuple[str, ...] = (
    "DEFAULT_AGENT_SPEC_CATALOG_VERSION",
    "build_default_agent_specs",
)

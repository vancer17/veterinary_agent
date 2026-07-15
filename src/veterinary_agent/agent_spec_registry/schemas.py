##################################################################################################
# 文件: src/veterinary_agent/agent_spec_registry/schemas.py
# 作用: 定义默认 AgentSpecRegistry 使用的受控输出 JSON Schema，约束业务子 Agent 的模型输出结构。
# 边界: 仅描述输出契约；不执行模型调用、不解析业务语义、不接入 RAG、工具、数据库或外部 schema 服务。
##################################################################################################

from veterinary_agent.agent_runner import JsonMap


def _string_schema(description: str | None = None) -> JsonMap:
    """构建字符串字段 schema。

    :param description: 可选字段说明。
    :return: 字符串 JSON Schema。
    """

    schema: JsonMap = {"type": "string"}
    if description is not None:
        schema["description"] = description
    return schema


def _number_schema(
    description: str | None = None,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> JsonMap:
    """构建数字字段 schema。

    :param description: 可选字段说明。
    :param minimum: 可选最小值约束。
    :param maximum: 可选最大值约束。
    :return: 数字 JSON Schema。
    """

    schema: JsonMap = {"type": "number"}
    if description is not None:
        schema["description"] = description
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    return schema


def _integer_schema(
    description: str | None = None,
    *,
    minimum: int | None = None,
) -> JsonMap:
    """构建整数字段 schema。

    :param description: 可选字段说明。
    :param minimum: 可选最小值约束。
    :return: 整数 JSON Schema。
    """

    schema: JsonMap = {"type": "integer"}
    if description is not None:
        schema["description"] = description
    if minimum is not None:
        schema["minimum"] = minimum
    return schema


def _boolean_schema(description: str | None = None) -> JsonMap:
    """构建布尔字段 schema。

    :param description: 可选字段说明。
    :return: 布尔 JSON Schema。
    """

    schema: JsonMap = {"type": "boolean"}
    if description is not None:
        schema["description"] = description
    return schema


def _array_schema(
    item_schema: JsonMap,
    description: str | None = None,
) -> JsonMap:
    """构建数组字段 schema。

    :param item_schema: 数组元素 schema。
    :param description: 可选字段说明。
    :return: 数组 JSON Schema。
    """

    schema: JsonMap = {"type": "array", "items": item_schema}
    if description is not None:
        schema["description"] = description
    return schema


def _string_array_schema(description: str | None = None) -> JsonMap:
    """构建字符串数组字段 schema。

    :param description: 可选字段说明。
    :return: 字符串数组 JSON Schema。
    """

    return _array_schema(_string_schema(), description)


def _object_schema(
    *,
    properties: JsonMap,
    required: tuple[str, ...] = (),
    additional_properties: bool = True,
    description: str | None = None,
) -> JsonMap:
    """构建对象字段 schema。

    :param properties: 对象属性 schema 映射。
    :param required: 必填属性名。
    :param additional_properties: 是否允许额外字段。
    :param description: 可选字段说明。
    :return: 对象 JSON Schema。
    """

    schema: JsonMap = {
        "type": "object",
        "properties": properties,
        "additionalProperties": additional_properties,
    }
    if required:
        schema["required"] = list(required)
    if description is not None:
        schema["description"] = description
    return schema


def _loose_object_schema(description: str | None = None) -> JsonMap:
    """构建宽松对象字段 schema。

    :param description: 可选字段说明。
    :return: 允许额外字段的对象 JSON Schema。
    """

    return _object_schema(properties={}, description=description)


def _schema_root(
    *,
    title: str,
    properties: JsonMap,
    required: tuple[str, ...] = (),
) -> JsonMap:
    """构建 Agent 输出根 schema。

    :param title: schema 标题。
    :param properties: 根对象属性 schema 映射。
    :param required: 根对象必填属性名。
    :return: Agent 输出根 JSON Schema。
    """

    return _object_schema(
        properties={"schema_version": _string_schema("输出契约版本。"), **properties},
        required=required,
        additional_properties=True,
        description=title,
    )


def build_task_decomposition_schema() -> JsonMap:
    """构建任务拆解 Agent 输出 schema。

    :return: 任务拆解 Agent 输出 JSON Schema。
    """

    span_schema = _object_schema(
        properties={
            "start_offset": _integer_schema("用户原文起始字符位置。", minimum=0),
            "end_offset": _integer_schema("用户原文结束字符位置。", minimum=0),
        },
        required=("start_offset", "end_offset"),
    )
    task_schema = _object_schema(
        properties={
            "task_type": _string_schema("归一化任务类型。"),
            "source_span": span_schema,
            "source_text": _string_schema("任务对应的用户原文片段。"),
            "normalized_query": _string_schema("归一化任务描述。"),
            "priority_hint": _string_schema("任务优先级提示。"),
            "coverage_required": _boolean_schema("是否必须覆盖该任务。"),
            "requires_independent_segment": _boolean_schema("是否应独立生成回复段落。"),
            "confidence": _number_schema("任务拆解置信度。", minimum=0.0, maximum=1.0),
            "attachment_bindings": _array_schema(
                _loose_object_schema("附件绑定摘要。"),
                "与任务相关的附件绑定列表。",
            ),
        },
        required=("task_type", "source_span", "source_text", "normalized_query"),
    )
    return _schema_root(
        title="任务拆解 Agent 输出契约。",
        properties={
            "tasks": _array_schema(task_schema, "拆解出的任务列表。"),
            "repair_notes": _string_array_schema("审查修复备注。"),
        },
        required=("tasks",),
    )


def build_input_safety_arbitration_schema() -> JsonMap:
    """构建输入安全仲裁 Agent 输出 schema。

    :return: 输入安全仲裁 Agent 输出 JSON Schema。
    """

    return _schema_root(
        title="输入安全仲裁 Agent 输出契约。",
        properties={
            "intent": _string_schema("归一化安全意图。"),
            "intent_confidence": _number_schema(
                "意图置信度。",
                minimum=0.0,
                maximum=1.0,
            ),
            "route": _string_schema("推荐业务路由。"),
            "executor_key": _string_schema("推荐执行器键。"),
            "compression_strategy": _string_schema("上下文压缩策略。"),
            "generation_profile": _string_schema("可选生成 profile。"),
            "reason_code": _string_schema("仲裁原因代码。"),
        },
        required=(
            "intent",
            "intent_confidence",
            "route",
            "executor_key",
            "compression_strategy",
            "reason_code",
        ),
    )


def build_standard_question_schema() -> JsonMap:
    """构建标准问诊追问采集 Agent 输出 schema。

    :return: 标准问诊追问采集 Agent 输出 JSON Schema。
    """

    question_schema = _object_schema(
        properties={
            "question_id": _string_schema("问题 ID。"),
            "question_text": _string_schema("面向用户的追问文本。"),
            "target_fact_key": _string_schema("目标事实键。"),
            "target_layer": _string_schema("目标问诊层级。"),
            "purpose": _string_schema("追问目的。"),
            "risk_impact": _string_schema("信息缺失的风险影响。"),
            "information_gain": _number_schema(
                "预估信息增益。",
                minimum=0.0,
                maximum=1.0,
            ),
            "evidence_ids": _string_array_schema("关联证据 ID。"),
        },
        required=("question_text", "target_fact_key"),
    )
    return _schema_root(
        title="标准问诊追问采集 Agent 输出契约。",
        properties={
            "candidate_questions": _array_schema(
                question_schema,
                "候选追问列表。",
            )
        },
        required=("candidate_questions",),
    )


def build_standard_triage_schema() -> JsonMap:
    """构建标准问诊分诊 Agent 输出 schema。

    :return: 标准问诊分诊 Agent 输出 JSON Schema。
    """

    return _schema_root(
        title="标准问诊分诊 Agent 输出契约。",
        properties={
            "triage_summary": _loose_object_schema("分诊摘要。"),
            "escalation_request": _loose_object_schema("可选升级请求。"),
        },
        required=("triage_summary",),
    )


def build_standard_direction_schema() -> JsonMap:
    """构建标准问诊方向提示 Agent 输出 schema。

    :return: 标准问诊方向提示 Agent 输出 JSON Schema。
    """

    return _schema_root(
        title="标准问诊方向提示 Agent 输出契约。",
        properties={
            "direction_hints": _array_schema(
                _loose_object_schema("方向提示项。"),
                "方向提示列表。",
            )
        },
        required=("direction_hints",),
    )


def build_standard_differential_schema() -> JsonMap:
    """构建标准问诊鉴别方向 Agent 输出 schema。

    :return: 标准问诊鉴别方向 Agent 输出 JSON Schema。
    """

    return _schema_root(
        title="标准问诊鉴别方向 Agent 输出契约。",
        properties={
            "differential_hypotheses": _array_schema(
                _loose_object_schema("鉴别方向项。"),
                "鉴别方向列表。",
            )
        },
        required=("differential_hypotheses",),
    )


def build_standard_care_schema() -> JsonMap:
    """构建标准问诊护理建议 Agent 输出 schema。

    :return: 标准问诊护理建议 Agent 输出 JSON Schema。
    """

    return _schema_root(
        title="标准问诊护理建议 Agent 输出契约。",
        properties={
            "care_suggestions": _array_schema(
                _loose_object_schema("护理建议项。"),
                "护理建议列表。",
            )
        },
        required=("care_suggestions",),
    )


def build_draft_response_schema() -> JsonMap:
    """构建通用草稿写作 Agent 输出 schema。

    :return: 通用草稿写作 Agent 输出 JSON Schema。
    """

    return _schema_root(
        title="草稿写作 Agent 输出契约。",
        properties={
            "draft_response": _string_schema("面向用户的草稿正文。"),
            "evidence_bindings": _array_schema(
                _loose_object_schema("证据绑定项。"),
                "可选证据绑定列表。",
            ),
            "section_titles": _string_array_schema("可选章节标题。"),
        },
        required=("draft_response",),
    )


def build_safety_confirmation_schema() -> JsonMap:
    """构建急症关键确认规划 Agent 输出 schema。

    :return: 急症关键确认规划 Agent 输出 JSON Schema。
    """

    plan_schema = _object_schema(
        properties={
            "mode": _string_schema("确认模式。"),
            "confirmation_text": _string_schema("关键确认问题文本。"),
            "blocks_vet_direction": _boolean_schema("是否阻塞就医方向输出。"),
            "reason_code": _string_schema("规划原因代码。"),
        },
        required=("mode",),
    )
    return _schema_root(
        title="急症关键确认规划 Agent 输出契约。",
        properties={"confirmation_plan": plan_schema},
        required=("confirmation_plan",),
    )


def build_education_planner_schema() -> JsonMap:
    """构建科普解释规划 Agent 输出 schema。

    :return: 科普解释规划 Agent 输出 JSON Schema。
    """

    dimension_schema = _object_schema(
        properties={
            "dimension_code": _string_schema("解释维度代码。"),
            "focus": _string_schema("本维度解释重点。"),
            "priority": _integer_schema("展示优先级。", minimum=1),
        },
        required=("dimension_code",),
    )
    return _schema_root(
        title="科普解释规划 Agent 输出契约。",
        properties={
            "dimensions": _array_schema(dimension_schema, "解释维度列表。"),
            "generation_constraints": _loose_object_schema("生成约束。"),
        },
        required=("dimensions",),
    )


def build_retrieval_planner_schema() -> JsonMap:
    """构建知识检索计划 Agent 输出 schema。

    :return: 知识检索计划 Agent 输出 JSON Schema。
    """

    query_schema = _object_schema(
        properties={
            "query": _string_schema("检索查询。"),
            "facet": _string_schema("检索 facet。"),
            "dimension_code": _string_schema("关联维度代码。"),
            "top_k": _integer_schema("期望返回条数。", minimum=1),
        },
        required=("query",),
    )
    return _schema_root(
        title="知识检索计划 Agent 输出契约。",
        properties={
            "search_queries": _array_schema(query_schema, "检索查询列表。"),
            "retrieval_constraints": _loose_object_schema("检索约束。"),
        },
        required=("search_queries",),
    )


def build_grounding_checker_schema() -> JsonMap:
    """构建科普接地性自检 Agent 输出 schema。

    :return: 科普接地性自检 Agent 输出 JSON Schema。
    """

    return _schema_root(
        title="科普接地性自检 Agent 输出契约。",
        properties={
            "passed": _boolean_schema("是否通过自检。"),
            "risk_flags": _string_array_schema("风险标记。"),
            "unsupported_claims": _string_array_schema("未支撑断言。"),
            "forbidden_format_detected": _boolean_schema("是否检测到禁用格式。"),
            "t4_risk_detected": _boolean_schema("是否检测到 T4 风险。"),
            "reference_range_risk_detected": _boolean_schema(
                "是否检测到参考范围风险。"
            ),
            "restricted_source_risk_detected": _boolean_schema(
                "是否检测到受限来源风险。"
            ),
        },
        required=("passed",),
    )


def build_nonmedical_planner_schema() -> JsonMap:
    """构建非医疗建议规划 Agent 输出 schema。

    :return: 非医疗建议规划 Agent 输出 JSON Schema。
    """

    dimension_schema = _object_schema(
        properties={
            "dimension_code": _string_schema("建议维度代码。"),
            "focus": _string_schema("本维度建议重点。"),
            "priority": _integer_schema("展示优先级。", minimum=1),
        },
        required=("dimension_code",),
    )
    return _schema_root(
        title="非医疗建议规划 Agent 输出契约。",
        properties={
            "dimensions": _array_schema(dimension_schema, "建议维度列表。"),
            "goal_summary": _string_schema("用户目标摘要。"),
            "generation_constraints": _loose_object_schema("生成约束。"),
        },
        required=("dimensions",),
    )


def build_nonmedical_self_checker_schema() -> JsonMap:
    """构建非医疗安全实用性自检 Agent 输出 schema。

    :return: 非医疗安全实用性自检 Agent 输出 JSON Schema。
    """

    return _schema_root(
        title="非医疗安全实用性自检 Agent 输出契约。",
        properties={
            "passed": _boolean_schema("是否通过自检。"),
            "risk_flags": _string_array_schema("风险标记。"),
            "extreme_diet_detected": _boolean_schema("是否检测到极端饮食建议。"),
            "punitive_training_detected": _boolean_schema("是否检测到惩罚式训练建议。"),
            "medical_signal_ignored": _boolean_schema("是否忽略医疗信号。"),
            "medication_boundary_detected": _boolean_schema("是否触碰用药边界。"),
            "overpromise_detected": _boolean_schema("是否存在过度承诺。"),
            "personalization_hallucination_detected": _boolean_schema(
                "是否存在个性化幻觉。"
            ),
        },
        required=("passed",),
    )


__all__: tuple[str, ...] = (
    "build_draft_response_schema",
    "build_education_planner_schema",
    "build_grounding_checker_schema",
    "build_input_safety_arbitration_schema",
    "build_nonmedical_planner_schema",
    "build_nonmedical_self_checker_schema",
    "build_retrieval_planner_schema",
    "build_safety_confirmation_schema",
    "build_standard_care_schema",
    "build_standard_differential_schema",
    "build_standard_direction_schema",
    "build_standard_question_schema",
    "build_standard_triage_schema",
    "build_task_decomposition_schema",
)

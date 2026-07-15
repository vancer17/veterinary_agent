##################################################################################################
# 文件: src/veterinary_agent/vet_conversation_graph/state_adapters.py
# 作用: 提供兽医主业务图内部使用的状态适配节点，衔接任务选择、执行器路由、护栏请求与 Composer 分支状态。
# 边界: 只做 LangGraph business_state 的结构转换与安全降级模板，不实现医疗判断、模型调用、存储访问或发布逻辑。
##################################################################################################

from collections.abc import Mapping, Sequence

from veterinary_agent.graph_runtime import (
    GraphNodeExecutionContext,
    GraphNodeResult,
    GraphState,
    JsonMap,
)
from veterinary_agent.guardrail_framework import GuardrailStage
from veterinary_agent.vet_response_composer import (
    ComposerBranchType,
    ComposerGuardStatus,
    ComposerSegmentType,
)

STANDARD_EXECUTOR_NODE_ID = "standard_consultation_agent"
EDUCATION_EXECUTOR_NODE_ID = "education_agent"
SAFETY_EXECUTOR_NODE_ID = "safety_trigger_agent"
NONMEDICAL_EXECUTOR_NODE_ID = "nonmedical_pet_care_agent"

_SAFE_DEGRADED_TEMPLATE_VERSION = "vet-conversation-graph.safe-degraded.v1"


def _as_mapping(value: object) -> Mapping[str, object] | None:
    """将未知值安全读取为字符串键映射。

    :param value: 需要读取的未知值。
    :return: 字符串键映射；无法读取时返回 None。
    """

    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


def _as_list(value: object) -> list[object]:
    """将未知值安全读取为普通列表。

    :param value: 需要读取的未知值。
    :return: 若输入为列表或元组则返回列表，否则返回空列表。
    """

    if isinstance(value, list | tuple):
        return list(value)
    return []


def _read_string(value: object) -> str | None:
    """从未知值中读取非空字符串。

    :param value: 需要读取的未知值。
    :return: 去除首尾空白后的字符串；无法读取时返回 None。
    """

    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _read_bool(value: object) -> bool | None:
    """从未知值中读取布尔值。

    :param value: 需要读取的未知值。
    :return: 若输入为布尔值则返回该值，否则返回 None。
    """

    if isinstance(value, bool):
        return value
    return None


def _first_mapping(*values: object) -> Mapping[str, object]:
    """按顺序读取首个可用映射。

    :param values: 候选未知值序列。
    :return: 首个字符串键映射；均不可用时返回空映射。
    """

    for value in values:
        mapping = _as_mapping(value)
        if mapping is not None:
            return mapping
    return {}


def _list_of_mappings(value: object) -> list[JsonMap]:
    """将未知列表过滤为 JSON 映射列表。

    :param value: 需要读取的未知列表。
    :return: 过滤非映射元素后的 JSON 映射列表。
    """

    return [
        dict(item)
        for item in (_as_mapping(raw_item) for raw_item in _as_list(value))
        if item is not None
    ]


def _branch_type_for_executor(executor_key: str | None) -> str:
    """根据执行器键名解析 Composer 分支类型。

    :param executor_key: 输入安全评估产出的执行器键名。
    :return: Composer 可识别的业务分支类型。
    """

    if executor_key == "safety_trigger":
        return ComposerBranchType.SAFETY_TRIGGER.value
    if executor_key == "education":
        return ComposerBranchType.EDUCATION.value
    if executor_key == "nonmedical_pet_care":
        return ComposerBranchType.NONMEDICAL_PET_CARE.value
    return ComposerBranchType.STANDARD_CONSULTATION.value


def _segment_type_for_branch(branch_type: str) -> str:
    """根据分支类型解析用户可见 segment 类型。

    :param branch_type: Composer 分支类型。
    :return: Composer segment 类型字符串。
    """

    if branch_type == ComposerBranchType.SAFETY_TRIGGER.value:
        return ComposerSegmentType.SAFETY.value
    if branch_type == ComposerBranchType.EDUCATION.value:
        return ComposerSegmentType.EDUCATION.value
    if branch_type == ComposerBranchType.NONMEDICAL_PET_CARE.value:
        return ComposerSegmentType.NONMEDICAL.value
    return ComposerSegmentType.MEDICAL.value


def _is_safety_branch(branch_type: str) -> bool:
    """判断分支是否为急症安全分支。

    :param branch_type: Composer 分支类型。
    :return: 若分支属于急症链路则返回 True。
    """

    return branch_type == ComposerBranchType.SAFETY_TRIGGER.value


class TaskLaneSelectorGraphNode:
    """将批量评估结果收敛为当前 MVP 单任务主通道。"""

    async def __call__(
        self,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        """选择本轮主任务并写入后续单任务节点需要的 state 键。

        :param state: 当前图运行状态。
        :param context: 当前节点执行上下文。
        :return: 包含主任务、分支种子与上下文构建请求的节点结果。
        """

        context_requests = self._read_context_requests(state=state)
        selected_request = self._select_primary_request(context_requests)
        task_id = _read_string(selected_request.get("task_id")) or "task_primary"
        assessment = self._read_assessment(state=state, task_id=task_id)
        generation_profile = _read_string(
            selected_request.get("generation_profile")
        ) or _read_string(assessment.get("generation_profile"))
        executor_key = _read_string(selected_request.get("executor_key")) or (
            _read_string(assessment.get("executor_key")) or "standard_consultation"
        )
        branch_type = _branch_type_for_executor(executor_key)
        normalized_request = self._build_context_request(
            selected_request=selected_request,
            assessment=assessment,
            task_id=task_id,
            generation_profile=generation_profile,
            executor_key=executor_key,
        )
        return GraphNodeResult(
            state_patch={
                "context_build_request": normalized_request,
                "selected_task_id": task_id,
                "task_id": task_id,
                "task_type": normalized_request.get("task_type") or "UNDECOMPOSED",
                "normalized_query": normalized_request.get("normalized_query") or "",
                "generation_profile": generation_profile,
                "executor_key": executor_key,
                "route": normalized_request.get("route") or assessment.get("route"),
                "audit_tier": normalized_request.get("audit_tier"),
                "assessment_summary": normalized_request.get("assessment_summary")
                or {},
                "branch_id": f"branch_{task_id}",
                "branch_type": branch_type,
                "medical_content_expected": branch_type
                != ComposerBranchType.NONMEDICAL_PET_CARE.value,
                "selected_context_build_request": normalized_request,
            }
        )

    def _read_context_requests(
        self,
        *,
        state: GraphState,
    ) -> Sequence[Mapping[str, object]]:
        """读取输入安全节点产出的上下文构建请求列表。

        :param state: 当前图运行状态。
        :return: 至少包含一个元素的上下文构建请求列表。
        """

        context_requests = _list_of_mappings(state.get("context_build_requests"))
        if context_requests:
            return context_requests
        single_request = _as_mapping(state.get("context_build_request"))
        if single_request is not None:
            return [single_request]
        return [
            {
                "task_id": "task_primary",
                "task_type": state.get("task_type") or "UNDECOMPOSED",
                "normalized_query": state.get("original_user_message") or "",
                "executor_key": "standard_consultation",
                "generation_profile": "standard",
                "route": "normal",
                "assessment_summary": {},
                "observed_facts": [],
            }
        ]

    def _select_primary_request(
        self,
        context_requests: Sequence[Mapping[str, object]],
    ) -> Mapping[str, object]:
        """选择当前 MVP 主任务请求。

        :param context_requests: 候选上下文构建请求列表。
        :return: 被选为主通道的上下文构建请求。
        """

        for request in context_requests:
            executor_key = _read_string(request.get("executor_key"))
            route = _read_string(request.get("route"))
            generation_profile = _read_string(request.get("generation_profile"))
            if "safety_trigger" in {executor_key, route, generation_profile}:
                return request
        return context_requests[0]

    def _read_assessment(
        self,
        *,
        state: GraphState,
        task_id: str,
    ) -> Mapping[str, object]:
        """读取指定任务的输入安全评估结果。

        :param state: 当前图运行状态。
        :param task_id: 需要读取的任务 ID。
        :return: 命中的评估结果映射；缺失时返回空映射。
        """

        by_task_id = _as_mapping(state.get("vet_input_assessment_result_by_task_id"))
        if by_task_id is not None:
            assessment = _as_mapping(by_task_id.get(task_id))
            if assessment is not None:
                return assessment
        return _first_mapping(state.get("vet_input_assessment_result"))

    def _build_context_request(
        self,
        *,
        selected_request: Mapping[str, object],
        assessment: Mapping[str, object],
        task_id: str,
        generation_profile: str | None,
        executor_key: str,
    ) -> JsonMap:
        """构建后续 ContextBuilder 使用的单任务请求。

        :param selected_request: 输入安全节点产出的候选上下文请求。
        :param assessment: 当前任务的输入安全评估结果。
        :param task_id: 当前任务 ID。
        :param generation_profile: 当前任务生成剖面。
        :param executor_key: 当前任务执行器键名。
        :return: 已补齐关键路由字段的上下文请求映射。
        """

        return {
            **dict(selected_request),
            "task_id": task_id,
            "generation_profile": generation_profile,
            "executor_key": executor_key,
            "route": selected_request.get("route") or assessment.get("route"),
            "audit_tier": selected_request.get("audit_tier")
            or assessment.get("audit_tier_floor"),
            "assessment_summary": selected_request.get("assessment_summary")
            or assessment.get("assessment_summary")
            or {},
            "observed_facts": selected_request.get("observed_facts") or [],
        }


class ExecutorRouterGraphNode:
    """根据输入安全评估产出的执行器选择具体业务 Agent 节点。"""

    def __init__(
        self,
        *,
        standard_node_id: str = STANDARD_EXECUTOR_NODE_ID,
        education_node_id: str = EDUCATION_EXECUTOR_NODE_ID,
        safety_node_id: str = SAFETY_EXECUTOR_NODE_ID,
        nonmedical_node_id: str = NONMEDICAL_EXECUTOR_NODE_ID,
    ) -> None:
        """初始化执行器路由节点。

        :param standard_node_id: 标准问诊节点 ID。
        :param education_node_id: 科普节点 ID。
        :param safety_node_id: 急症节点 ID。
        :param nonmedical_node_id: 非医疗养宠节点 ID。
        :return: None。
        """

        self._standard_node_id = standard_node_id
        self._education_node_id = education_node_id
        self._safety_node_id = safety_node_id
        self._nonmedical_node_id = nonmedical_node_id

    async def __call__(
        self,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        """选择后续业务 Agent 节点。

        :param state: 当前图运行状态。
        :param context: 当前节点执行上下文。
        :return: 带条件路由选择结果的节点结果。
        """

        del context
        executor_key = _read_string(state.get("executor_key"))
        generation_profile = _read_string(state.get("generation_profile"))
        selected_node = self._select_node(
            executor_key=executor_key,
            generation_profile=generation_profile,
        )
        return GraphNodeResult(
            state_patch={"selected_executor_node_id": selected_node},
            selected_next_nodes=(selected_node,),
        )

    def _select_node(
        self,
        *,
        executor_key: str | None,
        generation_profile: str | None,
    ) -> str:
        """解析执行器对应的图节点 ID。

        :param executor_key: 输入安全评估产出的执行器键名。
        :param generation_profile: 输入安全评估产出的生成剖面。
        :return: 后续业务 Agent 节点 ID。
        """

        if executor_key == "safety_trigger" or generation_profile == "safety_trigger":
            return self._safety_node_id
        if executor_key == "education" or generation_profile == "education":
            return self._education_node_id
        if executor_key == "nonmedical_pet_care":
            return self._nonmedical_node_id
        return self._standard_node_id


class GuardrailRequestBuilderGraphNode:
    """构建 GuardrailFramework 图节点消费的请求。"""

    def __init__(
        self,
        *,
        stage: GuardrailStage,
        output_state_key: str = "guardrail_request",
        previous_result_state_key: str | None = None,
    ) -> None:
        """初始化护栏请求构建节点。

        :param stage: 当前构建请求对应的护栏阶段。
        :param output_state_key: 写回 graph state 的请求键名。
        :param previous_result_state_key: 可选上一个护栏阶段结果键名。
        :return: None。
        """

        self._stage = stage
        self._output_state_key = output_state_key
        self._previous_result_state_key = previous_result_state_key

    async def __call__(
        self,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        """构建当前护栏阶段请求。

        :param state: 当前图运行状态。
        :param context: 当前节点执行上下文。
        :return: 包含护栏请求的节点结果。
        """

        request = self._build_guardrail_request(state=state, context=context)
        return GraphNodeResult(state_patch={self._output_state_key: request})

    def _build_guardrail_request(
        self,
        *,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> JsonMap:
        """从业务图 state 构建护栏请求映射。

        :param state: 当前图运行状态。
        :param context: 当前节点执行上下文。
        :return: GuardrailFramework 可校验的请求映射。
        """

        task_id = _read_string(state.get("task_id")) or "task_primary"
        segment_id = _read_string(state.get("segment_id")) or f"segment_{task_id}"
        executor_key = (
            _read_string(state.get("executor_key")) or "standard_consultation"
        )
        generation_profile = self._read_generation_profile(state=state)
        draft_response_ref = _read_string(state.get("draft_response_ref")) or (
            f"draft:{context.trace_id}:{task_id}"
        )
        draft_response = _read_string(state.get("draft_response")) or (
            "当前业务 Agent 未能生成可审查草稿。"
        )
        previous_result = self._read_previous_result(state=state)
        return {
            "stage": self._stage.value,
            "context": {
                "run_id": context.run_id,
                "trace_id": context.trace_id,
                "request_id": context.request_id,
                "session_id": context.session_id,
                "user_id": context.user_id,
                "pet_id": context.current_pet_id,
                "task_id": task_id,
                "segment_id": segment_id,
                "generation_profile": generation_profile,
                "params_version": context.params_version,
                "config_snapshot_id": context.config_snapshot_id,
            },
            "task_input": {
                "draft_response": draft_response,
                "draft_response_text": draft_response,
                "draft_response_ref": draft_response_ref,
                "executor_key": executor_key,
                "assessment_summary": state.get("assessment_summary") or {},
                "input_context": self._build_input_context(
                    state=state,
                    executor_key=executor_key,
                ),
                "previous_guardrail_result": previous_result,
            },
            "candidate_text_ref": self._candidate_text_ref(
                draft_response_ref=draft_response_ref,
                previous_result=previous_result,
            ),
            "runtime_metadata": {
                "branch_id": state.get("branch_id"),
                "branch_type": state.get("branch_type"),
                "stage": self._stage.value,
            },
        }

    def _read_generation_profile(self, *, state: GraphState) -> str:
        """读取护栏阶段需要的生成剖面。

        :param state: 当前图运行状态。
        :return: 非空生成剖面字符串。
        """

        generation_profile = _read_string(state.get("generation_profile"))
        if generation_profile is not None:
            return generation_profile
        executor_key = _read_string(state.get("executor_key"))
        if executor_key == "nonmedical_pet_care":
            return "nonmedical"
        return "standard"

    def _read_previous_result(self, *, state: GraphState) -> JsonMap | None:
        """读取上一个护栏阶段结果。

        :param state: 当前图运行状态。
        :return: 上一个护栏阶段结果映射；未配置或缺失时返回 None。
        """

        if self._previous_result_state_key is None:
            return None
        previous_result = _as_mapping(state.get(self._previous_result_state_key))
        if previous_result is None:
            return None
        return dict(previous_result)

    def _build_input_context(
        self,
        *,
        state: GraphState,
        executor_key: str,
    ) -> JsonMap:
        """构建输出安全审查使用的输入上下文摘要。

        :param state: 当前图运行状态。
        :param executor_key: 当前业务执行器键名。
        :return: 输出安全审查 handler 可消费的上下文摘要。
        """

        return {
            "executor_key": executor_key,
            "assessment_summary": state.get("assessment_summary") or {},
            "signal_codes": self._read_signal_codes(state=state),
            "rag_summary": self._read_rag_summary(state=state),
            "evidence_bindings": self._read_evidence_bindings(state=state),
            "context_summary_ref": self._context_summary_ref(state=state),
            "medical_content_expected": _read_bool(
                state.get("medical_content_expected")
            ),
            "metadata": {
                "branch_id": state.get("branch_id"),
                "branch_type": state.get("branch_type"),
                "selected_executor_node_id": state.get("selected_executor_node_id"),
            },
        }

    def _read_signal_codes(self, *, state: GraphState) -> list[str]:
        """从输入安全摘要中读取信号码。

        :param state: 当前图运行状态。
        :return: 字符串信号码列表。
        """

        assessment_summary = _as_mapping(state.get("assessment_summary")) or {}
        return [
            signal_code
            for signal_code in (
                _read_string(item)
                for item in _as_list(assessment_summary.get("signals"))
            )
            if signal_code is not None
        ]

    def _read_rag_summary(self, *, state: GraphState) -> JsonMap:
        """读取当前业务 Agent 产出的 RAG 摘要。

        :param state: 当前图运行状态。
        :return: RAG 摘要映射；缺失时返回空映射。
        """

        return dict(
            _first_mapping(
                state.get("education_rag_summary"),
                state.get("nonmedical_rag_summary"),
                state.get("standard_rag_summary"),
                state.get("safety_trigger_rag_summary"),
            )
        )

    def _read_evidence_bindings(self, *, state: GraphState) -> list[JsonMap]:
        """读取当前业务 Agent 产出的证据绑定。

        :param state: 当前图运行状态。
        :return: 证据绑定映射列表。
        """

        return _list_of_mappings(
            state.get("education_evidence_bindings")
            or state.get("standard_evidence_bindings")
            or []
        )

    def _context_summary_ref(self, *, state: GraphState) -> str | None:
        """读取上下文摘要引用。

        :param state: 当前图运行状态。
        :return: 上下文摘要引用；缺失时返回 None。
        """

        context_bundle = _as_mapping(state.get("context_bundle")) or {}
        summary = _as_mapping(context_bundle.get("summary")) or {}
        return _read_string(summary.get("summary_ref")) or _read_string(
            context_bundle.get("bundle_ref")
        )

    def _candidate_text_ref(
        self,
        *,
        draft_response_ref: str,
        previous_result: Mapping[str, object] | None,
    ) -> str:
        """解析当前护栏阶段候选文本引用。

        :param draft_response_ref: 原始草稿引用。
        :param previous_result: 上一个护栏阶段结果。
        :return: 当前阶段候选文本引用。
        """

        if previous_result is None:
            return draft_response_ref
        return (
            _read_string(previous_result.get("reviewed_text_ref"))
            or _read_string(previous_result.get("final_text_ref"))
            or draft_response_ref
        )


class BranchStateBuilderGraphNode:
    """将护栏结果转换为 VetResponseComposer 可消费的分支状态。"""

    def __init__(
        self,
        *,
        deterministic_gate_result_key: str = "deterministic_gate_result",
        review_result_key: str = "post_generation_review_result",
        output_state_key: str = "branch_execution_states",
    ) -> None:
        """初始化分支状态构建节点。

        :param deterministic_gate_result_key: 确定性发布门结果键名。
        :param review_result_key: 输出安全审查结果键名。
        :param output_state_key: 写回 Composer 分支状态列表的键名。
        :return: None。
        """

        self._deterministic_gate_result_key = deterministic_gate_result_key
        self._review_result_key = review_result_key
        self._output_state_key = output_state_key

    async def __call__(
        self,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        """构建当前主任务的 Composer 分支状态。

        :param state: 当前图运行状态。
        :param context: 当前节点执行上下文。
        :return: 包含 Composer 分支状态的节点结果。
        """

        branch = self._build_branch_state(state=state, context=context)
        return GraphNodeResult(
            state_patch={
                self._output_state_key: [branch],
                "branches": [branch],
                "triggered_branches": [branch],
                "publishable_segment": branch.get("publishable_segment"),
            }
        )

    def _build_branch_state(
        self,
        *,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> JsonMap:
        """构建单个业务分支状态。

        :param state: 当前图运行状态。
        :param context: 当前节点执行上下文。
        :return: Composer 分支状态映射。
        """

        task_id = _read_string(state.get("task_id")) or "task_primary"
        branch_type = (
            _read_string(state.get("branch_type")) or ComposerBranchType.OTHER.value
        )
        gate_result = _as_mapping(state.get(self._deterministic_gate_result_key)) or {}
        review_result = _as_mapping(state.get(self._review_result_key)) or {}
        publishable_segment = self._build_publishable_segment(
            state=state,
            context=context,
            task_id=task_id,
            branch_type=branch_type,
            gate_result=gate_result,
            review_result=review_result,
        )
        return {
            "branch_id": _read_string(state.get("branch_id")) or f"branch_{task_id}",
            "task_id": task_id,
            "branch_type": branch_type,
            "generation_profile": state.get("generation_profile"),
            "executor_key": state.get("executor_key"),
            "status": "completed",
            "publishable_segment": publishable_segment,
            "trace_patch_ref": self._trace_patch_ref(state=state),
        }

    def _build_publishable_segment(
        self,
        *,
        state: GraphState,
        context: GraphNodeExecutionContext,
        task_id: str,
        branch_type: str,
        gate_result: Mapping[str, object],
        review_result: Mapping[str, object],
    ) -> JsonMap:
        """构建安全发布候选段。

        :param state: 当前图运行状态。
        :param context: 当前节点执行上下文。
        :param task_id: 当前任务 ID。
        :param branch_type: 当前分支类型。
        :param gate_result: 确定性发布门结果。
        :param review_result: 输出安全审查结果。
        :return: Composer 可发布 segment 映射。
        """

        draft_response = _read_string(state.get("draft_response"))
        publish_allowed = _read_bool(gate_result.get("publish_allowed")) is True
        if publish_allowed and draft_response is not None:
            return self._build_gate_passed_segment(
                state=state,
                task_id=task_id,
                branch_type=branch_type,
                final_response=draft_response,
                gate_result=gate_result,
                review_result=review_result,
            )
        return self._build_safe_degraded_segment(
            state=state,
            context=context,
            task_id=task_id,
            branch_type=branch_type,
            gate_result=gate_result,
            review_result=review_result,
        )

    def _build_gate_passed_segment(
        self,
        *,
        state: GraphState,
        task_id: str,
        branch_type: str,
        final_response: str,
        gate_result: Mapping[str, object],
        review_result: Mapping[str, object],
    ) -> JsonMap:
        """构建发布门已通过的候选段。

        :param state: 当前图运行状态。
        :param task_id: 当前任务 ID。
        :param branch_type: 当前分支类型。
        :param final_response: 已允许发布的正文。
        :param gate_result: 确定性发布门结果。
        :param review_result: 输出安全审查结果。
        :return: 可发布候选段映射。
        """

        return {
            "segment_id": _read_string(state.get("segment_id")) or f"segment_{task_id}",
            "branch_id": _read_string(state.get("branch_id")) or f"branch_{task_id}",
            "task_id": task_id,
            "segment_type": _segment_type_for_branch(branch_type),
            "final_response": final_response,
            "final_response_ref": _read_string(gate_result.get("final_text_ref"))
            or _read_string(review_result.get("reviewed_text_ref"))
            or _read_string(state.get("draft_response_ref")),
            "title": self._segment_title(branch_type=branch_type),
            "guard_status": ComposerGuardStatus.GATE_PASSED.value,
            "fallback_triggered": False,
            "audit_tier": _read_string(state.get("audit_tier")) or "L2",
            "publish_allowed": True,
            "safety_direction_present": True
            if _is_safety_branch(branch_type)
            else None,
            "source_stage": "final_response",
            "metadata": self._segment_metadata(
                state=state,
                gate_result=gate_result,
                review_result=review_result,
            ),
        }

    def _build_safe_degraded_segment(
        self,
        *,
        state: GraphState,
        context: GraphNodeExecutionContext,
        task_id: str,
        branch_type: str,
        gate_result: Mapping[str, object],
        review_result: Mapping[str, object],
    ) -> JsonMap:
        """构建发布门未通过时的安全降级候选段。

        :param state: 当前图运行状态。
        :param context: 当前节点执行上下文。
        :param task_id: 当前任务 ID。
        :param branch_type: 当前分支类型。
        :param gate_result: 确定性发布门结果。
        :param review_result: 输出安全审查结果。
        :return: 模板安全的可发布候选段映射。
        """

        return {
            "segment_id": _read_string(state.get("segment_id")) or f"segment_{task_id}",
            "branch_id": _read_string(state.get("branch_id")) or f"branch_{task_id}",
            "task_id": task_id,
            "segment_type": _segment_type_for_branch(branch_type),
            "final_response": self._safe_degraded_text(branch_type=branch_type),
            "final_response_ref": f"safe-template:{context.run_id}:{task_id}",
            "title": "安全降级提示",
            "guard_status": ComposerGuardStatus.TEMPLATE_SAFE.value,
            "fallback_triggered": True,
            "fallback_template_version": _SAFE_DEGRADED_TEMPLATE_VERSION,
            "audit_tier": _read_string(state.get("audit_tier")) or "L2",
            "publish_allowed": True,
            "safety_direction_present": True
            if _is_safety_branch(branch_type)
            else None,
            "source_stage": "safe_template",
            "metadata": self._segment_metadata(
                state=state,
                gate_result=gate_result,
                review_result=review_result,
                degraded=True,
            ),
        }

    def _safe_degraded_text(self, *, branch_type: str) -> str:
        """生成发布门未通过时的安全降级文本。

        :param branch_type: 当前分支类型。
        :return: 用户可见安全降级文本。
        """

        if _is_safety_branch(branch_type):
            return (
                "当前急症相关回复未完成安全发布门校验。若宠物出现持续呕吐、抽搐、"
                "呼吸困难、明显疼痛、疑似中毒或状态快速恶化，请立即联系线下兽医或急诊。"
            )
        return (
            "当前回复未完成安全发布门校验，暂时无法生成正式兽医建议。"
            "请补充症状、持续时间、宠物年龄体重和既往病史，或稍后重试。"
        )

    def _segment_title(self, *, branch_type: str) -> str:
        """解析用户可见分段标题。

        :param branch_type: 当前分支类型。
        :return: 分段标题。
        """

        if branch_type == ComposerBranchType.SAFETY_TRIGGER.value:
            return "急症安全提示"
        if branch_type == ComposerBranchType.EDUCATION.value:
            return "科普说明"
        if branch_type == ComposerBranchType.NONMEDICAL_PET_CARE.value:
            return "日常养宠建议"
        return "问诊建议"

    def _segment_metadata(
        self,
        *,
        state: GraphState,
        gate_result: Mapping[str, object],
        review_result: Mapping[str, object],
        degraded: bool = False,
    ) -> JsonMap:
        """构建候选段轻量元信息。

        :param state: 当前图运行状态。
        :param gate_result: 确定性发布门结果。
        :param review_result: 输出安全审查结果。
        :param degraded: 当前候选段是否来自安全降级模板。
        :return: 可写入 Composer 的轻量元信息。
        """

        return {
            "graph_safe_degraded": degraded,
            "branch_id": state.get("branch_id"),
            "executor_key": state.get("executor_key"),
            "post_generation_review_status": review_result.get("status"),
            "deterministic_gate_status": gate_result.get("status"),
            "deterministic_gate_publish_allowed": gate_result.get("publish_allowed"),
        }

    def _trace_patch_ref(self, *, state: GraphState) -> str | None:
        """读取上游分支 trace patch 引用。

        :param state: 当前图运行状态。
        :return: trace patch 引用；缺失时返回 None。
        """

        return _read_string(state.get("draft_response_ref"))


__all__: tuple[str, ...] = (
    "BranchStateBuilderGraphNode",
    "EDUCATION_EXECUTOR_NODE_ID",
    "ExecutorRouterGraphNode",
    "GuardrailRequestBuilderGraphNode",
    "NONMEDICAL_EXECUTOR_NODE_ID",
    "SAFETY_EXECUTOR_NODE_ID",
    "STANDARD_EXECUTOR_NODE_ID",
    "TaskLaneSelectorGraphNode",
)

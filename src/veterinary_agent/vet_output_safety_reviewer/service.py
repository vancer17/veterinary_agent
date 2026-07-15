##################################################################################################
# 文件: src/veterinary_agent/vet_output_safety_reviewer/service.py
# 作用: 实现 VetOutputSafetyReviewer 默认应用内服务，编排输出安全审查、改写、trace 与观测。
# 边界: 不实现 HTTP 接入、GraphRuntime 调度、最终发布门或真实用药规则真源；仅做受控语义审查。
##################################################################################################

from hashlib import sha256
from time import perf_counter
import re
from typing import Protocol

from pydantic import ValidationError

from veterinary_agent.agent_runner import AgentRunner
from veterinary_agent.config import (
    RuntimeConfigError,
    RuntimeConfigProvider,
    RuntimeConfigSnapshot,
)
from veterinary_agent.observability import (
    MetricType,
    ObservabilityProvider,
    StructuredLogLevel,
)
from veterinary_agent.vet_output_safety_reviewer.dto import (
    MedicationPolicyAnalysisRequestDto,
    MedicationPolicyDecisionDto,
    MedicationSpanCandidateDto,
    OutputGuardActionDto,
    OutputReviewTracePatchDto,
    OutputReviewTraceRecordDto,
    OutputReviewTraceWriteResultDto,
    OutputSafetyFindingDto,
    OutputSafetyReviewRequestDto,
    OutputSafetyReviewResultDto,
    ReviewDomainResultDto,
    ReviewInputContextDto,
    RewritePlanDto,
)
from veterinary_agent.vet_output_safety_reviewer.enums import (
    MedicationPolicyDecisionStatus,
    OutputFindingSeverity,
    OutputFindingType,
    OutputReviewTraceWriteStatus,
    ReviewActionType,
    ReviewDomain,
    ReviewStatus,
    VetOutputSafetyReviewerErrorCode,
    VetOutputSafetyReviewerOperation,
)
from veterinary_agent.vet_output_safety_reviewer.errors import (
    VetOutputSafetyReviewerError,
)
from veterinary_agent.vet_output_safety_reviewer.ports import (
    MedicationPolicyPort,
    TodoMedicationPolicyPort,
)
from veterinary_agent.vet_output_safety_reviewer.trace import (
    TodoVetOutputSafetyReviewerTraceSink,
    VetOutputSafetyReviewerTraceSink,
)

_COMPONENT_NAME = "vet_output_safety_reviewer"
_REVIEWER_VERSION = "vet-output-safety-reviewer.v1"
_WRITER_VERSION = "vet-output-safety-reviewer.writer.v1"
_DEFAULT_DISCLAIMER = "以上内容仅供辅助参考，请以线下兽医诊断为准。"
_DEFAULT_URGENT_CARE = (
    "如果出现呼吸困难、抽搐、持续呕吐、外伤大出血或误食毒物，请立即就医。"
)
_TOXIC_TERMS: frozenset[str] = frozenset(
    {
        "布洛芬",
        "对乙酰氨基酚",
        "葡萄",
        "木糖醇",
        "巧克力",
        "百合",
        "洋葱",
        "大蒜",
    }
)
_MEDICATION_TERMS: frozenset[str] = frozenset(
    {
        "药",
        "片",
        "胶囊",
        "口服",
        "滴",
        "注射",
        "剂量",
        "频次",
        "疗程",
        "mg",
        "ml",
        "毫克",
        "毫升",
    }
)
_URGENT_CARE_TERMS: frozenset[str] = frozenset(
    {
        "呼吸困难",
        "抽搐",
        "持续呕吐",
        "血便",
        "误食毒物",
        "外伤大出血",
        "无法站立",
        "腹胀",
        "急诊",
        "立即就医",
    }
)
_DISCLAIMER_TERMS: frozenset[str] = frozenset(
    {
        "辅助参考",
        "以线下兽医诊断为准",
        "请以兽医诊断为准",
        "遵医嘱",
    }
)
_T4_PATTERN = re.compile(
    r"(?:(?:\d+(?:\.\d+)?)\s*(?:mg|g|ml|毫克|毫升|片|粒|滴))|"
    r"(?:每(?:日|天|次)\s*\d+)|(?:\d+\s*次/\s*(?:日|天))|"
    r"(?:\d+\s*天(?:疗程|用药)?)|(?:按体重.*?\d+)",
    re.IGNORECASE,
)
_CLAIM_SOFTENING_RULES: tuple[tuple[str, str], ...] = (
    ("确诊", "提示可能存在"),
    ("一定是", "不能仅凭当前信息确定"),
    ("明显异常", "需要结合线下检查进一步确认"),
    ("已经", "可能已经"),
    ("肯定", "更像是"),
    ("直接给药", "需要先确认后再决定"),
)
_CLINICAL_DELAY_PATTERNS: tuple[str, ...] = (
    "先观察几天",
    "可以再等等",
    "不用去医院",
    "先别急",
    "明天再说",
)


def _now_ms(started_at: float) -> int:
    """计算从指定单调时钟起点到当前的毫秒数。

    :param started_at: ``perf_counter`` 记录的起点。
    :return: 四舍五入后的非负毫秒数。
    """

    return max(0, round((perf_counter() - started_at) * 1000))


def _read_string(value: object) -> str | None:
    """从未知值中读取非空字符串。

    :param value: 需要读取的未知值。
    :return: 去除首尾空白后的非空字符串；无法读取时返回 None。
    """

    if isinstance(value, str):
        stripped_value = value.strip()
        if stripped_value:
            return stripped_value
    return None


def _contains_any(text: str, terms: frozenset[str]) -> bool:
    """判断文本是否命中任一目标词。

    :param text: 待检查文本。
    :param terms: 需要匹配的词集合。
    :return: 若命中任一词则返回 True。
    """

    return any(term in text for term in terms)


def _apply_replacements(text: str, replacements: tuple[tuple[str, str], ...]) -> str:
    """按顺序应用一组字符串替换。

    :param text: 原始正文。
    :param replacements: 需要顺序执行的替换对。
    :return: 替换后的正文。
    """

    rewritten = text
    for source, target in replacements:
        rewritten = rewritten.replace(source, target)
    return rewritten


def _ref_from_text(
    *,
    prefix: str,
    trace_id: str,
    task_id: str,
    segment_id: str,
    text: str,
) -> str:
    """基于正文生成稳定引用。

    :param prefix: 稳定引用前缀。
    :param trace_id: 全链路 trace ID。
    :param task_id: 子任务 ID。
    :param segment_id: segment ID。
    :param text: 用于生成 hash 的正文。
    :return: 稳定且可回放的引用字符串。
    """

    digest = sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{trace_id}:{task_id}:{segment_id}:{digest}"


def _normalize_generation_profile(value: str) -> str:
    """规范化生成剖面字符串。

    :param value: 原始剖面值。
    :return: 规范化后的剖面字符串。
    """

    return value.strip().lower()


def _short_stable_id(*, prefix: str, values: tuple[str, ...], max_length: int) -> str:
    """生成不超过字段上限的稳定短 ID。

    :param prefix: 业务前缀。
    :param values: 参与 hash 的稳定字段值。
    :param max_length: 目标字段最大长度。
    :return: 带前缀和 hash 摘要的稳定短 ID。
    """

    digest = sha256("|".join(values).encode("utf-8")).hexdigest()[:16]
    candidate = f"{prefix}:{digest}"
    return candidate[:max_length]


class VetOutputSafetyReviewer(Protocol):
    """VetOutputSafetyReviewer 应用内服务接口。"""

    def is_ready(self) -> bool:
        """判断输出安全审查服务是否具备执行条件。

        :return: 若 RuntimeConfig 可读取且组件已启用则返回 True。
        """

        ...

    async def review_draft_response_safety(
        self,
        request: OutputSafetyReviewRequestDto,
    ) -> OutputSafetyReviewResultDto:
        """审查业务草稿并产出受控改写结果。

        :param request: 输出安全审查请求。
        :return: 审查结果、发现项、护栏动作和 trace patch。
        :raises VetOutputSafetyReviewerError: 当请求或运行时前置契约不满足时抛出。
        """

        ...


class DefaultVetOutputSafetyReviewer:
    """VetOutputSafetyReviewer 默认应用内实现。"""

    def __init__(
        self,
        *,
        runtime_config_provider: RuntimeConfigProvider,
        agent_runner: AgentRunner | None = None,
        medication_policy_port: MedicationPolicyPort | None = None,
        trace_sink: VetOutputSafetyReviewerTraceSink | None = None,
        observability_provider: ObservabilityProvider | None = None,
    ) -> None:
        """初始化 VetOutputSafetyReviewer 默认实现。

        :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
        :param agent_runner: 可选 AgentRunner 预留端口；当前实现以确定性审查为主。
        :param medication_policy_port: 可选用药策略端口；缺失时使用 TODO 空壳。
        :param trace_sink: 可选输出审查 trace 写入端口；缺失时使用 TODO 空壳。
        :param observability_provider: 可选 Observability provider。
        :return: None。
        """

        self._runtime_config_provider = runtime_config_provider
        self._agent_runner = agent_runner
        self._medication_policy_port = (
            medication_policy_port or TodoMedicationPolicyPort()
        )
        self._trace_sink = trace_sink or TodoVetOutputSafetyReviewerTraceSink()
        self._observability_provider = observability_provider

    def is_ready(self) -> bool:
        """判断输出安全审查服务是否具备执行条件。

        :return: 若 RuntimeConfig 可读取且 GuardrailFramework 已启用则返回 True。
        """

        if not self._runtime_config_provider.is_ready():
            return False
        try:
            snapshot = self._runtime_config_provider.current_snapshot()
        except RuntimeConfigError:
            return False
        return snapshot.guardrail_framework.enabled

    async def review_draft_response_safety(
        self,
        request: OutputSafetyReviewRequestDto,
    ) -> OutputSafetyReviewResultDto:
        """审查业务草稿并产出受控改写结果。

        :param request: 输出安全审查请求。
        :return: 审查结果、发现项、护栏动作和 trace patch。
        :raises VetOutputSafetyReviewerError: 当请求或运行时前置契约不满足时抛出。
        """

        started_monotonic = perf_counter()
        result: OutputSafetyReviewResultDto | None = None
        try:
            self._load_snapshot_or_raise(request=request)
            draft_text = self._resolve_draft_text_or_raise(request=request)
            normalized_context = self._normalize_context(request=request)
            medication_decision = await self._analyze_medication_safety(
                request=request,
                draft_text=draft_text,
                context=normalized_context,
            )
            domain_results = self._review_domains(
                request=request,
                draft_text=draft_text,
                context=normalized_context,
                medication_decision=medication_decision,
            )
            findings = self._merge_findings(domain_results=domain_results)
            rewrite_plan = self._build_rewrite_plan(
                request=request,
                findings=findings,
                medication_decision=medication_decision,
            )
            reviewed_text = self._rewrite_draft_text(
                request=request,
                draft_text=draft_text,
                findings=findings,
                rewrite_plan=rewrite_plan,
                context=normalized_context,
            )
            reviewed_ref = _ref_from_text(
                prefix="reviewed",
                trace_id=request.trace_id,
                task_id=request.task_id,
                segment_id=request.segment_id,
                text=reviewed_text,
            )
            review_status = self._resolve_status(
                findings=findings,
                rewrite_plan=rewrite_plan,
                medication_decision=medication_decision,
                reviewed_text=reviewed_text,
                draft_text=draft_text,
            )
            guard_actions = self._build_guard_actions(
                request=request,
                findings=findings,
                rewrite_plan=rewrite_plan,
                reviewed_ref=reviewed_ref,
            )
            trace_patch = self._build_trace_patch(
                request=request,
                findings=findings,
                guard_actions=guard_actions,
                medication_decision=medication_decision,
                rewrite_plan=rewrite_plan,
                review_status=review_status,
            )
            result = OutputSafetyReviewResultDto(
                task_id=request.task_id,
                segment_id=request.segment_id,
                reviewed_draft_ref=reviewed_ref,
                reviewed_draft_text=reviewed_text,
                status=review_status,
                findings=findings,
                guard_actions=guard_actions,
                medication_decision=medication_decision,
                rewrite_plan=rewrite_plan,
                fallback_recommended=rewrite_plan.fallback_recommended,
                review_confidence=self._compute_confidence(
                    findings=findings,
                    medication_decision=medication_decision,
                    review_status=review_status,
                ),
                degraded_flags=self._merge_degraded_flags(
                    request=request,
                    medication_decision=medication_decision,
                    findings=findings,
                ),
                trace_patch=trace_patch,
            )
            trace_result = await self._write_trace_safely(
                request=request,
                result=result,
                duration_ms=_now_ms(started_monotonic),
            )
            result = result.model_copy(
                update={
                    "trace_delivery_status": trace_result.status,
                    "degraded_flags": self._merge_trace_degraded_flags(
                        result=result,
                        trace_result=trace_result,
                    ),
                    "trace_patch": result.trace_patch.model_copy(
                        update={
                            "degraded_flags": self._merge_trace_degraded_flags(
                                result=result,
                                trace_result=trace_result,
                            ),
                        }
                    ),
                }
            )
            return result
        except VetOutputSafetyReviewerError:
            raise
        except RuntimeConfigError as exc:
            raise VetOutputSafetyReviewerError(
                code=VetOutputSafetyReviewerErrorCode.OUTPUT_REVIEW_RUNTIME_CONFIG_UNAVAILABLE,
                operation=VetOutputSafetyReviewerOperation.REVIEW_DRAFT_RESPONSE_SAFETY,
                message="VetOutputSafetyReviewer 无法读取有效 RuntimeConfig 快照",
                retryable=exc.retryable,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                segment_id=request.segment_id,
                conflict_with={"runtime_config_error_code": exc.code.value},
            ) from exc
        except ValidationError as exc:
            raise VetOutputSafetyReviewerError(
                code=VetOutputSafetyReviewerErrorCode.OUTPUT_REVIEW_SCHEMA_INVALID,
                operation=VetOutputSafetyReviewerOperation.REVIEW_DRAFT_RESPONSE_SAFETY,
                message="VetOutputSafetyReviewer 输出结构不符合契约",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                segment_id=request.segment_id,
                conflict_with={"validation_error_count": len(exc.errors())},
            ) from exc
        except Exception as exc:
            raise VetOutputSafetyReviewerError(
                code=VetOutputSafetyReviewerErrorCode.OUTPUT_REVIEW_INTERNAL_ERROR,
                operation=VetOutputSafetyReviewerOperation.REVIEW_DRAFT_RESPONSE_SAFETY,
                message="VetOutputSafetyReviewer 执行过程中发生未映射异常",
                retryable=True,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                segment_id=request.segment_id,
            ) from exc
        finally:
            self._record_observability(
                request=request,
                result=result,
                duration_ms=_now_ms(started_monotonic),
            )

    def _load_snapshot_or_raise(
        self,
        *,
        request: OutputSafetyReviewRequestDto,
    ) -> RuntimeConfigSnapshot:
        """读取并校验当前 RuntimeConfig 快照。

        :param request: 当前输出安全审查请求。
        :return: 当前 RuntimeConfig 快照的受控对象。
        :raises VetOutputSafetyReviewerError: 当组件未就绪或配置关闭时抛出。
        :raises RuntimeConfigError: 当 RuntimeConfig provider 不可用时抛出。
        """

        snapshot = self._runtime_config_provider.current_snapshot()
        if not snapshot.guardrail_framework.enabled:
            raise VetOutputSafetyReviewerError(
                code=VetOutputSafetyReviewerErrorCode.OUTPUT_REVIEW_NOT_READY,
                operation=VetOutputSafetyReviewerOperation.REVIEW_DRAFT_RESPONSE_SAFETY,
                message="VetOutputSafetyReviewer 已被 RuntimeConfig 关闭",
                retryable=True,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                segment_id=request.segment_id,
            )
        return snapshot

    def _resolve_draft_text_or_raise(
        self,
        *,
        request: OutputSafetyReviewRequestDto,
    ) -> str:
        """读取待审查草稿正文。

        :param request: 当前输出安全审查请求。
        :return: 待审查草稿正文。
        :raises VetOutputSafetyReviewerError: 当草稿正文缺失时抛出。
        """

        draft_text = request.draft_response_text
        if draft_text is None:
            raise VetOutputSafetyReviewerError(
                code=VetOutputSafetyReviewerErrorCode.OUTPUT_REVIEW_DRAFT_MISSING,
                operation=VetOutputSafetyReviewerOperation.REVIEW_DRAFT_RESPONSE_SAFETY,
                message="VetOutputSafetyReviewer 缺少草稿正文",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                segment_id=request.segment_id,
                conflict_with={"draft_response_ref": request.draft_response_ref},
            )
        return draft_text

    def _normalize_context(
        self,
        *,
        request: OutputSafetyReviewRequestDto,
    ) -> ReviewInputContextDto:
        """规范化审查上下文。

        :param request: 当前输出安全审查请求。
        :return: 已通过模型校验的审查上下文。
        """

        context = request.input_context
        if context.medical_content_expected is False:
            profile = _normalize_generation_profile(request.generation_profile)
            context = context.model_copy(
                update={
                    "medical_content_expected": profile
                    in {"standard", "safety_trigger", "education"}
                }
            )
        return context

    async def _analyze_medication_safety(
        self,
        *,
        request: OutputSafetyReviewRequestDto,
        draft_text: str,
        context: ReviewInputContextDto,
    ) -> MedicationPolicyDecisionDto | None:
        """执行用药安全分析。

        :param request: 当前输出安全审查请求。
        :param draft_text: 待审查草稿正文。
        :param context: 规范化审查上下文。
        :return: 标准化用药策略判定；若草稿不涉药则返回 None。
        """

        if not self._should_analyze_medication(draft_text=draft_text, context=context):
            return None
        span_candidates = [
            self._span_to_policy_candidate(span) for span in context.medication_spans
        ]
        policy_request = MedicationPolicyAnalysisRequestDto(
            request_id=request.request_id,
            trace_id=request.trace_id,
            candidate_text_ref=request.draft_response_ref,
            candidate_text=draft_text,
            generation_profile=request.generation_profile,
            executor_key=request.executor_key,
            pet_species=_read_string(context.assessment_summary.get("pet_species"))
            if isinstance(context.assessment_summary, dict)
            else None,
            span_candidates=span_candidates,
            text_source="draft_response",
            params_version=request.params_version,
        )
        decision = await self._medication_policy_port.analyze_medication_expression(
            policy_request
        )
        if decision.policy_version is None:
            decision = decision.model_copy(
                update={"policy_version": "todo-medication-policy.v1"}
            )
        return decision

    def _should_analyze_medication(
        self,
        *,
        draft_text: str,
        context: ReviewInputContextDto,
    ) -> bool:
        """判断当前草稿是否需要进入用药安全分析。

        :param draft_text: 待审查草稿正文。
        :param context: 规范化审查上下文。
        :return: 若命中用药相关信号则返回 True。
        """

        if context.medication_spans:
            return True
        if context.medical_content_expected:
            return _contains_any(draft_text, _MEDICATION_TERMS)
        return _contains_any(draft_text, _TOXIC_TERMS) or _contains_any(
            draft_text, _MEDICATION_TERMS
        )

    def _span_to_policy_candidate(
        self,
        span: MedicationSpanCandidateDto,
    ) -> MedicationSpanCandidateDto:
        """将输出审查上下文中的 span 转换为用药策略候选。

        :param span: 输出审查 span 候选。
        :return: 规范化后的 span 候选。
        """

        return span

    def _review_domains(
        self,
        *,
        request: OutputSafetyReviewRequestDto,
        draft_text: str,
        context: ReviewInputContextDto,
        medication_decision: MedicationPolicyDecisionDto | None,
    ) -> list[ReviewDomainResultDto]:
        """执行各风险域审查并返回结构化结果。

        :param request: 当前输出安全审查请求。
        :param draft_text: 待审查草稿正文。
        :param context: 规范化审查上下文。
        :param medication_decision: 用药策略判定结果。
        :return: 风险域结构化结果列表。
        """

        profile = _normalize_generation_profile(request.generation_profile)
        domains: list[ReviewDomainResultDto] = [
            self._medication_domain_review(
                draft_text=draft_text,
                context=context,
                medication_decision=medication_decision,
            ),
            self._clinical_domain_review(
                request=request,
                draft_text=draft_text,
                context=context,
            ),
            self._evidence_domain_review(
                draft_text=draft_text,
                context=context,
            ),
            self._profile_domain_review(
                request=request,
                draft_text=draft_text,
                context=context,
            ),
            self._disclaimer_domain_review(
                draft_text=draft_text,
                context=context,
            ),
        ]
        if profile == "nonmedical":
            domains.append(
                ReviewDomainResultDto(
                    domain=ReviewDomain.PROFILE_BOUNDARY,
                    status="nonmedical_boundary_check",
                    findings=self._nonmedical_cross_domain_findings(
                        request=request,
                        draft_text=draft_text,
                    ),
                    rewrite_hints=[
                        "非医疗草稿出现医学表达时应明确降级或移交兽医链路。"
                    ],
                    degraded=False,
                )
            )
        return domains

    def _medication_domain_review(
        self,
        *,
        draft_text: str,
        context: ReviewInputContextDto,
        medication_decision: MedicationPolicyDecisionDto | None,
    ) -> ReviewDomainResultDto:
        """执行用药安全风险域审查。

        :param draft_text: 待审查草稿正文。
        :param context: 规范化审查上下文。
        :param medication_decision: 用药策略判定结果。
        :return: 用药安全风险域结果。
        """

        findings: list[OutputSafetyFindingDto] = []
        rewrite_hints: list[str] = []
        degraded = False
        if medication_decision is not None:
            rewrite_hints.extend(medication_decision.rewrite_hints)
            degraded = (
                medication_decision.status is MedicationPolicyDecisionStatus.UNAVAILABLE
                or medication_decision.status is MedicationPolicyDecisionStatus.DEGRADED
            )
            if medication_decision.degraded_flags:
                rewrite_hints.extend(medication_decision.degraded_flags)
        if _contains_any(draft_text, _TOXIC_TERMS):
            findings.append(
                OutputSafetyFindingDto(
                    finding_id="medication:toxic",
                    finding_type=OutputFindingType.TOXIC_SUBSTANCE_RECOMMENDED,
                    severity=OutputFindingSeverity.CRITICAL,
                    reason_code="OUTPUT_REVIEW_TOXIC_SUBSTANCE",
                    evidence_ref=self._matched_term(draft_text, _TOXIC_TERMS),
                    source_review_domain=ReviewDomain.MEDICATION_SAFETY,
                    p0_candidate=True,
                    metadata={"domain": "medication"},
                )
            )
            rewrite_hints.append("毒物相关表达必须删除或改写为紧急就医警示。")
        if self._contains_t4_regimen(draft_text):
            findings.append(
                OutputSafetyFindingDto(
                    finding_id="medication:t4",
                    finding_type=OutputFindingType.T4_DETECTED,
                    severity=OutputFindingSeverity.HIGH,
                    reason_code="OUTPUT_REVIEW_T4_DETECTED",
                    evidence_ref=self._matched_t4(draft_text),
                    source_review_domain=ReviewDomain.MEDICATION_SAFETY,
                    p0_candidate=True,
                    metadata={"domain": "medication"},
                )
            )
            rewrite_hints.append("精确剂量、频次、疗程与按体重换算应删除。")
        if (
            medication_decision is not None
            and medication_decision.status is MedicationPolicyDecisionStatus.UNAVAILABLE
        ):
            rewrite_hints.append("用药策略不可用时不得直接安全放行涉药草稿。")
        return ReviewDomainResultDto(
            domain=ReviewDomain.MEDICATION_SAFETY,
            status="reviewed" if findings else "passed",
            findings=findings,
            rewrite_hints=rewrite_hints,
            degraded=degraded or bool(findings and medication_decision is not None),
        )

    def _clinical_domain_review(
        self,
        *,
        request: OutputSafetyReviewRequestDto,
        draft_text: str,
        context: ReviewInputContextDto,
    ) -> ReviewDomainResultDto:
        """执行临床安全风险域审查。

        :param request: 当前输出安全审查请求。
        :param draft_text: 待审查草稿正文。
        :param context: 规范化审查上下文。
        :return: 临床安全风险域结果。
        """

        findings: list[OutputSafetyFindingDto] = []
        rewrite_hints: list[str] = []
        acute_signal = _contains_any(draft_text, _URGENT_CARE_TERMS) or any(
            signal_code in {"SAF-03", "ACUTE_EVENT"}
            for signal_code in context.signal_codes
        )
        if acute_signal and not _contains_any(
            draft_text, frozenset({"立即就医", "尽快就医", "急诊", "线下兽医"})
        ):
            findings.append(
                OutputSafetyFindingDto(
                    finding_id=_short_stable_id(
                        prefix="acute",
                        values=(request.trace_id, request.task_id, request.segment_id),
                        max_length=128,
                    ),
                    finding_type=OutputFindingType.ACUTE_WITHOUT_URGENT_CARE,
                    severity=OutputFindingSeverity.CRITICAL,
                    reason_code="OUTPUT_REVIEW_ACUTE_WITHOUT_URGENT_CARE",
                    evidence_ref=request.draft_response_ref,
                    source_review_domain=ReviewDomain.CLINICAL_SAFETY,
                    p0_candidate=True,
                    metadata={"profile": request.generation_profile},
                )
            )
            rewrite_hints.append("急症路径必须前置就医导向，且不得被无关内容稀释。")
        for delayed_term in _CLINICAL_DELAY_PATTERNS:
            if delayed_term in draft_text and acute_signal:
                findings.append(
                    OutputSafetyFindingDto(
                        finding_id=_short_stable_id(
                            prefix="delay",
                            values=(
                                request.trace_id,
                                request.task_id,
                                request.segment_id,
                                delayed_term,
                                str(len(findings)),
                            ),
                            max_length=128,
                        ),
                        finding_type=OutputFindingType.DELAYED_CARE_RISK,
                        severity=OutputFindingSeverity.HIGH,
                        reason_code="OUTPUT_REVIEW_DELAYED_CARE_RISK",
                        evidence_ref=request.draft_response_ref,
                        source_review_domain=ReviewDomain.CLINICAL_SAFETY,
                        p0_candidate=True,
                        metadata={
                            "delayed_term": delayed_term,
                            "profile": request.generation_profile,
                        },
                    )
                )
                rewrite_hints.append("鼓励等待或拖延就医的表达必须替换为线下就医建议。")
        return ReviewDomainResultDto(
            domain=ReviewDomain.CLINICAL_SAFETY,
            status="reviewed" if findings else "passed",
            findings=findings,
            rewrite_hints=rewrite_hints,
            degraded=False,
        )

    def _evidence_domain_review(
        self,
        *,
        draft_text: str,
        context: ReviewInputContextDto,
    ) -> ReviewDomainResultDto:
        """执行证据接地风险域审查。

        :param draft_text: 待审查草稿正文。
        :param context: 规范化审查上下文。
        :return: 证据接地风险域结果。
        """

        findings: list[OutputSafetyFindingDto] = []
        rewrite_hints: list[str] = []
        has_evidence = bool(context.evidence_bindings or context.rag_summary)
        if not has_evidence and _contains_any(
            draft_text,
            frozenset({"确诊", "明显异常", "参考范围", "正常范围", "检验值", "化验"}),
        ):
            findings.append(
                OutputSafetyFindingDto(
                    finding_id="evidence:unsupported-claim",
                    finding_type=OutputFindingType.UNSUPPORTED_MEDICAL_CLAIM,
                    severity=OutputFindingSeverity.HIGH,
                    reason_code="OUTPUT_REVIEW_UNSUPPORTED_MEDICAL_CLAIM",
                    evidence_ref=request_ref_from_context(context=context),
                    source_review_domain=ReviewDomain.EVIDENCE_GROUNDING,
                    p0_candidate=False,
                    metadata={"has_evidence": has_evidence},
                )
            )
            rewrite_hints.append("无依据医学结论应改写为不确定性表达并提示线下确认。")
        if context.ocr_confirmed is False and _contains_any(
            draft_text,
            frozenset({"异常", "确诊", "参考范围", "数值", "结果"}),
        ):
            findings.append(
                OutputSafetyFindingDto(
                    finding_id="evidence:unconfirmed-ocr",
                    finding_type=OutputFindingType.UNCONFIRMED_OCR_USED,
                    severity=OutputFindingSeverity.HIGH,
                    reason_code="OUTPUT_REVIEW_UNCONFIRMED_OCR_USED",
                    evidence_ref=context.context_summary_ref,
                    source_review_domain=ReviewDomain.EVIDENCE_GROUNDING,
                    p0_candidate=False,
                    metadata={"ocr_confirmed": context.ocr_confirmed},
                )
            )
            rewrite_hints.append("未确认 OCR 内容只能回显，不能转写为当前建议。")
        if (
            _contains_any(draft_text, frozenset({"参考范围", "正常范围"}))
            and not context.lab_analytes
        ):
            findings.append(
                OutputSafetyFindingDto(
                    finding_id="evidence:ref-range",
                    finding_type=OutputFindingType.REF_RANGE_HALLUCINATION,
                    severity=OutputFindingSeverity.MEDIUM,
                    reason_code="OUTPUT_REVIEW_REF_RANGE_HALLUCINATION",
                    evidence_ref=context.context_summary_ref,
                    source_review_domain=ReviewDomain.EVIDENCE_GROUNDING,
                    p0_candidate=False,
                    metadata={"lab_analytes_count": len(context.lab_analytes)},
                )
            )
            rewrite_hints.append("参考区间必须与已确认来源绑定。")
        return ReviewDomainResultDto(
            domain=ReviewDomain.EVIDENCE_GROUNDING,
            status="reviewed" if findings else "passed",
            findings=findings,
            rewrite_hints=rewrite_hints,
            degraded=not has_evidence and bool(findings),
        )

    def _profile_domain_review(
        self,
        *,
        request: OutputSafetyReviewRequestDto,
        draft_text: str,
        context: ReviewInputContextDto,
    ) -> ReviewDomainResultDto:
        """执行剖面边界风险域审查。

        :param request: 当前输出安全审查请求。
        :param draft_text: 待审查草稿正文。
        :param context: 规范化审查上下文。
        :return: 剖面边界风险域结果。
        """

        findings: list[OutputSafetyFindingDto] = []
        rewrite_hints: list[str] = []
        profile = _normalize_generation_profile(request.generation_profile)
        if profile == "education" and _contains_any(
            draft_text,
            frozenset({"诊断", "鉴别诊断", "四层", "治疗方案", "处方"}),
        ):
            findings.append(
                OutputSafetyFindingDto(
                    finding_id=_short_stable_id(
                        prefix="education-boundary",
                        values=(request.trace_id, request.task_id, request.segment_id),
                        max_length=128,
                    ),
                    finding_type=OutputFindingType.PROFILE_BOUNDARY_VIOLATION,
                    severity=OutputFindingSeverity.HIGH,
                    reason_code="OUTPUT_REVIEW_PROFILE_BOUNDARY_VIOLATION",
                    evidence_ref=request.draft_response_ref,
                    source_review_domain=ReviewDomain.PROFILE_BOUNDARY,
                    p0_candidate=False,
                    metadata={"profile": profile},
                )
            )
            rewrite_hints.append("科普剖面不得写成诊断或处置结论。")
        if profile == "safety_trigger" and _contains_any(
            draft_text,
            frozenset({"鉴别诊断", "四层", "长期管理", "复杂病因"}),
        ):
            findings.append(
                OutputSafetyFindingDto(
                    finding_id=_short_stable_id(
                        prefix="safety-trigger-boundary",
                        values=(request.trace_id, request.task_id, request.segment_id),
                        max_length=128,
                    ),
                    finding_type=OutputFindingType.PROFILE_BOUNDARY_VIOLATION,
                    severity=OutputFindingSeverity.HIGH,
                    reason_code="OUTPUT_REVIEW_PROFILE_BOUNDARY_VIOLATION",
                    evidence_ref=request.draft_response_ref,
                    source_review_domain=ReviewDomain.PROFILE_BOUNDARY,
                    p0_candidate=False,
                    metadata={"profile": profile},
                )
            )
            rewrite_hints.append("急症剖面不得展开长篇鉴别或延迟急救。")
        if profile == "nonmedical" and _contains_any(
            draft_text,
            frozenset({"药", "剂量", "治疗", "炎症", "确诊"}),
        ):
            findings.append(
                OutputSafetyFindingDto(
                    finding_id=_short_stable_id(
                        prefix="nonmedical-boundary",
                        values=(request.trace_id, request.task_id, request.segment_id),
                        max_length=128,
                    ),
                    finding_type=OutputFindingType.NONMED_CROSS_DOMAIN_SIGNAL_IGNORED,
                    severity=OutputFindingSeverity.HIGH,
                    reason_code="OUTPUT_REVIEW_NONMED_CROSS_DOMAIN_SIGNAL_IGNORED",
                    evidence_ref=request.draft_response_ref,
                    source_review_domain=ReviewDomain.PROFILE_BOUNDARY,
                    p0_candidate=False,
                    metadata={"profile": profile},
                )
            )
            rewrite_hints.append("非医疗草稿遇到医学表达时应及时降级或转入兽医链路。")
        return ReviewDomainResultDto(
            domain=ReviewDomain.PROFILE_BOUNDARY,
            status="reviewed" if findings else "passed",
            findings=findings,
            rewrite_hints=rewrite_hints,
            degraded=False,
        )

    def _disclaimer_domain_review(
        self,
        *,
        draft_text: str,
        context: ReviewInputContextDto,
    ) -> ReviewDomainResultDto:
        """执行免责声明与语气风险域审查。

        :param draft_text: 待审查草稿正文。
        :param context: 规范化审查上下文。
        :return: 免责声明与语气风险域结果。
        """

        findings: list[OutputSafetyFindingDto] = []
        rewrite_hints: list[str] = []
        medical_content = context.medical_content_expected or _contains_any(
            draft_text,
            frozenset(
                {
                    *_MEDICATION_TERMS,
                    *_TOXIC_TERMS,
                    "症状",
                    "检查",
                    "诊断",
                    "化验",
                }
            ),
        )
        if medical_content:
            if not _contains_any(draft_text, _DISCLAIMER_TERMS):
                findings.append(
                    OutputSafetyFindingDto(
                        finding_id="disclaimer:missing",
                        finding_type=OutputFindingType.MISSING_MEDICAL_DISCLAIMER,
                        severity=OutputFindingSeverity.MEDIUM,
                        reason_code="OUTPUT_REVIEW_MISSING_MEDICAL_DISCLAIMER",
                        evidence_ref=None,
                        source_review_domain=ReviewDomain.DISCLAIMER_AND_TONE,
                        p0_candidate=False,
                        metadata={},
                    )
                )
                rewrite_hints.append("涉诊涉药输出应自然补入辅助参考免责声明。")
        return ReviewDomainResultDto(
            domain=ReviewDomain.DISCLAIMER_AND_TONE,
            status="reviewed" if findings else "passed",
            findings=findings,
            rewrite_hints=rewrite_hints,
            degraded=False,
        )

    def _nonmedical_cross_domain_findings(
        self,
        *,
        request: OutputSafetyReviewRequestDto,
        draft_text: str,
    ) -> list[OutputSafetyFindingDto]:
        """构建非医疗路径的跨域医学信号发现项。

        :param request: 当前输出安全审查请求。
        :param draft_text: 待审查草稿正文。
        :return: 非医疗跨域发现项列表。
        """

        if not _contains_any(draft_text, _MEDICATION_TERMS):
            return []
        return [
            OutputSafetyFindingDto(
                finding_id=_short_stable_id(
                    prefix="nonmed-cross-domain",
                    values=(request.trace_id, request.task_id, request.segment_id),
                    max_length=128,
                ),
                finding_type=OutputFindingType.NONMED_CROSS_DOMAIN_SIGNAL_IGNORED,
                severity=OutputFindingSeverity.HIGH,
                reason_code="OUTPUT_REVIEW_NONMED_CROSS_DOMAIN_SIGNAL_IGNORED",
                evidence_ref=request.draft_response_ref,
                source_review_domain=ReviewDomain.PROFILE_BOUNDARY,
                p0_candidate=False,
                metadata={"profile": "nonmedical"},
            )
        ]

    def _merge_findings(
        self,
        *,
        domain_results: list[ReviewDomainResultDto],
    ) -> list[OutputSafetyFindingDto]:
        """合并并去重各风险域发现项。

        :param domain_results: 各风险域的结构化结果。
        :return: 去重后的发现项列表。
        """

        merged: list[OutputSafetyFindingDto] = []
        seen_ids: set[str] = set()
        for domain_result in domain_results:
            for finding in domain_result.findings:
                if finding.finding_id in seen_ids:
                    continue
                merged.append(finding)
                seen_ids.add(finding.finding_id)
        return merged

    def _build_rewrite_plan(
        self,
        *,
        request: OutputSafetyReviewRequestDto,
        findings: list[OutputSafetyFindingDto],
        medication_decision: MedicationPolicyDecisionDto | None,
    ) -> RewritePlanDto:
        """根据发现项构建改写计划。

        :param request: 当前输出安全审查请求。
        :param findings: 审查发现项。
        :param medication_decision: 用药策略判定结果。
        :return: 改写计划。
        """

        action_types: list[ReviewActionType] = []
        target_finding_ids: list[str] = []
        required_constraints: list[str] = []
        fallback_recommended = False
        for finding in findings:
            target_finding_ids.append(finding.finding_id)
            action_type = self._action_type_for_finding(finding=finding)
            if action_type not in action_types:
                action_types.append(action_type)
            if (
                finding.p0_candidate
                and action_type is ReviewActionType.BLOCK_RECOMMENDED
            ):
                fallback_recommended = True
            required_constraints.append(finding.reason_code)
        if medication_decision is not None and medication_decision.fallback_required:
            fallback_recommended = True
        if (
            fallback_recommended
            and ReviewActionType.FALLBACK_RECOMMENDED not in action_types
        ):
            action_types.append(ReviewActionType.FALLBACK_RECOMMENDED)
        if (
            not findings
            and medication_decision is not None
            and medication_decision.status is MedicationPolicyDecisionStatus.UNAVAILABLE
        ):
            fallback_recommended = True
            action_types.append(ReviewActionType.FALLBACK_RECOMMENDED)
            required_constraints.append("medication_policy_unavailable")
        return RewritePlanDto(
            plan_id=_short_stable_id(
                prefix="rewrite",
                values=(request.trace_id, request.task_id, request.segment_id),
                max_length=128,
            ),
            action_types=action_types,
            target_finding_ids=target_finding_ids,
            fallback_recommended=fallback_recommended,
            required_constraints=required_constraints,
        )

    def _action_type_for_finding(
        self,
        *,
        finding: OutputSafetyFindingDto,
    ) -> ReviewActionType:
        """根据发现项选择改写动作类型。

        :param finding: 当前发现项。
        :return: 适配的改写动作类型。
        """

        if finding.finding_type in {
            OutputFindingType.T4_DETECTED,
            OutputFindingType.TOXIC_SUBSTANCE_RECOMMENDED,
            OutputFindingType.ACUTE_WITHOUT_URGENT_CARE,
            OutputFindingType.DELAYED_CARE_RISK,
        }:
            return ReviewActionType.REMOVE_SPAN
        if finding.finding_type in {
            OutputFindingType.UNSUPPORTED_MEDICAL_CLAIM,
            OutputFindingType.FABRICATED_LAB_VALUE,
            OutputFindingType.UNCONFIRMED_OCR_USED,
            OutputFindingType.REF_RANGE_HALLUCINATION,
            OutputFindingType.NONMED_CROSS_DOMAIN_SIGNAL_IGNORED,
        }:
            return ReviewActionType.REMOVE_UNSUPPORTED_CLAIM
        return ReviewActionType.SOFTEN_CLAIM

    def _rewrite_draft_text(
        self,
        *,
        request: OutputSafetyReviewRequestDto,
        draft_text: str,
        findings: list[OutputSafetyFindingDto],
        rewrite_plan: RewritePlanDto,
        context: ReviewInputContextDto,
    ) -> str:
        """根据发现项与改写计划生成审查后正文。

        :param request: 当前输出安全审查请求。
        :param draft_text: 原始草稿正文。
        :param findings: 审查发现项。
        :param rewrite_plan: 改写计划。
        :param context: 规范化审查上下文。
        :return: 审查后正文。
        """

        del request, rewrite_plan, context
        reviewed_text = draft_text
        if any(
            finding.finding_type is OutputFindingType.ACUTE_WITHOUT_URGENT_CARE
            for finding in findings
        ):
            reviewed_text = f"{_DEFAULT_URGENT_CARE}\n{reviewed_text}"
        if any(
            finding.finding_type is OutputFindingType.TOXIC_SUBSTANCE_RECOMMENDED
            for finding in findings
        ):
            reviewed_text = f"请不要给宠物使用或喂食有毒物质。\n{reviewed_text}"
        reviewed_text = self._soften_claims(reviewed_text, findings=findings)
        reviewed_text = self._remove_t4_patterns(reviewed_text, findings=findings)
        reviewed_text = self._append_disclaimer_if_needed(
            reviewed_text,
            findings=findings,
        )
        reviewed_text = reviewed_text[:32768]
        return reviewed_text

    def _soften_claims(
        self,
        text: str,
        *,
        findings: list[OutputSafetyFindingDto],
    ) -> str:
        """软化无依据或越界表述。

        :param text: 原始正文。
        :param findings: 审查发现项。
        :return: 已软化后的正文。
        """

        del findings
        return _apply_replacements(text, _CLAIM_SOFTENING_RULES)

    def _remove_t4_patterns(
        self,
        text: str,
        *,
        findings: list[OutputSafetyFindingDto],
    ) -> str:
        """删除或替换精确剂量相关表达。

        :param text: 原始正文。
        :param findings: 审查发现项。
        :return: 已删除精确剂量的正文。
        """

        if not any(
            finding.finding_type is OutputFindingType.T4_DETECTED
            for finding in findings
        ):
            return text
        return _T4_PATTERN.sub("按药品说明书或遵兽医指导", text)

    def _append_disclaimer_if_needed(
        self,
        text: str,
        *,
        findings: list[OutputSafetyFindingDto],
    ) -> str:
        """在必要时补充免责声明。

        :param text: 原始正文。
        :param findings: 审查发现项。
        :return: 已补充免责声明的正文。
        """

        if not any(
            finding.finding_type is OutputFindingType.MISSING_MEDICAL_DISCLAIMER
            for finding in findings
        ):
            return text
        if _contains_any(text, _DISCLAIMER_TERMS):
            return text
        if text.endswith("\n"):
            return f"{text}{_DEFAULT_DISCLAIMER}"
        return f"{text}\n\n{_DEFAULT_DISCLAIMER}"

    def _build_guard_actions(
        self,
        *,
        request: OutputSafetyReviewRequestDto,
        findings: list[OutputSafetyFindingDto],
        rewrite_plan: RewritePlanDto,
        reviewed_ref: str,
    ) -> list[OutputGuardActionDto]:
        """归一化输出安全审查动作。

        :param request: 当前输出安全审查请求。
        :param findings: 审查发现项。
        :param rewrite_plan: 改写计划。
        :param reviewed_ref: 审查后正文引用。
        :return: 标准化护栏动作列表。
        """

        actions: list[OutputGuardActionDto] = []
        for index, finding in enumerate(findings):
            action_type = self._action_type_for_finding(finding=finding)
            actions.append(
                OutputGuardActionDto(
                    action_id=_short_stable_id(
                        prefix="output-action",
                        values=(
                            request.trace_id,
                            request.task_id,
                            request.segment_id,
                            str(index),
                            finding.finding_id,
                        ),
                        max_length=160,
                    ),
                    action_type=action_type,
                    reason_code=finding.reason_code,
                    before_ref=request.draft_response_ref,
                    after_ref=reviewed_ref,
                    source_finding_id=finding.finding_id,
                    metadata={"domain": finding.source_review_domain.value},
                )
            )
        if rewrite_plan.fallback_recommended:
            actions.append(
                OutputGuardActionDto(
                    action_id=_short_stable_id(
                        prefix="output-fallback",
                        values=(
                            request.trace_id,
                            request.task_id,
                            request.segment_id,
                            "fallback",
                        ),
                        max_length=160,
                    ),
                    action_type=ReviewActionType.FALLBACK_RECOMMENDED,
                    reason_code="OUTPUT_REVIEW_FALLBACK_RECOMMENDED",
                    before_ref=request.draft_response_ref,
                    after_ref=reviewed_ref,
                    source_finding_id=None,
                    metadata={"fallback_recommended": True},
                )
            )
        if not actions:
            actions.append(
                OutputGuardActionDto(
                    action_id=_short_stable_id(
                        prefix="output-allow",
                        values=(
                            request.trace_id,
                            request.task_id,
                            request.segment_id,
                            "allow",
                        ),
                        max_length=160,
                    ),
                    action_type=ReviewActionType.ALLOW,
                    reason_code="OUTPUT_REVIEW_ALLOW",
                    before_ref=request.draft_response_ref,
                    after_ref=reviewed_ref,
                    source_finding_id=None,
                    metadata={},
                )
            )
        return actions

    def _build_trace_patch(
        self,
        *,
        request: OutputSafetyReviewRequestDto,
        findings: list[OutputSafetyFindingDto],
        guard_actions: list[OutputGuardActionDto],
        medication_decision: MedicationPolicyDecisionDto | None,
        rewrite_plan: RewritePlanDto,
        review_status: ReviewStatus,
    ) -> OutputReviewTracePatchDto:
        """构建输出安全审查 trace patch。

        :param request: 当前输出安全审查请求。
        :param findings: 审查发现项。
        :param guard_actions: 护栏动作列表。
        :param medication_decision: 用药策略判定结果。
        :param rewrite_plan: 改写计划。
        :param review_status: 审查状态。
        :return: 输出审查 trace patch。
        """

        medication_policy_version = (
            medication_decision.policy_version
            if medication_decision is not None
            else None
        )
        if medication_policy_version is None and medication_decision is not None:
            medication_policy_version = "todo-medication-policy.v1"
        return OutputReviewTracePatchDto(
            reviewer_version=_REVIEWER_VERSION,
            writer_version=_WRITER_VERSION,
            medication_policy_version=medication_policy_version,
            finding_types=[finding.finding_type for finding in findings],
            action_types=[action.action_type for action in guard_actions],
            degraded_flags=self._merge_degraded_flags(
                request=request,
                medication_decision=medication_decision,
                findings=findings,
            ),
            review_domains=self._review_domains_from_findings(findings=findings),
        )

    def _review_domains_from_findings(
        self,
        *,
        findings: list[OutputSafetyFindingDto],
    ) -> list[ReviewDomain]:
        """从发现项回推本次触达的风险域。

        :param findings: 审查发现项。
        :return: 风险域列表。
        """

        domains: list[ReviewDomain] = []
        for finding in findings:
            if finding.source_review_domain not in domains:
                domains.append(finding.source_review_domain)
        return domains

    def _resolve_status(
        self,
        *,
        findings: list[OutputSafetyFindingDto],
        rewrite_plan: RewritePlanDto,
        medication_decision: MedicationPolicyDecisionDto | None,
        reviewed_text: str,
        draft_text: str,
    ) -> ReviewStatus:
        """根据发现项、改写计划和结果文本确定最终审查状态。

        :param findings: 审查发现项。
        :param rewrite_plan: 改写计划。
        :param medication_decision: 用药策略判定结果。
        :param reviewed_text: 审查后正文。
        :param draft_text: 原始草稿正文。
        :return: 最终审查状态。
        """

        del reviewed_text, draft_text
        if (
            medication_decision is not None
            and medication_decision.status is MedicationPolicyDecisionStatus.UNAVAILABLE
        ):
            return ReviewStatus.DEGRADED_REVIEW
        if rewrite_plan.fallback_recommended:
            return ReviewStatus.FALLBACK_RECOMMENDED
        if any(finding.p0_candidate for finding in findings):
            return ReviewStatus.REVIEWED_WITH_REWRITE
        if findings:
            return ReviewStatus.REVIEWED_WITH_REWRITE
        return ReviewStatus.REVIEWED_READY

    def _compute_confidence(
        self,
        *,
        findings: list[OutputSafetyFindingDto],
        medication_decision: MedicationPolicyDecisionDto | None,
        review_status: ReviewStatus,
    ) -> float:
        """计算输出安全审查置信度。

        :param findings: 审查发现项。
        :param medication_decision: 用药策略判定结果。
        :param review_status: 审查状态。
        :return: 0 到 1 之间的置信度。
        """

        confidence = 0.95
        confidence -= min(0.45, 0.08 * len(findings))
        if (
            medication_decision is not None
            and medication_decision.status is MedicationPolicyDecisionStatus.UNAVAILABLE
        ):
            confidence -= 0.25
        if review_status is ReviewStatus.DEGRADED_REVIEW:
            confidence -= 0.2
        return max(0.0, min(1.0, confidence))

    def _merge_degraded_flags(
        self,
        *,
        request: OutputSafetyReviewRequestDto,
        medication_decision: MedicationPolicyDecisionDto | None,
        findings: list[OutputSafetyFindingDto],
    ) -> list[str]:
        """合并本次审查的降级标记。

        :param request: 当前输出安全审查请求。
        :param medication_decision: 用药策略判定结果。
        :param findings: 审查发现项。
        :return: 降级标记列表。
        """

        flags: list[str] = []
        if medication_decision is not None and medication_decision.degraded_flags:
            flags.extend(medication_decision.degraded_flags)
        if (
            request.input_context.medical_content_expected
            and not request.input_context.evidence_bindings
            and not request.input_context.rag_summary
        ):
            flags.append("evidence_summary_missing")
        if findings and any(finding.p0_candidate for finding in findings):
            flags.append("p0_candidates_present")
        return list(dict.fromkeys(flags))

    def _merge_trace_degraded_flags(
        self,
        *,
        result: OutputSafetyReviewResultDto,
        trace_result: OutputReviewTraceWriteResultDto,
    ) -> list[str]:
        """将 trace 写入结果并入审查降级标记。

        :param result: 输出安全审查结果。
        :param trace_result: trace 写入结果。
        :return: 合并后的降级标记。
        """

        flags = list(result.degraded_flags)
        if trace_result.status is OutputReviewTraceWriteStatus.DEGRADED:
            flags.append("trace_write_degraded")
        if trace_result.status is OutputReviewTraceWriteStatus.SKIPPED:
            flags.append("trace_write_skipped")
        return list(dict.fromkeys(flags))

    async def _write_trace_safely(
        self,
        *,
        request: OutputSafetyReviewRequestDto,
        result: OutputSafetyReviewResultDto,
        duration_ms: int,
    ) -> OutputReviewTraceWriteResultDto:
        """安全写入输出审查脱敏 trace。

        :param request: 当前输出安全审查请求。
        :param result: 当前输出安全审查结果。
        :param duration_ms: 本次审查耗时，单位为毫秒。
        :return: trace 写入结果。
        """

        try:
            return await self._trace_sink.write_output_review_trace(
                OutputReviewTraceRecordDto(
                    request=request,
                    result=result,
                    duration_ms=duration_ms,
                )
            )
        except Exception:
            return OutputReviewTraceWriteResultDto(
                status=OutputReviewTraceWriteStatus.DEGRADED,
                error_code="OUTPUT_REVIEW_TRACE_WRITE_FAILED",
                retryable=True,
                detail="VetOutputSafetyReviewer trace 写入发生未映射异常",
            )

    def _record_observability(
        self,
        *,
        request: OutputSafetyReviewRequestDto,
        result: OutputSafetyReviewResultDto | None,
        duration_ms: int,
    ) -> None:
        """记录输出安全审查指标与结构化事件。

        :param request: 当前输出安全审查请求。
        :param result: 当前输出安全审查结果；失败时为空。
        :param duration_ms: 本次审查耗时，单位为毫秒。
        :return: None。
        """

        provider = self._observability_provider
        if provider is None:
            return
        status = result.status.value if result is not None else "failed"
        try:
            provider.record_metric(
                metric_name="output_review_total",
                value=1.0,
                metric_type=MetricType.COUNTER,
                labels={
                    "status": status,
                    "generation_profile": request.generation_profile,
                },
                description="输出安全审查总数。",
            )
            provider.record_metric(
                metric_name="output_review_duration_ms",
                value=float(duration_ms),
                metric_type=MetricType.HISTOGRAM,
                labels={
                    "status": status,
                    "generation_profile": request.generation_profile,
                },
                description="输出安全审查耗时，单位为毫秒。",
            )
            if result is not None:
                provider.record_metric(
                    metric_name="output_review_finding_total",
                    value=float(len(result.findings)),
                    metric_type=MetricType.COUNTER,
                    labels={
                        "status": result.status.value,
                        "generation_profile": request.generation_profile,
                    },
                    description="输出安全审查发现项数量。",
                )
            provider.record_event(
                event_name=(
                    "output_review.completed"
                    if result is not None
                    else "output_review.failed"
                ),
                component=_COMPONENT_NAME,
                level=(
                    StructuredLogLevel.INFO
                    if result is not None
                    else StructuredLogLevel.ERROR
                ),
                safe_fields={
                    "request_id": request.request_id,
                    "trace_id": request.trace_id,
                    "run_id": request.run_id,
                    "task_id": request.task_id,
                    "segment_id": request.segment_id,
                    "generation_profile": request.generation_profile,
                    "executor_key": request.executor_key,
                    "status": status,
                    "duration_ms": duration_ms,
                    "finding_count": (
                        len(result.findings) if result is not None else 0
                    ),
                    "fallback_recommended": (
                        result.fallback_recommended if result is not None else False
                    ),
                },
            )
        except Exception:
            return

    def _matched_term(self, text: str, terms: frozenset[str]) -> str | None:
        """读取文本中首个命中的关键词。

        :param text: 待检查文本。
        :param terms: 关键词集合。
        :return: 首个命中的关键词；未命中时返回 None。
        """

        for term in terms:
            if term in text:
                return term
        return None

    def _matched_t4(self, text: str) -> str | None:
        """读取文本中首个命中的 T4 模式片段。

        :param text: 待检查文本。
        :return: 首个命中的 T4 模式片段；未命中时返回 None。
        """

        match = _T4_PATTERN.search(text)
        if match is None:
            return None
        return match.group(0)

    def _contains_t4_regimen(self, text: str) -> bool:
        """判断文本是否包含精确计量模式。

        :param text: 待检查文本。
        :return: 若命中精确剂量、频次或疗程模式则返回 True。
        """

        return _T4_PATTERN.search(text) is not None


def create_default_vet_output_safety_reviewer(
    *,
    runtime_config_provider: RuntimeConfigProvider,
    agent_runner: AgentRunner | None = None,
    medication_policy_port: MedicationPolicyPort | None = None,
    trace_sink: VetOutputSafetyReviewerTraceSink | None = None,
    observability_provider: ObservabilityProvider | None = None,
) -> VetOutputSafetyReviewer:
    """创建默认 VetOutputSafetyReviewer 服务实例。

    :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
    :param agent_runner: 可选 AgentRunner 端口。
    :param medication_policy_port: 可选用药策略端口。
    :param trace_sink: 可选输出审查 trace 写入端口。
    :param observability_provider: 可选 Observability provider。
    :return: 默认 VetOutputSafetyReviewer 服务实例。
    """

    return DefaultVetOutputSafetyReviewer(
        runtime_config_provider=runtime_config_provider,
        agent_runner=agent_runner,
        medication_policy_port=medication_policy_port,
        trace_sink=trace_sink,
        observability_provider=observability_provider,
    )


def request_ref_from_context(
    *,
    context: ReviewInputContextDto,
) -> str | None:
    """从上下文中读取可用于证据引用的摘要引用。

    :param context: 输出安全审查上下文。
    :return: 可展示的摘要引用；不可用时返回 None。
    """

    return context.context_summary_ref or context.content_plan_ref


__all__: tuple[str, ...] = (
    "DefaultVetOutputSafetyReviewer",
    "VetOutputSafetyReviewer",
    "create_default_vet_output_safety_reviewer",
)

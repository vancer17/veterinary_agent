"""
文件：src/vet_agent/output_safety/detectors.py
作用：封装 Guardrails 输出安全检测器，并将检测结果转换为结构化候选。
范围：仅负责发现系统提示泄露、PII、密钥、剂量、药物、主题越界和长度风险候选，不直接改写或阻断响应。
说明：Guardrails Hub 校验器按需延迟导入；启用后检测失败按 Fail Fast 抛出异常，避免静默退回旧正则替换路径。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Protocol, cast

from vet_agent import Settings
from vet_agent.output_safety.models import (
    OutputSafetyCandidate,
    OutputSafetyCandidateCategory,
    OutputSafetyCandidateSource,
    OutputSafetyReviewContext,
    OutputSafetySegment,
)
from vet_agent.output_safety.repository import OutputSafetyRepository


_NO_DOSAGE_EXPRESSION_REGEX = (
    r"(?s)^(?!.*\b\d+(?:\.\d+)?\s*"
    r"(?:mg/kg|mg\s*/\s*kg|毫克/公斤|毫克每公斤|ml/kg|mL/kg|毫升/公斤)\b).*$"
)


class OutputSafetyDetector(Protocol):
    """定义输出安全候选检测器协议。

    :return: 无返回值。
    """

    def collect(self, context: OutputSafetyReviewContext) -> tuple[OutputSafetyCandidate, ...]:
        """采集本轮输出安全候选。

        :param context: 本轮输出安全复核上下文。
        :return: 返回输出安全候选元组。
        """
        ...

    def is_ready(self) -> bool:
        """检查检测器是否就绪。

        :return: 检测器可用时返回 True。
        """
        ...


@dataclass(frozen=True)
class _ValidatorSpec:
    """表示一个 Guardrails 输出校验器的运行配置。

    :param code: 输出安全候选编码。
    :param category: 输出安全候选类别。
    :param severity: 候选默认严重级别。
    :param message: 候选默认说明。
    :param detector: 检测器审计标识。
    :param module_path: Guardrails 校验器模块路径。
    :param class_name: Guardrails 校验器类名。
    :param kwargs: 校验器构造参数。
    :param metadata: 附加审计信息。
    :return: 无返回值。
    """

    code: str
    category: OutputSafetyCandidateCategory
    severity: str
    message: str
    detector: str
    module_path: str
    class_name: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _ValidationOutcome:
    """表示单个 Guardrails 校验器的归一检测结果。

    :param failed: Guardrails 校验是否失败。
    :param error_message: 校验失败说明。
    :param matched_terms: 校验器返回的错误片段文本。
    :param metadata: 检测器附加信息。
    :return: 无返回值。
    """

    failed: bool
    error_message: str
    matched_terms: tuple[str, ...]
    metadata: dict[str, Any]


class GuardrailsOutputSafetyDetector(OutputSafetyDetector):
    """使用 Guardrails 检测输出安全候选。

    :return: 无返回值。
    """

    def __init__(
        self,
        settings: Settings,
        repository: OutputSafetyRepository,
        *,
        protected_system_prompt: str,
    ) -> None:
        """初始化 Guardrails 输出安全检测器。

        :param settings: 应用配置对象。
        :param repository: 输出安全候选定义仓储。
        :param protected_system_prompt: 用于检测系统提示泄露的受保护提示摘要。
        :return: 无返回值。
        :raises ValueError: 受保护系统提示摘要为空时抛出。
        """
        if not protected_system_prompt.strip():
            raise ValueError("protected_system_prompt is required for output safety detector audit context")
        self.settings = settings
        self.repository = repository
        self.protected_system_prompt = protected_system_prompt
        self._validators: tuple[tuple[_ValidatorSpec, Any], ...] | None = None

    def collect(self, context: OutputSafetyReviewContext) -> tuple[OutputSafetyCandidate, ...]:
        """采集本轮输出安全候选。

        :param context: 本轮输出安全复核上下文。
        :return: 返回输出安全候选元组。
        """
        if not self.settings.enable_output_safety_guardrails:
            return ()
        candidates: list[OutputSafetyCandidate] = []
        for segment in context.texts_for_detection():
            text = segment.text.strip()
            if not text:
                continue
            for spec, validator in self._validator_instances():
                outcome = self._validate(validator, text, self._metadata(context, segment, spec))
                if outcome.failed:
                    candidates.append(self._candidate(spec, segment, outcome))
        return tuple(candidates)

    def is_ready(self) -> bool:
        """检查检测器是否就绪。

        :return: 启用状态下候选定义仓储可用时返回 True。
        """
        if not self.settings.enable_output_safety_guardrails:
            return True
        return self.repository.is_ready()

    def _validator_instances(self) -> tuple[tuple[_ValidatorSpec, Any], ...]:
        """延迟构造 Guardrails 校验器实例。

        :return: 返回校验器配置与实例元组。
        :raises RuntimeError: Guardrails 校验器导入或构造失败时抛出。
        """
        if self._validators is not None:
            return self._validators
        validators: list[tuple[_ValidatorSpec, Any]] = []
        for spec in self._validator_specs():
            try:
                validator_cls = getattr(import_module(spec.module_path), spec.class_name)
                validators.append((spec, validator_cls(**spec.kwargs)))
            except Exception as exc:
                raise RuntimeError(f"output safety guardrails validator unavailable: {spec.detector}") from exc
        self._validators = tuple(validators)
        return self._validators

    def _validator_specs(self) -> tuple[_ValidatorSpec, ...]:
        """构造输出安全 Guardrails 校验器清单。

        :return: 返回校验器运行配置元组。
        """
        return (
            _ValidatorSpec(
                code="OUTPUT_SYSTEM_PROMPT_LEAKAGE",
                category=OutputSafetyCandidateCategory.PROMPT_LEAKAGE,
                severity="blocked",
                message="输出可能泄露系统提示或内部指令。",
                detector="guardrails_detect_system_prompt_leakage",
                module_path="guardrails_ai.detect_system_prompt_leakage.main",
                class_name="DetectSystemPromptLeakage",
                kwargs={
                    "system_prompt": self.protected_system_prompt,
                    "threshold": self.settings.output_safety_system_prompt_leakage_threshold,
                },
            ),
            _ValidatorSpec(
                code="OUTPUT_PII_DETECTED",
                category=OutputSafetyCandidateCategory.PII,
                severity="blocked",
                message="输出可能包含个人身份信息。",
                detector="guardrails_detect_pii",
                module_path="guardrails_ai.detect_pii.main",
                class_name="DetectPII",
                kwargs={"pii_entities": "pii"},
            ),
            _ValidatorSpec(
                code="OUTPUT_SECRET_DETECTED",
                category=OutputSafetyCandidateCategory.SECRET,
                severity="blocked",
                message="输出可能包含密钥、令牌或密码。",
                detector="guardrails_secrets_present",
                module_path="guardrails_ai.secrets_present.main",
                class_name="SecretsPresent",
            ),
            _ValidatorSpec(
                code="OUTPUT_DOSAGE_EXPRESSION",
                category=OutputSafetyCandidateCategory.DOSAGE,
                severity="caution",
                message="输出出现具体剂量表达，需要策略层裁决是否允许交付。",
                detector="guardrails_regex_match_no_dosage_expression",
                module_path="guardrails_ai.regex_match.main",
                class_name="RegexMatch",
                kwargs={"regex": _NO_DOSAGE_EXPRESSION_REGEX, "match_type": "fullmatch"},
                metadata={"constraint": "no_specific_dosage_expression"},
            ),
            _ValidatorSpec(
                code="OUTPUT_MEDICATION_MENTIONED",
                category=OutputSafetyCandidateCategory.MEDICATION,
                severity="caution",
                message="输出涉及药物名称，需要策略层裁决用药边界。",
                detector="guardrails_mentions_drugs",
                module_path="guardrails_ai.mentions_drugs.main",
                class_name="MentionsDrugs",
            ),
            _ValidatorSpec(
                code="OUTPUT_TOPIC_BOUNDARY",
                category=OutputSafetyCandidateCategory.TOPIC_BOUNDARY,
                severity="caution",
                message="输出主题可能偏离宠物健康咨询范围。",
                detector="guardrails_restrict_to_topic",
                module_path="guardrails_ai.restricttotopic.main",
                class_name="RestrictToTopic",
                kwargs={
                    "valid_topics": ["veterinary medicine", "pet health consultation"],
                    "invalid_topics": ["human medical advice", "legal advice", "financial advice"],
                    "device": "cpu",
                    "disable_llm": True,
                },
            ),
            _ValidatorSpec(
                code="OUTPUT_LENGTH_EXCEEDED",
                category=OutputSafetyCandidateCategory.FORMAT,
                severity="caution",
                message="输出长度超过当前服务允许的最大字符数。",
                detector="guardrails_valid_length",
                module_path="guardrails_ai.valid_length.main",
                class_name="ValidLength",
                kwargs={"max": self.settings.output_safety_max_chars},
            ),
        )

    def _metadata(
        self,
        context: OutputSafetyReviewContext,
        segment: OutputSafetySegment,
        spec: _ValidatorSpec,
    ) -> dict[str, Any]:
        """构造单次 Guardrails 校验调用的 metadata。

        :param context: 本轮输出安全复核上下文。
        :param segment: 当前检测片段。
        :param spec: 当前校验器配置。
        :return: 返回校验器 metadata。
        """
        return {
            "request_id": context.request_id,
            "trace_id": context.trace_id,
            "response_id": context.response_id,
            "segment_id": segment.segment_id,
            "validator": spec.detector,
        }

    def _candidate(
        self,
        spec: _ValidatorSpec,
        segment: OutputSafetySegment,
        outcome: _ValidationOutcome,
    ) -> OutputSafetyCandidate:
        """根据仓储定义与检测器结果构造输出安全候选。

        :param spec: 当前校验器配置。
        :param segment: 当前检测片段。
        :param outcome: Guardrails 归一检测结果。
        :return: 返回输出安全候选。
        """
        definition = self.repository.definition_by_code(spec.code)
        return OutputSafetyCandidate(
            code=spec.code,
            category=definition.category if definition else spec.category,
            source=OutputSafetyCandidateSource.GUARDRAILS,
            severity=definition.default_severity if definition else spec.severity,
            message=definition.message if definition else spec.message,
            confidence=float(outcome.metadata.get("confidence", 1.0)),
            segment_id=segment.segment_id,
            matched_terms=outcome.matched_terms,
            metadata={
                "detector": definition.detector if definition else spec.detector,
                "segment_type": segment.segment_type,
                "segment_title": segment.title,
                **spec.metadata,
                **outcome.metadata,
            },
        )

    def _validate(self, validator: Any, text: str, metadata: dict[str, Any]) -> _ValidationOutcome:
        """执行单个 Guardrails 校验并转换结果。

        :param validator: Guardrails 校验器实例。
        :param text: 待检测文本。
        :param metadata: 检测器调用 metadata。
        :return: 返回归一检测结果。
        :raises RuntimeError: 检测器调用失败时抛出，避免静默回退。
        """
        try:
            result = validator.validate(text, metadata)
        except Exception as exc:
            raise RuntimeError("output safety guardrails detector failed") from exc
        if _is_failed_validation(result):
            return _ValidationOutcome(
                failed=True,
                error_message=_error_message(result),
                matched_terms=_matched_terms_from_result(text, result),
                metadata={
                    "error_message": _error_message(result),
                    "fix_value_present": getattr(result, "fix_value", None) is not None,
                    "validator_metadata": cast(dict[str, Any], getattr(result, "metadata", {}) or {}),
                },
            )
        return _ValidationOutcome(failed=False, error_message="", matched_terms=(), metadata={})


def _is_failed_validation(result: Any) -> bool:
    """判断 Guardrails 校验结果是否失败。

    :param result: Guardrails 校验结果对象。
    :return: 校验失败时返回 True。
    """
    outcome = getattr(result, "outcome", None)
    if str(outcome).lower().endswith("fail"):
        return True
    if getattr(outcome, "value", None) == "fail":
        return True
    return result.__class__.__name__ == "FailResult"


def _error_message(result: Any) -> str:
    """从 Guardrails 校验结果中提取错误说明。

    :param result: Guardrails 校验结果对象。
    :return: 返回错误说明文本。
    """
    message = getattr(result, "error_message", None) or getattr(result, "errorMessage", None)
    return str(message or "")


def _matched_terms_from_result(text: str, result: Any) -> tuple[str, ...]:
    """从 Guardrails 错误片段中提取候选线索。

    :param text: 当前检测文本。
    :param result: Guardrails 校验结果对象。
    :return: 返回去重后的错误片段文本元组。
    """
    spans = getattr(result, "error_spans", None) or []
    terms: list[str] = []
    for span in spans:
        start = getattr(span, "start", None)
        end = getattr(span, "end", None)
        if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text):
            term = text[start:end].strip()
            if term and term not in terms:
                terms.append(term)
    return tuple(terms)

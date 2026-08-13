"""
文件：src/vet_agent/input_safety/detectors.py
作用：封装 Guardrails 输入安全检测器，并将检测结果转换为结构化候选。
范围：仅负责提示注入等非临床输入风险预筛，不直接阻断主链路。
说明：检测器失败在启用模式下按 Fail Fast 抛出异常，避免静默退回旧关键词规则。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, cast

from guardrails_ai.types.validation_result import ValidationResult
from guardrails_ai.prompt_injection_detector.main import PromptInjectionDetector

from vet_agent import Settings
from vet_agent.input_safety.models import (
    InputSafetyCandidate,
    InputSafetyCandidateCategory,
    InputSafetyCandidateSource,
    InputSafetyRequestContext,
)
from vet_agent.input_safety.repository import InputSafetyRepository


class InputSafetyDetector(Protocol):
    """定义基础输入安全候选检测器协议。

    :return: 无返回值。
    """

    def collect(self, context: InputSafetyRequestContext) -> tuple[InputSafetyCandidate, ...]:
        """采集本轮输入安全候选。

        :param context: 本轮输入安全请求上下文。
        :return: 返回输入安全候选元组。
        """
        ...

    def is_ready(self) -> bool:
        """检查检测器是否就绪。

        :return: 检测器可用时返回 True。
        """
        ...


@dataclass(frozen=True)
class _DetectorOutcome:
    """表示单个 Guardrails 检测器的归一结果。

    :param failed: Guardrails 校验是否失败。
    :param error_message: 校验失败说明。
    :param metadata: 检测器附加信息。
    :return: 无返回值。
    """

    failed: bool
    error_message: str
    metadata: dict[str, Any]


class GuardrailsInputSafetyDetector(InputSafetyDetector):
    """使用 Guardrails 检测基础输入安全候选。

    :return: 无返回值。
    """

    def __init__(
        self,
        settings: Settings,
        repository: InputSafetyRepository,
        *,
        system_prompt: str,
    ) -> None:
        """初始化 Guardrails 输入安全检测器。

        :param settings: 应用配置对象。
        :param repository: 输入安全候选定义仓储。
        :param system_prompt: 用于记录检测器受保护提示边界的系统提示摘要。
        :return: 无返回值。
        """
        self.settings = settings
        self.repository = repository
        self.system_prompt = system_prompt
        self._prompt_injection = PromptInjectionDetector(
            llm_callable=settings.input_safety_guardrails_model,
            threshold=settings.input_safety_prompt_injection_threshold,
        )
        if not system_prompt.strip():
            raise ValueError("system_prompt is required for input safety detector audit context")

    def collect(self, context: InputSafetyRequestContext) -> tuple[InputSafetyCandidate, ...]:
        """采集本轮输入安全候选。

        :param context: 本轮输入安全请求上下文。
        :return: 返回输入安全候选元组。
        """
        if not self.settings.enable_input_safety_guardrails:
            return ()
        text = context.text.strip()
        if not text:
            return ()
        candidates: list[InputSafetyCandidate] = []
        injection = self._validate(self._prompt_injection, text, {"request_id": context.request_id})
        if injection.failed:
            candidate = self._candidate(
                code="PROMPT_INJECTION_ATTEMPT",
                fallback_category=InputSafetyCandidateCategory.PROMPT_ATTACK,
                fallback_severity="blocked",
                fallback_message="输入存在越权或提示注入风险。",
                detector_metadata=injection.metadata,
                matched_terms=(),
            )
            candidates.append(candidate)
        return tuple(candidates)

    def is_ready(self) -> bool:
        """检查检测器是否就绪。

        :return: 启用状态下候选定义与模型配置均可用时返回 True。
        """
        if not self.settings.enable_input_safety_guardrails:
            return True
        return bool(self.settings.litellm_configured and self.repository.is_ready())

    def _candidate(
        self,
        *,
        code: str,
        fallback_category: InputSafetyCandidateCategory,
        fallback_severity: str,
        fallback_message: str,
        detector_metadata: dict[str, Any],
        matched_terms: tuple[str, ...],
    ) -> InputSafetyCandidate:
        """根据仓储定义与检测器结果构造输入安全候选。

        :param code: 候选编码。
        :param fallback_category: 仓储未配置时的候选类别。
        :param fallback_severity: 仓储未配置时的候选严重级别。
        :param fallback_message: 仓储未配置时的候选说明。
        :param detector_metadata: 检测器返回的审计元数据。
        :param matched_terms: 结构化命中线索。
        :return: 返回输入安全候选。
        """
        definition = self.repository.definition_by_code(code)
        return InputSafetyCandidate(
            code=code,
            category=definition.category if definition else fallback_category,
            source=InputSafetyCandidateSource.GUARDRAILS,
            severity=definition.default_severity if definition else fallback_severity,
            message=definition.message if definition else fallback_message,
            confidence=float(detector_metadata.get("confidence", 1.0)),
            matched_terms=matched_terms,
            metadata={
                "detector": definition.detector if definition else "guardrails",
                **detector_metadata,
            },
        )

    def _validate(self, validator: Any, text: str, metadata: dict[str, Any]) -> _DetectorOutcome:
        """执行单个 Guardrails 校验并转换结果。

        :param validator: Guardrails 校验器实例。
        :param text: 待检测文本。
        :param metadata: 检测器调用元数据。
        :return: 返回归一检测结果。
        :raises RuntimeError: 检测器调用失败时抛出，避免静默回退。
        """
        try:
            result = cast(ValidationResult, validator.validate(text, metadata))
        except Exception as exc:
            raise RuntimeError("input safety guardrails detector failed") from exc
        if _is_failed_validation(result):
            return _DetectorOutcome(
                failed=True,
                error_message=str(result.error_message),
                metadata={
                    "error_message": str(result.error_message),
                    "fix_value": getattr(result, "fix_value", None),
                },
            )
        return _DetectorOutcome(failed=False, error_message="", metadata={})


def _is_failed_validation(result: ValidationResult) -> bool:
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

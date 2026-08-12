"""
文件：src/vet_agent/input_safety/service.py
作用：编排基础输入安全候选采集与 OPA 策略裁决。
范围：位于 Agent 主链路起始阶段，替代旧 SafetyAgent.analyze 输入硬规则路径。
说明：本服务不读取旧 safety_rules，不提供关键词回退，不承载临床安全分诊。
"""

from __future__ import annotations

from vet_agent import Settings
from vet_agent.input_safety.detectors import InputSafetyDetector
from vet_agent.input_safety.models import (
    InputSafetyCandidate,
    InputSafetyCandidateCategory,
    InputSafetyCandidateSource,
    InputSafetyDecision,
    InputSafetyRequestContext,
)
from vet_agent.input_safety.policy import InputSafetyPolicyClient
from vet_agent.input_safety.repository import InputSafetyRepository


_UNOPENED_ATTACHMENT_PURPOSES: tuple[str, ...] = ("radiology", "xray", "x_ray", "ct", "mri")


class InputSafetyService:
    """编排基础输入安全候选采集与策略裁决。

    :return: 无返回值。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        repository: InputSafetyRepository,
        detectors: tuple[InputSafetyDetector, ...],
        policy_client: InputSafetyPolicyClient,
    ) -> None:
        """初始化基础输入安全服务。

        :param settings: 应用配置对象。
        :param repository: 输入安全候选定义仓储。
        :param detectors: 输入安全检测器集合。
        :param policy_client: 输入安全策略裁决客户端。
        :return: 无返回值。
        """
        self.settings = settings
        self.repository = repository
        self.detectors = detectors
        self.policy_client = policy_client

    async def evaluate(self, context: InputSafetyRequestContext) -> InputSafetyDecision:
        """采集候选并执行策略裁决。

        :param context: 本轮输入安全请求上下文。
        :return: 返回输入安全策略裁决。
        """
        candidates = self._baseline_candidates(context)
        for detector in self.detectors:
            candidates.extend(detector.collect(context))
        deduped = self._dedupe(candidates)
        if not deduped and not self.settings.input_safety_policy_always_call:
            return InputSafetyDecision.allow_turn(metadata={"candidate_count": 0})
        decision = await self.policy_client.decide(context, tuple(deduped))
        return InputSafetyDecision(
            action=decision.action,
            allow=decision.allow,
            message=decision.message,
            reasons=decision.reasons,
            candidates=decision.candidates,
            signals=decision.signals,
            metadata={**decision.metadata, "candidate_count": len(deduped)},
        )

    def is_ready(self) -> bool:
        """检查输入安全服务依赖是否就绪。

        :return: 候选仓储、检测器和策略客户端均就绪时返回 True。
        """
        return (
            self.repository.is_ready()
            and self.policy_client.is_ready()
            and all(detector.is_ready() for detector in self.detectors)
        )

    def _baseline_candidates(self, context: InputSafetyRequestContext) -> list[InputSafetyCandidate]:
        """采集只依赖结构化请求字段的基础候选。

        :param context: 本轮输入安全请求上下文。
        :return: 返回基础候选列表。
        """
        candidates: list[InputSafetyCandidate] = []
        stripped = context.text.strip()
        if not stripped and not context.attachments:
            candidates.append(
                self._candidate(
                    code="EMPTY_INPUT",
                    category=InputSafetyCandidateCategory.INTEGRITY,
                    severity="blocked",
                    message="输入文本与附件均为空。",
                    matched_terms=(),
                    metadata={"text_length": len(context.text), "attachment_count": len(context.attachments)},
                )
            )
        if len(context.text) > self.settings.max_input_chars:
            candidates.append(
                self._candidate(
                    code="INPUT_TOO_LONG",
                    category=InputSafetyCandidateCategory.INTEGRITY,
                    severity="blocked",
                    message="输入文本超过当前服务允许的最大长度。",
                    matched_terms=(str(len(context.text)),),
                    metadata={"max_input_chars": self.settings.max_input_chars},
                )
            )
        if len(context.attachments) > self.settings.max_attachments:
            candidates.append(
                self._candidate(
                    code="TOO_MANY_ATTACHMENTS",
                    category=InputSafetyCandidateCategory.INTEGRITY,
                    severity="blocked",
                    message="附件数量超过当前服务允许的最大数量。",
                    matched_terms=(str(len(context.attachments)),),
                    metadata={"max_attachments": self.settings.max_attachments},
                )
            )
        for attachment in context.attachments:
            purpose = attachment.purpose.strip().lower()
            if not attachment.mime_type.strip():
                candidates.append(
                    self._candidate(
                        code="ATTACHMENT_MIME_TYPE_MISSING",
                        category=InputSafetyCandidateCategory.INTEGRITY,
                        severity="blocked",
                        message="附件缺少 MIME 类型。",
                        matched_terms=(attachment.attachment_id,),
                        metadata={"attachment_id": attachment.attachment_id},
                    )
                )
            if not purpose or purpose == "unknown":
                candidates.append(
                    self._candidate(
                        code="ATTACHMENT_PURPOSE_UNKNOWN",
                        category=InputSafetyCandidateCategory.INTEGRITY,
                        severity="caution",
                        message="附件用途未明确声明。",
                        matched_terms=(attachment.attachment_id,),
                        metadata={"attachment_id": attachment.attachment_id},
                    )
                )
            if purpose in _UNOPENED_ATTACHMENT_PURPOSES:
                candidates.append(
                    self._candidate(
                        code="RADIOLOGY_GATE",
                        category=InputSafetyCandidateCategory.UNOPENED_CAPABILITY,
                        severity="blocked",
                        message="当前服务未开放影像判读能力，不能根据 X 光、B 超、CT 或 MRI 附件给出影像诊断结论。",
                        matched_terms=(attachment.attachment_id,),
                        metadata={"attachment_id": attachment.attachment_id, "purpose": purpose},
                    )
                )
        return candidates

    def _candidate(
        self,
        *,
        code: str,
        category: InputSafetyCandidateCategory,
        severity: str,
        message: str,
        matched_terms: tuple[str, ...],
        metadata: dict[str, object],
    ) -> InputSafetyCandidate:
        """根据仓储定义构造结构化基础候选。

        :param code: 候选编码。
        :param category: 候选类别。
        :param severity: 默认严重级别。
        :param message: 默认说明。
        :param matched_terms: 结构化关联线索。
        :param metadata: 附加审计信息。
        :return: 返回输入安全候选。
        """
        definition = self.repository.definition_by_code(code)
        return InputSafetyCandidate(
            code=code,
            category=definition.category if definition else category,
            source=InputSafetyCandidateSource.STRUCTURED_REQUEST,
            severity=definition.default_severity if definition else severity,
            message=definition.message if definition else message,
            matched_terms=matched_terms,
            metadata={
                "detector": definition.detector if definition else "structured_request",
                **metadata,
            },
        )

    def _dedupe(self, candidates: list[InputSafetyCandidate]) -> tuple[InputSafetyCandidate, ...]:
        """按候选编码与命中线索去重。

        :param candidates: 待去重候选列表。
        :return: 返回去重后的候选元组。
        """
        seen: set[tuple[str, tuple[str, ...]]] = set()
        result: list[InputSafetyCandidate] = []
        for candidate in candidates:
            key = (candidate.code, candidate.matched_terms)
            if key in seen:
                continue
            seen.add(key)
            result.append(candidate)
        return tuple(result)

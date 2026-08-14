"""
=============================================================================
文件：src/vet_agent/consultation_state/service.py
作用：编排问诊状态合并、证据画像构建、回答充分性裁决与追问响应格式化。
范围：位于问诊语义抽取、临床安全与 RAG 追问规划之间；本层只消费已结构化
      的状态、语义和宠物上下文，不扫描用户原始文本，不实现关键词状态机。
说明：回答与追问的最终准入由 OPA 策略客户端决定；本服务仅负责状态合并、
      证据汇总和追问文案组织，避免回退到自定义规则状态机。
=============================================================================
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from vet_agent.clinical_safety import ClinicalSafetySemanticResult
from vet_agent.repositories import ConsultationRuleSet, RuleRepository
from vet_agent.services import PetContext

from .errors import ConsultationStateContractError, ConsultationStateDependencyError
from .models import (
    AnswerabilityDecision,
    ConsultationDecision,
    ConsultationState,
    ConsultationStatePolicyContext,
    ConsultationStatePolicyInput,
    ConsultationStatePolicyIntent,
    ConsultationStatePolicyLimits,
    ConsultationStatePolicyState,
)
from .policy import ConsultationAnswerabilityPolicyClient

if TYPE_CHECKING:
    from vet_agent.agents.semantic_extractor import SemanticExtractionResult


class ConsultationStateService:
    """在多轮问诊链路中合并结构化事实并生成回答充分性决策。

    :return: 无返回值；该服务是问诊状态与回答充分性迁移后的稳定编排入口。
    """

    def __init__(
        self,
        rule_repository: RuleRepository,
        policy_client: ConsultationAnswerabilityPolicyClient,
        *,
        max_followup_rounds: int = 2,
    ) -> None:
        """初始化当前对象。

        :param rule_repository: 问诊领域与槽位文案仓储。
        :param policy_client: 问诊回答充分性策略客户端。
        :param max_followup_rounds: 同一问诊最多连续追问轮数。
        :return: 无返回值。
        """
        self.rule_repository = rule_repository
        self.policy_client = policy_client
        self.max_followup_rounds = max(1, max_followup_rounds)

    async def update(
        self,
        previous: dict[str, Any] | None,
        user_text: str,
        pet_context: PetContext,
        *,
        policy_context: ConsultationStatePolicyContext,
        task_domain: str,
        semantic_result: SemanticExtractionResult | None = None,
        clinical_safety_semantic: ClinicalSafetySemanticResult | None = None,
        max_questions: int,
    ) -> ConsultationDecision:
        """更新多轮问诊状态并给出本轮回答或追问决策。

        :param previous: 上一轮持久化状态。
        :param user_text: 用户输入文本。
        :param pet_context: 宠物上下文。
        :param policy_context: 当前回合可信请求范围摘要。
        :param task_domain: 已由任务路由器确定的稳定任务域。
        :param semantic_result: 结构化问诊语义抽取结果。
        :param clinical_safety_semantic: 临床安全语义抽取结果。
        :param max_questions: 本轮最多追问数量。
        :return: 返回本轮问诊决策。
        """
        state = ConsultationState.from_dict(previous)
        self._merge_user_text(state, user_text)
        state.domain = task_domain
        self._prefill_from_pet_context(state, pet_context)
        self._merge_semantic_result(state, semantic_result)
        self._merge_clinical_safety_temporal_context(state, clinical_safety_semantic)
        state.user_intent = self._semantic_intent(semantic_result)

        rules = self.rule_repository.consultation_rules()
        unresolved_slots = self._required_slots(rules, state.domain)
        unresolved_slots = [slot for slot in unresolved_slots if not state.slots.get(slot)]
        state.evidence_profile = self._build_evidence_profile(state, unresolved_slots, rules)
        policy_input = self._build_policy_input(
            state=state,
            policy_context=policy_context,
            evidence_profile=state.evidence_profile,
            unresolved_slots=tuple(unresolved_slots),
            max_questions=max_questions,
        )

        try:
            answerability = await self.policy_client.decide(policy_input)
        except ConsultationStateDependencyError:
            raise
        except Exception as exc:  # pragma: no cover - 由策略客户端封装失败原因
            raise ConsultationStateDependencyError(
                "consultation answerability policy evaluation failed",
                details={"error_type": type(exc).__name__},
            ) from exc

        state.answerability = answerability.to_dict()
        ready = answerability.decision == "answer"
        missing_slots = [] if ready else list(answerability.blocking_slots or answerability.unresolved_slots)
        if not ready and not missing_slots:
            raise ConsultationStateContractError(
                "consultation answerability policy returned ask decision without follow-up slots",
                details={
                    "policy_backend": answerability.policy_backend,
                    "policy_path": answerability.policy_path,
                    "reason": answerability.reason,
                },
            )
        state.phase = "ready_to_answer" if ready else "collecting_info"

        return ConsultationDecision(
            state=state,
            ready=ready,
            missing_slots=[] if ready else missing_slots,
            questions=[],
            answerability=state.answerability,
        )

    def format_followup_response(
        self,
        decision: ConsultationDecision,
        *,
        question_reasons: list[str] | None = None,
    ) -> str:
        """格式化基于当前上下文生成的追问响应。

        :param decision: 问诊决策。
        :param question_reasons: 动态追问的知识库依据。
        :return: 返回追问响应文本。
        """
        rules = self.rule_repository.consultation_rules()
        known = self._known_lines(decision.state)
        missing = "、".join(self._label_for(rules, slot) for slot in decision.missing_slots[:5])
        questions = "\n".join(f"{index + 1}. {question}" for index, question in enumerate(decision.questions))
        reasons = "\n".join(question_reasons or [])
        reason_section = f"\n\n为什么先问这些？\n{reasons}" if reasons else ""
        return (
            "我先不武断下结论，先补一个最会影响分诊建议的关键上下文。"
            "这样可以避免把普通护理问题误判成疾病，"
            "也避免在证据不足时给出不可靠建议。\n\n"
            f"已知信息:\n{known or '- 目前只有你的主诉，还缺关键问诊信息。'}\n\n"
            f"本轮仍需确认: {missing or '关键问诊信息'}\n\n"
            f"请先回答:\n{questions}{reason_section}\n\n"
            f"{rules.safety_net_text}"
        )

    def is_ready(self) -> bool:
        """检查问诊状态服务是否就绪。

        :return: 仓储与策略客户端均就绪时返回 True。
        """
        return self.rule_repository.is_ready() and self.policy_client.is_ready()

    def _build_policy_input(
        self,
        *,
        state: ConsultationState,
        policy_context: ConsultationStatePolicyContext,
        evidence_profile: dict[str, Any],
        unresolved_slots: tuple[str, ...],
        max_questions: int,
    ) -> ConsultationStatePolicyInput:
        """构造回答充分性策略输入。

        :param state: 当前问诊状态。
        :param policy_context: 当前回合可信请求范围摘要。
        :param evidence_profile: 结构化证据画像。
        :param unresolved_slots: 尚未确认的槽位。
        :param max_questions: 本轮最多追问数量。
        :return: 返回结构化策略输入对象。
        """
        intent = ConsultationStatePolicyIntent(
            answer_now=bool(state.user_intent.get("answer_now")),
            wants_triage=bool(state.user_intent.get("wants_triage")),
            correction=bool(state.user_intent.get("correction")),
            raw_intent=str(state.user_intent.get("raw_intent") or "")[:120],
        )
        return ConsultationStatePolicyInput(
            context=policy_context,
            state=ConsultationStatePolicyState.from_state(state),
            intent=intent,
            limits=ConsultationStatePolicyLimits(
                max_followup_rounds=self.max_followup_rounds,
                max_questions=max(1, max_questions),
            ),
            evidence_profile=evidence_profile,
            unresolved_slots=unresolved_slots,
            advisory_slots=tuple(str(item) for item in evidence_profile.get("advisory_slots") or ()),
        )

    def _merge_user_text(self, state: ConsultationState, user_text: str) -> None:
        """合并本轮用户文本到问诊状态。

        :param state: 当前问诊状态。
        :param user_text: 用户输入文本。
        :return: 无返回值。
        """
        text = user_text.strip()
        if text and not state.chief_complaint:
            state.chief_complaint = text[:200]

    def _merge_clinical_safety_temporal_context(
        self,
        state: ConsultationState,
        semantic_result: ClinicalSafetySemanticResult | None,
    ) -> None:
        """将临床安全时间语义写入问诊状态。

        :param state: 当前问诊状态。
        :param semantic_result: 临床安全结构化语义结果。
        :return: 无返回值。
        """
        if semantic_result is None:
            return
        if semantic_result.temporal_scope == "unclear" and semantic_result.temporal_state in {"unknown", "unclear"}:
            return
        state.temporal_context = {
            "state": semantic_result.temporal_state,
            "scope": semantic_result.temporal_scope,
            "resolution_state": semantic_result.resolution_state,
            "text": semantic_result.temporal_text,
        }

    def _prefill_from_pet_context(self, state: ConsultationState, pet_context: PetContext) -> None:
        """从后端宠物资料预填稳定事实。

        :param state: 问诊状态。
        :param pet_context: 宠物上下文。
        :return: 无返回值。
        """
        profile = pet_context.verified_profile
        if profile.get("species") and profile["species"] != "未知":
            state.slots.setdefault("species", str(profile["species"]))
        if profile.get("age") and profile["age"] != "未知":
            state.slots.setdefault("life_stage_or_age", str(profile["age"]))
        if profile.get("weight_kg"):
            state.slots.setdefault("weight", f"{profile['weight_kg']}kg")

    def _merge_semantic_result(
        self,
        state: ConsultationState,
        semantic_result: SemanticExtractionResult | None,
    ) -> bool:
        """将 LLM 语义抽取结果合并到问诊状态。

        :param state: 当前问诊状态。
        :param semantic_result: 结构化问诊语义抽取结果。
        :return: 有可信事实进入当前问诊状态时返回 True。
        """
        metadata = semantic_result.to_metadata() if semantic_result is not None else {}
        if metadata:
            state.semantic_extraction = metadata
        if semantic_result is None or not semantic_result.is_trusted():
            return False
        facts = semantic_result.facts
        applied_observation_count = self._merge_semantic_observations(state, semantic_result)
        if not facts:
            if state.semantic_extraction:
                state.semantic_extraction["applied_fact_keys"] = []
                state.semantic_extraction["applied_observation_count"] = applied_observation_count
                state.semantic_extraction["used_as_primary_semantic_path"] = applied_observation_count > 0
            return applied_observation_count > 0

        rules = self.rule_repository.consultation_rules()
        correction = semantic_result.intent.correction
        applied_keys: list[str] = []
        for fact in facts:
            self._merge_core_working_fact(state, fact.to_dict(), correction=correction)
            key = fact.key.value
            status = fact.status
            value = fact.value.strip()
            if key not in rules.slots:
                continue
            if status in {"unknown", "uncertain"}:
                continue
            if key in {"species", "life_stage_or_age", "weight"} and state.slots.get(key) and not correction:
                continue
            if not value:
                continue
            state.slots[key] = value[:160]
            applied_keys.append(key)

        if state.semantic_extraction:
            state.semantic_extraction["applied_fact_keys"] = applied_keys
            state.semantic_extraction["applied_observation_count"] = applied_observation_count
            state.semantic_extraction["used_as_primary_semantic_path"] = bool(applied_keys or applied_observation_count)
        return bool(applied_keys or applied_observation_count)

    def _semantic_intent(self, semantic_result: SemanticExtractionResult | None) -> dict[str, Any]:
        """读取语义抽取结果中的用户意图。

        :param semantic_result: 结构化问诊语义抽取结果。
        :return: 返回回答充分性策略可消费的意图字典。
        """
        if semantic_result is None or not semantic_result.is_trusted():
            return {}
        return semantic_result.intent.to_dict()

    def _merge_core_working_fact(
        self,
        state: ConsultationState,
        fact: dict[str, Any],
        *,
        correction: bool,
    ) -> None:
        """将核心事实写入当前会话工作记忆。

        :param state: 当前问诊状态。
        :param fact: 已通过结构化契约校验的核心事实字典。
        :param correction: 本轮是否为用户明确纠正语境。
        :return: 无返回值。
        """
        key = str(fact.get("key") or "").strip()
        if not key:
            return
        record = {
            "kind": "core_fact",
            "key": key,
            "value": str(fact.get("value") or "")[:160],
            "status": str(fact.get("status") or "unknown"),
            "confidence": float(fact.get("confidence") or 0.0),
            "source_text": str(fact.get("source_text") or "")[:160],
            "category": str(fact.get("category") or "other"),
            "source": "consultation_semantic_extractor",
        }
        state.working_facts = self._upsert_current_fact(
            state.working_facts,
            record,
            key=key,
            replace_existing=correction or record["status"] in {"confirmed", "negative", "contradicted"},
            limit=64,
        )

    def _merge_semantic_observations(
        self,
        state: ConsultationState,
        semantic_result: SemanticExtractionResult,
    ) -> int:
        """将开放式结构化观察写入当前会话工作记忆。

        :param state: 当前问诊状态。
        :param semantic_result: 结构化问诊语义抽取结果。
        :return: 返回本轮新增或更新的开放观察数量。
        """
        applied_count = 0
        for observation in semantic_result.observations:
            if observation.status in {"unknown", "uncertain"}:
                continue
            record = observation.to_dict()
            record["kind"] = "open_observation"
            record["source"] = "consultation_semantic_extractor"
            state.observations = self._append_unique_record(
                state.observations,
                record,
                identity_fields=("category", "status", "value"),
                limit=48,
            )
            applied_count += 1
        return applied_count

    def _upsert_current_fact(
        self,
        records: list[dict[str, Any]],
        record: dict[str, Any],
        *,
        key: str,
        replace_existing: bool,
        limit: int,
    ) -> list[dict[str, Any]]:
        """按核心事实 key 更新当前工作记忆。

        :param records: 原有工作记忆记录。
        :param record: 本轮待写入记录。
        :param key: 核心事实键名。
        :param replace_existing: 是否替换同 key 的既有当前事实。
        :param limit: 最大保留记录数。
        :return: 返回更新后的工作记忆记录。
        """
        if replace_existing:
            records = [item for item in records if str(item.get("key") or "") != key]
        updated = self._append_unique_record(
            records,
            record,
            identity_fields=("kind", "key", "status", "value"),
            limit=limit,
        )
        return updated

    def _append_unique_record(
        self,
        records: list[dict[str, Any]],
        record: dict[str, Any],
        *,
        identity_fields: tuple[str, ...],
        limit: int,
    ) -> list[dict[str, Any]]:
        """追加去重后的结构化记录，并控制当前会话状态体积。

        :param records: 原有结构化记录列表。
        :param record: 待追加结构化记录。
        :param identity_fields: 用于判定重复的字段集合。
        :param limit: 最大保留记录数。
        :return: 返回追加后的结构化记录列表。
        """
        if self._record_identity_exists(records, record, identity_fields=identity_fields):
            return records[-limit:]
        return [*records, record][-limit:]

    def _record_identity_exists(
        self,
        records: list[dict[str, Any]],
        record: dict[str, Any],
        *,
        identity_fields: tuple[str, ...],
    ) -> bool:
        """判断结构化记录是否已存在。

        :param records: 已有结构化记录列表。
        :param record: 待检查结构化记录。
        :param identity_fields: 用于判定重复的字段集合。
        :return: 存在相同身份记录时返回 True，否则返回 False。
        """
        identity = tuple(str(record.get(field) or "") for field in identity_fields)
        return any(tuple(str(item.get(field) or "") for field in identity_fields) == identity for item in records)

    def _build_evidence_profile(
        self,
        state: ConsultationState,
        unresolved_slots: list[str],
        rules: ConsultationRuleSet,
    ) -> dict[str, Any]:
        """构建宽泛语义证据维度，避免固定槽位成为唯一门槛。

        :param state: 当前问诊状态。
        :param unresolved_slots: 尚未确认的槽位。
        :param rules: 问诊规则集合。
        :return: 返回结构化证据画像。
        """
        profile = {
            "patient_identity": self._profile_item(state, ["species", "life_stage_or_age", "weight"]),
            "symptom_profile": {
                "status": "known" if state.chief_complaint or state.slots.get("symptom_detail") else "unknown",
                "slots": ["chief_complaint", "symptom_detail"],
            },
            "time_course": self._time_course_profile(state),
            "systemic_status": self._profile_item(state, ["mental_status", "appetite"]),
            "intake_output": self._profile_item(state, ["appetite", "vomiting", "stool"]),
            "domain_specific": self._profile_item(
                state,
                ["breathing", "pain_or_mobility", "behavior_context", "current_food"],
            ),
            "open_observations": self._open_observation_profile(state),
        }
        known_categories = [
            category
            for category in (
                "time_course",
                "systemic_status",
                "intake_output",
                "domain_specific",
                "open_observations",
            )
            if profile.get(category, {}).get("status") == "known"
        ]
        advisory_slots = self._advisory_slots(state, unresolved_slots, rules, profile)
        profile["known_categories"] = known_categories
        profile["known_category_count"] = len(known_categories)
        profile["minimum_context"] = state.has_chief_complaint() and state.has_species()
        profile["advisory_slots"] = advisory_slots
        profile["unresolved_slots"] = unresolved_slots
        return profile

    def _advisory_slots(
        self,
        state: ConsultationState,
        unresolved_slots: list[str],
        rules: ConsultationRuleSet,
        profile: dict[str, Any],
    ) -> list[str]:
        """根据证据画像生成建议追问槽位。

        :param state: 当前问诊状态。
        :param unresolved_slots: 尚未确认的槽位。
        :param rules: 问诊规则集合。
        :param profile: 当前证据画像。
        :return: 返回建议追问槽位列表。
        """
        slot_order: list[str] = []
        category_mapping = (
            ("patient_identity", ("species", "life_stage_or_age", "weight")),
            ("symptom_profile", ("symptom_detail",)),
            ("time_course", ("onset",)),
            ("systemic_status", ("mental_status", "appetite")),
            ("intake_output", ("appetite", "vomiting", "stool")),
            ("domain_specific", ("breathing", "pain_or_mobility", "behavior_context", "current_food")),
        )
        unresolved_set = set(unresolved_slots)
        for category, slots in category_mapping:
            if profile.get(category, {}).get("status") == "known":
                continue
            for slot in slots:
                if slot in rules.slots and slot not in slot_order and (slot in unresolved_set or slot not in state.slots):
                    slot_order.append(slot)
        if not slot_order and unresolved_slots:
            slot_order.extend(slot for slot in unresolved_slots if slot in rules.slots)
        return slot_order

    def _open_observation_profile(self, state: ConsultationState) -> dict[str, Any]:
        """构建开放式结构化观察的证据画像摘要。

        :param state: 当前问诊状态。
        :return: 返回开放观察证据画像。
        """
        observations = [
            item
            for item in state.observations
            if item.get("value") and item.get("status") not in {"unknown", "uncertain"}
        ]
        categories = sorted({str(item.get("category") or "other") for item in observations})
        labels = [str(item.get("label") or item.get("category") or "观察") for item in observations[-6:]]
        return {
            "status": "known" if observations else "unknown",
            "count": len(observations),
            "categories": categories[:12],
            "labels": labels,
        }

    def _time_course_profile(self, state: ConsultationState) -> dict[str, Any]:
        """构建包含时间槽位和临床安全时间语义的时间证据画像。

        :param state: 当前问诊状态。
        :return: 返回时间证据画像。
        """
        profile = self._profile_item(state, ["onset"])
        if state.temporal_context:
            profile["status"] = "known"
            profile["temporal_context"] = dict(state.temporal_context)
        return profile

    def _profile_item(self, state: ConsultationState, slots: list[str]) -> dict[str, Any]:
        """构建单个语义证据维度的状态。

        :param state: 当前问诊状态。
        :param slots: 该维度关联的槽位。
        :return: 返回结构化证据维度状态。
        """
        known = [slot for slot in slots if state.slots.get(slot)]
        return {"status": "known" if known else "unknown", "slots": known}

    def _known_lines(self, state: ConsultationState) -> str:
        """格式化用户已经补充过的事实。

        :param state: 问诊状态。
        :return: 返回可展示的已知事实文本。
        """
        rules = self.rule_repository.consultation_rules()
        lines = []
        for slot, value in state.slots.items():
            if value:
                label = rules.slots[slot].label if slot in rules.slots else slot
                lines.append(f"- {label}: {value}")
        return "\n".join(lines)

    def _required_slots(self, rules: ConsultationRuleSet, domain: str) -> list[str]:
        """读取规则层建议关注的槽位。

        :param rules: 规则集合。
        :param domain: 问诊领域。
        :return: 返回规则建议的关注槽位。
        """
        if domain in rules.domains:
            return rules.domains[domain].required_slots
        return rules.domains.get("general").required_slots if "general" in rules.domains else []

    def _label_for(self, rules: ConsultationRuleSet, slot: str) -> str:
        """返回槽位对应的用户可见标签。

        :param rules: 规则集合。
        :param slot: 标准问诊槽位。
        :return: 返回用户可见标签。
        """
        return rules.slots[slot].label if slot in rules.slots else slot


ConsultationStateAgent = ConsultationStateService

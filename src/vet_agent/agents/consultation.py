"""
=============================================================================
文件：src/vet_agent/agents/consultation.py
作用：维护当前会话与任务范围内的活跃问诊状态，并基于结构化证据判断回答或追问。
范围：位于问诊语义抽取之后、追问 RAG 或回答 RAG 之前；本层只消费
      已通过结构化契约校验的问诊事实，不重新解析用户原始文本。
说明：本文件不执行关键词、正则或 seed 规则抽取，不写入长期记忆事实，
      不更新服务端可信宠物资料；核心槽位仅作为工作记忆的派生视图，
      长期事实治理由后续记忆写入链路负责。
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vet_agent.clinical_safety import ClinicalSafetySemanticResult
from vet_agent.repositories import ConsultationRuleSet, RuleRepository
from vet_agent.services import PetContext

from .semantic_extractor import ConsultationFactKey, ConsultationFactStatus, SemanticExtractionResult


SlotValue = str | bool | None


@dataclass
class ConsultationState:
    """表示当前 session 与任务范围内的活跃问诊状态。

    :param chief_complaint: 当前问诊主诉原文摘要。
    :param domain: 当前任务路由确定的问诊领域。
    :param phase: 当前问诊阶段。
    :param slots: 当前回答提示词兼容使用的核心槽位派生视图。
    :param working_facts: 当前会话范围内的结构化核心事实工作记忆。
    :param observations: 当前会话范围内无法归入核心槽位的开放观察。
    :param asked_questions: 已问过的追问问题。
    :param followup_rounds: 当前任务连续追问轮数。
    :param evidence_profile: 结构化证据画像。
    :param answerability: 回答充分性策略结果。
    :param user_intent: 本轮用户意图信号。
    :param semantic_extraction: 本轮问诊语义抽取 metadata。
    :param temporal_context: 临床安全链路提供的时间上下文摘要。
    :return: 无返回值。
    """

    chief_complaint: str | None = None
    domain: str = "general"
    phase: str = "collecting_info"
    slots: dict[str, SlotValue] = field(default_factory=dict)
    working_facts: list[dict[str, Any]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    asked_questions: list[str] = field(default_factory=list)
    followup_rounds: int = 0
    evidence_profile: dict[str, Any] = field(default_factory=dict)
    answerability: dict[str, Any] = field(default_factory=dict)
    user_intent: dict[str, Any] = field(default_factory=dict)
    semantic_extraction: dict[str, Any] = field(default_factory=dict)
    temporal_context: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ConsultationState":
        """从普通字典恢复问诊状态。

        :param data: 结构化状态字典。
        :return: 返回函数执行结果。
        """
        if not data:
            return cls()
        return cls(
            chief_complaint=data.get("chief_complaint"),
            domain=data.get("domain") or "general",
            phase=data.get("phase") or "collecting_info",
            slots=dict(data.get("slots") or {}),
            working_facts=list(data.get("working_facts") or []),
            observations=list(data.get("observations") or []),
            asked_questions=list(data.get("asked_questions") or []),
            followup_rounds=int(data.get("followup_rounds") or 0),
            evidence_profile=dict(data.get("evidence_profile") or {}),
            answerability=dict(data.get("answerability") or {}),
            user_intent=dict(data.get("user_intent") or {}),
            semantic_extraction=dict(data.get("semantic_extraction") or {}),
            temporal_context=dict(data.get("temporal_context") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为可持久化的普通字典。

        :return: 返回函数执行结果。
        """
        return {
            "chief_complaint": self.chief_complaint,
            "domain": self.domain,
            "phase": self.phase,
            "slots": self.slots,
            "working_facts": self.working_facts,
            "observations": self.observations,
            "asked_questions": self.asked_questions,
            "followup_rounds": self.followup_rounds,
            "evidence_profile": self.evidence_profile,
            "answerability": self.answerability,
            "user_intent": self.user_intent,
            "semantic_extraction": self.semantic_extraction,
            "temporal_context": self.temporal_context,
        }


@dataclass(frozen=True)
class AnswerabilityDecision:
    """表示回答充分性策略给出的下一步动作建议。

    :param decision: 策略动作，answer 表示阶段性回答，ask 表示继续追问。
    :param mode: 决策模式。
    :param answer_scope: 回答范围。
    :param blocking_slots: 仍阻塞回答的高价值证据槽位。
    :param unresolved_slots: 尚未确认但可能不再阻塞的证据槽位。
    :param reason: 决策原因。
    :return: 无返回值。
    """

    decision: str
    mode: str
    answer_scope: str
    blocking_slots: list[str]
    unresolved_slots: list[str]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """转换为响应 metadata 和持久化状态使用的字典。

        :return: 返回函数执行结果。
        """
        return {
            "decision": self.decision,
            "mode": self.mode,
            "answer_scope": self.answer_scope,
            "blocking_slots": self.blocking_slots,
            "unresolved_slots": self.unresolved_slots,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ConsultationDecision:
    """表示问诊状态合并后的本轮业务决策。

    :param state: 更新后的活跃问诊状态。
    :param ready: 是否进入阶段性回答路径。
    :param missing_slots: 本轮仍建议追问的槽位。
    :param questions: 本轮要输出的追问问题。
    :param answerability: 回答充分性策略 metadata。
    :return: 无返回值。
    """

    state: ConsultationState
    ready: bool
    missing_slots: list[str]
    questions: list[str]
    answerability: dict[str, Any] = field(default_factory=dict)


class AnswerabilityEvaluator:
    """基于语义证据充分性判断本轮应该回答还是继续追问。

    :return: 无返回值。
    """

    def __init__(self, *, max_followup_rounds: int = 2) -> None:
        """初始化回答充分性评估器。

        :param max_followup_rounds: 同一问诊最多连续追问轮数。
        :return: 无返回值。
        """
        self.max_followup_rounds = max(1, max_followup_rounds)

    def evaluate(
        self,
        *,
        state: ConsultationState,
        required_slots: list[str],
        unresolved_slots: list[str],
    ) -> AnswerabilityDecision:
        """根据证据状态判断是否可以进入阶段性回答。

        :param state: 当前问诊状态。
        :param required_slots: 规则层建议关注的槽位。
        :param unresolved_slots: 尚未确认的槽位。
        :return: 返回函数执行结果。
        """
        del required_slots
        if state.user_intent.get("answer_now"):
            return self._answer(
                "user_requested_answer_now",
                [],
                unresolved_slots,
                "用户明确要求根据现有信息先给阶段性判断。",
            )

        if not unresolved_slots:
            return self._answer("slot_complete", [], unresolved_slots, "规则建议关注的信息均已确认。")

        has_minimum = self._has_minimum_context(state)
        if state.followup_rounds >= self.max_followup_rounds and has_minimum:
            return self._answer(
                "max_followup_rounds_reached",
                [],
                unresolved_slots,
                "已达到连续追问轮数上限。",
            )

        if state.followup_rounds >= 1 and self._has_sufficient_semantic_evidence(state):
            return self._answer(
                "sufficient_semantic_evidence",
                [],
                unresolved_slots,
                "已获得足够的主诉、时间、整体状态或领域相关证据。",
            )

        blocking_slots = self._blocking_slots(state, unresolved_slots)
        return AnswerabilityDecision(
            decision="ask",
            mode="needs_high_value_evidence",
            answer_scope="insufficient",
            blocking_slots=blocking_slots,
            unresolved_slots=unresolved_slots,
            reason="仍缺少会明显影响分诊建议的高价值信息。",
        )

    def _answer(
        self,
        mode: str,
        blocking_slots: list[str],
        unresolved_slots: list[str],
        reason: str,
    ) -> AnswerabilityDecision:
        """构造允许阶段性回答的决策。

        :param mode: 回答模式。
        :param blocking_slots: 阻塞回答的槽位。
        :param unresolved_slots: 尚未确认但不再阻塞的槽位。
        :param reason: 决策原因。
        :return: 返回函数执行结果。
        """
        return AnswerabilityDecision(
            decision="answer",
            mode=mode,
            answer_scope="preliminary",
            blocking_slots=blocking_slots,
            unresolved_slots=unresolved_slots,
            reason=reason,
        )

    def _has_minimum_context(self, state: ConsultationState) -> bool:
        """判断是否具备阶段性回答的最低上下文。

        :param state: 当前问诊状态。
        :return: 返回函数执行结果。
        """
        return bool(state.chief_complaint and self._has_slot(state, "species"))

    def _has_sufficient_semantic_evidence(self, state: ConsultationState) -> bool:
        """判断语义证据维度是否已足够支持有限回答。

        :param state: 当前问诊状态。
        :return: 返回函数执行结果。
        """
        if not self._has_minimum_context(state):
            return False
        profile = state.evidence_profile or {}
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
        return len(known_categories) >= 2

    def _blocking_slots(self, state: ConsultationState, unresolved_slots: list[str]) -> list[str]:
        """从未确认槽位中挑选真正需要继续追问的高价值项。

        :param state: 当前问诊状态。
        :param unresolved_slots: 尚未确认的槽位。
        :return: 返回函数执行结果。
        """
        if "species" in unresolved_slots and not self._has_slot(state, "species"):
            return ["species"]
        if state.followup_rounds >= 1:
            high_value = [
                slot
                for slot in ("onset", "mental_status", "appetite", "breathing", "pain_or_mobility", "vomiting", "stool")
                if slot in unresolved_slots
            ]
            return high_value[:2] or unresolved_slots[:1]
        return unresolved_slots[:3]

    def _has_slot(self, state: ConsultationState, slot: str) -> bool:
        """判断槽位是否已经有可用值。

        :param state: 当前问诊状态。
        :param slot: 槽位名称。
        :return: 返回函数执行结果。
        """
        return state.slots.get(slot) not in (None, "", False)


class ConsultationStateAgent:
    """在多轮问诊链路中合并结构化事实并生成回答充分性决策。

    :return: 无返回值。
    """

    def __init__(self, rule_repository: RuleRepository, *, max_followup_rounds: int = 2) -> None:
        """初始化当前对象。

        :param rule_repository: 规则仓库。
        :param max_followup_rounds: 同一问诊最多连续追问轮数。
        :return: 无返回值。
        """
        self.rule_repository = rule_repository
        self.answerability_evaluator = AnswerabilityEvaluator(max_followup_rounds=max_followup_rounds)

    def update(
        self,
        previous: dict[str, Any] | None,
        user_text: str,
        pet_context: PetContext,
        *,
        task_domain: str,
        semantic_result: SemanticExtractionResult | None = None,
        clinical_safety_semantic: ClinicalSafetySemanticResult | None = None,
        max_questions: int,
    ) -> ConsultationDecision:
        """更新多轮问诊状态并给出本轮回答/追问决策。

        :param previous: 上一轮持久化状态。
        :param user_text: 用户输入文本。
        :param pet_context: 宠物上下文。
        :param task_domain: 已由任务路由器确定的稳定任务域。
        :param semantic_result: 结构化问诊语义抽取结果。
        :param clinical_safety_semantic: 临床安全语义抽取结果。
        :param max_questions: 本轮最多追问数量。
        :return: 返回函数执行结果。
        """
        state = ConsultationState.from_dict(previous)
        text = user_text.strip()
        if text and not state.chief_complaint:
            state.chief_complaint = text[:200]

        state.domain = task_domain
        self._prefill_from_pet_context(state, pet_context)
        self._merge_semantic_result(state, semantic_result)
        self._merge_clinical_safety_temporal_context(state, clinical_safety_semantic)
        state.user_intent = self._semantic_intent(semantic_result)

        rules = self.rule_repository.consultation_rules()
        required = self._required_slots(rules, state.domain)
        unresolved = [slot for slot in required if not state.slots.get(slot)]
        state.evidence_profile = self._build_evidence_profile(state, unresolved)
        answerability = self.answerability_evaluator.evaluate(
            state=state,
            required_slots=required,
            unresolved_slots=unresolved,
        )
        state.answerability = answerability.to_dict()

        ready = answerability.decision == "answer"
        followup_slots = [] if ready else list(answerability.blocking_slots or unresolved)
        questions = [] if ready else self._questions_for_missing(followup_slots, state, max_questions)
        if not ready and not questions and state.followup_rounds >= 1:
            answerability = AnswerabilityDecision(
                decision="answer",
                mode="no_new_followup_questions",
                answer_scope="preliminary",
                blocking_slots=[],
                unresolved_slots=unresolved,
                reason="可追问的问题已问过，继续重复追问的信息价值较低。",
            )
            state.answerability = answerability.to_dict()
            ready = True
            followup_slots = []

        state.phase = "ready_to_answer" if ready else "collecting_info"
        if questions:
            state.followup_rounds += 1
            state.asked_questions.extend(questions)

        return ConsultationDecision(
            state=state,
            ready=ready,
            missing_slots=[] if ready else followup_slots,
            questions=questions,
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
        :return: 返回函数执行结果。
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

    def format_state_for_prompt(self, state: ConsultationState) -> str:
        """将问诊状态格式化为最终回答 Agent 的上下文。

        :param state: 问诊状态。
        :return: 返回函数执行结果。
        """
        lines = [f"主诉: {state.chief_complaint or '未知'}", f"方向: {state.domain}"]
        for slot, value in state.slots.items():
            if value:
                lines.append(f"{slot}: {value}")
        if state.observations:
            lines.append("开放观察:")
            for observation in state.observations[-6:]:
                label = str(observation.get("label") or observation.get("category") or "观察")
                value = str(observation.get("value") or "")
                status = str(observation.get("status") or "unknown")
                if value:
                    lines.append(f"- {label}: {value}（{status}）")
        answerability = state.answerability or {}
        if answerability:
            unresolved = "、".join(answerability.get("unresolved_slots") or []) or "无"
            lines.append(
                f"回答充分性: {answerability.get('decision')} / {answerability.get('mode')} / "
                f"{answerability.get('reason')}"
            )
            lines.append(f"未确认但不再机械阻塞的证据: {unresolved}")
        semantic = state.semantic_extraction or {}
        if semantic:
            lines.append(
                f"语义抽取: {semantic.get('strategy')} / "
                f"applied={semantic.get('applied_fact_keys') or []}"
            )
        if state.temporal_context:
            lines.append(
                "临床安全时间上下文: "
                f"{state.temporal_context.get('scope', 'unclear')} / "
                f"{state.temporal_context.get('resolution_state', 'unknown')} / "
                f"{state.temporal_context.get('text', '') or '未提供时间原文'}"
            )
        return "\n".join(lines)

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
        if semantic_result.temporal_scope == "unclear" and semantic_result.temporal_state in {
            "unknown",
            "unclear",
        }:
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
            if status in {
                ConsultationFactStatus.UNKNOWN,
                ConsultationFactStatus.UNCERTAIN,
            }:
                continue
            if fact.key in {
                ConsultationFactKey.SPECIES,
                ConsultationFactKey.LIFE_STAGE_OR_AGE,
                ConsultationFactKey.WEIGHT,
            } and state.slots.get(key) and not correction:
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
        return {
            "answer_now": semantic_result.intent.answer_now,
            "wants_triage": semantic_result.intent.wants_triage,
            "correction": semantic_result.intent.correction,
            "raw_intent": semantic_result.intent.raw_intent[:120],
        }

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
            if observation.status in {
                ConsultationFactStatus.UNKNOWN,
                ConsultationFactStatus.UNCERTAIN,
            }:
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

    def _build_evidence_profile(self, state: ConsultationState, unresolved_slots: list[str]) -> dict[str, Any]:
        """构建宽泛语义证据维度，避免固定槽位成为唯一门槛。

        :param state: 当前问诊状态。
        :param unresolved_slots: 尚未确认的槽位。
        :return: 返回函数执行结果。
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
        profile["unresolved_slots"] = unresolved_slots
        return profile

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
        :return: 返回函数执行结果。
        """
        known = [slot for slot in slots if state.slots.get(slot)]
        return {"status": "known" if known else "unknown", "slots": known}

    def _questions_for_missing(self, missing: list[str], state: ConsultationState, max_questions: int) -> list[str]:
        """为仍阻塞回答的高价值证据生成追问。

        :param missing: 本轮仍需追问的槽位。
        :param state: 问诊状态。
        :param max_questions: 最多追问数量。
        :return: 返回函数执行结果。
        """
        questions: list[str] = []
        asked = set(state.asked_questions)
        rules = self.rule_repository.consultation_rules()
        for slot in missing:
            question = self._question_for(rules, slot)
            if question not in asked:
                questions.append(question)
            if len(questions) >= max_questions:
                break
        return questions

    def _known_lines(self, state: ConsultationState) -> str:
        """格式化用户已经补充过的事实。

        :param state: 问诊状态。
        :return: 返回函数执行结果。
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
        :return: 返回函数执行结果。
        """
        if domain in rules.domains:
            return rules.domains[domain].required_slots
        return rules.domains.get("general").required_slots if "general" in rules.domains else []

    def _question_for(self, rules: ConsultationRuleSet, slot: str) -> str:
        """读取槽位对应的兜底追问。

        :param rules: 规则集合。
        :param slot: 槽位名称。
        :return: 返回函数执行结果。
        """
        return rules.slots[slot].question if slot in rules.slots else slot

    def _label_for(self, rules: ConsultationRuleSet, slot: str) -> str:
        """返回槽位对应的用户可见标签。

        :param rules: 规则集合。
        :param slot: 标准问诊槽位。
        :return: 返回函数执行结果。
        """
        return rules.slots[slot].label if slot in rules.slots else slot

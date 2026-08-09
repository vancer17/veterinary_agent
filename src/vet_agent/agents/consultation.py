"""
文件：src/vet_agent/agents/consultation.py
作用：维护多轮问诊状态、抽取语义事实，并基于证据充分性决定回答或追问。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from vet_agent.clinical_safety import ClinicalSafetySemanticResult
from vet_agent.repositories import ConsultationRuleSet, RuleRepository, compile_regex
from vet_agent.services import PetContext


SlotValue = str | bool | None

ANSWER_NOW_PATTERNS = (
    "别问",
    "不要再问",
    "不用再问",
    "别再追问",
    "不要追问",
    "直接答",
    "直接回答",
    "直接说",
    "先回答",
    "先给我",
    "先告诉我",
    "给个判断",
    "给出判断",
    "只需要告诉我",
)
NORMAL_PATTERNS = (
    "正常",
    "没变",
    "没有变化",
    "没减少",
    "没有减少",
    "和平时一样",
    "和平常一样",
    "跟平时一样",
    "跟平常一样",
    "和以前一样",
    "照常",
    "还行",
    "可以",
    "不错",
    "很好",
    "挺好",
)
APPETITE_PATTERNS = ("食欲", "胃口", "饭量", "吃饭", "进食", "吃东西", "饮水", "喝水", "主食", "吃完", "清空")
APPETITE_DECLINE_PATTERNS = ("不吃", "食欲差", "食欲下降", "吃得少", "饭量减少", "喝水少")
MENTAL_PATTERNS = ("精神", "活跃", "活动", "反应", "叫它", "叫名字", "互动", "玩", "玩具")
MENTAL_DECLINE_PATTERNS = ("没精神", "精神差", "萎靡", "嗜睡", "趴着不动", "反应差")
VOMITING_NEGATIVE_PATTERNS = ("没有呕吐", "没呕吐", "没有吐", "没吐", "不吐", "未吐", "无呕吐")
VOMITING_POSITIVE_PATTERNS = ("呕吐", "吐了", "一直吐", "干呕")
STOOL_NORMAL_PATTERNS = ("大便正常", "便便正常", "排便正常", "没拉稀", "没有腹泻", "没有拉肚子", "没拉肚子")
STOOL_ABNORMAL_PATTERNS = ("拉稀", "腹泻", "软便", "水样便", "血便", "黑便", "便血")
BREATHING_NORMAL_PATTERNS = ("呼吸正常", "没有咳", "没咳", "不喘", "没有喘", "呼吸没问题")
BREATHING_ABNORMAL_PATTERNS = ("咳", "喘", "呼吸快", "呼吸费力", "张口呼吸", "鼻音", "打喷嚏", "流鼻涕")
PAIN_OR_MOBILITY_PATTERNS = ("疼", "跛", "瘸", "站不稳", "走路异常", "不让碰", "腹部绷紧", "躲开", "缩成一团")
ONSET_PATTERN = re.compile(
    r"(刚刚|今天|昨天|前天|昨晚|早上|中午|晚上|最近|这几天|这两天|这两三天|半小时|一小时|"
    r"\d+\s*(分钟|小时|天|周|个月)|[一二两三四五六七八九十]+个?多?月|[一二两三四五六七八九十]+天)"
)


@dataclass
class ConsultationState:
    chief_complaint: str | None = None
    domain: str = "general"
    phase: str = "collecting_info"
    slots: dict[str, SlotValue] = field(default_factory=dict)
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
    state: ConsultationState
    ready: bool
    missing_slots: list[str]
    questions: list[str]
    answerability: dict[str, Any] = field(default_factory=dict)


class AnswerabilityEvaluator:
    """基于语义证据充分性判断本轮应该回答还是继续追问。"""

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
        if not unresolved_slots:
            return self._answer("slot_complete", [], unresolved_slots, "规则建议关注的信息均已确认。")

        has_minimum = self._has_minimum_context(state)
        if state.user_intent.get("answer_now") and has_minimum:
            return self._answer("user_requested_answer_now", [], unresolved_slots, "用户明确要求先给阶段性判断。")

        if state.followup_rounds >= self.max_followup_rounds and has_minimum:
            return self._answer("max_followup_rounds_reached", [], unresolved_slots, "已达到连续追问轮数上限。")

        if state.followup_rounds >= 1 and self._has_sufficient_semantic_evidence(state):
            return self._answer("sufficient_semantic_evidence", [], unresolved_slots, "已获得足够的主诉、时间、整体状态或领域相关证据。")

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
            for category in ("time_course", "systemic_status", "intake_output", "domain_specific")
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
    """Builds structured consultation context across turns before final advice."""

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
        semantic_result: Any | None = None,
        clinical_safety_semantic: ClinicalSafetySemanticResult | None = None,
        max_questions: int,
    ) -> ConsultationDecision:
        """更新多轮问诊状态并给出本轮回答/追问决策。

        :param previous: 上一轮持久化状态。
        :param user_text: 用户输入文本。
        :param pet_context: 宠物上下文。
        :param semantic_result: LLM 语义抽取结果。
        :param clinical_safety_semantic: 临床安全语义抽取结果。
        :param max_questions: 本轮最多追问数量。
        :return: 返回函数执行结果。
        """
        state = ConsultationState.from_dict(previous)
        text = user_text.strip()
        if text and not state.chief_complaint:
            state.chief_complaint = text[:200]

        state.domain = self._classify_domain(text, state.domain)
        self._prefill_from_pet_context(state, pet_context)
        self._extract_slots(state, text)
        semantic_applied = self._merge_semantic_result(state, semantic_result)
        if not semantic_applied:
            self._extract_semantic_slots(state, text)
        self._merge_clinical_safety_temporal_context(state, clinical_safety_semantic)
        state.user_intent = self._merge_user_intent(self._detect_user_intent(text), semantic_result)

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
            "这样可以避免把普通护理问题误判成疾病，也避免在证据不足时给出不可靠建议。\n\n"
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

    def _classify_domain(self, text: str, previous_domain: str) -> str:
        """根据关键词识别本轮主要问诊领域。

        :param text: 待处理文本。
        :param previous_domain: 上一轮领域。
        :return: 返回函数执行结果。
        """
        rules = self.rule_repository.consultation_rules()
        for domain_rule in sorted(rules.domains.values(), key=lambda item: item.priority):
            if domain_rule.domain == "general":
                continue
            if any(keyword in text for keyword in domain_rule.classifier_keywords):
                return domain_rule.domain
        return previous_domain if previous_domain != "general" else "general"

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

    def _extract_slots(self, state: ConsultationState, text: str) -> None:
        """按规则抽取高置信度槽位事实。

        :param state: 问诊状态。
        :param text: 待处理文本。
        :return: 无返回值。
        """
        rules = self.rule_repository.consultation_rules()
        for slot_rule in rules.slots.values():
            value = self._extract_slot_value(slot_rule.slot_name, slot_rule.extraction_rules, text)
            if value:
                state.slots[slot_rule.slot_name] = value

    def _merge_semantic_result(self, state: ConsultationState, semantic_result: Any | None) -> bool:
        """将 LLM 语义抽取结果合并到问诊状态。

        :param state: 当前问诊状态。
        :param semantic_result: LLM 语义抽取结果。
        :return: 返回函数执行结果。
        """
        metadata = self._semantic_metadata(semantic_result)
        if metadata:
            state.semantic_extraction = metadata
        facts = self._semantic_facts(semantic_result)
        if not facts:
            return False

        rules = self.rule_repository.consultation_rules()
        intent = self._semantic_intent(semantic_result)
        correction = bool(intent.get("correction"))
        applied_keys: list[str] = []
        for fact in facts:
            key = str(self._item_value(fact, "key") or "").strip()
            status = str(self._item_value(fact, "status") or "confirmed").strip().lower()
            value = str(self._item_value(fact, "value") or "").strip()
            if key not in rules.slots:
                continue
            if status in {"unknown", "uncertain"}:
                continue
            if key in {"species", "life_stage_or_age", "weight"} and state.slots.get(key) and not correction:
                continue
            if status == "negative" and not value:
                value = self._negative_slot_value(key)
            if not value:
                continue
            state.slots[key] = value[:160]
            applied_keys.append(key)

        if state.semantic_extraction:
            state.semantic_extraction["applied_fact_keys"] = applied_keys
            state.semantic_extraction["used_as_primary_semantic_path"] = bool(applied_keys)
        return bool(applied_keys)

    def _merge_user_intent(self, rule_intent: dict[str, Any], semantic_result: Any | None) -> dict[str, Any]:
        """合并规则兜底意图与 LLM 语义意图。

        :param rule_intent: 规则兜底识别出的用户意图。
        :param semantic_result: LLM 语义抽取结果。
        :return: 返回函数执行结果。
        """
        semantic_intent = self._semantic_intent(semantic_result)
        return {
            "answer_now": bool(rule_intent.get("answer_now") or semantic_intent.get("answer_now")),
            "wants_triage": bool(semantic_intent.get("wants_triage")),
            "correction": bool(semantic_intent.get("correction")),
            "matched_patterns": list(rule_intent.get("matched_patterns") or []),
            "raw_intent": str(semantic_intent.get("raw_intent") or "")[:120],
        }

    def _semantic_metadata(self, semantic_result: Any | None) -> dict[str, Any]:
        """读取语义抽取结果的 metadata。

        :param semantic_result: LLM 语义抽取结果。
        :return: 返回函数执行结果。
        """
        if semantic_result is None:
            return {}
        to_metadata = getattr(semantic_result, "to_metadata", None)
        if callable(to_metadata):
            metadata = to_metadata()
            return dict(metadata) if isinstance(metadata, dict) else {}
        if isinstance(semantic_result, dict):
            return dict(semantic_result)
        return {}

    def _semantic_facts(self, semantic_result: Any | None) -> list[Any]:
        """读取语义抽取结果中的事实列表。

        :param semantic_result: LLM 语义抽取结果。
        :return: 返回函数执行结果。
        """
        if semantic_result is None:
            return []
        facts = getattr(semantic_result, "facts", None)
        if facts is None and isinstance(semantic_result, dict):
            facts = semantic_result.get("facts")
        return list(facts or [])

    def _semantic_intent(self, semantic_result: Any | None) -> dict[str, Any]:
        """读取语义抽取结果中的用户意图。

        :param semantic_result: LLM 语义抽取结果。
        :return: 返回函数执行结果。
        """
        if semantic_result is None:
            return {}
        intent = getattr(semantic_result, "intent", None)
        if intent is None and isinstance(semantic_result, dict):
            intent = semantic_result.get("intent")
        if intent is None:
            metadata = self._semantic_metadata(semantic_result)
            intent = metadata.get("intent")
        if hasattr(intent, "to_dict"):
            intent = intent.to_dict()
        return dict(intent) if isinstance(intent, dict) else {}

    def _item_value(self, item: Any, key: str) -> Any:
        """从 dataclass、Pydantic 对象或字典中读取字段值。

        :param item: 数据项。
        :param key: 字段名。
        :return: 返回函数执行结果。
        """
        if isinstance(item, dict):
            return item.get(key)
        return getattr(item, key, None)

    def _negative_slot_value(self, key: str) -> str:
        """返回否定事实对应的默认槽位值。

        :param key: 槽位名称。
        :return: 返回函数执行结果。
        """
        defaults = {
            "vomiting": "无呕吐",
            "stool": "未见排便相关异常",
            "breathing": "呼吸未见明显异常",
            "pain_or_mobility": "未见明显疼痛或活动异常",
        }
        return defaults.get(key, "用户明确否认相关异常")

    def _extract_semantic_slots(self, state: ConsultationState, text: str) -> None:
        """抽取更宽泛的自然语言事实，补足固定规则覆盖不足的问题。

        :param state: 当前问诊状态。
        :param text: 待处理文本。
        :return: 无返回值。
        """
        if not text:
            return
        if self._has_any(text, APPETITE_DECLINE_PATTERNS):
            state.slots["appetite"] = "食欲或饮水下降"
        elif self._has_any(text, APPETITE_PATTERNS) and self._has_any(text, NORMAL_PATTERNS):
            state.slots["appetite"] = "食欲/饮水基本正常"

        if self._has_any(text, MENTAL_DECLINE_PATTERNS):
            state.slots["mental_status"] = "精神变差"
        elif ("精神食欲" in text or "精神和食欲" in text or "精神、食欲" in text) and self._has_any(text, NORMAL_PATTERNS):
            state.slots["mental_status"] = "精神基本正常"
            state.slots["appetite"] = "食欲/饮水基本正常"
        elif self._has_any(text, MENTAL_PATTERNS) and self._has_any(text, NORMAL_PATTERNS):
            state.slots["mental_status"] = "精神基本正常"

        if self._has_any(text, VOMITING_NEGATIVE_PATTERNS):
            state.slots["vomiting"] = "无呕吐"
        elif self._has_any(text, VOMITING_POSITIVE_PATTERNS):
            state.slots["vomiting"] = "有呕吐"

        if self._has_any(text, STOOL_NORMAL_PATTERNS):
            state.slots["stool"] = "大便基本正常"
        elif self._has_any(text, STOOL_ABNORMAL_PATTERNS):
            state.slots["stool"] = "有排便相关异常"

        if self._has_any(text, BREATHING_NORMAL_PATTERNS):
            state.slots["breathing"] = "呼吸未见明显异常"
        elif self._has_any(text, BREATHING_ABNORMAL_PATTERNS):
            state.slots["breathing"] = "有呼吸相关表现"

        if self._has_any(text, PAIN_OR_MOBILITY_PATTERNS):
            state.slots["pain_or_mobility"] = "有疼痛或活动相关线索"

        onset_match = ONSET_PATTERN.search(text)
        if onset_match and not state.slots.get("onset"):
            state.slots["onset"] = onset_match.group(0)

        age_match = re.search(r"(\d+|一|二|两|三|四|五|六|七|八|九|十)\s*(岁|个月|月|年)", text)
        if age_match:
            state.slots["life_stage_or_age"] = age_match.group(0)

    def _detect_user_intent(self, text: str) -> dict[str, Any]:
        """识别用户是否明确希望停止追问并先获得阶段性回答。

        :param text: 待处理文本。
        :return: 返回函数执行结果。
        """
        matched = [pattern for pattern in ANSWER_NOW_PATTERNS if pattern in text]
        return {"answer_now": bool(matched), "matched_patterns": matched}

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
        }
        profile["unresolved_slots"] = unresolved_slots
        return profile

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

    def _extract_slot_value(self, slot_name: str, extraction_rules: list[dict[str, Any]], text: str) -> str | None:
        """按单个槽位规则抽取事实。

        :param slot_name: 槽位名称。
        :param extraction_rules: 抽取规则。
        :param text: 待处理文本。
        :return: 返回函数执行结果。
        """
        for rule in extraction_rules:
            match_type = rule.get("match_type")
            if match_type == "keyword":
                patterns = rule.get("patterns", [])
                if any(pattern in text for pattern in patterns):
                    return str(rule.get("value") or patterns[0])
            if match_type == "keyword_value":
                for pattern in rule.get("patterns", []):
                    if pattern in text:
                        return str(rule.get("value") or pattern)
            if match_type in {"regex", "regex_value"}:
                for match in compile_regex(rule["pattern"]).finditer(text):
                    value = str(rule.get("value") or match.group(0))
                    if slot_name == "life_stage_or_age" and not self._valid_age_value(value):
                        continue
                    return value
            if match_type == "text_if_keyword":
                if any(pattern in text for pattern in rule.get("patterns", [])):
                    return text[:120]
            if match_type == "text" and text:
                return text[:160]
        return None

    def _valid_age_value(self, value: str) -> bool:
        """过滤把“两个喷嚏”等量词误抽成年龄的正则结果。

        :param value: 候选年龄文本。
        :return: 返回函数执行结果。
        """
        return "岁" in value or "月" in value or "年" in value

    def _has_any(self, text: str, patterns: tuple[str, ...]) -> bool:
        """判断文本中是否包含任一模式。

        :param text: 待处理文本。
        :param patterns: 候选模式。
        :return: 返回函数执行结果。
        """
        return any(pattern in text for pattern in patterns)

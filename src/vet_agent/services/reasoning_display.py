from __future__ import annotations

from uuid import uuid4

from src.vet_agent.contracts import Evidence, ReasoningDisplay, SafetySignal


SLOT_LABELS = {
    "species": "物种",
    "life_stage_or_age": "年龄/生命阶段",
    "weight": "体重",
    "onset": "起病时间",
    "mental_status": "精神状态",
    "appetite": "食欲饮水",
    "vomiting": "呕吐情况",
    "stool": "大便情况",
    "breathing": "呼吸情况",
    "pain_or_mobility": "疼痛/活动",
    "behavior_context": "行为场景",
    "current_food": "当前饮食",
    "symptom_detail": "症状补充",
}


class ReasoningDisplayBuilder:
    """Builds safe user-visible reasoning summaries, not hidden chain-of-thought."""

    def user_answer_evidence(self, consultation_state: dict | None) -> list[Evidence]:
        slots = dict((consultation_state or {}).get("slots") or {})
        evidence: list[Evidence] = []
        for slot, value in slots.items():
            if value in (None, "", False):
                continue
            label = SLOT_LABELS.get(slot, slot)
            evidence.append(
                Evidence(
                    source="用户回答",
                    detail=f"{label}: {value}",
                    metadata={"slot": slot},
                )
            )
        chief_complaint = (consultation_state or {}).get("chief_complaint")
        if chief_complaint:
            evidence.insert(
                0,
                Evidence(
                    source="用户主诉",
                    detail=str(chief_complaint)[:160],
                    metadata={"field": "chief_complaint"},
                ),
            )
        return evidence

    def build_turn_display(
        self,
        *,
        status: str,
        segment_id: str | None,
        evidence: list[Evidence],
        consultation_state: dict | None = None,
        missing_slots: list[str] | None = None,
        safety_signals: list[SafetySignal] | None = None,
    ) -> ReasoningDisplay:
        title = "本轮思考过程"
        if status == "requires_followup":
            text = self._followup_text(consultation_state, missing_slots or [], evidence)
        elif status in {"safety_escalated", "blocked"}:
            text = self._safety_text(status, safety_signals or [])
        else:
            text = self._completed_text(consultation_state, evidence, safety_signals or [])

        return ReasoningDisplay(
            projection_id=f"rdp_{uuid4().hex}",
            segment_id=segment_id,
            title=title,
            text=text,
            metadata={
                "kind": "user_visible_diagnostic_evidence",
                "evidence_count": len(evidence),
                "public_citation_count": sum(1 for item in evidence if item.public_citation),
            },
        )

    def build_multi_task_display(
        self,
        *,
        task_summaries: list[dict],
        evidence: list[Evidence],
        status: str,
    ) -> ReasoningDisplay:
        task_text = "、".join(
            f"{item.get('title', '咨询任务')}({item.get('status', 'unknown')})"
            for item in task_summaries[:5]
        )
        text = (
            f"我先把本轮输入拆成 {len(task_summaries)} 个任务：{task_text}。"
            "每个任务分别核对用户回答、系统已知宠物资料、安全信号和可公开依据；"
            "信息不足的任务只补充追问，信息足够的任务才给阶段性建议。"
        )
        return ReasoningDisplay(
            projection_id=f"rdp_{uuid4().hex}",
            segment_id=None,
            title="本轮思考过程",
            text=text,
            metadata={
                "kind": "user_visible_multi_task_routing",
                "task_count": len(task_summaries),
                "status": status,
                "evidence_count": len(evidence),
            },
        )

    def references_from_evidence(self, evidence: list[Evidence]) -> list[dict]:
        references = []
        seen: set[tuple[str, str | None]] = set()
        for item in evidence:
            if not item.public_citation:
                continue
            metadata = item.metadata or {}
            url = metadata.get("source_url") or metadata.get("url")
            key = (item.source, url)
            if key in seen:
                continue
            seen.add(key)
            references.append(
                {
                    "source": item.source,
                    "title": metadata.get("title") or item.source,
                    "url": url,
                    "type": metadata.get("type") or "evidence",
                }
            )
        return references

    def _followup_text(
        self,
        consultation_state: dict | None,
        missing_slots: list[str],
        evidence: list[Evidence],
    ) -> str:
        known = self._known_user_answers(consultation_state, limit=5)
        missing = "、".join(SLOT_LABELS.get(slot, slot) for slot in missing_slots[:5]) or "关键问诊信息"
        basis = self._evidence_source_summary(evidence)
        return (
            "我先核对了本轮主诉、系统已知宠物资料和安全风险。"
            f"目前已知信息包括{known or '少量主诉信息'}；还缺少{missing}。"
            f"可用依据包括{basis or '用户当前描述'}。"
            "因此本轮先补齐问诊信息，避免在证据不足时直接下结论。"
        )

    def _completed_text(
        self,
        consultation_state: dict | None,
        evidence: list[Evidence],
        safety_signals: list[SafetySignal],
    ) -> str:
        known = self._known_user_answers(consultation_state, limit=6)
        basis = self._evidence_source_summary(evidence)
        safety = self._safety_signal_summary(safety_signals)
        return (
            f"我先做安全分诊{safety}，再结合你补充的问诊信息"
            f"{known or '和当前主诉'}。"
            f"本轮可展示依据包括{basis or '用户回答'}。"
            "基于这些信息，回复只给阶段性方向、观察要点和就医触发条件，不替代线下兽医诊断。"
        )

    def _safety_text(self, status: str, safety_signals: list[SafetySignal]) -> str:
        signal_text = self._safety_signal_summary(safety_signals)
        action = "阻断了本轮请求" if status == "blocked" else "优先升级为线下兽医处理"
        return (
            f"我先进行安全分诊{signal_text}，判断继续在线推理可能延误处理或超出可安全回答范围，"
            f"因此{action}，并保留明确的线下兽医兜底建议。"
        )

    def _known_user_answers(self, consultation_state: dict | None, *, limit: int) -> str:
        if not consultation_state:
            return ""
        parts = []
        for slot, value in dict(consultation_state.get("slots") or {}).items():
            if value in (None, "", False):
                continue
            label = SLOT_LABELS.get(slot, slot)
            parts.append(f"{label}={value}")
            if len(parts) >= limit:
                break
        return "、".join(parts)

    def _evidence_source_summary(self, evidence: list[Evidence]) -> str:
        public_sources: list[str] = []
        internal_count = 0
        user_count = 0
        for item in evidence:
            if item.source.startswith("用户"):
                user_count += 1
                continue
            if item.public_citation:
                public_sources.append(item.source)
            else:
                internal_count += 1
        unique_sources = self._unique(public_sources)[:4]
        parts: list[str] = []
        if user_count:
            parts.append("用户回答")
        parts.extend(unique_sources)
        if internal_count:
            parts.append("内部授权知识库摘要")
        return "、".join(parts)

    def _safety_signal_summary(self, safety_signals: list[SafetySignal]) -> str:
        if not safety_signals:
            return "，未发现需要立刻中断普通问诊流程的安全信号"
        labels = []
        for signal in safety_signals[:3]:
            term_text = f"({', '.join(signal.matched_terms[:3])})" if signal.matched_terms else ""
            labels.append(f"{signal.code}{term_text}")
        return f"，发现安全信号：{'、'.join(labels)}"

    def _unique(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

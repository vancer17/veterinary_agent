"""
文件：src/vet_agent/memory/context_builder.py
作用：将结构化记忆读取结果编译为回复生成 Agent 可消费的提示词上下文。
范围：只做分层展示、数量统计和字符预算裁剪，不判断疾病、不改变问诊状态、不生成长期事实。
说明：本文件用于避免 ResponseComposer 直接消费松散 dict 记忆结构，保持权威事实、会话窗口、episode 与 Mem0 线索边界清晰。
"""

from __future__ import annotations

from collections.abc import Iterable

from vet_agent import Evidence, Settings

from .models import (
    AuthoritativeMemoryFact,
    MemoryPromptContext,
    MemoryReadBundle,
    PetMemoryEpisode,
    SemanticRecollection,
    SessionMemoryTurn,
)


class MemoryContextBuilder:
    """编译回复生成 Agent 使用的记忆上下文。

    :return: 无返回值。
    """

    def __init__(self, settings: Settings) -> None:
        """初始化记忆上下文编译器。

        :param settings: 当前运行环境配置。
        :return: 无返回值。
        """
        self.settings = settings

    def build(self, bundle: MemoryReadBundle) -> MemoryPromptContext:
        """根据结构化记忆读取结果生成提示词上下文。

        :param bundle: 结构化记忆读取结果。
        :return: 返回回复生成 Agent 可消费的记忆上下文。
        """
        sections = [
            self._fact_section(bundle.authoritative_facts),
            self._session_turn_section(bundle.recent_session_turns),
            self._episode_section(bundle.recent_pet_episodes),
            self._semantic_section(bundle.semantic_recollections),
            self._consultation_state_section(bundle.consultation_state),
        ]
        prompt_text = self._fit_budget("\n\n".join(section for section in sections if section.strip()))
        if not prompt_text.strip():
            prompt_text = "暂无可用历史记忆。"
        evidence = (
            Evidence(
                source="结构化记忆读取",
                detail=(
                    "已按权威事实、当前会话窗口、宠物历史 episode 与语义记忆投影分层编译上下文。"
                ),
                metadata=bundle.to_metadata(),
            ),
        )
        return MemoryPromptContext(
            prompt_text=prompt_text,
            evidence=evidence,
            metadata={
                "prompt_chars": len(prompt_text),
                "audit": bundle.to_metadata(),
            },
        )

    def _fact_section(self, facts: tuple[AuthoritativeMemoryFact, ...]) -> str:
        """编译 PostgreSQL 权威长期事实分区。

        :param facts: 权威长期事实列表。
        :return: 返回提示词分区文本。
        """
        if not facts:
            return "已验证长期事实：暂无。"
        lines = [
            f"- {fact.fact_type}.{fact.fact_key}: {fact.fact_value}（置信度 {fact.confidence:.2f}）"
            for fact in facts
        ]
        return "已验证长期事实：\n" + "\n".join(lines)

    def _session_turn_section(self, turns: tuple[SessionMemoryTurn, ...]) -> str:
        """编译当前 session 滑动窗口分区。

        :param turns: 当前 session 最近回合列表。
        :return: 返回提示词分区文本。
        """
        if not turns:
            return "当前会话上下文：暂无。"
        chronological_turns = tuple(reversed(turns))
        lines = [
            f"- 用户：{turn.user_text[:240]}；助手摘要：{turn.summary[:320]}"
            for turn in chronological_turns
        ]
        return "当前会话上下文：\n" + "\n".join(lines)

    def _episode_section(self, episodes: tuple[PetMemoryEpisode, ...]) -> str:
        """编译宠物中期历史 episode 分区。

        :param episodes: 宠物中期历史 episode 列表。
        :return: 返回提示词分区文本。
        """
        if not episodes:
            return "宠物历史事件：暂无。"
        lines = [f"- {episode.title}: {episode.summary[:360]}" for episode in episodes]
        return "宠物历史事件：\n" + "\n".join(lines)

    def _semantic_section(self, recollections: tuple[SemanticRecollection, ...]) -> str:
        """编译 Mem0 语义记忆投影分区。

        :param recollections: Mem0 语义召回结果列表。
        :return: 返回提示词分区文本。
        """
        if not recollections:
            return "相关历史线索：暂无。"
        lines = [
            f"- {item.content[:360]}"
            for item in recollections
        ]
        return "相关历史线索（语义召回，仅作为线索，不得覆盖已验证事实）：\n" + "\n".join(lines)

    def _consultation_state_section(self, state: dict[str, object]) -> str:
        """编译当前活跃问诊状态分区。

        :param state: 当前 session 默认问诊状态。
        :return: 返回提示词分区文本。
        """
        if not state:
            return "当前问诊状态：暂无活跃状态。"
        return f"当前问诊状态：\n{state}"

    def _fit_budget(self, text: str) -> str:
        """按字符预算裁剪记忆提示词。

        :param text: 原始提示词文本。
        :return: 返回不超过配置字符预算的提示词文本。
        """
        limit = self.settings.memory_prompt_max_chars
        if len(text) <= limit:
            return text
        return self._line_budget(text.splitlines(), limit)

    def _line_budget(self, lines: Iterable[str], limit: int) -> str:
        """按行保留提示词内容直到达到字符预算。

        :param lines: 原始提示词行迭代器。
        :param limit: 字符预算上限。
        :return: 返回预算内的提示词文本。
        """
        selected: list[str] = []
        size = 0
        for line in lines:
            next_size = size + len(line) + 1
            if next_size > limit:
                selected.append("……以上记忆上下文已按字符预算截断。")
                break
            selected.append(line)
            size = next_size
        return "\n".join(selected)

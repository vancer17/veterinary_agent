"""
文件：src/vet_agent/memory/context_builder.py
作用：将结构化记忆读取结果编译为稳定的记忆提示词上下文。
范围：只做记忆分层展示、数量统计和字符预算裁剪，不判断疾病、不改变
      问诊状态、不生成长期事实，也不拼装回复生成阶段的其他上下文。
说明：最终回复上下文由 response_generation 包继续编译；本文件仅保持权威
      事实、会话窗口、episode 与 Mem0 线索之间的来源边界。
"""

from __future__ import annotations

from collections.abc import Iterable

from vet_agent import Evidence, Settings

from .models import (
    AuthoritativeMemoryFact,
    MemoryContextSection,
    MemoryPromptContext,
    MemoryReadBundle,
    PetMemoryEpisode,
    SemanticRecollection,
    SessionMemoryTurn,
)


class MemoryContextBuilder:
    """编译记忆读取链路输出的提示词上下文。

    :return: 无返回值。
    """

    def __init__(self, settings: Settings) -> None:
        """初始化记忆上下文编译器。

        :param settings: 当前运行环境配置。
        :return: 无返回值。
        """
        self.settings = settings

    def build(self, bundle: MemoryReadBundle) -> MemoryPromptContext:
        """根据结构化记忆读取结果生成纯记忆提示词上下文。

        :param bundle: 结构化记忆读取结果。
        :return: 返回供回复生成上下文编译器消费的记忆上下文。
        """
        candidate_sections = (
            self._fact_section(bundle.authoritative_facts),
            self._session_turn_section(bundle.recent_session_turns),
            self._episode_section(bundle.recent_pet_episodes),
            self._semantic_section(bundle.semantic_recollections),
        )
        sections = self._fit_sections_budget(
            tuple(section for section in candidate_sections if section is not None)
        )
        prompt_text = self._render_sections(sections) or "暂无可用历史记忆。"
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
            sections=sections,
            evidence=evidence,
            metadata={
                "prompt_chars": len(prompt_text),
                "sections": [section.to_metadata() for section in sections],
                "audit": bundle.to_metadata(),
            },
        )

    def _fact_section(
        self,
        facts: tuple[AuthoritativeMemoryFact, ...],
    ) -> MemoryContextSection | None:
        """编译 PostgreSQL 权威长期事实分区。

        :param facts: 权威长期事实列表。
        :return: 返回宠物范围权威事实分区；无事实时返回 None。
        """
        if not facts:
            return None
        lines = [
            f"- {fact.fact_type}.{fact.fact_key}: {fact.fact_value}"
            for fact in facts
            if fact.fact_value.strip()
        ]
        if not lines:
            return None
        return MemoryContextSection(
            scope="pet",
            authority="authoritative",
            content="\n".join(lines),
            source_label="已验证长期事实",
        )

    def _session_turn_section(
        self,
        turns: tuple[SessionMemoryTurn, ...],
    ) -> MemoryContextSection | None:
        """编译当前 session 滑动窗口分区。

        :param turns: 当前 session 最近回合列表。
        :return: 返回 session 共享参考分区；无有效摘要时返回 None。
        """
        if not turns:
            return None
        chronological_turns = tuple(reversed(turns))
        lines = [
            f"- 助手摘要：{turn.summary[:320]}"
            for turn in chronological_turns
            if turn.summary.strip()
        ]
        if not lines:
            return None
        return MemoryContextSection(
            scope="session_shared",
            authority="conversational",
            content="\n".join(lines),
            source_label="当前会话参考",
        )

    def _episode_section(
        self,
        episodes: tuple[PetMemoryEpisode, ...],
    ) -> MemoryContextSection | None:
        """编译宠物中期历史 episode 分区。

        :param episodes: 宠物中期历史 episode 列表。
        :return: 返回宠物范围历史事件分区；无有效事件时返回 None。
        """
        if not episodes:
            return None
        lines = [
            f"- {episode.title}: {episode.summary[:360]}"
            for episode in episodes
            if episode.title.strip() and episode.summary.strip()
        ]
        if not lines:
            return None
        return MemoryContextSection(
            scope="pet",
            authority="episode",
            content="\n".join(lines),
            source_label="宠物历史事件",
        )

    def _semantic_section(
        self,
        recollections: tuple[SemanticRecollection, ...],
    ) -> MemoryContextSection | None:
        """编译 Mem0 语义记忆投影分区。

        :param recollections: Mem0 语义召回结果列表。
        :return: 返回宠物范围语义线索分区；无有效线索时返回 None。
        """
        if not recollections:
            return None
        lines = [
            f"- {item.content[:360]}"
            for item in recollections
            if item.content.strip()
        ]
        if not lines:
            return None
        return MemoryContextSection(
            scope="pet",
            authority="semantic_hint",
            content="\n".join(lines),
            source_label="历史语义线索",
        )

    def _fit_sections_budget(
        self,
        sections: tuple[MemoryContextSection, ...],
    ) -> tuple[MemoryContextSection, ...]:
        """按记忆字符预算裁剪结构化记忆分区。

        :param sections: 已按来源边界划分的候选记忆分区。
        :return: 返回不超过记忆字符预算的结构化记忆分区。
        """
        selected: list[MemoryContextSection] = []
        used_chars = 0
        for section in sections:
            section_overhead = len(section.source_label) + 3
            remaining_chars = self.settings.memory_prompt_max_chars - used_chars - section_overhead
            if remaining_chars < 1:
                break
            content = self._fit_budget(section.content, remaining_chars)
            if not content.strip():
                continue
            selected.append(
                MemoryContextSection(
                    scope=section.scope,
                    authority=section.authority,
                    content=content,
                    source_label=section.source_label,
                    task_key=section.task_key,
                )
            )
            used_chars += section_overhead + len(content) + 2
        return tuple(selected)

    def _render_sections(self, sections: tuple[MemoryContextSection, ...]) -> str:
        """将结构化记忆分区渲染为兼容旧调用方的提示词文本。

        :param sections: 已完成预算裁剪的结构化记忆分区。
        :return: 返回仅包含模型可见业务内容的兼容提示词文本。
        """
        return "\n\n".join(
            f"{section.source_label}:\n{section.content}"
            for section in sections
            if section.content.strip()
        )

    def _fit_budget(self, text: str, limit: int) -> str:
        """按字符预算裁剪记忆提示词。

        :param text: 原始提示词文本。
        :param limit: 当前分区允许使用的字符预算。
        :return: 返回不超过配置字符预算的提示词文本。
        """
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

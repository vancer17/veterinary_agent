"""
=============================================================================
文件：src/vet_agent/response_generation/projections.py
作用：定义回复生成阶段的模型可见上下文投影。
范围：只承载上游已完成裁决、过滤和归属后的只读字段；不访问数据库，
      不调用模型，不执行语义理解、任务路由、临床安全裁决或记忆治理。
说明：本文件用于隔离模型提示词输入与 response metadata/trace 审计字段，
      避免把 policy payload、内部标识、置信度和检索分数暴露给回复模型。
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vet_agent.memory import MemoryContextSection, MemoryPromptContext
from vet_agent.repositories import KnowledgeHit

from .errors import ResponseGenerationContractError


@dataclass(frozen=True)
class ClinicalSafetyGenerationContext:
    """表示临床安全裁决对回复生成阶段可见的摘要。

    :param action: 上游临床安全策略给出的有限动作。
    :param allow: 上游临床安全策略是否允许普通问诊链路继续。
    :param message: 上游策略返回的面向生成阶段的裁决说明。
    :param reasons: 上游策略返回的可展示裁决原因。
    :return: 无返回值；该对象只呈现上游裁决，不允许回复生成阶段重判安全动作。
    """

    action: str
    allow: bool
    message: str
    reasons: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AnswerabilityGenerationContext:
    """表示回答充分性策略对回复生成阶段可见的摘要。

    :param decision: 上游回答充分性策略动作，回复生成阶段仅接受 answer。
    :param answer_scope: 当前允许生成的阶段性回答范围。
    :param reason: 上游策略给出的进入回答分支原因。
    :param unresolved_slots: 尚未确认但不阻塞阶段性回答的信息。
    :return: 无返回值；该对象只表达回答边界，不执行 answer/ask 二次裁决。
    """

    decision: str
    answer_scope: str
    reason: str
    unresolved_slots: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ConsultationGenerationContext:
    """表示当前任务问诊状态对回复生成阶段可见的事实投影。

    :param chief_complaint: 当前任务主诉摘要。
    :param domain: 当前任务域标签，仅用于展示上游已完成归属。
    :param phase: 当前问诊阶段。
    :param slots: 当前任务核心槽位的模型可见视图。
    :param working_facts: 当前任务结构化事实。
    :param observations: 当前任务开放观察。
    :param asked_questions: 当前任务已问问题摘要。
    :param temporal_context: 上游临床安全语义提供的时间上下文摘要。
    :return: 无返回值；该对象不决定是否追问或回答。
    """

    chief_complaint: str
    domain: str
    phase: str
    slots: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    working_facts: tuple[str, ...] = field(default_factory=tuple)
    observations: tuple[str, ...] = field(default_factory=tuple)
    asked_questions: tuple[str, ...] = field(default_factory=tuple)
    temporal_context: tuple[tuple[str, str], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class MemoryGenerationContext:
    """表示记忆读取结果对回复生成阶段可见的结构化分区。

    :param sections: 已按范围、任务归属和可信等级过滤后的记忆分区。
    :return: 无返回值；该对象只承载历史参考，不解决事实冲突。
    """

    sections: tuple[MemoryContextSection, ...] = field(default_factory=tuple)

    @classmethod
    def from_prompt_context(
        cls,
        memory_context: MemoryPromptContext,
        *,
        task_key: str,
    ) -> "MemoryGenerationContext":
        """从记忆上下文构造当前任务可见的记忆投影。

        :param memory_context: 记忆读取链路输出的提示词上下文。
        :param task_key: 当前任务稳定键；仅用于过滤已显式归属的任务级记忆。
        :return: 返回当前任务可消费的记忆生成上下文。
        """
        sections = tuple(
            section
            for section in memory_context.sections
            if _section_visible_for_task(section, task_key=task_key)
        )
        if sections:
            return cls(sections=sections)
        legacy_text = memory_context.prompt_text.strip()
        if not legacy_text:
            return cls()
        return cls(
            sections=(
                MemoryContextSection(
                    scope="session_shared",
                    authority="conversational",
                    content=legacy_text,
                    source_label="当前会话参考",
                ),
            )
        )


@dataclass(frozen=True)
class AnswerEvidenceContext:
    """表示回答相关 RAG 对回复生成阶段可见的一条已验证证据。

    :param title: 证据标题。
    :param summary: 证据摘要或正文。
    :param source_label: 模型可见的安全来源标签。
    :return: 无返回值；证据 ID、检索分数和内部元数据只进入审计，不进入提示词。
    """

    title: str
    summary: str
    source_label: str

    @classmethod
    def from_hit(cls, hit: KnowledgeHit, *, index: int) -> "AnswerEvidenceContext":
        """从回答 RAG 命中构造模型可见证据投影。

        :param hit: 已由回答 RAG 层召回并校验的知识命中。
        :param index: 当前命中序号，用于错误排障。
        :return: 返回模型可见的回答证据投影。
        :raises ResponseGenerationContractError: 命中缺少模型生成所需字段时抛出。
        """
        title = hit.title.strip()
        summary = hit.summary.strip()
        source_label = hit.source.strip()
        if not title or not summary or not source_label:
            raise ResponseGenerationContractError(
                "response generation answer evidence is incomplete",
                details={
                    "index": index,
                    "has_title": bool(title),
                    "has_summary": bool(summary),
                    "has_source": bool(source_label),
                },
            )
        return cls(title=title, summary=summary, source_label=source_label)


def _section_visible_for_task(section: MemoryContextSection, *, task_key: str) -> bool:
    """判断记忆分区是否允许进入当前任务回复生成上下文。

    :param section: 待检查的结构化记忆分区。
    :param task_key: 当前任务稳定键。
    :return: 范围匹配时返回 True；不根据内容猜测任务归属。
    """
    if section.scope in {"pet", "session_shared"}:
        return True
    if section.scope == "task":
        return section.task_key == task_key
    return False


def stringify_generation_value(value: Any) -> str:
    """将生成投影中的业务值转换为模型可读文本。

    :param value: 已经通过上游投影的业务值。
    :return: 返回稳定的模型可见文本。
    """
    if isinstance(value, str):
        return value
    return str(value)

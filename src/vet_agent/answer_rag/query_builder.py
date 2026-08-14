"""
=============================================================================
文件：src/vet_agent/answer_rag/query_builder.py
作用：构造回答相关 RAG 的结构化知识检索查询。
范围：只消费问诊状态、回答充分性裁决、问诊语义、可信宠物摘要和当前任务文本；
      不扫描关键词、不抽取事实、不决定是否回答、不生成自然语言回复。
说明：查询构造是确定性数据整理，目的是让 pgvector 召回更贴近本轮已知证据、
      任务域、风险分层依据和可执行护理边界。
=============================================================================
"""

from __future__ import annotations

import json
from typing import Any

from .errors import AnswerRagContractError
from .models import AnswerRagRequest


class AnswerRagQueryBuilder:
    """构造回答 RAG 检索查询的确定性组件。

    :return: 无返回值；该组件位于 AnswerRagService 内部数据整理阶段。
    """

    def build(self, request: AnswerRagRequest) -> str:
        """构造用于召回答案知识的结构化查询文本。

        :param request: 回答 RAG 结构化请求。
        :return: 返回检索查询文本。
        :raises AnswerRagContractError: 问诊状态中的结构化字段类型不符合契约时抛出。
        """
        state = dict(request.consultation_state or {})
        slots = state.get("slots")
        if slots is not None and not isinstance(slots, dict):
            raise AnswerRagContractError(
                "answer RAG consultation state slots must be an object",
                details={"value_type": type(slots).__name__},
            )
        domain = str(request.task_domain or state.get("domain") or "general").strip() or "general"
        payload = {
            "user_text": request.user_text.strip(),
            "pet_context_summary": request.pet_context_summary.strip(),
            "domain": domain,
            "known_slots": dict(slots or {}),
            "evidence_profile": self._limited_dict(state.get("evidence_profile")),
            "semantic_extraction": self._limited_dict(request.semantic_extraction)
            or self._limited_dict(state.get("semantic_extraction")),
            "answerability": self._limited_dict(request.answerability),
            "retrieval_goal": (
                "检索与阶段性回答、分诊依据、常见鉴别方向、居家观察、"
                "用药边界和线下就医红旗相关的兽医知识。"
            ),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _limited_dict(self, value: Any) -> dict[str, Any]:
        """归一并裁剪可进入检索查询的结构化字典。

        :param value: 候选字典。
        :return: 返回最多保留关键字段的结构化字典。
        :raises AnswerRagContractError: 候选值不是对象且不是缺省值时抛出。
        """
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise AnswerRagContractError(
                "answer RAG structured query field must be an object",
                details={"value_type": type(value).__name__},
            )
        allowed_keys = {
            "decision",
            "mode",
            "answer_scope",
            "blocking_slots",
            "unresolved_slots",
            "reason",
            "known_categories",
            "known_category_count",
            "minimum_context",
            "advisory_slots",
            "trusted",
            "strategy",
            "applied_fact_keys",
            "applied_observation_count",
            "facts",
            "observations",
            "intent",
            "chief_complaint",
            "domain",
            "slots",
        }
        return {str(key): item for key, item in dict(value or {}).items() if str(key) in allowed_keys}

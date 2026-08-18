"""
=============================================================================
文件：src/vet_agent/followup_rag/query_builder.py
作用：构造追问相关 RAG 的结构化知识检索查询。
范围：只消费问诊状态、OPA 回答充分性裁决、可信宠物摘要和当前任务文本；
      不扫描关键词、不抽取事实、不生成追问问题、不决定是否追问。
说明：查询构造是确定性数据整理，目的是让 pgvector 召回更贴近本轮
      blocking slots 和风险分层问题，避免旧版自然语言拼接分散在编排器中。
=============================================================================
"""

from __future__ import annotations

import json
from typing import Any

from .models import FollowupRagRequest


class FollowupRagQueryBuilder:
    """构造追问 RAG 检索查询的确定性组件。

    :return: 无返回值；该组件位于 FollowupRagService 内部数据整理阶段。
    """

    def build(self, request: FollowupRagRequest) -> str:
        """构造用于召回追问知识的结构化查询文本。

        :param request: 追问 RAG 结构化请求。
        :return: 返回检索查询文本。
        """
        state = dict(request.consultation_state or {})
        payload = {
            "user_text": request.user_text.strip(),
            "pet_context_summary": request.pet_context_summary.strip(),
            "domain": str(state.get("domain") or "general"),
            "known_slots": dict(state.get("slots") or {}),
            "evidence_profile": self._limited_dict(state.get("evidence_profile")),
            "semantic_extraction": self._limited_dict(state.get("semantic_extraction")),
            "missing_slots": list(request.missing_slots),
            "answerability": self._limited_dict(request.answerability),
            "retrieval_goal": "检索与风险分层、鉴别观察点、病症特异追问和下一步问诊要点相关的兽医知识。",
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _limited_dict(self, value: Any) -> dict[str, Any]:
        """归一并裁剪可进入检索查询的结构化字典。

        :param value: 候选字典。
        :return: 返回最多保留关键字段的结构化字典。
        """
        if not isinstance(value, dict):
            return {}
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
        }
        return {str(key): value for key, value in value.items() if str(key) in allowed_keys}

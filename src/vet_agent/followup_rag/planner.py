"""
=============================================================================
文件：src/vet_agent/followup_rag/planner.py
作用：使用 LiteLLM response_format 生成结构化追问计划候选。
范围：位于追问 RAG 知识召回之后、业务契约校验之前；只生成问题候选，
      不决定是否追问、不访问数据库、不更新问诊状态、不提供本地规则回退。
说明：模型必须返回 Pydantic 可校验结构。模型不可用、schema 非法或输出
      无法通过结构化调用时直接 Fail Fast，不再使用手写 JSON 截取。
=============================================================================
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vet_agent.runtime import QwenClient

from .errors import FollowupRagDependencyError
from .models import FollowupRagRequest, FollowupRagRetrievalResult


class FollowupQuestionItem(BaseModel):
    """定义结构化模型输出中的单个追问问题。

    :return: 无返回值；该模型用于 LiteLLM response_format 的 JSON Schema。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    slot: str = Field(min_length=1, description="问题对应的 missing slot，必须由业务层校验为本轮允许槽位。")
    question: str = Field(min_length=4, description="面向宠物主人的一句追问。")
    reason: str = Field(min_length=1, description="该问题影响分诊或下一步建议的原因。")
    evidence_chunk_ids: list[str] = Field(
        min_length=1,
        max_length=4,
        description="引用的本轮 RAG 证据标识，必须由业务层校验存在。",
    )
    priority: int = Field(default=100, ge=1, le=100, description="问题优先级，数值越小越靠前。")


class FollowupQuestionPlannerOutput(BaseModel):
    """定义结构化追问规划模型输出契约。

    :return: 无返回值；该模型是 FollowupQuestionPlanner 的返回结构。
    """

    model_config = ConfigDict(extra="forbid")

    questions: list[FollowupQuestionItem] = Field(
        min_length=1,
        max_length=5,
        description="本轮追问问题候选列表。",
    )
    rationale: str = Field(default="", description="追问规划摘要；不得包含诊断或治疗方案。")


class LiteLlmFollowupQuestionPlanner:
    """通过 LiteLLM response_format 生成追问问题候选。

    :return: 无返回值；生产路径使用该实现替代旧版手写 JSON 解析 Agent。
    """

    def __init__(self, qwen: QwenClient) -> None:
        """初始化结构化追问规划器。

        :param qwen: LiteLLM 兼容模型客户端。
        :return: 无返回值。
        """
        self.qwen = qwen

    async def generate(
        self,
        *,
        request: FollowupRagRequest,
        retrieval: FollowupRagRetrievalResult,
    ) -> FollowupQuestionPlannerOutput:
        """基于已审核知识证据生成结构化追问计划候选。

        :param request: 追问 RAG 结构化请求。
        :param retrieval: 本轮已审核知识召回结果。
        :return: 返回结构化追问计划候选。
        :raises FollowupRagDependencyError: 模型不可用或结构化调用失败时抛出。
        """
        if not self.qwen.available:
            raise FollowupRagDependencyError(
                "followup RAG structured question planner is unavailable",
                details={"reason": "llm_unavailable"},
            )
        try:
            return await self.qwen.chat_structured(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是兽医多 Agent 系统中的追问 RAG 规划器。"
                            "你只能基于已审核知识证据生成追问问题，不能诊断，不能治疗，不能给药。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": self._prompt(request, retrieval),
                    },
                ],
                response_model=FollowupQuestionPlannerOutput,
                model=request.model,
                temperature=0.0,
            )
        except Exception as exc:
            raise FollowupRagDependencyError(
                "followup RAG structured question planning failed",
                details={"error_type": type(exc).__name__},
            ) from exc

    def is_ready(self) -> bool:
        """检查结构化追问规划器是否具备调用条件。

        :return: LiteLLM 配置完整时返回 True。
        """
        return self.qwen.available

    def _prompt(
        self,
        request: FollowupRagRequest,
        retrieval: FollowupRagRetrievalResult,
    ) -> str:
        """构造结构化追问规划提示词。

        :param request: 追问 RAG 结构化请求。
        :param retrieval: 本轮已审核知识召回结果。
        :return: 返回 JSON 格式提示词。
        """
        evidence = [
            {
                "evidence_id": str(dict(hit.metadata or {}).get("chunk_id") or f"followup_hit_{index + 1}"),
                "title": hit.title,
                "summary": hit.summary[:800],
                "score": hit.score,
                "source": hit.source,
                "metadata": self._safe_metadata(hit.metadata),
            }
            for index, hit in enumerate(retrieval.hits)
        ]
        return json.dumps(
            {
                "task": "基于已审核 RAG 证据生成结构化追问计划。",
                "rules": [
                    "只输出追问问题，不给诊断结论、治疗方案或用药建议。",
                    "slot 必须来自 missing_slots。",
                    "每个问题必须引用 evidence 中至少一个 evidence_id。",
                    "问题应优先帮助区分风险等级、就医紧迫度或下一步问诊方向。",
                    "不要重复询问 consultation_state 中已经明确的信息。",
                    "最多输出 max_questions 个问题。",
                ],
                "max_questions": request.max_questions,
                "missing_slots": request.missing_slots,
                "answerability": request.answerability,
                "consultation_state": request.consultation_state,
                "pet_context_summary": request.pet_context_summary,
                "user_text": request.user_text,
                "evidence": evidence,
            },
            ensure_ascii=False,
        )

    def _safe_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """裁剪可进入模型提示词的知识元数据。

        :param metadata: 原始知识元数据。
        :return: 返回追问规划需要的安全元数据。
        """
        allowed_keys = {
            "chunk_id",
            "chunk_type",
            "field",
            "field_label",
            "condition_key",
            "condition_name",
            "condition_system",
            "domain",
            "species",
        }
        return {str(key): value for key, value in dict(metadata or {}).items() if str(key) in allowed_keys}

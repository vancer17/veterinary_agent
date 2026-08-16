"""
=============================================================================
文件：src/vet_agent/memory_extraction/service.py
作用：使用 LiteLLM response_format 抽取长期记忆候选提议。
范围：位于回合输出安全复核之后、长期事实写入之前；本层只负责结构化
      候选抽取与显式失败语义，不执行写入裁决、不访问数据库、不做关键词回退。
说明：候选抽取只读取显式来源片段和已完成回合文本；任务边界应由上游编排
      明确传入，不得通过 joined_text 自动混合多任务事实。
=============================================================================
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from vet_agent import AgentTurnResponse, Settings, TrustedIdentity
from vet_agent.runtime import QwenClient

from .models import (
    MemoryCandidateProposal,
    MemoryExtractionOutput,
    MemoryExtractionRequest,
    MemoryExtractionResult,
    MemoryExtractionStrategy,
)


class MemoryExtractionAgent:
    """通过 LiteLLM response_format 抽取长期记忆候选提议。

    :return: 无返回值。
    """

    def __init__(self, qwen: QwenClient, settings: Settings) -> None:
        """初始化长期记忆候选抽取器。

        :param qwen: 通义千问兼容客户端。
        :param settings: 应用配置对象。
        :return: 无返回值。
        """
        self.qwen = qwen
        self.settings = settings

    async def extract(
        self,
        *,
        identity: TrustedIdentity,
        user_text: str,
        response: AgentTurnResponse,
        model: str,
    ) -> MemoryExtractionResult:
        """抽取当前回合的长期记忆候选提议。

        :param identity: 可信身份信息。
        :param user_text: 用户输入文本；仅作为显式来源的后备载体。
        :param response: 当前回合响应对象。
        :param model: 当前回合使用的模型名称。
        :return: 返回结构化长期记忆候选抽取结果。
        """
        request = MemoryExtractionRequest.from_turn(
            identity,
            user_text=user_text,
            response=response,
        )
        if not self.settings.enable_memory_extraction:
            return self._failure_result(
                request,
                strategy=MemoryExtractionStrategy.MEMORY_EXTRACTION_DISABLED,
                fallback_reason="enable_memory_extraction=false",
            )
        if request.response_status not in {"completed", "requires_followup"}:
            return self._failure_result(
                request,
                strategy=MemoryExtractionStrategy.MEMORY_EXTRACTION_SKIPPED,
                fallback_reason=f"response_status={request.response_status}",
            )
        if not request.sources:
            return self._failure_result(
                request,
                strategy=MemoryExtractionStrategy.MEMORY_EXTRACTION_EMPTY_SOURCE,
                fallback_reason="no_extraction_source",
            )
        if not self.qwen.available:
            return self._failure_result(
                request,
                strategy=MemoryExtractionStrategy.MEMORY_EXTRACTION_UNAVAILABLE,
                fallback_reason="litellm_proxy_unavailable",
            )

        try:
            parsed = await self.qwen.chat_structured(
                self._messages(request),
                response_model=MemoryExtractionOutput,
                model=model,
                temperature=0.0,
            )
            proposals = self._build_proposals(request, parsed)
        except ValidationError as exc:
            return self._failure_result(
                request,
                strategy=MemoryExtractionStrategy.MEMORY_EXTRACTION_INVALID_SCHEMA,
                fallback_reason=str(exc),
            )
        except ValueError as exc:
            return self._failure_result(
                request,
                strategy=MemoryExtractionStrategy.MEMORY_EXTRACTION_INVALID_SCHEMA,
                fallback_reason=str(exc),
            )
        except RuntimeError as exc:
            return self._failure_result(
                request,
                strategy=MemoryExtractionStrategy.MEMORY_EXTRACTION_UNAVAILABLE,
                fallback_reason=str(exc),
            )
        except Exception as exc:
            return self._failure_result(
                request,
                strategy=MemoryExtractionStrategy.MEMORY_EXTRACTION_FAILED,
                fallback_reason=f"{type(exc).__name__}: {exc}",
            )

        return MemoryExtractionResult(
            proposals=proposals,
            strategy=MemoryExtractionStrategy.LITELLM_RESPONSE_FORMAT,
            fallback_reason=parsed.rationale.strip() or None,
            confidence=float(parsed.confidence),
            source_text=request.response_text[:500],
        )

    def _messages(self, request: MemoryExtractionRequest) -> list[dict[str, str]]:
        """构造长期记忆候选抽取所需的模型消息。

        :param request: 长期记忆候选抽取请求。
        :return: 返回可直接发送给结构化模型的消息列表。
        """
        return [
            {
                "role": "system",
                "content": (
                    "你是兽医长期记忆候选抽取器。"
                    "你只能基于显式来源片段抽取候选提议，必须返回严格 JSON Schema 结构。"
                    "不得使用关键词、正则、默认分类或空值补造事实。"
                    "不得跨 source_id 混合来源，不得把助手建议、RAG 证据或系统提示当作新事实。"
                    "未知分类使用 TODO，未知范围使用 unknown。"
                    "只输出候选，不要输出写入裁决。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "将显式来源归一为长期记忆候选提议。",
                        "payload": request.to_prompt_payload(),
                    },
                    ensure_ascii=False,
                ),
            },
        ]

    def _build_proposals(
        self,
        request: MemoryExtractionRequest,
        output: MemoryExtractionOutput,
    ) -> tuple[MemoryCandidateProposal, ...]:
        """将结构化模型输出归一为内部候选提议。

        :param request: 长期记忆候选抽取请求。
        :param output: 结构化模型输出。
        :return: 返回内部候选提议元组。
        :raises ValueError: 当模型输出引用未知来源标识或来源边界非法时抛出。
        """
        source_lookup = request.source_map()
        proposals: list[MemoryCandidateProposal] = []
        for item in output.proposals:
            source_id = item.source_id.strip()
            if not source_id:
                raise ValueError("memory extraction proposal source_id is required")
            source_entry = source_lookup.get(source_id)
            if source_entry is None:
                raise ValueError(f"memory extraction proposal references unknown source_id: {source_id}")
            proposals.append(
                MemoryCandidateProposal.from_item(
                    item,
                    source_entry=source_entry,
                )
            )
        return tuple(proposals)

    def _failure_result(
        self,
        request: MemoryExtractionRequest,
        *,
        strategy: MemoryExtractionStrategy,
        fallback_reason: str,
    ) -> MemoryExtractionResult:
        """构造显式失败或跳过状态的抽取结果。

        :param request: 长期记忆候选抽取请求。
        :param strategy: 失败或跳过策略。
        :param fallback_reason: 失败或跳过原因。
        :return: 返回结构化失败结果。
        """
        return MemoryExtractionResult(
            proposals=(),
            strategy=strategy,
            fallback_reason=fallback_reason[:240] if fallback_reason else None,
            confidence=0.0,
            source_text=request.response_text[:500],
        )

"""
=============================================================================
文件：src/vet_agent/memory_extraction/models.py
作用：定义长期记忆候选抽取链路的稳定结构化契约。
范围：承载输入来源、候选提议、LiteLLM response_format 输出模型与抽取结果；
      不访问数据库、不调用外部服务、不执行写入裁决或记忆投影。
说明：本文件仅描述长期记忆候选抽取阶段的数据形状；候选写入裁决、权威事实
      落库与 Mem0 投影应由后续独立治理链路负责。
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vet_agent import AgentTurnResponse, TrustedIdentity


class MemoryExtractionEntryKind(StrEnum):
    """表示长期记忆候选抽取输入来源的边界类型。

    说明：该枚举只用于构造结构化抽取输入，不决定写入、删除或纠正动作。

    :return: 无返回值；枚举值用于区分 turn 级与 task 级来源。
    """

    TURN = "turn"
    TASK = "task"


class MemoryExtractionSubjectScope(StrEnum):
    """表示长期记忆候选的主体范围。

    说明：主体范围只用于区分宠物事实、主人偏好与其它可信范围，不承载写入策略。

    :return: 无返回值；枚举值用于候选抽取与后续治理链路的主体识别。
    """

    PET = "pet"
    OWNER = "owner"


class MemoryExtractionFactType(StrEnum):
    """表示长期记忆候选的事实大类。

    说明：该枚举只承载长期事实的受控类别，不承担候选写入决定。

    :return: 无返回值；枚举值用于结构化候选归类与后续 OPA 裁决。
    """

    PROFILE = "profile"
    MEDICAL = "medical"
    MEDICATION = "medication"
    DIET = "diet"
    BEHAVIOR = "behavior"
    OWNER_PREFERENCE = "owner_preference"
    TODO = "TODO"


class MemoryExtractionAssertionStatus(StrEnum):
    """表示长期记忆候选的断言状态。

    说明：断言状态只描述候选是否明确、否定、纠正或冲突，不决定是否写入。

    :return: 无返回值；枚举值用于后续治理链路判定候选可信度与冲突形态。
    """

    CONFIRMED = "confirmed"
    UNCERTAIN = "uncertain"
    NEGATED = "negated"
    CORRECTED = "corrected"
    CONFLICTED = "conflicted"


class MemoryExtractionDurability(StrEnum):
    """表示长期记忆候选的持久性判断。

    说明：持久性只用于区分长期事实、会话性片段和急性临时信息，不直接写库。

    :return: 无返回值；枚举值用于后续写入治理和审计分层。
    """

    DURABLE = "durable"
    EPISODIC = "episodic"
    ACUTE = "acute"
    UNKNOWN = "unknown"


class MemoryExtractionTemporalScope(StrEnum):
    """表示长期记忆候选的时间范围。

    说明：时间范围用于约束长期候选的语义边界，不作为槽位状态机。

    :return: 无返回值；枚举值用于抽取、审计和写入治理。
    """

    CURRENT = "current"
    ONGOING = "ongoing"
    HISTORICAL = "historical"
    REMOTE_PAST = "remote_past"
    UNCLEAR = "unclear"
    UNKNOWN = "unknown"


class MemoryExtractionEvidenceKind(StrEnum):
    """表示长期记忆候选的证据来源类型。

    说明：证据类型只描述候选来自用户原文、问诊状态、开放观察或助手输出中的哪一类，
    不改变候选是否可写入的结论。

    :return: 无返回值；枚举值用于审计和后续 OPA 输入。
    """

    USER_TEXT = "user_text"
    CONSULTATION_STATE = "consultation_state"
    OPEN_OBSERVATION = "open_observation"
    ASSISTANT_OUTPUT = "assistant_output"
    TASK_CONTEXT = "task_context"


class MemoryExtractionStrategy(StrEnum):
    """表示长期记忆候选抽取链路的可观察策略名称。

    说明：该枚举只描述抽取阶段是否由 LiteLLM response_format 成功完成，
    不表示候选已被允许写入权威事实库。

    :return: 无返回值；枚举值用于响应 metadata、trace 和测试断言。
    """

    LITELLM_RESPONSE_FORMAT = "litellm_response_format"
    MEMORY_EXTRACTION_DISABLED = "memory_extraction_disabled"
    MEMORY_EXTRACTION_SKIPPED = "memory_extraction_skipped"
    MEMORY_EXTRACTION_UNAVAILABLE = "memory_extraction_unavailable"
    MEMORY_EXTRACTION_INVALID_SCHEMA = "memory_extraction_invalid_schema"
    MEMORY_EXTRACTION_FAILED = "memory_extraction_failed"
    MEMORY_EXTRACTION_EMPTY_SOURCE = "memory_extraction_empty_source"


@dataclass(frozen=True)
class MemoryExtractionSourceEntry:
    """表示长期记忆候选抽取的单个显式来源片段。

    :param source_id: 来源片段标识。
    :param entry_kind: 来源片段类型。
    :param user_text: 用户侧原始文本。
    :param assistant_text: 助手侧文本或回合摘要。
    :param task_id: 任务展示标识；没有时为空。
    :param task_key: 任务稳定键；没有时为空。
    :param task_title: 任务标题。
    :param task_domain: 任务域。
    :param consultation_state: 当前任务的结构化问诊状态快照。
    :param metadata: 附加审计信息。
    :return: 无返回值；该对象只承载候选抽取输入来源。
    """

    source_id: str
    entry_kind: MemoryExtractionEntryKind
    user_text: str
    assistant_text: str = ""
    task_id: str | None = None
    task_key: str | None = None
    task_title: str = ""
    task_domain: str = ""
    consultation_state: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_metadata(cls, item: dict[str, Any]) -> "MemoryExtractionSourceEntry":
        """从响应 metadata 中的来源字典恢复来源片段。

        :param item: 已序列化的来源片段字典。
        :return: 返回结构化长期记忆候选来源片段。
        :raises ValueError: 来源字典缺少必要字段或枚举值非法时抛出。
        """
        source_id = str(item.get("source_id") or "").strip()
        if not source_id:
            raise ValueError("memory extraction source_id is required")
        try:
            entry_kind = MemoryExtractionEntryKind(str(item.get("entry_kind") or "").strip())
        except ValueError as exc:
            raise ValueError(f"invalid memory extraction entry_kind for {source_id!r}") from exc
        consultation_state = item.get("consultation_state")
        metadata = item.get("metadata")
        return cls(
            source_id=source_id,
            entry_kind=entry_kind,
            user_text=str(item.get("user_text") or ""),
            assistant_text=str(item.get("assistant_text") or ""),
            task_id=str(item.get("task_id")) if item.get("task_id") else None,
            task_key=str(item.get("task_key")) if item.get("task_key") else None,
            task_title=str(item.get("task_title") or ""),
            task_domain=str(item.get("task_domain") or ""),
            consultation_state=dict(consultation_state) if isinstance(consultation_state, dict) else {},
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
        )

    def to_metadata(self) -> dict[str, Any]:
        """转换为可存入响应 metadata 的结构化来源字典。

        :return: 返回长期记忆候选来源字典。
        """
        return {
            "source_id": self.source_id,
            "entry_kind": self.entry_kind.value,
            "user_text": self.user_text,
            "assistant_text": self.assistant_text,
            "task_id": self.task_id,
            "task_key": self.task_key,
            "task_title": self.task_title,
            "task_domain": self.task_domain,
            "consultation_state": dict(self.consultation_state),
            "metadata": dict(self.metadata),
        }

    def to_prompt_item(self) -> dict[str, Any]:
        """转换为 LiteLLM response_format 提示词中的来源片段。

        :return: 返回模型可见的长期记忆候选来源字典。
        """
        return {
            "source_id": self.source_id,
            "entry_kind": self.entry_kind.value,
            "task_id": self.task_id,
            "task_key": self.task_key,
            "task_title": self.task_title,
            "task_domain": self.task_domain,
            "user_text": self.user_text[:2400],
            "assistant_text": self.assistant_text[:2400],
            "consultation_state": {
                key: value for key, value in self.consultation_state.items()
            },
            "metadata": {
                key: value for key, value in self.metadata.items()
            },
        }


@dataclass(frozen=True)
class MemoryExtractionRequest:
    """表示长期记忆候选抽取服务消费的结构化请求。

    :param identity: 可信身份范围。
    :param response_id: 当前回合响应标识。
    :param response_status: 当前回合响应状态。
    :param response_model: 当前回合模型名称。
    :param response_text: 当前回合助手输出文本。
    :param sources: 显式来源片段集合。
    :param response_metadata: 当前回合响应 metadata 快照。
    :return: 无返回值；该对象只用于抽取阶段的提示词编译。
    """

    identity: TrustedIdentity
    response_id: str
    response_status: str
    response_model: str
    response_text: str
    sources: tuple[MemoryExtractionSourceEntry, ...]
    response_metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_turn(
        cls,
        identity: TrustedIdentity,
        *,
        user_text: str,
        response: AgentTurnResponse,
    ) -> "MemoryExtractionRequest":
        """根据 Agent 回合输出构造长期记忆候选抽取请求。

        :param identity: 可信身份范围。
        :param user_text: 当前回合用户原始文本。
        :param response: 当前回合响应对象。
        :return: 返回可直接用于结构化抽取的请求对象。
        """
        response_metadata = dict(response.metadata or {})
        raw_sources = response_metadata.get("memory_extraction_sources")
        sources: tuple[MemoryExtractionSourceEntry, ...]
        if isinstance(raw_sources, list) and raw_sources:
            parsed_sources: list[MemoryExtractionSourceEntry] = []
            for item in raw_sources:
                if not isinstance(item, dict):
                    continue
                try:
                    parsed_sources.append(MemoryExtractionSourceEntry.from_metadata(item))
                except ValueError:
                    continue
            sources = tuple(parsed_sources)
        else:
            sources = (
                MemoryExtractionSourceEntry(
                    source_id=response.id,
                    entry_kind=MemoryExtractionEntryKind.TURN,
                    user_text=user_text,
                    assistant_text=response.output_text,
                    task_title="当前回合",
                    metadata={"fallback": True},
                ),
            )
        if not sources:
            sources = (
                MemoryExtractionSourceEntry(
                    source_id=response.id,
                    entry_kind=MemoryExtractionEntryKind.TURN,
                    user_text=user_text,
                    assistant_text=response.output_text,
                    task_title="当前回合",
                    metadata={"fallback": True},
                ),
            )
        return cls(
            identity=identity,
            response_id=response.id,
            response_status=response.status,
            response_model=response.model,
            response_text=response.output_text,
            sources=sources,
            response_metadata=response_metadata,
        )

    def source_map(self) -> dict[str, MemoryExtractionSourceEntry]:
        """构造按 source_id 索引的长期记忆候选来源映射。

        :return: 返回当前请求中可见的来源片段映射。
        """
        return {item.source_id: item for item in self.sources}

    def to_prompt_payload(self) -> dict[str, Any]:
        """转换为 LiteLLM response_format 使用的提示词负载。

        :return: 返回长期记忆候选抽取提示词负载。
        """
        return {
            "identity": {
                "user_id": self.identity.user_id,
                "pet_id": self.identity.pet_id,
                "session_id": self.identity.session_id,
            },
            "response": {
                "response_id": self.response_id,
                "status": self.response_status,
                "model": self.response_model,
                "text": self.response_text[:4000],
            },
            "response_context": {
                key: value
                for key, value in self.response_metadata.items()
                if key
                in {
                    "consultation_phase",
                    "consultation_state",
                    "answerability",
                    "task_router",
                    "task_count",
                    "multi_agent_path",
                }
            },
            "source_count": len(self.sources),
            "allowed_subject_scopes": [item.value for item in MemoryExtractionSubjectScope],
            "allowed_fact_types": [item.value for item in MemoryExtractionFactType],
            "allowed_assertion_statuses": [item.value for item in MemoryExtractionAssertionStatus],
            "allowed_durabilities": [item.value for item in MemoryExtractionDurability],
            "allowed_temporal_scopes": [item.value for item in MemoryExtractionTemporalScope],
            "allowed_evidence_kinds": [item.value for item in MemoryExtractionEvidenceKind],
            "sources": [source.to_prompt_item() for source in self.sources],
            "rules": [
                "只抽取长期值得保留的候选，不要输出临时噪声。",
                "候选必须显式来自单个 source_id，不能跨来源混合。",
                "task 级来源不得自动提升为 session 外的宠物权威事实。",
                "只根据来源中的用户原文、问诊状态、开放观察或回合摘要抽取。",
                "不要把助手解释、建议、RAG 证据或系统提示当成新的事实权威。",
                "fact_type 与 fact_key 只表达候选归类，不表示写入许可。",
                "未知分类使用 TODO，未知时间范围使用 unknown。",
                "事实不确定时用 assertion_status=uncertain，不要补写为确认事实。",
                "对明显否定、纠正或冲突语义要显式标记状态。",
            ],
        }


class MemoryExtractionProposalItem(BaseModel):
    """定义 LiteLLM response_format 返回的长期记忆候选条目契约。

    :return: 无返回值；该模型只校验结构，不表示候选已被允许写入。
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, description="候选来源标识。")
    subject_scope: MemoryExtractionSubjectScope = Field(description="候选主体范围。")
    fact_type: MemoryExtractionFactType = Field(description="候选事实大类。")
    fact_key: str = Field(min_length=1, max_length=120, description="候选事实稳定键。")
    fact_value: str = Field(min_length=1, max_length=400, description="归一后的候选事实值。")
    assertion_status: MemoryExtractionAssertionStatus = Field(description="候选断言状态。")
    durability: MemoryExtractionDurability = Field(description="候选持久性判断。")
    temporal_scope: MemoryExtractionTemporalScope = Field(description="候选时间范围。")
    confidence: float = Field(ge=0.0, le=1.0, description="本条候选抽取置信度。")
    source_kind: MemoryExtractionEvidenceKind = Field(description="候选的证据来源类型。")
    source_text: str = Field(default="", max_length=500, description="来源原文片段。")
    rationale: str = Field(default="", max_length=240, description="简短抽取依据，仅用于审计。")


class MemoryExtractionOutput(BaseModel):
    """定义 LiteLLM response_format 返回的长期记忆候选集合契约。

    :return: 无返回值；该模型只承载结构化候选，不承载写入裁决。
    """

    model_config = ConfigDict(extra="forbid")

    proposals: list[MemoryExtractionProposalItem] = Field(default_factory=list, max_length=12)
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="本轮长期记忆候选抽取整体置信度。",
    )
    rationale: str = Field(
        default="",
        max_length=240,
        description="简短抽取依据，仅用于审计，不进入写入裁决。",
    )


@dataclass(frozen=True)
class MemoryCandidateProposal:
    """表示长期记忆抽取阶段的单条候选提议。

    :param source_id: 候选来源标识。
    :param subject_scope: 候选主体范围。
    :param fact_type: 候选事实大类。
    :param fact_key: 候选事实稳定键。
    :param fact_value: 候选事实值。
    :param assertion_status: 候选断言状态。
    :param durability: 候选持久性判断。
    :param temporal_scope: 候选时间范围。
    :param confidence: 候选抽取置信度。
    :param source_kind: 候选的证据来源类型。
    :param source_text: 候选来源原文片段。
    :param rationale: 候选抽取依据。
    :param metadata: 附加审计信息。
    :return: 无返回值；该对象仅表示抽取候选，不表示写入许可。
    """

    source_id: str
    subject_scope: MemoryExtractionSubjectScope
    fact_type: MemoryExtractionFactType
    fact_key: str
    fact_value: str
    assertion_status: MemoryExtractionAssertionStatus
    durability: MemoryExtractionDurability
    temporal_scope: MemoryExtractionTemporalScope
    confidence: float
    source_kind: MemoryExtractionEvidenceKind
    source_text: str
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_item(
        cls,
        item: MemoryExtractionProposalItem,
        *,
        source_entry: MemoryExtractionSourceEntry,
    ) -> "MemoryCandidateProposal":
        """将结构化 LLM 输出归一为内部候选提议。

        :param item: 通过 Pydantic 校验的结构化候选条目。
        :param source_entry: 与候选对应的显式来源片段。
        :return: 返回内部长期记忆候选提议。
        """
        metadata = {
            "source_entry": source_entry.to_metadata(),
            "source_kind": item.source_kind.value,
            "rationale": item.rationale[:240],
        }
        return cls(
            source_id=item.source_id.strip(),
            subject_scope=item.subject_scope,
            fact_type=item.fact_type,
            fact_key=item.fact_key.strip(),
            fact_value=item.fact_value.strip(),
            assertion_status=item.assertion_status,
            durability=item.durability,
            temporal_scope=item.temporal_scope,
            confidence=float(item.confidence),
            source_kind=item.source_kind,
            source_text=item.source_text.strip()[:500],
            rationale=item.rationale.strip()[:240],
            metadata=metadata,
        )

    def to_metadata(self) -> dict[str, Any]:
        """转换为可存入响应 metadata 或数据库审计字段的字典。

        :return: 返回候选提议审计字典。
        """
        return {
            "source_id": self.source_id,
            "subject_scope": self.subject_scope.value,
            "fact_type": self.fact_type.value,
            "fact_key": self.fact_key,
            "fact_value": self.fact_value,
            "assertion_status": self.assertion_status.value,
            "durability": self.durability.value,
            "temporal_scope": self.temporal_scope.value,
            "confidence": self.confidence,
            "source_kind": self.source_kind.value,
            "source_text": self.source_text,
            "rationale": self.rationale,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class MemoryExtractionResult:
    """表示长期记忆候选抽取阶段的结构化结果或显式失败状态。

    :param proposals: 通过契约校验的长期记忆候选提议。
    :param strategy: 抽取策略或失败状态。
    :param fallback_reason: 不可用、失败或 schema 非法原因。
    :param confidence: 本轮抽取整体置信度。
    :param source_text: 当前回合摘要文本。
    :return: 无返回值；该对象供编排层写入审计与后续治理使用。
    """

    proposals: tuple[MemoryCandidateProposal, ...]
    strategy: MemoryExtractionStrategy = MemoryExtractionStrategy.MEMORY_EXTRACTION_EMPTY_SOURCE
    fallback_reason: str | None = None
    confidence: float = 0.0
    source_text: str = ""

    def is_trusted(self) -> bool:
        """判断当前结果是否由结构化模型路径成功产出。

        :return: 结构化抽取成功时返回 True，否则返回 False。
        """
        return self.strategy == MemoryExtractionStrategy.LITELLM_RESPONSE_FORMAT

    def to_metadata(self) -> dict[str, Any]:
        """转换为响应 metadata 和审计留痕使用的结构化摘要。

        :return: 返回长期记忆候选抽取摘要。
        """
        return {
            "agent": "MemoryExtractionAgent",
            "strategy": self.strategy.value,
            "fallback_reason": self.fallback_reason,
            "confidence": self.confidence,
            "source_text": self.source_text,
            "trusted": self.is_trusted(),
            "proposal_count": len(self.proposals),
            "proposal_keys": [f"{item.fact_type.value}:{item.fact_key}" for item in self.proposals],
            "proposals": [item.to_metadata() for item in self.proposals],
        }


MemoryFactCandidate = MemoryCandidateProposal

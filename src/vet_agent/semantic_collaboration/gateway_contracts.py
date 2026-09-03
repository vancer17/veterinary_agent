"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/gateway_contracts.py
作用：定义受限语义协作 DAG M05 结构化 LLM Gateway 的稳定输入输出契约。
范围：覆盖 SKILL prompt 投影、单次模型调用请求、底层传输响应协议、
      model proposal、调用 metadata 与 attempt 身份绑定。
说明：本文件只声明契约和结构校验，不发送 HTTP 请求、不解析模型内容、
      不执行语义 verifier、不提交 artifact、不访问任何下游领域状态。
=============================================================================
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .scheduler_contracts import SemanticTaskExecutionRequest


class SemanticChatMessage(BaseModel):
    """表示发送给结构化模型网关的单条受限消息。

    :return: 无返回值；消息内容由上层 SKILL prompt 投影权威生成。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    role: Literal["system", "user", "assistant"] = Field(
        description="OpenAI 兼容消息角色。",
    )
    content: str = Field(
        min_length=1,
        description="非空消息正文；不得用摘要替代权威原文投影。",
    )


class SkillPromptProjection(BaseModel):
    """表示单个 SKILL 的不可变提示词投影。

    :return: 无返回值；M05 只消费和哈希该投影，不重新选择上下文。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    skill_id: str = Field(
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
        min_length=1,
        max_length=120,
        description="提示词投影绑定的生产 SKILL 标识。",
    )
    skill_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$",
        description="提示词投影绑定的生产 SKILL 版本。",
    )
    prompt_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$",
        description="提示词模板自身语义化版本。",
    )
    context_digest: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="生成该提示词投影的 TurnSnapshot digest。",
    )
    messages: tuple[SemanticChatMessage, ...] = Field(
        min_length=1,
        max_length=32,
        description="按顺序发送的受限模型消息集合。",
    )

    @model_validator(mode="after")
    def validate_message_roles(self) -> Self:
        """校验消息集合必须包含用户消息且角色顺序稳定。

        :return: 返回可进入 M05 的提示词投影。
        :raises ValueError: 缺少用户消息或出现重复 system 消息时抛出。
        """
        if not any(message.role == "user" for message in self.messages):
            raise ValueError("skill prompt projection requires a user message")
        system_count = sum(message.role == "system" for message in self.messages)
        if system_count > 1:
            raise ValueError("skill prompt projection allows at most one system message")
        return self


class StructuredLLMCallRequest(BaseModel):
    """表示 M05 单次结构化模型调用请求。

    :return: 无返回值；一次请求必须对应一个 Temporal 语义 attempt。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    execution: SemanticTaskExecutionRequest = Field(
        description="M04 传入的权威任务执行上下文。",
    )
    prompt: SkillPromptProjection = Field(
        description="与任务 Skill 和 TurnSnapshot 绑定的提示词投影。",
    )
    model: str = Field(
        min_length=1,
        max_length=200,
        description="本次调用必须精确使用的模型名称。",
    )
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="结构化调用采样温度。",
    )
    timeout_seconds: float | None = Field(
        default=None,
        gt=0.0,
        le=600.0,
        description="本次模型调用超时；为空时使用运行时默认配置。",
    )


class StructuredModelTransportResponse(Protocol):
    """表示底层结构化模型传输必须返回的单次响应视图。

    :return: 无返回值；实现不得隐藏 fallback、内部重试或响应清洗。
    """

    content: object | None
    requested_model: str
    response_model: str | None
    response_id: str | None
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    usage_available: bool


class StructuredModelTransport(Protocol):
    """表示 M05 依赖的单次结构化模型传输端口。

    :return: 无返回值；该协议隔离 LiteLLM 客户端实现和 Semantic DAG 契约。
    """

    async def structured_once(
        self,
        messages: list[dict[str, str]],
        *,
        json_schema: dict[str, Any],
        schema_name: str,
        model: str,
        temperature: float,
        timeout_seconds: float | None,
    ) -> StructuredModelTransportResponse:
        """执行一次严格 JSON Schema 绑定的模型传输。

        :param messages: OpenAI 兼容消息列表。
        :param json_schema: SkillCatalog 提供的权威输出 JSON Schema。
        :param schema_name: 传给模型网关的稳定 schema 名称。
        :param model: 必须精确使用的模型名称。
        :param temperature: 采样温度。
        :param timeout_seconds: 可选调用超时时间。
        :return: 返回原始内容与调用 metadata，不解析语义。
        """


class StructuredLLMCallMetadata(BaseModel):
    """表示一次 M05 结构化模型调用的完整审计元数据。

    :return: 无返回值；该对象不携带完整 prompt、原始响应或密钥。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    run_id: str = Field(description="语义协作 DAG workflow 稳定标识。")
    task_id: str = Field(description="权威 PlanTask 标识。")
    attempt_number: int = Field(ge=1, description="当前 Temporal 语义尝试编号。")
    skill_id: str = Field(description="当前调用的生产 SKILL 标识。")
    skill_version: str = Field(description="当前调用的生产 SKILL 版本。")
    turn_snapshot_digest: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="当前调用绑定的 TurnSnapshot digest。",
    )
    prompt_hash: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="canonical prompt JSON 的 SHA-256 摘要。",
    )
    skill_contract_digest: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="SkillSpec canonical JSON 的 SHA-256 摘要。",
    )
    output_schema_id: str = Field(description="权威输出 schema 标识。")
    output_schema_version: str = Field(description="权威输出 schema 版本。")
    output_schema_digest: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="权威输出 schema canonical JSON 摘要。",
    )
    requested_model: str = Field(description="请求传入的精确模型名称。")
    response_model: str | None = Field(
        default=None,
        description="模型网关响应中的实际模型快照。",
    )
    response_id: str | None = Field(
        default=None,
        description="模型网关响应标识。",
    )
    finish_reason: str | None = Field(
        default=None,
        description="模型响应 finish reason。",
    )
    prompt_tokens: int | None = Field(
        default=None,
        ge=0,
        description="请求 token 数；缺失时保持 None。",
    )
    completion_tokens: int | None = Field(
        default=None,
        ge=0,
        description="响应 token 数；缺失时保持 None。",
    )
    total_tokens: int | None = Field(
        default=None,
        ge=0,
        description="总 token 数；缺失时保持 None。",
    )
    usage_available: bool = Field(
        default=False,
        description="模型网关是否返回了完整可用 usage。",
    )
    latency_ms: float = Field(
        ge=0.0,
        description="本次底层模型传输耗时，单位毫秒。",
    )


class SemanticModelProposal(BaseModel):
    """表示 M05 返回但尚未经过 M07 验证的模型 proposal。

    :return: 无返回值；该对象不是 verified artifact，也不能直接进入领域投影。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    execution: SemanticTaskExecutionRequest = Field(
        description="生成该 proposal 的权威任务执行上下文。",
    )
    payload: dict[str, object] = Field(
        description="通过权威输出 JSON Schema 的模型 proposal。",
    )
    proposal_digest: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="canonical proposal JSON 的 SHA-256 摘要。",
    )
    metadata: StructuredLLMCallMetadata = Field(
        description="本次结构化模型调用的审计元数据。",
    )

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        """校验 proposal 身份与调用元数据完全一致。

        :return: 返回身份闭合的模型 proposal。
        :raises ValueError: 任务、attempt 或上下文身份不一致时抛出。
        """
        execution = self.execution
        metadata = self.metadata
        if (
            execution.run_id,
            execution.task.task_id,
            execution.attempt_number,
            execution.turn_snapshot_digest,
        ) != (
            metadata.run_id,
            metadata.task_id,
            metadata.attempt_number,
            metadata.turn_snapshot_digest,
        ):
            raise ValueError("semantic model proposal identity mismatch")
        if (
            execution.task.skill_id,
            execution.task.skill_version,
        ) != (
            metadata.skill_id,
            metadata.skill_version,
        ):
            raise ValueError("semantic model proposal skill identity mismatch")
        return self

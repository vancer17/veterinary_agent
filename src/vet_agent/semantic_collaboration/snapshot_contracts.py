"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/snapshot_contracts.py
作用：定义受限语义协作 DAG M02 TurnSnapshot 的权威机器可读契约。
范围：覆盖当前回合原文、上一轮追问、已验证事实摘要、可信宠物上下文、
      构建请求、硬预算、稳定 digest、构建结果与 SKILL 受限投影契约。
说明：本文件只承载不可变契约和确定性校验，不调用 LLM、不访问数据库、
      不读取问诊状态、临床安全评估、required_context、OPA 或长期记忆。
=============================================================================
"""

from __future__ import annotations

import hashlib
import hmac
import json
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import SkillContextResource
from .errors import TurnSnapshotDigestMismatchError

#: 当前生产 TurnSnapshot 契约版本。
TURN_SNAPSHOT_VERSION: Literal["1.0.0"] = "1.0.0"


class OriginalTextExtractionPolicy(StrEnum):
    """表示当前回合原文进入 TurnSnapshot 前的确定性提取策略。

    :return: 无返回值；该枚举防止各 SKILL 使用不同规则重新拼接原文。
    """

    #: 入口仅存在一个文本消息时逐字保留。
    SINGLE_MESSAGE = "single_message"
    #: 入口存在多个文本消息时按明确规范拼接。
    CANONICAL_JOINED_MESSAGES = "canonical_joined_messages"


class BoundedHistoryReadStatus(StrEnum):
    """表示上一轮受限历史读取后的显式状态。

    :return: 无返回值；该枚举区分没有上一轮与成功读取但无追问。
    """

    #: 成功读取上一轮，问题集合可以为空。
    AVAILABLE = "available"
    #: 当前回合是会话内第一回合。
    NO_PREVIOUS_TURN = "no_previous_turn"


class VerifiedPriorFactPolarity(StrEnum):
    """表示已验证历史事实摘要允许携带的断言状态。

    :return: 无返回值；该枚举仅复用上游 verified artifact 的稳定语义。
    """

    #: 用户报告现象存在。
    PRESENT = "present"
    #: 用户明确否认现象存在。
    DENIED = "denied"
    #: 用户明确报告状态正常。
    REPORTED_NORMAL = "reported_normal"
    #: 用户表达不确定。
    UNCERTAIN = "uncertain"
    #: 用户纠正先前信息。
    CORRECTED = "corrected"


class VerifiedPriorFactSummaryStatus(StrEnum):
    """表示已验证历史事实摘要的成功读取状态。

    :return: 无返回值；该枚举防止来源失败被伪装为空事实。
    """

    #: 成功读取且至少存在一条已验证事实。
    AVAILABLE = "available"
    #: 成功读取但当前范围没有已验证 claim。
    NO_VERIFIED_CLAIMS = "no_verified_claims"


class TrustedPetContextSource(StrEnum):
    """表示可信宠物上下文进入 TurnSnapshot 前的服务端来源。

    :return: 无返回值；该枚举禁止请求侧自报画像伪装为可信上下文。
    """

    #: 服务端范围上下文服务提供的可信画像。
    SCOPE_CONTEXT_SERVICE = "scope_context_service"


class TurnSnapshotBudgetUnit(StrEnum):
    """表示 TurnSnapshot 硬预算使用的确定性计量单位。

    :return: 无返回值；当前生产契约只允许 Unicode code point 计数。
    """

    #: 按 Python 字符串 Unicode code point 数计数。
    UNICODE_CODEPOINTS = "unicode_codepoints"


class OriginalUserText(BaseModel):
    """表示当前回合进入语义协作 DAG 的不可变原文。

    :return: 无返回值；该对象保留证据原文，不做摘要、裁剪或医学改写。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
        str_strip_whitespace=False,
    )

    text: str = Field(
        min_length=1,
        description="当前回合原文；仅要求包含非空白字符，不得被 trim 或截断。",
    )
    input_item_count: int = Field(
        ge=1,
        description="形成当前原文的入口输入项数量，用于审计提取策略。",
    )
    extraction_policy: OriginalTextExtractionPolicy = Field(
        description="当前原文的确定性提取策略。",
    )

    @model_validator(mode="after")
    def require_nonblank_text(self) -> Self:
        """校验原文不能是空白字符。

        :return: 返回通过非空白校验的原文契约。
        :raises ValueError: 原文仅包含空白字符时抛出。
        """
        if not self.text.strip():
            raise ValueError("original user text must contain a non-whitespace character")
        return self

    @model_validator(mode="after")
    def validate_extraction_policy(self) -> Self:
        """校验原文提取策略与输入项数量一致。

        :return: 返回通过提取策略一致性校验的原文契约。
        :raises ValueError: 单消息策略携带多个输入项时抛出。
        """
        if (
            self.extraction_policy == OriginalTextExtractionPolicy.SINGLE_MESSAGE
            and self.input_item_count != 1
        ):
            raise ValueError("single message policy requires exactly one input item")
        return self


class TurnSnapshotSourceScope(BaseModel):
    """表示受限上下文来源读取所需的最小可信范围。

    :return: 无返回值；该对象不进入 digest，也不暴露给模型。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
        str_strip_whitespace=True,
    )

    user_id: str = Field(min_length=1, description="可信用户范围标识。")
    session_id: str = Field(min_length=1, description="可信会话范围标识。")
    pet_id: str = Field(min_length=1, description="可信宠物范围标识。")


class TurnSnapshotSourceRequest(BaseModel):
    """表示传给受限上下文来源端口的读取请求。

    :return: 无返回值；该对象不携带当前原文，避免来源读取时泄露不必要输入。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
        str_strip_whitespace=True,
    )

    scope: TurnSnapshotSourceScope = Field(description="可信读取范围。")
    turn_id: str = Field(min_length=1, description="当前回合稳定标识。")
    turn_index: int = Field(ge=0, description="当前回合在会话中的零基序号。")


class TurnSnapshotBuildRequest(BaseModel):
    """表示 TurnSnapshotBuilder 的当前回合构建请求。

    :return: 无返回值；该对象只作为 M02 输入边界，不触发任何模型调用。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
        str_strip_whitespace=True,
    )

    scope: TurnSnapshotSourceScope = Field(description="受限上下文读取范围。")
    turn_id: str = Field(min_length=1, description="当前回合稳定标识。")
    turn_index: int = Field(ge=0, description="当前回合零基序号。")
    original_user_text: OriginalUserText = Field(
        description="入口层按稳定策略提取的当前回合原文。",
    )
    attachment_count: int = Field(
        default=0,
        ge=0,
        description="当前回合附件数量；M02 文本契约当前必须为 0。",
    )

    def source_request(self) -> TurnSnapshotSourceRequest:
        """构造不携带当前原文的来源读取请求。

        :return: 返回只包含可信范围与回合身份的读取请求。
        """
        return TurnSnapshotSourceRequest(
            scope=self.scope,
            turn_id=self.turn_id,
            turn_index=self.turn_index,
        )


class LastAssistantQuestion(BaseModel):
    """表示上一轮助手追问中的一个可审计问题。

    :return: 无返回值；该对象用于多轮对齐，不承载医学判断。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
        str_strip_whitespace=False,
    )

    question_id: str = Field(
        min_length=1,
        description="追问稳定标识；用于后续回答对齐和审计。",
    )
    text: str = Field(
        min_length=1,
        description="追问原文；不得被摘要或截断。",
    )
    source_turn_id: str = Field(
        min_length=1,
        description="产生该追问的历史回合标识。",
    )
    order_index: int = Field(
        ge=0,
        description="该追问在上一轮输出中的顺序。",
    )

    @model_validator(mode="after")
    def require_nonblank_question(self) -> Self:
        """校验追问原文不能为空或空白。

        :return: 返回通过非空白校验的追问契约。
        :raises ValueError: 追问原文仅包含空白字符时抛出。
        """
        if not self.text.strip():
            raise ValueError("assistant question must contain a non-whitespace character")
        return self


class BoundedHistoryReadResult(BaseModel):
    """表示受限上一轮历史读取端口的成功结果。

    :return: 无返回值；该对象只允许上一轮追问进入 TurnSnapshot。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
    )

    status: BoundedHistoryReadStatus = Field(
        description="上一轮历史读取后的显式状态。",
    )
    questions: tuple[LastAssistantQuestion, ...] = Field(
        default=(),
        max_length=3,
        description="上一轮助手追问集合；必须按 order_index 升序排列。",
    )

    @model_validator(mode="after")
    def validate_history_status(self) -> Self:
        """校验上一轮历史状态与问题集合一致。

        :return: 返回通过状态一致性校验的受限历史结果。
        :raises ValueError: 状态与问题集合冲突或顺序非法时抛出。
        """
        if self.status == BoundedHistoryReadStatus.NO_PREVIOUS_TURN and self.questions:
            raise ValueError("no_previous_turn cannot contain assistant questions")
        question_ids = [question.question_id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("assistant question identifiers must be unique")
        source_turn_ids = {
            question.source_turn_id for question in self.questions
        }
        if len(source_turn_ids) > 1:
            raise ValueError("assistant questions must come from one previous turn")
        indexes = [question.order_index for question in self.questions]
        if len(indexes) != len(set(indexes)) or indexes != sorted(indexes):
            raise ValueError("assistant question order indexes must be unique and ascending")
        return self


class VerifiedPriorFact(BaseModel):
    """表示一条来自已验证语义 artifact 的历史事实摘要。

    :return: 无返回值；该对象不重新判断事实，仅保留上游权威投影。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
        str_strip_whitespace=True,
    )

    claim_id: str = Field(min_length=1, description="上游已验证 claim 稳定标识。")
    statement: str = Field(
        min_length=1,
        description="上游已验证事实的简明陈述。",
    )
    statement_type: str = Field(
        min_length=1,
        description="上游已验证事实类型标识。",
    )
    polarity: VerifiedPriorFactPolarity = Field(
        description="上游已验证断言状态。",
    )
    artifact_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$",
        description="产生该事实摘要的 artifact 语义化版本。",
    )
    source_digest: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="上游已验证 artifact 的 SHA-256 hex digest。",
    )


class VerifiedPriorFactSummary(BaseModel):
    """表示成功读取后的已验证历史事实摘要。

    :return: 无返回值；该对象显式区分没有事实与来源读取失败。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
    )

    status: VerifiedPriorFactSummaryStatus = Field(
        description="已验证事实摘要读取状态。",
    )
    facts: tuple[VerifiedPriorFact, ...] = Field(
        default=(),
        description="按上游稳定顺序保留的已验证事实集合。",
    )

    @model_validator(mode="after")
    def validate_summary_status(self) -> Self:
        """校验事实摘要状态与事实集合一致。

        :return: 返回通过状态一致性校验的事实摘要。
        :raises ValueError: 状态与事实集合冲突时抛出。
        """
        if (
            self.status == VerifiedPriorFactSummaryStatus.NO_VERIFIED_CLAIMS
            and self.facts
        ):
            raise ValueError("no_verified_claims cannot contain prior facts")
        if self.status == VerifiedPriorFactSummaryStatus.AVAILABLE and not self.facts:
            raise ValueError("available prior fact summary requires at least one fact")
        claim_ids = [fact.claim_id for fact in self.facts]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("prior fact claim identifiers must be unique")
        return self


class TrustedPetProfile(BaseModel):
    """表示进入语义协作 DAG 的服务端可信宠物画像。

    :return: 无返回值；该对象禁止携带请求侧自报资料。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
    )

    species: str | None = Field(default=None, description="服务端已验证物种。")
    breed: str | None = Field(default=None, description="服务端已验证品种。")
    age_text: str | None = Field(default=None, description="服务端已验证年龄描述。")
    weight_kg: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
        description="服务端已验证体重，单位千克。",
    )
    sex: str | None = Field(default=None, description="服务端已验证性别。")
    neutered: bool | None = Field(
        default=None,
        description="服务端已验证绝育状态。",
    )


class TrustedPetContext(BaseModel):
    """表示 M02 允许模型读取的服务端可信宠物上下文。

    :return: 无返回值；该对象只包含白名单画像字段和来源声明。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
    )

    source: TrustedPetContextSource = Field(
        description="可信宠物上下文来源；当前只允许范围上下文服务。",
    )
    profile: TrustedPetProfile = Field(
        description="服务端已验证宠物画像白名单投影。",
    )


class TurnSnapshotBudget(BaseModel):
    """表示 TurnSnapshot 构建和 SKILL 投影使用的硬预算集合。

    :return: 无返回值；预算超限必须显式失败，不得裁剪权威上下文。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
    )

    budget_unit: TurnSnapshotBudgetUnit = Field(
        default=TurnSnapshotBudgetUnit.UNICODE_CODEPOINTS,
        description="确定性预算计量单位。",
    )
    max_original_user_text_chars: int = Field(
        ge=1,
        description="当前回合原文最大 Unicode code point 数。",
    )
    max_last_question_chars: int = Field(
        ge=1,
        description="上一轮追问文本合计最大 Unicode code point 数。",
    )
    max_verified_prior_fact_chars: int = Field(
        ge=1,
        description="已验证历史事实陈述合计最大 Unicode code point 数。",
    )
    max_trusted_pet_context_chars: int = Field(
        ge=1,
        description="可信宠物上下文最大 Unicode code point 数。",
    )
    max_total_context_chars: int = Field(
        ge=1,
        description="四类模型可见上下文合计最大 Unicode code point 数。",
    )

    @model_validator(mode="after")
    def validate_total_budget(self) -> Self:
        """校验总预算不小于任何单项预算。

        :return: 返回通过预算闭合校验的预算契约。
        :raises ValueError: 总预算小于任一分项预算时抛出。
        """
        limits = (
            self.max_original_user_text_chars,
            self.max_last_question_chars,
            self.max_verified_prior_fact_chars,
            self.max_trusted_pet_context_chars,
        )
        if self.max_total_context_chars < max(limits):
            raise ValueError("total context budget cannot be smaller than a component budget")
        return self


class TurnSnapshotUsage(BaseModel):
    """表示一次 TurnSnapshot 构建后的确定性预算占用。

    :return: 无返回值；该对象用于审计和指标，不进入 context digest。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
    )

    budget_unit: TurnSnapshotBudgetUnit = Field(
        description="预算占用使用的计量单位。",
    )
    original_user_text_chars: int = Field(
        ge=0,
        description="当前回合原文 Unicode code point 数。",
    )
    last_assistant_question_chars: int = Field(
        ge=0,
        description="上一轮追问文本合计 Unicode code point 数。",
    )
    verified_prior_fact_chars: int = Field(
        ge=0,
        description="已验证历史事实陈述合计 Unicode code point 数。",
    )
    trusted_pet_context_chars: int = Field(
        ge=0,
        description="可信宠物上下文确定性表示长度。",
    )
    total_context_chars: int = Field(
        ge=0,
        description="全部模型可见上下文的合计占用。",
    )

    def to_metadata(self) -> dict[str, int | str]:
        """生成不含用户内容的预算审计 metadata。

        :return: 返回可写入 trace 的确定性预算字段字典。
        """
        return {
            "budget_unit": self.budget_unit.value,
            **self.model_dump(mode="json", exclude={"budget_unit"}),
        }


class TurnSnapshot(BaseModel):
    """表示一次用户回合的不可变受限全局语义上下文。

    :return: 无返回值；所有生成、审查和修复任务必须绑定同一 digest。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
        str_strip_whitespace=False,
    )

    turn_id: str = Field(min_length=1, description="当前回合稳定标识。")
    turn_index: int = Field(ge=0, description="当前回合零基序号。")
    original_user_text: str = Field(
        min_length=1,
        description="当前回合完整原文，不得被摘要或截断。",
    )
    original_text_extraction_policy: OriginalTextExtractionPolicy = Field(
        description="当前原文的入口提取策略。",
    )
    original_text_input_item_count: int = Field(
        ge=1,
        description="形成当前原文的入口输入项数量。",
    )
    bounded_history_status: BoundedHistoryReadStatus = Field(
        description="上一轮受限历史的显式读取状态。",
    )
    last_assistant_questions: tuple[LastAssistantQuestion, ...] = Field(
        default=(),
        max_length=3,
        description="上一轮助手追问集合。",
    )
    verified_prior_fact_summary: VerifiedPriorFactSummary = Field(
        description="成功读取后的已验证历史事实摘要。",
    )
    trusted_pet_context: TrustedPetContext = Field(
        description="服务端可信宠物上下文白名单投影。",
    )
    snapshot_version: Literal["1.0.0"] = Field(
        default=TURN_SNAPSHOT_VERSION,
        description="TurnSnapshot 契约版本。",
    )
    context_digest: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="权威上下文字段的 canonical SHA-256 digest。",
    )

    @model_validator(mode="after")
    def verify_context_digest(self) -> Self:
        """校验 context digest 与权威字段一致。

        :return: 返回通过 digest 校验的不可变 TurnSnapshot。
        :raises ValueError: digest 与权威字段不一致时抛出。
        """
        expected = compute_turn_snapshot_digest(
            self.model_dump(mode="json", exclude={"context_digest"}),
        )
        if not hmac.compare_digest(expected, self.context_digest):
            raise ValueError("turn snapshot context digest mismatch")
        return self

    def canonical_authoritative_json(self) -> str:
        """生成排除 digest 后的权威字段 canonical JSON。

        :return: 返回排序键、紧凑分隔且保留 Unicode 的 JSON 字符串。
        """
        return canonical_turn_snapshot_json(
            self.model_dump(mode="json", exclude={"context_digest"}),
        )

    def verify_digest(self, expected_digest: str) -> None:
        """校验外部任务 envelope 中的 digest。

        :param expected_digest: generator、reviewer 或 repairer 声明的 digest。
        :return: 无返回值。
        :raises TurnSnapshotDigestMismatchError: digest 不一致时抛出。
        """
        # 该方法保持契约层纯校验；异常类型由调用方或 snapshot 门面统一包装。
        if not hmac.compare_digest(self.context_digest, expected_digest):
            raise TurnSnapshotDigestMismatchError(
                "turn snapshot digest does not match task envelope",
            )


class TurnSnapshotBuildResult(BaseModel):
    """表示 TurnSnapshotBuilder 的成功构建结果。

    :return: 无返回值；usage 与 snapshot 一起返回，避免重复计算预算。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
    )

    snapshot: TurnSnapshot = Field(description="构建完成的不可变 TurnSnapshot。")
    usage: TurnSnapshotUsage = Field(description="该次构建的确定性预算占用。")


class TurnSnapshotProjection(BaseModel):
    """表示按 SkillSpec 上下文契约生成的受限 TurnSnapshot 视图。

    :return: 无返回值；未声明资源的值为 None 且不得被 Gateway 读取。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
    )

    turn_snapshot_digest: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="投影绑定的 TurnSnapshot digest。",
    )
    included_resources: tuple[SkillContextResource, ...] = Field(
        description="投影显式包含的 TurnSnapshot 资源集合。",
    )
    original_user_text: str | None = Field(
        default=None,
        description="已授权时的当前回合原文；未授权时必须保持 None。",
    )
    last_assistant_questions: tuple[LastAssistantQuestion, ...] | None = Field(
        default=None,
        description="已授权时的上一轮追问；未授权时必须保持 None。",
    )
    verified_prior_fact_summary: VerifiedPriorFactSummary | None = Field(
        default=None,
        description="已授权时的已验证历史事实摘要；未授权时必须保持 None。",
    )
    trusted_pet_context: TrustedPetContext | None = Field(
        default=None,
        description="已授权时的可信宠物上下文；未授权时必须保持 None。",
    )
    context_chars: int = Field(
        ge=0,
        description="投影 canonical JSON 的 Unicode code point 长度。",
    )

    @model_validator(mode="after")
    def validate_projection_resources(self) -> Self:
        """校验授权资源与投影字段保持一致。

        :return: 返回通过资源一致性校验的受限投影。
        :raises ValueError: 授权集合与字段存在冲突时抛出。
        """
        included = set(self.included_resources)
        if SkillContextResource.TURN_SNAPSHOT_DIGEST not in included:
            raise ValueError("turn snapshot projection must include snapshot digest")
        resource_fields = (
            (
                SkillContextResource.ORIGINAL_USER_TEXT,
                self.original_user_text is not None,
            ),
            (
                SkillContextResource.VERIFIED_PRIOR_FACT_SUMMARY,
                self.verified_prior_fact_summary is not None,
            ),
            (
                SkillContextResource.TRUSTED_PET_CONTEXT,
                self.trusted_pet_context is not None,
            ),
        )
        for resource, present in resource_fields:
            if (resource in included) != present:
                raise ValueError(f"projection resource state mismatch: {resource.value}")
        history_authorized = (
            SkillContextResource.LAST_ASSISTANT_QUESTIONS in included
            or SkillContextResource.BOUNDED_CONVERSATION_HISTORY in included
        )
        if history_authorized != (self.last_assistant_questions is not None):
            raise ValueError("projection bounded history state mismatch")
        return self


def canonical_turn_snapshot_json(payload: dict[str, Any]) -> str:
    """生成 TurnSnapshot 权威字段的 canonical JSON。

    :param payload: 待序列化的权威字段字典。
    :return: 返回排序键、紧凑分隔且保留 Unicode 的 JSON 字符串。
    """
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def compute_turn_snapshot_digest(payload: dict[str, Any]) -> str:
    """计算 TurnSnapshot 权威字段的 SHA-256 digest。

    :param payload: 排除 context_digest 后的权威字段字典。
    :return: 返回 64 位小写 hex digest。
    """
    canonical = canonical_turn_snapshot_json(payload)
    material = f"semantic-collaboration.turn-snapshot.v1\n{canonical}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()

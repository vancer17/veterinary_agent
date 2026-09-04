"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/prompt_renderer.py
作用：实现受限语义协作 DAG M06 / M08 / M10 的标准化 SKILL 文档提示词渲染器。
范围：覆盖渲染请求身份校验、生成 / Review / Repair 受限上下文投影、SKILL 文档
      绑定、模型可见章节选择、受限 Jinja 变量替换、tag 冲突校验与目录闭合。
说明：SKILL.md 文件头部元数据仅确定性代码可见；正文只有声明章节进入模型。
      本文件不调用模型、不做 evidence 绑定、不读取下游领域状态。
=============================================================================
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import (
    TURN_SNAPSHOT_CONTEXT_RESOURCES,
    SkillExecutionFamily,
    SkillSpec,
    SkillTaskKind,
)
from .errors import SemanticPromptRenderError
from .gateway_contracts import (
    SemanticChatMessage,
    SkillPromptProjection,
)
from .repair_contracts import ReviewRepairDimension
from .scheduler_contracts import SemanticTaskExecutionRequest
from .skill_document import SemanticSkillDocument, load_semantic_skill_document
from .skill_template import RestrictedSkillTemplate
from .snapshot_contracts import TurnSnapshotProjection

PROMPT_RESERVED_TAGS: tuple[str, ...] = (
    "task",
    "current_turn",
    "last_assistant_questions",
    "verified_prior_facts",
    "trusted_pet_context",
    "generated_claims",
    "claim_proposition",
    "claim_candidates",
    "target_claim",
    "repair_dimensions",
    "repair_hints",
)


class SkillPromptReviewContext(BaseModel):
    """表示 M08 Review SKILL 的确定性任务内上下文。

    :return: 无返回值；该上下文只携带待审查 claims，不携带生成器审计信息。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    generated_claims: tuple[str, ...] | None = Field(
        default=None,
        description="Coverage Review 待审查的系统编号 claim 集合。",
    )
    claim_proposition: str | None = Field(
        default=None,
        description="Faithfulness Review 当前唯一待审查 proposition。",
    )

    @model_validator(mode="after")
    def validate_review_context(self) -> Self:
        """校验 Review 上下文一次只表达一种审查粒度。

        :return: 返回可注入受限模板的 Review 上下文。
        :raises ValueError: 上下文缺失、重复、claim 超界或形态非法时抛出。
        """
        if (self.generated_claims is None) == (self.claim_proposition is None):
            raise ValueError("skill review context must define exactly one subject")
        if self.generated_claims is not None:
            if len(self.generated_claims) > 8:
                raise ValueError(
                    "generated claims review subject is empty or oversized"
                )
            if any(
                not claim.strip()
                or claim != claim.strip()
                or "\n" in claim
                or "\r" in claim
                for claim in self.generated_claims
            ):
                raise ValueError("generated claims review subject is invalid")
        if self.claim_proposition is not None and (
            not self.claim_proposition.strip()
            or self.claim_proposition != self.claim_proposition.strip()
            or "\n" in self.claim_proposition
            or "\r" in self.claim_proposition
        ):
            raise ValueError("claim proposition review subject is invalid")
        return self


class SkillPromptRenderRequest(BaseModel):
    """表示单个 M06 生成 SKILL 的受限渲染请求。

    :return: 无返回值；请求只携带权威任务、SkillSpec 与 TurnSnapshot 投影。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
        arbitrary_types_allowed=True,
    )

    execution: SemanticTaskExecutionRequest = Field(
        description="M04 传入的权威任务执行 envelope。",
    )
    spec: SkillSpec = Field(description="SkillCatalog 解析出的权威 SkillSpec。")
    projection: TurnSnapshotProjection = Field(
        description="按 SkillSpec 上下文策略生成的受限 TurnSnapshot 投影。",
    )
    review_context: SkillPromptReviewContext | None = Field(
        default=None,
        description="M08 Review 专用任务内上下文；生成 SKILL 必须为空。",
    )
    repair_context: SkillPromptRepairContext | None = Field(
        default=None,
        description="M10 Repair 专用任务内上下文；非 Repair SKILL 必须为空。",
    )

    @model_validator(mode="after")
    def validate_renderer_identity(self) -> Self:
        """校验任务、SKILL 与上下文摘要身份完全闭合。

        :return: 返回可进入 renderer 的不可变请求。
        :raises ValueError: 任务身份或上下文摘要不一致时抛出。
        """
        if (
            self.execution.task.skill_id,
            self.execution.task.skill_version,
            self.execution.turn_snapshot_digest,
        ) != (
            self.spec.skill_id,
            self.spec.skill_version,
            self.projection.turn_snapshot_digest,
        ):
            raise ValueError("skill prompt render request identity mismatch")
        if self.review_context is not None and self.repair_context is not None:
            raise ValueError("skill prompt cannot combine review and repair contexts")
        return self


class SkillPromptRepairContext(BaseModel):
    """表示 M10 Repair SKILL 的确定性任务内上下文。

    :return: 无返回值；该上下文不携带生成器、Reviewer 或下游领域审计信息。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    claim_candidates: tuple[str, ...] | None = Field(
        default=None,
        description="Inventory Repair 的任务内 c0/c1 claim 候选集合。",
    )
    target_claim: str | None = Field(
        default=None,
        description="Proposition Repair 的唯一待修复 proposition。",
    )
    repair_dimensions: tuple[ReviewRepairDimension, ...] = Field(
        min_length=1,
        description="M09 声明的修复维度，仅作为模型输入语义先验。",
    )
    repair_hints: tuple[str, ...] = Field(
        default=(),
        max_length=8,
        description="Coverage 提供的非权威修复线索。",
    )

    @model_validator(mode="after")
    def validate_repair_context(self) -> Self:
        """校验 Repair 上下文一次只表达一种修复粒度。

        :return: 返回可注入受限模板的 Repair 上下文。
        :raises ValueError: 主体缺失、重复、超界或 hint 形态非法时抛出。
        """
        if (self.claim_candidates is None) == (self.target_claim is None):
            raise ValueError("skill repair context must define exactly one subject")
        if self.claim_candidates is not None:
            if len(self.claim_candidates) > 8:
                raise ValueError("claim candidates repair subject is oversized")
            if any(
                not claim.strip()
                or claim != claim.strip()
                or "\n" in claim
                or "\r" in claim
                for claim in self.claim_candidates
            ):
                raise ValueError("claim candidates repair subject is invalid")
        if self.target_claim is not None and (
            not self.target_claim.strip()
            or self.target_claim != self.target_claim.strip()
            or "\n" in self.target_claim
            or "\r" in self.target_claim
        ):
            raise ValueError("target claim repair subject is invalid")
        if any(
            not hint.strip() or hint != hint.strip() or "\n" in hint or "\r" in hint
            for hint in self.repair_hints
        ):
            raise ValueError("repair hint shape is invalid")
        return self


class SkillPromptRenderer(Protocol):
    """表示 M06 / M08 / M10 版本化提示词渲染端口。

    :return: 无返回值；实现只能读取受限投影并输出不可变提示词投影。
    """

    @property
    def task_kind(self) -> SkillTaskKind:
        """读取 renderer 绑定的正交任务类型。

        :return: 返回 SkillTaskKind 稳定枚举。
        """

    @property
    def skill_id(self) -> str:
        """读取 renderer 绑定的生产 SKILL 标识。

        :return: 返回 SKILL 稳定标识。
        """

    @property
    def skill_version(self) -> str:
        """读取 renderer 绑定的生产 SKILL 版本。

        :return: 返回精确语义化版本。
        """

    @property
    def prompt_version(self) -> str:
        """读取提示词语义化版本。

        :return: 返回独立于 SkillSpec 版本的 prompt 版本。
        """

    def render(self, request: SkillPromptRenderRequest) -> SkillPromptProjection:
        """渲染单个受限 SKILL 提示词投影。

        :param request: 身份闭合的 M06 渲染请求。
        :return: 返回可交给 M05 的不可变提示词投影。
        """


def _required_text(
    projection: TurnSnapshotProjection,
    value: str | None,
    *,
    resource_name: str,
) -> str:
    """读取已授权上下文中的必填文本。

    :param projection: 受限 TurnSnapshot 投影。
    :param value: 投影字段当前值。
    :param resource_name: 稳定资源名称。
    :return: 返回非空上下文文本。
    :raises SemanticPromptRenderError: 已授权资源缺失时抛出。
    """
    if value is None:
        raise SemanticPromptRenderError(
            f"authorized prompt resource is missing: {resource_name}",
        )
    if not projection.included_resources:
        raise SemanticPromptRenderError("prompt projection has no included resources")
    return value


def _current_turn_text(projection: TurnSnapshotProjection) -> str:
    """读取当前回合原文变量。

    :param projection: 受限 TurnSnapshot 投影。
    :return: 返回未截断、未摘要的当前回合文本。
    """
    return _required_text(
        projection,
        projection.original_user_text,
        resource_name="original_user_text",
    )


def _question_lines(projection: TurnSnapshotProjection) -> str:
    """渲染上一轮有界追问上下文。

    :param projection: 受限 TurnSnapshot 投影。
    :return: 返回按顺序编号的追问文本；无追问时返回 none。
    :raises SemanticPromptRenderError: 已授权历史资源缺失时抛出。
    """
    questions = projection.last_assistant_questions
    if questions is None:
        raise SemanticPromptRenderError(
            "authorized prompt resource is missing: last_assistant_questions",
        )
    if not questions:
        return "none"
    return "\n".join(
        f"{index}. {question.text}" for index, question in enumerate(questions, start=1)
    )


def _prior_fact_lines(projection: TurnSnapshotProjection) -> str:
    """渲染已验证历史事实摘要。

    :param projection: 受限 TurnSnapshot 投影。
    :return: 返回历史事实陈述列表；无事实时返回 none。
    :raises SemanticPromptRenderError: 已授权事实资源缺失时抛出。
    """
    summary = projection.verified_prior_fact_summary
    if summary is None:
        raise SemanticPromptRenderError(
            "authorized prompt resource is missing: verified_prior_fact_summary",
        )
    if not summary.facts:
        return "none"
    return "\n".join(f"- {fact.statement}" for fact in summary.facts)


def _pet_context_lines(projection: TurnSnapshotProjection) -> str:
    """渲染可信宠物上下文的极浅键值视图。

    :param projection: 受限 TurnSnapshot 投影。
    :return: 返回仅包含白名单画像字段的文本；无画像时返回 none。
    :raises SemanticPromptRenderError: 已授权宠物上下文缺失时抛出。
    """
    context = projection.trusted_pet_context
    if context is None:
        raise SemanticPromptRenderError(
            "authorized prompt resource is missing: trusted_pet_context",
        )
    values: list[str] = []
    if context.profile.species is not None:
        values.append(f"species: {context.profile.species}")
    if context.profile.breed is not None:
        values.append(f"breed: {context.profile.breed}")
    if context.profile.age_text is not None:
        values.append(f"age: {context.profile.age_text}")
    if context.profile.weight_kg is not None:
        values.append(f"weight_kg: {context.profile.weight_kg}")
    if not values:
        return "none"
    return "\n".join(values)


def _generated_claim_lines(context: SkillPromptReviewContext) -> str:
    """渲染 Coverage Review 的系统编号 claim 集合。

    :param context: 当前 Review 任务的受限任务内上下文。
    :return: 返回逐行编号的 generated claims 文本。
    :raises SemanticPromptRenderError: Review 上下文缺失时抛出。
    """
    if context.generated_claims is None:
        raise SemanticPromptRenderError(
            "coverage review prompt context is missing generated claims",
        )
    if not context.generated_claims:
        return "none"
    return "\n".join(
        f"{index}. {claim}"
        for index, claim in enumerate(context.generated_claims, start=1)
    )


def _claim_proposition_text(context: SkillPromptReviewContext) -> str:
    """读取 Faithfulness Review 的唯一 proposition。

    :param context: 当前 Review 任务的受限任务内上下文。
    :return: 返回单行自然语言 proposition。
    :raises SemanticPromptRenderError: Review 上下文缺失时抛出。
    """
    if context.claim_proposition is None:
        raise SemanticPromptRenderError(
            "faithfulness review prompt context is missing claim proposition",
        )
    return context.claim_proposition


def _claim_candidate_lines(context: SkillPromptRepairContext) -> str:
    """渲染 Inventory Repair 的任务内 claim 候选集合。

    :param context: 当前 Repair 任务的受限任务内上下文。
    :return: 返回带 c0/c1 局部选择符的候选 claim 文本。
    :raises SemanticPromptRenderError: Repair 上下文缺失时抛出。
    """
    if context.claim_candidates is None:
        raise SemanticPromptRenderError(
            "inventory repair prompt context is missing claim candidates",
        )
    if not context.claim_candidates:
        return "none"
    return "\n".join(
        f"c{index}: {claim}" for index, claim in enumerate(context.claim_candidates)
    )


def _target_claim_text(context: SkillPromptRepairContext) -> str:
    """读取 Proposition Repair 的唯一目标 claim。

    :param context: 当前 Repair 任务的受限任务内上下文。
    :return: 返回待修复 proposition。
    :raises SemanticPromptRenderError: Repair 上下文缺失时抛出。
    """
    if context.target_claim is None:
        raise SemanticPromptRenderError(
            "proposition repair prompt context is missing target claim",
        )
    return context.target_claim


def _repair_dimension_lines(context: SkillPromptRepairContext) -> str:
    """渲染 M09 声明的修复维度语义先验。

    :param context: 当前 Repair 任务的受限任务内上下文。
    :return: 返回逐行编号的修复维度文本。
    """
    return "\n".join(
        f"{index}. {dimension.value}"
        for index, dimension in enumerate(context.repair_dimensions, start=1)
    )


def _repair_hint_lines(context: SkillPromptRepairContext) -> str:
    """渲染 Coverage Review 提供的非权威修复线索。

    :param context: 当前 Repair 任务的受限任务内上下文。
    :return: 返回带非权威标记的修复提示文本。
    """
    if not context.repair_hints:
        return "none"
    hints = "\n".join(f"- {hint}" for hint in context.repair_hints)
    return f"hint_authority=non_authoritative\n{hints}"


def _validate_projection_resources(request: SkillPromptRenderRequest) -> None:
    """校验受限投影覆盖 SkillSpec 声明的全部 TurnSnapshot 资源。

    :param request: 身份闭合的 M06 渲染请求。
    :return: 无返回值。
    :raises SemanticPromptRenderError: 必需上下文未被投影时抛出。
    """
    included = set(request.projection.included_resources)
    required = {
        resource
        for resource in request.spec.context_contract.required_resources
        if resource in TURN_SNAPSHOT_CONTEXT_RESOURCES
    }
    missing = required - included
    if missing:
        names = ", ".join(resource.value for resource in sorted(missing))
        raise SemanticPromptRenderError(
            f"prompt projection misses required resources: {names}",
        )


def _prompt_variables(
    request: SkillPromptRenderRequest,
    allowed_variables: tuple[str, ...],
) -> dict[str, str]:
    """从受限 TurnSnapshot 投影构造模板变量。

    :param request: 身份闭合的 M06 渲染请求。
    :param allowed_variables: SKILL 文档声明的变量白名单。
    :return: 返回与白名单完全闭合的顶层字符串变量集合。
    :raises SemanticPromptRenderError: 授权变量缺失或未知变量声明时抛出。
    """
    known_variables = {
        "current_turn",
        "last_assistant_questions",
        "verified_prior_facts",
        "trusted_pet_context",
        "generated_claims",
        "claim_proposition",
        "claim_candidates",
        "target_claim",
        "repair_dimensions",
        "repair_hints",
    }
    if (
        "generated_claims" in allowed_variables
        or "claim_proposition" in allowed_variables
    ):
        if request.review_context is None:
            raise SemanticPromptRenderError(
                "review prompt variables require review context",
            )
    elif request.review_context is not None:
        raise SemanticPromptRenderError(
            "review context is not allowed for non-review skill",
        )
    repair_variables = {
        "claim_candidates",
        "target_claim",
        "repair_dimensions",
        "repair_hints",
    }
    if repair_variables & set(allowed_variables):
        if request.repair_context is None:
            raise SemanticPromptRenderError(
                "repair prompt variables require repair context",
            )
    elif request.repair_context is not None:
        raise SemanticPromptRenderError(
            "repair context is not allowed for non-repair skill",
        )
    unknown = set(allowed_variables) - known_variables
    if unknown:
        raise SemanticPromptRenderError(
            "semantic skill declares unknown prompt variable",
        )
    values: dict[str, str] = {}
    for name in allowed_variables:
        if name == "generated_claims":
            if request.review_context is None:
                raise SemanticPromptRenderError(
                    "review prompt renderer requires review context",
                )
            values[name] = _generated_claim_lines(request.review_context)
        elif name == "claim_proposition":
            if request.review_context is None:
                raise SemanticPromptRenderError(
                    "review prompt renderer requires review context",
                )
            values[name] = _claim_proposition_text(request.review_context)
        elif name == "claim_candidates":
            if request.repair_context is None:
                raise SemanticPromptRenderError(
                    "repair prompt renderer requires repair context",
                )
            values[name] = _claim_candidate_lines(request.repair_context)
        elif name == "target_claim":
            if request.repair_context is None:
                raise SemanticPromptRenderError(
                    "repair prompt renderer requires repair context",
                )
            values[name] = _target_claim_text(request.repair_context)
        elif name == "repair_dimensions":
            if request.repair_context is None:
                raise SemanticPromptRenderError(
                    "repair prompt renderer requires repair context",
                )
            values[name] = _repair_dimension_lines(request.repair_context)
        elif name == "repair_hints":
            if request.repair_context is None:
                raise SemanticPromptRenderError(
                    "repair prompt renderer requires repair context",
                )
            values[name] = _repair_hint_lines(request.repair_context)
        elif name == "current_turn":
            values[name] = _current_turn_text(request.projection)
        elif name == "last_assistant_questions":
            values[name] = _question_lines(request.projection)
        elif name == "verified_prior_facts":
            values[name] = _prior_fact_lines(request.projection)
        else:
            values[name] = _pet_context_lines(request.projection)
    return values


def _reject_reserved_tags(values: dict[str, str]) -> None:
    """拒绝动态变量中出现的保留 tag。

    :param values: 待插入模板的顶层字符串变量。
    :return: 无返回值。
    :raises SemanticPromptRenderError: 任一变量包含保留 tag 时抛出。
    """
    for value in values.values():
        for tag in PROMPT_RESERVED_TAGS:
            if f"<{tag}>" in value or f"</{tag}>" in value:
                raise SemanticPromptRenderError(
                    f"prompt context contains reserved tag: {tag}",
                )


def _validate_rendered_tags(
    content: str,
    expected_tags: tuple[str, ...],
) -> None:
    """校验渲染结果中的保留 tag 完整且唯一。

    :param content: 已完成的 user message 文本。
    :param expected_tags: 当前模板应生成的保留 tag 集合。
    :return: 无返回值。
    :raises SemanticPromptRenderError: tag 缺失、重复或不成对时抛出。
    """
    for tag in expected_tags:
        opening_count = content.count(f"<{tag}>")
        closing_count = content.count(f"</{tag}>")
        if opening_count != 1 or closing_count != 1:
            raise SemanticPromptRenderError(
                f"prompt reserved tag is invalid: {tag}",
            )


class StandardSkillPromptRenderer:
    """表示基于标准化 SKILL.md 的 M06 / M08 / M10 提示词渲染器。

    :return: 无返回值；渲染器只消费启动期校验后的文档与受限投影。
    """

    def __init__(self, document: SemanticSkillDocument) -> None:
        """初始化绑定标准化 SKILL 文档的渲染器。

        :param document: 通过元数据与章节校验的生产 SKILL 文档。
        :return: 无返回值。
        """
        self.document = document

    @property
    def task_kind(self) -> SkillTaskKind:
        """读取当前 renderer 绑定的正交任务类型。

        :return: 返回 SKILL 文档元数据中的任务类型。
        """
        return self.document.metadata.task_kind

    @property
    def skill_id(self) -> str:
        """读取当前 renderer 绑定的 SKILL 标识。

        :return: 返回 SKILL 文档元数据中的稳定标识。
        """
        return self.document.metadata.skill_id

    @property
    def skill_version(self) -> str:
        """读取当前 renderer 绑定的 SKILL 版本。

        :return: 返回 SKILL 文档元数据中的精确版本。
        """
        return self.document.metadata.skill_version

    @property
    def prompt_version(self) -> str:
        """读取当前提示词版本。

        :return: 返回独立于 SkillSpec 版本的 prompt 语义版本。
        """
        return self.document.metadata.prompt_version

    def render(self, request: SkillPromptRenderRequest) -> SkillPromptProjection:
        """渲染标准化 SKILL 的受限提示词投影。

        :param request: 身份闭合的 M06 渲染请求。
        :return: 返回可交给 M05 的不可变提示词投影。
        :raises SemanticPromptRenderError: 文档绑定、上下文、模板或 tag 契约失败时抛出。
        """
        self._validate(request)
        variables = _prompt_variables(
            request,
            self.document.metadata.prompt_variables,
        )
        _reject_reserved_tags(variables)
        system_content = self._system_content()
        user_content = RestrictedSkillTemplate(
            self.document.section("Prompt Context Template").content,
            allowed_variables=self.document.metadata.prompt_variables,
        ).render(variables)
        expected_tags = ("task", *self.document.metadata.prompt_variables)
        _validate_rendered_tags(user_content, expected_tags)
        return SkillPromptProjection(
            skill_id=self.skill_id,
            skill_version=self.skill_version,
            prompt_version=self.prompt_version,
            context_digest=request.projection.turn_snapshot_digest,
            messages=(
                SemanticChatMessage(role="system", content=system_content),
                SemanticChatMessage(role="user", content=user_content),
            ),
        )

    def _system_content(self) -> str:
        """组装模型可见的静态规则章节。

        :return: 返回不包含 Prompt Context Template 的 system message 正文。
        """
        sections = []
        for name in self.document.metadata.model_visible_sections:
            if name == "Prompt Context Template":
                continue
            sections.append(self.document.section(name).content)
        return "\n\n".join(sections)

    def _validate(self, request: SkillPromptRenderRequest) -> None:
        """校验渲染请求与 SKILL 文档身份。

        :param request: 身份闭合的 M06 渲染请求。
        :return: 无返回值。
        :raises SemanticPromptRenderError: 文档与 SkillSpec 或任务身份不一致时抛出。
        """
        if (
            request.spec.skill_id != self.skill_id
            or request.spec.skill_version != self.skill_version
            or request.spec.task_kind != self.task_kind
        ):
            raise SemanticPromptRenderError("standard skill renderer mismatch")
        self.document.validate_against_spec(request.spec)
        _validate_projection_resources(request)


class TurnIntentPromptRenderer(StandardSkillPromptRenderer):
    """表示 Turn Intent standardized SKILL 渲染器。

    :return: 无返回值；该渲染器只输出回合级意图规则。
    """

    def __init__(self) -> None:
        """加载生产包内的 Turn Intent SKILL.md。

        :return: 无返回值。
        """
        super().__init__(load_semantic_skill_document("turn_intent"))


class ClaimPropositionInventoryPromptRenderer(StandardSkillPromptRenderer):
    """表示 Claim Proposition Inventory standardized SKILL 渲染器。

    :return: 无返回值；该渲染器只输出自然语言 claim proposition 规则。
    """

    def __init__(self) -> None:
        """加载生产包内的 Claim Inventory SKILL.md。

        :return: 无返回值。
        """
        super().__init__(load_semantic_skill_document("claim_inventory"))


class ClaimCoverageReviewPromptRenderer(StandardSkillPromptRenderer):
    """表示 Coverage Review standardized SKILL 渲染器。

    :return: 无返回值；该渲染器只输出回合级覆盖审查矩阵规则。
    """

    def __init__(self) -> None:
        """加载生产包内的 Coverage Review SKILL.md。

        :return: 无返回值。
        """
        super().__init__(load_semantic_skill_document("claim_coverage_review"))


class ClaimFaithfulnessReviewPromptRenderer(StandardSkillPromptRenderer):
    """表示 Faithfulness Review standardized SKILL 渲染器。

    :return: 无返回值；该渲染器只输出单 claim 忠实性审查矩阵规则。
    """

    def __init__(self) -> None:
        """加载生产包内的 Faithfulness Review SKILL.md。

        :return: 无返回值。
        """
        super().__init__(load_semantic_skill_document("claim_faithfulness_review"))


class ClaimInventoryRepairPromptRenderer(StandardSkillPromptRenderer):
    """表示 Claim Inventory Repair standardized SKILL 渲染器。

    :return: 无返回值；该渲染器只输出稀疏 delta 修复规则。
    """

    def __init__(self) -> None:
        """加载生产包内的 Claim Inventory Repair SKILL.md。

        :return: 无返回值。
        """
        super().__init__(load_semantic_skill_document("claim_inventory_repair"))


class ClaimPropositionRepairPromptRenderer(StandardSkillPromptRenderer):
    """表示 Claim Proposition Repair standardized SKILL 渲染器。

    :return: 无返回值；该渲染器只输出单 proposition 修复规则。
    """

    def __init__(self) -> None:
        """加载生产包内的 Claim Proposition Repair SKILL.md。

        :return: 无返回值。
        """
        super().__init__(load_semantic_skill_document("claim_proposition_repair"))


class SkillPromptRendererRegistry:
    """表示 M06 生产 renderer 的不可变注册目录。

    :return: 无返回值；目录启动期闭合，禁止运行时动态发明提示词。
    """

    def __init__(self, renderers: Iterable[SkillPromptRenderer]) -> None:
        """初始化并冻结生产 renderer 目录。

        :param renderers: 当前生产面全部版本化 renderer。
        :raises SemanticPromptRenderError: 身份重复或字段缺失时抛出。
        """
        self._renderers: dict[tuple[str, str], SkillPromptRenderer] = {}
        for renderer in renderers:
            identity = (renderer.skill_id, renderer.skill_version)
            if identity in self._renderers:
                raise SemanticPromptRenderError(
                    "duplicate semantic prompt renderer identity",
                )
            if not renderer.prompt_version:
                raise SemanticPromptRenderError(
                    "semantic prompt renderer requires prompt version",
                )
            self._renderers[identity] = renderer

    def require(
        self,
        skill_id: str,
        skill_version: str,
    ) -> SkillPromptRenderer:
        """按精确 SKILL 身份解析 renderer。

        :param skill_id: 生产 SKILL 稳定标识。
        :param skill_version: 生产 SKILL 精确版本。
        :return: 返回绑定的版本化 renderer。
        :raises SemanticPromptRenderError: renderer 缺失时抛出。
        """
        renderer = self._renderers.get((skill_id, skill_version))
        if renderer is None:
            raise SemanticPromptRenderError(
                "semantic prompt renderer is not registered",
            )
        return renderer

    def identities(self) -> tuple[tuple[str, str], ...]:
        """读取全部已注册 renderer 身份。

        :return: 返回按插入顺序保留的 SKILL 身份元组。
        """
        return tuple(self._renderers)

    def validate_catalog(
        self,
        specs: Iterable[SkillSpec],
    ) -> None:
        """校验生产 SkillCatalog 与 renderer 目录一一闭合。

        :param specs: 生产 SkillCatalog 中的全部 SkillSpec。
        :return: 无返回值。
        :raises SemanticPromptRenderError: 缺失 renderer、未知 renderer 或任务类型不匹配时抛出。
        """
        materialized_specs = tuple(specs)
        model_backed_identities = {
            (spec.skill_id, spec.skill_version)
            for spec in materialized_specs
            if spec.execution_family
            in {
                SkillExecutionFamily.STRUCTURED_GENERATION,
                SkillExecutionFamily.STRUCTURED_REVIEW,
                SkillExecutionFamily.STRUCTURED_REPAIR,
            }
        }
        renderer_identities = set(self._renderers)
        if model_backed_identities != renderer_identities:
            raise SemanticPromptRenderError(
                "semantic prompt renderer catalog is not closed",
            )
        renderer_by_identity = {
            (renderer.skill_id, renderer.skill_version): renderer
            for renderer in self._renderers.values()
        }
        for identity in model_backed_identities:
            renderer = renderer_by_identity[identity]
            spec = next(
                item
                for item in materialized_specs
                if (item.skill_id, item.skill_version) == identity
            )
            if renderer.task_kind != spec.task_kind:
                raise SemanticPromptRenderError(
                    "semantic prompt renderer task kind mismatch",
                )


def build_production_prompt_renderer_registry() -> SkillPromptRendererRegistry:
    """构建当前 M06 生产面的不可变 renderer 目录。

    :return: 返回包含 Turn Intent 与 Claim Proposition Inventory renderer 的目录。
    """
    return SkillPromptRendererRegistry(
        (
            TurnIntentPromptRenderer(),
            ClaimPropositionInventoryPromptRenderer(),
            ClaimCoverageReviewPromptRenderer(),
            ClaimFaithfulnessReviewPromptRenderer(),
            ClaimInventoryRepairPromptRenderer(),
            ClaimPropositionRepairPromptRenderer(),
        ),
    )

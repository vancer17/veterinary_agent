"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/prompt_renderer.py
作用：实现受限语义协作 DAG M06 的标准化 SKILL 文档提示词渲染器。
范围：覆盖渲染请求身份校验、受限上下文投影、SKILL 文档绑定、模型可见
      章节选择、受限 Jinja 变量替换、tag 冲突校验与 renderer 目录闭合。
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
)


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
        return self


class SkillPromptRenderer(Protocol):
    """表示 M06 版本化提示词渲染端口。

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
        f"{index}. {question.text}"
        for index, question in enumerate(questions, start=1)
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
    renderers = {
        "current_turn": _current_turn_text,
        "last_assistant_questions": _question_lines,
        "verified_prior_facts": _prior_fact_lines,
        "trusted_pet_context": _pet_context_lines,
    }
    unknown = set(allowed_variables) - set(renderers)
    if unknown:
        raise SemanticPromptRenderError(
            "semantic skill declares unknown prompt variable",
        )
    return {
        name: renderers[name](request.projection)
        for name in allowed_variables
    }


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
    """表示基于标准化 SKILL.md 的 M06 提示词渲染器。

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
        generation_identities = {
            (spec.skill_id, spec.skill_version)
            for spec in materialized_specs
            if spec.execution_family is SkillExecutionFamily.STRUCTURED_GENERATION
        }
        renderer_identities = set(self._renderers)
        if generation_identities != renderer_identities:
            raise SemanticPromptRenderError(
                "semantic prompt renderer catalog is not closed",
            )
        renderer_by_identity = {
            (renderer.skill_id, renderer.skill_version): renderer
            for renderer in self._renderers.values()
        }
        for identity in generation_identities:
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
        ),
    )

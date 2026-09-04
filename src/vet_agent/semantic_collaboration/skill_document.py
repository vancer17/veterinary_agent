"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/skill_document.py
作用：加载并校验受限语义协作 DAG 的标准化 SKILL.md 生产文档。
范围：覆盖静态 front matter 契约、标准章节解析、模型可见章节白名单、
      SkillSpec / schema / context / verifier 绑定校验和包内资源读取。
说明：文件头部元数据仅确定性代码可见；正文只有显式声明的章节才会进入
      Prompt Renderer，Markdown 不作为字段所有权或上下文权限权威。
=============================================================================
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import (
    SkillContextResource,
    SkillDocProjection,
    SkillExecutionFamily,
    SkillSpec,
    SkillTaskKind,
)
from .errors import SemanticSkillDocumentError

SKILL_DOCUMENT_SECTIONS: tuple[str, ...] = (
    "Identity",
    "Scope",
    "Context Policy",
    "Output Authority",
    "Failure And Repair",
    "Role",
    "Workflow",
    "Output Constraints",
    "Exception And Boundary Rules",
    "Memory And Context Rules",
    "Prompt Context Template",
    "Safety Boundary",
)

SKILL_DOCUMENT_REQUIRED_MODEL_SECTIONS: tuple[str, ...] = (
    "Role",
    "Workflow",
    "Output Constraints",
    "Exception And Boundary Rules",
    "Memory And Context Rules",
    "Prompt Context Template",
    "Safety Boundary",
)

ALLOWED_SKILL_PROMPT_VARIABLES: tuple[str, ...] = (
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


class SemanticSkillDocumentMetadata(BaseModel):
    """表示 SKILL.md 文件头部静态元数据的机器可读契约。

    :return: 无返回值；该元数据不进入模型 prompt。
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
    )
    skill_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    prompt_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    task_kind: SkillTaskKind
    execution_family: SkillExecutionFamily
    verifier_id: str = Field(min_length=1, max_length=200)
    verifier_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    output_schema_id: str = Field(min_length=1, max_length=200)
    output_schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    context_resources: tuple[SkillContextResource, ...] = Field(min_length=1)
    prompt_variables: tuple[str, ...] = Field(min_length=1)
    model_visible_sections: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_document_metadata(self) -> Self:
        """校验元数据集合唯一且只使用受支持的 prompt 变量。

        :return: 返回通过封闭集合校验的静态元数据。
        :raises ValueError: 集合存在重复、未知变量或缺少必需模型章节时抛出。
        """
        collections: tuple[tuple[str, ...], ...] = (
            tuple(resource.value for resource in self.context_resources),
            self.prompt_variables,
            self.model_visible_sections,
        )
        if any(len(values) != len(set(values)) for values in collections):
            raise ValueError("semantic skill metadata collection is duplicate")
        unknown_variables = set(self.prompt_variables) - set(
            ALLOWED_SKILL_PROMPT_VARIABLES,
        )
        if unknown_variables:
            raise ValueError("semantic skill prompt variable is unknown")
        missing_sections = set(SKILL_DOCUMENT_REQUIRED_MODEL_SECTIONS) - set(
            self.model_visible_sections,
        )
        if missing_sections:
            raise ValueError("semantic skill model-visible section is missing")
        return self


class SemanticSkillDocumentSection(BaseModel):
    """表示标准化 SKILL.md 中的一个二级章节。

    :return: 无返回值；章节正文仅在显式声明后进入模型。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    name: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1)


class SemanticSkillDocument(BaseModel):
    """表示通过启动期校验的标准化生产 SKILL 文档。

    :return: 无返回值；该对象是版本化 prompt 来源，不是运行时契约权威。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
        arbitrary_types_allowed=True,
    )

    projection: SkillDocProjection
    metadata: SemanticSkillDocumentMetadata
    sections: tuple[SemanticSkillDocumentSection, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sections(self) -> Self:
        """校验章节集合封闭、唯一且与模型可见声明一致。

        :return: 返回通过章节闭合校验的 SKILL 文档。
        :raises SemanticSkillDocumentError: 章节缺失、重复、未知或不可见时抛出。
        """
        names = tuple(section.name for section in self.sections)
        if names != SKILL_DOCUMENT_SECTIONS:
            missing = set(SKILL_DOCUMENT_SECTIONS) - set(names)
            unknown = set(names) - set(SKILL_DOCUMENT_SECTIONS)
            if missing or unknown or len(names) != len(set(names)):
                raise SemanticSkillDocumentError(
                    "semantic skill document sections are not closed",
                )
        visible = set(self.metadata.model_visible_sections)
        if "Prompt Context Template" not in visible:
            raise SemanticSkillDocumentError(
                "semantic skill prompt template is not model-visible",
            )
        return self

    def section(self, name: str) -> SemanticSkillDocumentSection:
        """按名称读取标准化 SKILL 章节。

        :param name: 标准章节名称。
        :return: 返回对应的不可变章节。
        :raises SemanticSkillDocumentError: 章节不存在时抛出。
        """
        for section in self.sections:
            if section.name == name:
                return section
        raise SemanticSkillDocumentError("semantic skill section is not found")

    def validate_against_spec(self, spec: SkillSpec) -> None:
        """校验 SKILL 文档元数据与权威 SkillSpec 完全绑定。

        :param spec: SkillCatalog 解析出的权威机器契约。
        :return: 无返回值。
        :raises SemanticSkillDocumentError: 身份、schema、上下文或 verifier 不一致时抛出。
        """
        metadata = self.metadata
        expected_context = tuple(spec.context_contract.required_resources)
        if (
            metadata.skill_id,
            metadata.skill_version,
            metadata.task_kind,
            metadata.execution_family,
            metadata.verifier_id,
            metadata.verifier_version,
            metadata.output_schema_id,
            metadata.output_schema_version,
            metadata.context_resources,
        ) != (
            spec.skill_id,
            spec.skill_version,
            spec.task_kind,
            spec.execution_family,
            spec.verifier_binding.verifier_id,
            spec.verifier_binding.verifier_version,
            spec.output_contract.schema_id,
            spec.output_contract.schema_version,
            expected_context,
        ):
            raise SemanticSkillDocumentError(
                "semantic skill document does not match SkillSpec",
            )

    @classmethod
    def from_markdown(cls, document: str) -> SemanticSkillDocument:
        """解析并校验一份标准化 SKILL.md 全文。

        :param document: 包含静态 front matter 与标准章节的 Markdown 文本。
        :return: 返回通过机器契约校验的 SKILL 文档。
        :raises SemanticSkillDocumentError: front matter、章节或摘要非法时抛出。
        """
        try:
            projection = SkillDocProjection(
                document=document,
                content_sha256=sha256(document.encode("utf-8")).hexdigest(),
            )
            raw_metadata = projection.frontmatter()
            metadata = SemanticSkillDocumentMetadata(
                skill_id=_required_metadata(raw_metadata, "skill_id"),
                skill_version=_required_metadata(raw_metadata, "skill_version"),
                prompt_version=_required_metadata(raw_metadata, "prompt_version"),
                task_kind=SkillTaskKind(
                    _required_metadata(raw_metadata, "task_kind"),
                ),
                execution_family=SkillExecutionFamily(
                    _required_metadata(raw_metadata, "execution_family"),
                ),
                verifier_id=_required_metadata(raw_metadata, "verifier_id"),
                verifier_version=_required_metadata(
                    raw_metadata,
                    "verifier_version",
                ),
                output_schema_id=_required_metadata(
                    raw_metadata,
                    "output_schema_id",
                ),
                output_schema_version=_required_metadata(
                    raw_metadata,
                    "output_schema_version",
                ),
                context_resources=tuple(
                    SkillContextResource(value)
                    for value in _csv_values(
                        _required_metadata(raw_metadata, "context_resources"),
                    )
                ),
                prompt_variables=_csv_values(
                    _required_metadata(raw_metadata, "prompt_variables"),
                ),
                model_visible_sections=_section_values(
                    _required_metadata(raw_metadata, "model_visible_sections"),
                ),
            )
            return cls(
                projection=projection,
                metadata=metadata,
                sections=_parse_sections(projection.document),
            )
        except SemanticSkillDocumentError:
            raise
        except Exception as error:
            raise SemanticSkillDocumentError(
                "semantic skill document contract is invalid",
            ) from error


def _required_metadata(metadata: Mapping[str, str], key: str) -> str:
    """读取必需的静态 front matter 字段。

    :param metadata: 已解析的 front matter 字符串映射。
    :param key: 必需字段名。
    :return: 返回非空字段值。
    :raises SemanticSkillDocumentError: 字段缺失或为空时抛出。
    """
    value = metadata.get(key)
    if value is None or not value.strip():
        raise SemanticSkillDocumentError(
            f"semantic skill document metadata is missing: {key}",
        )
    return value.strip()


def _csv_values(value: str) -> tuple[str, ...]:
    """解析逗号分隔的静态元数据集合。

    :param value: front matter 中的原始逗号分隔字符串。
    :return: 返回去空白后的字符串元组。
    :raises SemanticSkillDocumentError: 集合为空或存在空项时抛出。
    """
    values = tuple(
        item.strip()
        for item in value.replace("，", ",").split(",")
    )
    if not values or any(not item for item in values):
        raise SemanticSkillDocumentError(
            "semantic skill metadata CSV value is invalid",
        )
    return values


def _section_values(value: str) -> tuple[str, ...]:
    """解析模型可见章节的静态声明。

    :param value: front matter 中的原始章节列表字符串。
    :return: 返回标准章节名称元组。
    :raises SemanticSkillDocumentError: 声明未知章节时抛出。
    """
    sections = _csv_values(value)
    unknown = set(sections) - set(SKILL_DOCUMENT_SECTIONS)
    if unknown:
        raise SemanticSkillDocumentError(
            "semantic skill model-visible section is unknown",
        )
    return sections


def _parse_sections(document: str) -> tuple[SemanticSkillDocumentSection, ...]:
    """按固定二级标题解析 SKILL.md 正文章节。

    :param document: SKILL.md 全文。
    :return: 返回按文档顺序排列的章节集合。
    :raises SemanticSkillDocumentError: front matter 结束符或章节结构非法时抛出。
    """
    lines = document.splitlines()
    try:
        end_index = lines[1:].index("---") + 1
    except ValueError as error:
        raise SemanticSkillDocumentError(
            "semantic skill document frontmatter is unterminated",
        ) from error
    sections: list[SemanticSkillDocumentSection] = []
    current_name: str | None = None
    current_lines: list[str] = []
    for line in lines[end_index + 1 :]:
        if line.startswith("## "):
            if current_name is not None:
                sections.append(
                    SemanticSkillDocumentSection(
                        name=current_name,
                        content="\n".join(current_lines).strip(),
                    ),
                )
            current_name = line.removeprefix("## ").strip()
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)
    if current_name is not None:
        sections.append(
            SemanticSkillDocumentSection(
                name=current_name,
                content="\n".join(current_lines).strip(),
            ),
        )
    if not sections or any(not section.content for section in sections):
        raise SemanticSkillDocumentError(
            "semantic skill document section is empty",
        )
    return tuple(sections)


def load_semantic_skill_document(skill_id: str) -> SemanticSkillDocument:
    """从生产包资源中读取标准化 SKILL.md。

    :param skill_id: 生产 SKILL 稳定标识。
    :return: 返回通过机器契约校验的标准化 SKILL 文档。
    :raises SemanticSkillDocumentError: 资源缺失或文档契约非法时抛出。
    """
    resource: Traversable = files(__package__).joinpath(
        "skills",
        skill_id,
        "SKILL.md",
    )
    if not resource.is_file():
        raise SemanticSkillDocumentError(
            "semantic skill document resource is not found",
        )
    return SemanticSkillDocument.from_markdown(resource.read_text("utf-8"))

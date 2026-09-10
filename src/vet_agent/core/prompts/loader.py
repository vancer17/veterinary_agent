"""
文件系统 prompt 资产加载器。

该模块把语义层维护的 ``spec.json`` 与 ``template.md`` 加载为不可变
``LoadedPromptAsset``。加载器只执行路径、字节、strict JSON、章节、占位符
和消息组装协议校验，不理解也不修改任何兽医语义 prompt 文案。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from vet_agent.core import errors as core_errors
from vet_agent.core.prompts.contracts import (
    LoadedPromptAsset,
    PromptAssetSpec,
    PromptMessageRole,
    PromptMessageSpec,
    PromptPlaceholderKind,
    PromptPlaceholderSpec,
    PromptTemplateKind,
)
from vet_agent.core.schema import StrictJSONGateway, StrictModel

_SECTION_HEADING_PATTERN = re.compile(
    pattern=r"^(?P<marker>#{1,2})\s+(?P<title>\S.*?)\s*$",
)
_PLACEHOLDER_PATTERN = re.compile(
    pattern=r"\{\{\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\}\}",
)
_PROMPT_ID_PATTERN = re.compile(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_PROMPT_VERSION_PATTERN = re.compile(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_PLACEHOLDER_NAME_PATTERN = re.compile(pattern=r"^[a-z][a-z0-9_]*$")
_FORBIDDEN_PLACEHOLDER_NAMES = frozenset(
    {
        "api_key",
        "attempt_id",
        "authorization",
        "correlation_id",
        "database_dsn",
        "execution_id",
        "password",
        "pet_id",
        "request_id",
        "run_id",
        "secret",
        "token",
        "user_id",
    },
)
_REQUIRED_SECTIONS = frozenset(
    {
        "Role",
        "Workflow",
        "Output Constraints",
        "Exception And Boundary Rules",
        "Context Rules",
        "Prompt Context Template",
        "Safety Boundary",
    },
)
_MAX_RENDERED_PLACEHOLDER_CHARS = 200_000


class _PromptPlaceholderSpecFile(StrictModel):
    """JSON 文件中的占位符规格输入形态。

    文件输入使用字符串与列表，加载后再转换为带枚举和 tuple 的公共规格，
    避免 strict Pydantic 对 JSON 原生 list / string 做隐式转换。

    :ivar name: JSON 文件中的占位符名。
    :ivar kind: JSON 文件中的占位符类型字符串。
    :ivar max_rendered_chars: JSON 文件中的最大渲染字符数。
    """

    name: str
    kind: str
    max_rendered_chars: int


class _PromptMessageSpecFile(StrictModel):
    """JSON 文件中的消息规格输入形态。

    :ivar role: JSON 文件中的消息角色字符串。
    :ivar sections: JSON 文件中的章节标题列表。
    """

    role: str
    sections: list[str]


class _PromptAssetSpecFile(StrictModel):
    """JSON 文件中的 prompt 资产规格输入形态。

    该模型只承担 strict JSON 解析；工程闭合校验由后续加载流程完成。

    :ivar prompt_id: JSON 文件中的 prompt 标识。
    :ivar prompt_version: JSON 文件中的 prompt 版本。
    :ivar template_kind: JSON 文件中的模板类型字符串。
    :ivar model_role: JSON 文件中的稳定模型角色名。
    :ivar output_schema_id: JSON 文件中的输出 schema 目录键。
    :ivar allowed_sections: JSON 文件中的允许章节标题列表。
    :ivar messages: JSON 文件中的 system / user 消息组装列表。
    :ivar placeholders: JSON 文件中的受限占位符列表。
    """

    prompt_id: str
    prompt_version: str
    template_kind: str
    model_role: str
    output_schema_id: str
    allowed_sections: list[str]
    messages: list[_PromptMessageSpecFile]
    placeholders: list[_PromptPlaceholderSpecFile]


def _sha256_bytes(content: bytes) -> str:
    """计算字节内容的 SHA-256 十六进制摘要。

    :param content: 原始 spec 或模板文件字节。
    :return: 64 位十六进制摘要，用于版本审计和启动期一致性校验。
    """
    return hashlib.sha256(content).hexdigest()


def _normalize_asset_spec(source: _PromptAssetSpecFile) -> PromptAssetSpec:
    """将文件输入规格转换为公共不可变枚举与 tuple 形态。

    :param source: strict JSON 解析得到的文件输入模型。
    :return: 可进入闭合校验的公共 prompt 资产规格。
    :raises ValueError: 当文件中的受控枚举值不存在。
    """
    return PromptAssetSpec(
        prompt_id=source.prompt_id,
        prompt_version=source.prompt_version,
        template_kind=PromptTemplateKind(source.template_kind),
        model_role=source.model_role,
        output_schema_id=source.output_schema_id,
        allowed_sections=tuple(source.allowed_sections),
        messages=tuple(
            PromptMessageSpec(
                role=PromptMessageRole(message.role),
                sections=tuple(message.sections),
            )
            for message in source.messages
        ),
        placeholders=tuple(
            PromptPlaceholderSpec(
                name=placeholder.name,
                kind=PromptPlaceholderKind(placeholder.kind),
                max_rendered_chars=placeholder.max_rendered_chars,
            )
            for placeholder in source.placeholders
        ),
    )


def _extract_placeholder_names(content: str) -> set[str]:
    """提取一个模板章节中声明的受限占位符名。

    :param content: 单个章节正文。
    :return: 去重后的占位符名集合。
    """
    return {
        match.group("name")
        for match in _PLACEHOLDER_PATTERN.finditer(string=content)
    }


def _append_parsed_section(
    *,
    sections: dict[str, str],
    title: str,
    lines: list[str],
) -> None:
    """保存一个已解析的模板章节。

    :param sections: 解析过程中的可变章节收集器。
    :param title: ATX 标题文本。
    :param lines: 该章节的原始正文行。
    :return: 无返回值；章节合法时写入收集器。
    :raises core_errors.ConfigurationError: 当章节标题重复或正文为空。
    """
    body = "".join(lines).strip()
    if title in sections:
        raise core_errors.ConfigurationError(
            error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
            message="Prompt 模板章节重复",
            context={"section": title},
        )
    if not body:
        raise core_errors.ConfigurationError(
            error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
            message="Prompt 模板章节为空",
            context={"section": title},
        )
    sections[title] = body


def _parse_template_sections(template: str) -> Mapping[str, str]:
    """解析 SKILL-like Markdown 模板的顶层章节。

    :param template: 原始 UTF-8 模板文本。
    :return: 标题到正文正文的不可变映射。
    :raises core_errors.ConfigurationError: 当模板包含代码块、非章节前言、
        不受支持的标题层级、混合标题层级、重复章节或空章节。
    """
    sections: dict[str, str] = {}
    current_title: str | None = None
    current_lines: list[str] = []
    heading_marker_length: int | None = None

    for raw_line in template.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        if line.lstrip().startswith("```"):
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Prompt 模板不允许使用 Markdown 代码块",
                context={"parse_stage": "template_section"},
            )
        if line.startswith("#"):
            match = _SECTION_HEADING_PATTERN.match(string=line)
            if match is None:
                raise core_errors.ConfigurationError(
                    error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                    message="Prompt 模板包含不受支持的标题层级",
                    context={"parse_stage": "template_section"},
                )
            marker_length = len(match.group("marker"))
            title = match.group("title")
            if heading_marker_length is None:
                heading_marker_length = marker_length
            elif heading_marker_length != marker_length:
                raise core_errors.ConfigurationError(
                    error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                    message="Prompt 模板混合使用一级与二级标题",
                    context={"parse_stage": "template_section"},
                )
            if current_title is not None:
                _append_parsed_section(
                    sections=sections,
                    title=current_title,
                    lines=current_lines,
                )
            current_title = title
            current_lines = []
            continue
        if current_title is None and line.strip():
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Prompt 模板不允许存在章节前言",
                context={"parse_stage": "template_section"},
            )
        if current_title is not None:
            current_lines.append(raw_line)

    if current_title is not None:
        _append_parsed_section(
            sections=sections,
            title=current_title,
            lines=current_lines,
        )
    if not sections:
        raise core_errors.ConfigurationError(
            error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
            message="Prompt 模板不包含章节",
            context={"parse_stage": "template_section"},
        )
    return MappingProxyType(sections)


def _validate_asset_spec(
    *,
    spec: PromptAssetSpec,
    sections: Mapping[str, str],
) -> None:
    """校验 prompt spec 与模板章节之间的闭合关系。

    :param spec: 已通过 strict schema 校验的资产规格。
    :param sections: 模板解析得到的章节正文。
    :return: 无返回值；工程协议闭合时允许构造资产。
    :raises core_errors.ConfigurationError: 当标识、章节、消息、占位符或
        模板格式违反受控协议。
    """
    invalid_reasons: list[str] = []
    if _PROMPT_ID_PATTERN.fullmatch(string=spec.prompt_id) is None:
        invalid_reasons.append("prompt_id_invalid")
    if (
        _PROMPT_VERSION_PATTERN.fullmatch(string=spec.prompt_version) is None
        or spec.prompt_version.casefold() == "latest"
    ):
        invalid_reasons.append("prompt_version_invalid")
    if spec.template_kind is not PromptTemplateKind.SECTIONED_MARKDOWN_V1:
        invalid_reasons.append("template_kind_unsupported")
    if not spec.model_role:
        invalid_reasons.append("model_role_empty")
    if not spec.output_schema_id:
        invalid_reasons.append("output_schema_id_empty")

    allowed_sections = tuple(spec.allowed_sections)
    if not allowed_sections or len(allowed_sections) != len(set(allowed_sections)):
        invalid_reasons.append("allowed_sections_invalid")
    elif not _REQUIRED_SECTIONS.issubset(set(allowed_sections)):
        invalid_reasons.append("required_sections_missing")
    elif not set(allowed_sections).issubset(sections.keys()):
        invalid_reasons.append("allowed_sections_not_in_template")
    elif not set(sections.keys()).issubset(set(allowed_sections)):
        invalid_reasons.append("template_sections_not_declared")

    if tuple(message.role for message in spec.messages) != (
        PromptMessageRole.SYSTEM,
        PromptMessageRole.USER,
    ):
        invalid_reasons.append("message_roles_invalid")
    else:
        referenced_sections = [
            section
            for message in spec.messages
            for section in message.sections
        ]
        if (
            not referenced_sections
            or len(referenced_sections) != len(set(referenced_sections))
            or set(referenced_sections) != set(allowed_sections)
        ):
            invalid_reasons.append("message_sections_not_closed")

        system_sections = spec.messages[0].sections
        user_sections = spec.messages[1].sections
        if "Prompt Context Template" in system_sections:
            invalid_reasons.append("context_template_in_system")
        if "Prompt Context Template" not in user_sections:
            invalid_reasons.append("context_template_missing_in_user")

        system_placeholder_names = {
            name
            for section in system_sections
            for name in _extract_placeholder_names(sections.get(section, ""))
        }
        if system_placeholder_names:
            invalid_reasons.append("placeholder_in_system_message")

    placeholder_specs = tuple(spec.placeholders)
    placeholder_names = tuple(placeholder.name for placeholder in placeholder_specs)
    declared_placeholder_names = set(placeholder_names)
    used_placeholder_names = {
        name
        for section in sections.values()
        for name in _extract_placeholder_names(section)
    }
    if not placeholder_specs or len(placeholder_names) != len(
        declared_placeholder_names
    ):
        invalid_reasons.append("placeholders_duplicated_or_empty")
    if used_placeholder_names != declared_placeholder_names:
        invalid_reasons.append("placeholders_not_closed")
    for placeholder in placeholder_specs:
        if _PLACEHOLDER_NAME_PATTERN.fullmatch(string=placeholder.name) is None:
            invalid_reasons.append(f"placeholder_name_invalid:{placeholder.name}")
        if placeholder.name in _FORBIDDEN_PLACEHOLDER_NAMES:
            invalid_reasons.append(f"placeholder_forbidden:{placeholder.name}")
        if (
            placeholder.max_rendered_chars <= 0
            or placeholder.max_rendered_chars > _MAX_RENDERED_PLACEHOLDER_CHARS
        ):
            invalid_reasons.append(
                f"placeholder_max_chars_invalid:{placeholder.name}",
            )
        if placeholder.kind not in PromptPlaceholderKind:
            invalid_reasons.append(f"placeholder_kind_invalid:{placeholder.name}")

    if invalid_reasons:
        raise core_errors.ConfigurationError(
            error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
            message="Prompt 资产规格非法",
            context={
                "prompt_id": spec.prompt_id or "<empty>",
                "reasons": ",".join(invalid_reasons),
            },
        )


def _read_limited_file(
    *,
    path: Path,
    max_bytes: int,
) -> tuple[bytes, str]:
    """读取受大小限制的 UTF-8 文件。

    :param path: 待读取文件路径。
    :param max_bytes: 单文件允许的最大字节数。
    :return: 原始字节与解码后 UTF-8 文本。
    :raises core_errors.ConfigurationError: 当文件缺失、过大或不是合法 UTF-8。
    """
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise core_errors.ConfigurationError(
            error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
            message="Prompt 资产文件不可读取",
            context={"file_name": path.name},
            cause=exc,
        ) from exc
    if len(content) > max_bytes:
        raise core_errors.ConfigurationError(
            error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
            message="Prompt 资产文件超过大小限制",
            context={"file_name": path.name},
        )
    try:
        text = content.decode(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise core_errors.ConfigurationError(
            error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
            message="Prompt 资产文件不是 UTF-8 文本",
            context={"file_name": path.name},
            cause=exc,
        ) from exc
    return content, text


class PromptAssetLoader:
    """从受控目录加载版本化 prompt 资产。

    加载器位于 composition root 之后、模型调用之前，是 prompt 文件进入核心
    执行链路的唯一文件系统入口。
    """

    def __init__(self, *, max_file_bytes: int = 1_048_576) -> None:
        """初始化 prompt 资产加载器。

        :param max_file_bytes: 单个 spec 或模板文件允许的最大字节数。
        :return: 无返回值；保存受限读取配置。
        :raises core_errors.ConfigurationError: 当文件大小上限不合法。
        """
        if max_file_bytes <= 0:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Prompt 资产文件大小上限非法",
                context={"reasons": "max_file_bytes_not_positive"},
            )
        self._max_file_bytes = max_file_bytes
        self._json_gateway = StrictJSONGateway()

    def load(
        self,
        *,
        spec_path: Path,
        template_path: Path,
    ) -> LoadedPromptAsset:
        """加载并校验一对 prompt spec 与模板文件。

        :param spec_path: ``spec.json`` 文件路径。
        :param template_path: 同版本目录下的 ``template.md`` 文件路径。
        :return: 加载成功的不可变 prompt 资产。
        :raises core_errors.ConfigurationError: 当文件名、JSON、schema、章节、
            占位符或消息组装协议非法。
        """
        if spec_path.name != "spec.json" or template_path.name != "template.md":
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Prompt 资产文件名不符合协议",
                context={"reasons": "asset_file_name_invalid"},
            )
        if spec_path.parent.resolve() != template_path.parent.resolve():
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Prompt spec 与 template 不在同一版本目录",
                context={"reasons": "asset_directory_mismatch"},
            )
        try:
            spec_bytes, spec_text = _read_limited_file(
                path=spec_path,
                max_bytes=self._max_file_bytes,
            )
            template_bytes, template = _read_limited_file(
                path=template_path,
                max_bytes=self._max_file_bytes,
            )
            spec = self._json_gateway.parse(
                content=spec_text,
                output_schema=_PromptAssetSpecFile,
            )
            normalized_spec = _normalize_asset_spec(spec)
            sections = _parse_template_sections(template)
            _validate_asset_spec(spec=normalized_spec, sections=sections)
        except (core_errors.CoreError, ValueError) as exc:
            reason = (
                str(exc.context.get("reasons", exc.error_code.value))
                if isinstance(exc, core_errors.CoreError)
                else "spec_enum_invalid"
            )
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Prompt 资产加载失败",
                context={
                    "spec_file": spec_path.name,
                    "reasons": reason,
                },
                cause=exc,
            ) from exc

        return LoadedPromptAsset(
            spec=normalized_spec,
            template=template,
            template_sha256=_sha256_bytes(template_bytes),
            spec_sha256=_sha256_bytes(spec_bytes),
            sections=MappingProxyType(dict(sections)),
        )

    def load_directory(self, *, directory: Path) -> LoadedPromptAsset:
        """加载一个 prompt 版本目录。

        :param directory: 必须直接包含 ``spec.json`` 与 ``template.md`` 的目录。
        :return: 加载成功的不可变 prompt 资产。
        :raises core_errors.ConfigurationError: 当目录或文件缺失、越界或非法。
        """
        return self.load(
            spec_path=directory / "spec.json",
            template_path=directory / "template.md",
        )

    def load_all(self, *, roots: tuple[Path, ...]) -> tuple[LoadedPromptAsset, ...]:
        """递归扫描多个 prompt 根目录并加载全部资产。

        :param roots: 语义模块拥有的 prompt 资产根目录。
        :return: 按扫描顺序返回的全部不可变 prompt 资产。
        :raises core_errors.ConfigurationError: 当根目录缺失、资产越界、重复、
            缺失或协议非法。
        """
        if not roots:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Prompt 资产根目录为空",
                context={"reasons": "prompt_roots_empty"},
            )
        assets: list[LoadedPromptAsset] = []
        seen_assets: set[tuple[str, str]] = set()
        for root in roots:
            resolved_root = root.resolve()
            if not resolved_root.is_dir():
                raise core_errors.ConfigurationError(
                    error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                    message="Prompt 资产根目录不存在",
                    context={"root_name": root.name},
                )
            spec_paths = tuple(resolved_root.rglob(pattern="spec.json"))
            if not spec_paths:
                raise core_errors.ConfigurationError(
                    error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                    message="Prompt 资产根目录不包含资产",
                    context={"root_name": root.name},
                )
            for spec_path in spec_paths:
                if spec_path.is_symlink():
                    raise core_errors.ConfigurationError(
                        error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                        message="Prompt spec 不允许使用符号链接",
                        context={"root_name": root.name},
                    )
                if not spec_path.resolve().is_relative_to(resolved_root):
                    raise core_errors.ConfigurationError(
                        error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                        message="Prompt spec 越过资产根目录",
                        context={"root_name": root.name},
                    )
                template_path = spec_path.parent / "template.md"
                if template_path.is_symlink():
                    raise core_errors.ConfigurationError(
                        error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                        message="Prompt template 不允许使用符号链接",
                        context={"root_name": root.name},
                    )
                resolved_template = template_path.resolve()
                if not resolved_template.is_relative_to(resolved_root):
                    raise core_errors.ConfigurationError(
                        error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                        message="Prompt template 越过资产根目录",
                        context={"root_name": root.name},
                    )
                asset = self.load_directory(directory=spec_path.parent)
                asset_key = (asset.spec.prompt_id, asset.spec.prompt_version)
                if asset_key in seen_assets:
                    raise core_errors.ConfigurationError(
                        error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                        message="Prompt 资产版本重复",
                        context={
                            "prompt_id": asset.spec.prompt_id,
                            "prompt_version": asset.spec.prompt_version,
                        },
                    )
                seen_assets.add(asset_key)
                assets.append(asset)
        return tuple(assets)

__all__ = [
    "PromptAssetLoader",
]

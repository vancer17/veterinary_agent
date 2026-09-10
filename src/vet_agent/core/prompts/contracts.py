"""
版本化 prompt 资产的公共契约。

该模块定义 ``core.prompts`` 对语义层暴露的稳定数据形态，包括模板类型、
消息角色、占位符规格、资产 spec、已加载资产和 prompt 绑定结果。这里只
描述工程协议，不定义任何兽医语义 prompt 文案或语义判断规则。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from vet_agent.core.schema import StrictModel


class PromptMessageRole(StrEnum):
    """模型调用消息角色。

    ``core.llm`` 只接受 system / user 两个稳定角色，避免语义层把工具调用
    或自由 agent 协议混入统一模型调用入口。
    """

    #: 系统级静态约束、任务流程与安全边界。
    SYSTEM = "system"

    #: 由 prompt renderer 安全构造的动态上下文消息。
    USER = "user"


class PromptTemplateKind(StrEnum):
    """prompt 模板的受控解析格式。

    模板格式是工程协议；新增格式必须扩展本枚举并同步 loader / renderer。
    """

    #: 一级或二级 ATX 标题切分的 SKILL-like Markdown 模板。
    SECTIONED_MARKDOWN_V1 = "sectioned_markdown_v1"


class PromptPlaceholderKind(StrEnum):
    """prompt 占位符的受控取值类型。

    JSON 类型用于动态语义输入；TEXT 类型仅用于明确的应用侧短文本，二者都
    会在渲染阶段作为数据处理而不是作为系统指令拼接。
    """

    #: 会 canonical JSON 序列化并转义标签分隔符的结构化输入。
    JSON = "json"

    #: 会做 XML 文本转义的普通字符串输入。
    TEXT = "text"


@dataclass(frozen=True, slots=True)
class PromptMessage:
    """不可变模型调用消息。

    :ivar role: 消息角色。
    :ivar content: 已渲染消息内容；普通日志不得输出该字段。
    """

    role: PromptMessageRole
    content: str


@dataclass(frozen=True, slots=True)
class PromptRecord:
    """prompt 注册表中的版本化调用绑定快照。

    该对象是 ``core.llm`` 可以感知的最小 prompt 形态，不携带加载路径、
    section 布局或渲染变量，避免模型网关依赖 prompt 文件系统实现。

    :ivar prompt_id: 语义层定义的稳定 prompt 标识。
    :ivar prompt_version: 语义 prompt 的不可变版本号。
    :ivar prompt_sha256: 原始模板内容 SHA-256 摘要。
    :ivar model_role: 绑定的稳定模型角色名，不是具体模型名。
    :ivar output_schema: 绑定的严格模型输出 schema。
    :ivar template: 原始模板文本；仅在注册表和专用审计中使用。
    """

    prompt_id: str
    prompt_version: str
    prompt_sha256: str
    model_role: str
    output_schema: type[StrictModel]
    template: str


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    """已由核心渲染层生成的不可变调用输入。

    :ivar record: 版本化 prompt 绑定记录。
    :ivar messages: 严格顺序化的 system / user 模型消息列表。
    """

    record: PromptRecord
    messages: tuple[PromptMessage, ...]


class PromptPlaceholderSpec(StrictModel):
    """单个受限占位符的声明式规格。

    :ivar name: 模板中出现的 snake_case 占位符名。
    :ivar kind: 渲染时的受控取值类型。
    :ivar max_rendered_chars: 替换后允许的最大字符数。
    """

    name: str
    kind: PromptPlaceholderKind
    max_rendered_chars: int


class PromptMessageSpec(StrictModel):
    """单条模型消息由模板章节组成的顺序规格。

    :ivar role: 消息角色，当前协议必须恰好包含 system 和 user 各一条。
    :ivar sections: 按顺序拼入该消息的模板章节标题。
    """

    role: PromptMessageRole
    sections: tuple[str, ...]


class PromptAssetSpec(StrictModel):
    """版本化 prompt 资产的机器可读规格。

    spec 是 prompt 文件与 registry / renderer 之间的本地工程契约；其中
    不允许出现具体模型名、API 地址、密钥或运行期身份。

    :ivar prompt_id: 语义层定义的稳定 prompt 标识。
    :ivar prompt_version: 语义 prompt 的不可变版本号。
    :ivar template_kind: 受控模板解析格式。
    :ivar model_role: 稳定模型角色名。
    :ivar output_schema_id: composition root 提供的输出 schema 目录键。
    :ivar allowed_sections: 模板允许出现的章节标题集合。
    :ivar messages: system / user 消息的章节组装顺序。
    :ivar placeholders: 模板允许使用的受限动态占位符集合。
    """

    prompt_id: str
    prompt_version: str
    template_kind: PromptTemplateKind
    model_role: str
    output_schema_id: str
    allowed_sections: tuple[str, ...]
    messages: tuple[PromptMessageSpec, ...]
    placeholders: tuple[PromptPlaceholderSpec, ...]


@dataclass(frozen=True, slots=True)
class LoadedPromptAsset:
    """从文件系统加载并通过工程校验的 prompt 资产。

    该对象只存在于 composition root、prompt registry 和 renderer 中；
    ``core.llm`` 只消费其生成的 ``PromptRecord``，不感知文件资产细节。

    :ivar spec: 已通过 strict schema 校验的资产规格。
    :ivar template: 原始 UTF-8 模板文本。
    :ivar template_sha256: 原始模板字节内容的 SHA-256 摘要。
    :ivar spec_sha256: 原始 spec 字节内容的 SHA-256 摘要。
    :ivar sections: 以标题索引的不可变模板章节正文。
    """

    spec: PromptAssetSpec
    template: str
    template_sha256: str
    spec_sha256: str
    sections: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class PromptBinding:
    """registry 返回的 prompt 资产与模型调用绑定。

    renderer 需要完整资产布局才能安全构造消息；``core.llm`` 后续只读取
    binding 中 ``PromptRecord`` 的版本、摘要、角色和 schema 绑定。

    :ivar asset: 已加载的完整 prompt 资产。
    :ivar record: 可进入模型调用协议的最小版本化记录。
    """

    asset: LoadedPromptAsset
    record: PromptRecord


__all__ = [
    "LoadedPromptAsset",
    "PromptAssetSpec",
    "PromptBinding",
    "PromptMessage",
    "PromptMessageRole",
    "PromptMessageSpec",
    "PromptPlaceholderKind",
    "PromptPlaceholderSpec",
    "PromptRecord",
    "PromptTemplateKind",
    "RenderedPrompt",
]

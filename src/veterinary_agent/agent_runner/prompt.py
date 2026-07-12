##################################################################################################
# 文件: src/veterinary_agent/agent_runner/prompt.py
# 作用: 基于 Jinja2 安全模板环境渲染 AgentRunner prompt，并通过 LangChain 消息层输出模型消息。
# 边界: 不读取业务数据库、不构造宠物画像、不执行 RAG、不补齐记忆或 P0 临床上下文。
##################################################################################################

from dataclasses import dataclass
import json
import re

from jinja2 import StrictUndefined, TemplateSyntaxError, UndefinedError, meta
from jinja2.sandbox import SandboxedEnvironment

from veterinary_agent.agent_runner.dto import (
    AgentRunRequestDto,
    AgentSpecDto,
    PromptBlockDto,
)
from veterinary_agent.agent_runner.enums import (
    AgentRunnerErrorCode,
    AgentRunnerOperation,
)
from veterinary_agent.agent_runner.errors import AgentRunnerError
from veterinary_agent.agent_runner.messages import LangChainMessageComposer
from veterinary_agent.llm_gateway import LlmMessageDto

_ALLOWED_TEMPLATE_FIELDS: frozenset[str] = frozenset(
    {
        "agent_id",
        "agent_version",
        "task_input",
        "task_input_data",
        "task_input_json",
        "prompt_blocks",
        "prompt_block_items",
        "prompt_blocks_text",
        "runtime_options",
        "runtime_options_data",
        "runtime_options_json",
        "session_id",
        "user_id",
        "request_id",
        "trace_id",
        "run_id",
    }
)
_LEGACY_TEMPLATE_FIELD_PATTERN = re.compile(
    r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})"
)


@dataclass(frozen=True, slots=True)
class PromptRenderResult:
    """AgentRunner prompt 渲染结果。"""

    system_prompt: str
    used_fields: frozenset[str]


def _render_json(value: object) -> str:
    """将结构化值渲染为稳定 JSON 文本。

    :param value: 需要渲染的结构化值。
    :return: 使用 UTF-8 文本与稳定 key 顺序渲染的 JSON 字符串。
    """

    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _prompt_block_to_map(block: PromptBlockDto) -> dict[str, object]:
    """将 prompt 上下文块转换为模板可读映射。

    :param block: 已编译 prompt 上下文块。
    :return: 可传给 Jinja2 模板的上下文块映射。
    """

    return {
        "block_id": block.block_id,
        "block_type": block.block_type,
        "content_ref_or_text": block.content_ref_or_text,
        "metadata": block.metadata,
    }


def _render_prompt_block(block: PromptBlockDto) -> str:
    """渲染单个 prompt 上下文块。

    :param block: 已编译 prompt 上下文块。
    :return: 带块头的上下文块文本。
    """

    metadata_text = _render_json(block.metadata) if block.metadata else "{}"
    return (
        f"[{block.block_type}:{block.block_id}]\n"
        f"metadata: {metadata_text}\n"
        f"{block.content_ref_or_text}"
    )


def _render_prompt_blocks(blocks: list[PromptBlockDto]) -> str:
    """渲染 prompt 上下文块列表。

    :param blocks: 已编译 prompt 上下文块列表。
    :return: 以空行分隔的上下文块文本；空列表返回占位文本。
    """

    if not blocks:
        return "(无上游上下文块)"
    return "\n\n".join(_render_prompt_block(block) for block in blocks)


def _build_template_context(
    *,
    request: AgentRunRequestDto,
    spec: AgentSpecDto,
) -> dict[str, object]:
    """构造 Jinja2 prompt 模板可使用的固定上下文字段。

    :param request: AgentRunner 单次运行请求。
    :param spec: 已解析的 Agent 规格。
    :return: 模板字段名到受控上下文值的映射。
    """

    prompt_block_items = [
        _prompt_block_to_map(block) for block in request.prompt_blocks
    ]
    return {
        "agent_id": spec.agent_id,
        "agent_version": spec.agent_version,
        "task_input": _render_json(request.task_input),
        "task_input_data": request.task_input,
        "task_input_json": _render_json(request.task_input),
        "prompt_blocks": _render_prompt_blocks(request.prompt_blocks),
        "prompt_block_items": prompt_block_items,
        "prompt_blocks_text": _render_prompt_blocks(request.prompt_blocks),
        "runtime_options": _render_json(request.runtime_options),
        "runtime_options_data": request.runtime_options,
        "runtime_options_json": _render_json(request.runtime_options),
        "session_id": request.session_id,
        "user_id": request.user_id,
        "request_id": request.request_id,
        "trace_id": request.trace_id,
        "run_id": request.run_id,
    }


def _legacy_template_fields(template: str) -> set[str]:
    """提取旧版 ``{field}`` prompt 模板字段。

    :param template: prompt 模板正文。
    :return: 旧版简单字段名集合。
    """

    return {
        match.group(1) for match in _LEGACY_TEMPLATE_FIELD_PATTERN.finditer(template)
    }


def _normalize_legacy_template_syntax(template: str) -> str:
    """将旧版简单字段语法转换为 Jinja2 变量语法。

    :param template: prompt 模板正文。
    :return: 已兼容转换的 Jinja2 模板正文。
    """

    def replace_legacy_field(match: re.Match[str]) -> str:
        """替换单个旧版模板字段。

        :param match: 正则匹配到的旧版字段。
        :return: Jinja2 变量表达式或原始文本。
        """

        field_name = match.group(1)
        if field_name in _ALLOWED_TEMPLATE_FIELDS:
            return "{{ " + field_name + " }}"
        return match.group(0)

    return _LEGACY_TEMPLATE_FIELD_PATTERN.sub(replace_legacy_field, template)


def _build_jinja_environment() -> SandboxedEnvironment:
    """创建 AgentRunner 专用 Jinja2 安全模板环境。

    :return: 已注册受控过滤器的 Jinja2 sandbox 环境。
    """

    environment = SandboxedEnvironment(
        autoescape=False,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters["tojson_text"] = _render_json
    return environment


class DefaultPromptRenderer:
    """基于 Jinja2 与 LangChain 的 AgentRunner 默认 prompt 渲染器。"""

    def __init__(
        self,
        *,
        message_composer: LangChainMessageComposer | None = None,
        environment: SandboxedEnvironment | None = None,
    ) -> None:
        """初始化默认 prompt 渲染器。

        :param message_composer: 可选 LangChain 消息编排器；未传入时创建默认实例。
        :param environment: 可选 Jinja2 sandbox 环境；未传入时创建默认安全环境。
        :return: None。
        """

        self._message_composer = message_composer or LangChainMessageComposer()
        self._environment = environment or _build_jinja_environment()

    def render_system_prompt(
        self,
        *,
        request: AgentRunRequestDto,
        spec: AgentSpecDto,
    ) -> PromptRenderResult:
        """渲染系统 prompt 文本。

        :param request: AgentRunner 单次运行请求。
        :param spec: 已解析的 Agent 规格。
        :return: 已渲染 prompt 文本与使用字段集合。
        :raises AgentRunnerError: 当模板字段非法、字段缺失或渲染结果为空时抛出。
        """

        template_source = _normalize_legacy_template_syntax(spec.prompt_template)
        used_fields = self._validate_template_fields(
            template_source=template_source,
            request=request,
            spec=spec,
        )
        context = _build_template_context(request=request, spec=spec)
        try:
            rendered = self._environment.from_string(template_source).render(**context)
        except (TemplateSyntaxError, UndefinedError, TypeError, ValueError) as exc:
            raise self._build_render_error(
                request=request,
                spec=spec,
                message="prompt 模板渲染失败",
                reason=str(exc),
            ) from exc
        if not rendered.strip():
            raise self._build_render_error(
                request=request,
                spec=spec,
                message="prompt 渲染结果为空",
                reason=None,
            )
        return PromptRenderResult(
            system_prompt=rendered,
            used_fields=frozenset(used_fields),
        )

    def render_prompt(
        self,
        *,
        request: AgentRunRequestDto,
        spec: AgentSpecDto,
    ) -> list[LlmMessageDto]:
        """渲染一次模型调用消息。

        :param request: AgentRunner 单次运行请求。
        :param spec: 已解析的 Agent 规格。
        :return: 可传给 LlmGateway 的消息列表。
        :raises AgentRunnerError: 当模板字段非法、字段缺失或渲染结果为空时抛出。
        """

        render_result = self.render_system_prompt(request=request, spec=spec)
        return self._message_composer.compose_base_llm_messages(
            system_prompt=render_result.system_prompt,
        )

    def _validate_template_fields(
        self,
        *,
        template_source: str,
        request: AgentRunRequestDto,
        spec: AgentSpecDto,
    ) -> set[str]:
        """校验 Jinja2 模板字段白名单。

        :param template_source: 已规范化的 Jinja2 模板正文。
        :param request: AgentRunner 单次运行请求。
        :param spec: 已解析的 Agent 规格。
        :return: 模板实际使用字段集合。
        :raises AgentRunnerError: 当模板语法非法或包含未授权字段时抛出。
        """

        legacy_fields = _legacy_template_fields(spec.prompt_template)
        unknown_legacy_fields = sorted(
            legacy_fields.difference(_ALLOWED_TEMPLATE_FIELDS)
        )
        if unknown_legacy_fields:
            raise self._build_render_error(
                request=request,
                spec=spec,
                message="prompt 模板包含未授权旧版字段",
                reason=None,
                conflict_with={"unknown_fields": unknown_legacy_fields},
            )
        try:
            parsed_template = self._environment.parse(template_source)
        except TemplateSyntaxError as exc:
            raise self._build_render_error(
                request=request,
                spec=spec,
                message="prompt 模板语法非法",
                reason=str(exc),
            ) from exc
        used_fields = meta.find_undeclared_variables(parsed_template)
        unknown_fields = sorted(used_fields.difference(_ALLOWED_TEMPLATE_FIELDS))
        if unknown_fields:
            raise self._build_render_error(
                request=request,
                spec=spec,
                message="prompt 模板包含未授权字段",
                reason=None,
                conflict_with={"unknown_fields": unknown_fields},
            )
        return set(used_fields)

    def _build_render_error(
        self,
        *,
        request: AgentRunRequestDto,
        spec: AgentSpecDto,
        message: str,
        reason: str | None,
        conflict_with: dict[str, object] | None = None,
    ) -> AgentRunnerError:
        """构建 prompt 渲染错误。

        :param request: AgentRunner 单次运行请求。
        :param spec: 已解析的 Agent 规格。
        :param message: 错误说明。
        :param reason: Jinja2 或运行时错误原因。
        :param conflict_with: 可选脱敏冲突摘要。
        :return: AgentRunner prompt 渲染错误。
        """

        conflict = dict(conflict_with or {})
        if reason is not None:
            conflict["reason"] = reason
        return AgentRunnerError(
            code=AgentRunnerErrorCode.PROMPT_RENDER_FAILED,
            operation=AgentRunnerOperation.RENDER_PROMPT,
            message=message,
            run_id=request.run_id,
            request_id=request.request_id,
            trace_id=request.trace_id,
            agent_id=spec.agent_id,
            agent_version=spec.agent_version,
            model_profile_id=spec.model_profile,
            conflict_with=conflict or None,
        )


__all__: tuple[str, ...] = ("DefaultPromptRenderer", "PromptRenderResult")

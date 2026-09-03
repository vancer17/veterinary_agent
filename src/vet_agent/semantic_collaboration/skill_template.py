"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/skill_template.py
作用：实现标准化 SKILL.md 的受限 Jinja 占位符渲染器。
范围：覆盖沙箱环境、AST 白名单、变量白名单、严格未定义变量、占位符替换
      与未解析模板标记校验。
说明：模板只允许顶层字符串变量替换，不允许条件、循环、过滤器、属性访问、
      宏、导入或任意表达式；本文件不读取业务上下文、不调用模型。
=============================================================================
"""

from __future__ import annotations

from collections.abc import Iterator

from jinja2 import Environment, StrictUndefined, nodes
from jinja2.meta import find_undeclared_variables
from jinja2.sandbox import SandboxedEnvironment

from .errors import SemanticPromptRenderError


def _iter_nodes(node: nodes.Node) -> Iterator[nodes.Node]:
    """深度优先遍历 Jinja AST 节点。

    :param node: Jinja 解析树中的当前节点。
    :return: 返回当前节点及其全部子节点。
    """
    yield node
    for child in node.iter_child_nodes():
        yield from _iter_nodes(child)


class RestrictedSkillTemplate:
    """表示只允许顶层变量替换的 SKILL 提示词模板。

    :return: 无返回值；该模板不是规则引擎，也不承载业务逻辑。
    """

    def __init__(
        self,
        source: str,
        *,
        allowed_variables: tuple[str, ...],
    ) -> None:
        """解析并约束一个静态 SKILL 模板片段。

        :param source: 待渲染的模板正文。
        :param allowed_variables: 当前 SKILL 允许的顶层字符串变量集合。
        :return: 无返回值。
        :raises SemanticPromptRenderError: 模板包含逻辑、过滤器、未知变量或缺失变量时抛出。
        """
        if not allowed_variables or len(set(allowed_variables)) != len(
            allowed_variables,
        ):
            raise SemanticPromptRenderError(
                "restricted skill template variables are invalid",
            )
        self.allowed_variables = allowed_variables
        self.environment: Environment = SandboxedEnvironment(
            autoescape=False,
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=False,
        )
        self.source = source
        self._syntax_tree = self.environment.parse(source)
        self._validate_syntax_tree()
        declared = set(find_undeclared_variables(self._syntax_tree))
        expected = set(allowed_variables)
        if declared != expected:
            raise SemanticPromptRenderError(
                "restricted skill template variables do not match whitelist",
            )
        self._template = self.environment.from_string(source)

    def render(self, variables: dict[str, str]) -> str:
        """渲染受限 SKILL 模板并校验输出中没有模板标记。

        :param variables: 顶层字符串变量集合，键必须与白名单完全一致。
        :return: 返回不含未解析 Jinja 标记的确定性提示词文本。
        :raises SemanticPromptRenderError: 变量集合不闭合或输出仍含模板标记时抛出。
        """
        if set(variables) != set(self.allowed_variables):
            raise SemanticPromptRenderError(
                "restricted skill template render variables are not closed",
            )
        rendered = self._template.render(variables)
        if "{{" in rendered or "{%" in rendered:
            raise SemanticPromptRenderError(
                "restricted skill template output contains template markers",
            )
        return rendered

    def _validate_syntax_tree(self) -> None:
        """校验 Jinja AST 只包含纯文本和顶层变量节点。

        :return: 无返回值。
        :raises SemanticPromptRenderError: AST 包含逻辑、过滤器或表达式节点时抛出。
        """
        allowed_nodes = {
            nodes.Template,
            nodes.Output,
            nodes.TemplateData,
            nodes.Name,
        }
        for node in _iter_nodes(self._syntax_tree):
            if type(node) not in allowed_nodes:
                raise SemanticPromptRenderError(
                    "restricted skill template contains forbidden logic",
                )
            if isinstance(node, nodes.Name) and node.ctx != "load":
                raise SemanticPromptRenderError(
                    "restricted skill template cannot assign variables",
                )

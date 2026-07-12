##################################################################################################
# 文件: tools/ci/python_conventions.py
# 作用: 提供全仓 Python 文件头、严格类型提示与中文新版 ReST 函数说明的静态检查能力。
# 边界: 只读取并解析 Python 源码，不导入被检查模块、不修改文件或判断业务架构语义。
##################################################################################################

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

_SCANNED_DIRECTORIES: tuple[str, ...] = ("src", "tests", "migrations", "tools")
_IGNORED_DIRECTORY_NAMES: frozenset[str] = frozenset(
    {".pytest_cache", ".ruff_cache", ".venv", "__pycache__"}
)


@dataclass(frozen=True, slots=True)
class PythonConventionViolation:
    """单条 Python 源码规范违规信息。"""

    line: int
    message: str


def iter_project_python_files(project_root: Path) -> tuple[Path, ...]:
    """列出项目中需要执行 Python 源码规范检查的文件。

    :param project_root: 项目根目录。
    :return: 已排除缓存与虚拟环境目录并按路径排序的 Python 文件元组。
    """

    files = [
        path
        for directory_name in _SCANNED_DIRECTORIES
        for path in (project_root / directory_name).rglob("*.py")
        if not _IGNORED_DIRECTORY_NAMES.intersection(path.parts)
    ]
    main_path = project_root / "main.py"
    if main_path.exists():
        files.append(main_path)
    return tuple(sorted(files))


def _function_arguments(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[ast.arg, ...]:
    """提取函数除 ``self`` 与 ``cls`` 外的全部显式参数。

    :param node: AST 同步或异步函数定义节点。
    :return: 需要类型提示与 ReST 参数说明的参数节点元组。
    """

    arguments = (
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
    )
    return tuple(
        argument for argument in arguments if argument.arg not in {"self", "cls"}
    )


def _contains_chinese(value: str) -> bool:
    """判断文本是否至少包含一个中文字符。

    :param value: 待检查的函数说明文本。
    :return: 若文本包含中文字符则返回 True。
    """

    return any("\u4e00" <= character <= "\u9fff" for character in value)


def _inspect_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[PythonConventionViolation]:
    """检查单个函数、方法或闭包的类型与函数说明契约。

    :param node: AST 同步或异步函数定义节点。
    :return: 当前函数产生的规范违规列表。
    """

    violations: list[PythonConventionViolation] = []
    docstring = ast.get_docstring(node, clean=False)
    if docstring is None:
        return [PythonConventionViolation(node.lineno, f"{node.name} 缺少函数说明")]
    if not _contains_chinese(docstring):
        violations.append(
            PythonConventionViolation(node.lineno, f"{node.name} 函数说明缺少中文")
        )
    if ":type " in docstring or ":rtype:" in docstring:
        violations.append(
            PythonConventionViolation(node.lineno, f"{node.name} 包含旧式类型字段")
        )
    if ":return:" not in docstring:
        violations.append(
            PythonConventionViolation(node.lineno, f"{node.name} 缺少 :return: 字段")
        )
    if node.returns is None:
        violations.append(
            PythonConventionViolation(node.lineno, f"{node.name} 缺少返回类型提示")
        )

    for argument in _function_arguments(node):
        if argument.annotation is None:
            violations.append(
                PythonConventionViolation(
                    node.lineno,
                    f"{node.name} 参数 {argument.arg} 缺少类型提示",
                )
            )
        if f":param {argument.arg}:" not in docstring:
            violations.append(
                PythonConventionViolation(
                    node.lineno,
                    f"{node.name} 参数 {argument.arg} 缺少 ReST 字段",
                )
            )

    for argument in (node.args.vararg, node.args.kwarg):
        if argument is None:
            continue
        if argument.annotation is None:
            violations.append(
                PythonConventionViolation(
                    node.lineno,
                    f"{node.name} 参数 {argument.arg} 缺少类型提示",
                )
            )
        if f":param {argument.arg}:" not in docstring:
            violations.append(
                PythonConventionViolation(
                    node.lineno,
                    f"{node.name} 参数 {argument.arg} 缺少 ReST 字段",
                )
            )
    return violations


def inspect_python_file(path: Path) -> tuple[PythonConventionViolation, ...]:
    """检查一个 Python 文件的顶部注释块与全部函数契约。

    :param path: 待检查的 Python 源码文件路径。
    :return: 当前文件的全部规范违规元组。
    """

    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    violations: list[PythonConventionViolation] = []
    comment_count = sum(line.lstrip().startswith("#") for line in lines[:8])
    if len(lines) < 4 or comment_count < 4:
        violations.append(PythonConventionViolation(1, "文件顶部注释块不足四行"))

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return (
            *violations,
            PythonConventionViolation(exc.lineno or 1, f"Python 语法错误: {exc.msg}"),
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            violations.extend(_inspect_function(node))
    return tuple(violations)


__all__: tuple[str, ...] = (
    "PythonConventionViolation",
    "inspect_python_file",
    "iter_project_python_files",
)

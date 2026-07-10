##################################################################################################
# 文件: tests/llm_gateway/test_test_suite_quality.py
# 作用: 固化 LlmGateway 组件测试源码的文件头、类型提示、中文 ReST 注释与公共包导入边界规范。
# 边界: 仅静态解析 tests/llm_gateway 源码；不导入内部实现模块、不执行模型调用或修改被测文件。
##################################################################################################

import ast
from pathlib import Path

import pytest

_TEST_PACKAGE_ROOT = Path(__file__).resolve().parent
_REQUIRED_HEADER_FIELDS: tuple[str, ...] = ("# 文件:", "# 作用:", "# 边界:")


def _test_source_files() -> tuple[Path, ...]:
    """列出 LlmGateway 组件测试包内需要执行规范检查的 Python 源码。

    :return: 按文件名排序的 Python 源码路径元组。
    """

    return tuple(sorted(_TEST_PACKAGE_ROOT.glob("*.py")))


def _function_arguments(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[ast.arg, ...]:
    """提取函数除 self 与 cls 外的全部显式参数。

    :param node: AST 同步或异步函数定义节点。
    :return: 需要类型提示与 ReST 参数注释的参数节点元组。
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


def _function_contract_errors(
    *,
    source_path: Path,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
    """检查单个函数或闭包的类型提示与新版 ReST 注释契约。

    :param source_path: 当前函数所在源码路径。
    :param node: AST 同步或异步函数定义节点。
    :return: 当前函数违反规范的错误说明列表。
    """

    prefix = f"{source_path.name}:{node.lineno}:{node.name}"
    errors: list[str] = []
    docstring = ast.get_docstring(node, clean=False)
    if docstring is None:
        return [f"{prefix} 缺少中文 ReST 函数说明"]
    if not _contains_chinese(docstring):
        errors.append(f"{prefix} 函数说明必须包含中文描述")
    if ":type " in docstring or ":rtype:" in docstring:
        errors.append(f"{prefix} 不得使用 :type 或 :rtype: 字段")
    if ":return:" not in docstring:
        errors.append(f"{prefix} 缺少 :return: 字段")
    if node.returns is None:
        errors.append(f"{prefix} 缺少返回类型提示")
    for argument in _function_arguments(node):
        if argument.annotation is None:
            errors.append(f"{prefix} 参数 {argument.arg} 缺少类型提示")
        if f":param {argument.arg}:" not in docstring:
            errors.append(f"{prefix} 缺少 :param {argument.arg}: 字段")
    if node.args.vararg is not None:
        if node.args.vararg.annotation is None:
            errors.append(f"{prefix} 可变位置参数缺少类型提示")
        if f":param {node.args.vararg.arg}:" not in docstring:
            errors.append(f"{prefix} 缺少 :param {node.args.vararg.arg}: 字段")
    if node.args.kwarg is not None:
        if node.args.kwarg.annotation is None:
            errors.append(f"{prefix} 可变关键字参数缺少类型提示")
        if f":param {node.args.kwarg.arg}:" not in docstring:
            errors.append(f"{prefix} 缺少 :param {node.args.kwarg.arg}: 字段")
    return errors


@pytest.mark.parametrize("source_path", _test_source_files())
def test_llm_gateway_test_files_have_standard_header(source_path: Path) -> None:
    """验证每个 LlmGateway 测试源码都具备标准文件信息注释块。

    :param source_path: 当前参数化检查的测试源码路径。
    :return: None。
    """

    source = source_path.read_text(encoding="utf-8")
    header = "\n".join(source.splitlines()[:8])

    assert header.startswith("#" * 98)
    for required_field in _REQUIRED_HEADER_FIELDS:
        assert required_field in header


@pytest.mark.parametrize("source_path", _test_source_files())
def test_llm_gateway_test_functions_have_typed_chinese_rest_docs(
    source_path: Path,
) -> None:
    """验证所有测试函数、方法与闭包都有严格类型提示和中文 ReST 注释。

    :param source_path: 当前参数化检查的测试源码路径。
    :return: None。
    """

    tree = ast.parse(
        source_path.read_text(encoding="utf-8"),
        filename=str(source_path),
    )
    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            errors.extend(
                _function_contract_errors(
                    source_path=source_path,
                    node=node,
                )
            )

    assert errors == []


@pytest.mark.parametrize("source_path", _test_source_files())
def test_llm_gateway_tests_use_public_production_package_exports(
    source_path: Path,
) -> None:
    """验证测试只从 veterinary_agent 顶层公共出口引用生产组件。

    :param source_path: 当前参数化检查的测试源码路径。
    :return: None。
    """

    tree = ast.parse(
        source_path.read_text(encoding="utf-8"),
        filename=str(source_path),
    )
    invalid_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("veterinary_agent."):
                invalid_imports.append(f"{source_path.name}:{node.lineno}:{module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("veterinary_agent."):
                    invalid_imports.append(
                        f"{source_path.name}:{node.lineno}:{alias.name}"
                    )

    assert invalid_imports == []

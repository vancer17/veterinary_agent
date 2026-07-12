##################################################################################################
# 文件: tests/llm_gateway/test_test_suite_quality.py
# 作用: 固化 LlmGateway 测试只经所属一级包公共出口引用生产契约的导入边界。
# 边界: 仅静态解析 tests/llm_gateway 导入语句；通用文件头、类型与 ReST 规范由架构测试统一检查。
##################################################################################################

import ast
from pathlib import Path

import pytest

_TEST_PACKAGE_ROOT = Path(__file__).resolve().parent


def _test_source_files() -> tuple[Path, ...]:
    """列出 LlmGateway 组件测试包内需要执行规范检查的 Python 源码。

    :return: 按文件名排序的 Python 源码路径元组。
    """

    return tuple(sorted(_TEST_PACKAGE_ROOT.glob("*.py")))


@pytest.mark.parametrize("source_path", _test_source_files())
def test_llm_gateway_tests_use_owning_public_package_exports(
    source_path: Path,
) -> None:
    """验证测试从所属一级包导入生产契约且不越过公共出口。

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
            if module == "veterinary_agent" or module.count(".") > 1:
                invalid_imports.append(f"{source_path.name}:{node.lineno}:{module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "veterinary_agent" or alias.name.count(".") > 1:
                    invalid_imports.append(
                        f"{source_path.name}:{node.lineno}:{alias.name}"
                    )

    assert invalid_imports == []

##################################################################################################
# 文件: tests/architecture/test_python_conventions.py
# 作用: 验证全仓 Python 文件具备标准文件头、严格类型提示与中文新版 ReST 函数说明。
# 边界: 仅调用 tools/ci 静态检查器读取源码；不导入被检查模块、不执行业务逻辑或修改文件。
##################################################################################################

from pathlib import Path

import pytest

from tools.ci.architecture_guard import ROOT
from tools.ci.python_conventions import (
    inspect_python_file,
    iter_project_python_files,
)

_PROJECT_PYTHON_FILES = iter_project_python_files(ROOT)


@pytest.mark.parametrize("source_path", _PROJECT_PYTHON_FILES)
def test_python_source_follows_repository_conventions(source_path: Path) -> None:
    """验证单个 Python 文件满足全仓源码规范。

    :param source_path: 当前参数化检查的 Python 源码路径。
    :return: None。
    """

    violations = inspect_python_file(source_path)
    messages = [
        f"{source_path.relative_to(ROOT)}:{violation.line}: {violation.message}"
        for violation in violations
    ]
    assert messages == []

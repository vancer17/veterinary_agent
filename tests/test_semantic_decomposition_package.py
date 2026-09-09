"""
=============================================================================
文件：tests/test_semantic_decomposition_package.py
作用：验证架构基线分支的 src-layout 包边界可被测试环境发现。
范围：只检查包导入契约，不测试尚未实现的语义分解能力。
说明：该测试防止后续工程在未建立显式公共 API 前绕过包入口形成隐式依赖。
=============================================================================
"""

from __future__ import annotations


def test_semantic_decomposition_package_is_importable() -> None:
    """验证语义分解领域包存在且可通过顶层包导入。

    :return: 无返回值；导入失败时由测试框架显式失败。
    """
    from vet_agent import semantic_decomposition

    assert semantic_decomposition.__doc__ is not None
    assert "开放自然语言 proposition" in semantic_decomposition.__doc__

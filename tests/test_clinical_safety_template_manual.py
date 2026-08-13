"""
文件：tests/test_clinical_safety_template_manual.py
作用：验证临床安全静态资产模板操作手册存在且与标准目录一并发布。
说明：本测试只检查文档载体，不依赖数据库或外部服务。
"""

from __future__ import annotations

from pathlib import Path


def test_clinical_safety_template_manual_exists() -> None:
    """验证临床安全模板操作手册文件存在。

    :return: 无返回值；断言通过表示运维手册已随标准目录发布。
    """
    manual = Path("docs/standards/clinical-safety/template-operations-manual.md")
    assert manual.exists()
    assert manual.read_text(encoding="utf-8").startswith("<!--")


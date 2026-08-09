"""
文件：scripts/clinical_safety/__init__.py
作用：作为临床安全离线数据治理脚本包入口，暴露转换器与读取函数。
说明：转换实现仅用于生成标准安全资产与向量片段，不进入在线 Agent 决策链路。
"""


from .converter import (
    build_safety_chunks,
    build_standard_safety_documents,
    convert_safety_reference_payload,
    load_safety_reference,
)

__all__ = [
    "build_safety_chunks",
    "build_standard_safety_documents",
    "convert_safety_reference_payload",
    "load_safety_reference",
]

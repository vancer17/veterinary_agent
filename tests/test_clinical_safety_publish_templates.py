"""
文件：tests/test_clinical_safety_publish_templates.py
作用：验证临床安全静态资产发布态模板与标准文档保持一致。
说明：本测试只覆盖模板和 schema 派生结果，不依赖数据库或外部服务。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vet_agent.clinical_safety import (
    build_clinical_safety_publish_template_documents,
    clinical_safety_asset_publish_json_schema,
    clinical_safety_chunk_publish_json_schema,
    validate_clinical_safety_publish_contract,
)


def test_publish_template_documents_pass_strict_contract() -> None:
    """验证标准模板文档可通过发布态严格契约校验。

    :return: 无返回值；断言通过表示模板可以作为人工审核和导出基线。
    """
    asset_document, chunk_document = build_clinical_safety_publish_template_documents()

    contract = validate_clinical_safety_publish_contract(asset_document, chunk_document)

    assert contract.asset_count == 1
    assert contract.chunk_count == 3
    assert contract.asset_document.assets[0].code == "TEMPLATE_CLINICAL_RISK_SAMPLE"


def test_publish_template_schema_files_match_generated_schema() -> None:
    """验证仓库内模板 schema 文件与代码导出的 schema 同源。

    :return: 无返回值；断言通过表示模板文档不会与契约定义漂移。
    """
    docs_dir = Path("docs/standards/clinical-safety")
    asset_schema_file = docs_dir / "clinical-safety-assets.publish.schema.json"
    chunk_schema_file = docs_dir / "clinical-safety-chunks.publish.schema.json"

    asset_schema = json.loads(asset_schema_file.read_text(encoding="utf-8"))
    chunk_schema = json.loads(chunk_schema_file.read_text(encoding="utf-8"))

    assert asset_schema == clinical_safety_asset_publish_json_schema()
    assert chunk_schema == clinical_safety_chunk_publish_json_schema()


def test_publish_template_example_files_pass_contract() -> None:
    """验证仓库内模板示例文件本身可通过发布态严格契约校验。

    :return: 无返回值；断言通过表示示例文件可直接用于人工审核。
    """
    docs_dir = Path("docs/standards/clinical-safety")
    asset_document = json.loads((docs_dir / "clinical-safety-assets.publish.example.json").read_text(encoding="utf-8"))
    chunk_document = json.loads((docs_dir / "clinical-safety-chunks.publish.example.json").read_text(encoding="utf-8"))

    contract = validate_clinical_safety_publish_contract(asset_document, chunk_document)

    assert contract.asset_count == 1
    assert contract.chunk_count == 3


"""
文件：src/vet_agent/clinical_safety/publish_templates.py
作用：构造临床安全发布态静态资产模板、示例文档与导出辅助数据。
说明：本模块只服务于离线资产治理、人工审核和静态文档生成，不参与运行时候选召回或策略裁决。
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from .asset_contract import (
    ClinicalSafetyActionClassContract,
    ClinicalSafetyAssetTypeContract,
    ClinicalSafetyChunkTypeContract,
    ClinicalSafetyDecisionHintValueContract,
    ClinicalSafetySeverityContract,
)


_TEMPLATE_VERSION = "template-v1"
_TEMPLATE_GENERATED_AT = "2026-08-13T08:00:00+00:00"
_TEMPLATE_ASSET_ID = "clinical_safety_template_example_001"
_TEMPLATE_CODE = "TEMPLATE_CLINICAL_RISK_SAMPLE"
_TEMPLATE_SOURCE_FILE = "docs/standards/clinical-safety/clinical-safety-assets.publish.example.json"
_TEMPLATE_SOURCE_PATH = "$.assets[0]"


def build_clinical_safety_publish_template_documents() -> tuple[dict[str, Any], dict[str, Any]]:
    """构造临床安全发布态静态资产示例文档。

    :return: 返回资产文档与 chunk 文档，二者均满足发布态严格契约。
    """
    asset_document: dict[str, Any] = {
        "_meta": {
            "schema": "clinical_safety_assets",
            "schema_version": "1.0.0",
            "version": _TEMPLATE_VERSION,
            "source_file": _TEMPLATE_SOURCE_FILE,
            "asset_count": 1,
            "generated_at": _TEMPLATE_GENERATED_AT,
            "source_meta": {
                "template": True,
                "purpose": "publish_contract_example",
            },
        },
        "assets": [
            {
                "asset_id": _TEMPLATE_ASSET_ID,
                "code": _TEMPLATE_CODE,
                "asset_type": ClinicalSafetyAssetTypeContract.DANGER_PATTERN.value,
                "canonical_name": "示例临床风险",
                "category": "模板示例",
                "species_scope": ["dog", "cat"],
                "sex_scope": [],
                "age_scope": [],
                "severity": ClinicalSafetySeverityContract.URGENT.value,
                "action_class": ClinicalSafetyActionClassContract.SAME_DAY_VISIT.value,
                "aliases": ["模板示例", "示例风险"],
                "carriers": [],
                "user_expressions": ["示例症状"],
                "symptoms": ["示例症状"],
                "recognition_phrases": [
                    "示例临床风险",
                    "示例症状",
                    "模板示例",
                    "示例风险",
                ],
                "required_context": {
                    "species": ["dog", "cat"],
                    "symptoms": ["示例症状"],
                },
                "decision_hints": {
                    "active_symptom": ClinicalSafetyDecisionHintValueContract.SAFETY_ESCALATED.value,
                },
                "clinical_risk_summary": "这是一个仅用于说明结构的示例临床安全资产。",
                "triage_message": "这是一个仅用于说明结构的示例分诊口径。",
                "source": {
                    "source_file": _TEMPLATE_SOURCE_FILE,
                    "source_path": _TEMPLATE_SOURCE_PATH,
                    "source_text": "这是一个仅用于说明结构的示例资产，不代表真实临床结论。",
                },
                "review_status": "approved",
                "version": _TEMPLATE_VERSION,
                "enabled": True,
                "published_at": _TEMPLATE_GENERATED_AT,
                "raw_text": {
                    "title": "示例临床风险",
                    "body": "这是一个仅用于说明结构的示例原始文本。",
                },
                "metadata": {
                    "template": True,
                    "review_note": "仅用于模板说明",
                },
            }
        ],
    }
    chunk_document: dict[str, Any] = {
        "_meta": {
            "schema": "clinical_safety_chunks",
            "schema_version": "1.0.0",
            "version": _TEMPLATE_VERSION,
            "source_file": _TEMPLATE_SOURCE_FILE,
            "asset_count": 1,
            "chunk_count": 3,
            "generated_at": _TEMPLATE_GENERATED_AT,
        },
        "chunks": _build_template_chunks(),
    }
    return asset_document, chunk_document


def _build_template_chunks() -> list[dict[str, Any]]:
    """构造临床安全发布态示例 chunk 列表。

    :return: 返回与示例资产一一对应的三类 chunk。
    """
    chunks = [
        _build_template_chunk(
            chunk_suffix="recognition",
            chunk_type=ClinicalSafetyChunkTypeContract.RECOGNITION.value,
            title="示例临床风险 风险识别",
            embedding_text="示例临床风险；示例症状；模板示例；示例风险",
        ),
        _build_template_chunk(
            chunk_suffix="clinical_risk",
            chunk_type=ClinicalSafetyChunkTypeContract.CLINICAL_RISK.value,
            title="示例临床风险 临床风险",
            embedding_text="这是一个仅用于说明结构的示例临床风险摘要。",
        ),
        _build_template_chunk(
            chunk_suffix="triage_action",
            chunk_type=ClinicalSafetyChunkTypeContract.TRIAGE_ACTION.value,
            title="示例临床风险 分诊处置",
            embedding_text="这是一个仅用于说明结构的示例分诊口径。",
        ),
    ]
    return chunks


def _build_template_chunk(
    *,
    chunk_suffix: str,
    chunk_type: str,
    title: str,
    embedding_text: str,
) -> dict[str, Any]:
    """构造单条临床安全发布态示例 chunk。

    :param chunk_suffix: chunk 语义后缀，用于保持同一资产下不同 chunk 的稳定命名。
    :param chunk_type: chunk 类型枚举值。
    :param title: chunk 标题。
    :param embedding_text: 用于生成向量的标准文本。
    :return: 返回可通过发布态严格契约校验的 chunk 字典。
    """
    return {
        "chunk_id": f"{_TEMPLATE_ASSET_ID}.{chunk_suffix}.{_TEMPLATE_VERSION}",
        "asset_id": _TEMPLATE_ASSET_ID,
        "chunk_type": chunk_type,
        "title": title,
        "embedding_text": embedding_text,
        "metadata": {
            "asset_id": _TEMPLATE_ASSET_ID,
            "code": _TEMPLATE_CODE,
            "asset_type": ClinicalSafetyAssetTypeContract.DANGER_PATTERN.value,
            "canonical_name": "示例临床风险",
            "severity": ClinicalSafetySeverityContract.URGENT.value,
            "action_class": ClinicalSafetyActionClassContract.SAME_DAY_VISIT.value,
        },
        "review_status": "approved",
        "version": _TEMPLATE_VERSION,
        "enabled": True,
        "embedding_model": "text-embedding-v4",
        "embedding_dimension": 1024,
        "content_hash": _content_hash(embedding_text),
    }


def _content_hash(text: str) -> str:
    """计算示例 chunk 的内容哈希。

    :param text: 需要进行哈希计算的标准文本。
    :return: 返回 SHA-256 十六进制摘要。
    """
    return sha256(text.encode("utf-8")).hexdigest()

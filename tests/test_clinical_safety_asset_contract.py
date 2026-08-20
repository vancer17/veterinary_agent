"""
文件：tests/test_clinical_safety_asset_contract.py
作用：验证临床安全静态资产只存在发布态严格契约通过状态。
范围：覆盖发布态资产、chunk、跨文档引用和禁止运行时兜底编码等资产治理边界。
说明：测试不依赖数据库、模型服务或仓库内历史静态资产内容，避免把不稳定参考资产纳入默认门禁可信源。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from vet_agent.clinical_safety import (
    ClinicalSafetyAssetContractError,
    clinical_safety_asset_publish_json_schema,
    clinical_safety_chunk_publish_json_schema,
    validate_clinical_safety_publish_contract,
)


def test_publish_contract_accepts_strict_asset_and_chunk_documents() -> None:
    """验证完整发布态资产和 chunk 文档可通过严格契约校验。

    :return: 无返回值；断言通过表示发布态严格契约允许合格资产进入发布流程。
    """
    asset_document, chunk_document = _valid_publish_documents()

    contract = validate_clinical_safety_publish_contract(asset_document, chunk_document)

    assert contract.asset_count == 1
    assert contract.chunk_count == 1


def test_publish_contract_rejects_generated_fallback_code() -> None:
    """验证发布态严格契约拒绝兜底生成的安全编码。

    :return: 无返回值；断言通过表示资产 code 不能由运行时或离线兜底 slug 伪造。
    """
    asset_document, chunk_document = _valid_publish_documents()
    asset_document["assets"][0]["code"] = "CLINICAL_SAFETY_UNKNOWN"
    chunk_document["chunks"][0]["metadata"]["code"] = "CLINICAL_SAFETY_UNKNOWN"

    with pytest.raises(ClinicalSafetyAssetContractError):
        validate_clinical_safety_publish_contract(asset_document, chunk_document)


def test_publish_contract_rejects_generic_emergency_code() -> None:
    """验证发布态契约拒绝急诊资产继续复用泛化总标签。

    :return: 无返回值；断言通过表示不同急诊资产必须具备独立信号身份。
    """
    asset_document, chunk_document = _valid_publish_documents()
    asset_document["assets"][0]["asset_type"] = "emergency_red_flag"
    asset_document["assets"][0]["code"] = "EMERGENCY_RED_FLAG"
    chunk_document["chunks"][0]["metadata"]["asset_type"] = "emergency_red_flag"
    chunk_document["chunks"][0]["metadata"]["code"] = "EMERGENCY_RED_FLAG"

    with pytest.raises(ClinicalSafetyAssetContractError):
        validate_clinical_safety_publish_contract(asset_document, chunk_document)


def test_publish_contract_requires_opaque_emergency_asset_code() -> None:
    """验证急诊资产编码使用 opaque 资产级身份而非医学语义命名。

    :return: 无返回值；断言通过表示阶段 4 不把自然语言准入条目枚举化。
    """
    asset_document, chunk_document = _valid_publish_documents()
    asset_document["assets"][0]["asset_type"] = "emergency_red_flag"
    asset_document["assets"][0]["code"] = "EMERGENCY_MODE_7K4Q9PXRAB"
    asset_document["assets"][0]["metadata"]["code_governance"] = {
        "strategy": "opaque_asset_identity_v1",
        "legacy_code": "CONTRACT_TEST_RISK",
    }
    chunk_document["chunks"][0]["metadata"]["asset_type"] = "emergency_red_flag"
    chunk_document["chunks"][0]["metadata"]["code"] = "EMERGENCY_MODE_7K4Q9PXRAB"

    contract = validate_clinical_safety_publish_contract(
        asset_document,
        chunk_document,
        require_embeddings=False,
    )

    assert contract.asset_document.assets[0].code == "EMERGENCY_MODE_7K4Q9PXRAB"


def test_publish_contract_requires_emergency_code_governance() -> None:
    """验证急诊资产发布时必须携带完整编码治理审计信息。

    :return: 无返回值；断言通过表示重新生成资产不会丢失历史编码映射。
    """
    asset_document, chunk_document = _valid_publish_documents()
    asset = asset_document["assets"][0]
    asset["asset_type"] = "emergency_red_flag"
    asset["code"] = "EMERGENCY_MODE_7K4Q9PXRAB"
    chunk = chunk_document["chunks"][0]
    chunk["metadata"]["asset_type"] = "emergency_red_flag"
    chunk["metadata"]["code"] = asset["code"]

    with pytest.raises(ClinicalSafetyAssetContractError):
        validate_clinical_safety_publish_contract(
            asset_document,
            chunk_document,
            require_embeddings=False,
        )


def test_publish_contract_rejects_incomplete_emergency_code_governance() -> None:
    """验证不完整的急诊编码治理信息不能进入发布态。

    :return: 无返回值；断言通过表示 legacy code 映射不会变成可选审计装饰。
    """
    asset_document, chunk_document = _valid_publish_documents()
    asset = asset_document["assets"][0]
    asset["asset_type"] = "emergency_red_flag"
    asset["code"] = "EMERGENCY_MODE_7K4Q9PXRAB"
    asset["metadata"]["code_governance"] = {
        "strategy": "opaque_asset_identity_v1",
        "legacy_code": "",
    }
    chunk = chunk_document["chunks"][0]
    chunk["metadata"]["asset_type"] = "emergency_red_flag"
    chunk["metadata"]["code"] = asset["code"]

    with pytest.raises(ClinicalSafetyAssetContractError):
        validate_clinical_safety_publish_contract(
            asset_document,
            chunk_document,
            require_embeddings=False,
        )


def test_publish_contract_reserves_code_governance_for_emergency_assets() -> None:
    """验证非急诊资产不得携带急诊编码治理字段。

    :return: 无返回值；断言通过表示临床安全 metadata 不会形成第二套类型语义。
    """
    asset_document, chunk_document = _valid_publish_documents()
    asset_document["assets"][0]["metadata"]["code_governance"] = {
        "strategy": "opaque_asset_identity_v1",
        "legacy_code": "CONTRACT_TEST_RISK",
    }

    with pytest.raises(ClinicalSafetyAssetContractError):
        validate_clinical_safety_publish_contract(
            asset_document,
            chunk_document,
            require_embeddings=False,
        )


def test_publish_contract_rejects_duplicate_emergency_asset_codes() -> None:
    """验证同一发布批次内急诊资产不得复用信号编码。

    :return: 无返回值；断言通过表示 evaluator 审计不会因 code 重复而误合并。
    """
    asset_document, chunk_document = _valid_publish_documents()
    first_asset = asset_document["assets"][0]
    first_asset["asset_type"] = "emergency_red_flag"
    first_asset["code"] = "EMERGENCY_MODE_7K4Q9PXRAB"
    first_asset["metadata"]["code_governance"] = {
        "strategy": "opaque_asset_identity_v1",
        "legacy_code": "CONTRACT_TEST_RISK",
    }
    second_asset = dict(first_asset)
    second_asset["asset_id"] = "safety_contract_asset_002"
    second_asset["source"] = dict(first_asset["source"])
    second_asset["required_context"] = dict(first_asset["required_context"])
    second_asset["decision_hints"] = dict(first_asset["decision_hints"])
    second_asset["raw_text"] = dict(first_asset["raw_text"])
    second_asset["metadata"] = dict(first_asset["metadata"])
    asset_document["assets"].append(second_asset)
    asset_document["_meta"]["asset_count"] = 2

    first_chunk = chunk_document["chunks"][0]
    first_chunk["metadata"]["asset_type"] = "emergency_red_flag"
    first_chunk["metadata"]["code"] = "EMERGENCY_MODE_7K4Q9PXRAB"
    second_chunk = dict(first_chunk)
    second_chunk["chunk_id"] = "safety_contract_asset_002.recognition.v1"
    second_chunk["asset_id"] = second_asset["asset_id"]
    second_chunk["metadata"] = dict(first_chunk["metadata"])
    second_chunk["metadata"]["asset_id"] = second_asset["asset_id"]
    chunk_document["chunks"].append(second_chunk)
    chunk_document["_meta"]["asset_count"] = 2
    chunk_document["_meta"]["chunk_count"] = 2

    with pytest.raises(ClinicalSafetyAssetContractError):
        validate_clinical_safety_publish_contract(
            asset_document,
            chunk_document,
            require_embeddings=False,
        )


@pytest.mark.parametrize(
    "generated_code",
    [
        "TOXIC_SUBSTANCE_001",
        "EMERGENCY_RED_FLAG_001",
        "DANGER_PATTERN_EMERGENCYREDFLAGS_001",
    ],
)
def test_publish_contract_rejects_numbered_generated_codes(generated_code: str) -> None:
    """验证发布态严格契约拒绝序号型默认安全编码。

    :param generated_code: 待校验的序号型默认编码。
    :return: 无返回值；断言通过表示旧版离线兜底编码不能进入发布态资产。
    """
    asset_document, chunk_document = _valid_publish_documents()
    asset_document["assets"][0]["code"] = generated_code
    chunk_document["chunks"][0]["metadata"]["code"] = generated_code

    with pytest.raises(ClinicalSafetyAssetContractError):
        validate_clinical_safety_publish_contract(asset_document, chunk_document)


def test_publish_contract_rejects_missing_embedding_metadata() -> None:
    """验证发布态严格契约默认要求 chunk 已具备 embedding 元信息。

    :return: 无返回值；断言通过表示生产发布不能把未向量化 chunk 作为可召回资产。
    """
    asset_document, chunk_document = _valid_publish_documents()
    chunk_document["chunks"][0]["embedding_model"] = None

    with pytest.raises(ClinicalSafetyAssetContractError):
        validate_clinical_safety_publish_contract(asset_document, chunk_document)


def test_publish_contract_rejects_restricted_scope_without_required_context() -> None:
    """验证受限适用范围必须声明等值的裁决前置上下文。

    :return: 无返回值；断言通过表示语义画像未知时受限资产不会在裁决层 fail-open。
    """
    asset_document, chunk_document = _valid_publish_documents()
    asset_document["assets"][0]["required_context"] = {"symptoms": ["呼吸困难"]}

    with pytest.raises(ClinicalSafetyAssetContractError):
        validate_clinical_safety_publish_contract(asset_document, chunk_document)


def test_publish_contract_rejects_misaligned_required_context_values() -> None:
    """验证受限适用范围与前置上下文取值不得漂移。

    :return: 无返回值；断言通过表示召回过滤与裁决前置保持同一资产治理口径。
    """
    asset_document, chunk_document = _valid_publish_documents()
    asset_document["assets"][0]["required_context"]["species"] = ["cat"]

    with pytest.raises(ClinicalSafetyAssetContractError):
        validate_clinical_safety_publish_contract(asset_document, chunk_document)


def test_publish_contract_accepts_unrestricted_scope_without_profile_context() -> None:
    """验证通用资产可以不声明画像前置上下文。

    :return: 无返回值；断言通过表示新增一致性规则不会强制通用资产携带无意义前提。
    """
    asset_document, chunk_document = _valid_publish_documents()
    asset = asset_document["assets"][0]
    asset["species_scope"] = []
    asset["required_context"] = {"symptoms": ["呼吸困难"]}

    contract = validate_clinical_safety_publish_contract(asset_document, chunk_document)

    assert contract.asset_count == 1


def test_publish_contract_can_generate_json_schema() -> None:
    """验证发布态严格契约可导出 JSON Schema 供静态文档和工具链使用。

    :return: 无返回值；断言通过表示 schema 导出能力可用于资产治理文档和发布流程。
    """
    asset_schema = clinical_safety_asset_publish_json_schema()
    chunk_schema = clinical_safety_chunk_publish_json_schema()

    assert asset_schema["title"] == "ClinicalSafetyAssetDocumentPublishContract"
    assert chunk_schema["title"] == "ClinicalSafetyChunkDocumentPublishContract"


def _valid_publish_documents() -> tuple[dict[str, Any], dict[str, Any]]:
    """构造最小发布态临床安全资产和 chunk 文档。

    :return: 返回可通过严格契约校验的资产文档与 chunk 文档。
    """
    generated_at = datetime(2026, 8, 13, 10, 0, tzinfo=UTC).isoformat()
    asset_document: dict[str, Any] = {
        "_meta": {
            "schema": "clinical_safety_assets",
            "schema_version": "1.0.0",
            "version": "v1",
            "source_file": "tests/fixtures/clinical_safety.json",
            "asset_count": 1,
            "generated_at": generated_at,
            "source_meta": {},
        },
        "assets": [
            {
                "asset_id": "safety_contract_asset_001",
                "code": "CONTRACT_TEST_RISK",
                "asset_type": "danger_pattern",
                "canonical_name": "契约测试风险",
                "category": "契约测试",
                "species_scope": ["dog", "cat"],
                "sex_scope": [],
                "age_scope": [],
                "severity": "urgent",
                "action_class": "emergency",
                "aliases": ["契约测试"],
                "carriers": [],
                "user_expressions": [],
                "symptoms": ["呼吸困难"],
                "recognition_phrases": ["呼吸困难"],
                "required_context": {
                    "species": ["dog", "cat"],
                    "symptoms": ["呼吸困难"],
                },
                "decision_hints": {"active_symptom": "safety_escalated"},
                "clinical_risk_summary": "契约测试风险摘要。",
                "triage_message": "契约测试分诊口径。",
                "source": {
                    "source_file": "tests/fixtures/clinical_safety.json",
                    "source_path": "items[1]",
                    "source_text": "契约测试来源。",
                },
                "review_status": "approved",
                "version": "v1",
                "enabled": True,
                "published_at": generated_at,
                "raw_text": {},
                "metadata": {},
            }
        ],
    }
    chunk_document: dict[str, Any] = {
        "_meta": {
            "schema": "clinical_safety_chunks",
            "schema_version": "1.0.0",
            "version": "v1",
            "source_file": "tests/fixtures/clinical_safety.json",
            "asset_count": 1,
            "chunk_count": 1,
            "generated_at": generated_at,
        },
        "chunks": [
            {
                "chunk_id": "safety_contract_asset_001.recognition.v1",
                "asset_id": "safety_contract_asset_001",
                "chunk_type": "recognition",
                "title": "契约测试风险 风险识别",
                "embedding_text": "契约测试风险；呼吸困难",
                "metadata": {
                    "asset_id": "safety_contract_asset_001",
                    "code": "CONTRACT_TEST_RISK",
                    "asset_type": "danger_pattern",
                    "canonical_name": "契约测试风险",
                    "severity": "urgent",
                    "action_class": "emergency",
                },
                "review_status": "approved",
                "version": "v1",
                "enabled": True,
                "embedding_model": "text-embedding-v4",
                "embedding_dimension": 1024,
                "content_hash": "9b0f6ef7b5a7454fae1c3a2e2bf8e9d8",
            }
        ],
    }
    return asset_document, chunk_document

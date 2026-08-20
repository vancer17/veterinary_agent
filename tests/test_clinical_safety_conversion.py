"""
文件：tests/test_clinical_safety_conversion.py
作用：验证原始临床安全参考数据能够稳定转换为标准资产与向量检索片段。
说明：测试聚焦数据治理格式，不依赖模型服务或数据库。
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts.clinical_safety import build_standard_safety_documents
from vet_agent.clinical_safety import validate_clinical_safety_publish_contract


def test_safety_reference_converts_explicit_codes_to_standard_assets() -> None:
    """验证显式编码的原始安全参考数据可转换为标准临床安全资产。

    :return: 无返回值；断言通过表示离线转换器不再依赖仓库内不稳定参考资产。
    """
    payload = _minimal_reference_payload()

    asset_document, chunk_document = build_standard_safety_documents(
        payload,
        source_file="tests/fixtures/clinical_safety_reference.json",
        version="v1",
        review_status="pending",
    )

    assets = asset_document["assets"]
    chunks = chunk_document["chunks"]
    assert asset_document["_meta"]["asset_count"] == 2
    assert chunk_document["_meta"]["chunk_count"] == 6
    assert len(chunks) == len(assets) * 3
    assert all(
        asset["review_status"] == "pending" and asset["enabled"] is False
        for asset in assets
    )
    assert all(
        chunk["review_status"] == "pending" and chunk["enabled"] is False
        for chunk in chunks
    )
    xylitol = _find_asset(assets, "木糖醇")
    assert xylitol["code"] == "TOXIC_XYLITOL"
    assert xylitol["asset_type"] == "toxin"
    assert xylitol["action_class"] == "emergency"
    assert "xylitol" in {alias.lower() for alias in xylitol["aliases"]}
    assert "无糖口香糖" in xylitol["carriers"]
    assert xylitol["decision_hints"]["actual_exposure"] == "safety_escalated"

    cyanosis = _find_asset(assets, "舌/牙龈发绀发紫")
    assert cyanosis["code"] == "EMERGENCY_MODE_7K4Q9PXRAB"
    assert {"舌/牙龈发绀发紫", "牙龈发绀发紫", "发绀", "发紫"}.issubset(
        set(cyanosis["recognition_phrases"])
    )
    assert "发绀" in _recognition_chunk_text(chunks, cyanosis["asset_id"])


def test_safety_reference_can_pass_publish_contract() -> None:
    """验证显式编码的转换结果可进入发布态严格契约校验。

    :return: 无返回值；断言通过表示离线转换结果具备严格发布态结构。
    """
    payload = _minimal_reference_payload()

    asset_document, chunk_document = build_standard_safety_documents(
        payload,
        source_file="tests/fixtures/clinical_safety_reference.json",
        version="v1",
        review_status="approved",
    )

    contract = validate_clinical_safety_publish_contract(
        asset_document,
        chunk_document,
        require_embeddings=False,
    )

    assert contract.asset_count == 2
    assert contract.chunk_count == 6
    assert all(
        asset.review_status == "approved" and asset.enabled
        for asset in contract.asset_document.assets
    )
    assert all(
        chunk.review_status == "approved" and chunk.enabled
        for chunk in contract.chunk_document.chunks
    )


def test_safety_reference_conversion_rejects_missing_explicit_code() -> None:
    """验证离线转换器缺少显式 code 时快速失败。

    :return: 无返回值；断言通过表示转换阶段不会生成序号型临床安全兜底编码。
    """
    payload = _minimal_reference_payload()
    del payload["toxinsAndDrugs"][0]["code"]

    with pytest.raises(ValueError, match="clinical safety asset code is required"):
        build_standard_safety_documents(
            payload,
            source_file="tests/fixtures/clinical_safety_reference.json",
            version="v1",
            review_status="pending",
        )


def test_safety_reference_preserves_explicit_code_governance() -> None:
    """验证离线转换会稳定保留急诊 code 治理审计信息。

    :return: 无返回值；断言通过表示重新生成资产不会丢失阶段 4 历史编码映射。
    """
    payload = _minimal_reference_payload()
    payload["emergencyRedFlags"][0]["code_governance"] = {
        "strategy": "opaque_asset_identity_v1",
        "legacy_code": "CYANOSIS_RISK_PATTERN",
    }

    asset_document, _ = build_standard_safety_documents(
        payload,
        source_file="tests/fixtures/clinical_safety_reference.json",
        version="v1",
        review_status="pending",
    )

    asset = _find_asset(asset_document["assets"], "舌/牙龈发绀发紫")
    assert asset["metadata"]["code_governance"] == {
        "strategy": "opaque_asset_identity_v1",
        "legacy_code": "CYANOSIS_RISK_PATTERN",
    }


def test_safety_reference_rejects_incomplete_code_governance() -> None:
    """验证不完整的 code 治理信息在离线转换阶段快速失败。

    :return: 无返回值；断言通过表示历史编码映射不会变成半结构化审计字段。
    """
    payload = _minimal_reference_payload()
    payload["emergencyRedFlags"][0]["code_governance"] = {
        "strategy": "opaque_asset_identity_v1",
    }

    with pytest.raises(
        ValueError,
        match="code governance requires strategy and legacy_code",
    ):
        build_standard_safety_documents(
            payload,
            source_file="tests/fixtures/clinical_safety_reference.json",
            version="v1",
            review_status="pending",
        )


def test_safety_reference_requires_emergency_code_governance() -> None:
    """验证急诊资产缺少 code 治理信息时离线转换快速失败。

    :return: 无返回值；断言通过表示新增急诊资产不能丢失历史编码审计边界。
    """
    payload = _minimal_reference_payload()
    del payload["emergencyRedFlags"][0]["code_governance"]

    with pytest.raises(ValueError, match="code governance is required"):
        build_standard_safety_documents(
            payload,
            source_file="tests/fixtures/clinical_safety_reference.json",
            version="v1",
            review_status="pending",
        )


def _minimal_reference_payload() -> dict[str, Any]:
    """构造转换测试使用的最小临床安全参考数据。

    :return: 返回不依赖仓库静态资产文件的原始参考数据。
    """
    return {
        "_meta": {
            "file_name": "clinical_safety_reference.test.json",
            "clinical_review_required": True,
        },
        "toxinsAndDrugs": [
            {
                "code": "TOXIC_XYLITOL",
                "category": "毒物",
                "item": "木糖醇(xylitol)",
                "aliases": "xylitol、无糖口香糖、木糖醇口香糖",
                "species": "犬",
                "danger": "犬误食后可出现呕吐、低血糖和虚弱。",
                "action": "急诊。疑似误食需立即联系线下兽医医院。",
                "source": "测试来源。",
            }
        ],
        "emergencyRedFlags": [
            {
                "code": "EMERGENCY_MODE_7K4Q9PXRAB",
                "code_governance": {
                    "strategy": "opaque_asset_identity_v1",
                    "legacy_code": "CYANOSIS_RISK_PATTERN",
                },
                "category": "呼吸循环",
                "item": "舌/牙龈发绀发紫",
                "aliases": "牙龈发绀发紫、发绀、发紫",
                "species": "犬猫",
                "danger": "可见呼吸困难、呼吸很快和牙龈发紫。",
                "action": "急诊。正在发生时优先线下处理。",
                "source": "测试来源。",
            }
        ],
        "dangerPatterns": [],
    }


def _find_asset(assets: list[dict[str, Any]], canonical_name: str) -> dict[str, Any]:
    """按规范名称查找标准临床安全资产。

    :param assets: 标准临床安全资产列表。
    :param canonical_name: 待查找的规范名称。
    :return: 返回匹配的标准临床安全资产。
    """
    for asset in assets:
        if asset["canonical_name"] == canonical_name:
            return asset
    raise AssertionError(f"asset not found: {canonical_name}")


def _recognition_chunk_text(chunks: list[dict[str, Any]], asset_id: str) -> str:
    """读取指定资产的 recognition chunk 文本。

    :param chunks: 标准临床安全 chunk 列表。
    :param asset_id: 待读取的资产标识。
    :return: 返回 recognition chunk 的向量化文本。
    """
    for chunk in chunks:
        if chunk["asset_id"] == asset_id and chunk["chunk_type"] == "recognition":
            return str(chunk["embedding_text"])
    raise AssertionError(f"recognition chunk not found: {asset_id}")

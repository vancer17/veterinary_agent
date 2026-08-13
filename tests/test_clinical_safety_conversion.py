"""
文件：tests/test_clinical_safety_conversion.py
作用：验证原始临床安全参考数据能够稳定转换为标准资产与向量检索片段。
说明：测试聚焦数据治理格式，不依赖模型服务或数据库。
"""


from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.clinical_safety import build_standard_safety_documents, load_safety_reference
from vet_agent.clinical_safety import validate_clinical_safety_publish_contract


def test_safety_reference_converts_to_standard_assets() -> None:
    """验证原始安全参考数据可全量转换为标准临床安全资产。

    :return: 无返回值；断言通过表示标准资产转换结果符合预期。
    """
    source = Path("scripts/clinical_safety/assets/vet_safety_reference.json")
    payload = load_safety_reference(source)

    asset_document, chunk_document = build_standard_safety_documents(
        payload,
        source_file=str(source),
        version="v1",
        review_status="pending",
    )

    assets = asset_document["assets"]
    chunks = chunk_document["chunks"]
    assert asset_document["_meta"]["asset_count"] == 130
    assert chunk_document["_meta"]["chunk_count"] == 390
    assert len(chunks) == len(assets) * 3
    assert all(asset["review_status"] == "pending" and asset["enabled"] is False for asset in assets)
    assert all(chunk["review_status"] == "pending" and chunk["enabled"] is False for chunk in chunks)
    xylitol = _find_asset(assets, "木糖醇")
    assert xylitol["code"] == "TOXIC_XYLITOL"
    assert xylitol["asset_type"] == "toxin"
    assert xylitol["action_class"] == "emergency"
    assert "xylitol" in {alias.lower() for alias in xylitol["aliases"]}
    assert "无糖口香糖" in xylitol["carriers"]
    assert xylitol["decision_hints"]["actual_exposure"] == "safety_escalated"

    senior_cat = _find_asset(assets, "老年猫:消瘦 + 食欲不减反而亢进 + 多饮多尿")
    assert senior_cat["canonical_name"] == "老年猫:消瘦 + 食欲不减反而亢进 + 多饮多尿"
    assert "消瘦" in senior_cat["recognition_phrases"]
    assert "食欲不减反而亢进" in senior_cat["recognition_phrases"]
    assert "多饮多尿" in senior_cat["recognition_phrases"]
    assert senior_cat["canonical_name"] in _recognition_chunk_text(chunks, senior_cat["asset_id"])

    gdv = _find_asset(assets, "胃扩张扭转")
    assert {"胃扩张扭转", "GDV", "胃扭转", "腹胀", "干呕"}.issubset(
        set(gdv["recognition_phrases"])
    )
    recognition_text = _recognition_chunk_text(chunks, gdv["asset_id"])
    assert any(
        phrase in recognition_text
        for phrase in ("无效干呕+腹胀", "腹胀+无效干呕+流口水")
    )

    cyanosis = _find_asset(assets, "舌/牙龈发绀发紫")
    assert {"舌/牙龈发绀发紫", "牙龈发绀发紫", "发绀", "发紫"}.issubset(
        set(cyanosis["recognition_phrases"])
    )
    assert "发绀" in _recognition_chunk_text(chunks, cyanosis["asset_id"])


def test_safety_reference_can_pass_publish_contract() -> None:
    """验证原始安全参考数据经过转换后可进入发布态严格契约校验。

    :return: 无返回值；断言通过表示离线转换结果具备严格发布态结构。
    """
    source = Path("scripts/clinical_safety/assets/vet_safety_reference.json")
    payload = load_safety_reference(source)

    asset_document, chunk_document = build_standard_safety_documents(
        payload,
        source_file=str(source),
        version="v1",
        review_status="approved",
    )

    contract = validate_clinical_safety_publish_contract(
        asset_document,
        chunk_document,
        require_embeddings=False,
    )

    assert contract.asset_count == 130
    assert contract.chunk_count == 390
    assert all(asset.review_status == "approved" and asset.enabled for asset in contract.asset_document.assets)
    assert all(chunk.review_status == "approved" and chunk.enabled for chunk in contract.chunk_document.chunks)


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

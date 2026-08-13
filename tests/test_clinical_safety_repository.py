"""
文件：tests/test_clinical_safety_repository.py
作用：验证临床安全文件仓储只作为离线入口，并在运行时发布态读取中保持严格契约。
说明：测试使用临时目录和发布态模板数据，不依赖仓库内历史静态资产内容或外部数据库。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vet_agent.clinical_safety import (
    ClinicalSafetyAsset,
    FileClinicalSafetyRepository,
    build_clinical_safety_publish_template_documents,
)


def test_clinical_safety_asset_rejects_empty_code() -> None:
    """验证运行时资产模型拒绝空安全信号编码。

    :return: 无返回值；断言通过表示候选召回链路不会根据名称补齐 code。
    """
    with pytest.raises(ValueError, match="clinical safety asset code is required"):
        ClinicalSafetyAsset(
            asset_id="asset_without_code",
            asset_type="danger_pattern",
            canonical_name="缺少编码资产",
            category="契约测试",
            species_scope=("dog",),
            sex_scope=(),
            age_scope=(),
            severity="urgent",
            action_class="same_day_visit",
            code="",
        )


def test_file_repository_defaults_to_published_assets_only(tmp_path: Path) -> None:
    """验证文件仓储默认只返回完整发布态资产和 chunk。

    :param tmp_path: 临时资产目录。
    :return: 无返回值；断言通过表示离线草稿不会被默认读取为运行时候选来源。
    """
    asset_document, chunk_document = build_clinical_safety_publish_template_documents()
    _write_repository_documents(tmp_path, asset_document, chunk_document)

    repository = FileClinicalSafetyRepository(tmp_path)

    assert len(repository.assets()) == 1
    assert len(repository.chunks()) == 3
    assert repository.asset_by_id("clinical_safety_template_example_001") is not None


def test_file_repository_excludes_draft_assets_by_default(tmp_path: Path) -> None:
    """验证文件仓储默认不返回草稿资产。

    :param tmp_path: 临时资产目录。
    :return: 无返回值；断言通过表示草稿只可通过离线显式读取路径访问。
    """
    asset_document, chunk_document = build_clinical_safety_publish_template_documents()
    for asset in asset_document["assets"]:
        asset["review_status"] = "pending"
        asset["enabled"] = False
        asset["published_at"] = None
    for chunk in chunk_document["chunks"]:
        chunk["review_status"] = "pending"
        chunk["enabled"] = False
    _write_repository_documents(tmp_path, asset_document, chunk_document)

    repository = FileClinicalSafetyRepository(tmp_path)

    assert repository.assets() == []
    assert repository.chunks() == []
    assert len(repository.assets(published_only=False)) == 1
    assert len(repository.chunks(published_only=False)) == 3


def test_file_repository_rejects_invalid_asset_enum(tmp_path: Path) -> None:
    """验证文件仓储不会把非法枚举静默修复成默认值。

    :param tmp_path: 临时资产目录。
    :return: 无返回值；断言通过表示非法资产数据会在读取阶段显式失败。
    """
    asset_document, chunk_document = build_clinical_safety_publish_template_documents()
    asset_document["assets"][0]["asset_type"] = "unknown_asset_type"
    _write_repository_documents(tmp_path, asset_document, chunk_document)

    repository = FileClinicalSafetyRepository(tmp_path)

    with pytest.raises(ValueError, match="invalid clinical safety asset_type"):
        repository.assets(published_only=False)


def _write_repository_documents(
    asset_dir: Path,
    asset_document: dict[str, Any],
    chunk_document: dict[str, Any],
) -> None:
    """写入临床安全文件仓储测试文档。

    :param asset_dir: 临时资产目录。
    :param asset_document: 临床安全资产文档。
    :param chunk_document: 临床安全 chunk 文档。
    :return: 无返回值。
    """
    asset_dir.mkdir(parents=True, exist_ok=True)
    (asset_dir / "vet_safety_assets.v1.json").write_text(
        json.dumps(asset_document, ensure_ascii=False),
        encoding="utf-8",
    )
    (asset_dir / "vet_safety_chunks.v1.json").write_text(
        json.dumps(chunk_document, ensure_ascii=False),
        encoding="utf-8",
    )

"""
文件：tests/test_seed_database_clinical_safety.py
作用：验证临床安全 seed 导入在发布态下遵循严格资产与 embedding 契约。
说明：测试使用临时文件和最小会话桩，不连接真实数据库或外部模型服务。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.seed_database import seed_clinical_safety
from vet_agent.clinical_safety import build_clinical_safety_publish_template_documents


class MinimalSession:
    """提供 seed_clinical_safety 所需的最小会话桩。

    :return: 无返回值；该对象用于隔离数据库层副作用。
    """

    def get(self, model: type[object], identity: object) -> object | None:
        """模拟 SQLAlchemy Session.get。

        :param model: 数据表模型类型。
        :param identity: 主键标识。
        :return: 始终返回 None，表示需要新建模型。
        """
        del model, identity
        return None

    def add(self, instance: object) -> None:
        """模拟 SQLAlchemy Session.add。

        :param instance: 待加入会话的模型实例。
        :return: 无返回值。
        """
        del instance


class EmptyEmbeddingClient:
    """提供空 embedding 的测试客户端。

    :return: 无返回值；该对象用于验证发布态导入不会接受空向量。
    """

    @property
    def available(self) -> bool:
        """声明测试 embedding 客户端可用。

        :return: 始终返回 True。
        """
        return True

    def embed(self, text: str) -> list[float]:
        """返回空向量以模拟上游异常结果。

        :param text: 待向量化文本。
        :return: 返回空向量。
        """
        del text
        return []


def test_seed_clinical_safety_requires_embedding_client_for_approved_import(tmp_path: Path) -> None:
    """验证发布态临床安全导入必须具备可用 embedding 客户端。

    :param tmp_path: 临时资产目录。
    :return: 无返回值；断言通过表示 approved 导入不会写入缺失向量的发布态 chunk。
    """
    asset_document, chunk_document = build_clinical_safety_publish_template_documents()
    _write_repository_documents(tmp_path, asset_document, chunk_document)

    with pytest.raises(RuntimeError, match="clinical safety approved import requires an available embedding client"):
        seed_clinical_safety(
            MinimalSession(),
            tmp_path,
            None,
            embedding_model="text-embedding-v4",
            review_status="approved",
        )


def test_seed_clinical_safety_rejects_empty_embedding_for_approved_import(tmp_path: Path) -> None:
    """验证发布态临床安全导入拒绝空 embedding 结果。

    :param tmp_path: 临时资产目录。
    :return: 无返回值；断言通过表示空向量不会被写成发布态 chunk。
    """
    asset_document, chunk_document = build_clinical_safety_publish_template_documents()
    _write_repository_documents(tmp_path, asset_document, chunk_document)

    with pytest.raises(RuntimeError, match="clinical safety published chunk embedding is empty"):
        seed_clinical_safety(
            MinimalSession(),
            tmp_path,
            EmptyEmbeddingClient(),
            embedding_model="text-embedding-v4",
            review_status="approved",
        )


def _write_repository_documents(
    asset_dir: Path,
    asset_document: dict[str, Any],
    chunk_document: dict[str, Any],
) -> None:
    """写入临床安全 seed 测试文档。

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

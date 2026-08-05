"""
文件：src/vet_agent/services/clinical_knowledge.py
作用：管理结构化临床病症卡资产，支持导入、校验、字段级 RAG chunk 生成、发布与查询。
说明：本文件承载 common_conditions_handbook.md 工单的后台运维入库能力；运行时只消费已审核发布的知识片段。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update

from vet_agent.db import (
    ClinicalConditionCardModel,
    KnowledgeChunkModel,
    KnowledgeIngestionBatchModel,
    RagAuditEventModel,
    make_session_factory,
)
from vet_agent.stores import JsonDocumentStore


CONDITION_REQUIRED_FIELDS = (
    "condition",
    "system",
    "presentation",
    "differentials",
    "followupQuestions",
    "triage",
    "redFlagsEscalate",
    "medicationDirection",
    "homeAdvice",
    "source",
)
CHUNK_FIELD_LABELS = {
    "condition_overview": "病症概览",
    "followup_questions": "病症特异追问",
    "triage": "分诊建议",
    "red_flags": "升级红旗",
    "medication_direction": "用药方向",
    "home_advice": "居家护理",
}
PUBLISHED_REVIEW_STATUS = "approved"


@dataclass(frozen=True)
class ClinicalConditionCard:
    """表示一张结构化临床病症卡。

    :param condition_key: 病症稳定键。
    :param condition_name: 病症名称。
    :param system: 所属系统。
    :param presentation: 典型表现。
    :param differentials: 鉴别诊断。
    :param followup_questions: 病症特异追问。
    :param triage: 分诊建议。
    :param red_flags_escalate: 升级红旗。
    :param medication_direction: 用药方向。
    :param home_advice: 居家护理。
    :param source: 来源说明。
    :param source_url: 来源链接。
    :param metadata: 附加元数据。
    :return: 无返回值。
    """

    condition_key: str
    condition_name: str
    system: str
    presentation: str
    differentials: str
    followup_questions: str
    triage: str
    red_flags_escalate: str
    medication_direction: str
    home_advice: str
    source: str
    source_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, item: dict[str, Any], *, index: int) -> "ClinicalConditionCard":
        """从 JSON 原始条目构建病症卡。

        :param item: 原始病症卡数据。
        :param index: 条目序号。
        :return: 返回函数执行结果。
        """
        condition = _clean_text(item.get("condition"))
        system = _clean_text(item.get("system"))
        key_source = f"{system}:{condition}" if condition else f"condition:{index}"
        source_url = _first_url(_clean_text(item.get("source")))
        return cls(
            condition_key=_stable_key(key_source),
            condition_name=condition,
            system=system,
            presentation=_clean_text(item.get("presentation")),
            differentials=_clean_text(item.get("differentials")),
            followup_questions=_clean_text(item.get("followupQuestions")),
            triage=_clean_text(item.get("triage")),
            red_flags_escalate=_clean_text(item.get("redFlagsEscalate")),
            medication_direction=_clean_text(item.get("medicationDirection")),
            home_advice=_clean_text(item.get("homeAdvice")),
            source=_clean_text(item.get("source")),
            source_url=source_url,
            metadata={"raw_index": index},
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。

        :return: 返回函数执行结果。
        """
        return {
            "condition_key": self.condition_key,
            "condition_name": self.condition_name,
            "system": self.system,
            "presentation": self.presentation,
            "differentials": self.differentials,
            "followup_questions": self.followup_questions,
            "triage": self.triage,
            "red_flags_escalate": self.red_flags_escalate,
            "medication_direction": self.medication_direction,
            "home_advice": self.home_advice,
            "source": self.source,
            "source_url": self.source_url,
            "metadata": self.metadata,
            "content_hash": self.content_hash(),
        }

    def content_hash(self) -> str:
        """计算病症卡内容哈希。

        :return: 返回函数执行结果。
        """
        payload = json.dumps(self.to_content_payload(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_content_payload(self) -> dict[str, str | None]:
        """返回参与哈希和 chunk 生成的核心内容。

        :return: 返回函数执行结果。
        """
        return {
            "condition_name": self.condition_name,
            "system": self.system,
            "presentation": self.presentation,
            "differentials": self.differentials,
            "followup_questions": self.followup_questions,
            "triage": self.triage,
            "red_flags_escalate": self.red_flags_escalate,
            "medication_direction": self.medication_direction,
            "home_advice": self.home_advice,
            "source": self.source,
            "source_url": self.source_url,
        }


@dataclass(frozen=True)
class ConditionChunkDraft:
    """表示由病症卡派生的字段级知识片段。

    :param chunk_type: 片段类型。
    :param title: 片段标题。
    :param content: 片段正文。
    :param metadata: 片段元数据。
    :return: 无返回值。
    """

    chunk_type: str
    title: str
    content: str
    metadata: dict[str, Any]


class ClinicalKnowledgeService:
    """结构化临床知识资产服务。"""

    def __init__(self, store: "ClinicalKnowledgeStore", embedding_client=None) -> None:
        """初始化结构化临床知识资产服务。

        :param store: 数据存储实现。
        :param embedding_client: 向量生成客户端。
        :return: 无返回值。
        """
        self.store = store
        self.embedding_client = embedding_client

    async def import_conditions(
        self,
        payload: dict[str, Any],
        *,
        source: str,
        version: str = "v1",
        actor_id: str | None = None,
        publish: bool = False,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        """导入结构化病症卡并生成字段级 RAG chunk。

        :param payload: JSON 载荷。
        :param source: 知识来源名称。
        :param version: 知识版本。
        :param actor_id: 操作人标识。
        :param publish: 是否导入后立即发布。
        :param source_url: 来源文件地址。
        :return: 返回函数执行结果。
        """
        cards, validation_errors = parse_condition_payload(payload)
        if validation_errors:
            return await self.store.record_failed_batch(
                source=source,
                version=version,
                actor_id=actor_id,
                source_url=source_url,
                validation_errors=validation_errors,
                metadata=_payload_metadata(payload),
            )

        batch_id = f"clinical_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"
        chunk_rows: list[dict[str, Any]] = []
        for card in cards:
            for chunk in build_condition_chunks(card):
                embedding = self._embed_or_none(chunk.content)
                row = {
                    "source": source,
                    "title": chunk.title,
                    "content": chunk.content,
                    "embedding": embedding,
                    "public_citation": True,
                    "copyright_risk": "low",
                    "domain": _domain_from_system(card.system),
                    "species": _species_from_text(f"{card.condition_name}\n{card.presentation}"),
                    "source_url": card.source_url or source_url,
                    "version": version,
                    "enabled": publish,
                    "review_status": PUBLISHED_REVIEW_STATUS if publish else "pending",
                    "quality_score": 0.9,
                    "ingestion_batch": batch_id,
                    "metadata": {
                        **chunk.metadata,
                        "asset_type": "clinical_condition",
                        "condition_key": card.condition_key,
                        "condition_name": card.condition_name,
                        "condition_system": card.system,
                        "field": chunk.chunk_type,
                        "version": version,
                    },
                }
                chunk_rows.append(row)

        return await self.store.import_batch(
            batch_id=batch_id,
            cards=cards,
            chunks=chunk_rows,
            source=source,
            version=version,
            actor_id=actor_id,
            publish=publish,
            source_url=source_url,
            metadata=_payload_metadata(payload),
        )

    async def import_conditions_from_file(
        self,
        path: Path,
        *,
        source: str,
        version: str = "v1",
        actor_id: str | None = None,
        publish: bool = False,
    ) -> dict[str, Any]:
        """从本地文件导入结构化病症卡。

        :param path: 本地 JSON 文件路径。
        :param source: 知识来源名称。
        :param version: 知识版本。
        :param actor_id: 操作人标识。
        :param publish: 是否导入后立即发布。
        :return: 返回函数执行结果。
        """
        payload = json.loads(path.read_text(encoding="utf-8"))
        return await self.import_conditions(
            payload,
            source=source,
            version=version,
            actor_id=actor_id,
            publish=publish,
            source_url=str(path),
        )

    async def preview_conditions(self, payload: dict[str, Any], *, limit: int = 5) -> dict[str, Any]:
        """预览结构化病症卡入库效果。

        :param payload: JSON 载荷。
        :param limit: 返回数量上限。
        :return: 返回函数执行结果。
        """
        cards, validation_errors = parse_condition_payload(payload)
        previews = []
        for card in cards[:limit]:
            chunks = build_condition_chunks(card)
            previews.append(
                {
                    "condition_key": card.condition_key,
                    "condition_name": card.condition_name,
                    "system": card.system,
                    "content_hash": card.content_hash(),
                    "chunk_count": len(chunks),
                    "chunks": [
                        {
                            "chunk_type": chunk.chunk_type,
                            "title": chunk.title,
                            "content_preview": chunk.content[:240],
                        }
                        for chunk in chunks
                    ],
                }
            )
        return {
            "valid": not validation_errors,
            "condition_count": len(cards),
            "validation_errors": validation_errors,
            "items": previews,
        }

    async def publish_batch(self, batch_id: str, *, actor_id: str | None = None, reason: str | None = None) -> dict[str, Any]:
        """发布指定导入批次。

        :param batch_id: 导入批次标识。
        :param actor_id: 操作人标识。
        :param reason: 操作原因。
        :return: 返回函数执行结果。
        """
        return await self.store.publish_batch(batch_id, actor_id=actor_id, reason=reason)

    async def list_batches(self, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """分页查询导入批次。

        :param limit: 返回数量上限。
        :param offset: 分页偏移量。
        :return: 返回函数执行结果。
        """
        return await self.store.list_batches(limit=limit, offset=offset)

    async def list_conditions(
        self,
        *,
        review_status: str | None = None,
        system: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """分页查询结构化病症卡。

        :param review_status: 审核状态。
        :param system: 所属系统。
        :param limit: 返回数量上限。
        :param offset: 分页偏移量。
        :return: 返回函数执行结果。
        """
        return await self.store.list_conditions(
            review_status=review_status,
            system=system,
            limit=limit,
            offset=offset,
        )

    def _embed_or_none(self, text: str) -> list[float] | None:
        """按配置生成向量，失败时保留文本检索能力。

        :param text: 待向量化文本。
        :return: 返回函数执行结果。
        """
        if self.embedding_client is None:
            return None
        try:
            return self.embedding_client.embed(text)
        except Exception:
            return None


class ClinicalKnowledgeStore:
    async def record_failed_batch(self, **kwargs) -> dict[str, Any]:
        """记录失败的导入批次。

        :param kwargs: 批次参数。
        :return: 返回函数执行结果。
        """
        raise NotImplementedError

    async def import_batch(self, **kwargs) -> dict[str, Any]:
        """导入病症卡和知识片段。

        :param kwargs: 批次参数。
        :return: 返回函数执行结果。
        """
        raise NotImplementedError

    async def publish_batch(self, batch_id: str, *, actor_id: str | None, reason: str | None) -> dict[str, Any]:
        """发布导入批次。

        :param batch_id: 导入批次标识。
        :param actor_id: 操作人标识。
        :param reason: 操作原因。
        :return: 返回函数执行结果。
        """
        raise NotImplementedError

    async def list_batches(self, *, limit: int, offset: int) -> dict[str, Any]:
        """分页查询导入批次。

        :param limit: 返回数量上限。
        :param offset: 分页偏移量。
        :return: 返回函数执行结果。
        """
        raise NotImplementedError

    async def list_conditions(self, *, review_status: str | None, system: str | None, limit: int, offset: int) -> dict[str, Any]:
        """分页查询病症卡。

        :param review_status: 审核状态。
        :param system: 所属系统。
        :param limit: 返回数量上限。
        :param offset: 分页偏移量。
        :return: 返回函数执行结果。
        """
        raise NotImplementedError


class JsonClinicalKnowledgeStore(ClinicalKnowledgeStore):
    """开发环境使用的 JSON 文件病症卡存储。"""

    def __init__(self, store: JsonDocumentStore) -> None:
        """初始化 JSON 存储。

        :param store: JSON 文档存储。
        :return: 无返回值。
        """
        self.store = store

    async def record_failed_batch(self, **kwargs) -> dict[str, Any]:
        """记录失败的导入批次。

        :param kwargs: 批次参数。
        :return: 返回函数执行结果。
        """
        data = self.store.load()
        batch = self._batch_dict(
            batch_id=f"clinical_failed_{uuid4().hex[:8]}",
            total_conditions=0,
            total_chunks=0,
            status="failed",
            review_status="rejected",
            **kwargs,
        )
        data.setdefault("batches", []).append(batch)
        self.store.save(data)
        return batch

    async def import_batch(self, **kwargs) -> dict[str, Any]:
        """导入病症卡和知识片段。

        :param kwargs: 批次参数。
        :return: 返回函数执行结果。
        """
        data = self.store.load()
        cards = [card.to_dict() for card in kwargs["cards"]]
        chunks = [dict(chunk) for chunk in kwargs["chunks"]]
        batch = self._batch_dict(
            batch_id=kwargs["batch_id"],
            total_conditions=len(cards),
            total_chunks=len(chunks),
            status="published" if kwargs["publish"] else "imported",
            review_status=PUBLISHED_REVIEW_STATUS if kwargs["publish"] else "pending",
            source=kwargs["source"],
            version=kwargs["version"],
            actor_id=kwargs.get("actor_id"),
            source_url=kwargs.get("source_url"),
            validation_errors=[],
            metadata=kwargs.get("metadata") or {},
        )
        data.setdefault("batches", []).append(batch)
        data.setdefault("conditions", []).extend(
            {
                **card,
                "ingestion_batch": kwargs["batch_id"],
                "enabled": bool(kwargs["publish"]),
                "review_status": PUBLISHED_REVIEW_STATUS if kwargs["publish"] else "pending",
            }
            for card in cards
        )
        data.setdefault("chunks", []).extend(chunks)
        self.store.save(data)
        return batch

    async def publish_batch(self, batch_id: str, *, actor_id: str | None, reason: str | None) -> dict[str, Any]:
        """发布导入批次。

        :param batch_id: 导入批次标识。
        :param actor_id: 操作人标识。
        :param reason: 操作原因。
        :return: 返回函数执行结果。
        """
        data = self.store.load()
        found = None
        now = datetime.now(UTC).isoformat()
        for batch in data.get("batches", []):
            if batch.get("batch_id") != batch_id:
                continue
            batch["status"] = "published"
            batch["review_status"] = PUBLISHED_REVIEW_STATUS
            batch["published_by"] = actor_id
            batch["published_at"] = now
            found = batch
            break
        if found is None:
            raise KeyError("clinical knowledge batch not found")
        for condition in data.get("conditions", []):
            if condition.get("ingestion_batch") == batch_id:
                condition["enabled"] = True
                condition["review_status"] = PUBLISHED_REVIEW_STATUS
        for chunk in data.get("chunks", []):
            if chunk.get("ingestion_batch") == batch_id:
                chunk["enabled"] = True
                chunk["review_status"] = PUBLISHED_REVIEW_STATUS
        data.setdefault("audit_events", []).append(
            {
                "action": "publish_clinical_knowledge_batch",
                "batch_id": batch_id,
                "actor_id": actor_id,
                "reason": reason,
                "created_at": now,
            }
        )
        self.store.save(data)
        return found

    async def list_batches(self, *, limit: int, offset: int) -> dict[str, Any]:
        """分页查询导入批次。

        :param limit: 返回数量上限。
        :param offset: 分页偏移量。
        :return: 返回函数执行结果。
        """
        rows = list(self.store.load().get("batches", []))
        return {"items": rows[offset : offset + limit], "total": len(rows), "backend": "json"}

    async def list_conditions(self, *, review_status: str | None, system: str | None, limit: int, offset: int) -> dict[str, Any]:
        """分页查询病症卡。

        :param review_status: 审核状态。
        :param system: 所属系统。
        :param limit: 返回数量上限。
        :param offset: 分页偏移量。
        :return: 返回函数执行结果。
        """
        rows = list(self.store.load().get("conditions", []))
        if review_status:
            rows = [item for item in rows if item.get("review_status") == review_status]
        if system:
            rows = [item for item in rows if item.get("system") == system]
        return {"items": rows[offset : offset + limit], "total": len(rows), "backend": "json"}

    def _batch_dict(self, *, batch_id: str, total_conditions: int, total_chunks: int, **kwargs) -> dict[str, Any]:
        """构造 JSON 批次字典。

        :param batch_id: 导入批次标识。
        :param total_conditions: 病症卡数量。
        :param total_chunks: 知识片段数量。
        :param kwargs: 批次参数。
        :return: 返回函数执行结果。
        """
        now = datetime.now(UTC).isoformat()
        return {
            "batch_id": batch_id,
            "asset_type": "clinical_conditions",
            "source": kwargs.get("source"),
            "source_url": kwargs.get("source_url"),
            "version": kwargs.get("version"),
            "status": kwargs.get("status"),
            "review_status": kwargs.get("review_status"),
            "total_conditions": total_conditions,
            "total_chunks": total_chunks,
            "validation_errors": kwargs.get("validation_errors") or [],
            "created_by": kwargs.get("actor_id"),
            "published_by": kwargs.get("actor_id") if kwargs.get("status") == "published" else None,
            "published_at": now if kwargs.get("status") == "published" else None,
            "metadata": kwargs.get("metadata") or {},
            "created_at": now,
            "updated_at": now,
        }


class PostgresClinicalKnowledgeStore(ClinicalKnowledgeStore):
    """生产环境使用的 PostgreSQL 病症卡存储。"""

    def __init__(self, database_url: str) -> None:
        """初始化 PostgreSQL 存储。

        :param database_url: 数据库连接地址。
        :return: 无返回值。
        """
        self.session_factory = make_session_factory(database_url)

    async def record_failed_batch(self, **kwargs) -> dict[str, Any]:
        """记录失败的导入批次。

        :param kwargs: 批次参数。
        :return: 返回函数执行结果。
        """
        batch_id = f"clinical_failed_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"
        with self.session_factory.begin() as session:
            batch = KnowledgeIngestionBatchModel(
                batch_id=batch_id,
                source=kwargs["source"],
                source_url=kwargs.get("source_url"),
                version=kwargs["version"],
                status="failed",
                review_status="rejected",
                validation_errors=kwargs.get("validation_errors") or [],
                created_by=kwargs.get("actor_id"),
                metadata_json=kwargs.get("metadata") or {},
            )
            session.add(batch)
        return await self._get_batch(batch_id)

    async def import_batch(self, **kwargs) -> dict[str, Any]:
        """导入病症卡和知识片段。

        :param kwargs: 批次参数。
        :return: 返回函数执行结果。
        """
        now = datetime.now(UTC)
        review_status = PUBLISHED_REVIEW_STATUS if kwargs["publish"] else "pending"
        enabled = bool(kwargs["publish"])
        with self.session_factory.begin() as session:
            session.add(
                KnowledgeIngestionBatchModel(
                    batch_id=kwargs["batch_id"],
                    source=kwargs["source"],
                    source_url=kwargs.get("source_url"),
                    version=kwargs["version"],
                    status="published" if kwargs["publish"] else "imported",
                    review_status=review_status,
                    total_conditions=len(kwargs["cards"]),
                    total_chunks=len(kwargs["chunks"]),
                    validation_errors=[],
                    created_by=kwargs.get("actor_id"),
                    published_by=kwargs.get("actor_id") if kwargs["publish"] else None,
                    published_at=now if kwargs["publish"] else None,
                    metadata_json=kwargs.get("metadata") or {},
                )
            )
            for card in kwargs["cards"]:
                session.add(
                    ClinicalConditionCardModel(
                        condition_key=card.condition_key,
                        condition_name=card.condition_name,
                        system=card.system,
                        presentation=card.presentation,
                        differentials=card.differentials,
                        followup_questions=card.followup_questions,
                        triage=card.triage,
                        red_flags_escalate=card.red_flags_escalate,
                        medication_direction=card.medication_direction,
                        home_advice=card.home_advice,
                        source=card.source,
                        source_url=card.source_url or kwargs.get("source_url"),
                        content_hash=card.content_hash(),
                        version=kwargs["version"],
                        enabled=enabled,
                        review_status=review_status,
                        quality_score=0.9,
                        ingestion_batch=kwargs["batch_id"],
                        last_reviewed_at=now if enabled else None,
                        metadata_json=card.metadata,
                    )
                )
            for chunk in kwargs["chunks"]:
                session.add(
                    KnowledgeChunkModel(
                        source=chunk["source"],
                        title=chunk["title"],
                        content=chunk["content"],
                        embedding=chunk["embedding"],
                        public_citation=chunk["public_citation"],
                        copyright_risk=chunk["copyright_risk"],
                        domain=chunk["domain"],
                        species=chunk["species"],
                        source_url=chunk["source_url"],
                        version=chunk["version"],
                        enabled=chunk["enabled"],
                        review_status=chunk["review_status"],
                        quality_score=chunk["quality_score"],
                        last_reviewed_at=now if enabled else None,
                        ingestion_batch=chunk["ingestion_batch"],
                        metadata_json=chunk["metadata"],
                    )
                )
            session.add(
                RagAuditEventModel(
                    action="import_clinical_knowledge_batch",
                    actor_id=kwargs.get("actor_id"),
                    reason="import common condition handbook structured assets",
                    before=None,
                    after={
                        "batch_id": kwargs["batch_id"],
                        "total_conditions": len(kwargs["cards"]),
                        "total_chunks": len(kwargs["chunks"]),
                        "published": enabled,
                    },
                )
            )
        return await self._get_batch(kwargs["batch_id"])

    async def publish_batch(self, batch_id: str, *, actor_id: str | None, reason: str | None) -> dict[str, Any]:
        """发布导入批次。

        :param batch_id: 导入批次标识。
        :param actor_id: 操作人标识。
        :param reason: 操作原因。
        :return: 返回函数执行结果。
        """
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            batch = session.scalar(select(KnowledgeIngestionBatchModel).where(KnowledgeIngestionBatchModel.batch_id == batch_id))
            if batch is None:
                raise KeyError("clinical knowledge batch not found")
            before = self._batch_dict(batch)
            batch.status = "published"
            batch.review_status = PUBLISHED_REVIEW_STATUS
            batch.published_by = actor_id
            batch.published_at = now
            batch.updated_at = now
            session.execute(
                update(ClinicalConditionCardModel)
                .where(ClinicalConditionCardModel.ingestion_batch == batch_id)
                .values(
                    enabled=True,
                    review_status=PUBLISHED_REVIEW_STATUS,
                    last_reviewed_at=now,
                    updated_at=now,
                )
            )
            session.execute(
                update(KnowledgeChunkModel)
                .where(KnowledgeChunkModel.ingestion_batch == batch_id)
                .values(
                    enabled=True,
                    review_status=PUBLISHED_REVIEW_STATUS,
                    last_reviewed_at=now,
                    updated_at=now,
                )
            )
            session.add(
                RagAuditEventModel(
                    action="publish_clinical_knowledge_batch",
                    actor_id=actor_id,
                    reason=reason,
                    before=before,
                    after={"batch_id": batch_id, "status": "published", "review_status": PUBLISHED_REVIEW_STATUS},
                )
            )
        return await self._get_batch(batch_id)

    async def list_batches(self, *, limit: int, offset: int) -> dict[str, Any]:
        """分页查询导入批次。

        :param limit: 返回数量上限。
        :param offset: 分页偏移量。
        :return: 返回函数执行结果。
        """
        with self.session_factory() as session:
            total = int(session.scalar(select(func.count()).select_from(KnowledgeIngestionBatchModel)) or 0)
            rows = session.scalars(
                select(KnowledgeIngestionBatchModel)
                .order_by(KnowledgeIngestionBatchModel.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        return {"items": [self._batch_dict(row) for row in rows], "total": total, "backend": "postgres"}

    async def list_conditions(self, *, review_status: str | None, system: str | None, limit: int, offset: int) -> dict[str, Any]:
        """分页查询病症卡。

        :param review_status: 审核状态。
        :param system: 所属系统。
        :param limit: 返回数量上限。
        :param offset: 分页偏移量。
        :return: 返回函数执行结果。
        """
        filters = []
        if review_status:
            filters.append(ClinicalConditionCardModel.review_status == review_status)
        if system:
            filters.append(ClinicalConditionCardModel.system == system)
        with self.session_factory() as session:
            total = int(session.scalar(select(func.count()).select_from(ClinicalConditionCardModel).where(*filters)) or 0)
            rows = session.scalars(
                select(ClinicalConditionCardModel)
                .where(*filters)
                .order_by(ClinicalConditionCardModel.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        return {"items": [self._condition_dict(row) for row in rows], "total": total, "backend": "postgres"}

    async def _get_batch(self, batch_id: str) -> dict[str, Any]:
        """按批次标识读取批次。

        :param batch_id: 导入批次标识。
        :return: 返回函数执行结果。
        """
        with self.session_factory() as session:
            row = session.scalar(select(KnowledgeIngestionBatchModel).where(KnowledgeIngestionBatchModel.batch_id == batch_id))
        if row is None:
            raise KeyError("clinical knowledge batch not found")
        return self._batch_dict(row)

    def _batch_dict(self, row: KnowledgeIngestionBatchModel) -> dict[str, Any]:
        """转换批次模型为字典。

        :param row: 批次模型。
        :return: 返回函数执行结果。
        """
        return {
            "batch_id": row.batch_id,
            "asset_type": row.asset_type,
            "source": row.source,
            "source_url": row.source_url,
            "version": row.version,
            "status": row.status,
            "review_status": row.review_status,
            "total_conditions": row.total_conditions,
            "total_chunks": row.total_chunks,
            "validation_errors": row.validation_errors,
            "created_by": row.created_by,
            "published_by": row.published_by,
            "published_at": row.published_at.isoformat() if row.published_at else None,
            "metadata": row.metadata_json,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    def _condition_dict(self, row: ClinicalConditionCardModel) -> dict[str, Any]:
        """转换病症卡模型为字典。

        :param row: 病症卡模型。
        :return: 返回函数执行结果。
        """
        return {
            "id": row.id,
            "condition_key": row.condition_key,
            "condition_name": row.condition_name,
            "system": row.system,
            "enabled": row.enabled,
            "review_status": row.review_status,
            "quality_score": row.quality_score,
            "ingestion_batch": row.ingestion_batch,
            "content_hash": row.content_hash,
            "source": row.source,
            "source_url": row.source_url,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


def parse_condition_payload(payload: dict[str, Any]) -> tuple[list[ClinicalConditionCard], list[dict[str, Any]]]:
    """解析并校验结构化病症卡载荷。

    :param payload: JSON 载荷。
    :return: 返回函数执行结果。
    """
    raw_conditions = payload.get("conditions")
    if not isinstance(raw_conditions, list):
        return [], [{"field": "conditions", "reason": "conditions must be a list"}]

    errors: list[dict[str, Any]] = []
    cards: list[ClinicalConditionCard] = []
    seen_keys: set[str] = set()
    for index, item in enumerate(raw_conditions, start=1):
        if not isinstance(item, dict):
            errors.append({"index": index, "reason": "condition item must be an object"})
            continue
        missing = [field for field in CONDITION_REQUIRED_FIELDS if not _clean_text(item.get(field))]
        if missing:
            errors.append({"index": index, "field": ",".join(missing), "reason": "required field is empty"})
            continue
        card = ClinicalConditionCard.from_raw(item, index=index)
        if card.condition_key in seen_keys:
            errors.append({"index": index, "field": "condition", "reason": "duplicated condition key"})
            continue
        seen_keys.add(card.condition_key)
        cards.append(card)
    return cards, errors


def build_condition_chunks(card: ClinicalConditionCard) -> list[ConditionChunkDraft]:
    """将病症卡拆成字段级知识片段。

    :param card: 结构化病症卡。
    :return: 返回函数执行结果。
    """
    overview = "\n".join(
        [
            f"病症: {card.condition_name}",
            f"系统: {card.system}",
            f"典型表现: {card.presentation}",
            f"鉴别诊断: {card.differentials}",
        ]
    )
    fields = [
        ("condition_overview", overview),
        ("followup_questions", f"病症: {card.condition_name}\n该问什么: {card.followup_questions}"),
        ("triage", f"病症: {card.condition_name}\n居家 vs 就医: {card.triage}"),
        ("red_flags", f"病症: {card.condition_name}\n升级红旗: {card.red_flags_escalate}"),
        ("medication_direction", f"病症: {card.condition_name}\n用药方向: {card.medication_direction}"),
        ("home_advice", f"病症: {card.condition_name}\n居家护理: {card.home_advice}"),
    ]
    chunks = []
    for chunk_type, content in fields:
        if not _clean_text(content):
            continue
        chunks.append(
            ConditionChunkDraft(
                chunk_type=chunk_type,
                title=f"{card.condition_name} | {CHUNK_FIELD_LABELS[chunk_type]}",
                content=f"{content}\n来源: {card.source}",
                metadata={
                    "chunk_type": chunk_type,
                    "field_label": CHUNK_FIELD_LABELS[chunk_type],
                    "content_hash": card.content_hash(),
                },
            )
        )
    return chunks


def _clean_text(value: Any) -> str:
    """清洗文本字段。

    :param value: 待处理值。
    :return: 返回函数执行结果。
    """
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _stable_key(value: str) -> str:
    """根据病症名称生成稳定键。

    :param value: 待处理值。
    :return: 返回函数执行结果。
    """
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", value).strip("-")[:48]
    return f"{slug}-{digest}" if slug else digest


def _first_url(value: str) -> str | None:
    """提取文本中的第一个 URL。

    :param value: 待处理值。
    :return: 返回函数执行结果。
    """
    match = re.search(r"https?://[^\s)）]+", value)
    return match.group(0) if match else None


def _payload_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    """读取载荷元数据。

    :param payload: JSON 载荷。
    :return: 返回函数执行结果。
    """
    meta = payload.get("_meta")
    return dict(meta) if isinstance(meta, dict) else {}


def _domain_from_system(system: str) -> str:
    """根据临床系统粗略映射到现有问诊领域。

    :param system: 临床系统文本。
    :return: 返回函数执行结果。
    """
    if any(token in system for token in ("肠胃", "消化", "粪便", "胰腺")):
        return "gastrointestinal"
    if any(token in system for token in ("呼吸", "心肺", "气管")):
        return "respiratory"
    if any(token in system for token in ("关节", "骨", "活动", "疼痛", "神经")):
        return "mobility"
    if any(token in system for token in ("行为", "精神")):
        return "behavior"
    if any(token in system for token in ("营养", "喂养", "肥胖")):
        return "feeding"
    return "general"


def _species_from_text(text: str) -> str | None:
    """根据病症卡文本标注物种范围。

    :param text: 待处理文本。
    :return: 返回函数执行结果。
    """
    has_cat = "猫" in text
    has_dog = "犬" in text or "狗" in text
    if has_cat and has_dog:
        return "both"
    if has_cat:
        return "cat"
    if has_dog:
        return "dog"
    return None

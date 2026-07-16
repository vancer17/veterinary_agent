from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, unquote, urlparse
from uuid import uuid4

from sqlalchemy import delete, desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from vet_agent.config import Settings
from vet_agent.contracts import TrustedIdentity
from vet_agent.db.models import PetReportItemModel, PetReportModel
from vet_agent.db.session import make_session_factory
from vet_agent.runtime.qwen import QwenClient
from vet_agent.stores.json_store import JsonDocumentStore


RADIOLOGY_PURPOSES = {"radiology", "xray", "x_ray", "ultrasound", "ct", "mri"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


@dataclass(frozen=True)
class OssImageSource:
    original_url: str
    model_url: str
    storage_ref: str
    bucket: str
    object_key: str
    mime_type: str
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedReportItem:
    item_name: str
    value_text: str
    numeric_value: float | None = None
    unit: str | None = None
    reference_range: str | None = None
    abnormal_flag: str | None = None
    confidence: float = 0.8
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_name": self.item_name,
            "value_text": self.value_text,
            "numeric_value": self.numeric_value,
            "unit": self.unit,
            "reference_range": self.reference_range,
            "abnormal_flag": self.abnormal_flag,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class VisionParseResult:
    items: list[ParsedReportItem]
    summary: str
    ocr_text: str
    ocr_engine: str
    parser_version: str
    raw_model_response: str
    warnings: list[str] = field(default_factory=list)


class OssImageSourceValidator:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.oss_bucket
        self.prefix = settings.oss_prefix.strip("/")
        self.endpoint = settings.oss_endpoint.rstrip("/")

    def validate(self, image_url: str) -> OssImageSource:
        value = (image_url or "").strip()
        if not value:
            raise ValueError("oss_image_url is required")

        parsed = urlparse(value)
        if parsed.scheme == "oss":
            bucket = parsed.netloc
            object_key = unquote(parsed.path.lstrip("/"))
            model_url = self._build_https_url(bucket, object_key)
        elif parsed.scheme in {"http", "https"}:
            bucket, object_key = self._parse_http_url(parsed)
            model_url = value
        elif "://" not in value:
            bucket = self.bucket
            object_key = value.lstrip("/")
            model_url = self._build_https_url(bucket, object_key)
        else:
            raise ValueError("oss_image_url must be an OSS URL, HTTPS OSS URL, or OSS object key")

        self._validate_bucket_and_key(bucket, object_key)
        mime_type = self._mime_type(object_key)
        return OssImageSource(
            original_url=value,
            model_url=model_url,
            storage_ref=f"oss://{bucket}/{object_key}",
            bucket=bucket,
            object_key=object_key,
            mime_type=mime_type,
        )

    def _parse_http_url(self, parsed) -> tuple[str, str]:
        host = (parsed.hostname or "").lower()
        endpoint_hosts = self._allowed_endpoint_hosts()
        bucket_hosts = {f"{self.bucket}.{host}" for host in endpoint_hosts}
        path = unquote(parsed.path.lstrip("/"))

        if host in bucket_hosts:
            return self.bucket, path

        if host in endpoint_hosts:
            parts = path.split("/", 1)
            if len(parts) != 2:
                raise ValueError("OSS path-style URL must include bucket and object key")
            return parts[0], parts[1]

        raise ValueError("oss_image_url host is not an allowed OSS endpoint")

    def _allowed_endpoint_hosts(self) -> set[str]:
        endpoint = self.endpoint.lower()
        hosts = {endpoint}
        if "-internal" in endpoint:
            hosts.add(endpoint.replace("-internal", ""))
        return hosts

    def _build_https_url(self, bucket: str, object_key: str) -> str:
        encoded_key = quote(object_key, safe="/-_.~")
        return f"https://{bucket}.{self.endpoint}/{encoded_key}"

    def _validate_bucket_and_key(self, bucket: str, object_key: str) -> None:
        if bucket != self.bucket:
            raise ValueError(f"OSS bucket must be {self.bucket}")
        if not object_key or object_key.endswith("/"):
            raise ValueError("OSS object key is required")
        if self.prefix and not object_key.startswith(f"{self.prefix}/"):
            raise ValueError(f"OSS object key must start with {self.prefix}/")
        if self._extension(object_key) not in IMAGE_EXTENSIONS:
            raise ValueError("oss_image_url must point to a supported image file")

    def _mime_type(self, object_key: str) -> str:
        extension = self._extension(object_key)
        if extension in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if extension == ".png":
            return "image/png"
        if extension == ".webp":
            return "image/webp"
        if extension == ".bmp":
            return "image/bmp"
        return "application/octet-stream"

    def _extension(self, object_key: str) -> str:
        clean_key = object_key.split("?", 1)[0].split("#", 1)[0]
        match = re.search(r"(\.[A-Za-z0-9]+)$", clean_key)
        return match.group(1).lower() if match else ""


class ReportVisionParserAgent:
    parser_version = "oss-image-qwen-v1"

    def __init__(self, qwen_client: QwenClient, settings: Settings) -> None:
        self.qwen_client = qwen_client
        self.settings = settings
        self.line_parser = ReportLineParser()

    async def parse(self, source: OssImageSource, *, report_type: str) -> VisionParseResult:
        prompt = self._prompt(report_type)
        raw = await self.qwen_client.chat_with_images(
            prompt=prompt,
            image_urls=[source.model_url],
            model=self.settings.qwen_vision_model,
            temperature=0.0,
        )
        data = self._extract_json(raw)
        if data:
            items = self._items_from_json(data.get("items"))
            summary = str(data.get("summary") or "").strip()
            ocr_text = str(data.get("ocr_text") or "").strip()
            if not summary:
                summary = self._summary(items)
            return VisionParseResult(
                items=items,
                summary=summary,
                ocr_text=ocr_text,
                ocr_engine=f"qwen_vision:{self.settings.qwen_vision_model}",
                parser_version=self.parser_version,
                raw_model_response=raw,
            )

        items, summary = self.line_parser.parse(raw)
        return VisionParseResult(
            items=items,
            summary=summary,
            ocr_text=raw,
            ocr_engine=f"qwen_vision:{self.settings.qwen_vision_model}",
            parser_version=self.parser_version,
            raw_model_response=raw,
            warnings=["vision_model_returned_non_json"],
        )

    def _prompt(self, report_type: str) -> str:
        return (
            "You are parsing a veterinary lab report image from OSS. "
            "Extract only text and lab-measurement rows that are clearly visible in the image. "
            "Do not infer a diagnosis and do not invent missing values. "
            "Return strict JSON only, with this shape: "
            '{"summary": string, "ocr_text": string, "items": ['
            '{"item_name": string, "value_text": string, "numeric_value": number|null, '
            '"unit": string|null, "reference_range": string|null, '
            '"abnormal_flag": "high"|"low"|null, "confidence": number}'
            "]}. "
            f"Report type hint: {report_type or 'unknown'}."
        )

    def _extract_json(self, text: str) -> dict[str, Any] | None:
        candidate = text.strip()
        if candidate.startswith("```"):
            candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.I)
            candidate = re.sub(r"\s*```$", "", candidate)
        try:
            value = json.loads(candidate)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            pass

        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def _items_from_json(self, raw_items: Any) -> list[ParsedReportItem]:
        if not isinstance(raw_items, list):
            return []
        items: list[ParsedReportItem] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("item_name") or raw.get("name") or "").strip()
            value_text = str(raw.get("value_text") or raw.get("value") or "").strip()
            if not name or not value_text:
                continue
            items.append(
                ParsedReportItem(
                    item_name=name[:120],
                    value_text=value_text[:120],
                    numeric_value=self._number(raw.get("numeric_value")),
                    unit=self._optional_string(raw.get("unit")),
                    reference_range=self._optional_string(raw.get("reference_range")),
                    abnormal_flag=self._normalize_flag(raw.get("abnormal_flag")),
                    confidence=self._confidence(raw.get("confidence")),
                    metadata={"source": "qwen_vision"},
                )
            )
        return items

    def _summary(self, items: list[ParsedReportItem]) -> str:
        abnormal_count = sum(1 for item in items if item.abnormal_flag)
        if not items:
            return "Vision parser did not extract structured lab items; manual review is recommended."
        if abnormal_count:
            return f"Parsed {len(items)} lab items; {abnormal_count} item(s) include abnormal flags."
        return f"Parsed {len(items)} lab items."

    def _number(self, value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(str(value).replace("<", "").replace(">", ""))
        except ValueError:
            return None

    def _optional_string(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _normalize_flag(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip().lower()
        if text in {"h", "high", "higher", "up", "above"}:
            return "high"
        if text in {"l", "low", "lower", "down", "below"}:
            return "low"
        return None

    def _confidence(self, value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            confidence = 0.75
        return min(1.0, max(0.0, confidence))


class ReportLineParser:
    def parse(self, text: str) -> tuple[list[ParsedReportItem], str]:
        items: list[ParsedReportItem] = []
        for line in text.splitlines():
            item = self._parse_line(line)
            if item:
                items.append(item)
        abnormal_count = sum(1 for item in items if item.abnormal_flag)
        if not items:
            return [], "No structured lab items were extracted; manual review is recommended."
        if abnormal_count:
            return items, f"Parsed {len(items)} lab items; {abnormal_count} item(s) include abnormal flags."
        return items, f"Parsed {len(items)} lab items."

    def _parse_line(self, line: str) -> ParsedReportItem | None:
        text = re.sub(r"\s+", " ", line.strip())
        if len(text) < 3:
            return None
        pattern = re.compile(
            r"^(?P<name>[A-Za-z][A-Za-z0-9_ /().%+-]{1,40}?)\s+"
            r"(?P<value>[<>]?\d+(?:\.\d+)?)\s*"
            r"(?P<unit>[A-Za-z%/^0-9.-]{0,18})?\s*"
            r"(?P<ref>(?:\(?\d+(?:\.\d+)?\s*[-~]\s*\d+(?:\.\d+)?\)?))?\s*"
            r"(?P<flag>H|L|HIGH|LOW)?$",
            flags=re.I,
        )
        match = pattern.match(text)
        if not match:
            return None
        value_text = match.group("value")
        return ParsedReportItem(
            item_name=match.group("name").strip(),
            value_text=value_text,
            numeric_value=self._number(value_text),
            unit=(match.group("unit") or "").strip() or None,
            reference_range=(match.group("ref") or "").strip() or None,
            abnormal_flag=self._normalize_flag(match.group("flag")),
            confidence=0.78,
            metadata={"source_line": line[:300], "source": "fallback_line_parser"},
        )

    def _number(self, value: str) -> float | None:
        try:
            return float(value.replace("<", "").replace(">", ""))
        except ValueError:
            return None

    def _normalize_flag(self, value: str | None) -> str | None:
        if not value:
            return None
        lowered = value.lower()
        if lowered in {"h", "high"}:
            return "high"
        if lowered in {"l", "low"}:
            return "low"
        return None


class ReportIngestionService:
    def __init__(self, store: "ReportStore", qwen_client: QwenClient, settings: Settings) -> None:
        self.store = store
        self.validator = OssImageSourceValidator(settings)
        self.parser = ReportVisionParserAgent(qwen_client, settings)

    async def parse_report(
        self,
        identity: TrustedIdentity,
        *,
        oss_image_url: str,
        report_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        report_id = f"rpt_{uuid4().hex}"
        source = self.validator.validate(oss_image_url)
        safety_flags = self._safety_flags(report_type)
        status = "blocked" if safety_flags else "parsed"
        parsed = self._blocked_parse_result() if safety_flags else await self._parse_with_vision(source, report_type)
        if not safety_flags and parsed.warnings and "vision_model_failed" in parsed.warnings:
            status = "needs_ocr"

        record = {
            "report_id": report_id,
            "user_id": identity.user_id,
            "pet_id": identity.pet_id,
            "session_id": identity.session_id,
            "report_type": report_type or "unknown",
            "source_type": "oss_image_url",
            "status": status,
            "raw_text": parsed.ocr_text[:100_000],
            "summary": parsed.summary,
            "ocr_engine": parsed.ocr_engine,
            "parser_version": parsed.parser_version,
            "attachments": [self._attachment_record(report_id, report_type, source)],
            "safety_flags": safety_flags,
            "metadata": {
                "warnings": [*source.warnings, *parsed.warnings],
                "oss_bucket": source.bucket,
                "oss_object_key": source.object_key,
                "oss_endpoint": self.validator.endpoint,
                **(metadata or {}),
            },
            "items": [item.to_dict() for item in parsed.items],
            "created_at": datetime.now(UTC).isoformat(),
        }
        await self.store.save(record)
        return record

    async def list_reports(self, identity: TrustedIdentity) -> list[dict[str, Any]]:
        return await self.store.list(identity)

    async def get_report(self, identity: TrustedIdentity, report_id: str) -> dict[str, Any] | None:
        return await self.store.get(identity, report_id)

    async def _parse_with_vision(self, source: OssImageSource, report_type: str) -> VisionParseResult:
        try:
            return await self.parser.parse(source, report_type=report_type)
        except Exception as exc:
            return VisionParseResult(
                items=[],
                summary="Vision model could not parse the OSS image; manual review is recommended.",
                ocr_text="",
                ocr_engine="unavailable",
                parser_version=self.parser.parser_version,
                raw_model_response="",
                warnings=["vision_model_failed", type(exc).__name__],
            )

    def _blocked_parse_result(self) -> VisionParseResult:
        return VisionParseResult(
            items=[],
            summary="Radiology or imaging reports are blocked from online interpretation; offline veterinary review is required.",
            ocr_text="",
            ocr_engine="blocked",
            parser_version=self.parser.parser_version,
            raw_model_response="",
        )

    def _safety_flags(self, report_type: str) -> list[dict[str, Any]]:
        if (report_type or "").lower() not in RADIOLOGY_PURPOSES:
            return []
        return [
            {
                "code": "RADIOLOGY_REPORT_GATE",
                "severity": "blocked",
                "message": "Radiology reports are not interpreted online.",
            }
        ]

    def _attachment_record(self, report_id: str, report_type: str, source: OssImageSource) -> dict[str, Any]:
        return {
            "attachment_id": f"{report_id}_image",
            "mime_type": source.mime_type,
            "purpose": report_type or "lab_report",
            "storage_ref": source.storage_ref,
            "metadata": {
                "oss_bucket": source.bucket,
                "oss_object_key": source.object_key,
            },
        }


class ReportStore:
    async def save(self, record: dict[str, Any]) -> None:
        raise NotImplementedError

    async def list(self, identity: TrustedIdentity) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def get(self, identity: TrustedIdentity, report_id: str) -> dict[str, Any] | None:
        raise NotImplementedError


class JsonReportStore(ReportStore):
    def __init__(self, store: JsonDocumentStore) -> None:
        self.store = store

    async def save(self, record: dict[str, Any]) -> None:
        data = self.store.load()
        reports = data.setdefault("reports", {})
        reports[record["report_id"]] = record
        self.store.save(data)

    async def list(self, identity: TrustedIdentity) -> list[dict[str, Any]]:
        data = self.store.load()
        rows = [
            dict(row)
            for row in data.get("reports", {}).values()
            if row.get("user_id") == identity.user_id and row.get("pet_id") == identity.pet_id
        ]
        return sorted(rows, key=lambda row: row.get("created_at") or "", reverse=True)

    async def get(self, identity: TrustedIdentity, report_id: str) -> dict[str, Any] | None:
        data = self.store.load()
        row = data.get("reports", {}).get(report_id)
        if not row:
            return None
        if row.get("user_id") != identity.user_id or row.get("pet_id") != identity.pet_id:
            return None
        return dict(row)


class PostgresReportStore(ReportStore):
    def __init__(self, database_url: str) -> None:
        self.session_factory = make_session_factory(database_url)

    async def save(self, record: dict[str, Any]) -> None:
        report_values = {
            "report_id": record["report_id"],
            "user_id": record["user_id"],
            "pet_id": record["pet_id"],
            "session_id": record["session_id"],
            "report_type": record["report_type"],
            "source_type": record["source_type"],
            "status": record["status"],
            "raw_text": record["raw_text"],
            "summary": record["summary"],
            "ocr_engine": record["ocr_engine"],
            "parser_version": record["parser_version"],
            "attachments": record["attachments"],
            "safety_flags": record["safety_flags"],
            "metadata": record["metadata"],
            "updated_at": datetime.now(UTC),
        }
        statement = pg_insert(PetReportModel.__table__).values(**report_values)
        statement = statement.on_conflict_do_update(
            constraint="uq_pet_reports_report_id",
            set_={key: value for key, value in report_values.items() if key != "report_id"},
        )
        with self.session_factory.begin() as session:
            session.execute(statement)
            session.execute(delete(PetReportItemModel).where(PetReportItemModel.report_id == record["report_id"]))
            for item in record.get("items") or []:
                session.add(
                    PetReportItemModel(
                        report_id=record["report_id"],
                        item_name=item["item_name"],
                        value_text=item["value_text"],
                        numeric_value=item.get("numeric_value"),
                        unit=item.get("unit"),
                        reference_range=item.get("reference_range"),
                        abnormal_flag=item.get("abnormal_flag"),
                        confidence=float(item.get("confidence") or 0.8),
                        metadata_json=item.get("metadata") or {},
                    )
                )

    async def list(self, identity: TrustedIdentity) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            reports = session.scalars(
                select(PetReportModel)
                .where(
                    PetReportModel.user_id == identity.user_id,
                    PetReportModel.pet_id == identity.pet_id,
                )
                .order_by(desc(PetReportModel.created_at))
            ).all()
            report_ids = [row.report_id for row in reports]
            items = (
                session.scalars(select(PetReportItemModel).where(PetReportItemModel.report_id.in_(report_ids))).all()
                if report_ids
                else []
            )
        items_by_report: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            items_by_report.setdefault(item.report_id, []).append(self._item_dict(item))
        return [self._report_dict(row, items_by_report.get(row.report_id, [])) for row in reports]

    async def get(self, identity: TrustedIdentity, report_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            report = session.scalar(
                select(PetReportModel).where(
                    PetReportModel.report_id == report_id,
                    PetReportModel.user_id == identity.user_id,
                    PetReportModel.pet_id == identity.pet_id,
                )
            )
            items = session.scalars(select(PetReportItemModel).where(PetReportItemModel.report_id == report_id)).all()
        if not report:
            return None
        return self._report_dict(report, [self._item_dict(item) for item in items])

    def _report_dict(self, row: PetReportModel, items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "report_id": row.report_id,
            "user_id": row.user_id,
            "pet_id": row.pet_id,
            "session_id": row.session_id,
            "report_type": row.report_type,
            "source_type": row.source_type,
            "status": row.status,
            "raw_text": row.raw_text,
            "summary": row.summary,
            "ocr_engine": row.ocr_engine,
            "parser_version": row.parser_version,
            "attachments": row.attachments or [],
            "safety_flags": row.safety_flags or [],
            "metadata": row.metadata_json or {},
            "items": items,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    def _item_dict(self, row: PetReportItemModel) -> dict[str, Any]:
        return {
            "item_name": row.item_name,
            "value_text": row.value_text,
            "numeric_value": row.numeric_value,
            "unit": row.unit,
            "reference_range": row.reference_range,
            "abnormal_flag": row.abnormal_flag,
            "confidence": row.confidence,
            "metadata": row.metadata_json or {},
        }

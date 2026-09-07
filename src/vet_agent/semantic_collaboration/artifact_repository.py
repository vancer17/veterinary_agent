"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/artifact_repository.py
作用：实现受限语义协作 DAG M11 的 PostgreSQL 权威 artifact 仓储。
范围：覆盖回合级 artifact version 读取、review 绑定读取、最新版本读取、
      gate event 查询以及 artifact / event / stale / 索引 / 幂等请求原子提交。
说明：本文件是唯一访问 M11 SQLAlchemy 模型的仓储边界；业务层不得直接操作
      数据表，也不得通过更新或删除旧版本来修复数据。
=============================================================================
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from vet_agent.db import (
    SemanticArtifactCommitRequestModel,
    SemanticArtifactGateEventModel,
    SemanticArtifactStaleMarkerModel,
    SemanticArtifactVersionModel,
    SemanticClaimReviewIndexModel,
    SemanticClarificationGapIndexModel,
)

from .artifact_contracts import (
    SemanticArtifactActorType,
    SemanticArtifactCommitBundle,
    SemanticArtifactCommitRequestKind,
    SemanticArtifactCommitRequestRecord,
    SemanticArtifactGate,
    SemanticArtifactGateEventRecord,
    SemanticArtifactKind,
    SemanticArtifactRepository,
    SemanticArtifactVersionRecord,
    compute_artifact_payload_digest,
    parse_artifact_reference,
)
from .errors import SemanticArtifactStoreError
from .repair_contracts import compute_review_bundle_digest


def _now_text(value: datetime) -> str:
    """将数据库时间转换为稳定 UTC ISO-8601 文本。

    :param value: 数据库返回的时间。
    :return: 返回带 UTC 信息的 ISO-8601 字符串。
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _payload_from_row(
    row: SemanticArtifactVersionModel,
) -> Any:
    """从数据库 JSONB payload 解析严格 Pydantic 回合 payload。

    :param row: 权威 artifact version 数据库行。
    :return: 返回 SemanticTurnArtifactPayload。
    :raises SemanticArtifactStoreError: payload 或 digest 非法时抛出。
    """
    try:
        canonical = json.dumps(
            row.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        from .artifact_contracts import SemanticTurnArtifactPayload

        payload = SemanticTurnArtifactPayload.model_validate_json(canonical)
    except (TypeError, ValueError, ValidationError) as error:
        raise SemanticArtifactStoreError(
            "semantic artifact payload is invalid",
        ) from error
    if compute_artifact_payload_digest(payload) != row.payload_digest:
        raise SemanticArtifactStoreError("semantic artifact payload digest mismatch")
    return payload


def _record_from_row(
    row: SemanticArtifactVersionModel,
) -> SemanticArtifactVersionRecord:
    """将 artifact 数据库行转换为稳定 Pydantic 记录。

    :param row: 权威 artifact version 数据库行。
    :return: 返回通过 payload digest 校验的 version record。
    :raises SemanticArtifactStoreError: 引用、payload 或 digest 非法时抛出。
    """
    payload = _payload_from_row(row)
    try:
        artifact_id, version = parse_artifact_reference(row.artifact_reference)
    except ValueError as error:
        raise SemanticArtifactStoreError(
            "semantic artifact reference is invalid",
        ) from error
    if artifact_id != row.artifact_id or version != row.version:
        raise SemanticArtifactStoreError(
            "semantic artifact reference identity mismatch"
        )
    return SemanticArtifactVersionRecord(
        artifact_id=row.artifact_id,
        version=row.version,
        artifact_kind=SemanticArtifactKind(row.artifact_kind),
        payload=payload,
        payload_digest=row.payload_digest,
        artifact_reference=row.artifact_reference,
        base_artifact_reference=(
            None
            if row.base_artifact_id is None or row.base_version is None
            else f"semantic-artifact://{row.base_artifact_id}/{row.base_version}"
        ),
        created_at=_now_text(row.created_at),
    )


def _gate_record_from_row(
    row: SemanticArtifactGateEventModel,
) -> SemanticArtifactGateEventRecord:
    """将 gate event 数据库行转换为稳定 Pydantic 记录。

    :param row: gate event 数据库行。
    :return: 返回不可变 gate event record。
    """
    return SemanticArtifactGateEventRecord(
        artifact_id=row.artifact_id,
        version=row.version,
        sequence=row.sequence,
        from_gate=(
            None if row.from_gate is None else SemanticArtifactGate(row.from_gate)
        ),
        to_gate=SemanticArtifactGate(row.to_gate),
        reason_code=row.reason_code,
        actor_type=SemanticArtifactActorType(row.actor_type),
        decision_digest=row.decision_digest,
        decision=row.decision_json,
        created_at=_now_text(row.created_at),
    )


def _commit_record_from_row(
    row: SemanticArtifactCommitRequestModel,
) -> SemanticArtifactCommitRequestRecord:
    """将幂等提交数据库行转换为稳定 Pydantic 记录。

    :param row: artifact commit request 数据库行。
    :return: 返回既有幂等提交结果。
    """
    return SemanticArtifactCommitRequestRecord(
        request_key=row.request_key,
        request_kind=SemanticArtifactCommitRequestKind(row.request_kind),
        artifact_id=row.artifact_id,
        version=row.version,
        payload_digest=row.payload_digest,
        result_reference=row.result_reference,
        created_at=_now_text(row.created_at),
    )


class PostgresSemanticArtifactRepository(SemanticArtifactRepository):
    """表示基于 PostgreSQL 的 M11 权威 artifact 仓储实现。

    :return: 无返回值；本实现只追加数据，不提供 UPDATE / DELETE 业务入口。
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
    ) -> None:
        """初始化 PostgreSQL artifact 仓储。

        :param session_factory: SQLAlchemy session factory。
        :return: 无返回值。
        """
        self._session_factory = session_factory

    def get_artifact(
        self,
        artifact_reference: str,
    ) -> SemanticArtifactVersionRecord | None:
        """按权威引用读取 artifact version。

        :param artifact_reference: M11 稳定 artifact 引用。
        :return: 找到时返回不可变 version record，否则返回 None。
        :raises SemanticArtifactStoreError: 数据库访问失败时抛出。
        """
        try:
            artifact_id, version = parse_artifact_reference(artifact_reference)
        except ValueError as error:
            raise SemanticArtifactStoreError(
                "semantic artifact reference is invalid",
            ) from error
        try:
            with self._session_factory() as session:
                row = session.get(
                    SemanticArtifactVersionModel,
                    (artifact_id, version),
                )
                return None if row is None else _record_from_row(row)
        except SQLAlchemyError as error:
            raise SemanticArtifactStoreError(
                "semantic artifact store read failed",
            ) from error

    def get_artifact_by_review(
        self,
        source_proposal_digest: str,
        review_bundle_digest: str,
    ) -> SemanticArtifactVersionRecord | None:
        """按来源 proposal 与 review bundle 摘要读取 version。

        :param source_proposal_digest: Claim Inventory proposal 摘要。
        :param review_bundle_digest: M08 Review Bundle 摘要。
        :return: 找到时返回 version record，否则返回 None。
        :raises SemanticArtifactStoreError: 数据库访问失败时抛出。
        """
        statement = select(SemanticArtifactVersionModel).where(
            SemanticArtifactVersionModel.source_proposal_digest
            == source_proposal_digest,
            SemanticArtifactVersionModel.review_bundle_digest == review_bundle_digest,
        )
        try:
            with self._session_factory() as session:
                row = session.scalars(statement).first()
                return None if row is None else _record_from_row(row)
        except SQLAlchemyError as error:
            raise SemanticArtifactStoreError(
                "semantic artifact store read failed",
            ) from error

    def get_latest_artifact(
        self,
        run_id: str,
        source_task_id: str,
        turn_snapshot_digest: str,
    ) -> SemanticArtifactVersionRecord | None:
        """读取指定回合链的最新 artifact version。

        :param run_id: 语义 DAG workflow 标识。
        :param source_task_id: Claim Inventory 来源任务标识。
        :param turn_snapshot_digest: 当前回合上下文摘要。
        :return: 找到时返回最新 version record，否则返回 None。
        :raises SemanticArtifactStoreError: 数据库访问失败时抛出。
        """
        statement = (
            select(SemanticArtifactVersionModel)
            .where(
                SemanticArtifactVersionModel.run_id == run_id,
                SemanticArtifactVersionModel.source_task_id == source_task_id,
                SemanticArtifactVersionModel.turn_snapshot_digest
                == turn_snapshot_digest,
            )
            .order_by(SemanticArtifactVersionModel.version.desc())
            .limit(1)
        )
        try:
            with self._session_factory() as session:
                row = session.scalars(statement).first()
                return None if row is None else _record_from_row(row)
        except SQLAlchemyError as error:
            raise SemanticArtifactStoreError(
                "semantic artifact store read failed",
            ) from error

    def get_commit_request(
        self,
        request_key: str,
    ) -> SemanticArtifactCommitRequestRecord | None:
        """读取既有幂等提交请求。

        :param request_key: 稳定请求键。
        :return: 找到时返回既有提交结果，否则返回 None。
        :raises SemanticArtifactStoreError: 数据库访问失败时抛出。
        """
        try:
            with self._session_factory() as session:
                row = session.get(
                    SemanticArtifactCommitRequestModel,
                    request_key,
                )
                return None if row is None else _commit_record_from_row(row)
        except SQLAlchemyError as error:
            raise SemanticArtifactStoreError(
                "semantic artifact commit request read failed",
            ) from error

    def get_current_gate(
        self,
        artifact_reference: str,
    ) -> SemanticArtifactGateEventRecord | None:
        """读取 artifact version 当前最大 sequence gate event。

        :param artifact_reference: M11 稳定 artifact 引用。
        :return: 找到时返回当前 gate event，否则返回 None。
        :raises SemanticArtifactStoreError: 数据库访问失败时抛出。
        """
        try:
            artifact_id, version = parse_artifact_reference(artifact_reference)
        except ValueError as error:
            raise SemanticArtifactStoreError(
                "semantic artifact reference is invalid",
            ) from error
        statement = (
            select(SemanticArtifactGateEventModel)
            .where(
                SemanticArtifactGateEventModel.artifact_id == artifact_id,
                SemanticArtifactGateEventModel.version == version,
            )
            .order_by(SemanticArtifactGateEventModel.sequence.desc())
            .limit(1)
        )
        try:
            with self._session_factory() as session:
                row = session.scalars(statement).first()
                return None if row is None else _gate_record_from_row(row)
        except SQLAlchemyError as error:
            raise SemanticArtifactStoreError(
                "semantic artifact gate read failed",
            ) from error

    def is_artifact_stale(
        self,
        artifact_reference: str,
    ) -> bool:
        """判断 artifact version 是否已被显式 stale marker 取代。

        :param artifact_reference: M11 稳定 artifact 引用。
        :return: 存在 stale marker 时返回 True。
        :raises SemanticArtifactStoreError: 引用或数据库访问失败时抛出。
        """
        try:
            artifact_id, version = parse_artifact_reference(artifact_reference)
        except ValueError as error:
            raise SemanticArtifactStoreError(
                "semantic artifact reference is invalid",
            ) from error
        statement = select(SemanticArtifactStaleMarkerModel).where(
            SemanticArtifactStaleMarkerModel.artifact_id == artifact_id,
            SemanticArtifactStaleMarkerModel.version == version,
        )
        try:
            with self._session_factory() as session:
                return session.scalars(statement).first() is not None
        except SQLAlchemyError as error:
            raise SemanticArtifactStoreError(
                "semantic artifact stale marker read failed",
            ) from error

    def commit(
        self,
        bundle: SemanticArtifactCommitBundle,
    ) -> SemanticArtifactCommitRequestRecord:
        """原子插入 artifact version、事件、索引与幂等请求。

        :param bundle: 完整 M11 append-only 提交负载。
        :return: 返回新建或并发重放后的既有幂等提交结果。
        :raises SemanticArtifactStoreError: 身份冲突、数据库失败或数据损坏时抛出。
        """
        try:
            with self._session_factory.begin() as session:
                existing = session.get(
                    SemanticArtifactCommitRequestModel,
                    bundle.request_key,
                )
                if existing is not None:
                    return _commit_record_from_row(existing)
                artifact = bundle.artifact
                session.add(
                    SemanticArtifactVersionModel(
                        artifact_id=artifact.artifact_id,
                        version=artifact.version,
                        artifact_kind=artifact.artifact_kind.value,
                        artifact_reference=artifact.artifact_reference,
                        run_id=artifact.payload.run_id,
                        source_task_id=artifact.payload.source_task_id,
                        turn_snapshot_digest=artifact.payload.turn_snapshot_digest,
                        source_proposal_digest=artifact.payload.source_proposal_digest,
                        review_bundle_digest=(
                            None
                            if artifact.payload.review_bundle is None
                            else compute_review_bundle_digest(
                                artifact.payload.review_bundle,
                            )
                        ),
                        payload=artifact.payload.model_dump(mode="json"),
                        payload_digest=artifact.payload_digest,
                        base_artifact_id=(
                            None
                            if artifact.base_artifact_reference is None
                            else parse_artifact_reference(
                                artifact.base_artifact_reference,
                            )[0]
                        ),
                        base_version=(
                            None
                            if artifact.base_artifact_reference is None
                            else parse_artifact_reference(
                                artifact.base_artifact_reference,
                            )[1]
                        ),
                        repair_depth=artifact.payload.repair_depth,
                        created_at=datetime.now(UTC),
                    ),
                )
                for event in bundle.gate_events:
                    session.add(
                        SemanticArtifactGateEventModel(
                            artifact_id=event.artifact_id,
                            version=event.version,
                            sequence=event.sequence,
                            from_gate=(
                                None
                                if event.from_gate is None
                                else event.from_gate.value
                            ),
                            to_gate=event.to_gate.value,
                            reason_code=event.reason_code,
                            actor_type=event.actor_type.value,
                            decision_digest=event.decision_digest,
                            decision_json=event.decision,
                            created_at=datetime.now(UTC),
                        ),
                    )
                for marker in bundle.stale_markers:
                    session.add(
                        SemanticArtifactStaleMarkerModel(
                            artifact_id=marker.artifact_id,
                            version=marker.version,
                            stale_reason=marker.stale_reason.value,
                            replaced_by_artifact_id=marker.replaced_by_artifact_id,
                            replaced_by_version=marker.replaced_by_version,
                            created_at=datetime.now(UTC),
                        ),
                    )
                for claim in bundle.claim_review_index:
                    session.add(
                        SemanticClaimReviewIndexModel(
                            artifact_id=claim.artifact_id,
                            version=claim.version,
                            claim_index=claim.claim_index,
                            run_id=claim.run_id,
                            claim_digest=claim.claim_digest,
                            proposition=claim.proposition,
                            review_status=claim.review_status.value,
                            repair_dimensions=[
                                dimension.value for dimension in claim.repair_dimensions
                            ],
                            created_at=datetime.now(UTC),
                        ),
                    )
                for gap in bundle.clarification_gap_index:
                    session.add(
                        SemanticClarificationGapIndexModel(
                            artifact_id=gap.artifact_id,
                            version=gap.version,
                            gap_id=gap.gap_id,
                            run_id=gap.run_id,
                            claim_index=gap.claim_index,
                            claim_digest=gap.claim_digest,
                            ambiguous_dimension=gap.ambiguous_dimension.value,
                            required_binding_type=gap.required_binding_type.value,
                            status=gap.status.value,
                            created_at=datetime.now(UTC),
                        ),
                    )
                result = SemanticArtifactCommitRequestRecord(
                    request_key=bundle.request_key,
                    request_kind=bundle.request_kind,
                    artifact_id=artifact.artifact_id,
                    version=artifact.version,
                    payload_digest=artifact.payload_digest,
                    result_reference=artifact.artifact_reference,
                    created_at=_now_text(datetime.now(UTC)),
                )
                session.add(
                    SemanticArtifactCommitRequestModel(
                        request_key=result.request_key,
                        request_kind=result.request_kind.value,
                        artifact_id=result.artifact_id,
                        version=result.version,
                        payload_digest=result.payload_digest,
                        result_reference=result.result_reference,
                        created_at=datetime.now(UTC),
                    ),
                )
                session.flush()
                return result
        except IntegrityError:
            existing_request = self.get_commit_request(bundle.request_key)
            if existing_request is not None:
                return existing_request
            raise SemanticArtifactStoreError(
                "semantic artifact commit identity conflict",
            ) from None
        except SQLAlchemyError as error:
            raise SemanticArtifactStoreError(
                "semantic artifact commit failed",
            ) from error

    def append_gate_event(
        self,
        event: SemanticArtifactGateEventRecord,
        *,
        request_key: str,
        payload_digest: str,
    ) -> SemanticArtifactCommitRequestRecord:
        """向既有 artifact version 追加不可变 gate event。

        :param event: 待追加的 gate event。
        :param request_key: gate event 幂等键。
        :param payload_digest: 结构化决策 digest。
        :return: 返回新建或并发重放后的既有幂等提交结果。
        :raises SemanticArtifactStoreError: artifact 缺失、序列冲突或数据库失败时抛出。
        """
        try:
            with self._session_factory.begin() as session:
                existing = session.get(
                    SemanticArtifactCommitRequestModel,
                    request_key,
                )
                if existing is not None:
                    return _commit_record_from_row(existing)
                artifact = session.get(
                    SemanticArtifactVersionModel,
                    (event.artifact_id, event.version),
                )
                if artifact is None:
                    raise SemanticArtifactStoreError(
                        "semantic artifact gate event target is missing",
                    )
                session.add(
                    SemanticArtifactGateEventModel(
                        artifact_id=event.artifact_id,
                        version=event.version,
                        sequence=event.sequence,
                        from_gate=(
                            None if event.from_gate is None else event.from_gate.value
                        ),
                        to_gate=event.to_gate.value,
                        reason_code=event.reason_code,
                        actor_type=event.actor_type.value,
                        decision_digest=event.decision_digest,
                        decision_json=event.decision,
                        created_at=datetime.now(UTC),
                    ),
                )
                result = SemanticArtifactCommitRequestRecord(
                    request_key=request_key,
                    request_kind=SemanticArtifactCommitRequestKind.GATE_EVENT,
                    artifact_id=event.artifact_id,
                    version=event.version,
                    payload_digest=payload_digest,
                    result_reference=artifact.artifact_reference,
                    created_at=_now_text(datetime.now(UTC)),
                )
                session.add(
                    SemanticArtifactCommitRequestModel(
                        request_key=result.request_key,
                        request_kind=result.request_kind.value,
                        artifact_id=result.artifact_id,
                        version=result.version,
                        payload_digest=result.payload_digest,
                        result_reference=result.result_reference,
                        created_at=datetime.now(UTC),
                    ),
                )
                session.flush()
                return result
        except IntegrityError:
            existing_request = self.get_commit_request(request_key)
            if existing_request is not None:
                return existing_request
            raise SemanticArtifactStoreError(
                "semantic artifact gate event conflict",
            ) from None
        except SQLAlchemyError as error:
            raise SemanticArtifactStoreError(
                "semantic artifact gate event commit failed",
            ) from error


__all__ = ["PostgresSemanticArtifactRepository"]

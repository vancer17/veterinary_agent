"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/artifact_store.py
作用：实现受限语义协作 DAG M11 的回合级 Artifact Store 服务。
范围：覆盖 M08 Review Bundle 提交、M10 base snapshot 解析、M10 patch set
      原子提交、版本链与 gate event 生成、stale marker 与派生索引写入。
说明：本文件不调用模型、不执行 M07/M08 re-review、不做医学判断、不生成
      用户追问，也不把 semantic_review_supported 直接提升为 verified。
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy.orm import Session, sessionmaker

from .artifact_contracts import (
    SemanticArtifactActorType,
    SemanticArtifactClaimRecord,
    SemanticArtifactCommitBundle,
    SemanticArtifactCommitRequestKind,
    SemanticArtifactCommitRequestRecord,
    SemanticArtifactGate,
    SemanticArtifactGateEventRecord,
    SemanticArtifactKind,
    SemanticArtifactRepairLineage,
    SemanticArtifactRepository,
    SemanticArtifactStaleMarkerRecord,
    SemanticArtifactStaleReason,
    SemanticArtifactVersionRecord,
    SemanticClaimReviewIndexRecord,
    SemanticClaimReviewStatus,
    SemanticClarificationGapIndexRecord,
    SemanticClarificationGapStatus,
    SemanticSystemReviewRecord,
    SemanticTurnArtifactPayload,
    build_artifact_reference,
    canonical_artifact_json,
    compute_artifact_payload_digest,
    compute_semantic_artifact_id,
    parse_artifact_reference,
    semantic_review_outcome_to_gate,
)
from .artifact_repository import PostgresSemanticArtifactRepository
from .errors import SemanticArtifactStoreError
from .patch_applier import DeterministicPatchApplier
from .patch_contracts import (
    PatchApplicationPreview,
    PatchApplicationState,
    RepairPatchStore,
    RepairTargetArtifactSnapshot,
    RepairTargetSnapshotResolver,
    SemanticPatchSet,
)
from .repair_contracts import ReviewRepairDimension, compute_review_bundle_digest
from .review_contracts import (
    ReviewArtifactStore,
    SemanticReviewBundle,
    compute_claim_digest,
)


def _now_text() -> str:
    """读取当前 UTC ISO-8601 时间。

    :return: 返回带 UTC 信息的稳定时间字符串。
    """
    return datetime.now(UTC).isoformat()


def _decision_digest(value: object) -> str:
    """计算结构化决策 canonical digest。

    :param value: Pydantic 契约对象或可序列化字典。
    :return: 返回 SHA-256 决策摘要。
    """
    return sha256(canonical_artifact_json(value).encode("utf-8")).hexdigest()


def _claim_records(
    claims: tuple[str, ...],
) -> tuple[SemanticArtifactClaimRecord, ...]:
    """将 claim inventory 转换为回合 payload 内的系统派生记录。

    :param claims: 自包含 claim proposition 集合。
    :return: 返回带连续 index 与 digest 的 claim record 集合。
    """
    return tuple(
        SemanticArtifactClaimRecord(
            claim_index=index,
            claim_digest=compute_claim_digest(claim),
            proposition=claim,
        )
        for index, claim in enumerate(claims)
    )


class PostgresSemanticArtifactStore(
    RepairTargetSnapshotResolver,
    RepairPatchStore,
    ReviewArtifactStore,
):
    """表示基于 M11 仓储的回合级权威 Artifact Store 服务。

    :return: 无返回值；服务生成不可变版本，不提供旧版本更新路径。
    """

    def __init__(
        self,
        repository: SemanticArtifactRepository,
    ) -> None:
        """初始化 M11 Artifact Store。

        :param repository: M11 权威 artifact 仓储端口。
        :return: 无返回值。
        """
        self.repository = repository

    async def load(
        self,
        source_proposal_digest: str,
        review_bundle_digest: str,
    ) -> RepairTargetArtifactSnapshot:
        """读取 M10 patch 绑定的权威 base artifact snapshot。

        :param source_proposal_digest: 被修复 Claim Inventory proposal 摘要。
        :param review_bundle_digest: M08 Review Bundle 摘要。
        :return: 返回 M11 权威 base claims 与版本绑定。
        :raises SemanticArtifactStoreError: artifact 缺失、损坏或不可修复时抛出。
        """
        record = self.repository.get_artifact_by_review(
            source_proposal_digest,
            review_bundle_digest,
        )
        if record is None:
            raise SemanticArtifactStoreError("repair target artifact is not found")
        self._validate_artifact(record)
        if record.payload.repair_depth != 0:
            raise SemanticArtifactStoreError(
                "repair target artifact is already repaired"
            )
        return RepairTargetArtifactSnapshot(
            source_proposal_digest=record.payload.source_proposal_digest,
            review_bundle_digest=review_bundle_digest,
            turn_snapshot_digest=record.payload.turn_snapshot_digest,
            artifact_reference=record.artifact_reference,
            base_version=record.version,
            repair_depth=record.payload.repair_depth,
            claims=tuple(claim.proposition for claim in record.payload.claims),
        )

    async def commit_review_bundle(
        self,
        bundle: SemanticReviewBundle,
    ) -> str:
        """以幂等方式提交 M08 Review Bundle 并生成回合级 artifact。

        :param bundle: 已完成 M08 验证与确定性派生的审查聚合。
        :return: 返回 M11 权威 artifact 引用。
        :raises SemanticArtifactStoreError: 身份冲突、claims 漂移或提交失败时抛出。
        """
        review_digest = compute_review_bundle_digest(bundle)
        request_key = f"review:{review_digest}"
        existing = self.repository.get_commit_request(request_key)
        if existing is not None:
            if (
                existing.request_kind
                is not SemanticArtifactCommitRequestKind.REVIEW_BUNDLE
            ):
                raise SemanticArtifactStoreError(
                    "semantic artifact commit request conflict",
                )
            return existing.result_reference
        latest = self.repository.get_latest_artifact(
            bundle.run_id,
            bundle.source_task_id,
            bundle.turn_snapshot_digest,
        )
        if latest is not None:
            self._validate_artifact(latest)
            if latest.artifact_id != compute_semantic_artifact_id(
                run_id=bundle.run_id,
                source_task_id=bundle.source_task_id,
                turn_snapshot_digest=bundle.turn_snapshot_digest,
            ):
                raise SemanticArtifactStoreError(
                    "latest semantic artifact chain identity mismatch",
                )
            if latest.payload.claims != _claim_records(bundle.claims):
                raise SemanticArtifactStoreError(
                    "review bundle claims do not match latest artifact",
                )
            if latest.payload.source_proposal_digest != bundle.source_proposal_digest:
                raise SemanticArtifactStoreError(
                    "review bundle source proposal does not match artifact chain",
                )
        payload = SemanticTurnArtifactPayload(
            artifact_kind=SemanticArtifactKind.SEMANTIC_TURN_RESULT,
            run_id=bundle.run_id,
            source_task_id=bundle.source_task_id,
            source_attempt_number=bundle.source_attempt_number,
            turn_snapshot_digest=bundle.turn_snapshot_digest,
            source_proposal_digest=bundle.source_proposal_digest,
            claims=_claim_records(bundle.claims),
            review_bundle=bundle,
            repair_lineage=(None if latest is None else latest.payload.repair_lineage),
            repair_depth=0 if latest is None else latest.payload.repair_depth,
        )
        payload_digest = compute_artifact_payload_digest(payload)
        artifact_id = compute_semantic_artifact_id(
            run_id=bundle.run_id,
            source_task_id=bundle.source_task_id,
            turn_snapshot_digest=bundle.turn_snapshot_digest,
        )
        version = 1 if latest is None else latest.version + 1
        artifact = self._build_artifact_record(
            payload=payload,
            payload_digest=payload_digest,
            artifact_id=artifact_id,
            version=version,
            base_reference=None if latest is None else latest.artifact_reference,
        )
        current_gate = (
            None
            if latest is None
            else self.repository.get_current_gate(latest.artifact_reference)
        )
        gate = semantic_review_outcome_to_gate(bundle.aggregate_outcome)
        events = [
            self._gate_event(
                artifact,
                1,
                None if current_gate is None else current_gate.to_gate,
                gate,
                bundle,
            ),
        ]
        if gate is SemanticArtifactGate.SEMANTIC_REVIEW_SUPPORTED:
            events.append(
                self._gate_event(
                    artifact,
                    2,
                    SemanticArtifactGate.SEMANTIC_REVIEW_SUPPORTED,
                    SemanticArtifactGate.EVIDENCE_BINDING_PENDING,
                    bundle,
                ),
            )
        commit_bundle = SemanticArtifactCommitBundle(
            request_key=request_key,
            request_kind=SemanticArtifactCommitRequestKind.REVIEW_BUNDLE,
            artifact=artifact,
            gate_events=tuple(events),
            stale_markers=(
                ()
                if latest is None
                else (
                    self._stale_marker(
                        latest,
                        artifact,
                        reason="superseded_by_review",
                    ),
                )
            ),
            claim_review_index=self._claim_index(artifact),
            clarification_gap_index=self._gap_index(artifact),
        )
        return self.repository.commit(commit_bundle).result_reference

    async def commit(
        self,
        patch_set: SemanticPatchSet,
        preview: PatchApplicationPreview,
    ) -> str:
        """以幂等方式原子提交 M10 verified patch set。

        :param patch_set: 通过 M10 patch set verifier 的 typed patch 集合。
        :param preview: M10 deterministic application preview。
        :return: 返回修复后新版本 artifact 引用。
        :raises SemanticArtifactStoreError: base 绑定、preview、版本或提交冲突时抛出。
        """
        if not isinstance(preview, PatchApplicationPreview):
            raise SemanticArtifactStoreError("repair patch preview type is invalid")
        if preview.state is not PatchApplicationState.PREVIEW_READY:
            raise SemanticArtifactStoreError("repair patch preview is not ready")
        base = self.repository.get_artifact(patch_set.artifact_reference)
        if base is None:
            raise SemanticArtifactStoreError("repair patch base artifact is not found")
        self._validate_artifact(base)
        if base.version != patch_set.base_version:
            raise SemanticArtifactStoreError("repair patch base version mismatch")
        if base.payload.repair_depth != 0:
            raise SemanticArtifactStoreError(
                "repair target artifact is already repaired"
            )
        if base.payload.review_bundle is None:
            raise SemanticArtifactStoreError("repair base review bundle is missing")
        snapshot = await self.load(
            base.payload.source_proposal_digest,
            compute_review_bundle_digest(base.payload.review_bundle),
        )
        recomputed_preview = DeterministicPatchApplier().preview(patch_set, snapshot)
        if recomputed_preview != preview:
            raise SemanticArtifactStoreError("repair patch preview mismatch")
        dimensions = tuple(
            dimension
            for patch in patch_set.patches
            for dimension in patch.repair_task.review_dimensions
        )
        payload = SemanticTurnArtifactPayload(
            artifact_kind=SemanticArtifactKind.SEMANTIC_TURN_RESULT,
            run_id=base.payload.run_id,
            source_task_id=base.payload.source_task_id,
            source_attempt_number=base.payload.source_attempt_number,
            turn_snapshot_digest=base.payload.turn_snapshot_digest,
            source_proposal_digest=base.payload.source_proposal_digest,
            claims=_claim_records(preview.claims),
            review_bundle=None,
            repair_lineage=SemanticArtifactRepairLineage(
                repair_plan_id=patch_set.repair_plan_id,
                patch_set=patch_set,
                base_artifact_reference=base.artifact_reference,
                base_version=base.version,
                repair_dimensions=dimensions,
            ),
            repair_depth=1,
        )
        payload_digest = compute_artifact_payload_digest(payload)
        request_key = f"repair:{patch_set.repair_plan_id}"
        existing = self.repository.get_commit_request(request_key)
        if existing is not None:
            return self._validate_existing_request(
                existing=existing,
                request_kind=SemanticArtifactCommitRequestKind.REPAIR_PATCH_SET,
                payload_digest=payload_digest,
            )
        artifact = self._build_artifact_record(
            payload=payload,
            payload_digest=payload_digest,
            artifact_id=base.artifact_id,
            version=base.version + 1,
            base_reference=base.artifact_reference,
        )
        current_gate = self.repository.get_current_gate(base.artifact_reference)
        commit_bundle = SemanticArtifactCommitBundle(
            request_key=request_key,
            request_kind=SemanticArtifactCommitRequestKind.REPAIR_PATCH_SET,
            artifact=artifact,
            gate_events=(
                self._gate_event(
                    artifact,
                    1,
                    (None if current_gate is None else current_gate.to_gate),
                    SemanticArtifactGate.PATCH_APPLIED,
                    patch_set,
                ),
            ),
            stale_markers=(
                self._stale_marker(
                    base,
                    artifact,
                    reason="superseded_by_repair",
                ),
            ),
            claim_review_index=self._repair_pending_index(artifact),
            clarification_gap_index=(),
        )
        return self.repository.commit(commit_bundle).result_reference

    def get_current_artifact(
        self,
        *,
        run_id: str,
        source_task_id: str,
        turn_snapshot_digest: str,
    ) -> SemanticArtifactVersionRecord | None:
        """读取指定回合当前最新 artifact。

        :param run_id: 语义 DAG workflow 标识。
        :param source_task_id: Claim Inventory 来源任务标识。
        :param turn_snapshot_digest: 当前回合上下文摘要。
        :return: 找到时返回最新 version record。
        """
        record = self.repository.get_latest_artifact(
            run_id,
            source_task_id,
            turn_snapshot_digest,
        )
        if record is not None:
            self._validate_artifact(record)
        return record

    def get_current_gate(
        self,
        artifact_reference: str,
    ) -> SemanticArtifactGateEventRecord | None:
        """读取 artifact 当前 gate event。

        :param artifact_reference: M11 稳定 artifact 引用。
        :return: 找到时返回当前 gate event。
        """
        return self.repository.get_current_gate(artifact_reference)

    def record_system_review(
        self,
        artifact_reference: str,
        review: SemanticSystemReviewRecord,
    ) -> str:
        """记录面向系统性缺陷的人工审查事件。

        :param artifact_reference: M11 稳定 artifact 引用。
        :param review: 系统性人工审查记录。
        :return: 返回当前 artifact 引用；该记录不改变 verified gate。
        :raises SemanticArtifactStoreError: artifact 缺失或 gate 事件冲突时抛出。
        """
        artifact = self.repository.get_artifact(artifact_reference)
        if artifact is None:
            raise SemanticArtifactStoreError("semantic artifact is not found")
        self._validate_artifact(artifact)
        current = self.repository.get_current_gate(artifact_reference)
        if current is None:
            raise SemanticArtifactStoreError("semantic artifact gate is missing")
        event = SemanticArtifactGateEventRecord(
            artifact_id=artifact.artifact_id,
            version=artifact.version,
            sequence=current.sequence + 1,
            from_gate=current.to_gate,
            to_gate=current.to_gate,
            reason_code=f"system_human_review_{review.status.value}",
            actor_type=SemanticArtifactActorType.HUMAN_REVIEWER,
            decision_digest=_decision_digest(review),
            decision=review.model_dump(mode="json"),
            created_at=_now_text(),
        )
        return self.repository.append_gate_event(
            event,
            request_key=f"human-review:{review.review_id}",
            payload_digest=_decision_digest(review),
        ).result_reference

    def _validate_artifact(
        self,
        record: SemanticArtifactVersionRecord,
    ) -> None:
        """校验 artifact record 的引用、digest 与 payload 身份。

        :param record: 从仓储读取的 artifact version record。
        :return: 无返回值。
        :raises SemanticArtifactStoreError: artifact 数据损坏时抛出。
        """
        artifact_id, version = parse_artifact_reference(record.artifact_reference)
        if (
            artifact_id != record.artifact_id
            or version != record.version
            or compute_artifact_payload_digest(record.payload) != record.payload_digest
        ):
            raise SemanticArtifactStoreError("semantic artifact record is corrupted")

    def _validate_existing_request(
        self,
        *,
        existing: SemanticArtifactCommitRequestRecord,
        request_kind: SemanticArtifactCommitRequestKind,
        payload_digest: str,
    ) -> str:
        """校验幂等重放请求与当前 payload 完全一致。

        :param existing: 既有 M11 幂等提交记录。
        :param request_kind: 当前请求类型。
        :param payload_digest: 当前构建 payload 的 canonical digest。
        :return: 返回既有 artifact 引用。
        :raises SemanticArtifactStoreError: 同 key 不同 payload 时抛出。
        """
        if (
            existing.request_kind is not request_kind
            or existing.payload_digest != payload_digest
        ):
            raise SemanticArtifactStoreError(
                "semantic artifact commit request conflict",
            )
        return existing.result_reference

    def _build_artifact_record(
        self,
        *,
        payload: SemanticTurnArtifactPayload,
        payload_digest: str,
        artifact_id: str,
        version: int,
        base_reference: str | None,
    ) -> SemanticArtifactVersionRecord:
        """构造待插入的不可变 artifact version record。

        :param payload: 回合级权威 payload。
        :param payload_digest: payload canonical digest。
        :param artifact_id: 回合链稳定身份。
        :param version: 新版本号。
        :param base_reference: base artifact 引用；初始版本为空。
        :return: 返回待写入 M11 的 artifact record。
        """
        return SemanticArtifactVersionRecord(
            artifact_id=artifact_id,
            version=version,
            artifact_kind=payload.artifact_kind,
            payload=payload,
            payload_digest=payload_digest,
            artifact_reference=build_artifact_reference(
                artifact_id=artifact_id,
                version=version,
            ),
            base_artifact_reference=base_reference,
            created_at=_now_text(),
        )

    def _gate_event(
        self,
        artifact: SemanticArtifactVersionRecord,
        sequence: int,
        from_gate: SemanticArtifactGate | None,
        to_gate: SemanticArtifactGate,
        decision: object,
    ) -> SemanticArtifactGateEventRecord:
        """构造 append-only gate event。

        :param artifact: 当前新 artifact version。
        :param sequence: 当前 version 内的事件顺序。
        :param from_gate: 迁移前 gate 状态。
        :param to_gate: 迁移后 gate 状态。
        :param decision: 结构化决策对象。
        :return: 返回 gate event record。
        """
        decision_object = (
            decision.model_dump(mode="json")
            if isinstance(decision, SemanticReviewBundle | SemanticPatchSet)
            else {"value": canonical_artifact_json(decision)}
        )
        return SemanticArtifactGateEventRecord(
            artifact_id=artifact.artifact_id,
            version=artifact.version,
            sequence=sequence,
            from_gate=from_gate,
            to_gate=to_gate,
            reason_code=f"semantic_artifact_{to_gate.value}",
            actor_type=SemanticArtifactActorType.SYSTEM,
            decision_digest=_decision_digest(decision),
            decision=dict(decision_object),
            created_at=_now_text(),
        )

    def _stale_marker(
        self,
        previous: SemanticArtifactVersionRecord,
        replacement: SemanticArtifactVersionRecord,
        *,
        reason: str,
    ) -> SemanticArtifactStaleMarkerRecord:
        """构造旧版本 stale marker。

        :param previous: 被取代的 artifact version。
        :param replacement: 取代旧版本的新 artifact version。
        :param reason: 稳定 stale 原因。
        :return: 返回 stale marker record。
        :raises SemanticArtifactStoreError: stale 原因非法时抛出。
        """
        return SemanticArtifactStaleMarkerRecord(
            artifact_id=previous.artifact_id,
            version=previous.version,
            stale_reason=SemanticArtifactStaleReason(reason),
            replaced_by_artifact_id=replacement.artifact_id,
            replaced_by_version=replacement.version,
            created_at=_now_text(),
        )

    def _claim_index(
        self,
        artifact: SemanticArtifactVersionRecord,
    ) -> tuple[SemanticClaimReviewIndexRecord, ...]:
        """从 artifact review payload 派生 claim review 索引。

        :param artifact: 包含 M08 review bundle 的 artifact version。
        :return: 返回逐 claim 查询索引集合。
        """
        bundle = artifact.payload.review_bundle
        if bundle is None:
            return ()
        records_by_index = {
            record.claim_index: record for record in bundle.faithfulness_reviews
        }
        result: list[SemanticClaimReviewIndexRecord] = []
        for claim in artifact.payload.claims:
            review = records_by_index.get(claim.claim_index)
            status = self._claim_status(bundle, review)
            dimensions: tuple[ReviewRepairDimension, ...] = (
                ()
                if review is None or review.derived is None
                else review.derived.repair_dimensions
            )
            result.append(
                SemanticClaimReviewIndexRecord(
                    artifact_id=artifact.artifact_id,
                    version=artifact.version,
                    run_id=artifact.payload.run_id,
                    claim_index=claim.claim_index,
                    claim_digest=claim.claim_digest,
                    proposition=claim.proposition,
                    review_status=status,
                    repair_dimensions=dimensions,
                    created_at=artifact.created_at,
                ),
            )
        return tuple(result)

    def _claim_status(
        self,
        bundle: SemanticReviewBundle,
        review: object,
    ) -> SemanticClaimReviewStatus:
        """读取单条 claim 的派生审查状态。

        :param bundle: M08 Review Bundle。
        :param review: 当前 claim 的 Faithfulness Review record。
        :return: 返回 claim 索引状态。
        """
        from .review_contracts import ClaimFaithfulnessReviewRecord

        if not isinstance(review, ClaimFaithfulnessReviewRecord):
            return SemanticClaimReviewStatus.REPAIR_PENDING
        if review.derived is None:
            return SemanticClaimReviewStatus.REPAIR_PENDING
        if bundle.aggregate_outcome.value == "semantic_review_supported":
            return SemanticClaimReviewStatus.SUPPORTED
        if review.derived.outcome.value == "repair_required":
            return SemanticClaimReviewStatus.REPAIR_REQUIRED
        if review.derived.outcome.value == "clarification_required":
            return SemanticClaimReviewStatus.CLARIFICATION_REQUIRED
        if review.derived.outcome.value == "repair_then_clarification_required":
            return SemanticClaimReviewStatus.REPAIR_REQUIRED
        if review.derived.outcome.value == "human_review_required":
            return SemanticClaimReviewStatus.HUMAN_REVIEW_REQUIRED
        if review.derived.outcome.value == "disagreement":
            return SemanticClaimReviewStatus.DISAGREEMENT
        return SemanticClaimReviewStatus.REPAIR_PENDING

    def _gap_index(
        self,
        artifact: SemanticArtifactVersionRecord,
    ) -> tuple[SemanticClarificationGapIndexRecord, ...]:
        """从 artifact review payload 派生 clarification gap 索引。

        :param artifact: 包含 M08 review bundle 的 artifact version。
        :return: 返回 gap 查询索引集合。
        """
        bundle = artifact.payload.review_bundle
        if bundle is None:
            return ()
        claims = {claim.claim_index: claim for claim in artifact.payload.claims}
        return tuple(
            SemanticClarificationGapIndexRecord(
                artifact_id=artifact.artifact_id,
                version=artifact.version,
                run_id=artifact.payload.run_id,
                gap_id=gap.gap_id,
                claim_index=gap.claim_index,
                claim_digest=claims[gap.claim_index].claim_digest,
                ambiguous_dimension=gap.ambiguous_dimension,
                required_binding_type=gap.required_binding_type,
                status=SemanticClarificationGapStatus.ACTIVE,
                created_at=artifact.created_at,
            )
            for gap in bundle.clarification_gaps
        )

    def _repair_pending_index(
        self,
        artifact: SemanticArtifactVersionRecord,
    ) -> tuple[SemanticClaimReviewIndexRecord, ...]:
        """为修复后 artifact 派生待复审 claim 索引。

        :param artifact: M10 patch 产生的新 artifact version。
        :return: 返回 repair_pending claim 索引集合。
        """
        return tuple(
            SemanticClaimReviewIndexRecord(
                artifact_id=artifact.artifact_id,
                version=artifact.version,
                run_id=artifact.payload.run_id,
                claim_index=claim.claim_index,
                claim_digest=claim.claim_digest,
                proposition=claim.proposition,
                review_status=SemanticClaimReviewStatus.REPAIR_PENDING,
                repair_dimensions=(),
                created_at=artifact.created_at,
            )
            for claim in artifact.payload.claims
        )


def build_postgres_semantic_artifact_store(
    session_factory: sessionmaker[Session],
) -> PostgresSemanticArtifactStore:
    """构造生产 M11 回合级 Artifact Store。

    :param session_factory: SQLAlchemy session factory。
    :return: 返回绑定 PostgreSQL 权威仓储的 M11 Store。
    """
    return PostgresSemanticArtifactStore(
        PostgresSemanticArtifactRepository(session_factory),
    )


__all__ = [
    "PostgresSemanticArtifactStore",
    "build_postgres_semantic_artifact_store",
]

"""
=============================================================================
文件：tests/test_semantic_collaboration_artifact_store.py
作用：验证受限语义协作 DAG M11 回合级 Artifact Store 与版本契约。
范围：覆盖 review bundle 内嵌提交、幂等重放、gate event、claim/gap 派生索引、
      M10 base snapshot、typed patch 新版本、stale marker、re-review 版本、
      系统性人工审查与 repair-of-repair 阻断。
说明：本测试使用进程内仓储替身，不访问 PostgreSQL、LiteLLM、Temporal、OPA
      或任何 input_preprocessing 历史 experiment runner。
=============================================================================
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from tests.test_semantic_collaboration_repair_planner import _bundle, _planner
from tests.test_semantic_collaboration_repair_skill import (
    SequentialRepairTransport,
    _repair_model_policy,
)
from tests.test_semantic_collaboration_review import (
    StubSnapshotReader,
    _empty_coverage_payload,
    _empty_faithfulness_payload,
    _faithfulness_payload,
    _snapshot,
)
from vet_agent.semantic_collaboration import (
    PostgresSemanticArtifactStore,
    SemanticArtifactCommitBundle,
    SemanticArtifactCommitRequestKind,
    SemanticArtifactCommitRequestRecord,
    SemanticArtifactGate,
    SemanticArtifactGateEventRecord,
    SemanticArtifactRepository,
    SemanticArtifactStaleMarkerRecord,
    SemanticArtifactVersionRecord,
    SemanticHumanReviewStatus,
    SemanticSystemReviewRecord,
    StructuredLLMGateway,
    StructuredRepairSkillRunner,
    TurnSnapshotProjector,
    build_production_prompt_renderer_registry,
    build_production_skill_catalog,
    compute_review_bundle_digest,
)


@dataclass
class FakeArtifactRepository(SemanticArtifactRepository):
    """提供进程内 M11 权威仓储测试替身。

    :return: 无返回值；该替身只验证服务契约，不模拟数据库并发。
    """

    artifacts: dict[str, SemanticArtifactVersionRecord] = field(default_factory=dict)
    artifacts_by_review: dict[tuple[str, str], SemanticArtifactVersionRecord] = field(
        default_factory=dict
    )
    latest: dict[tuple[str, str, str], SemanticArtifactVersionRecord] = field(
        default_factory=dict
    )
    requests: dict[str, SemanticArtifactCommitRequestRecord] = field(
        default_factory=dict
    )
    gates: dict[str, SemanticArtifactGateEventRecord] = field(default_factory=dict)
    stale_markers: list[SemanticArtifactStaleMarkerRecord] = field(default_factory=list)
    commits: list[SemanticArtifactCommitBundle] = field(default_factory=list)

    def get_artifact(
        self,
        artifact_reference: str,
    ) -> SemanticArtifactVersionRecord | None:
        """读取进程内权威 artifact。

        :param artifact_reference: M11 artifact 引用。
        :return: 找到时返回 version record。
        """
        return self.artifacts.get(artifact_reference)

    def get_artifact_by_review(
        self,
        source_proposal_digest: str,
        review_bundle_digest: str,
    ) -> SemanticArtifactVersionRecord | None:
        """按 review digest 读取进程内 artifact。

        :param source_proposal_digest: Claim Inventory proposal digest。
        :param review_bundle_digest: M08 Review Bundle digest。
        :return: 找到时返回 version record。
        """
        return self.artifacts_by_review.get(
            (source_proposal_digest, review_bundle_digest),
        )

    def get_latest_artifact(
        self,
        run_id: str,
        source_task_id: str,
        turn_snapshot_digest: str,
    ) -> SemanticArtifactVersionRecord | None:
        """读取进程内最新 artifact。

        :param run_id: DAG workflow 标识。
        :param source_task_id: Claim Inventory 来源任务标识。
        :param turn_snapshot_digest: TurnSnapshot digest。
        :return: 找到时返回最新 version record。
        """
        return self.latest.get((run_id, source_task_id, turn_snapshot_digest))

    def get_commit_request(
        self,
        request_key: str,
    ) -> SemanticArtifactCommitRequestRecord | None:
        """读取进程内幂等提交请求。

        :param request_key: 稳定请求键。
        :return: 找到时返回既有请求。
        """
        return self.requests.get(request_key)

    def get_current_gate(
        self,
        artifact_reference: str,
    ) -> SemanticArtifactGateEventRecord | None:
        """读取进程内当前 gate event。

        :param artifact_reference: M11 artifact 引用。
        :return: 找到时返回 gate event。
        """
        return self.gates.get(artifact_reference)

    def commit(
        self,
        bundle: SemanticArtifactCommitBundle,
    ) -> SemanticArtifactCommitRequestRecord:
        """保存进程内完整 M11 提交负载。

        :param bundle: 完整 append-only 提交负载。
        :return: 返回新建幂等提交结果。
        """
        artifact = bundle.artifact
        self.commits.append(bundle)
        self.artifacts[artifact.artifact_reference] = artifact
        if artifact.payload.review_bundle is not None:
            self.artifacts_by_review[
                (
                    artifact.payload.source_proposal_digest,
                    compute_review_bundle_digest(artifact.payload.review_bundle),
                )
            ] = artifact
        self.latest[
            (
                artifact.payload.run_id,
                artifact.payload.source_task_id,
                artifact.payload.turn_snapshot_digest,
            )
        ] = artifact
        for event in bundle.gate_events:
            self.gates[artifact.artifact_reference] = event
        self.stale_markers.extend(bundle.stale_markers)
        result = SemanticArtifactCommitRequestRecord(
            request_key=bundle.request_key,
            request_kind=bundle.request_kind,
            artifact_id=artifact.artifact_id,
            version=artifact.version,
            payload_digest=artifact.payload_digest,
            result_reference=artifact.artifact_reference,
            created_at=artifact.created_at,
        )
        self.requests[bundle.request_key] = result
        return result

    def append_gate_event(
        self,
        event: SemanticArtifactGateEventRecord,
        *,
        request_key: str,
        payload_digest: str,
    ) -> SemanticArtifactCommitRequestRecord:
        """保存进程内 gate event 与幂等请求。

        :param event: 待追加 gate event。
        :param request_key: gate event 幂等键。
        :param payload_digest: 决策 digest。
        :return: 返回新建幂等提交结果。
        """
        artifact = self.artifacts[
            f"semantic-artifact://{event.artifact_id}/{event.version}"
        ]
        self.gates[artifact.artifact_reference] = event
        result = SemanticArtifactCommitRequestRecord(
            request_key=request_key,
            request_kind=SemanticArtifactCommitRequestKind.GATE_EVENT,
            artifact_id=event.artifact_id,
            version=event.version,
            payload_digest=payload_digest,
            result_reference=artifact.artifact_reference,
            created_at=event.created_at,
        )
        self.requests[request_key] = result
        return result


def _repair_runner(
    store: PostgresSemanticArtifactStore,
    snapshot: object,
) -> StructuredRepairSkillRunner:
    """构造直接使用 M11 Store 的 M10 Runner。

    :param store: M11 Artifact Store 服务。
    :param snapshot: 当前回合 TurnSnapshot。
    :return: 返回可执行 M10 Runner。
    """

    catalog = build_production_skill_catalog()
    return StructuredRepairSkillRunner(
        registry=catalog.registry(),
        renderer_registry=build_production_prompt_renderer_registry(),
        snapshot_reader=StubSnapshotReader(snapshot),
        target_snapshot_resolver=store,
        projector=TurnSnapshotProjector(),
        gateway=StructuredLLMGateway(
            registry=catalog.registry(),
            transport=SequentialRepairTransport(
                payloads=({"proposition": "英短精神状态正常"},),
            ),
        ),
        model_policy=_repair_model_policy(),
    )


def test_review_bundle_creates_embedded_turn_artifact_and_idempotent_replay() -> None:
    """验证 M08 review 以回合级内嵌 section 提交并可幂等重放。

    :return: 无返回值。
    """
    repository = FakeArtifactRepository()
    store = PostgresSemanticArtifactStore(repository)
    bundle = _bundle(
        (
            {"claims": ["英短没有呕吐"]},
            _empty_coverage_payload(),
            _empty_faithfulness_payload(),
        )
    )

    reference = asyncio.run(store.commit_review_bundle(bundle))
    assert reference.startswith(
        "semantic-artifact://",
    )
    replay_reference = asyncio.run(store.commit_review_bundle(bundle))
    artifact = repository.get_artifact(reference)

    assert reference == replay_reference
    assert artifact is not None
    assert artifact.version == 1
    assert artifact.payload.review_bundle == bundle
    assert artifact.payload.repair_depth == 0
    assert repository.gates[reference].to_gate is (
        SemanticArtifactGate.EVIDENCE_BINDING_PENDING
    )
    assert len(repository.commits) == 1
    assert repository.commits[0].claim_review_index[0].review_status.value == (
        "supported"
    )


def test_clarification_gaps_are_embedded_and_indexed() -> None:
    """验证 clarification gap 随 review bundle 内嵌并可派生查询。

    :return: 无返回值。
    """
    repository = FakeArtifactRepository()
    store = PostgresSemanticArtifactStore(repository)
    bundle = _bundle(
        (
            {"claims": ["它的精神状态不明"]},
            _empty_coverage_payload(),
            _faithfulness_payload(指代对象不明=True),
        )
    )

    assert asyncio.run(store.commit_review_bundle(bundle)).startswith(
        "semantic-artifact://",
    )

    gap_index = repository.commits[0].clarification_gap_index
    assert len(gap_index) == 1
    assert gap_index[0].status.value == "active"
    assert gap_index[0].ambiguous_dimension.value == "指代对象不明"


def test_patch_commit_re_review_and_repair_of_repair_are_versioned() -> None:
    """验证 M10 patch、M08 re-review 与 repair depth 门禁形成版本链。

    :return: 无返回值。
    """
    repository = FakeArtifactRepository()
    store = PostgresSemanticArtifactStore(repository)
    bundle = _bundle(
        (
            {"claims": ["英短精神状态被否认"]},
            _empty_coverage_payload(),
            _faithfulness_payload(正常状态误写为否认=True),
        )
    )
    assert asyncio.run(store.commit_review_bundle(bundle)).startswith(
        "semantic-artifact://",
    )
    turn_snapshot = _snapshot()
    plan = _planner().plan(bundle)
    repair_result = asyncio.run(
        _repair_runner(store, turn_snapshot).repair(
            plan,
            bundle,
        ),
    )
    assert repair_result.patch_set is not None
    assert repair_result.preview is not None

    repaired_reference = asyncio.run(
        store.commit(
            repair_result.patch_set,
            repair_result.preview,
        ),
    )
    latest = repository.get_artifact(repaired_reference)

    assert latest is not None
    assert latest.version == 2
    assert latest.payload.repair_depth == 1
    assert latest.payload.review_bundle is None
    assert latest.payload.claims[0].proposition == "英短精神状态正常"
    assert repository.gates[repaired_reference].to_gate is (
        SemanticArtifactGate.PATCH_APPLIED
    )
    assert repository.stale_markers[0].stale_reason.value == ("superseded_by_repair")

    reviewed_bundle = _bundle(
        (
            {"claims": ["英短精神状态正常"]},
            _empty_coverage_payload(),
            _empty_faithfulness_payload(),
        )
    ).model_copy(
        update={
            "source_proposal_digest": bundle.source_proposal_digest,
        },
    )
    reviewed_reference = asyncio.run(store.commit_review_bundle(reviewed_bundle))
    reviewed = repository.get_artifact(reviewed_reference)

    assert reviewed is not None
    assert reviewed.version == 3
    assert reviewed.payload.repair_depth == 1
    assert reviewed.payload.repair_lineage is not None
    assert repository.gates[reviewed_reference].to_gate is (
        SemanticArtifactGate.EVIDENCE_BINDING_PENDING
    )

    with pytest.raises(Exception, match="already repaired"):
        asyncio.run(
            store.load(
                reviewed_bundle.source_proposal_digest,
                compute_review_bundle_digest(reviewed_bundle),
            ),
        )


def test_system_human_review_does_not_promote_artifact_to_verified() -> None:
    """验证系统性人工审查保留当前 gate 而不批量标记 verified。

    :return: 无返回值。
    """
    repository = FakeArtifactRepository()
    store = PostgresSemanticArtifactStore(repository)
    bundle = _bundle(
        (
            {"claims": ["英短没有呕吐"]},
            _empty_coverage_payload(),
            _empty_faithfulness_payload(),
        )
    )
    reference = asyncio.run(store.commit_review_bundle(bundle))
    review = SemanticSystemReviewRecord(
        review_id="system-review-1",
        reviewer_role="semantic_engineering",
        status=SemanticHumanReviewStatus.NO_SYSTEM_DEFECT,
        summary_digest="0" * 64,
        reviewed_at="2026-09-07T00:00:00+00:00",
    )

    result = store.record_system_review(reference, review)
    gate = store.get_current_gate(reference)

    assert result == reference
    assert gate is not None
    assert gate.to_gate is SemanticArtifactGate.EVIDENCE_BINDING_PENDING
    assert gate.actor_type.value == "human_reviewer"

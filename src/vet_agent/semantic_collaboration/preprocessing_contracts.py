"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/preprocessing_contracts.py
作用：定义语义预处理主链的稳定请求与结果信封契约。
范围：覆盖 TurnSnapshot 构建请求入口、verified / partial / failed 显式终态、
      M11 / M12 / M13 来源身份和失败码。
说明：本文件不接入 VetOrchestrator、不实现 M15 路由、不做 M14 观测指标、
      不把失败转换为空 facts，也不携带原始用户文本作为响应负载。
=============================================================================
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .claim_graph_contracts import SemanticGraphTerminalState
from .consultation_projection import ConsultationProjection
from .snapshot_contracts import TurnSnapshotBuildRequest


class SemanticPreprocessingExecutionState(StrEnum):
    """表示一次语义预处理主链的业务执行终态。"""

    COMPLETED_VERIFIED = "completed_verified"
    COMPLETED_PARTIAL_WITH_GAPS = "completed_partial_with_gaps"
    BLOCKED = "blocked"
    FAILED = "failed"

    def is_consumable(self) -> bool:
        """判断结果是否携带可交给问诊领域的 M13 投影。

        :return: verified 或 partial with gaps 返回 True。
        """
        return self in {
            SemanticPreprocessingExecutionState.COMPLETED_VERIFIED,
            SemanticPreprocessingExecutionState.COMPLETED_PARTIAL_WITH_GAPS,
        }


class SemanticPreprocessingFailureCode(StrEnum):
    """表示语义预处理主链的稳定失败编码。"""

    TURN_SNAPSHOT_FAILED = "turn_snapshot_failed"
    PLAN_FAILED = "plan_failed"
    GENERATION_FAILED = "generation_failed"
    GENERATION_BLOCKED = "generation_blocked"
    REVIEW_FAILED = "review_failed"
    REPAIR_FAILED = "repair_failed"
    REPAIR_EXHAUSTED = "repair_exhausted"
    ARTIFACT_COMMIT_FAILED = "artifact_commit_failed"
    GRAPH_BLOCKED = "graph_blocked"
    GRAPH_DISAGREEMENT = "graph_disagreement"
    PROJECTION_REJECTED = "projection_rejected"
    PIPELINE_FAILED = "pipeline_failed"


class SemanticConsultationPreprocessingRequest(BaseModel):
    """表示一次问诊语义预处理主链请求。

    :return: 无返回值；请求只携带当前回合输入，不携带问诊状态或临床安全结果。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    snapshot_request: TurnSnapshotBuildRequest = Field(
        description="M02 当前回合 TurnSnapshot 构建请求。",
    )


class SemanticConsultationPreprocessingResult(BaseModel):
    """表示语义预处理主链输出给问诊领域的系统信封。

    :return: 无返回值；失败结果不携带 ConsultationProjection，也不得被解释为空 facts。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    execution_state: SemanticPreprocessingExecutionState
    turn_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=240,
        description="当前回合标识；快照构建失败前可为空。",
    )
    turn_index: int | None = Field(
        default=None,
        ge=0,
        description="当前回合零基序号；快照构建失败前可为空。",
    )
    turn_snapshot_digest: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
        description="当前回合 TurnSnapshot digest。",
    )
    run_id: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
        description="语义 DAG workflow 稳定标识。",
    )
    plan_id: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
        description="权威 Plan IR digest。",
    )
    artifact_reference: str | None = Field(
        default=None,
        max_length=512,
        description="当前 M11 artifact 引用。",
    )
    artifact_version: int | None = Field(
        default=None,
        ge=1,
        description="当前 M11 artifact 版本。",
    )
    graph_id: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
        description="当前 M12 Claim Graph 标识。",
    )
    graph_digest: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
        description="当前 M12 Claim Graph digest。",
    )
    graph_terminal_state: SemanticGraphTerminalState | None = Field(
        default=None,
        description="当前 M12 graph 终态。",
    )
    projection_id: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
        description="M13 问诊投影稳定标识。",
    )
    consultation_projection: ConsultationProjection | None = Field(
        default=None,
        description="M13 问诊领域可消费投影；失败结果必须为空。",
    )
    failure_code: SemanticPreprocessingFailureCode | None = None
    failure_message: str | None = Field(
        default=None,
        min_length=1,
        max_length=1000,
        description="面向工程排障的失败说明，不包含用户原文或完整 prompt。",
    )

    @model_validator(mode="after")
    def validate_result_payload(self) -> Self:
        """校验主链结果状态与负载闭合。

        :return: 返回通过显式状态机校验的结果信封。
        :raises ValueError: 成功缺投影或失败携带投影时抛出。
        """
        if self.execution_state.is_consumable():
            required = (
                self.turn_id,
                self.turn_index,
                self.turn_snapshot_digest,
                self.run_id,
                self.plan_id,
                self.artifact_reference,
                self.artifact_version,
                self.graph_id,
                self.graph_digest,
                self.graph_terminal_state,
                self.projection_id,
                self.consultation_projection,
            )
            if any(value is None for value in required):
                raise ValueError("consumable preprocessing result payload is invalid")
            if self.failure_code is not None or self.failure_message is not None:
                raise ValueError("consumable preprocessing result cannot carry failure")
            expected_state = (
                SemanticPreprocessingExecutionState.COMPLETED_PARTIAL_WITH_GAPS
                if self.graph_terminal_state
                is SemanticGraphTerminalState.GRAPH_PARTIAL_WITH_GAPS
                else SemanticPreprocessingExecutionState.COMPLETED_VERIFIED
            )
            if self.execution_state is not expected_state:
                raise ValueError("preprocessing result graph state mismatch")
            return self
        if self.consultation_projection is not None or self.projection_id is not None:
            raise ValueError("non-consumable preprocessing result cannot carry projection")
        if self.failure_code is None or not self.failure_message:
            raise ValueError("non-consumable preprocessing result requires failure")
        return self


__all__ = [
    "SemanticConsultationPreprocessingRequest",
    "SemanticConsultationPreprocessingResult",
    "SemanticPreprocessingExecutionState",
    "SemanticPreprocessingFailureCode",
]

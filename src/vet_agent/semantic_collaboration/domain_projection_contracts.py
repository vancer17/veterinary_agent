"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/domain_projection_contracts.py
作用：定义受限语义协作 DAG M13 领域投影的稳定输入输出契约。
范围：覆盖问诊、临床安全与长期记忆 adapter 的消费状态策略、投影来源身份、
      统一结果信封、稳定失败码与确定性投影 ID。
说明：M13 只消费 M12 graph build result，不读取 M11 内部 payload、M08 矩阵、
      M09 计划、M10 patch 或原始用户文本；本文件不调用 LLM、不做关键词语义
      判断、不做医学判断，也不写任何下游领域状态。
=============================================================================
"""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .claim_graph_contracts import (
    SemanticClaimGraph,
    SemanticGraphBuildResult,
    SemanticGraphTerminalState,
)


class SemanticProjectionAdapterKind(StrEnum):
    """表示 M13 领域投影 adapter 的目标领域类型。

    :return: 无返回值；该枚举只标识消费边界，不表示领域业务决策。
    """

    CONSULTATION = "consultation"
    CLINICAL_SAFETY = "clinical_safety"
    LONG_TERM_MEMORY = "long_term_memory"


class SemanticProjectionImplementationState(StrEnum):
    """表示 M13 adapter 契约当前的生产实现状态。

    :return: 无返回值；TODO 状态必须显式失败，不得返回空投影。
    """

    PRODUCTION = "production"
    TODO = "todo"


class SemanticProjectionExecutionState(StrEnum):
    """表示一次 M13 领域投影调用的显式执行状态。

    :return: 无返回值；rejected 状态必须携带稳定失败码。
    """

    PROJECTED = "projected"
    REJECTED = "rejected"


class SemanticProjectionFailureCode(StrEnum):
    """表示 M13 领域投影失败的稳定错误编码。

    :return: 无返回值；失败不得被转换为空 facts、空候选或旧链路回退。
    """

    GRAPH_MISSING = "graph_missing"
    TERMINAL_STATE_NOT_ALLOWED = "terminal_state_not_allowed"
    GRAPH_TERMINAL_STATE_MISMATCH = "graph_terminal_state_mismatch"
    GAP_SHAPE_MISMATCH = "gap_shape_mismatch"
    PROJECTION_CONTRACT_MISMATCH = "projection_contract_mismatch"
    ADAPTER_NOT_IMPLEMENTED = "adapter_not_implemented"


class SemanticProjectionContract(BaseModel):
    """表示单个 M13 领域 adapter 的显式消费契约。

    :return: 无返回值；该契约只描述结构状态策略，不描述领域业务规则。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    adapter_kind: SemanticProjectionAdapterKind
    contract_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    implementation_state: SemanticProjectionImplementationState
    accepted_terminal_states: tuple[SemanticGraphTerminalState, ...] = Field(
        min_length=1,
    )

    @model_validator(mode="after")
    def validate_consumable_states(self) -> SemanticProjectionContract:
        """校验 adapter 契约不得声明消费不可用 graph 终态。

        :return: 返回只消费结构可终态的投影契约。
        :raises ValueError: 声明 blocked 或 disagreement 时抛出。
        """
        forbidden_states = {
            SemanticGraphTerminalState.GRAPH_BLOCKED,
            SemanticGraphTerminalState.GRAPH_DISAGREEMENT,
        }
        if forbidden_states.intersection(self.accepted_terminal_states):
            raise ValueError(
                "semantic projection contract cannot consume blocked graph state",
            )
        return self


class SemanticProjectionSource(BaseModel):
    """表示 M13 领域投影结果绑定的 M12 graph 来源身份。

    :return: 无返回值；来源身份由系统复制，不允许模型或下游伪造。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    graph_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    graph_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    graph_contract_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    run_id: str = Field(min_length=1, max_length=64)
    turn_snapshot_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_reference: str = Field(min_length=1, max_length=512)
    artifact_version: int = Field(ge=1)
    artifact_payload_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    current_gate_sequence: int = Field(ge=1)
    terminal_state: SemanticGraphTerminalState

    @classmethod
    def from_graph(
        cls,
        graph: SemanticClaimGraph,
    ) -> SemanticProjectionSource:
        """将 M12 graph identity 投影为 M13 来源身份。

        :param graph: 已通过 M12 结构门禁的 Claim Graph。
        :return: 返回不可变领域投影来源身份。
        """
        return cls(
            graph_id=graph.identity.graph_id,
            graph_digest=graph.identity.graph_digest,
            graph_contract_version=graph.identity.graph_contract_version,
            run_id=graph.identity.run_id,
            turn_snapshot_digest=graph.identity.turn_snapshot_digest,
            artifact_reference=graph.identity.artifact_reference,
            artifact_version=graph.identity.artifact_version,
            artifact_payload_digest=graph.identity.artifact_payload_digest,
            current_gate_sequence=graph.identity.current_gate_sequence,
            terminal_state=graph.terminal_state,
        )


class SemanticProjectionResult[ProjectionPayloadT: BaseModel](BaseModel):
    """表示 M13 领域投影的统一系统信封与执行终态。

    :return: 无返回值；rejected 信封不携带领域负载，避免伪装成空投影。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    adapter_kind: SemanticProjectionAdapterKind
    adapter_contract_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    adapter_implementation_state: SemanticProjectionImplementationState
    execution_state: SemanticProjectionExecutionState
    source: SemanticProjectionSource | None = None
    projection: ProjectionPayloadT | None = None
    projection_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    failure_code: SemanticProjectionFailureCode | None = None
    failure_message: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_result_payload(
        self,
    ) -> SemanticProjectionResult[ProjectionPayloadT]:
        """校验投影成功与失败信封的负载闭合关系。

        :return: 返回通过显式状态机校验的投影结果。
        :raises ValueError: 成功缺少负载或失败携带负载时抛出。
        """
        if self.execution_state is SemanticProjectionExecutionState.PROJECTED:
            if (
                self.source is None
                or self.projection is None
                or self.projection_id is None
                or self.failure_code is not None
                or self.failure_message is not None
            ):
                raise ValueError("projected semantic projection payload is invalid")
            return self
        if (
            self.projection is not None
            or self.projection_id is not None
            or self.failure_code is None
            or self.failure_message is None
        ):
            raise ValueError("rejected semantic projection payload is invalid")
        return self


class SemanticDomainProjectionAdapter[ProjectionPayloadT: BaseModel](Protocol):
    """表示 M13 领域投影 adapter 的稳定调用端口。

    :return: 无返回值；实现必须显式返回 projected 或 rejected，不得抛出后降级。
    """

    @property
    def contract(self) -> SemanticProjectionContract:
        """读取 adapter 的结构消费契约。

        :return: 返回不可变 M13 投影契约。
        """

    def project(
        self,
        graph_result: SemanticGraphBuildResult,
    ) -> SemanticProjectionResult[ProjectionPayloadT]:
        """将 M12 graph result 投影为领域消费契约。

        :param graph_result: M12 graph 构建与结构门禁结果。
        :return: 返回显式 projected 或 rejected 投影结果。
        """


def compute_domain_projection_id(
    *,
    contract: SemanticProjectionContract,
    graph: SemanticClaimGraph,
    projection: BaseModel,
) -> str:
    """计算领域投影负载的确定性身份摘要。

    :param contract: 当前 adapter 的结构消费契约。
    :param graph: 已通过 M12 结构门禁的来源 graph。
    :param projection: 具体领域投影负载。
    :return: 返回 SHA-256 projection id。
    """
    material: dict[str, object] = {
        "adapter_contract_version": contract.contract_version,
        "adapter_kind": contract.adapter_kind,
        "graph_digest": graph.identity.graph_digest,
        "graph_id": graph.identity.graph_id,
        "projection": projection.model_dump(mode="json"),
    }
    canonical = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def build_projected_projection_result[
    ProjectionPayloadT: BaseModel,
](
    *,
    contract: SemanticProjectionContract,
    graph: SemanticClaimGraph,
    projection: ProjectionPayloadT,
) -> SemanticProjectionResult[ProjectionPayloadT]:
    """构造成功领域投影的系统信封。

    :param contract: 当前 adapter 的结构消费契约。
    :param graph: 来源 Claim Graph。
    :param projection: 具体领域投影负载。
    :return: 返回携带来源身份与 projection id 的成功结果。
    """
    return SemanticProjectionResult[ProjectionPayloadT](
        adapter_kind=contract.adapter_kind,
        adapter_contract_version=contract.contract_version,
        adapter_implementation_state=contract.implementation_state,
        execution_state=SemanticProjectionExecutionState.PROJECTED,
        source=SemanticProjectionSource.from_graph(graph),
        projection=projection,
        projection_id=compute_domain_projection_id(
            contract=contract,
            graph=graph,
            projection=projection,
        ),
    )


def build_rejected_projection_result[
    ProjectionPayloadT: BaseModel,
](
    *,
    contract: SemanticProjectionContract,
    graph: SemanticClaimGraph | None,
    failure_code: SemanticProjectionFailureCode,
    failure_message: str,
) -> SemanticProjectionResult[ProjectionPayloadT]:
    """构造显式失败的领域投影系统信封。

    :param contract: 当前 adapter 的结构消费契约。
    :param graph: 可用时的来源 Claim Graph；blocked 结果可为空。
    :param failure_code: 稳定投影失败码。
    :param failure_message: 面向工程排障的失败说明。
    :return: 返回不携带领域负载的 rejected 结果。
    """
    return SemanticProjectionResult[ProjectionPayloadT](
        adapter_kind=contract.adapter_kind,
        adapter_contract_version=contract.contract_version,
        adapter_implementation_state=contract.implementation_state,
        execution_state=SemanticProjectionExecutionState.REJECTED,
        source=None if graph is None else SemanticProjectionSource.from_graph(graph),
        failure_code=failure_code,
        failure_message=failure_message,
    )


__all__ = [
    "SemanticDomainProjectionAdapter",
    "SemanticProjectionAdapterKind",
    "SemanticProjectionContract",
    "SemanticProjectionExecutionState",
    "SemanticProjectionFailureCode",
    "SemanticProjectionImplementationState",
    "SemanticProjectionResult",
    "SemanticProjectionSource",
    "build_projected_projection_result",
    "build_rejected_projection_result",
    "compute_domain_projection_id",
]

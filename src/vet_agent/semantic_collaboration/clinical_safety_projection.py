"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/clinical_safety_projection.py
作用：预留受限语义协作 DAG M13 的临床安全领域投影 Adapter 边界。
范围：仅声明未来可消费的 graph 结构终态，并在当前依赖契约未就绪时显式失败。
说明：TODO 空壳不猜测临床安全输入 schema、不产生 urgent / blocked 信号、
      不调用临床安全召回 / required_context / OPA、不做诊断或安全动作；
      这些职责始终属于既有临床安全领域链路。
=============================================================================
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel

from .claim_graph_contracts import SemanticGraphBuildResult, SemanticGraphTerminalState
from .domain_projection_contracts import (
    SemanticProjectionAdapterKind,
    SemanticProjectionContract,
    SemanticProjectionFailureCode,
    SemanticProjectionImplementationState,
    SemanticProjectionResult,
    build_rejected_projection_result,
)


class TODOClinicalSafetyProjectionAdapter:
    """表示等待临床安全输入契约明确的 M13 TODO 空壳 Adapter。

    :return: 无返回值；空壳必须显式失败，不得输出空临床安全事实集合。
    """

    contract: Final[SemanticProjectionContract] = SemanticProjectionContract(
        adapter_kind=SemanticProjectionAdapterKind.CLINICAL_SAFETY,
        contract_version="0.1.0",
        implementation_state=SemanticProjectionImplementationState.TODO,
        accepted_terminal_states=(SemanticGraphTerminalState.GRAPH_VERIFIED,),
    )

    def project(
        self,
        graph_result: SemanticGraphBuildResult,
    ) -> SemanticProjectionResult[BaseModel]:
        """显式拒绝当前阶段的临床安全投影调用。

        :param graph_result: M12 graph 构建与结构门禁结果。
        :return: 返回 adapter_not_implemented 的 rejected 投影结果。
        """
        return build_rejected_projection_result(
            contract=self.contract,
            graph=graph_result.graph,
            failure_code=SemanticProjectionFailureCode.ADAPTER_NOT_IMPLEMENTED,
            failure_message=(
                "clinical safety projection awaits its explicit domain input contract"
            ),
        )


__all__ = ["TODOClinicalSafetyProjectionAdapter"]

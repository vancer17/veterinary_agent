"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/long_term_memory_projection.py
作用：预留受限语义协作 DAG M13 的长期记忆候选投影 Adapter 边界。
范围：仅声明未来只消费 verified graph 的结构策略，并在候选策略未就绪时失败。
说明：TODO 空壳不猜测记忆候选 schema、不写长期权威事实、不写 Mem0、
      不消费 partial / disagreement / blocked graph；候选裁决与写入始终由
      独立长期记忆领域负责。
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


class TODOLongTermMemoryProjectionAdapter:
    """表示等待记忆候选策略明确的 M13 TODO 空壳 Adapter。

    :return: 无返回值；空壳必须显式失败，不得输出空记忆候选集合。
    """

    contract: Final[SemanticProjectionContract] = SemanticProjectionContract(
        adapter_kind=SemanticProjectionAdapterKind.LONG_TERM_MEMORY,
        contract_version="0.1.0",
        implementation_state=SemanticProjectionImplementationState.TODO,
        accepted_terminal_states=(SemanticGraphTerminalState.GRAPH_VERIFIED,),
    )

    def project(
        self,
        graph_result: SemanticGraphBuildResult,
    ) -> SemanticProjectionResult[BaseModel]:
        """显式拒绝当前阶段的长期记忆候选投影调用。

        :param graph_result: M12 graph 构建与结构门禁结果。
        :return: 返回 adapter_not_implemented 的 rejected 投影结果。
        """
        return build_rejected_projection_result(
            contract=self.contract,
            graph=graph_result.graph,
            failure_code=SemanticProjectionFailureCode.ADAPTER_NOT_IMPLEMENTED,
            failure_message=(
                "long-term memory projection awaits its explicit candidate policy"
            ),
        )


__all__ = ["TODOLongTermMemoryProjectionAdapter"]

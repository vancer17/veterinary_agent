##################################################################################################
# 文件: src/veterinary_agent/graph_runtime/errors.py
# 作用: 定义 GraphRuntime 组件内部领域异常与错误构造辅助函数。
# 边界: 不负责将错误映射为 HTTP 响应；应用层错误映射由 AgentApplicationService 与 ApiIngress 承担。
##################################################################################################

from veterinary_agent.graph_runtime.dto import JsonMap
from veterinary_agent.graph_runtime.enums import GraphRuntimeErrorCode


class GraphRuntimeError(RuntimeError):
    """GraphRuntime 领域异常。"""

    def __init__(
        self,
        *,
        code: GraphRuntimeErrorCode,
        message: str,
        request_id: str | None = None,
        trace_id: str | None = None,
        run_id: str | None = None,
        graph_id: str | None = None,
        graph_version: str | None = None,
        node_id: str | None = None,
        retryable: bool = False,
        details: JsonMap | None = None,
    ) -> None:
        """初始化 GraphRuntime 领域异常。

        :param code: GraphRuntime 稳定错误码。
        :param message: 面向工程排障的错误说明。
        :param request_id: 可选入口请求 ID。
        :param trace_id: 可选全链路追踪 ID。
        :param run_id: 可选图运行 ID。
        :param graph_id: 可选图定义 ID。
        :param graph_version: 可选图定义版本。
        :param node_id: 可选发生错误的节点 ID。
        :param retryable: 当前错误是否允许调用方稍后重试。
        :param details: 可选安全错误详情。
        :return: None。
        """

        self.code = code
        self.request_id = request_id
        self.trace_id = trace_id
        self.run_id = run_id
        self.graph_id = graph_id
        self.graph_version = graph_version
        self.node_id = node_id
        self.retryable = retryable
        self.details = details or {}
        super().__init__(message)

    def to_safe_fields(self) -> JsonMap:
        """转换为可写入日志或事件的安全字段。

        :return: GraphRuntime 错误摘要字段。
        """

        return {
            "code": self.code.value,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "graph_id": self.graph_id,
            "graph_version": self.graph_version,
            "node_id": self.node_id,
            "retryable": self.retryable,
            "details": self.details,
        }


class GraphRuntimeCancelledError(GraphRuntimeError):
    """GraphRuntime 运行取消异常。"""

    def __init__(
        self,
        *,
        request_id: str,
        trace_id: str,
        run_id: str,
        graph_id: str,
        graph_version: str,
    ) -> None:
        """初始化 GraphRuntime 运行取消异常。

        :param request_id: 入口请求 ID。
        :param trace_id: 全链路追踪 ID。
        :param run_id: 图运行 ID。
        :param graph_id: 图定义 ID。
        :param graph_version: 图定义版本。
        :return: None。
        """

        super().__init__(
            code=GraphRuntimeErrorCode.GRAPH_RUN_CANCELLED,
            message="GraphRuntime 运行已被取消",
            request_id=request_id,
            trace_id=trace_id,
            run_id=run_id,
            graph_id=graph_id,
            graph_version=graph_version,
            retryable=False,
        )


__all__: tuple[str, ...] = (
    "GraphRuntimeCancelledError",
    "GraphRuntimeError",
)

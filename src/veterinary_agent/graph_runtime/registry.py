##################################################################################################
# 文件: src/veterinary_agent/graph_runtime/registry.py
# 作用: 注册不可变版本化图定义，并缓存由 LangGraphCompiler 生成的 compiled graph。
# 边界: 不动态拼装业务图、不读取配置、不执行 compiled graph。
##################################################################################################

from veterinary_agent.graph_runtime.definition import GraphDefinition
from veterinary_agent.graph_runtime.enums import GraphRuntimeErrorCode
from veterinary_agent.graph_runtime.errors import GraphRuntimeError
from veterinary_agent.graph_runtime.langgraph_backend import (
    CompiledGraph,
    LangGraphCompiler,
)


class GraphRegistry:
    """GraphRuntime 版本化图注册表。"""

    def __init__(self) -> None:
        """初始化空图注册表。

        :return: None。
        """

        self._definitions: dict[tuple[str, str], GraphDefinition] = {}
        self._compiled_graphs: dict[tuple[str, str], CompiledGraph] = {}

    def register(self, definition: GraphDefinition) -> None:
        """注册一个不可变版本化图定义。

        :param definition: 需要注册的项目图定义。
        :return: None。
        :raises GraphRuntimeError: 当同一图版本已经注册时抛出。
        """

        key = (definition.graph_id, definition.graph_version)
        if key in self._definitions:
            raise GraphRuntimeError(
                code=GraphRuntimeErrorCode.GRAPH_VERSION_UNAVAILABLE,
                message="GraphRuntime 图定义版本重复注册",
                graph_id=definition.graph_id,
                graph_version=definition.graph_version,
                retryable=False,
            )
        self._definitions[key] = definition

    def compile_all(self, compiler: LangGraphCompiler) -> None:
        """编译注册表中的全部图版本。

        :param compiler: 已注入唯一 LangGraph checkpointer 的图编译器。
        :return: None。
        """

        compiled_graphs: dict[tuple[str, str], CompiledGraph] = {}
        for key, definition in self._definitions.items():
            compiled_graphs[key] = compiler.compile(definition)
        self._compiled_graphs = compiled_graphs

    def get_definition(
        self,
        *,
        graph_id: str,
        graph_version: str,
    ) -> GraphDefinition:
        """读取指定版本的图定义。

        :param graph_id: 图定义 ID。
        :param graph_version: 图定义版本。
        :return: 命中的版本化图定义。
        :raises GraphRuntimeError: 当图或版本不存在时抛出。
        """

        definition = self._definitions.get((graph_id, graph_version))
        if definition is not None:
            return definition
        raise self._build_not_found_error(
            graph_id=graph_id,
            graph_version=graph_version,
        )

    def get_compiled(
        self,
        *,
        graph_id: str,
        graph_version: str,
    ) -> CompiledGraph:
        """读取指定版本的 compiled graph。

        :param graph_id: 图定义 ID。
        :param graph_version: 图定义版本。
        :return: 已编译的 LangGraph。
        :raises GraphRuntimeError: 当图未注册或尚未完成编译时抛出。
        """

        key = (graph_id, graph_version)
        compiled = self._compiled_graphs.get(key)
        if compiled is not None:
            return compiled
        if key not in self._definitions:
            raise self._build_not_found_error(
                graph_id=graph_id,
                graph_version=graph_version,
            )
        raise GraphRuntimeError(
            code=GraphRuntimeErrorCode.GRAPH_RUNTIME_NOT_READY,
            message="GraphRuntime 图定义尚未完成 LangGraph 编译",
            graph_id=graph_id,
            graph_version=graph_version,
            retryable=True,
        )

    def has_graph(
        self,
        *,
        graph_id: str,
        graph_version: str,
        require_compiled: bool = False,
    ) -> bool:
        """判断注册表是否包含指定图版本。

        :param graph_id: 图定义 ID。
        :param graph_version: 图定义版本。
        :param require_compiled: 是否同时要求该图已完成 LangGraph 编译。
        :return: 图版本满足查询条件时返回 True。
        """

        key = (graph_id, graph_version)
        if require_compiled:
            return key in self._compiled_graphs
        return key in self._definitions

    def is_empty(self) -> bool:
        """判断图定义注册表是否为空。

        :return: 尚未注册任何图定义时返回 True。
        """

        return not self._definitions

    def _build_not_found_error(
        self,
        *,
        graph_id: str,
        graph_version: str,
    ) -> GraphRuntimeError:
        """构建图定义或图版本不存在错误。

        :param graph_id: 调用方请求的图定义 ID。
        :param graph_version: 调用方请求的图定义版本。
        :return: 带稳定错误码的 GraphRuntime 领域异常。
        """

        has_graph = any(
            registered_graph_id == graph_id
            for registered_graph_id, _registered_version in self._definitions
        )
        return GraphRuntimeError(
            code=(
                GraphRuntimeErrorCode.GRAPH_VERSION_UNAVAILABLE
                if has_graph
                else GraphRuntimeErrorCode.GRAPH_DEFINITION_NOT_FOUND
            ),
            message="GraphRuntime 图定义不存在或版本不可用",
            graph_id=graph_id,
            graph_version=graph_version,
            retryable=False,
        )


__all__: tuple[str, ...] = ("GraphRegistry",)

##################################################################################################
# 文件: src/veterinary_agent/education_agent/factory.py
# 作用: 提供 EducationAgent 默认服务实例创建函数，集中装配可选 AgentRunner、RAG、trace 与观测端口。
# 边界: 只创建应用内服务实例，不读取配置、不启动外部连接、不执行科普生成。
##################################################################################################

from veterinary_agent.agent_runner import AgentRunner
from veterinary_agent.config import RuntimeConfigProvider
from veterinary_agent.education_agent.contract import EducationAgent
from veterinary_agent.education_agent.ports import EducationRagPort
from veterinary_agent.education_agent.service import DefaultEducationAgent
from veterinary_agent.education_agent.trace import EducationTraceSink
from veterinary_agent.observability import ObservabilityProvider


def create_default_education_agent(
    *,
    runtime_config_provider: RuntimeConfigProvider,
    agent_runner: AgentRunner | None = None,
    rag_port: EducationRagPort | None = None,
    trace_sink: EducationTraceSink | None = None,
    observability_provider: ObservabilityProvider | None = None,
) -> EducationAgent:
    """创建默认 EducationAgent 服务实例。

    :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
    :param agent_runner: 可选 AgentRunner 端口。
    :param rag_port: 可选科普 RAG 端口。
    :param trace_sink: 可选科普 trace 写入端口。
    :param observability_provider: 可选 Observability provider。
    :return: 默认 EducationAgent 服务实例。
    """

    return DefaultEducationAgent(
        runtime_config_provider=runtime_config_provider,
        agent_runner=agent_runner,
        rag_port=rag_port,
        trace_sink=trace_sink,
        observability_provider=observability_provider,
    )


__all__: tuple[str, ...] = ("create_default_education_agent",)

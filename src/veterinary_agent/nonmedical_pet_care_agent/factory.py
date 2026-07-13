##################################################################################################
# 文件: src/veterinary_agent/nonmedical_pet_care_agent/factory.py
# 作用: 提供 NonmedicalPetCareAgent 默认服务实例创建函数，集中装配可选 AgentRunner、RAG、trace 与观测端口。
# 边界: 只创建应用内服务实例，不读取配置、不启动外部连接、不执行非医疗建议生成。
##################################################################################################

from veterinary_agent.agent_runner import AgentRunner
from veterinary_agent.config import RuntimeConfigProvider
from veterinary_agent.nonmedical_pet_care_agent.contract import (
    NonmedicalPetCareAgent,
)
from veterinary_agent.nonmedical_pet_care_agent.ports import (
    NonmedicalPetCareRagPort,
)
from veterinary_agent.nonmedical_pet_care_agent.service import (
    DefaultNonmedicalPetCareAgent,
)
from veterinary_agent.nonmedical_pet_care_agent.trace import (
    NonmedicalPetCareTraceSink,
)
from veterinary_agent.observability import ObservabilityProvider


def create_default_nonmedical_pet_care_agent(
    *,
    runtime_config_provider: RuntimeConfigProvider,
    agent_runner: AgentRunner | None = None,
    rag_port: NonmedicalPetCareRagPort | None = None,
    trace_sink: NonmedicalPetCareTraceSink | None = None,
    observability_provider: ObservabilityProvider | None = None,
) -> NonmedicalPetCareAgent:
    """创建默认 NonmedicalPetCareAgent 服务实例。

    :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
    :param agent_runner: 可选 AgentRunner 端口。
    :param rag_port: 可选非医疗 RAG 端口。
    :param trace_sink: 可选非医疗 trace 写入端口。
    :param observability_provider: 可选 Observability provider。
    :return: 默认 NonmedicalPetCareAgent 服务实例。
    """

    return DefaultNonmedicalPetCareAgent(
        runtime_config_provider=runtime_config_provider,
        agent_runner=agent_runner,
        rag_port=rag_port,
        trace_sink=trace_sink,
        observability_provider=observability_provider,
    )


__all__: tuple[str, ...] = ("create_default_nonmedical_pet_care_agent",)

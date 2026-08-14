"""
=============================================================================
文件：src/vet_agent/task_routing/__init__.py
作用：作为 task_routing 包入口，集中暴露多任务拆分迁移后的稳定公共能力。
范围：向 Agent 主链路、容器装配、入口错误转换和测试替身暴露结构化任务路由模型、
      仓储协议、策略客户端和服务。
说明：跨包调用不得直接引用 task_routing 包内部实现文件，应通过本包顶层导出使用。
=============================================================================
"""

from .errors import (
    TaskRoutingContractError,
    TaskRoutingDependencyError,
    TaskRoutingError,
    TaskRoutingPolicyRejectedError,
)
from .models import (
    DEFAULT_TASK_KEY,
    TASK_ROUTING_SCHEMA_VERSION,
    ActiveTaskState,
    RoutedTask,
    TaskExecutionPlan,
    TaskRoutingDecision,
    TaskRoutingDomain,
    TaskRoutingDomainCatalog,
    TaskRoutingProposal,
    TaskRoutingRequestContext,
    TaskRoutingStrategy,
    TaskRoutingTaskProposal,
)
from .policy import (
    LocalTaskRoutingPolicyClient,
    OpaTaskRoutingPolicyClient,
    TaskRoutingPolicyAction,
    TaskRoutingPolicyClient,
    TaskRoutingPolicyDecision,
    TaskRoutingPolicyInput,
)
from .ports import StructuredChatClient, TaskRoutingDomainRepository
from .repository import (
    PostgresTaskRoutingDomainRepository,
    StaticTaskRoutingDomainRepository,
    default_task_routing_domains,
)
from .service import TaskRoutingService

__all__ = [
    "DEFAULT_TASK_KEY",
    "TASK_ROUTING_SCHEMA_VERSION",
    "ActiveTaskState",
    "LocalTaskRoutingPolicyClient",
    "OpaTaskRoutingPolicyClient",
    "PostgresTaskRoutingDomainRepository",
    "RoutedTask",
    "StaticTaskRoutingDomainRepository",
    "StructuredChatClient",
    "TaskExecutionPlan",
    "TaskRoutingContractError",
    "TaskRoutingDecision",
    "TaskRoutingDependencyError",
    "TaskRoutingDomain",
    "TaskRoutingDomainCatalog",
    "TaskRoutingDomainRepository",
    "TaskRoutingError",
    "TaskRoutingPolicyAction",
    "TaskRoutingPolicyClient",
    "TaskRoutingPolicyDecision",
    "TaskRoutingPolicyInput",
    "TaskRoutingPolicyRejectedError",
    "TaskRoutingProposal",
    "TaskRoutingRequestContext",
    "TaskRoutingService",
    "TaskRoutingStrategy",
    "TaskRoutingTaskProposal",
    "default_task_routing_domains",
]

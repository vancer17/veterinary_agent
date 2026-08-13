"""
=============================================================================
文件：src/vet_agent/task_routing/errors.py
作用：定义多任务拆分迁移后的任务路由错误模型。
范围：位于记忆读取之后、问诊语义抽取之前；用于表达结构化任务路由依赖不可用、
      模型输出契约非法、策略拒绝和任务域目录不可用等 Fail Fast 场景。
说明：本文件不访问数据库、不调用模型、不执行策略；仅承载可被入口层转换为
      SERVICE_UNAVAILABLE 的稳定异常契约。
=============================================================================
"""

from __future__ import annotations

from typing import Any


class TaskRoutingError(RuntimeError):
    """表示任务路由数据链中的基础异常。

    :return: 无返回值；异常携带 details 用于入口层和 trace 记录结构化失败原因。
    """

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        """初始化任务路由异常。

        :param message: 面向排障和错误响应的失败描述。
        :param details: 结构化失败详情。
        :return: 无返回值。
        """
        super().__init__(message)
        self.details = details or {}


class TaskRoutingDependencyError(TaskRoutingError):
    """表示任务路由依赖不可用。

    :return: 无返回值；模型网关、任务域目录或策略服务不可用时使用该异常。
    """


class TaskRoutingContractError(TaskRoutingError):
    """表示任务路由结构化输出或技术不变量非法。

    :return: 无返回值；模型返回 schema 合法但不满足任务计划不变量时使用该异常。
    """


class TaskRoutingPolicyRejectedError(TaskRoutingError):
    """表示任务路由策略明确拒绝本轮任务计划。

    :return: 无返回值；OPA 返回 reject 动作或非法拒绝原因时使用该异常。
    """

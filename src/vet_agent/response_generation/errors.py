"""
=============================================================================
文件：src/vet_agent/response_generation/errors.py
作用：定义回复生成上下文编译与模型调用链路的统一异常类型。
范围：区分上下文契约失败、上游结构化依赖失败与通用领域错误；
      不承载提示词编排、模型调用、输出清洗或业务分支裁决。
说明：回复生成链路坚持 Fail Fast，不提供硬编码回复回退、关键词降级或
      自定义状态机兜底；异常会沿主链路显式暴露。
=============================================================================
"""

from __future__ import annotations

from typing import Any


class ResponseGenerationError(Exception):
    """表示回复生成链路的基础领域异常。

    :param message: 错误描述。
    :param details: 结构化排障信息。
    :return: 无返回值；该异常作为回复生成所有显式失败的公共父类。
    """

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        """初始化回复生成基础异常。

        :param message: 错误描述。
        :param details: 结构化排障信息。
        :return: 无返回值。
        """
        super().__init__(message)
        self.details = details or {}


class ResponseGenerationContractError(ResponseGenerationError):
    """表示回复生成上下文输入或配置不满足业务契约。

    :return: 无返回值；该异常通常说明上游结构化事实缺失或调用顺序错误。
    """


class ResponseGenerationDependencyError(ResponseGenerationError):
    """表示回复生成所需的模型客户端或运行时依赖不可用。

    :return: 无返回值；该异常用于表达 LiteLLM、Qwen 或其网络依赖故障。
    """

"""
=============================================================================
文件：src/vet_agent/response_generation/ports.py
作用：定义回复生成上下文编译与模型调用链路的鸭子类型协议。
范围：隔离回复生成服务实现与编排层；编排层只依赖本文件协议，
      不直接穿透到模型调用、提示词拼装或内部辅助方法。
说明：实现类应显式继承对应 Protocol，以便追溯调用栈并区分生产实现与测试替身。
=============================================================================
"""

from __future__ import annotations

from typing import Protocol

from .models import ResponseGenerationRequest, ResponseGenerationResult


class ResponseGenerationServiceProtocol(Protocol):
    """定义回复生成服务协议。

    :return: 无返回值；主编排器通过该协议隔离提示词编译和模型调用实现。
    """

    async def generate(self, request: ResponseGenerationRequest, *, model: str) -> ResponseGenerationResult:
        """在结构化上下文编译完成后生成最终回复。

        :param request: 回复生成结构化请求。
        :param model: 模型名称。
        :return: 返回回复生成结果。
        """
        ...

    def is_ready(self) -> bool:
        """检查回复生成服务是否就绪。

        :return: 模型客户端与编译器可用时返回 True。
        """
        ...

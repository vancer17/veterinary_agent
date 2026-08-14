"""
=============================================================================
文件：src/vet_agent/consultation_state/errors.py
作用：定义问诊状态与回答充分性迁移链路的显式错误类型。
范围：承载状态合并、证据画像、OPA 策略裁决与服务编排中的契约错误、依赖错误
      和策略拒绝错误；业务层通过这些异常区分输入非法、外部依赖不可用与策略
      拒绝三类失败语义。
说明：本文件不访问数据库、不调用外部服务、不扫描用户原始文本，仅作为异常
      契约的稳定出口，供上层服务、容器装配与测试断言使用。
=============================================================================
"""

from __future__ import annotations

from typing import Any


class ConsultationStateError(RuntimeError):
    """表示问诊状态与回答充分性链路的基础异常。

    :return: 无返回值；该异常是本迁移阶段所有显式失败语义的根异常。
    """

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        """初始化问诊状态基础异常。

        :param message: 异常说明。
        :param details: 可审计的结构化附加信息。
        :return: 无返回值。
        """
        super().__init__(message)
        self.details = details or {}


class ConsultationStateContractError(ConsultationStateError):
    """表示问诊状态或策略输入契约非法的异常。

    :return: 无返回值；该异常用于标识可由调用方修正的结构化输入问题。
    """


class ConsultationStateDependencyError(ConsultationStateError):
    """表示问诊状态链路依赖不可用或外部调用失败的异常。

    :return: 无返回值；该异常用于标识数据库、OPA 或其他运行时依赖故障。
    """


class ConsultationStatePolicyRejectedError(ConsultationStateError):
    """表示回答充分性策略明确拒绝当前请求的异常。

    :return: 无返回值；该异常用于标识策略层拒绝继续进入主链路。
    """

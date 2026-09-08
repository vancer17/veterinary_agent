"""
=============================================================================
文件：src/vet_agent/semantic_preprocessing/production_sources.py
作用：在语义预处理组合包中提供 M02 可安全接入的受限来源 Adapter。
范围：覆盖首轮历史 / 已验证事实的显式边界和服务端可信宠物画像白名单投影。
说明：本文件不读取旧问诊状态、不解析历史响应全文、不消费旧结构化记忆、
      不推断 proposition 极性；多轮来源未就绪时必须显式 Fail Fast。
=============================================================================
"""

from __future__ import annotations

from math import isfinite
from typing import Any

from vet_agent import TrustedIdentity
from vet_agent.repositories import ScopeRepository
from vet_agent.semantic_collaboration.errors import (
    TurnSnapshotSourceUnavailableError,
)
from vet_agent.semantic_collaboration.snapshot_contracts import (
    BoundedHistoryReadResult,
    BoundedHistoryReadStatus,
    TrustedPetContext,
    TrustedPetContextSource,
    TrustedPetProfile,
    TurnSnapshotSourceRequest,
    VerifiedPriorFactSummary,
    VerifiedPriorFactSummaryStatus,
)


class FirstTurnOnlyBoundedHistoryReader:
    """表示等待结构化追问读模型前的显式首轮历史边界。

    :return: 无返回值；第一轮返回 no_previous_turn，多轮显式失败。
    """

    async def read(
        self,
        request: TurnSnapshotSourceRequest,
    ) -> BoundedHistoryReadResult:
        """读取当前回合允许消费的上一轮受限历史。

        :param request: 不携带当前原文的来源读取请求。
        :return: 第一轮返回 NO_PREVIOUS_TURN。
        :raises TurnSnapshotSourceUnavailableError: 非首轮且结构化历史读模型未接入时抛出。
        """
        if request.turn_index == 0:
            return BoundedHistoryReadResult(
                status=BoundedHistoryReadStatus.NO_PREVIOUS_TURN,
            )
        raise TurnSnapshotSourceUnavailableError(
            "structured assistant question history is not implemented",
            source_name="bounded_conversation_history",
        )


class FirstTurnOnlyVerifiedPriorFactReader:
    """表示等待 proposition-preserving 记忆读模型前的显式历史事实边界。

    :return: 无返回值；第一轮返回 no_verified_claims，多轮显式失败。
    """

    async def read(
        self,
        request: TurnSnapshotSourceRequest,
    ) -> VerifiedPriorFactSummary:
        """读取当前回合允许消费的已验证历史事实摘要。

        :param request: 不携带当前原文的来源读取请求。
        :return: 第一轮返回 NO_VERIFIED_CLAIMS。
        :raises TurnSnapshotSourceUnavailableError: 非首轮且历史事实读模型未接入时抛出。
        """
        if request.turn_index == 0:
            return VerifiedPriorFactSummary(
                status=VerifiedPriorFactSummaryStatus.NO_VERIFIED_CLAIMS,
            )
        raise TurnSnapshotSourceUnavailableError(
            "proposition-preserving verified prior fact reader is not implemented",
            source_name="verified_prior_fact_summary",
        )


def _optional_text(profile: dict[str, Any], key: str) -> str | None:
    """读取白名单中的可选文本字段。

    :param profile: 服务端已验证宠物画像 JSON。
    :param key: 白名单字段名。
    :return: 返回字符串值；字段缺失时返回 None。
    :raises TurnSnapshotSourceUnavailableError: 字段类型非法时抛出。
    """
    value = profile.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TurnSnapshotSourceUnavailableError(
            "trusted pet profile text field type is invalid",
            source_name="trusted_pet_context",
        )
    return value


def _optional_weight(profile: dict[str, Any]) -> float | None:
    """读取白名单中的服务端已验证体重。

    :param profile: 服务端已验证宠物画像 JSON。
    :return: 返回非 NaN 且非无穷的千克体重。
    :raises TurnSnapshotSourceUnavailableError: 字段类型或数值非法时抛出。
    """
    value = profile.get("weight_kg")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TurnSnapshotSourceUnavailableError(
            "trusted pet profile weight type is invalid",
            source_name="trusted_pet_context",
        )
    weight = float(value)
    if weight < 0 or not isfinite(weight):
        raise TurnSnapshotSourceUnavailableError(
            "trusted pet profile weight value is invalid",
            source_name="trusted_pet_context",
        )
    return weight


def _optional_bool(profile: dict[str, Any], key: str) -> bool | None:
    """读取白名单中的可选布尔字段。

    :param profile: 服务端已验证宠物画像 JSON。
    :param key: 白名单字段名。
    :return: 返回布尔值；字段缺失时返回 None。
    :raises TurnSnapshotSourceUnavailableError: 字段类型非法时抛出。
    """
    value = profile.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise TurnSnapshotSourceUnavailableError(
            "trusted pet profile boolean field type is invalid",
            source_name="trusted_pet_context",
        )
    return value


class ScopeTrustedPetContextReader:
    """从服务端范围仓储读取 M02 允许的可信宠物画像。

    :return: 无返回值；该 Adapter 只做白名单字段投影，不读取请求侧自报资料。
    """

    def __init__(self, scope_repository: ScopeRepository) -> None:
        """初始化范围仓储 Adapter。

        :param scope_repository: 服务端宠物画像与会话绑定仓储。
        :return: 无返回值。
        """
        self.scope_repository = scope_repository

    async def read(
        self,
        request: TurnSnapshotSourceRequest,
    ) -> TrustedPetContext:
        """读取与可信范围完全匹配的白名单宠物画像。

        :param request: 不携带当前原文的来源读取请求。
        :return: 返回 M02 可消费的可信宠物上下文。
        :raises TurnSnapshotSourceUnavailableError: 画像缺失、停用或范围不匹配时抛出。
        """
        identity = TrustedIdentity(
            user_id=request.scope.user_id,
            session_id=request.scope.session_id,
            pet_id=request.scope.pet_id,
        )
        try:
            profile = self.scope_repository.get_pet_profile(identity)
            binding = self.scope_repository.get_session_binding(
                request.scope.session_id,
            )
        except Exception as error:
            raise TurnSnapshotSourceUnavailableError(
                "trusted pet context source read failed",
                source_name="trusted_pet_context",
            ) from error
        if (
            profile is None
            or not profile.is_active
            or binding is None
            or binding.user_id != request.scope.user_id
            or binding.pet_id != request.scope.pet_id
            or profile.user_id != request.scope.user_id
            or profile.pet_id != request.scope.pet_id
        ):
            raise TurnSnapshotSourceUnavailableError(
                "trusted pet profile scope is not available",
                source_name="trusted_pet_context",
            )
        return TrustedPetContext(
            source=TrustedPetContextSource.SCOPE_CONTEXT_SERVICE,
            profile=TrustedPetProfile(
                species=_optional_text(profile.profile, "species"),
                breed=_optional_text(profile.profile, "breed"),
                age_text=_optional_text(profile.profile, "age"),
                weight_kg=_optional_weight(profile.profile),
                sex=_optional_text(profile.profile, "sex"),
                neutered=_optional_bool(profile.profile, "neutered"),
            ),
        )


__all__ = [
    "FirstTurnOnlyBoundedHistoryReader",
    "FirstTurnOnlyVerifiedPriorFactReader",
    "ScopeTrustedPetContextReader",
]

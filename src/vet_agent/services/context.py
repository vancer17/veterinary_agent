"""
文件：src/vet_agent/services/context.py
作用：组装当前回合可进入 Agent 主链路的宠物上下文，并隔离已验证资料与请求侧自报资料。
范围：位于范围授权之后、临床安全与问诊链路之前，只消费 ScopeContextService 暴露的已验证宠物资料。
说明：本文件不直接访问数据库模型，不从 pet_info 推断权威画像；缺少已验证资料时按 Fail Fast 处理。
"""


from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ingress import ForbiddenError
from vet_agent import AuthorizedScopeContext, Evidence, ScopeAssertion, TrustedIdentity, VetContext

from .scope import ScopeContextService


@dataclass
class PetContext:
    """表示当前回合可见的宠物上下文。

    :param verified_profile: 服务端已验证的宠物画像。
    :param reported_profile: 请求侧自报的宠物画像，仅保留审计提示。
    :param telemetry: 服务端已验证的近期指标。
    :param algorithm_risks: 服务端已验证的算法风险。
    :param alerts: 服务端已验证的告警。
    :param device: 服务端已验证的设备状态。
    :param evidence: 可对外展示的上下文证据。
    :return: 无返回值。
    """

    verified_profile: dict[str, Any] = field(default_factory=dict)
    reported_profile: dict[str, Any] = field(default_factory=dict)
    telemetry: dict[str, Any] = field(default_factory=dict)
    algorithm_risks: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    device: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)

    def summary(self) -> str:
        """生成可供模型使用的已验证宠物上下文摘要。

        :return: 返回已验证宠物上下文摘要。
        """
        profile_line = "宠物画像: 当前暂无服务端已验证资料，相关字段按未知处理。"
        if self.verified_profile:
            profile_line = (
                f"宠物画像: 物种={self.verified_profile.get('species', '未知')}, "
                f"品种={self.verified_profile.get('breed', '未知')}, "
                f"年龄={self.verified_profile.get('age', '未知')}, "
                f"体重={self.verified_profile.get('weight_kg', '未知')}kg, "
                f"性别={self.verified_profile.get('sex', '未知')}, "
                f"绝育={self.verified_profile.get('neutered', '未知')}。"
            )

        telemetry_line = "近期指标: 当前暂无服务端已验证遥测数据。"
        if self.telemetry:
            telemetry_line = (
                f"近期指标: 静息心率={self.telemetry.get('resting_hr', '未知')}, "
                f"呼吸率={self.telemetry.get('respiratory_rate', '未知')}, "
                f"活动量={self.telemetry.get('activity_level', '未知')}。"
            )

        risk_line = "算法风险: 当前暂无服务端已验证风险信号。"
        if self.algorithm_risks:
            risks = ", ".join(
                f"{risk.get('domain')}={risk.get('level')}({risk.get('confidence')})" for risk in self.algorithm_risks
            )
            risk_line = f"算法风险: {risks or '暂无高风险信号'}。"

        alert_line = "告警: 当前暂无服务端已验证告警。"
        if self.alerts:
            alert_line = "告警: " + ", ".join(str(alert.get("message") or alert.get("code") or "未知告警") for alert in self.alerts) + "。"

        return "\n".join([profile_line, telemetry_line, risk_line, alert_line])


class PetContextProvider:
    """组装当前回合可用的宠物上下文。

    说明：当前仅暴露服务端已验证资料；请求侧自报信息只保留为审计提示，不进入临床硬判断。
    """

    def __init__(self, scope_service: ScopeContextService) -> None:
        """初始化宠物上下文提供器。

        :param scope_service: 身份、宠物资料与会话范围上下文服务。
        :return: 无返回值。
        """
        self.scope_service = scope_service

    async def load(
        self,
        identity: TrustedIdentity,
        scope_assertion: ScopeAssertion,
        vet_context: VetContext,
        metadata: dict[str, Any],
        *,
        authorized_scope_context: AuthorizedScopeContext | None = None,
    ) -> PetContext:
        """加载当前回合可见的宠物上下文。

        :param identity: 本轮可信身份范围。
        :param scope_assertion: BFF 对本轮 Agent 调用范围的服务端声明。
        :param vet_context: 兽医业务上下文。
        :param metadata: 附加元数据。
        :param authorized_scope_context: 入口授权后生成的内部范围上下文快照。
        :return: 返回仅包含已验证资料的宠物上下文。
        """
        del metadata
        if authorized_scope_context is None:
            scope_context = await self.scope_service.authorize(
                scope_assertion,
                pet_info=vet_context.pet_info,
            )
            verified_profile = scope_context.verified_profile
            reported_pet_info = scope_context.reported_pet_info
        else:
            if authorized_scope_context.identity != identity:
                raise ForbiddenError(
                    "authorized scope context does not match request identity",
                    details={
                        "user_id": identity.user_id,
                        "pet_id": identity.pet_id,
                        "session_id": identity.session_id,
                    },
                )
            verified_profile = dict(authorized_scope_context.verified_profile)
            reported_pet_info = dict(authorized_scope_context.reported_pet_info)
        if not verified_profile:
            raise ForbiddenError(
                "verified pet profile is required",
                details={
                    "user_id": identity.user_id,
                    "pet_id": identity.pet_id,
                    "session_id": identity.session_id,
                },
            )
        reported_profile = self._reported_profile(reported_pet_info)
        evidence = self._build_evidence(identity, reported_profile, verified_profile)
        return PetContext(
            verified_profile=verified_profile,
            reported_profile=reported_profile,
            telemetry={},
            algorithm_risks=[],
            alerts=[],
            device={},
            evidence=evidence,
        )

    def _reported_profile(self, pet_info: dict[str, Any]) -> dict[str, Any]:
        """整理请求侧自报的宠物画像。

        :param pet_info: 请求侧提交的宠物信息。
        :return: 返回经过基础筛选的自报宠物画像。
        """
        if not pet_info:
            return {}
        reported: dict[str, Any] = {}
        for key in ("species", "breed", "age", "sex", "neutered"):
            value = pet_info.get(key)
            if value not in (None, ""):
                reported[key] = value
        weight = pet_info.get("weight_kg") or pet_info.get("weight")
        if weight not in (None, ""):
            reported["weight_kg"] = weight
        return reported

    def _build_evidence(
        self,
        identity: TrustedIdentity,
        reported_profile: dict[str, Any],
        verified_profile: dict[str, Any],
    ) -> list[Evidence]:
        """构造宠物上下文证据。

        :param identity: 本轮可信身份范围。
        :param reported_profile: 请求侧自报的宠物画像。
        :param verified_profile: 服务端已验证的宠物画像。
        :return: 返回上下文证据列表。
        """
        evidence: list[Evidence] = [
            Evidence(
                source="可信宠物画像",
                detail=(
                    f"pet_id={identity.pet_id} 的服务端已验证资料已加载。"
                    if verified_profile
                    else "当前未接入服务端已验证宠物画像，相关字段已按未知处理。"
                ),
            )
        ]
        if reported_profile:
            evidence.append(
                Evidence(
                    source="请求自报宠物资料",
                    detail="本轮收到的 pet_info 仅作为未验证输入保留，不进入临床硬判断。",
                )
            )
        return evidence

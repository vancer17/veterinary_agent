"""
文件：src/vet_agent/services/context.py
作用：组装当前回合可见的宠物上下文，并区分已验证资料与自报资料。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vet_agent import Evidence, VetContext


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

    async def load(self, vet_context: VetContext, metadata: dict[str, Any]) -> PetContext:
        """加载当前回合可见的宠物上下文。

        :param vet_context: 兽医业务上下文。
        :param metadata: 附加元数据。
        :return: 返回仅包含已验证资料的宠物上下文。
        """
        reported_profile = self._reported_profile(vet_context.pet_info)
        verified_profile = self._load_verified_profile(vet_context, metadata)
        evidence = self._build_evidence(vet_context, reported_profile, verified_profile)
        return PetContext(
            verified_profile=verified_profile,
            reported_profile=reported_profile,
            telemetry={},
            algorithm_risks=[],
            alerts=[],
            device={},
            evidence=evidence,
        )

    def _load_verified_profile(self, vet_context: VetContext, metadata: dict[str, Any]) -> dict[str, Any]:
        """加载服务端已验证的宠物画像。

        :param vet_context: 兽医业务上下文。
        :param metadata: 附加元数据。
        :return: 当前未接入可信资料源时返回空字典。
        """
        # TODO: 接入宠物画像领域后，仅从该领域读取并验证服务端资料。
        del vet_context, metadata
        return {}

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
        vet_context: VetContext,
        reported_profile: dict[str, Any],
        verified_profile: dict[str, Any],
    ) -> list[Evidence]:
        """构造宠物上下文证据。

        :param vet_context: 兽医业务上下文。
        :param reported_profile: 请求侧自报的宠物画像。
        :param verified_profile: 服务端已验证的宠物画像。
        :return: 返回上下文证据列表。
        """
        evidence: list[Evidence] = [
            Evidence(
                source="可信宠物画像",
                detail=(
                    f"pet_id={vet_context.pet_id} 的服务端已验证资料已加载。"
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

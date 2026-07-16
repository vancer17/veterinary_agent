from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from vet_agent.contracts import Evidence, VetContext


@dataclass
class PetContext:
    profile: dict[str, Any] = field(default_factory=dict)
    telemetry: dict[str, Any] = field(default_factory=dict)
    algorithm_risks: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    device: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)

    def summary(self) -> str:
        profile = self.profile
        telemetry = self.telemetry
        risks = ", ".join(
            f"{risk.get('domain')}={risk.get('level')}({risk.get('confidence')})" for risk in self.algorithm_risks
        )
        return (
            f"宠物画像: 物种={profile.get('species', '未知')}, 品种={profile.get('breed', '未知')}, "
            f"年龄={profile.get('age', '未知')}, 体重={profile.get('weight_kg', '未知')}kg, "
            f"性别={profile.get('sex', '未知')}, 绝育={profile.get('neutered', '未知')}。\n"
            f"近期指标: 静息心率={telemetry.get('resting_hr', '未知')}, "
            f"呼吸率={telemetry.get('respiratory_rate', '未知')}, 活动量={telemetry.get('activity_level', '未知')}。\n"
            f"算法风险: {risks or '暂无高风险信号'}。"
        )


class PetContextProvider:
    """Aggregates existing pet data from trusted backend context."""

    async def load(self, vet_context: VetContext, metadata: dict[str, Any]) -> PetContext:
        supplied = vet_context.pet_info or {}
        profile = {
            "species": supplied.get("species") or metadata.get("species") or "未知",
            "breed": supplied.get("breed") or metadata.get("breed") or "未知品种",
            "age": supplied.get("age") or metadata.get("age") or "未知",
            "weight_kg": supplied.get("weight_kg") or supplied.get("weight") or metadata.get("weight_kg"),
            "sex": supplied.get("sex") or metadata.get("sex") or "未知",
            "neutered": supplied.get("neutered") if supplied.get("neutered") is not None else metadata.get("neutered", "未知"),
        }
        telemetry = metadata.get("telemetry") or supplied.get("telemetry") or {}
        telemetry.setdefault("resting_hr", "暂无")
        telemetry.setdefault("respiratory_rate", "暂无")
        telemetry.setdefault("activity_level", "暂无")
        telemetry.setdefault("data_freshness", "unknown")

        risks = metadata.get("algorithm_risks") or supplied.get("algorithm_risks") or []
        alerts = metadata.get("alerts") or supplied.get("alerts") or []
        device = supplied.get("device") or metadata.get("device") or {
            "bound": False,
            "online": "unknown",
            "last_seen_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        }

        evidence = [
            Evidence(source="我方宠物画像", detail=f"pet_id={vet_context.pet_id} 的基础资料已加载。"),
            Evidence(source="我方健康数据", detail="近期生命体征/活动数据已作为上下文输入。"),
        ]
        for risk in risks:
            contribution = risk.get("evidence_contribution") or risk.get("evidence") or "算法风险输出"
            evidence.append(
                Evidence(
                    source="我方健康算法 v2.0",
                    detail=f"{risk.get('domain', '未知风险域')}: {contribution}",
                )
            )

        return PetContext(profile=profile, telemetry=telemetry, algorithm_risks=risks, alerts=alerts, device=device, evidence=evidence)

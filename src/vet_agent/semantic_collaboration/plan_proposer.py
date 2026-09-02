"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/plan_proposer.py
作用：实现受限语义协作 DAG 的 M03 任务规划 LLM 最小选择适配器。
范围：覆盖任务规划上下文投影、固定字段 PlanSelection 结构化调用、
      模型失败与结构化失败显式包装。
说明：本文件不让模型输出 Plan IR、任务引用、依赖、版本或 schema；失败时不
      重试、不生成默认计划、不回退旧问诊语义抽取器。
=============================================================================
"""

from __future__ import annotations

import json
from typing import Protocol

from pydantic import ValidationError

from .contracts import (
    DOMAIN_ISOLATED_CONTEXT_RESOURCES,
    ContextContract,
    SkillContextResource,
)
from .errors import PlanModelClientError, PlanSelectionSchemaError
from .plan_contracts import PlanPolicySpec, PlanSelection
from .snapshot import TurnSnapshotProjector
from .snapshot_contracts import TurnSnapshot

PLAN_PLANNER_CONTEXT_CONTRACT = ContextContract(
    required_resources=(
        SkillContextResource.TURN_SNAPSHOT_DIGEST,
        SkillContextResource.ORIGINAL_USER_TEXT,
        SkillContextResource.BOUNDED_CONVERSATION_HISTORY,
        SkillContextResource.LAST_ASSISTANT_QUESTIONS,
        SkillContextResource.VERIFIED_PRIOR_FACT_SUMMARY,
        SkillContextResource.TRUSTED_PET_CONTEXT,
    ),
    forbidden_resources=tuple(sorted(DOMAIN_ISOLATED_CONTEXT_RESOURCES)),
)


class StructuredPlanModelClient(Protocol):
    """表示任务规划模型客户端必须提供的结构化调用能力。

    :return: 无返回值；该协议隔离 QwenClient 实现，避免跨包直接依赖。
    """

    async def chat_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_model: type[PlanSelection],
        model: str | None,
        temperature: float,
    ) -> PlanSelection:
        """调用结构化模型并返回 PlanSelection。

        :param messages: OpenAI 兼容消息列表。
        :param response_model: 模型必须遵守的 Pydantic 契约。
        :param model: 可选模型名称；缺省由客户端使用生产默认模型。
        :param temperature: 结构化调用温度。
        :return: 返回通过 PlanSelection 校验的结构化结果。
        """


class LLMPlanSelector:
    """把任务规划 LLM 限制为固定字段 PlanSelection 生产者。

    :return: 无返回值；该类不编译计划、不校验 Plan IR、不执行调度。
    """

    def __init__(
        self,
        *,
        client: StructuredPlanModelClient,
        policy: PlanPolicySpec,
        model: str | None = None,
    ) -> None:
        """初始化结构化模型选择器。

        :param client: 满足结构化调用协议的模型客户端。
        :param policy: 初始 Turn Plan 生产策略。
        :param model: 可选模型名称；缺省使用模型客户端默认模型。
        :return: 无返回值。
        """
        self.client = client
        self.policy = policy
        self.model = model
        self.projector = TurnSnapshotProjector()

    async def select(self, snapshot: TurnSnapshot) -> PlanSelection:
        """从当前 TurnSnapshot 生成最小计划选择结果。

        :param snapshot: 当前回合不可变 TurnSnapshot。
        :return: 返回仅包含固定字段选择的 PlanSelection。
        :raises PlanModelClientError: 模型客户端调用失败时抛出。
        :raises PlanSelectionSchemaError: 模型返回值不符合结构化契约时抛出。
        """
        projection = self.projector.project(
            snapshot,
            PLAN_PLANNER_CONTEXT_CONTRACT,
        )
        messages = self._messages(projection.model_dump(mode="json"))
        try:
            return await self.client.chat_structured(
                messages,
                response_model=PlanSelection,
                model=self.model,
                temperature=0.0,
            )
        except ValidationError as error:
            raise PlanSelectionSchemaError(
                "plan selection response failed strict schema validation",
            ) from error
        except Exception as error:
            raise PlanModelClientError(
                "structured plan model client call failed",
            ) from error

    def _messages(self, projection_payload: dict[str, object]) -> list[dict[str, str]]:
        """构造受限任务规划模型消息。

        :param projection_payload: 已通过 M02 访问控制的投影字段。
        :return: 返回系统与用户两条 OpenAI 兼容消息。
        """
        context_json = json.dumps(
            projection_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        system_message = (
            "你是受限语义协作 DAG 的任务规划器。你只能输出固定字段 JSON。"
            "不要输出任务图、依赖、任务 ID、版本号、schema、医学判断或解释文本。"
            "claim_envelope_count 只是执行槽位估计，不是最终 claim 数量。"
        )
        user_message = (
            "请根据以下受限回合上下文选择需要启用的语义 lane。\n"
            f"claim_envelope_count 允许范围：0 到 {self.policy.max_claim_envelope_count}。\n"
            "当 claim_envelope_count 大于 0 时，run_statement_semantics 必须为 true。\n"
            "输出字段必须且只能是：claim_envelope_count、"
            "run_statement_semantics、run_participant_phrase、"
            "run_temporal_phrase、run_measurement_phrase、"
            "run_canonical_descriptor。\n"
            f"受限上下文 JSON：{context_json}"
        )
        return [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ]

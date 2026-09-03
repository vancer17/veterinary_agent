"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/generation.py
作用：实现受限语义协作 DAG M06 的结构化生成执行器。
范围：覆盖精确模型策略、TurnSnapshot 读取端口、SkillSpec 解析、受限上下文
      投影、版本化 prompt renderer 调用与 M05 StructuredLLMGateway 组装。
说明：本文件只返回 SemanticModelProposal，不验证语义、不生成 artifact、不返回
      DAG 任务终态、不调用问诊或临床安全领域，也不提供旧链路回退。
=============================================================================
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .catalog import SkillRegistry
from .contracts import SkillExecutionFamily, SkillSpec
from .errors import SemanticGenerationContractError
from .gateway import StructuredLLMGateway
from .gateway_contracts import (
    SemanticModelProposal,
    StructuredLLMCallRequest,
)
from .prompt_renderer import (
    SkillPromptRendererRegistry,
    SkillPromptRenderRequest,
)
from .scheduler_contracts import SemanticTaskExecutionRequest
from .snapshot import TurnSnapshotProjector
from .snapshot_contracts import TurnSnapshot


class TurnSnapshotReader(Protocol):
    """表示 M06 worker 按 digest 读取权威 TurnSnapshot 的端口。

    :return: 无返回值；实现不得返回摘要替代品或未验证上下文。
    """

    async def load(self, turn_snapshot_digest: str) -> TurnSnapshot:
        """读取指定摘要对应的不可变 TurnSnapshot。

        :param turn_snapshot_digest: 任务绑定的上下文 SHA-256 摘要。
        :return: 返回可验证 digest 的完整回合快照。
        """


class TODOTurnSnapshotReader:
    """表示持久化 TurnSnapshot 读取实现尚未接入前的显式空壳。

    :return: 无返回值；该占位始终 Fail Fast，不伪造快照。
    """

    async def load(self, turn_snapshot_digest: str) -> TurnSnapshot:
        """阻断尚未实现的持久化 TurnSnapshot 读取。

        :param turn_snapshot_digest: 任务绑定的上下文 SHA-256 摘要。
        :raises NotImplementedError: 持久化读取端口未实现时始终抛出。
        :return: 无返回值。
        """
        raise NotImplementedError(
            "persistent TurnSnapshot reader is not implemented",
        )


class SemanticGenerationModelRule(BaseModel):
    """表示单个生产 SKILL 的精确结构化模型调用策略。

    :return: 无返回值；策略不提供 fallback 模型或隐藏采样配置。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    skill_id: str = Field(
        min_length=1,
        max_length=120,
        description="模型策略绑定的生产 SKILL 标识。",
    )
    skill_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$",
        description="模型策略绑定的生产 SKILL 精确版本。",
    )
    model: str = Field(
        min_length=1,
        max_length=200,
        description="必须精确传给 M05 的模型名称。",
    )
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="结构化生成采样温度。",
    )
    timeout_seconds: float | None = Field(
        default=None,
        gt=0.0,
        le=600.0,
        description="可选单次模型调用超时时间。",
    )


class SemanticGenerationModelPolicy(BaseModel):
    """表示当前 M06 生产面的精确模型策略集合。

    :return: 无返回值；策略解析失败在模型调用前显式阻断。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    rules: tuple[SemanticGenerationModelRule, ...] = Field(
        min_length=1,
        description="按 SKILL 精确身份绑定的结构化模型调用规则。",
    )

    @model_validator(mode="after")
    def validate_rule_identities(self) -> Self:
        """校验模型策略身份唯一且无重复绑定。

        :return: 返回可进入 M06 执行器的模型策略。
        :raises ValueError: SKILL 身份重复时抛出。
        """
        identities = [
            (rule.skill_id, rule.skill_version) for rule in self.rules
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("semantic generation model rule identity is duplicate")
        return self

    def require(
        self,
        skill_id: str,
        skill_version: str,
    ) -> SemanticGenerationModelRule:
        """解析指定 SKILL 的精确模型策略。

        :param skill_id: 生产 SKILL 稳定标识。
        :param skill_version: 生产 SKILL 精确版本。
        :return: 返回该 SKILL 的模型、采样与超时策略。
        :raises SemanticGenerationContractError: 策略缺失时抛出。
        """
        for rule in self.rules:
            if (rule.skill_id, rule.skill_version) == (
                skill_id,
                skill_version,
            ):
                return rule
        raise SemanticGenerationContractError(
            "semantic generation model policy is not registered",
        )


class StructuredGenerationSkillRunner:
    """表示 M06 自然语言 proposition 生成执行器。

    :return: 无返回值；执行器组合 M02、M05 与 M06 renderer，不产生权威 artifact。
    """

    def __init__(
        self,
        *,
        registry: SkillRegistry,
        renderer_registry: SkillPromptRendererRegistry,
        snapshot_reader: TurnSnapshotReader,
        projector: TurnSnapshotProjector,
        gateway: StructuredLLMGateway,
        model_policy: SemanticGenerationModelPolicy,
    ) -> None:
        """初始化 M06 生成执行器的封闭依赖集合。

        :param registry: 已冻结的生产 SkillCatalog 只读门面。
        :param renderer_registry: 启动期闭合的版本化 renderer 目录。
        :param snapshot_reader: 按 digest 读取 TurnSnapshot 的端口。
        :param projector: 按 SkillSpec 生成受限上下文投影的投影器。
        :param gateway: M05 单次结构化模型网关。
        :param model_policy: 当前生产面的精确模型策略。
        :return: 无返回值。
        """
        self.registry = registry
        self.renderer_registry = renderer_registry
        self.snapshot_reader = snapshot_reader
        self.projector = projector
        self.gateway = gateway
        self.model_policy = model_policy

    async def generate(
        self,
        execution: SemanticTaskExecutionRequest,
    ) -> SemanticModelProposal:
        """执行一次受限 M06 生成任务并返回模型 proposal。

        :param execution: M04 传入的权威任务执行 envelope。
        :return: 返回尚未通过 M07/M08 验证的 SemanticModelProposal。
        :raises SemanticGenerationContractError: 任务、SKILL 或模型策略非法时抛出。
        """
        spec = self._resolve_spec(execution)
        snapshot = await self.snapshot_reader.load(
            execution.turn_snapshot_digest,
        )
        snapshot.verify_digest(execution.turn_snapshot_digest)
        projection = self.projector.project(
            snapshot,
            spec.context_contract,
        )
        renderer = self.renderer_registry.require(
            spec.skill_id,
            spec.skill_version,
        )
        prompt = renderer.render(
            SkillPromptRenderRequest(
                execution=execution,
                spec=spec,
                projection=projection,
            ),
        )
        model_rule = self.model_policy.require(
            spec.skill_id,
            spec.skill_version,
        )
        return await self.gateway.generate(
            StructuredLLMCallRequest(
                execution=execution,
                prompt=prompt,
                model=model_rule.model,
                temperature=model_rule.temperature,
                timeout_seconds=model_rule.timeout_seconds,
            ),
        )

    def _resolve_spec(
        self,
        execution: SemanticTaskExecutionRequest,
    ) -> SkillSpec:
        """解析并校验当前任务绑定的权威生成 SkillSpec。

        :param execution: M04 传入的权威任务执行 envelope。
        :return: 返回精确版本的生产生成 SkillSpec。
        :raises SemanticGenerationContractError: SKILL 缺失或非生成任务时抛出。
        """
        try:
            spec = self.registry.require(
                execution.task.skill_id,
                execution.task.skill_version,
            )
        except Exception as exc:
            raise SemanticGenerationContractError(
                "semantic generation skill is not registered",
            ) from exc
        if spec.execution_family is not SkillExecutionFamily.STRUCTURED_GENERATION:
            raise SemanticGenerationContractError(
                "semantic generation runner requires structured generation skill",
            )
        return spec


def validate_generation_configuration(
    *,
    specs: Iterable[SkillSpec],
    renderer_registry: SkillPromptRendererRegistry,
    model_policy: SemanticGenerationModelPolicy,
) -> None:
    """校验 M06 SkillCatalog、renderer 与模型策略全部闭合。

    :param specs: 生产 SkillCatalog 中的全部 SkillSpec。
    :param renderer_registry: 当前生产 renderer 目录。
    :param model_policy: 当前生产模型策略。
    :return: 无返回值。
    :raises SemanticGenerationContractError: 任一生成 SKILL 配置缺失时抛出。
    """
    materialized_specs = tuple(specs)
    renderer_registry.validate_catalog(materialized_specs)
    generation_identities = {
        (spec.skill_id, spec.skill_version)
        for spec in materialized_specs
        if spec.execution_family is SkillExecutionFamily.STRUCTURED_GENERATION
    }
    policy_identities = {
        (rule.skill_id, rule.skill_version) for rule in model_policy.rules
    }
    if policy_identities != generation_identities:
        raise SemanticGenerationContractError(
            "semantic generation model policy is not closed to catalog",
        )
    for spec in materialized_specs:
        if spec.execution_family is not SkillExecutionFamily.STRUCTURED_GENERATION:
            continue
        model_policy.require(spec.skill_id, spec.skill_version)

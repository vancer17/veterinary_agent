"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/plan_compiler.py
作用：实现受限语义协作 DAG 的 M03 确定性 Plan Compiler。
范围：覆盖 PlanSelection 预算检查、SkillRegistry 解析、必选任务展开、
      claim envelope 生成、静态依赖重写、schema 引用注入与 Plan IR 身份计算。
说明：本文件不调用 LLM、不修复非法选择、不生成默认回退计划，也不接入 M04
      DAGScheduler；所有不可编译输入均以稳定错误显式失败。
=============================================================================
"""

from __future__ import annotations

from .catalog import SkillRegistry
from .contracts import SkillSpec
from .errors import PlanCompilationError
from .plan_contracts import (
    PLAN_IR_VERSION,
    PlanDependency,
    PlanDependencyRule,
    PlanEnvelope,
    PlanEnvelopeKind,
    PlanIR,
    PlanPolicySpec,
    PlanSelection,
    PlanSkillRequirementMode,
    PlanTask,
    PlanTaskSelectionSource,
    SchemaContractReference,
    compute_plan_digest,
    compute_plan_policy_digest,
)
from .snapshot_contracts import TurnSnapshot


class DeterministicPlanCompiler:
    """把模型侧最小 PlanSelection 确定性编译为 CandidatePlanIR。

    :return: 无返回值；编译结果仍必须经过 PlanValidator 才能调度。
    """

    def __init__(
        self,
        *,
        registry: SkillRegistry,
        policy: PlanPolicySpec,
    ) -> None:
        """初始化绑定权威目录与生产策略的计划编译器。

        :param registry: 已冻结的 SkillCatalog 只读门面。
        :param policy: 初始 Turn Plan 生产策略。
        :return: 无返回值。
        :raises SkillCatalogError: 策略引用的 SKILL 或版本不存在时抛出。
        """
        self.registry = registry
        self.policy = policy
        self.skill_specs = self._resolve_policy_specs()
        self.policy_digest = compute_plan_policy_digest(policy)

    def compile(
        self,
        selection: PlanSelection,
        snapshot: TurnSnapshot,
    ) -> PlanIR:
        """将受限计划选择编译为绑定上下文与目录的 Plan IR。

        :param selection: 任务规划 LLM 输出的固定字段选择结果。
        :param snapshot: 当前回合不可变 TurnSnapshot。
        :return: 返回尚未通过 M03 准入门禁的 CandidatePlanIR。
        :raises PlanCompilationError: 选择超过策略预算或策略无法展开时抛出。
        """
        self._validate_budget(selection)
        envelopes = self._build_envelopes(selection)
        unresolved_tasks = self._build_tasks(
            selection=selection,
            snapshot=snapshot,
            envelopes=envelopes,
        )
        dependencies = self._build_dependencies(unresolved_tasks)
        tasks = self._apply_dependencies(
            tasks=unresolved_tasks,
            dependencies=dependencies,
        )
        payload = {
            "plan_version": PLAN_IR_VERSION,
            "turn_id": snapshot.turn_id,
            "snapshot_digest": snapshot.context_digest,
            "skill_catalog_digest": self.registry.contract_digest(),
            "plan_policy_digest": self.policy_digest,
            "envelopes": [envelope.model_dump(mode="json") for envelope in envelopes],
            "tasks": [task.model_dump(mode="json") for task in tasks],
            "dependencies": [
                dependency.model_dump(mode="json")
                for dependency in dependencies
            ],
        }
        return PlanIR(
            plan_id=compute_plan_digest(payload),
            plan_version=PLAN_IR_VERSION,
            turn_id=snapshot.turn_id,
            snapshot_digest=snapshot.context_digest,
            skill_catalog_digest=self.registry.contract_digest(),
            plan_policy_digest=self.policy_digest,
            envelopes=envelopes,
            tasks=tasks,
            dependencies=dependencies,
        )

    def _resolve_policy_specs(self) -> dict[str, SkillSpec]:
        """解析生产策略引用的全部权威 SkillSpec。

        :return: 返回 skill_id 到精确版本 SkillSpec 的映射。
        """
        specs: dict[str, SkillSpec] = {}
        for rule in self.policy.skills:
            specs[rule.skill_id] = self.registry.require(
                rule.skill_id,
                rule.skill_version,
            )
        return specs

    def _validate_budget(self, selection: PlanSelection) -> None:
        """校验模型选择是否超过生产计划硬预算。

        :param selection: 任务规划 LLM 输出的固定字段选择结果。
        :return: 无返回值。
        :raises PlanCompilationError: claim envelope 或任务数量超过策略时抛出。
        """
        if selection.claim_envelope_count > self.policy.max_claim_envelope_count:
            raise PlanCompilationError(
                "claim envelope count exceeds plan policy budget",
                failure_code="plan_budget_exceeded",
            )
        root_task_count = sum(
            rule.requirement == PlanSkillRequirementMode.ALWAYS
            for rule in self.policy.skills
        )
        selected_claim_lanes = sum(
            rule.target_envelope_kind == PlanEnvelopeKind.CLAIM
            and (
                rule.requirement
                == PlanSkillRequirementMode.WHEN_CLAIM_ENVELOPE_PRESENT
                or self._is_rule_selected(rule.skill_id, selection)
            )
            for rule in self.policy.skills
        )
        estimated_task_count = root_task_count + (
            selected_claim_lanes * selection.claim_envelope_count
        )
        if estimated_task_count > self.policy.max_task_count:
            raise PlanCompilationError(
                "estimated plan task count exceeds plan policy budget",
                failure_code="plan_budget_exceeded",
            )

    def _build_envelopes(
        self,
        selection: PlanSelection,
    ) -> tuple[PlanEnvelope, ...]:
        """生成 turn root 与受限数量 claim envelope。

        :param selection: 任务规划 LLM 输出的固定字段选择结果。
        :return: 返回系统生成的不可变 envelope 集合。
        """
        envelopes: list[PlanEnvelope] = [
            PlanEnvelope(
                envelope_id="turn_root",
                kind=PlanEnvelopeKind.TURN,
            ),
        ]
        envelopes.extend(
            PlanEnvelope(
                envelope_id=f"claim_env_{ordinal:04d}",
                kind=PlanEnvelopeKind.CLAIM,
                parent_envelope_id="turn_root",
                ordinal=ordinal,
            )
            for ordinal in range(selection.claim_envelope_count)
        )
        return tuple(envelopes)

    def _build_tasks(
        self,
        *,
        selection: PlanSelection,
        snapshot: TurnSnapshot,
        envelopes: tuple[PlanEnvelope, ...],
    ) -> tuple[PlanTask, ...]:
        """按生产策略与模型选择展开完整任务集合。

        :param selection: 任务规划 LLM 输出的固定字段选择结果。
        :param snapshot: 当前回合不可变 TurnSnapshot。
        :param envelopes: 系统生成的计划 envelope 集合。
        :return: 返回确定性排序前的不可变任务集合。
        """
        tasks: list[PlanTask] = []
        for rule in self.policy.skills:
            if rule.requirement == PlanSkillRequirementMode.FORBIDDEN:
                continue
            selected = self._is_rule_selected(rule.skill_id, selection)
            required = rule.requirement in {
                PlanSkillRequirementMode.ALWAYS,
                PlanSkillRequirementMode.WHEN_CLAIM_ENVELOPE_PRESENT,
            }
            if not selected and not required:
                continue
            target_envelopes = self._target_envelopes(
                target_kind=rule.target_envelope_kind,
                envelopes=envelopes,
            )
            for envelope in target_envelopes:
                tasks.append(
                    self._build_task(
                        spec=self.skill_specs[rule.skill_id],
                        envelope=envelope,
                        turn_id=snapshot.turn_id,
                        selection_source=(
                            PlanTaskSelectionSource.PLAN_POLICY
                            if required
                            else PlanTaskSelectionSource.PLAN_SELECTION
                        ),
                    ),
                )
        return tuple(tasks)

    def _is_rule_selected(
        self,
        skill_id: str,
        selection: PlanSelection,
    ) -> bool:
        """读取固定字段选择结果中对应 SKILL 的启用状态。

        :param skill_id: 生产策略中的 SKILL 稳定标识。
        :param selection: 任务规划 LLM 输出的固定字段选择结果。
        :return: 返回该 SKILL 是否被模型选择启用。
        """
        selected_fields = {
            "statement_semantics": selection.run_statement_semantics,
            "participant_phrase": selection.run_participant_phrase,
            "temporal_phrase": selection.run_temporal_phrase,
            "measurement_phrase": selection.run_measurement_phrase,
            "canonical_descriptor": selection.run_canonical_descriptor,
        }
        return selected_fields.get(skill_id, False)

    def _target_envelopes(
        self,
        *,
        target_kind: PlanEnvelopeKind,
        envelopes: tuple[PlanEnvelope, ...],
    ) -> tuple[PlanEnvelope, ...]:
        """按 SKILL 允许的 envelope 类型筛选执行范围。

        :param target_kind: 计划策略声明的目标 envelope 类型。
        :param envelopes: 当前计划全部 envelope。
        :return: 返回匹配类型的 envelope 集合。
        """
        return tuple(
            envelope
            for envelope in envelopes
            if envelope.kind == target_kind
        )

    def _build_task(
        self,
        *,
        spec: SkillSpec,
        envelope: PlanEnvelope,
        turn_id: str,
        selection_source: PlanTaskSelectionSource,
    ) -> PlanTask:
        """构造带稳定身份与权威 schema 引用的计划任务。

        :param spec: SkillCatalog 解析出的权威 SkillSpec。
        :param envelope: 任务绑定的计划 envelope。
        :param turn_id: 当前 TurnSnapshot 回合标识。
        :param selection_source: 任务产生来源。
        :return: 返回不可变计划任务。
        """
        return PlanTask(
            task_id=(
                f"{turn_id}:{envelope.envelope_id}:{spec.skill_id}:"
                f"{spec.skill_version}"
            ),
            skill_id=spec.skill_id,
            skill_version=spec.skill_version,
            target_envelope_id=envelope.envelope_id,
            depends_on=(),
            expected_output_schema=SchemaContractReference.from_contract(
                spec.output_contract,
            ),
            selection_source=selection_source,
        )

    def _build_dependencies(
        self,
        tasks: tuple[PlanTask, ...],
    ) -> tuple[PlanDependency, ...]:
        """根据 PlanPolicy 静态规则重写任务依赖边。

        :param tasks: 系统展开后的计划任务集合。
        :return: 返回按稳定键排序的 canonical 依赖边集合。
        """
        rules = {rule.skill_id: rule for rule in self.policy.skills}
        tasks_by_envelope = self._tasks_by_envelope(tasks)
        dependencies: list[PlanDependency] = []
        for task in tasks:
            rule = rules[task.skill_id]
            for dependency_rule in rule.depends_on:
                dependency_task = self._resolve_dependency_task(
                    task=task,
                    dependency_rule=dependency_rule,
                    tasks_by_envelope=tasks_by_envelope,
                )
                dependencies.append(
                    PlanDependency(
                        task_id=task.task_id,
                        depends_on_task_id=dependency_task.task_id,
                    ),
                )
        return tuple(
            sorted(
                dependencies,
                key=_dependency_sort_key,
            ),
        )

    def _tasks_by_envelope(
        self,
        tasks: tuple[PlanTask, ...],
    ) -> dict[str, dict[str, PlanTask]]:
        """构建 envelope、skill 到任务的确定性索引。

        :param tasks: 系统展开后的计划任务集合。
        :return: 返回用于依赖解析的二级只读映射副本。
        """
        index: dict[str, dict[str, PlanTask]] = {}
        for task in tasks:
            skill_tasks = index.setdefault(task.target_envelope_id, {})
            skill_tasks[task.skill_id] = task
        return index

    def _resolve_dependency_task(
        self,
        *,
        task: PlanTask,
        dependency_rule: PlanDependencyRule,
        tasks_by_envelope: dict[str, dict[str, PlanTask]],
    ) -> PlanTask:
        """按策略声明的 envelope 范围解析被依赖任务。

        :param task: 需要展开依赖的计划任务。
        :param dependency_rule: 生产策略中的依赖规则。
        :param tasks_by_envelope: envelope、skill 到任务的索引。
        :return: 返回被依赖的计划任务。
        :raises PlanCompilationError: 依赖任务缺失或范围非法时抛出。
        """
        if dependency_rule.dependency_scope.value == "root_envelope":
            dependency_envelope_id = "turn_root"
        elif dependency_rule.dependency_scope.value == "same_envelope":
            dependency_envelope_id = task.target_envelope_id
        else:
            raise PlanCompilationError(
                "unknown plan dependency scope",
                failure_code="plan_schema_invalid",
            )
        dependency_task = tasks_by_envelope.get(
            dependency_envelope_id,
            {},
        ).get(dependency_rule.dependency_skill_id)
        if dependency_task is None:
            raise PlanCompilationError(
                "plan policy dependency task cannot be resolved",
                failure_code="plan_schema_invalid",
            )
        return dependency_task

    def _apply_dependencies(
        self,
        tasks: tuple[PlanTask, ...],
        dependencies: tuple[PlanDependency, ...],
    ) -> tuple[PlanTask, ...]:
        """把 canonical 依赖边写回任务的 depends_on 权威字段。

        :param tasks: 尚未携带依赖的任务集合。
        :param dependencies: 已按稳定键排序的依赖边集合。
        :return: 返回携带依赖的任务集合。
        """
        dependency_ids = {
            dependency.task_id: tuple(
                edge.depends_on_task_id
                for edge in dependencies
                if edge.task_id == dependency.task_id
            )
            for dependency in dependencies
        }
        return tuple(
            task.model_copy(
                update={
                    "depends_on": dependency_ids.get(
                        task.task_id,
                        (),
                    ),
                },
            )
            for task in tasks
        )


def _dependency_sort_key(dependency: PlanDependency) -> tuple[str, str]:
    """读取计划依赖边的稳定排序键。

    :param dependency: 计划依赖边。
    :return: 返回任务标识与被依赖任务标识组成的元组。
    """
    return dependency.task_id, dependency.depends_on_task_id

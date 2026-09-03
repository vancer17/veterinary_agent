"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/plan_validator.py
作用：实现受限语义协作 DAG 的 M03 Plan Validator。
范围：覆盖计划与 TurnSnapshot / SkillCatalog / PlanPolicy 绑定校验、任务与
      envelope 结构校验、静态依赖与 DAG 无环校验、上下文策略校验、
      expected schema 校验以及 canonical plan 身份校验。
说明：本文件只输出 validated 或 blocked 显式终态，不修复计划、不补默认任务、
      不调用旧语义链路，也不把失败转换为空 facts 或空任务。
=============================================================================
"""

from __future__ import annotations

import hmac
from graphlib import CycleError, TopologicalSorter

from .catalog import SkillRegistry
from .contracts import (
    DOMAIN_ISOLATED_CONTEXT_RESOURCES,
    TURN_SNAPSHOT_CONTEXT_RESOURCES,
    SkillContextResource,
    SkillSpec,
)
from .errors import (
    TurnSnapshotBudgetExceededError,
    TurnSnapshotContextPolicyViolationError,
)
from .plan_contracts import (
    PlanDependencyRule,
    PlanDependencyScope,
    PlanEnvelopeKind,
    PlanIR,
    PlanPolicySpec,
    PlanSkillRequirementMode,
    PlanTask,
    PlanValidationFailure,
    PlanValidationFailureCode,
    PlanValidationResult,
    ValidatedPlan,
    compute_plan_policy_digest,
    schema_reference_matches,
)
from .snapshot import TurnSnapshotProjector
from .snapshot_contracts import TurnSnapshot


class PlanValidator:
    """对 CandidatePlanIR 执行生产调度前的全部准入门禁。

    :return: 无返回值；该对象不修改计划内容，也不产生任何回退路径。
    """

    failures: list[PlanValidationFailure]

    def __init__(
        self,
        *,
        registry: SkillRegistry,
        policy: PlanPolicySpec,
    ) -> None:
        """初始化绑定权威目录与生产策略的计划校验器。

        :param registry: 已冻结的 SkillCatalog 只读门面。
        :param policy: 初始 Turn Plan 生产策略。
        :return: 无返回值。
        """
        self.registry = registry
        self.policy = policy
        self.policy_rules = {rule.skill_id: rule for rule in policy.skills}
        self.policy_digest = compute_plan_policy_digest(policy)
        self.projector = TurnSnapshotProjector()
        self.failures = []

    def validate(
        self,
        plan: PlanIR,
        snapshot: TurnSnapshot,
    ) -> PlanValidationResult:
        """校验 CandidatePlanIR 是否可进入 M04 DAGScheduler。

        :param plan: 确定性编译产生的 CandidatePlanIR。
        :param snapshot: 当前回合不可变 TurnSnapshot。
        :return: 返回 validated 或 blocked 的显式终态结果。
        """
        self.failures: list[PlanValidationFailure] = []
        specs = self._validate_bindings(plan=plan, snapshot=snapshot)
        self._validate_structure(plan)
        self._validate_envelopes(plan)
        self._validate_tasks(plan=plan, specs=specs)
        self._validate_dependencies(plan)
        self._validate_context_policies(
            plan=plan,
            snapshot=snapshot,
            specs=specs,
        )
        self._validate_dag(plan)
        self._validate_identity(plan)
        failures = tuple(
            sorted(
                self.failures,
                key=_failure_sort_key,
            ),
        )
        if failures:
            return PlanValidationResult(
                validated_plan=None,
                failures=failures,
            )
        return PlanValidationResult(
            validated_plan=ValidatedPlan(plan=plan),
            failures=(),
        )

    def _validate_bindings(
        self,
        *,
        plan: PlanIR,
        snapshot: TurnSnapshot,
    ) -> dict[str, SkillSpec]:
        """校验计划绑定的回合、目录并解析权威 SkillSpec。

        :param plan: 待校验的 CandidatePlanIR。
        :param snapshot: 当前回合不可变 TurnSnapshot。
        :return: 返回 task_id 到权威 SkillSpec 的映射；解析失败的任务不进入映射。
        """
        if not hmac.compare_digest(plan.turn_id, snapshot.turn_id):
            self._add_failure(
                code=PlanValidationFailureCode.TURN_ID_MISMATCH,
                path="turn_id",
                message="plan turn id does not match turn snapshot",
            )
        if not hmac.compare_digest(plan.snapshot_digest, snapshot.context_digest):
            self._add_failure(
                code=PlanValidationFailureCode.SNAPSHOT_DIGEST_MISMATCH,
                path="snapshot_digest",
                message="plan snapshot digest does not match turn snapshot",
            )
        if not hmac.compare_digest(
            plan.skill_catalog_digest,
            self.registry.contract_digest(),
        ):
            self._add_failure(
                code=PlanValidationFailureCode.SKILL_CATALOG_DIGEST_MISMATCH,
                path="skill_catalog_digest",
                message="plan skill catalog digest does not match active catalog",
            )
        if not hmac.compare_digest(plan.plan_policy_digest, self.policy_digest):
            self._add_failure(
                code=PlanValidationFailureCode.PLAN_POLICY_VIOLATION,
                path="plan_policy_digest",
                message="plan policy digest does not match active policy",
            )
        specs: dict[str, SkillSpec] = {}
        for task in plan.tasks:
            exact_spec = self.registry.get(
                task.skill_id,
                task.skill_version,
            )
            if exact_spec is None:
                known_spec = self.registry.get(task.skill_id)
                self._add_failure(
                    code=(
                        PlanValidationFailureCode.UNKNOWN_SKILL_SELECTED
                        if known_spec is None
                        else PlanValidationFailureCode.SKILL_VERSION_INVALID
                    ),
                    path=f"tasks.{task.task_id}.skill_identity",
                    message="task skill identity is not registered in active catalog",
                    task_id=task.task_id,
                )
                continue
            specs[task.task_id] = exact_spec
        return specs

    def _validate_structure(self, plan: PlanIR) -> None:
        """校验计划基础结构、数量预算与标识唯一性。

        :param plan: 待校验的 CandidatePlanIR。
        :return: 无返回值。
        """
        if not plan.envelopes or not plan.tasks:
            self._add_failure(
                code=PlanValidationFailureCode.EMPTY_PLAN,
                path="plan",
                message="plan must contain at least one envelope and task",
            )
        if len(plan.tasks) > self.policy.max_task_count:
            self._add_failure(
                code=PlanValidationFailureCode.PLAN_BUDGET_EXCEEDED,
                path="tasks",
                message="plan task count exceeds policy budget",
            )
        envelope_ids = [envelope.envelope_id for envelope in plan.envelopes]
        if len(envelope_ids) != len(set(envelope_ids)):
            self._add_failure(
                code=PlanValidationFailureCode.DUPLICATE_ENVELOPE,
                path="envelopes",
                message="plan envelope id is duplicated",
            )
        task_ids = [task.task_id for task in plan.tasks]
        if len(task_ids) != len(set(task_ids)):
            self._add_failure(
                code=PlanValidationFailureCode.DUPLICATE_TASK_ID,
                path="tasks",
                message="plan task id is duplicated",
            )

    def _validate_envelopes(self, plan: PlanIR) -> None:
        """校验 envelope 类型、父级、顺序和任务绑定完整性。

        :param plan: 待校验的 CandidatePlanIR。
        :return: 无返回值。
        """
        envelope_ids = {envelope.envelope_id for envelope in plan.envelopes}
        root_envelopes = tuple(
            envelope
            for envelope in plan.envelopes
            if envelope.kind == PlanEnvelopeKind.TURN
        )
        if len(root_envelopes) != 1 or root_envelopes[0].envelope_id != "turn_root":
            self._add_failure(
                code=PlanValidationFailureCode.ENVELOPE_POLICY_VIOLATION,
                path="envelopes.turn_root",
                message="plan must contain exactly one turn_root envelope",
                envelope_id=(
                    root_envelopes[0].envelope_id
                    if len(root_envelopes) == 1
                    else None
                ),
            )
        claim_envelopes = tuple(
            envelope
            for envelope in plan.envelopes
            if envelope.kind == PlanEnvelopeKind.CLAIM
        )
        if claim_envelopes:
            self._add_failure(
                code=PlanValidationFailureCode.ENVELOPE_POLICY_VIOLATION,
                path="envelopes.claim",
                message="initial root plan cannot preallocate claim envelopes",
                envelope_id=claim_envelopes[0].envelope_id,
            )
        for envelope in claim_envelopes:
            if envelope.parent_envelope_id != "turn_root":
                self._add_failure(
                    code=PlanValidationFailureCode.ENVELOPE_POLICY_VIOLATION,
                    path=f"envelopes.{envelope.envelope_id}.parent_envelope_id",
                    message="claim envelope parent must be turn_root",
                    envelope_id=envelope.envelope_id,
                )
        expected_claim_ids = [
            f"claim_env_{ordinal:04d}"
            for ordinal in range(len(claim_envelopes))
        ]
        actual_claim_ids = [envelope.envelope_id for envelope in claim_envelopes]
        if actual_claim_ids != expected_claim_ids:
            self._add_failure(
                code=PlanValidationFailureCode.NON_CANONICAL_ORDER,
                path="envelopes.claim",
                message="claim envelope identifiers are not contiguous and canonical",
            )
        for task in plan.tasks:
            if task.target_envelope_id not in envelope_ids:
                self._add_failure(
                    code=PlanValidationFailureCode.ENVELOPE_NOT_FOUND,
                    path=f"tasks.{task.task_id}.target_envelope_id",
                    message="task target envelope does not exist",
                    task_id=task.task_id,
                )

    def _validate_tasks(
        self,
        *,
        plan: PlanIR,
        specs: dict[str, SkillSpec],
    ) -> None:
        """校验任务策略准入、目标类型、数量和输出 schema 引用。

        :param plan: 待校验的 CandidatePlanIR。
        :param specs: task_id 到权威 SkillSpec 的映射。
        :return: 无返回值。
        """
        envelopes_by_id = {
            envelope.envelope_id: envelope for envelope in plan.envelopes
        }
        tasks_by_skill: dict[str, list[PlanTask]] = {
            rule.skill_id: [] for rule in self.policy.skills
        }
        for task in plan.tasks:
            rule = self.policy_rules.get(task.skill_id)
            if rule is None:
                self._add_failure(
                    code=PlanValidationFailureCode.PLAN_POLICY_VIOLATION,
                    path=f"tasks.{task.task_id}.skill_id",
                    message="task skill is absent from active plan policy",
                    task_id=task.task_id,
                )
                continue
            if rule.requirement == PlanSkillRequirementMode.FORBIDDEN:
                self._add_failure(
                    code=PlanValidationFailureCode.FORBIDDEN_SKILL_SELECTED,
                    path=f"tasks.{task.task_id}.skill_id",
                    message="skill is forbidden in the initial turn plan",
                    task_id=task.task_id,
                )
            if task.skill_version != rule.skill_version:
                self._add_failure(
                    code=PlanValidationFailureCode.SKILL_VERSION_INVALID,
                    path=f"tasks.{task.task_id}.skill_version",
                    message="task skill version does not match active plan policy",
                    task_id=task.task_id,
                )
            envelope = envelopes_by_id.get(task.target_envelope_id)
            if envelope is not None and envelope.kind != rule.target_envelope_kind:
                self._add_failure(
                    code=PlanValidationFailureCode.ENVELOPE_POLICY_VIOLATION,
                    path=f"tasks.{task.task_id}.target_envelope_id",
                    message="task envelope kind does not match plan policy",
                    task_id=task.task_id,
                    envelope_id=envelope.envelope_id,
                )
            spec = specs.get(task.task_id)
            if spec is not None and not schema_reference_matches(
                task.expected_output_schema,
                spec.output_contract,
            ):
                self._add_failure(
                    code=PlanValidationFailureCode.OUTPUT_SCHEMA_MISMATCH,
                    path=f"tasks.{task.task_id}.expected_output_schema",
                    message="task output schema does not match SkillCatalog",
                    task_id=task.task_id,
                )
            if task.skill_id in tasks_by_skill:
                tasks_by_skill[task.skill_id].append(task)
        claim_envelope_count = sum(
            envelope.kind == PlanEnvelopeKind.CLAIM
            for envelope in plan.envelopes
        )
        for rule in self.policy.skills:
            task_count = len(tasks_by_skill[rule.skill_id])
            expected_count = self._expected_task_count(
                requirement=rule.requirement,
                claim_envelope_count=claim_envelope_count,
                actual_count=task_count,
            )
            if expected_count is not None and task_count != expected_count:
                failure_code = (
                    PlanValidationFailureCode.MANDATORY_TASK_MISSING
                    if task_count < expected_count
                    else PlanValidationFailureCode.PLAN_POLICY_VIOLATION
                )
                self._add_failure(
                    code=failure_code,
                    path=f"plan_policy.skills.{rule.skill_id}",
                    message="task count does not satisfy active plan policy",
                )

    def _expected_task_count(
        self,
        *,
        requirement: PlanSkillRequirementMode,
        claim_envelope_count: int,
        actual_count: int,
    ) -> int | None:
        """计算策略模式下允许的任务数量。

        :param requirement: SKILL 在生产策略中的选择模式。
        :param claim_envelope_count: 当前计划 claim envelope 数量。
        :param actual_count: 当前该 SKILL 的任务数量。
        :return: 返回唯一允许数量；可选模式返回合法数量集合时返回 None。
        """
        if requirement == PlanSkillRequirementMode.ALWAYS:
            return 1
        if requirement == PlanSkillRequirementMode.WHEN_CLAIM_ENVELOPE_PRESENT:
            return claim_envelope_count
        if requirement == PlanSkillRequirementMode.OPTIONAL:
            if actual_count not in {0, claim_envelope_count}:
                return actual_count + 1
            return None
        return 0

    def _validate_dependencies(self, plan: PlanIR) -> None:
        """校验任务依赖字段、canonical 依赖边与静态策略完全一致。

        :param plan: 待校验的 CandidatePlanIR。
        :return: 无返回值。
        """
        tasks_by_id = {task.task_id: task for task in plan.tasks}
        tasks_by_envelope = self._tasks_by_envelope(plan.tasks)
        declared_edges = {
            (dependency.task_id, dependency.depends_on_task_id)
            for dependency in plan.dependencies
        }
        if len(declared_edges) != len(plan.dependencies):
            self._add_failure(
                code=PlanValidationFailureCode.DUPLICATE_DEPENDENCY,
                path="dependencies",
                message="plan dependency edge is duplicated",
            )
        canonical_edges = sorted(declared_edges)
        if tuple(canonical_edges) != tuple(
            (dependency.task_id, dependency.depends_on_task_id)
            for dependency in plan.dependencies
        ):
            self._add_failure(
                code=PlanValidationFailureCode.NON_CANONICAL_ORDER,
                path="dependencies",
                message="plan dependencies are not canonically ordered",
            )
        task_edge_map: dict[str, set[str]] = {
            task.task_id: set(task.depends_on)
            for task in plan.tasks
        }
        for task in plan.tasks:
            if len(task.depends_on) != len(set(task.depends_on)):
                self._add_failure(
                    code=PlanValidationFailureCode.DUPLICATE_DEPENDENCY,
                    path=f"tasks.{task.task_id}.depends_on",
                    message="task dependency is duplicated",
                    task_id=task.task_id,
                )
            for dependency_id in task.depends_on:
                if dependency_id == task.task_id:
                    self._add_failure(
                        code=PlanValidationFailureCode.SELF_DEPENDENCY_DETECTED,
                        path=f"tasks.{task.task_id}.depends_on",
                        message="task cannot depend on itself",
                        task_id=task.task_id,
                    )
                elif dependency_id not in tasks_by_id:
                    self._add_failure(
                        code=PlanValidationFailureCode.DEPENDENCY_NOT_FOUND,
                        path=f"tasks.{task.task_id}.depends_on",
                        message="task dependency does not exist",
                        task_id=task.task_id,
                    )
        declared_task_edges = {
            (task_id, dependency_id)
            for task_id, dependencies in task_edge_map.items()
            for dependency_id in dependencies
        }
        if declared_edges != declared_task_edges:
            self._add_failure(
                code=PlanValidationFailureCode.PLAN_SCHEMA_INVALID,
                path="dependencies",
                message="task depends_on fields do not match declared dependencies",
            )
        for task in plan.tasks:
            expected_dependencies = self._expected_dependency_ids(
                task=task,
                tasks_by_envelope=tasks_by_envelope,
            )
            if expected_dependencies is None:
                continue
            if set(task.depends_on) != expected_dependencies:
                self._add_failure(
                    code=PlanValidationFailureCode.PLAN_POLICY_VIOLATION,
                    path=f"tasks.{task.task_id}.depends_on",
                    message="task dependencies do not match active plan policy",
                    task_id=task.task_id,
                )

    def _tasks_by_envelope(
        self,
        tasks: tuple[PlanTask, ...],
    ) -> dict[str, dict[str, PlanTask]]:
        """构建依赖校验使用的 envelope 与 SKILL 任务索引。

        :param tasks: 当前计划任务集合。
        :return: 返回 envelope_id、skill_id 到任务的映射。
        """
        index: dict[str, dict[str, PlanTask]] = {}
        for task in tasks:
            skill_tasks = index.setdefault(task.target_envelope_id, {})
            skill_tasks[task.skill_id] = task
        return index

    def _expected_dependency_ids(
        self,
        *,
        task: PlanTask,
        tasks_by_envelope: dict[str, dict[str, PlanTask]],
    ) -> set[str] | None:
        """按生产策略计算任务的期望依赖集合。

        :param task: 当前待校验任务。
        :param tasks_by_envelope: envelope、skill 到任务的索引。
        :return: 返回期望依赖集合；策略缺少任务规则时返回 None。
        """
        rule = self.policy_rules.get(task.skill_id)
        if rule is None:
            return None
        expected: set[str] = set()
        for dependency_rule in rule.depends_on:
            dependency_task = self._resolve_dependency_task(
                task=task,
                dependency_rule=dependency_rule,
                tasks_by_envelope=tasks_by_envelope,
            )
            if dependency_task is None:
                self._add_failure(
                    code=PlanValidationFailureCode.DEPENDENCY_NOT_FOUND,
                    path=f"tasks.{task.task_id}.depends_on",
                    message="plan policy dependency task is absent",
                    task_id=task.task_id,
                )
                continue
            expected.add(dependency_task.task_id)
        return expected

    def _resolve_dependency_task(
        self,
        *,
        task: PlanTask,
        dependency_rule: PlanDependencyRule,
        tasks_by_envelope: dict[str, dict[str, PlanTask]],
    ) -> PlanTask | None:
        """按依赖规则范围解析被依赖任务。

        :param task: 当前待校验任务。
        :param dependency_rule: 生产策略中的依赖规则。
        :param tasks_by_envelope: envelope、skill 到任务的索引。
        :return: 找到时返回被依赖任务，否则返回 None。
        """
        if dependency_rule.dependency_scope == PlanDependencyScope.ROOT_ENVELOPE:
            envelope_id = "turn_root"
        elif dependency_rule.dependency_scope == PlanDependencyScope.SAME_ENVELOPE:
            envelope_id = task.target_envelope_id
        else:
            return None
        return tasks_by_envelope.get(envelope_id, {}).get(
            dependency_rule.dependency_skill_id,
        )

    def _validate_context_policies(
        self,
        *,
        plan: PlanIR,
        snapshot: TurnSnapshot,
        specs: dict[str, SkillSpec],
    ) -> None:
        """校验任务上下文资源、领域隔离和 verified artifact 依赖。

        :param plan: 待校验的 CandidatePlanIR。
        :param snapshot: 当前回合不可变 TurnSnapshot。
        :param specs: task_id 到权威 SkillSpec 的映射。
        :return: 无返回值。
        """
        for task in plan.tasks:
            spec = specs.get(task.task_id)
            if spec is None:
                continue
            required_resources = set(spec.context_contract.required_resources)
            forbidden_intersection = required_resources & (
                DOMAIN_ISOLATED_CONTEXT_RESOURCES
            )
            if forbidden_intersection:
                self._add_failure(
                    code=PlanValidationFailureCode.CONTEXT_POLICY_VIOLATION,
                    path=f"tasks.{task.task_id}.context_policy",
                    message="task requires a domain-isolated resource",
                    task_id=task.task_id,
                )
            if spec.context_contract.requires_snapshot_digest and not (
                hmac.compare_digest(
                    plan.snapshot_digest,
                    snapshot.context_digest,
                )
            ):
                self._add_failure(
                    code=PlanValidationFailureCode.CONTEXT_POLICY_VIOLATION,
                    path=f"tasks.{task.task_id}.context_policy.snapshot_digest",
                    message="task context digest does not match active snapshot",
                    task_id=task.task_id,
                )
            unsupported_snapshot_resources = required_resources - (
                TURN_SNAPSHOT_CONTEXT_RESOURCES
            ) - {
                SkillContextResource.VERIFIED_PEER_ARTIFACT,
                SkillContextResource.VERIFIED_REVIEW_ARTIFACT,
                SkillContextResource.VERIFIED_PATCH_PROPOSAL,
                SkillContextResource.ARTIFACT_BASE_VERSION,
            }
            if unsupported_snapshot_resources:
                self._add_failure(
                    code=PlanValidationFailureCode.CONTEXT_POLICY_VIOLATION,
                    path=f"tasks.{task.task_id}.context_policy.required_resources",
                    message="task requires an unsupported scheduler resource",
                    task_id=task.task_id,
                )
            if (
                SkillContextResource.VERIFIED_PEER_ARTIFACT in required_resources
                and not task.depends_on
            ):
                self._add_failure(
                    code=PlanValidationFailureCode.CONTEXT_POLICY_VIOLATION,
                    path=f"tasks.{task.task_id}.context_policy.verified_peer",
                    message="verified peer artifact requires a plan dependency",
                    task_id=task.task_id,
                )
            if required_resources & {
                SkillContextResource.VERIFIED_REVIEW_ARTIFACT,
                SkillContextResource.VERIFIED_PATCH_PROPOSAL,
                SkillContextResource.ARTIFACT_BASE_VERSION,
            }:
                self._add_failure(
                    code=PlanValidationFailureCode.CONTEXT_POLICY_VIOLATION,
                    path=f"tasks.{task.task_id}.context_policy.artifact_lane",
                    message="review or patch artifact resource is invalid in initial plan",
                    task_id=task.task_id,
                )
            self._validate_snapshot_projection(
                task_id=task.task_id,
                snapshot=snapshot,
                spec=spec,
            )

    def _validate_snapshot_projection(
        self,
        *,
        task_id: str,
        snapshot: TurnSnapshot,
        spec: SkillSpec,
    ) -> None:
        """通过 M02 投影器执行上下文资源与预算准入。

        :param task_id: 当前任务标识。
        :param snapshot: 当前回合不可变 TurnSnapshot。
        :param spec: 当前任务对应的权威 SkillSpec。
        :return: 无返回值。
        """
        try:
            self.projector.project(
                snapshot,
                spec.context_contract,
            )
        except TurnSnapshotContextPolicyViolationError:
            self._add_failure(
                code=PlanValidationFailureCode.CONTEXT_POLICY_VIOLATION,
                path=f"tasks.{task_id}.context_policy.projection",
                message="turn snapshot projection violates context policy",
                task_id=task_id,
            )
        except TurnSnapshotBudgetExceededError:
            self._add_failure(
                code=PlanValidationFailureCode.PLAN_BUDGET_EXCEEDED,
                path=f"tasks.{task_id}.context_policy.budget",
                message="turn snapshot projection exceeds skill context budget",
                task_id=task_id,
            )

    def _validate_dag(self, plan: PlanIR) -> None:
        """校验计划依赖图无环且每个节点可拓扑解析。

        :param plan: 待校验的 CandidatePlanIR。
        :return: 无返回值。
        """
        graph = {
            task.task_id: set(task.depends_on)
            for task in plan.tasks
        }
        sorter = TopologicalSorter(graph)
        try:
            sorter.prepare()
        except CycleError:
            self._add_failure(
                code=PlanValidationFailureCode.DEPENDENCY_CYCLE_DETECTED,
                path="dependencies.graph",
                message="plan dependency graph contains a cycle",
            )

    def _validate_identity(self, plan: PlanIR) -> None:
        """校验 canonical plan 身份与权威计划内容一致。

        :param plan: 待校验的 CandidatePlanIR。
        :return: 无返回值。
        """
        expected_plan_id = plan.plan_digest()
        if not hmac.compare_digest(plan.plan_id, expected_plan_id):
            self._add_failure(
                code=PlanValidationFailureCode.PLAN_ID_INVALID,
                path="plan_id",
                message="plan id does not match canonical authoritative fields",
            )

    def _add_failure(
        self,
        *,
        code: PlanValidationFailureCode,
        path: str,
        message: str,
        task_id: str | None = None,
        envelope_id: str | None = None,
    ) -> None:
        """追加一条稳定计划准入失败记录。

        :param code: 稳定失败编码。
        :param path: 失败字段路径。
        :param message: 面向工程排障的技术说明。
        :param task_id: 关联任务标识。
        :param envelope_id: 关联 envelope 标识。
        :return: 无返回值。
        """
        self.failures.append(
            PlanValidationFailure(
                code=code,
                path=path,
                message=message,
                task_id=task_id,
                envelope_id=envelope_id,
            ),
        )


def _failure_sort_key(failure: PlanValidationFailure) -> tuple[str, str, str, str]:
    """读取计划失败记录的稳定排序键。

    :param failure: 计划准入失败记录。
    :return: 返回路径、失败码、任务与 envelope 组成的排序元组。
    """
    return (
        failure.path,
        failure.code.value,
        failure.task_id or "",
        failure.envelope_id or "",
    )

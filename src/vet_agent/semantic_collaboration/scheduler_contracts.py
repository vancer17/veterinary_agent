"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/scheduler_contracts.py
作用：定义受限语义协作 DAG M04 的 Temporal-first 调度契约。
范围：覆盖 workflow 执行策略、任务重试策略、任务端口请求与结果、
      语义终态以及 PostgreSQL 只读投影契约。
说明：本文件不定义任务队列、worker 租约、attempt 生命周期或数据库调度状态；
      durable 执行、重试、超时与中断恢复由 Temporal runtime 负责。
=============================================================================
"""

from __future__ import annotations

from enum import StrEnum
from hashlib import sha256
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import SkillFailureCode
from .plan_contracts import PlanTask, ValidatedPlan

DAG_SCHEDULER_CONTRACT_VERSION: Final = "1.0.0"


class DAGRunStatus(StrEnum):
    """表示语义协作 DAG workflow 的业务状态投影。

    :return: 无返回值；running 仅代表 workflow 尚未完成，不作为执行队列状态。
    """

    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    CANCELED = "canceled"
    TIMED_OUT = "timed_out"
    FAILED = "failed"

    def is_terminal(self) -> bool:
        """判断当前业务状态是否已经终态。

        :return: 除 running 外均返回 True。
        """
        return self is not DAGRunStatus.RUNNING


class DAGTaskTerminalState(StrEnum):
    """表示单个语义任务的权威业务终态。

    :return: 无返回值；该枚举只描述语义结果，不描述基础设施调度状态。
    """

    VERIFIED = "verified"
    REPAIR_VERIFIED = "repair_verified"
    NOT_APPLICABLE = "not_applicable"
    BLOCKED = "blocked"
    DISAGREEMENT = "disagreement"
    REPAIR_EXHAUSTED = "repair_exhausted"
    REPAIR_FAILED = "repair_failed"
    DEPENDENCY_FAILED = "dependency_failed"
    REVIEW_FAILED = "review_failed"
    CONTEXT_BUDGET_EXCEEDED = "context_budget_exceeded"
    TIMEOUT = "timeout"

    def is_dependency_success(self) -> bool:
        """判断该终态是否允许下游任务继续执行。

        :return: verified、repair_verified 或 not_applicable 返回 True。
        """
        return self in {
            DAGTaskTerminalState.VERIFIED,
            DAGTaskTerminalState.REPAIR_VERIFIED,
            DAGTaskTerminalState.NOT_APPLICABLE,
        }

    def requires_artifact(self) -> bool:
        """判断该终态是否必须携带已验证 artifact 引用。

        :return: verified 或 repair_verified 返回 True。
        """
        return self in {
            DAGTaskTerminalState.VERIFIED,
            DAGTaskTerminalState.REPAIR_VERIFIED,
        }


class DAGExecutionPolicy(BaseModel):
    """表示一次 Temporal workflow 的固化执行策略。

    :return: 无返回值；策略作为 workflow 输入进入 event history。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    policy_id: str = Field(
        default="semantic-collaboration-dag-temporal-v1",
        min_length=1,
        max_length=160,
        description="调度策略稳定标识，用于审计和 workflow 输入绑定。",
    )
    max_concurrency: int = Field(
        default=8,
        ge=1,
        le=32,
        description="同一 workflow 内允许同时执行的语义任务数量。",
    )
    task_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        le=180.0,
        description="单个语义任务的 activity 超时时间。",
    )
    run_timeout_seconds: float = Field(
        default=120.0,
        gt=0.0,
        le=600.0,
        description="整个语义协作 DAG workflow 的运行超时时间。",
    )
    infrastructure_retry_initial_interval_seconds: float = Field(
        default=1.0,
        gt=0.0,
        le=30.0,
        description="Temporal 基础设施异常重试的初始间隔。",
    )
    infrastructure_retry_max_interval_seconds: float = Field(
        default=10.0,
        gt=0.0,
        le=60.0,
        description="Temporal 基础设施异常重试的最大间隔。",
    )
    semantic_retry_backoff_seconds: float = Field(
        default=0.0,
        ge=0.0,
        le=30.0,
        description="语义侧可重试失败在 workflow timer 中的等待时间。",
    )

    @model_validator(mode="after")
    def validate_retry_intervals(self) -> Self:
        """校验基础设施重试间隔的下限与上限。

        :return: 返回通过一致性校验的策略。
        :raises ValueError: 最大间隔小于初始间隔时抛出。
        """
        if (
            self.infrastructure_retry_max_interval_seconds
            < self.infrastructure_retry_initial_interval_seconds
        ):
            raise ValueError("infrastructure retry max interval is below initial interval")
        return self


class DAGTaskPolicy(BaseModel):
    """表示由 SkillFailurePolicy 投影出的单任务语义重试策略。

    :return: 无返回值；该策略由 Temporal workflow 执行，不落库为队列状态。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task_id: str = Field(
        min_length=1,
        max_length=360,
        description="策略绑定的权威 PlanTask 标识。",
    )
    max_attempts: int = Field(
        ge=1,
        le=3,
        description="语义结果允许的最大执行次数。",
    )
    retryable_failure_codes: tuple[SkillFailureCode, ...] = Field(
        description="允许由 workflow 有界重试的稳定失败码集合。",
    )

    @model_validator(mode="after")
    def validate_retry_codes(self) -> Self:
        """校验任务策略没有重复失败码。

        :return: 返回通过闭合校验的任务策略。
        :raises ValueError: 失败码重复时抛出。
        """
        codes = list(self.retryable_failure_codes)
        if len(codes) != len(set(codes)):
            raise ValueError("duplicate retryable failure code")
        return self


class SemanticTaskExecutionRequest(BaseModel):
    """表示 Temporal activity 传给任务执行端口的固定输入。

    :return: 无返回值；请求不携带未验证同伴输出或下游领域状态。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run_id: str = Field(
        min_length=1,
        max_length=64,
        description="当前语义协作 DAG workflow 稳定标识。",
    )
    attempt_number: int = Field(
        ge=1,
        le=3,
        description="workflow 内的语义尝试编号。",
    )
    task: PlanTask = Field(description="待执行的权威 PlanTask。")
    turn_snapshot_digest: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="任务必须绑定的 TurnSnapshot digest。",
    )
    dependency_artifacts: dict[str, str] = Field(
        description="直接上游成功任务的已验证 artifact 引用映射。",
    )


class DAGTaskExecutionResult(BaseModel):
    """表示任务执行端口返回的显式业务结果。

    :return: 无返回值；成功结果必须携带 artifact 引用。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task_id: str = Field(
        min_length=1,
        max_length=360,
        description="结果对应的权威 PlanTask 标识。",
    )
    terminal_state: DAGTaskTerminalState = Field(
        description="执行端口与 verifier 共同确认的任务终态。",
    )
    artifact_reference: str | None = Field(
        default=None,
        max_length=512,
        description="已验证 artifact 引用；失败终态必须为空。",
    )
    failure_code: SkillFailureCode | None = Field(
        default=None,
        description="失败终态对应的稳定 SKILL 失败码。",
    )
    failure_message: str | None = Field(
        default=None,
        max_length=1000,
        description="面向工程排障的失败说明，不包含用户原文。",
    )

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> Self:
        """校验任务终态与 artifact、失败码负载的一致性。

        :return: 返回通过终态负载校验的结果。
        :raises ValueError: 成功缺 artifact、失败携带 artifact 或失败缺原因时抛出。
        """
        if self.terminal_state.requires_artifact():
            if not self.artifact_reference:
                raise ValueError("verified terminal state requires artifact")
            if self.failure_code is not None or self.failure_message is not None:
                raise ValueError("verified terminal state cannot carry failure")
            return self
        if self.terminal_state.is_dependency_success():
            if self.artifact_reference is not None:
                raise ValueError("not_applicable cannot carry artifact")
            if self.failure_code is not None or self.failure_message is not None:
                raise ValueError("not_applicable cannot carry failure")
            return self
        if self.artifact_reference is not None:
            raise ValueError("failure terminal state cannot carry artifact")
        if self.failure_code is None or not self.failure_message:
            raise ValueError("failure terminal state requires code and message")
        return self


class DAGTaskProjectionRecord(BaseModel):
    """表示单个语义任务的只读投影记录。

    :return: 无返回值；投影不参与调度，终态由 Temporal activity 写入。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task_id: str = Field(
        min_length=1,
        max_length=360,
        description="权威 PlanTask 标识。",
    )
    skill_id: str = Field(
        min_length=1,
        max_length=120,
        description="任务绑定的 SKILL 标识。",
    )
    skill_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$",
        description="任务绑定的精确 SKILL 版本。",
    )
    target_envelope_id: str = Field(
        min_length=1,
        max_length=240,
        description="任务绑定的 turn 或 claim envelope 标识。",
    )
    terminal_state: DAGTaskTerminalState | None = Field(
        default=None,
        description="任务业务终态；workflow 未完成时可以为空。",
    )
    artifact_reference: str | None = Field(
        default=None,
        max_length=512,
        description="成功终态绑定的已验证 artifact 引用。",
    )
    failure_code: SkillFailureCode | None = Field(
        default=None,
        description="失败终态稳定失败码。",
    )
    failure_message: str | None = Field(
        default=None,
        max_length=1000,
        description="失败终态工程排障说明。",
    )


class DAGRunProjectionRecord(BaseModel):
    """表示一个语义协作 DAG workflow 的只读投影。

    :return: 无返回值；执行历史权威在 Temporal，本对象仅服务查询和审计。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run_id: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="由权威 Plan IR 派生的 workflow 稳定标识。",
    )
    contract_version: str = Field(
        default=DAG_SCHEDULER_CONTRACT_VERSION,
        description="DAG 调度契约版本。",
    )
    workflow_id: str = Field(
        min_length=1,
        max_length=160,
        description="Temporal workflow 标识。",
    )
    plan_id: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="Plan IR canonical digest。",
    )
    turn_id: str = Field(
        min_length=1,
        max_length=240,
        description="当前计划绑定的 TurnSnapshot 回合标识。",
    )
    snapshot_digest: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="当前计划绑定的 TurnSnapshot digest。",
    )
    skill_catalog_digest: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="创建计划时冻结的 SkillCatalog digest。",
    )
    plan_policy_digest: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="创建计划时冻结的 PlanPolicy digest。",
    )
    status: DAGRunStatus = Field(
        description="workflow 业务状态投影。",
    )
    tasks: tuple[DAGTaskProjectionRecord, ...] = Field(
        description="任务终态投影集合。",
    )

    @model_validator(mode="after")
    def validate_terminal_projection(self) -> Self:
        """校验终态 run 投影的任务闭合性。

        :return: 返回通过闭合校验的 run 投影。
        :raises ValueError: 终态 run 仍有未终态任务时抛出。
        """
        if self.status.is_terminal() and any(
            task.terminal_state is None
            for task in self.tasks
        ):
            raise ValueError("terminal run projection requires terminal tasks")
        return self


class DAGRunProjectionInitializeRequest(BaseModel):
    """表示初始化 DAG 只读投影的幂等请求。

    :return: 无返回值；该请求不领取租约，也不创建可执行任务队列。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run_id: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="DAG workflow 稳定标识。",
    )
    workflow_id: str = Field(
        min_length=1,
        max_length=160,
        description="Temporal workflow 标识。",
    )
    validated_plan: ValidatedPlan = Field(
        description="已通过 M03 校验的权威计划。",
    )
    policy: DAGExecutionPolicy = Field(
        description="当前 workflow 固化执行策略。",
    )
    task_policies: tuple[DAGTaskPolicy, ...] = Field(
        description="由 SkillCatalog 投影出的任务策略集合。",
    )

    @model_validator(mode="after")
    def validate_task_policies(self) -> Self:
        """校验任务策略与 Plan IR 任务集合完全一致。

        :return: 返回通过闭合校验的初始化请求。
        :raises ValueError: 策略缺失、重复或任务为空时抛出。
        """
        task_ids = {task.task_id for task in self.validated_plan.plan.tasks}
        policy_ids = [policy.task_id for policy in self.task_policies]
        if not task_ids:
            raise ValueError("semantic dag plan is empty")
        if len(policy_ids) != len(set(policy_ids)):
            raise ValueError("duplicate dag task policy")
        if task_ids != set(policy_ids):
            raise ValueError("dag task policy set does not match plan tasks")
        return self


def semantic_dag_run_id(plan_id: str) -> str:
    """根据权威 Plan IR 身份派生稳定 workflow 标识。

    :param plan_id: 已通过 M03 校验的 Plan IR canonical digest。
    :return: 返回 64 位小写 SHA-256 workflow 标识。
    """
    return sha256(
        f"semantic-collaboration-dag-temporal\0{plan_id}".encode(),
    ).hexdigest()

# =============================================================================
# 文件: src/vet_agent/core/repair/scheduler.py
# 作用: 实现 Targeted Repair 的版本化 prompt 调用、受控并发调度、统一输出
#       收集、严格 schema 包装与事务式失败边界。
# 范围: 覆盖 prompt 绑定解析、安全渲染、LLMGateway 调用、语义层 validator
#       调用、输出摘要与计划顺序保持。
# 说明: 本文件不定义修复动作，不应用 patch，不做修复后 Review，不按缺陷码
#       推导语义，也不允许任一失败被转换为空修复结果。
# TODO: repair_scheduled / completed / failed 专用审计事件待 core.execution
#       的通用 AuditEvent 端口落地后接入；当前模型调用仍经 LLMGateway 审计。
# =============================================================================
"""Targeted Repair 执行调度器。

该模块位于 PromptRegistry / PromptRenderer / LLMGateway 之后、语义层确定性
patch 之前。它保证同一轮修复任务全部有效后才返回结果集合。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TypeVar

from vet_agent.core import errors as core_errors
from vet_agent.core import llm as core_llm
from vet_agent.core import prompts as core_prompts
from vet_agent.core.execution import ExecutionContext
from vet_agent.core.repair.contracts import (
    RepairOutputValidator,
    RepairPlanSpec,
    RepairTask,
    RepairTaskResult,
)
from vet_agent.core.schema import StrictModel

T = TypeVar("T", bound=StrictModel)


def _output_digest(output: StrictModel) -> str:
    """计算 typed 修复输出的确定性摘要。

    该函数位于语义层 validator 之后，用于审计和结果引用；摘要不包含原始
    prompt、用户输入或完整模型输出文本。

    :param output: 已通过严格 schema 与语义校验的 typed 输出。
    :return: canonical JSON 字节的 SHA-256 十六进制摘要。
    """
    canonical_json = output.model_dump_json(by_alias=True)
    return hashlib.sha256(canonical_json.encode(encoding="utf-8")).hexdigest()


def _repair_schema_error(
    *,
    task: RepairTask[StrictModel],
    reason: str,
    cause: BaseException,
) -> core_errors.RepairSchemaError:
    """构造修复输出结构失败的公共错误信封。

    该函数只包装 Strict JSON 或 strict schema 失败，不触发第二次模型调用，
    也不尝试宽松修复 JSON。

    :param task: 发生结构失败的修复任务。
    :param reason: 稳定的工程失败原因码。
    :param cause: Strict JSON Gateway 或模型网关抛出的底层异常。
    :return: 可向上传播的修复 schema 异常。
    """
    return core_errors.RepairSchemaError(
        error_code=core_errors.CoreErrorCode.REPAIR_SCHEMA_INVALID,
        message="Repair 输出未通过严格结构校验",
        context={
            "repair_task_id": task.repair_task_id,
            "prompt_id": task.prompt_id,
            "reasons": reason,
        },
        cause=cause,
    )


@dataclass(frozen=True, slots=True)
class TargetedRepairScheduler[T: StrictModel]:
    """按计划执行 Targeted Repair 并事务式收集结果。

    调度器是 PromptRegistry、受限渲染器和唯一模型入口的组合协调者；语义层
    通过注入 ``RepairOutputValidator`` 保留各自领域的确定性输出边界。
    """

    prompt_registry: core_prompts.PromptRegistry
    prompt_renderer: core_prompts.DefaultPromptRenderer
    llm_gateway: core_llm.LLMGateway

    async def run(
        self,
        *,
        plan: RepairPlanSpec[T],
        render_values_by_task: Mapping[
            str,
            Mapping[str, core_prompts.PromptRenderValue],
        ],
        output_validator: RepairOutputValidator[T],
        context: ExecutionContext,
    ) -> tuple[RepairTaskResult[T], ...]:
        """执行完整 Repair Plan 并按计划顺序返回全部有效结果。

        :param plan: 已通过启动期闭合校验的 target 级修复计划。
        :param render_values_by_task: 修复任务标识到 prompt 渲染变量的映射。
        :param output_validator: 语义层提供的确定性修复输出校验器。
        :param context: 当前执行上下文。
        :return: 与计划任务顺序一致的修复结果集合。
        :raises vet_agent.core.errors.RepairPlanError: 当渲染变量映射不闭合。
        :raises vet_agent.core.errors.ConfigurationError: 当 prompt 绑定不闭合。
        :raises vet_agent.core.errors.RepairSchemaError: 当任一修复输出未通过
            Strict JSON 或 strict schema 校验。
        :raises vet_agent.core.errors.CoreError: 当模型传输、协议、预算或语义层
            validator 显式失败。
        """
        self._validate_render_values(
            plan=plan,
            render_values_by_task=render_values_by_task,
        )
        semaphore = asyncio.Semaphore(plan.max_concurrency)
        executions = [
            self._execute_task(
                task=task,
                render_values=render_values_by_task[task.repair_task_id],
                output_validator=output_validator,
                context=context,
                semaphore=semaphore,
            )
            for task in plan.tasks
        ]
        outcomes = await asyncio.gather(*executions, return_exceptions=True)

        for task, outcome in zip(plan.tasks, outcomes, strict=True):
            if isinstance(outcome, BaseExceptionGroup):
                error = core_errors.RepairFailedError(
                    error_code=core_errors.CoreErrorCode.REPAIR_FAILED,
                    message="Repair 执行产生未分类异常组",
                    context={
                        "repair_task_id": task.repair_task_id,
                        "reasons": "unexpected_exception_group",
                    },
                )
                raise error from outcome
            if isinstance(outcome, BaseException):
                raise outcome
        return tuple(
            outcome
            for outcome in outcomes
            if not isinstance(outcome, BaseException)
        )

    def _validate_render_values(
        self,
        *,
        plan: RepairPlanSpec[T],
        render_values_by_task: Mapping[
            str,
            Mapping[str, core_prompts.PromptRenderValue],
        ],
    ) -> None:
        """校验修复任务与渲染变量映射的键闭合性。

        :param plan: 当前待执行的修复计划。
        :param render_values_by_task: 任务标识到渲染变量集合的映射。
        :return: 无返回值；变量映射闭合时允许渲染 prompt。
        :raises vet_agent.core.errors.RepairPlanError: 当缺少任务变量或存在
            未声明任务的悬空变量。
        """
        expected_task_ids = {task.repair_task_id for task in plan.tasks}
        actual_task_ids = set(render_values_by_task)
        invalid_reasons: list[str] = []
        if expected_task_ids - actual_task_ids:
            invalid_reasons.append("render_values_task_missing")
        if actual_task_ids - expected_task_ids:
            invalid_reasons.append("render_values_task_extra")
        if invalid_reasons:
            raise core_errors.RepairPlanError(
                error_code=core_errors.CoreErrorCode.REPAIR_PLAN_INVALID,
                message="Repair 渲染变量映射非法",
                context={"reasons": ",".join(invalid_reasons)},
            )

    async def _execute_task(
        self,
        *,
        task: RepairTask[T],
        render_values: Mapping[str, core_prompts.PromptRenderValue],
        output_validator: RepairOutputValidator[T],
        context: ExecutionContext,
        semaphore: asyncio.Semaphore,
    ) -> RepairTaskResult[T]:
        """执行一个修复任务并返回已验证的候选输出。

        :param task: 当前 target 级修复任务。
        :param render_values: 该任务 prompt 声明的动态渲染变量。
        :param output_validator: 语义层提供的确定性输出校验器。
        :param context: 当前执行上下文。
        :param semaphore: 控制物理模型调用并发的信号量。
        :return: 该任务的修复结果与模型执行 metadata。
        :raises vet_agent.core.errors.ConfigurationError: 当 prompt 或 schema
            绑定不一致。
        :raises vet_agent.core.errors.RepairPlanError: 当模型角色绑定非法。
        :raises vet_agent.core.errors.RepairSchemaError: 当修复输出不是严格 JSON
            或未通过 strict schema。
        :raises vet_agent.core.errors.CoreError: 当模型调用或语义层 validator
            显式失败。
        """
        async with semaphore:
            binding = self.prompt_registry.get(
                prompt_id=task.prompt_id,
                prompt_version=task.prompt_version,
            )
            role = self._resolve_model_role(
                task=task,
                model_role_name=binding.record.model_role,
            )
            rendered_prompt = self.prompt_renderer.render(
                binding=binding,
                values=render_values,
            )
            core_prompts.validate_prompt_binding(
                model_role=role.value,
                output_schema=task.output_schema,
                prompt=rendered_prompt,
            )
            repair_context = replace(
                context,
                step=f"{context.step}.repair.{task.repair_task_id}",
            )
            try:
                response = await self.llm_gateway.complete_json(
                    role=role,
                    prompt=rendered_prompt,
                    output_schema=task.output_schema,
                    context=repair_context,
                )
            except (
                core_errors.StrictJSONValidationError,
                core_errors.SchemaValidationError,
            ) as exc:
                raise _repair_schema_error(
                    task=task,
                    reason="schema_validation_failed",
                    cause=exc,
                ) from exc
            output_validator.validate(
                task=task,
                output=response.output,
            )
            return RepairTaskResult(
                repair_task_id=task.repair_task_id,
                target=task.target,
                target_kind=task.target_kind,
                output=response.output,
                llm_metadata=response.metadata,
                output_digest=_output_digest(output=response.output),
            )

    def _resolve_model_role(
        self,
        *,
        task: RepairTask[T],
        model_role_name: str,
    ) -> core_llm.ModelRole:
        """解析 prompt 绑定的稳定模型角色。

        :param task: 当前修复任务，用于错误定位。
        :param model_role_name: prompt record 中绑定的模型角色名。
        :return: 可用于唯一模型入口的稳定模型角色。
        :raises vet_agent.core.errors.RepairPlanError: 当角色名不属于
            ``ModelRole`` 架构枚举。
        """
        try:
            return core_llm.ModelRole(model_role_name)
        except ValueError as exc:
            raise core_errors.RepairPlanError(
                error_code=core_errors.CoreErrorCode.REPAIR_PLAN_INVALID,
                message="Repair prompt 绑定了未知模型角色",
                context={
                    "repair_task_id": task.repair_task_id,
                    "model_role": model_role_name,
                },
                cause=exc,
            ) from exc


__all__ = [
    "TargetedRepairScheduler",
]

# =============================================================================
# 文件: src/vet_agent/core/review/group_runner.py
# 作用: 实现 Review Group 的版本化 prompt 调用与 group 专属闭合校验。
# 范围: 覆盖 prompt binding 解析、安全渲染、LLMGateway 调用、稀疏 Delta
#       提取、allowlist / target 校验与批量 group 的 fail-fast 编排。
# 说明: 本文件不定义 Review prompt 文案，不理解缺陷码含义，不执行语义
#       修复，也不允许单 group 失败被转换为空 Delta 或 partial success。
# =============================================================================
"""
Grouped Review 执行器。

该模块位于 prompt registry 与唯一模型入口之后、Delta 合并之前。它把一个
Review Group 的模型输出转换成受 target allowlist 约束的稀疏 Delta，并在
结构违反 group 协议时显式进入 ``review_group_schema_invalid``。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace

from vet_agent.core import errors as core_errors
from vet_agent.core import llm as core_llm
from vet_agent.core import prompts as core_prompts
from vet_agent.core.execution import ExecutionContext
from vet_agent.core.review.contracts import (
    ReviewDeltaEntry,
    ReviewGroupResult,
    ReviewGroupSpec,
    ReviewPlanSpec,
    SparseReviewDelta,
)


def _review_group_schema_error(
    *,
    group_id: str,
    prompt_id: str,
    reason: str,
    cause: BaseException | None = None,
) -> core_errors.ReviewGroupSchemaError:
    """构造带脱敏定位信息的 Review Group 协议错误。

    :param group_id: 发生协议错误的 group 标识。
    :param prompt_id: 该 group 绑定的 prompt 标识。
    :param reason: 稳定的工程失败原因码。
    :param cause: 可选底层异常，用于保留调用栈。
    :return: 可向上传播的 Review Group 协议异常。
    """
    return core_errors.ReviewGroupSchemaError(
        error_code=core_errors.CoreErrorCode.REVIEW_GROUP_SCHEMA_INVALID,
        message="Review Group 输出不符合协议",
        context={
            "group_id": group_id,
            "prompt_id": prompt_id,
            "reasons": reason,
        },
        cause=cause,
    )


@dataclass(frozen=True, slots=True)
class ReviewGroupRunner:
    """执行单个 Review Group 的受控模型调用器。

    :ivar prompt_registry: exact version prompt 绑定注册表。
    :ivar prompt_renderer: 受限 prompt 渲染器。
    :ivar llm_gateway: 唯一严格 JSON 模型调用入口。
    """

    prompt_registry: core_prompts.PromptRegistry
    prompt_renderer: core_prompts.DefaultPromptRenderer
    llm_gateway: core_llm.LLMGateway

    async def run(
        self,
        *,
        spec: ReviewGroupSpec,
        render_values: Mapping[str, core_prompts.PromptRenderValue],
        context: ExecutionContext,
    ) -> ReviewGroupResult:
        """执行一个 Review Group 并返回稀疏 Delta。

        :param spec: 已通过启动期校验的 group 配置。
        :param render_values: 该 group prompt 声明的动态渲染变量。
        :param context: 当前执行上下文。
        :return: group 执行结果与模型调用 metadata。
        :raises core_errors.ConfigurationError: 当 prompt、schema 或渲染配置
            不一致。
        :raises core_errors.ReviewGroupSchemaError: 当模型输出包含空数组、
            ``null``、未知 target、重复 target 或 schema 校验失败。
        :raises core_errors.CoreError: 当模型传输、协议、预算或审计失败。
        """
        binding = self.prompt_registry.get(
            prompt_id=spec.prompt_id,
            prompt_version=spec.prompt_version,
        )
        role = self._resolve_model_role(spec=spec, binding=binding)
        rendered_prompt = self.prompt_renderer.render(
            binding=binding,
            values=render_values,
        )
        core_prompts.validate_prompt_binding(
            model_role=role.value,
            output_schema=spec.output_schema,
            prompt=rendered_prompt,
        )
        group_context = replace(
            context,
            step=f"{context.step}.review.{spec.group_id}",
        )
        try:
            response = await self.llm_gateway.complete_json(
                role=role,
                prompt=rendered_prompt,
                output_schema=spec.output_schema,
                context=group_context,
            )
        except core_errors.SchemaValidationError as exc:
            raise _review_group_schema_error(
                group_id=spec.group_id,
                prompt_id=spec.prompt_id,
                reason="schema_validation_failed",
                cause=exc,
            ) from exc
        delta = self._extract_delta(
            output=response.output,
            spec=spec,
        )
        return ReviewGroupResult(
            group_id=spec.group_id,
            delta=delta,
            llm_metadata=response.metadata,
        )

    def _resolve_model_role(
        self,
        *,
        spec: ReviewGroupSpec,
        binding: core_prompts.PromptBinding,
    ) -> core_llm.ModelRole:
        """解析 prompt 绑定的稳定模型角色。

        :param spec: 当前 Review Group 配置。
        :param binding: registry 返回的 exact version 绑定。
        :return: 可进入统一模型网关的稳定角色。
        :raises core_errors.ConfigurationError: 当 prompt、schema 或角色
            绑定与 group 配置不一致。
        """
        invalid_reasons: list[str] = []
        if binding.record.prompt_id != spec.prompt_id:
            invalid_reasons.append("prompt_id_mismatch")
        if binding.record.prompt_version != spec.prompt_version:
            invalid_reasons.append("prompt_version_mismatch")
        if binding.record.output_schema is not spec.output_schema:
            invalid_reasons.append("output_schema_mismatch")
        if invalid_reasons:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Review Group prompt 绑定不一致",
                context={
                    "group_id": spec.group_id,
                    "reasons": ",".join(invalid_reasons),
                },
            )
        try:
            return core_llm.ModelRole(binding.record.model_role)
        except ValueError as exc:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Review Group prompt 绑定了未知模型角色",
                context={
                    "group_id": spec.group_id,
                    "model_role": binding.record.model_role,
                },
                cause=exc,
            ) from exc

    def _extract_delta(
        self,
        *,
        output: SparseReviewDelta,
        spec: ReviewGroupSpec,
    ) -> tuple[ReviewDeltaEntry, ...]:
        """从 typed 输出中提取受 allowlist 约束的稀疏 Delta。

        :param output: 已通过 strict schema 校验的模型输出。
        :param spec: 当前 Review Group 配置。
        :return: 按 group 声明顺序与 target 快照顺序排序的 Delta 条目。
        :raises core_errors.ReviewGroupSchemaError: 当字段值为空、重复、
            非列表或引用未授权 target。
        """
        entries: list[ReviewDeltaEntry] = []
        allowed_targets = {
            target.selector: target.kind
            for target in spec.allowed_targets
        }
        fields_set = output.model_fields_set
        for defect_code in spec.allowed_defect_codes:
            if defect_code not in fields_set:
                continue
            value = getattr(output, defect_code)
            if not isinstance(value, list):
                raise _review_group_schema_error(
                    group_id=spec.group_id,
                    prompt_id=spec.prompt_id,
                    reason="target_array_invalid",
                )
            if not value or len(value) != len(set(value)):
                raise _review_group_schema_error(
                    group_id=spec.group_id,
                    prompt_id=spec.prompt_id,
                    reason="target_array_empty_or_duplicated",
                )
            if any(not isinstance(selector, str) for selector in value):
                raise _review_group_schema_error(
                    group_id=spec.group_id,
                    prompt_id=spec.prompt_id,
                    reason="target_selector_not_string",
                )
            if any(selector not in allowed_targets for selector in value):
                raise _review_group_schema_error(
                    group_id=spec.group_id,
                    prompt_id=spec.prompt_id,
                    reason="target_not_allowed",
                )
            if any(
                allowed_targets[selector] is not spec.target_kind
                for selector in value
            ):
                raise _review_group_schema_error(
                    group_id=spec.group_id,
                    prompt_id=spec.prompt_id,
                    reason="target_kind_mismatch",
                )
            selected = set(value)
            ordered_targets: list[str] = []
            for target in spec.allowed_targets:
                if target.selector in selected:
                    ordered_targets.append(target.selector)
            entries.append(
                ReviewDeltaEntry(
                    defect_code=defect_code,
                    targets=tuple(ordered_targets),
                ),
            )
        return tuple(entries)


@dataclass(frozen=True, slots=True)
class GroupedReviewRunner:
    """按计划并发执行多个 Review Group 的编排器。

    :ivar group_runner: 单 group 执行器。
    """

    group_runner: ReviewGroupRunner

    async def run(
        self,
        *,
        plan: ReviewPlanSpec,
        render_values_by_group: Mapping[
            str,
            Mapping[str, core_prompts.PromptRenderValue],
        ],
        context: ExecutionContext,
    ) -> tuple[ReviewGroupResult, ...]:
        """执行完整 Review Plan 并保持计划顺序返回结果。

        :param plan: 已通过启动期校验的 Review Plan。
        :param render_values_by_group: group 标识到渲染变量的映射。
        :param context: 当前执行上下文。
        :return: 与 plan 顺序一致的 group 执行结果集合。
        :raises core_errors.ConfigurationError: 当渲染变量缺少或多余。
        :raises BaseExceptionGroup: 当任一 group 失败时，TaskGroup 保留原始
            工程异常的异常组。
        """
        self._validate_render_values(
            plan=plan,
            render_values_by_group=render_values_by_group,
        )
        tasks: list[asyncio.Task[ReviewGroupResult]] = []
        async with asyncio.TaskGroup() as task_group:
            for group in plan.groups:
                task = task_group.create_task(
                    self.group_runner.run(
                        spec=group,
                        render_values=render_values_by_group[group.group_id],
                        context=context,
                    ),
                )
                tasks.append(task)
        return tuple(task.result() for task in tasks)

    def _validate_render_values(
        self,
        *,
        plan: ReviewPlanSpec,
        render_values_by_group: Mapping[
            str,
            Mapping[str, core_prompts.PromptRenderValue],
        ],
    ) -> None:
        """校验 group 渲染变量的键闭合性。

        :param plan: 当前 Review Plan。
        :param render_values_by_group: group 标识到渲染变量的映射。
        :return: 无返回值；变量映射不闭合时显式失败。
        :raises core_errors.ConfigurationError: 当缺少 group 变量或存在
            未声明 group 的悬空变量。
        """
        expected_group_ids = {group.group_id for group in plan.groups}
        actual_group_ids = set(render_values_by_group)
        invalid_reasons: list[str] = []
        if expected_group_ids - actual_group_ids:
            invalid_reasons.append("render_values_group_missing")
        if actual_group_ids - expected_group_ids:
            invalid_reasons.append("render_values_group_extra")
        if invalid_reasons:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Review 渲染变量映射非法",
                context={"reasons": ",".join(invalid_reasons)},
            )


__all__ = [
    "GroupedReviewRunner",
    "ReviewGroupRunner",
]

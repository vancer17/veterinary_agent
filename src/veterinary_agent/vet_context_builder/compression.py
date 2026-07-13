##################################################################################################
# 文件: src/veterinary_agent/vet_context_builder/compression.py
# 作用: 按策略预算选择 prompt 块、记录丢弃原因并在最终阶段检查和重新注入 P0 块。
# 边界: 只处理已编译候选块，不读取来源、不改变块正文、不调用 LLM 或保存 trace。
##################################################################################################

from dataclasses import dataclass

from veterinary_agent.config import VetContextBuilderSettings
from veterinary_agent.vet_context_builder.dto import (
    CompressionAuditDto,
    VetContextBuildRequestDto,
    VetPromptBlockDto,
)
from veterinary_agent.vet_context_builder.enums import (
    ContextCompressionStrategy,
    VetContextBuilderErrorCode,
    VetContextBuilderOperation,
    VetPromptBlockType,
)
from veterinary_agent.vet_context_builder.errors import VetContextBuilderError


@dataclass(frozen=True, slots=True)
class ContextCompressionResult:
    """上下文块预算选择结果。"""

    prompt_blocks: list[VetPromptBlockDto]
    audit: CompressionAuditDto


def _block_priority_key(block: VetPromptBlockDto) -> tuple[int, str]:
    """构建 prompt 块稳定保留排序键。

    :param block: 待排序的 prompt 块。
    :return: 块优先级数值与稳定块 ID 组成的排序键。
    """

    return int(block.priority), block.block_id


def _removable_block_key(block: VetPromptBlockDto) -> tuple[int, int]:
    """构建 P0 再注入时可移除块的逆向排序键。

    :param block: 待评估的可选 prompt 块。
    :return: 块优先级数值与 token 估算组成的排序键。
    """

    return int(block.priority), block.token_estimate


class ContextBudgetManager:
    """确定性块级 token 预算管理器。"""

    def __init__(self, *, settings: VetContextBuilderSettings) -> None:
        """初始化上下文预算管理器。

        :param settings: 当前 VetContextBuilder 配置。
        :return: None。
        """

        self._settings = settings

    def compress(
        self,
        *,
        request: VetContextBuildRequestDto,
        candidates: list[VetPromptBlockDto],
        truncated_block_ids: list[str],
    ) -> ContextCompressionResult:
        """按 token 与块数预算选择最终 prompt 块。

        :param request: 当前上下文构建请求。
        :param candidates: 已编译候选块。
        :param truncated_block_ids: 块编译阶段已执行有界裁剪的块 ID。
        :return: 最终 prompt 块与压缩审计摘要。
        :raises VetContextBuilderError: 当必需块本身超过预算或块数限制时抛出。
        """

        token_budget = self._effective_budget(request.compression_strategy)
        required_blocks = sorted(
            (block for block in candidates if block.required),
            key=_block_priority_key,
        )
        required_tokens = sum(block.token_estimate for block in required_blocks)
        if (
            required_tokens > token_budget
            or len(required_blocks) > self._settings.max_prompt_blocks
        ):
            raise VetContextBuilderError(
                code=(VetContextBuilderErrorCode.CONTEXT_REQUIRED_BLOCKS_EXCEED_BUDGET),
                operation=VetContextBuilderOperation.TRIM_BLOCKS,
                message="VetContextBuilder 必需上下文块超过配置预算",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={
                    "required_tokens": required_tokens,
                    "token_budget": token_budget,
                    "required_block_count": len(required_blocks),
                    "max_prompt_blocks": self._settings.max_prompt_blocks,
                },
            )

        selected = list(required_blocks)
        selected_ids = {block.block_id for block in selected}
        estimated_tokens = required_tokens
        dropped_reasons: dict[str, str] = {}
        optional_blocks = sorted(
            (block for block in candidates if not block.required),
            key=_block_priority_key,
        )
        for block in optional_blocks:
            if len(selected) >= self._settings.max_prompt_blocks:
                dropped_reasons[block.block_id] = "max_prompt_blocks"
                continue
            if estimated_tokens + block.token_estimate > token_budget:
                dropped_reasons[block.block_id] = "token_budget"
                continue
            selected.append(block)
            selected_ids.add(block.block_id)
            estimated_tokens += block.token_estimate

        selected, p0_reinjected = self._ensure_p0_after_trim(
            candidates=candidates,
            selected=selected,
            token_budget=token_budget,
            dropped_reasons=dropped_reasons,
            request=request,
        )
        selected.sort(key=_block_priority_key)
        selected_ids = {block.block_id for block in selected}
        estimated_tokens = sum(block.token_estimate for block in selected)
        for block in candidates:
            if block.block_id not in selected_ids:
                dropped_reasons.setdefault(block.block_id, "policy_filtered")
        dropped_block_ids = sorted(dropped_reasons)
        audit = CompressionAuditDto(
            compression_strategy=request.compression_strategy,
            token_budget=token_budget,
            estimated_tokens=estimated_tokens,
            trim_applied=bool(dropped_block_ids or truncated_block_ids),
            p0_reinjected=p0_reinjected,
            included_block_ids=[block.block_id for block in selected],
            dropped_block_ids=dropped_block_ids,
            dropped_reasons=dropped_reasons,
            truncated_block_ids=truncated_block_ids,
            fallback_path=None,
        )
        return ContextCompressionResult(prompt_blocks=selected, audit=audit)

    def _effective_budget(self, strategy: ContextCompressionStrategy) -> int:
        """解析应用安全余量后的策略 token 预算。

        :param strategy: 当前上下文压缩策略。
        :return: 扣除配置安全余量后的正整数 token 预算。
        """

        configured_budget = {
            ContextCompressionStrategy.SINGLE_FULL: (
                self._settings.budgets.single_full_tokens
            ),
            ContextCompressionStrategy.SAFETY_MINIMAL: (
                self._settings.budgets.safety_minimal_tokens
            ),
            ContextCompressionStrategy.EDUCATION_LIGHT: (
                self._settings.budgets.education_light_tokens
            ),
        }[strategy]
        return max(
            1,
            int(configured_budget * (1.0 - self._settings.budget_headroom_ratio)),
        )

    def _ensure_p0_after_trim(
        self,
        *,
        candidates: list[VetPromptBlockDto],
        selected: list[VetPromptBlockDto],
        token_budget: int,
        dropped_reasons: dict[str, str],
        request: VetContextBuildRequestDto,
    ) -> tuple[list[VetPromptBlockDto], bool]:
        """在块选择完成后强制检查并重新注入 P0 块。

        :param candidates: 全部候选块。
        :param selected: 初次预算选择后的块。
        :param token_budget: 当前有效 token 预算。
        :param dropped_reasons: 可变块丢弃原因映射。
        :param request: 当前上下文构建请求。
        :return: 完成 P0 后置检查的块列表与是否发生重新注入的标记。
        :raises VetContextBuilderError: 当重新注入 P0 后无法满足 token 或块数预算时抛出。
        """

        p0_candidate = next(
            (
                block
                for block in candidates
                if block.block_type is VetPromptBlockType.PET_PROFILE_P0
            ),
            None,
        )
        if p0_candidate is None:
            return selected, False
        if any(
            block.block_type is VetPromptBlockType.PET_PROFILE_P0 for block in selected
        ):
            return selected, False

        mutable_selected = list(selected)
        removable = sorted(
            (block for block in mutable_selected if not block.required),
            key=_removable_block_key,
            reverse=True,
        )
        while (
            sum(block.token_estimate for block in mutable_selected)
            + p0_candidate.token_estimate
            > token_budget
            or len(mutable_selected) >= self._settings.max_prompt_blocks
        ) and removable:
            removed = removable.pop(0)
            mutable_selected.remove(removed)
            dropped_reasons[removed.block_id] = "p0_reinjection"
        if (
            sum(block.token_estimate for block in mutable_selected)
            + p0_candidate.token_estimate
            > token_budget
            or len(mutable_selected) >= self._settings.max_prompt_blocks
        ):
            raise VetContextBuilderError(
                code=(VetContextBuilderErrorCode.CONTEXT_REQUIRED_BLOCKS_EXCEED_BUDGET),
                operation=VetContextBuilderOperation.TRIM_BLOCKS,
                message="VetContextBuilder 无法在预算内重新注入 P0 块",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )
        mutable_selected.append(p0_candidate)
        dropped_reasons.pop(p0_candidate.block_id, None)
        return mutable_selected, True


__all__: tuple[str, ...] = (
    "ContextBudgetManager",
    "ContextCompressionResult",
)

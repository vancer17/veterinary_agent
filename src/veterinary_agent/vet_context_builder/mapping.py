##################################################################################################
# 文件: src/veterinary_agent/vet_context_builder/mapping.py
# 作用: 将领域上下文块映射为 AgentRunner 可消费的通用 PromptBlockDto。
# 边界: 只做 DTO 映射，不渲染完整 prompt、不读取来源、不改变领域块正文或排序。
##################################################################################################

from veterinary_agent.agent_runner import PromptBlockDto
from veterinary_agent.vet_context_builder.dto import (
    VetContextBundleDto,
    VetPromptBlockDto,
)


def to_agent_prompt_block(block: VetPromptBlockDto) -> PromptBlockDto:
    """将单个领域 prompt 块映射为 AgentRunner 通用块。

    :param block: VetContextBuilder 已完成校验的领域 prompt 块。
    :return: AgentRunner 可消费的通用 prompt 块。
    """

    metadata: dict[str, object] = {
        **block.metadata,
        "priority": int(block.priority),
        "required": block.required,
        "content_hash": block.content_hash,
        "estimated_units": block.token_estimate,
        "source_refs": [
            source_ref.model_dump(mode="json") for source_ref in block.source_refs
        ],
    }
    return PromptBlockDto(
        block_id=block.block_id,
        block_type=block.block_type.value,
        content_ref_or_text=block.content_ref_or_text,
        metadata=metadata,
    )


def to_agent_prompt_blocks(bundle: VetContextBundleDto) -> list[PromptBlockDto]:
    """将上下文 bundle 的全部领域块映射为 AgentRunner 通用块。

    :param bundle: 已完成领域不变量校验的上下文 bundle。
    :return: 按 Builder 最终顺序排列的 AgentRunner prompt 块列表。
    """

    return [to_agent_prompt_block(block) for block in bundle.prompt_blocks]


__all__: tuple[str, ...] = (
    "to_agent_prompt_block",
    "to_agent_prompt_blocks",
)

"""
版本化 prompt 基础设施包。

该包提供 prompt 资产加载、exact version 注册、受限渲染和模型调用绑定校验。
语义模块必须通过本包顶层暴露的契约使用这些能力，不得直接引用内部实现
模块，也不得绕过 registry 构造模型调用输入。
"""

from __future__ import annotations

from vet_agent.core.prompts.contracts import (
    LoadedPromptAsset,
    PromptAssetSpec,
    PromptBinding,
    PromptMessage,
    PromptMessageRole,
    PromptMessageSpec,
    PromptPlaceholderKind,
    PromptPlaceholderSpec,
    PromptRecord,
    PromptTemplateKind,
    RenderedPrompt,
)
from vet_agent.core.prompts.loader import PromptAssetLoader
from vet_agent.core.prompts.registry import PromptRegistry
from vet_agent.core.prompts.renderer import DefaultPromptRenderer, PromptRenderValue
from vet_agent.core.prompts.validation import validate_prompt_binding

__all__ = [
    "DefaultPromptRenderer",
    "LoadedPromptAsset",
    "PromptAssetLoader",
    "PromptAssetSpec",
    "PromptBinding",
    "PromptMessage",
    "PromptMessageRole",
    "PromptMessageSpec",
    "PromptPlaceholderKind",
    "PromptPlaceholderSpec",
    "PromptRecord",
    "PromptRegistry",
    "PromptRenderValue",
    "PromptTemplateKind",
    "RenderedPrompt",
    "validate_prompt_binding",
]

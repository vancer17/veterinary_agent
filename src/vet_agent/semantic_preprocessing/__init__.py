"""
=============================================================================
文件：src/vet_agent/semantic_preprocessing/__init__.py
作用：作为语义预处理来源 Adapter 的组合包入口。
范围：暴露首轮历史 / 已验证事实边界与服务端可信宠物画像白名单投影。
说明：本包负责把外部受限来源适配为 semantic_collaboration M02 端口；
      核心语义协作包不反向依赖本包，也不在此实现问诊或临床安全领域逻辑。
=============================================================================
"""

from __future__ import annotations

from .production_sources import (
    FirstTurnOnlyBoundedHistoryReader,
    FirstTurnOnlyVerifiedPriorFactReader,
    ScopeTrustedPetContextReader,
)

__all__ = [
    "FirstTurnOnlyBoundedHistoryReader",
    "FirstTurnOnlyVerifiedPriorFactReader",
    "ScopeTrustedPetContextReader",
]

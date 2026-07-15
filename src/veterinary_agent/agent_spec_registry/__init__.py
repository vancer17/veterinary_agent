##################################################################################################
# 文件: src/veterinary_agent/agent_spec_registry/__init__.py
# 作用: 作为 AgentSpecRegistry 规格目录包统一出口，暴露默认规格构建与注册表创建能力。
# 边界: 外部包应从本文件导入能力，避免跨包直接引用目录、schema 或注册表内部实现模块。
##################################################################################################

from .catalog import (
    DEFAULT_AGENT_SPEC_CATALOG_VERSION,
    build_default_agent_specs,
)
from .registry import create_default_agent_spec_registry

__all__: tuple[str, ...] = (
    "DEFAULT_AGENT_SPEC_CATALOG_VERSION",
    "build_default_agent_specs",
    "create_default_agent_spec_registry",
)

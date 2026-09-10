"""
兽医智能体公共基础设施包。

本包只承载跨语义子层复用的工程能力，不承载兽医语义真相。当前提交先落地
``core.llm`` 与 ``core.prompts`` 的最小共享契约；配置、审计、编排等能力
将在各自架构范围内继续演进。
"""

from vet_agent.core import errors, execution, llm, prompts, schema, testing

__all__ = ["errors", "execution", "llm", "prompts", "schema", "testing"]

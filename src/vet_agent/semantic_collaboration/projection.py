"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/projection.py
作用：从权威 SkillSpec 生成并审计通用 SKILL.md 投影。
范围：覆盖 Markdown frontmatter、必需段落、字段所有权摘要和安全生产边界。
      当前 M06 生成任务使用包内标准化 SKILL.md，不由本文件生成提示词。
说明：Markdown 只是提示词与人工审查投影；运行时字段所有权始终来自
      SkillSpec / SkillCatalog，禁止解析正文替代权威契约。
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .contracts import (
    FieldOwnershipPath,
    SkillContextResource,
    SkillDocProjection,
    SkillFailureCode,
    SkillSpec,
)


@dataclass(frozen=True)
class SkillProjectionMetadata:
    """表示生成 SKILL.md 投影所需的权威契约摘要。

    :param skill_id: SKILL 稳定标识。
    :param skill_version: SKILL 语义化版本。
    :param task_kind: 正交任务类型枚举值。
    :param verifier_id: verifier 稳定标识。
    :param verifier_version: verifier 语义化版本。
    :param contract_version: SkillSpec 契约版本。
    :param execution_family: 执行家族枚举值。
    :param owns: 权威输出字段路径集合。
    :param forbidden_output: 禁止输出字段路径集合。
    :param required_context: 必需上下文资源集合。
    :param terminal_failures: 终态失败码集合。
    :param repair_mappings: 修复目标描述集合。
    :return: 无返回值。
    """

    skill_id: str
    skill_version: str
    task_kind: str
    verifier_id: str
    verifier_version: str
    contract_version: str
    execution_family: str
    owns: tuple[FieldOwnershipPath, ...]
    forbidden_output: tuple[FieldOwnershipPath, ...]
    required_context: tuple[SkillContextResource, ...]
    terminal_failures: tuple[SkillFailureCode, ...]
    repair_mappings: tuple[tuple[str, str, str], ...]


def render_skill_projection_from_metadata(
    metadata: SkillProjectionMetadata,
) -> str:
    """从权威契约摘要生成 Markdown 投影正文。

    :param metadata: SkillSpec 的启动期投影摘要。
    :return: 返回可写入 SKILL.md 的完整 Markdown 字符串。
    """
    owned_paths = "\n".join(f"- `{path.path}`" for path in metadata.owns)
    forbidden_paths = "\n".join(
        f"- `{path.path}`" for path in metadata.forbidden_output
    )
    required_context = "\n".join(
        f"- `{resource.value}`" for resource in metadata.required_context
    )
    failure_codes = "\n".join(
        f"- `{code.value}`" for code in metadata.terminal_failures
    )
    repair_mappings = "\n".join(
        f"- `{failure_code}` → `{repair_skill_id}@{repair_skill_version}`"
        for failure_code, repair_skill_id, repair_skill_version in metadata.repair_mappings
    )
    if not repair_mappings:
        repair_mappings = "- None."
    return f"""---
skill_id: {metadata.skill_id}
skill_version: {metadata.skill_version}
task_kind: {metadata.task_kind}
verifier_id: {metadata.verifier_id}
verifier_version: {metadata.verifier_version}
---
# {metadata.skill_id} Skill Projection

## Identity

This projection is generated from the authoritative `SkillSpec`.

## Scope

- Task kind: `{metadata.task_kind}`
- Execution family: `{metadata.execution_family}`
- Contract version: `{metadata.contract_version}`

## Context Policy

{required_context}

The skill reads only the declared immutable TurnSnapshot resources.

## Output Authority

{owned_paths}

## Failure And Repair

{failure_codes}

{repair_mappings}

Explicitly forbidden outputs:

{forbidden_paths}

## Safety Boundary

This skill does not perform medical judgment, write domain state, invoke clinical
safety policy, or consume unverified peer artifacts.
"""


def projection_metadata_from_spec(spec: SkillSpec) -> SkillProjectionMetadata:
    """从权威 SkillSpec 提取投影生成摘要。

    :param spec: 权威机器可读 SKILL 契约。
    :return: 返回 SKILL.md 投影生成所需的结构化摘要。
    """
    return SkillProjectionMetadata(
        skill_id=spec.skill_id,
        skill_version=spec.skill_version,
        task_kind=spec.task_kind.value,
        verifier_id=spec.verifier_binding.verifier_id,
        verifier_version=spec.verifier_binding.verifier_version,
        contract_version=spec.contract_version,
        execution_family=spec.execution_family.value,
        owns=spec.owns,
        forbidden_output=spec.forbidden_output,
        required_context=spec.context_contract.required_resources,
        terminal_failures=spec.failure_policy.terminal_on,
        repair_mappings=tuple(
            (
                mapping.failure_code.value,
                mapping.repair_skill_id,
                mapping.repair_skill_version,
            )
            for mapping in spec.repair_mappings
        ),
    )


def render_skill_projection(metadata: SkillProjectionMetadata) -> SkillDocProjection:
    """为权威契约摘要构造带摘要的 SKILL.md 投影对象。

    :param metadata: SkillSpec 的启动期投影摘要。
    :return: 返回通过启动期一致性校验的 Markdown 投影。
    """
    document = render_skill_projection_from_metadata(metadata)
    digest = sha256(document.encode("utf-8")).hexdigest()
    return SkillDocProjection(document=document, content_sha256=digest)

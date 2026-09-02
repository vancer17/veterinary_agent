"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/production.py
作用：声明受限语义协作 DAG 的 M01 生产 SkillCatalog 组合根。
范围：覆盖 Turn Intent、Claim Inventory、Statement Semantics、Participant、
      Temporal、Measurement、Canonical、Review、Repair 与 Patch Apply 的
      权威契约。
说明：本文件只注册正交语义任务，不包含医学词表、prompt 全文、调度实现、
      模型调用或领域状态写入；未实现的下游能力由后续模块显式立项。
=============================================================================
"""

from __future__ import annotations

from typing import Any

from .catalog import SkillCatalog
from .contracts import (
    DOMAIN_ISOLATED_CONTEXT_RESOURCES,
    ContextContract,
    FailurePolicy,
    FieldOwnershipPath,
    RepairMapping,
    SchemaContract,
    SkillContextResource,
    SkillExecutionFamily,
    SkillFailureCode,
    SkillObservabilityContract,
    SkillPatchType,
    SkillSpec,
    SkillTaskKind,
    SkillTraceKind,
    VerifierBinding,
)
from .projection import SkillProjectionMetadata, render_skill_projection


def _paths(*values: str) -> tuple[FieldOwnershipPath, ...]:
    """将点分字段字符串集合转换为所有权路径对象。

    :param values: 点分输出字段路径集合。
    :return: 返回规范化字段所有权路径元组。
    """
    return tuple(FieldOwnershipPath(path=value) for value in values)


def _context_resource_sort_key(resource: SkillContextResource) -> str:
    """读取上下文资源枚举的排序键。

    :param resource: 受限上下文资源枚举。
    :return: 返回资源稳定枚举值。
    """
    return resource.value


def _leaf_schema(path: str) -> dict[str, Any]:
    """按字段路径构造结构化叶子 schema。

    :param path: 规范化字段路径。
    :return: 返回叶子字段的 JSON Schema 定义。
    """
    scalar_types: dict[str, str] = {
        "turn_intent.evidence.phrase": "string",
        "claim.envelope.ordinal": "integer",
        "claim.envelope.parent_scope": "string",
        "claim.semantics.statement_type": "string",
        "claim.semantics.assertion_state": "string",
        "claim.semantics.certainty": "string",
        "claim.semantics.scope": "string",
        "claim.participants.subject.phrase": "string",
        "claim.participants.agent.phrase": "string",
        "claim.participants.recipient.phrase": "string",
        "claim.participants.object.phrase": "string",
        "claim.temporal.phrase": "string",
        "claim.temporal.claim_binding": "string",
        "claim.measurement.phrase": "string",
        "claim.measurement.claim_binding": "string",
        "claim.canonical.descriptor": "string",
        "claim.canonical.target_query": "string",
        "claim.canonical.claim_binding": "string",
        "review.verdict": "string",
        "review.failure_code": "string",
        "review.repair_hint": "string",
        "repair.patch_type": "string",
        "repair.base_version": "string",
        "artifact.version": "integer",
        "artifact.lineage": "string",
        "artifact.stale": "boolean",
    }
    schema_type = scalar_types.get(path, "object")
    return {
        "type": schema_type,
        "description": f"Authority field owned by this skill: {path}.",
        **({"additionalProperties": False} if schema_type == "object" else {}),
    }


def _insert_field_tree(tree: dict[str, Any], segments: tuple[str, ...]) -> None:
    """将一条字段路径插入输出 schema 的嵌套属性树。

    :param tree: 当前层级的 properties 字典。
    :param segments: 字段路径分段集合。
    :return: 无返回值。
    """
    current = tree
    for index, segment in enumerate(segments):
        is_leaf = index == len(segments) - 1
        path = ".".join(segments[: index + 1])
        if is_leaf:
            current[segment] = _leaf_schema(path)
            return
        if segment not in current or current[segment].get("type") != "object":
            current[segment] = {
                "type": "object",
                "description": f"Authority scope for {path}.",
                "properties": {},
                "additionalProperties": False,
            }
        nested_properties = current[segment].setdefault("properties", {})
        if not isinstance(nested_properties, dict):
            raise TypeError(f"invalid schema tree at {path}")
        current = nested_properties


def _output_schema(
    schema_id: str, owns: tuple[FieldOwnershipPath, ...]
) -> SchemaContract:
    """构造 SKILL 输出契约。

    :param schema_id: 输出 schema 稳定标识。
    :param owns: 当前 SKILL 拥有的字段路径集合。
    :return: 返回严格 object 根的输出 schema 契约。
    """
    properties: dict[str, Any] = {}
    for path in owns:
        _insert_field_tree(properties, path.segments())
    return SchemaContract(
        schema_id=schema_id,
        schema_version="1.0.0",
        json_schema={
            "type": "object",
            "description": "Strict output contract for a semantic collaboration skill.",
            "properties": properties,
            "required": sorted(properties),
            "additionalProperties": False,
        },
    )


def _input_schema(skill_id: str) -> SchemaContract:
    """构造 SKILL 输入契约。

    :param skill_id: SKILL 稳定标识。
    :return: 返回绑定 TurnSnapshot 与 verified artifact 的输入 schema 契约。
    """
    return SchemaContract(
        schema_id=f"semantic_collaboration.{skill_id}.input",
        schema_version="1.0.0",
        json_schema={
            "type": "object",
            "description": "Immutable task envelope bound to one TurnSnapshot digest.",
            "properties": {
                "task_id": {"type": "string", "description": "计划内任务标识。"},
                "turn_snapshot_digest": {
                    "type": "string",
                    "description": "当前回合不可变上下文摘要。",
                },
                "dependencies": {
                    "type": "object",
                    "description": "按契约声明的 verified artifact 输入。",
                    "additionalProperties": False,
                    "properties": {},
                },
            },
            "required": ["task_id", "turn_snapshot_digest", "dependencies"],
            "additionalProperties": False,
        },
    )


def _context_contract(
    required_resources: tuple[SkillContextResource, ...],
) -> ContextContract:
    """构造受限上下文契约。

    :param required_resources: 必需只读上下文资源集合。
    :return: 返回包含完整领域隔离禁止项的上下文契约。
    """
    return ContextContract(
        required_resources=required_resources,
        forbidden_resources=tuple(
            sorted(
                DOMAIN_ISOLATED_CONTEXT_RESOURCES,
                key=_context_resource_sort_key,
            )
        ),
    )


def _verifier_binding(
    task_kind: SkillTaskKind,
    verifier_id: str,
) -> VerifierBinding:
    """构造确定性 verifier 绑定。

    :param task_kind: 当前 SKILL 的正交任务类型。
    :param verifier_id: verifier 稳定标识。
    :return: 返回 verifier 绑定契约。
    """
    return VerifierBinding(
        verifier_id=verifier_id,
        verifier_version="1.0.0",
        accepted_task_kinds=(task_kind,),
    )


def _repair_mapping(
    failure_code: SkillFailureCode,
    patch_type: SkillPatchType,
) -> RepairMapping:
    """构造一个白名单修复映射。

    :param failure_code: 可修复失败码。
    :param patch_type: 允许的 typed patch 类型。
    :return: 返回修复映射契约。
    """
    return RepairMapping(
        failure_code=failure_code,
        repair_skill_id="semantic_repair",
        repair_skill_version="1.0.0",
        allowed_patch_types=(patch_type,),
    )


def _skill_spec(
    *,
    skill_id: str,
    task_kind: SkillTaskKind,
    execution_family: SkillExecutionFamily,
    verifier_id: str,
    owns: tuple[FieldOwnershipPath, ...],
    forbidden_output: tuple[FieldOwnershipPath, ...],
    required_context: tuple[SkillContextResource, ...],
    repair_mappings: tuple[RepairMapping, ...] = (),
    retryable_failures: tuple[SkillFailureCode, ...] = (),
    max_attempts: int = 1,
) -> SkillSpec:
    """构造生产 SkillSpec。

    :param skill_id: SKILL 稳定标识。
    :param task_kind: 正交任务类型。
    :param execution_family: 执行家族类型。
    :param verifier_id: verifier 稳定标识。
    :param owns: 权威输出字段集合。
    :param forbidden_output: 禁止输出字段集合。
    :param required_context: 必需上下文资源集合。
    :param repair_mappings: 白名单修复映射集合。
    :param retryable_failures: 允许有界重试的失败码集合。
    :param max_attempts: 最大执行尝试次数。
    :return: 返回通过自身一致性校验的 SkillSpec。
    """
    mapped_failures = {mapping.failure_code for mapping in repair_mappings}
    retryable = set(retryable_failures)
    terminal = tuple(
        code
        for code in SkillFailureCode
        if code not in mapped_failures and code not in retryable
    )
    trace_kind = {
        SkillExecutionFamily.STRUCTURED_GENERATION: SkillTraceKind.GENERATION_SKILL,
        SkillExecutionFamily.DETERMINISTIC_REVIEW: SkillTraceKind.REVIEW_SKILL,
        SkillExecutionFamily.TYPED_REPAIR: SkillTraceKind.REPAIR_SKILL,
        SkillExecutionFamily.DETERMINISTIC_PATCH_APPLY: SkillTraceKind.PATCH_APPLIER,
    }[execution_family]
    projection_metadata = SkillProjectionMetadata(
        skill_id=skill_id,
        skill_version="1.0.0",
        task_kind=task_kind.value,
        verifier_id=verifier_id,
        verifier_version="1.0.0",
        contract_version="1.0.0",
        execution_family=execution_family.value,
        owns=owns,
        forbidden_output=forbidden_output,
        required_context=required_context,
        terminal_failures=terminal,
        repair_mappings=tuple(
            (
                mapping.failure_code.value,
                mapping.repair_skill_id,
                mapping.repair_skill_version,
            )
            for mapping in repair_mappings
        ),
    )
    return SkillSpec(
        skill_id=skill_id,
        skill_version="1.0.0",
        contract_version="1.0.0",
        task_kind=task_kind,
        execution_family=execution_family,
        input_contract=_input_schema(skill_id),
        output_contract=_output_schema(
            f"semantic_collaboration.{skill_id}.output",
            owns,
        ),
        owns=owns,
        does_not_own=forbidden_output,
        forbidden_output=forbidden_output,
        context_contract=_context_contract(required_context),
        verifier_binding=_verifier_binding(task_kind, verifier_id),
        failure_policy=FailurePolicy(
            terminal_on=terminal,
            retryable_on=retryable_failures,
            max_attempts=max_attempts,
        ),
        repair_mappings=repair_mappings,
        prompt_projection=render_skill_projection(projection_metadata),
        observability=SkillObservabilityContract(trace_kind=trace_kind),
    )


TURN_INTENT_SPEC = _skill_spec(
    skill_id="turn_intent",
    task_kind=SkillTaskKind.TURN_INTENT,
    execution_family=SkillExecutionFamily.STRUCTURED_GENERATION,
    verifier_id="turn_intent_verifier",
    owns=_paths(
        "turn_intent.acts",
        "turn_intent.evidence.phrase",
    ),
    forbidden_output=_paths("claim", "medical_decision"),
    required_context=(
        SkillContextResource.TURN_SNAPSHOT_DIGEST,
        SkillContextResource.ORIGINAL_USER_TEXT,
        SkillContextResource.BOUNDED_CONVERSATION_HISTORY,
        SkillContextResource.LAST_ASSISTANT_QUESTIONS,
        SkillContextResource.VERIFIED_PRIOR_FACT_SUMMARY,
    ),
    repair_mappings=(
        _repair_mapping(
            SkillFailureCode.SCHEMA_INVALID,
            SkillPatchType.INTENT_FIELD_PATCH,
        ),
    ),
    retryable_failures=(
        SkillFailureCode.MODEL_CALL_FAILED,
        SkillFailureCode.RESPONSE_PARSE_FAILED,
        SkillFailureCode.TIMEOUT,
    ),
    max_attempts=2,
)


CLAIM_INVENTORY_SPEC = _skill_spec(
    skill_id="claim_inventory",
    task_kind=SkillTaskKind.CLAIM_INVENTORY,
    execution_family=SkillExecutionFamily.STRUCTURED_GENERATION,
    verifier_id="claim_inventory_verifier",
    owns=_paths(
        "claim.envelope.ordinal",
        "claim.envelope.parent_scope",
    ),
    forbidden_output=_paths("turn_intent", "medical_decision"),
    required_context=(
        SkillContextResource.TURN_SNAPSHOT_DIGEST,
        SkillContextResource.ORIGINAL_USER_TEXT,
        SkillContextResource.VERIFIED_PRIOR_FACT_SUMMARY,
        SkillContextResource.TRUSTED_PET_CONTEXT,
        SkillContextResource.VERIFIED_PEER_ARTIFACT,
    ),
    repair_mappings=(
        _repair_mapping(
            SkillFailureCode.SCHEMA_INVALID,
            SkillPatchType.CLAIM_ENVELOPE_PATCH,
        ),
    ),
    retryable_failures=(
        SkillFailureCode.MODEL_CALL_FAILED,
        SkillFailureCode.RESPONSE_PARSE_FAILED,
        SkillFailureCode.TIMEOUT,
    ),
    max_attempts=2,
)


STATEMENT_SEMANTICS_SPEC = _skill_spec(
    skill_id="statement_semantics",
    task_kind=SkillTaskKind.STATEMENT_SEMANTICS,
    execution_family=SkillExecutionFamily.STRUCTURED_GENERATION,
    verifier_id="statement_semantics_verifier",
    owns=_paths(
        "claim.semantics.statement_type",
        "claim.semantics.assertion_state",
        "claim.semantics.certainty",
        "claim.semantics.scope",
    ),
    forbidden_output=_paths("claim.participants", "medical_decision"),
    required_context=(
        SkillContextResource.TURN_SNAPSHOT_DIGEST,
        SkillContextResource.ORIGINAL_USER_TEXT,
        SkillContextResource.VERIFIED_PEER_ARTIFACT,
    ),
    repair_mappings=(
        _repair_mapping(
            SkillFailureCode.SCHEMA_INVALID,
            SkillPatchType.STATEMENT_SEMANTICS_PATCH,
        ),
        _repair_mapping(
            SkillFailureCode.REVIEW_REJECTED,
            SkillPatchType.STATEMENT_SEMANTICS_PATCH,
        ),
    ),
    retryable_failures=(
        SkillFailureCode.MODEL_CALL_FAILED,
        SkillFailureCode.RESPONSE_PARSE_FAILED,
        SkillFailureCode.TIMEOUT,
    ),
    max_attempts=2,
)


PARTICIPANT_PHRASE_SPEC = _skill_spec(
    skill_id="participant_phrase",
    task_kind=SkillTaskKind.PARTICIPANT_PHRASE,
    execution_family=SkillExecutionFamily.STRUCTURED_GENERATION,
    verifier_id="participant_phrase_verifier",
    owns=_paths(
        "claim.participants.subject.phrase",
        "claim.participants.agent.phrase",
        "claim.participants.recipient.phrase",
        "claim.participants.object.phrase",
    ),
    forbidden_output=_paths("claim.semantics", "medical_decision"),
    required_context=(
        SkillContextResource.TURN_SNAPSHOT_DIGEST,
        SkillContextResource.ORIGINAL_USER_TEXT,
        SkillContextResource.TRUSTED_PET_CONTEXT,
        SkillContextResource.VERIFIED_PEER_ARTIFACT,
    ),
    repair_mappings=(
        _repair_mapping(
            SkillFailureCode.EVIDENCE_OUT_OF_SCOPE,
            SkillPatchType.PARTICIPANT_PHRASE_PATCH,
        ),
    ),
    retryable_failures=(
        SkillFailureCode.MODEL_CALL_FAILED,
        SkillFailureCode.RESPONSE_PARSE_FAILED,
        SkillFailureCode.TIMEOUT,
    ),
    max_attempts=2,
)


TEMPORAL_PHRASE_SPEC = _skill_spec(
    skill_id="temporal_phrase",
    task_kind=SkillTaskKind.TEMPORAL_PHRASE,
    execution_family=SkillExecutionFamily.STRUCTURED_GENERATION,
    verifier_id="temporal_phrase_verifier",
    owns=_paths(
        "claim.temporal.phrase",
        "claim.temporal.claim_binding",
    ),
    forbidden_output=_paths("claim.measurement", "medical_decision"),
    required_context=(
        SkillContextResource.TURN_SNAPSHOT_DIGEST,
        SkillContextResource.ORIGINAL_USER_TEXT,
        SkillContextResource.VERIFIED_PEER_ARTIFACT,
    ),
    repair_mappings=(
        _repair_mapping(
            SkillFailureCode.CLAIM_BINDING_INVALID,
            SkillPatchType.TEMPORAL_PHRASE_PATCH,
        ),
    ),
    retryable_failures=(
        SkillFailureCode.MODEL_CALL_FAILED,
        SkillFailureCode.RESPONSE_PARSE_FAILED,
        SkillFailureCode.TIMEOUT,
    ),
    max_attempts=2,
)


MEASUREMENT_PHRASE_SPEC = _skill_spec(
    skill_id="measurement_phrase",
    task_kind=SkillTaskKind.MEASUREMENT_PHRASE,
    execution_family=SkillExecutionFamily.STRUCTURED_GENERATION,
    verifier_id="measurement_phrase_verifier",
    owns=_paths(
        "claim.measurement.phrase",
        "claim.measurement.claim_binding",
    ),
    forbidden_output=_paths("claim.temporal", "medical_decision"),
    required_context=(
        SkillContextResource.TURN_SNAPSHOT_DIGEST,
        SkillContextResource.ORIGINAL_USER_TEXT,
        SkillContextResource.VERIFIED_PEER_ARTIFACT,
    ),
    repair_mappings=(
        _repair_mapping(
            SkillFailureCode.CLAIM_BINDING_INVALID,
            SkillPatchType.MEASUREMENT_PHRASE_PATCH,
        ),
    ),
    retryable_failures=(
        SkillFailureCode.MODEL_CALL_FAILED,
        SkillFailureCode.RESPONSE_PARSE_FAILED,
        SkillFailureCode.TIMEOUT,
    ),
    max_attempts=2,
)


CANONICAL_DESCRIPTOR_SPEC = _skill_spec(
    skill_id="canonical_descriptor",
    task_kind=SkillTaskKind.CANONICAL_DESCRIPTOR,
    execution_family=SkillExecutionFamily.STRUCTURED_GENERATION,
    verifier_id="canonical_descriptor_verifier",
    owns=_paths(
        "claim.canonical.descriptor",
        "claim.canonical.target_query",
        "claim.canonical.claim_binding",
    ),
    forbidden_output=_paths("canonical_id", "medical_decision"),
    required_context=(
        SkillContextResource.TURN_SNAPSHOT_DIGEST,
        SkillContextResource.ORIGINAL_USER_TEXT,
        SkillContextResource.VERIFIED_PEER_ARTIFACT,
    ),
    repair_mappings=(
        _repair_mapping(
            SkillFailureCode.SCHEMA_INVALID,
            SkillPatchType.CANONICAL_DESCRIPTOR_PATCH,
        ),
    ),
    retryable_failures=(
        SkillFailureCode.MODEL_CALL_FAILED,
        SkillFailureCode.RESPONSE_PARSE_FAILED,
        SkillFailureCode.TIMEOUT,
    ),
    max_attempts=2,
)


SEMANTIC_REVIEW_SPEC = _skill_spec(
    skill_id="semantic_review",
    task_kind=SkillTaskKind.REVIEW,
    execution_family=SkillExecutionFamily.DETERMINISTIC_REVIEW,
    verifier_id="semantic_review_verifier",
    owns=_paths(
        "review.verdict",
        "review.failure_code",
        "review.repair_hint",
    ),
    forbidden_output=_paths("claim", "medical_decision"),
    required_context=(
        SkillContextResource.TURN_SNAPSHOT_DIGEST,
        SkillContextResource.ORIGINAL_USER_TEXT,
        SkillContextResource.VERIFIED_PEER_ARTIFACT,
        SkillContextResource.VERIFIED_REVIEW_ARTIFACT,
    ),
)


SEMANTIC_REPAIR_SPEC = _skill_spec(
    skill_id="semantic_repair",
    task_kind=SkillTaskKind.REPAIR,
    execution_family=SkillExecutionFamily.TYPED_REPAIR,
    verifier_id="semantic_repair_verifier",
    owns=_paths(
        "repair.patch_type",
        "repair.base_version",
        "repair.proposal",
    ),
    forbidden_output=_paths("artifact", "medical_decision"),
    required_context=(
        SkillContextResource.TURN_SNAPSHOT_DIGEST,
        SkillContextResource.ORIGINAL_USER_TEXT,
        SkillContextResource.VERIFIED_PEER_ARTIFACT,
        SkillContextResource.VERIFIED_REVIEW_ARTIFACT,
        SkillContextResource.ARTIFACT_BASE_VERSION,
    ),
)


PATCH_APPLIER_SPEC = _skill_spec(
    skill_id="patch_applier",
    task_kind=SkillTaskKind.PATCH_APPLY,
    execution_family=SkillExecutionFamily.DETERMINISTIC_PATCH_APPLY,
    verifier_id="patch_applier_verifier",
    owns=_paths(
        "artifact.version",
        "artifact.lineage",
        "artifact.stale",
    ),
    forbidden_output=_paths("claim", "medical_decision"),
    required_context=(
        SkillContextResource.TURN_SNAPSHOT_DIGEST,
        SkillContextResource.VERIFIED_PATCH_PROPOSAL,
        SkillContextResource.ARTIFACT_BASE_VERSION,
    ),
)


PRODUCTION_SEMANTIC_SKILL_SPECS: tuple[SkillSpec, ...] = (
    TURN_INTENT_SPEC,
    CLAIM_INVENTORY_SPEC,
    STATEMENT_SEMANTICS_SPEC,
    PARTICIPANT_PHRASE_SPEC,
    TEMPORAL_PHRASE_SPEC,
    MEASUREMENT_PHRASE_SPEC,
    CANONICAL_DESCRIPTOR_SPEC,
    SEMANTIC_REVIEW_SPEC,
    SEMANTIC_REPAIR_SPEC,
    PATCH_APPLIER_SPEC,
)


def build_production_skill_catalog() -> SkillCatalog:
    """构建并冻结受限语义协作生产 SkillCatalog。

    :return: 返回完成全局闭合校验后的不可变生产目录。
    """
    return SkillCatalog(PRODUCTION_SEMANTIC_SKILL_SPECS).freeze()

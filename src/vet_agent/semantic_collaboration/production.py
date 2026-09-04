"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/production.py
作用：声明受限语义协作 DAG 的 M01 生产 SkillCatalog 组合根。
范围：覆盖 Turn Intent、Claim Proposition Inventory、Review、Repair 与
      Patch Apply 的权威契约；当前生产生成面只保留两个正交根任务。
说明：本文件不包含医学词表、prompt 全文、调度实现、模型调用或领域状态
      写入；生成任务绑定包内标准化 SKILL.md，Participant / Temporal /
      Measurement / Canonical 等未具备 resolver 与消费者的 lane 不进入当前
      生产目录。
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
    SchemaContract,
    SkillContextResource,
    SkillExecutionFamily,
    SkillFailureCode,
    SkillObservabilityContract,
    SkillSpec,
    SkillTaskKind,
    SkillTraceKind,
    VerifierBinding,
)
from .projection import SkillProjectionMetadata, render_skill_projection
from .skill_document import load_semantic_skill_document


def _paths(*values: str) -> tuple[FieldOwnershipPath, ...]:
    """将点分字段字符串集合转换为所有权路径对象。

    :param values: 点分输出字段路径集合。
    :return: 返回规范化字段所有权路径元组。
    """
    return tuple(FieldOwnershipPath(path=value) for value in values)


def _context_resource_sort_key(resource: SkillContextResource) -> str:
    """读取上下文资源枚举的稳定排序键。

    :param resource: 受限上下文资源枚举。
    :return: 返回资源稳定枚举值。
    """
    return resource.value


def _leaf_schema(path: str) -> dict[str, Any]:
    """按字段路径构造结构化叶子 schema。

    :param path: 规范化字段所有权路径。
    :return: 返回不含医学语义的字段 schema 定义。
    """
    scalar_types: dict[str, str] = {
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


def _insert_field_tree(
    tree: dict[str, Any],
    segments: tuple[str, ...],
) -> None:
    """把一条所有权路径插入输出 schema 属性树。

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


def _nested_output_schema(
    schema_id: str,
    owns: tuple[FieldOwnershipPath, ...],
) -> SchemaContract:
    """从字段所有权路径构造严格嵌套输出 schema。

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


def _turn_intent_output_schema() -> SchemaContract:
    """构造 Turn Intent fixed-field boolean 输出契约。

    :return: 返回七个回合意图信号的极薄严格 schema。
    """
    fields = (
        "answer_now",
        "wants_triage",
        "correction",
        "clarification_request",
        "fact_statement_present",
        "question_present",
        "report_context_present",
    )
    properties: dict[str, Any] = {
        field: {
            "type": "boolean",
            "description": f"Turn-level intent signal: {field}.",
        }
        for field in fields
    }
    return SchemaContract(
        schema_id="semantic_collaboration.turn_intent.output",
        schema_version="2.0.0",
        json_schema={
            "type": "object",
            "description": "Fixed-field turn intent contract without evidence fields.",
            "properties": properties,
            "required": list(fields),
            "additionalProperties": False,
        },
    )


def _claim_inventory_output_schema() -> SchemaContract:
    """构造自然语言 Claim Proposition Inventory 输出契约。

    :return: 返回仅包含 claims 字符串数组的极薄严格 schema。
    """
    return SchemaContract(
        schema_id="semantic_collaboration.claim_inventory.output",
        schema_version="2.0.0",
        json_schema={
            "type": "object",
            "description": "Self-contained natural-language claim proposition inventory.",
            "properties": {
                "claims": {
                    "type": "array",
                    "description": "Ordered self-contained Chinese claim propositions.",
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 240,
                    },
                    "minItems": 0,
                    "maxItems": 8,
                },
            },
            "required": ["claims"],
            "additionalProperties": False,
        },
    )


def _claim_coverage_review_output_schema() -> SchemaContract:
    """构造 Coverage Review 固定布尔矩阵输出契约。

    :return: 返回仅包含覆盖矩阵和有界缺失提示的严格 schema。
    """
    fields = (
        "存在漏抽显式事实",
        "存在多事实合并",
        "存在重复claim",
        "存在原文不支持的claim",
        "存在非自包含proposition",
        "存在shared scope拆分错误",
        "未分类覆盖问题",
    )
    matrix_properties: dict[str, Any] = {
        field: {
            "type": "boolean",
            "description": f"Coverage review signal: {field}.",
        }
        for field in fields
    }
    return SchemaContract(
        schema_id="semantic_collaboration.claim_coverage_review.output",
        schema_version="1.0.0",
        json_schema={
            "type": "object",
            "description": "Turn-level claim coverage boolean matrix.",
            "properties": {
                "coverage_matrix": {
                    "type": "object",
                    "description": "Fixed Chinese coverage review dimensions.",
                    "properties": matrix_properties,
                    "required": list(fields),
                    "additionalProperties": False,
                },
                "missing_claim_candidates": {
                    "type": "array",
                    "description": "Bounded natural-language repair hints only.",
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 240,
                    },
                    "minItems": 0,
                    "maxItems": 8,
                },
            },
            "required": ["coverage_matrix", "missing_claim_candidates"],
            "additionalProperties": False,
        },
    )


def _claim_faithfulness_review_output_schema() -> SchemaContract:
    """构造 Faithfulness Review 固定布尔矩阵输出契约。

    :return: 返回只描述单条 proposition 语义漂移维度的严格 schema。
    """
    fields = (
        "主体或指代范围改变",
        "否定方向改变",
        "否定范围改变",
        "正常状态误写为否认",
        "事实类型改变",
        "时间范围改变",
        "频率或数量改变",
        "程度或强度改变",
        "确定性改变",
        "因果关系改变",
        "医学推断或建议添加",
        "命题不自包含",
        "指代对象不明",
        "时间基准不明",
        "否定范围不明",
        "比较基线不明",
        "未分类语义改变",
    )
    matrix_properties: dict[str, Any] = {
        field: {
            "type": "boolean",
            "description": f"Faithfulness review signal: {field}.",
        }
        for field in fields
    }
    return SchemaContract(
        schema_id="semantic_collaboration.claim_faithfulness_review.output",
        schema_version="1.0.0",
        json_schema={
            "type": "object",
            "description": "Claim-level faithfulness boolean matrix.",
            "properties": {
                "faithfulness_matrix": {
                    "type": "object",
                    "description": "Fixed Chinese faithfulness review dimensions.",
                    "properties": matrix_properties,
                    "required": list(fields),
                    "additionalProperties": False,
                },
            },
            "required": ["faithfulness_matrix"],
            "additionalProperties": False,
        },
    )


def _claim_proposition_repair_output_schema() -> SchemaContract:
    """构造单条 claim 修复 SKILL 的极薄输出契约。

    :return: 返回只包含一个修复 proposition 的严格 schema。
    """
    return SchemaContract(
        schema_id="semantic_collaboration.claim_proposition_repair.output",
        schema_version="1.0.0",
        json_schema={
            "type": "object",
            "description": "Single-proposition repair output without model self-attestation.",
            "properties": {
                "proposition": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 240,
                    "description": "Repaired self-contained Chinese proposition.",
                },
            },
            "required": ["proposition"],
            "additionalProperties": False,
        },
    )


def _claim_inventory_repair_output_schema() -> SchemaContract:
    """构造 Claim Inventory 修复 SKILL 的稀疏 delta 输出契约。

    :return: 返回只允许局部修改和新增 claim 的严格 schema。
    """
    proposition = {
        "type": "string",
        "minLength": 1,
        "maxLength": 240,
    }
    return SchemaContract(
        schema_id="semantic_collaboration.claim_inventory_repair.output",
        schema_version="1.0.0",
        json_schema={
            "type": "object",
            "description": "Sparse claim inventory delta without full artifact rewrite.",
            "properties": {
                "modified_claims": {
                    "type": "array",
                    "maxItems": 2,
                    "items": {
                        "type": "object",
                        "properties": {
                            "target": {
                                "type": "string",
                                "pattern": "^c[0-7]$",
                            },
                            "propositions": {
                                "type": "array",
                                "maxItems": 3,
                                "items": proposition,
                            },
                        },
                        "required": ["target", "propositions"],
                        "additionalProperties": False,
                    },
                },
                "added_claims": {
                    "type": "array",
                    "maxItems": 2,
                    "items": proposition,
                },
            },
            "required": ["modified_claims", "added_claims"],
            "additionalProperties": False,
        },
    )


def _input_schema(skill_id: str) -> SchemaContract:
    """构造 SKILL 输入契约。

    :param skill_id: SKILL 稳定标识。
    :return: 返回绑定 TurnSnapshot digest 与依赖 artifact 的输入 schema。
    """
    return SchemaContract(
        schema_id=f"semantic_collaboration.{skill_id}.input",
        schema_version="1.0.0",
        json_schema={
            "type": "object",
            "description": "Immutable task envelope bound to one TurnSnapshot digest.",
            "properties": {
                "task_id": {"type": "string"},
                "turn_snapshot_digest": {"type": "string"},
                "dependencies": {
                    "type": "object",
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
    :return: 返回包含领域隔离禁止项的上下文契约。
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


def _skill_spec(
    *,
    skill_id: str,
    skill_version: str,
    task_kind: SkillTaskKind,
    execution_family: SkillExecutionFamily,
    verifier_id: str,
    owns: tuple[FieldOwnershipPath, ...],
    output_contract: SchemaContract,
    forbidden_output: tuple[FieldOwnershipPath, ...],
    required_context: tuple[SkillContextResource, ...],
    retryable_failures: tuple[SkillFailureCode, ...] = (),
    max_attempts: int = 1,
) -> SkillSpec:
    """构造通过权威边界校验的生产 SkillSpec。

    :param skill_id: 生产 SKILL 稳定标识。
    :param skill_version: 生产 SKILL 精确版本。
    :param task_kind: 正交语义任务类型。
    :param execution_family: SKILL 执行家族类型。
    :param verifier_id: 绑定的确定性 verifier 标识。
    :param owns: 当前 SKILL 权威字段集合。
    :param output_contract: 显式输出 schema 契约。
    :param forbidden_output: 禁止输出字段集合。
    :param required_context: 必需上下文资源集合。
    :param retryable_failures: 允许有界重试的失败码集合。
    :param max_attempts: 最大执行尝试次数。
    :return: 返回可注册的 SkillSpec。
    """
    retryable = set(retryable_failures)
    terminal = tuple(code for code in SkillFailureCode if code not in retryable)
    trace_kind = {
        SkillExecutionFamily.STRUCTURED_GENERATION: SkillTraceKind.GENERATION_SKILL,
        SkillExecutionFamily.STRUCTURED_REVIEW: SkillTraceKind.REVIEW_SKILL,
        SkillExecutionFamily.STRUCTURED_REPAIR: SkillTraceKind.REPAIR_SKILL,
        SkillExecutionFamily.DETERMINISTIC_PATCH_APPLY: SkillTraceKind.PATCH_APPLIER,
    }[execution_family]
    document_backed_families = {
        SkillExecutionFamily.STRUCTURED_GENERATION,
        SkillExecutionFamily.STRUCTURED_REVIEW,
        SkillExecutionFamily.STRUCTURED_REPAIR,
    }
    skill_document = (
        load_semantic_skill_document(skill_id)
        if execution_family in document_backed_families
        else None
    )
    if skill_document is None:
        projection_metadata = SkillProjectionMetadata(
            skill_id=skill_id,
            skill_version=skill_version,
            task_kind=task_kind.value,
            verifier_id=verifier_id,
            verifier_version="1.0.0",
            contract_version="1.0.0",
            execution_family=execution_family.value,
            owns=owns,
            forbidden_output=forbidden_output,
            required_context=required_context,
            terminal_failures=terminal,
            repair_mappings=(),
        )
        prompt_projection = render_skill_projection(projection_metadata)
    else:
        prompt_projection = skill_document.projection
    spec = SkillSpec(
        skill_id=skill_id,
        skill_version=skill_version,
        contract_version="1.0.0",
        task_kind=task_kind,
        execution_family=execution_family,
        input_contract=_input_schema(skill_id),
        output_contract=output_contract,
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
        prompt_projection=prompt_projection,
        observability=SkillObservabilityContract(trace_kind=trace_kind),
    )
    if skill_document is not None:
        skill_document.validate_against_spec(spec)
    return spec


TURN_INTENT_SPEC = _skill_spec(
    skill_id="turn_intent",
    skill_version="2.0.0",
    task_kind=SkillTaskKind.TURN_INTENT,
    execution_family=SkillExecutionFamily.STRUCTURED_GENERATION,
    verifier_id="turn_intent_verifier",
    owns=_paths(
        "answer_now",
        "wants_triage",
        "correction",
        "clarification_request",
        "fact_statement_present",
        "question_present",
        "report_context_present",
    ),
    output_contract=_turn_intent_output_schema(),
    forbidden_output=_paths("claims", "evidence", "medical_decision"),
    required_context=(
        SkillContextResource.TURN_SNAPSHOT_DIGEST,
        SkillContextResource.ORIGINAL_USER_TEXT,
        SkillContextResource.LAST_ASSISTANT_QUESTIONS,
        SkillContextResource.VERIFIED_PRIOR_FACT_SUMMARY,
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
    skill_version="2.0.0",
    task_kind=SkillTaskKind.CLAIM_INVENTORY,
    execution_family=SkillExecutionFamily.STRUCTURED_GENERATION,
    verifier_id="claim_inventory_verifier",
    owns=_paths("claims"),
    output_contract=_claim_inventory_output_schema(),
    forbidden_output=_paths(
        "answer_now",
        "wants_triage",
        "evidence",
        "medical_decision",
    ),
    required_context=(
        SkillContextResource.TURN_SNAPSHOT_DIGEST,
        SkillContextResource.ORIGINAL_USER_TEXT,
        SkillContextResource.LAST_ASSISTANT_QUESTIONS,
        SkillContextResource.VERIFIED_PRIOR_FACT_SUMMARY,
        SkillContextResource.TRUSTED_PET_CONTEXT,
    ),
    retryable_failures=(
        SkillFailureCode.MODEL_CALL_FAILED,
        SkillFailureCode.RESPONSE_PARSE_FAILED,
        SkillFailureCode.TIMEOUT,
    ),
    max_attempts=2,
)


CLAIM_COVERAGE_REVIEW_SPEC = _skill_spec(
    skill_id="claim_coverage_review",
    skill_version="1.0.0",
    task_kind=SkillTaskKind.CLAIM_COVERAGE_REVIEW,
    execution_family=SkillExecutionFamily.STRUCTURED_REVIEW,
    verifier_id="claim_coverage_review_verifier",
    owns=_paths("coverage_matrix", "missing_claim_candidates"),
    output_contract=_claim_coverage_review_output_schema(),
    forbidden_output=_paths(
        "claims",
        "verdict",
        "reason",
        "confidence",
        "corrected_proposition",
        "evidence",
        "medical_decision",
    ),
    required_context=(
        SkillContextResource.TURN_SNAPSHOT_DIGEST,
        SkillContextResource.ORIGINAL_USER_TEXT,
        SkillContextResource.LAST_ASSISTANT_QUESTIONS,
        SkillContextResource.VERIFIED_PRIOR_FACT_SUMMARY,
        SkillContextResource.TRUSTED_PET_CONTEXT,
    ),
    retryable_failures=(
        SkillFailureCode.MODEL_CALL_FAILED,
        SkillFailureCode.RESPONSE_PARSE_FAILED,
        SkillFailureCode.TIMEOUT,
    ),
    max_attempts=2,
)


CLAIM_FAITHFULNESS_REVIEW_SPEC = _skill_spec(
    skill_id="claim_faithfulness_review",
    skill_version="1.0.0",
    task_kind=SkillTaskKind.CLAIM_FAITHFULNESS_REVIEW,
    execution_family=SkillExecutionFamily.STRUCTURED_REVIEW,
    verifier_id="claim_faithfulness_review_verifier",
    owns=_paths("faithfulness_matrix"),
    output_contract=_claim_faithfulness_review_output_schema(),
    forbidden_output=_paths(
        "claims",
        "verdict",
        "reason",
        "confidence",
        "corrected_proposition",
        "evidence",
        "assertion_state",
        "canonical_id",
        "entity_id",
        "medical_decision",
    ),
    required_context=(
        SkillContextResource.TURN_SNAPSHOT_DIGEST,
        SkillContextResource.ORIGINAL_USER_TEXT,
        SkillContextResource.LAST_ASSISTANT_QUESTIONS,
        SkillContextResource.VERIFIED_PRIOR_FACT_SUMMARY,
        SkillContextResource.TRUSTED_PET_CONTEXT,
    ),
    retryable_failures=(
        SkillFailureCode.MODEL_CALL_FAILED,
        SkillFailureCode.RESPONSE_PARSE_FAILED,
        SkillFailureCode.TIMEOUT,
    ),
    max_attempts=2,
)


CLAIM_INVENTORY_REPAIR_SPEC = _skill_spec(
    skill_id="claim_inventory_repair",
    skill_version="1.0.0",
    task_kind=SkillTaskKind.REPAIR,
    execution_family=SkillExecutionFamily.STRUCTURED_REPAIR,
    verifier_id="claim_inventory_repair_verifier",
    owns=_paths("modified_claims", "added_claims"),
    output_contract=_claim_inventory_repair_output_schema(),
    forbidden_output=_paths(
        "claims",
        "operation",
        "addresses_dimensions",
        "reason",
        "confidence",
        "evidence",
        "medical_decision",
    ),
    required_context=(
        SkillContextResource.TURN_SNAPSHOT_DIGEST,
        SkillContextResource.ORIGINAL_USER_TEXT,
        SkillContextResource.LAST_ASSISTANT_QUESTIONS,
        SkillContextResource.VERIFIED_PRIOR_FACT_SUMMARY,
        SkillContextResource.TRUSTED_PET_CONTEXT,
    ),
    retryable_failures=(
        SkillFailureCode.MODEL_CALL_FAILED,
        SkillFailureCode.RESPONSE_PARSE_FAILED,
        SkillFailureCode.TIMEOUT,
    ),
    max_attempts=2,
)


CLAIM_PROPOSITION_REPAIR_SPEC = _skill_spec(
    skill_id="claim_proposition_repair",
    skill_version="1.0.0",
    task_kind=SkillTaskKind.REPAIR,
    execution_family=SkillExecutionFamily.STRUCTURED_REPAIR,
    verifier_id="claim_proposition_repair_verifier",
    owns=_paths("proposition"),
    output_contract=_claim_proposition_repair_output_schema(),
    forbidden_output=_paths(
        "claims",
        "operation",
        "target",
        "addresses_dimensions",
        "reason",
        "confidence",
        "evidence",
        "medical_decision",
    ),
    required_context=(
        SkillContextResource.TURN_SNAPSHOT_DIGEST,
        SkillContextResource.ORIGINAL_USER_TEXT,
        SkillContextResource.LAST_ASSISTANT_QUESTIONS,
        SkillContextResource.VERIFIED_PRIOR_FACT_SUMMARY,
        SkillContextResource.TRUSTED_PET_CONTEXT,
    ),
    retryable_failures=(
        SkillFailureCode.MODEL_CALL_FAILED,
        SkillFailureCode.RESPONSE_PARSE_FAILED,
        SkillFailureCode.TIMEOUT,
    ),
    max_attempts=2,
)


PATCH_APPLIER_SPEC = _skill_spec(
    skill_id="patch_applier",
    skill_version="1.0.0",
    task_kind=SkillTaskKind.PATCH_APPLY,
    execution_family=SkillExecutionFamily.DETERMINISTIC_PATCH_APPLY,
    verifier_id="patch_applier_verifier",
    owns=_paths(
        "artifact.version",
        "artifact.lineage",
        "artifact.stale",
    ),
    output_contract=_nested_output_schema(
        "semantic_collaboration.patch_applier.output",
        _paths(
            "artifact.version",
            "artifact.lineage",
            "artifact.stale",
        ),
    ),
    forbidden_output=_paths("claims", "medical_decision"),
    required_context=(
        SkillContextResource.TURN_SNAPSHOT_DIGEST,
        SkillContextResource.VERIFIED_PATCH_PROPOSAL,
        SkillContextResource.ARTIFACT_BASE_VERSION,
    ),
)


PRODUCTION_SEMANTIC_SKILL_SPECS: tuple[SkillSpec, ...] = (
    TURN_INTENT_SPEC,
    CLAIM_INVENTORY_SPEC,
    CLAIM_COVERAGE_REVIEW_SPEC,
    CLAIM_FAITHFULNESS_REVIEW_SPEC,
    CLAIM_INVENTORY_REPAIR_SPEC,
    CLAIM_PROPOSITION_REPAIR_SPEC,
    PATCH_APPLIER_SPEC,
)


def build_production_skill_catalog() -> SkillCatalog:
    """构建并冻结受限语义协作生产 SkillCatalog。

    :return: 返回完成全局闭合校验后的不可变生产目录。
    """
    return SkillCatalog(PRODUCTION_SEMANTIC_SKILL_SPECS).freeze()

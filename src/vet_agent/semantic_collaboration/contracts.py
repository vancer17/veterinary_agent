"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/contracts.py
作用：定义受限语义协作 DAG 的 M01 权威 Skill 契约模型。
范围：覆盖 Skill 身份、输入输出 schema、字段所有权、上下文策略、verifier
      绑定、失败策略、修复映射、可观测性与 SKILL.md 投影契约。
说明：本文件是机器可读契约层，不执行图调度、不调用 LLM、不访问数据库，
      也不解析 Markdown 正文作为运行时字段所有权权威。
=============================================================================
"""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import SkillContractError, SkillProjectionError


class SkillTaskKind(StrEnum):
    """表示受限语义协作 DAG 中的正交任务类型。

    :return: 无返回值；该枚举防止按症状词或疾病词注册 SKILL。
    """

    TURN_INTENT = "turn_intent"
    CLAIM_INVENTORY = "claim_inventory"
    STATEMENT_SEMANTICS = "statement_semantics"
    PARTICIPANT_PHRASE = "participant_phrase"
    TEMPORAL_PHRASE = "temporal_phrase"
    MEASUREMENT_PHRASE = "measurement_phrase"
    CANONICAL_DESCRIPTOR = "canonical_descriptor"
    CLAIM_COVERAGE_REVIEW = "claim_coverage_review"
    CLAIM_FAITHFULNESS_REVIEW = "claim_faithfulness_review"
    REPAIR = "repair"
    PATCH_APPLY = "patch_apply"


class SkillExecutionFamily(StrEnum):
    """表示 SKILL 在执行层面的家族类型。

    :return: 无返回值；该枚举用于后续 Gateway、Verifier 与 Patch Applier 分派。
    """

    STRUCTURED_GENERATION = "structured_generation"
    STRUCTURED_REVIEW = "structured_review"
    STRUCTURED_REPAIR = "structured_repair"
    DETERMINISTIC_PATCH_APPLY = "deterministic_patch_apply"


class SkillContextResource(StrEnum):
    """表示 TurnSnapshot 与任务 artifact 中可声明的受限上下文资源。

    :return: 无返回值；该枚举是上下文访问矩阵的唯一稳定命名来源。
    """

    TURN_SNAPSHOT_DIGEST = "turn_snapshot_digest"
    ORIGINAL_USER_TEXT = "original_user_text"
    BOUNDED_CONVERSATION_HISTORY = "bounded_conversation_history"
    LAST_ASSISTANT_QUESTIONS = "last_assistant_questions"
    VERIFIED_PRIOR_FACT_SUMMARY = "verified_prior_fact_summary"
    TRUSTED_PET_CONTEXT = "trusted_pet_context"
    VERIFIED_PEER_ARTIFACT = "verified_peer_artifact"
    VERIFIED_REVIEW_ARTIFACT = "verified_review_artifact"
    VERIFIED_PATCH_PROPOSAL = "verified_patch_proposal"
    ARTIFACT_BASE_VERSION = "artifact_base_version"
    CONSULTATION_STATE = "consultation_state"
    CLINICAL_SAFETY_EVALUATION = "clinical_safety_evaluation"
    CLINICAL_SAFETY_RETRIEVAL = "clinical_safety_retrieval"
    REQUIRED_CONTEXT_EVALUATION = "required_context_evaluation"
    CLINICAL_SAFETY_OPA = "clinical_safety_opa"
    LONG_TERM_MEMORY = "long_term_memory"
    UNVERIFIED_PEER_ARTIFACT = "unverified_peer_artifact"


class SkillFailureCode(StrEnum):
    """表示 SKILL 执行与验证阶段的稳定失败编码。

    :return: 无返回值；该枚举禁止未知失败码进入修复任务。
    """

    MODEL_CALL_FAILED = "model_call_failed"
    RESPONSE_PARSE_FAILED = "response_parse_failed"
    SCHEMA_INVALID = "schema_invalid"
    FORBIDDEN_OUTPUT = "forbidden_output"
    OWNERSHIP_VIOLATION = "ownership_violation"
    CONTEXT_POLICY_VIOLATION = "context_policy_violation"
    CONTEXT_DIGEST_MISMATCH = "context_digest_mismatch"
    EVIDENCE_OUT_OF_SCOPE = "evidence_out_of_scope"
    CLAIM_BINDING_INVALID = "claim_binding_invalid"
    VERIFIER_FAILED = "verifier_failed"
    REVIEW_REJECTED = "review_rejected"
    REPAIR_BUDGET_EXHAUSTED = "repair_budget_exhausted"
    BASE_VERSION_CONFLICT = "base_version_conflict"
    DEPENDENCY_FAILED = "dependency_failed"
    TIMEOUT = "timeout"
    CONTEXT_BUDGET_EXCEEDED = "context_budget_exceeded"


class SkillPatchType(StrEnum):
    """表示修复 SKILL 允许提议的白名单 typed patch 类型。

    :return: 无返回值；该枚举防止自由 JSON Patch 或跨字段重写。
    """

    INTENT_FIELD_PATCH = "intent_field_patch"
    CLAIM_PROPOSITION_PATCH = "claim_proposition_patch"
    STATEMENT_SEMANTICS_PATCH = "statement_semantics_patch"
    PARTICIPANT_PHRASE_PATCH = "participant_phrase_patch"
    TEMPORAL_PHRASE_PATCH = "temporal_phrase_patch"
    MEASUREMENT_PHRASE_PATCH = "measurement_phrase_patch"
    CANONICAL_DESCRIPTOR_PATCH = "canonical_descriptor_patch"
    REVIEW_HINT_PATCH = "review_hint_patch"


class SkillTraceKind(StrEnum):
    """表示 SKILL 在追踪系统中暴露的任务类型。

    :return: 无返回值；该枚举保持 M01 到 M14 观测字段的稳定映射。
    """

    GENERATION_SKILL = "generation_skill"
    REVIEW_SKILL = "review_skill"
    REPAIR_SKILL = "repair_skill"
    PATCH_APPLIER = "patch_applier"


DOMAIN_ISOLATED_CONTEXT_RESOURCES: frozenset[SkillContextResource] = frozenset(
    {
        SkillContextResource.CONSULTATION_STATE,
        SkillContextResource.CLINICAL_SAFETY_EVALUATION,
        SkillContextResource.CLINICAL_SAFETY_RETRIEVAL,
        SkillContextResource.REQUIRED_CONTEXT_EVALUATION,
        SkillContextResource.CLINICAL_SAFETY_OPA,
        SkillContextResource.LONG_TERM_MEMORY,
        SkillContextResource.UNVERIFIED_PEER_ARTIFACT,
    }
)

TURN_SNAPSHOT_CONTEXT_RESOURCES: frozenset[SkillContextResource] = frozenset(
    {
        SkillContextResource.TURN_SNAPSHOT_DIGEST,
        SkillContextResource.ORIGINAL_USER_TEXT,
        SkillContextResource.BOUNDED_CONVERSATION_HISTORY,
        SkillContextResource.LAST_ASSISTANT_QUESTIONS,
        SkillContextResource.VERIFIED_PRIOR_FACT_SUMMARY,
        SkillContextResource.TRUSTED_PET_CONTEXT,
    }
)

SKILL_DOC_REQUIRED_SECTIONS: tuple[str, ...] = (
    "Identity",
    "Scope",
    "Context Policy",
    "Output Authority",
    "Failure And Repair",
    "Safety Boundary",
)


class FieldOwnershipPath(BaseModel):
    """表示 SKILL 输出字段所有权的规范化点分路径。

    :param path: 以小写标识符组成的输出字段路径。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    path: str = Field(
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$",
        min_length=1,
        max_length=240,
        description="输出字段所有权路径；使用点分结构且禁止数组通配符。",
    )

    def segments(self) -> tuple[str, ...]:
        """返回字段所有权路径的分段表示。

        :return: 返回用于冲突检测的路径分段元组。
        """
        return tuple(self.path.split("."))

    def conflicts_with(self, other: Self) -> bool:
        """判断当前所有权路径与另一个路径是否存在权威覆盖冲突。

        :param other: 另一个待比较的字段所有权路径。
        :return: 路径相同或存在父子覆盖时返回 True。
        """
        left = self.segments()
        right = other.segments()
        shared_length = min(len(left), len(right))
        return left[:shared_length] == right[:shared_length]


class SchemaContract(BaseModel):
    """表示 SKILL 输入或输出的机器可读 JSON Schema 契约。

    :param schema_id: 稳定 schema 标识。
    :param schema_version: 语义化 schema 版本。
    :param json_schema: JSON Schema 字典。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: str = Field(
        pattern=r"^[a-z0-9][a-z0-9._/-]*$",
        min_length=1,
        max_length=200,
        description="输入或输出 schema 的稳定标识。",
    )
    schema_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$",
        description="输入或输出 schema 的语义化版本。",
    )
    json_schema: dict[str, Any] = Field(
        description="以 JSON object 为根的严格 schema 定义。",
    )

    @field_validator("json_schema")
    @classmethod
    def validate_json_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        """校验 JSON Schema 的最小权威结构。

        :param value: 待校验的 schema 字典。
        :return: 返回通过基础结构校验的 schema 字典。
        :raises SkillContractError: schema 根类型不是 object 时抛出。
        """
        if value.get("type") != "object":
            raise SkillContractError("schema root must be an object")
        if not isinstance(value.get("properties"), dict):
            raise SkillContractError("schema root must declare properties")
        required = value.get("required", [])
        if not isinstance(required, list) or any(
            not isinstance(item, str) for item in required
        ):
            raise SkillContractError("schema required fields must be string array")
        if len(required) != len(set(required)):
            raise SkillContractError("schema required fields must be unique")
        if not set(required).issubset(value["properties"]):
            raise SkillContractError("schema required field is not declared")
        if value.get("additionalProperties") is not False:
            raise SkillContractError("schema must forbid additional properties")
        return value

    def supports_path(self, path: FieldOwnershipPath) -> bool:
        """判断当前 schema 是否包含指定字段路径。

        :param path: 字段所有权路径。
        :return: 路径可由 object properties 逐级解析时返回 True。
        """
        current: Any = self.json_schema
        for segment in path.segments():
            if not isinstance(current, dict):
                return False
            properties = current.get("properties")
            if not isinstance(properties, dict) or segment not in properties:
                return False
            current = properties[segment]
        return True

    def canonical_json(self) -> str:
        """生成 schema 契约的稳定 JSON 表示。

        :return: 返回排序键、紧凑分隔且保留 Unicode 的 JSON 字符串。
        """
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


class ContextContract(BaseModel):
    """表示 SKILL 可读取的受限上下文与强制禁止上下文。

    :param required_resources: 任务执行必须存在的 TurnSnapshot 或 artifact 资源。
    :param forbidden_resources: 领域隔离与未验证输出禁止项。
    :param requires_snapshot_digest: 是否必须绑定 TurnSnapshot digest。
    :param max_context_chars: 上下文文本预算上限。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    required_resources: tuple[SkillContextResource, ...] = Field(
        min_length=1,
        description="SKILL 执行所需的受限只读上下文资源。",
    )
    forbidden_resources: tuple[SkillContextResource, ...] = Field(
        min_length=1,
        description="SKILL 永远不得读取的领域隔离或未验证资源。",
    )
    requires_snapshot_digest: bool = Field(
        default=True,
        description="是否要求所有输入共享同一个 TurnSnapshot digest。",
    )
    max_context_chars: int = Field(
        default=20000,
        ge=1,
        le=200000,
        description="单个 SKILL 可见上下文的字符数上限。",
    )

    @model_validator(mode="after")
    def validate_resources(self) -> Self:
        """校验上下文需求与禁止项不存在交叉且领域隔离完整。

        :return: 返回通过一致性校验的上下文契约。
        :raises SkillContractError: 上下文交叉或缺少领域隔离时抛出。
        """
        required = set(self.required_resources)
        forbidden = set(self.forbidden_resources)
        if required & forbidden:
            raise SkillContractError(
                "context resource cannot be both required and forbidden"
            )
        if not DOMAIN_ISOLATED_CONTEXT_RESOURCES.issubset(forbidden):
            missing = sorted(
                resource.value
                for resource in DOMAIN_ISOLATED_CONTEXT_RESOURCES - forbidden
            )
            raise SkillContractError(
                f"skill context contract missing domain isolation: {', '.join(missing)}"
            )
        if self.requires_snapshot_digest and (
            SkillContextResource.TURN_SNAPSHOT_DIGEST not in required
        ):
            raise SkillContractError("snapshot digest resource is required")
        return self


class VerifierBinding(BaseModel):
    """表示 SKILL 与确定性 verifier 的稳定绑定声明。

    :param verifier_id: verifier 稳定标识。
    :param verifier_version: verifier 语义化版本。
    :param accepted_task_kinds: verifier 可承接的任务类型集合。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    verifier_id: str = Field(
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
        min_length=1,
        max_length=160,
        description="确定性 verifier 的稳定标识。",
    )
    verifier_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$",
        description="确定性 verifier 的语义化版本。",
    )
    accepted_task_kinds: tuple[SkillTaskKind, ...] = Field(
        min_length=1,
        description="该 verifier 声明支持的任务类型集合。",
    )


class FailurePolicy(BaseModel):
    """表示 SKILL 失败码、重试与终态策略。

    :param terminal_on: 必须进入显式终态的失败码。
    :param retryable_on: 允许有界重试的失败码。
    :param max_attempts: 单任务最大执行尝试次数。
    :param timeout_ms: 单次执行超时时间。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    terminal_on: tuple[SkillFailureCode, ...] = Field(
        min_length=1,
        description="不可通过重试掩盖的显式终态失败码。",
    )
    retryable_on: tuple[SkillFailureCode, ...] = Field(
        default=(),
        description="允许受控重试且必须保留 attempt 审计的失败码。",
    )
    max_attempts: int = Field(default=1, ge=1, le=3)
    timeout_ms: int = Field(default=30000, ge=100, le=180000)

    @model_validator(mode="after")
    def validate_failure_states(self) -> Self:
        """校验终态与可重试失败码互斥。

        :return: 返回通过校验的失败策略。
        :raises SkillContractError: 同一失败码同时终态和可重试时抛出。
        """
        if set(self.terminal_on) & set(self.retryable_on):
            raise SkillContractError("failure code cannot be terminal and retryable")
        return self


class RepairMapping(BaseModel):
    """表示失败码到白名单修复 SKILL 与 patch 类型的映射。

    :param failure_code: 允许进入修复流程的稳定失败码。
    :param repair_skill_id: 已注册修复 SKILL 标识。
    :param repair_skill_version: 已注册修复 SKILL 版本。
    :param allowed_patch_types: 该失败允许提议的 typed patch 类型。
    :param max_repairs: 单失败码修复预算。
    :param requires_base_version: 是否必须声明 artifact base version。
    :param marks_downstream_stale: 修复后是否标记下游 stale。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    failure_code: SkillFailureCode = Field(description="可修复的稳定失败码。")
    repair_skill_id: str = Field(
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
        min_length=1,
        max_length=120,
        description="目标修复 SKILL 的稳定标识。",
    )
    repair_skill_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$",
        description="目标修复 SKILL 的语义化版本。",
    )
    allowed_patch_types: tuple[SkillPatchType, ...] = Field(
        min_length=1,
        description="该失败码允许使用的白名单 patch 类型。",
    )
    max_repairs: int = Field(default=1, ge=1, le=3)
    requires_base_version: bool = Field(default=True)
    marks_downstream_stale: bool = Field(default=True)

    @model_validator(mode="after")
    def validate_repair_boundary(self) -> Self:
        """校验修复映射必须携带版本与 stale 治理。

        :return: 返回通过修复边界校验的映射。
        :raises SkillContractError: 缺少 base version 或 stale 策略时抛出。
        """
        if not self.requires_base_version:
            raise SkillContractError("repair mapping must require base version")
        if not self.marks_downstream_stale:
            raise SkillContractError("repair mapping must mark downstream stale")
        return self


class SkillObservabilityContract(BaseModel):
    """表示 SKILL 必须输出的审计与追踪字段。

    :param trace_kind: 稳定追踪任务类型。
    :param requires_model_snapshot: 是否记录模型快照。
    :param requires_usage: 是否记录 token 用量。
    :param requires_prompt_hash: 是否记录 prompt 投影哈希。
    :param requires_contract_hash: 是否记录 SkillSpec 契约哈希。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_kind: SkillTraceKind = Field(description="观测系统中的稳定任务类型。")
    requires_model_snapshot: bool = Field(default=True)
    requires_usage: bool = Field(default=True)
    requires_prompt_hash: bool = Field(default=True)
    requires_contract_hash: bool = Field(default=True)


class SkillDocProjection(BaseModel):
    """表示 SKILL.md 提示词投影的启动期一致性契约。

    :param document: Markdown 投影全文。
    :param content_sha256: 投影内容摘要。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    document: str = Field(min_length=1, description="SKILL.md 提示词投影全文。")
    content_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="Markdown 投影内容的 SHA-256 摘要。",
    )

    @field_validator("document")
    @classmethod
    def validate_sections(cls, value: str) -> str:
        """校验 Markdown 投影包含必需段落。

        :param value: Markdown 投影全文。
        :return: 返回包含必需段落的 Markdown 文档。
        :raises SkillProjectionError: 缺少必需段落时抛出。
        """
        lines = value.splitlines()
        headings = {
            line.removeprefix("## ").strip() for line in lines if line.startswith("## ")
        }
        missing = [
            section
            for section in SKILL_DOC_REQUIRED_SECTIONS
            if section not in headings
        ]
        if missing:
            raise SkillProjectionError(
                f"skill projection missing sections: {', '.join(missing)}"
            )
        return value

    @model_validator(mode="after")
    def validate_digest(self) -> Self:
        """校验投影摘要与内容一致。

        :return: 返回摘要一致的 SKILL.md 投影。
        :raises SkillProjectionError: 摘要不匹配时抛出。
        """
        actual = sha256(self.document.encode("utf-8")).hexdigest()
        if actual != self.content_sha256:
            raise SkillProjectionError("skill projection digest mismatch")
        return self

    def frontmatter(self) -> dict[str, str]:
        """读取 Markdown 顶层元数据用于启动期一致性检查。

        :return: 返回 frontmatter 键值字典。
        :raises SkillProjectionError: frontmatter 结构非法时抛出。
        """
        lines = self.document.splitlines()
        if not lines or lines[0] != "---":
            raise SkillProjectionError("skill projection must start with frontmatter")
        try:
            end_index = lines[1:].index("---") + 1
        except ValueError as exc:
            raise SkillProjectionError(
                "skill projection frontmatter is unterminated"
            ) from exc
        metadata: dict[str, str] = {}
        for line in lines[1:end_index]:
            key, separator, raw_value = line.partition(":")
            if not separator or not key or not raw_value:
                raise SkillProjectionError(
                    "skill projection frontmatter line is invalid"
                )
            metadata[key.strip()] = raw_value.strip()
        return metadata

    def validate_against_spec(self, spec: SkillSpec) -> None:
        """校验投影元数据与权威 SkillSpec 一致。

        :param spec: 权威机器可读 SKILL 契约。
        :return: 无返回值。
        :raises SkillProjectionError: 身份、任务或 verifier 元数据不一致时抛出。
        """
        metadata = self.frontmatter()
        expected = {
            "skill_id": spec.skill_id,
            "skill_version": spec.skill_version,
            "task_kind": spec.task_kind.value,
            "verifier_id": spec.verifier_binding.verifier_id,
            "verifier_version": spec.verifier_binding.verifier_version,
        }
        for key, value in expected.items():
            if metadata.get(key) != value:
                raise SkillProjectionError(
                    f"skill projection {key} does not match SkillSpec"
                )


class SkillSpec(BaseModel):
    """表示受限语义协作 DAG 中单个生产 SKILL 的权威契约。

    :param skill_id: SKILL 稳定标识。
    :param skill_version: SKILL 语义化版本。
    :param contract_version: SkillSpec 契约格式版本。
    :param task_kind: 正交任务类型。
    :param execution_family: 执行家族类型。
    :param input_contract: 输入 schema 契约。
    :param output_contract: 输出 schema 契约。
    :param owns: 当前 SKILL 拥有权威写权的输出字段。
    :param does_not_own: 显式声明不拥有的字段。
    :param forbidden_output: 禁止输出的字段。
    :param context_contract: 受限上下文契约。
    :param verifier_binding: 确定性 verifier 绑定。
    :param failure_policy: 失败与重试策略。
    :param repair_mappings: 可修复失败码映射。
    :param prompt_projection: SKILL.md 启动期投影。
    :param observability: 审计字段契约。
    :return: 无返回值。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
        str_strip_whitespace=True,
    )

    skill_id: str = Field(
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
        min_length=1,
        max_length=120,
        description="生产 SKILL 的稳定标识。",
    )
    skill_version: str = Field(
        pattern=r"^\d+\.\d+\.\d+$",
        description="生产 SKILL 的语义化版本。",
    )
    contract_version: str = Field(
        default="1.0.0",
        pattern=r"^\d+\.\d+\.\d+$",
        description="SkillSpec 契约格式版本。",
    )
    task_kind: SkillTaskKind = Field(description="正交语义任务类型。")
    execution_family: SkillExecutionFamily = Field(description="执行家族类型。")
    input_contract: SchemaContract = Field(description="输入 JSON Schema 契约。")
    output_contract: SchemaContract = Field(description="输出 JSON Schema 契约。")
    owns: tuple[FieldOwnershipPath, ...] = Field(
        min_length=1,
        description="当前 SKILL 的权威字段集合。",
    )
    does_not_own: tuple[FieldOwnershipPath, ...] = Field(
        default=(),
        description="显式声明不拥有的输出字段集合。",
    )
    forbidden_output: tuple[FieldOwnershipPath, ...] = Field(
        min_length=1,
        description="当前 SKILL 禁止输出的字段集合。",
    )
    context_contract: ContextContract = Field(description="受限上下文访问契约。")
    verifier_binding: VerifierBinding = Field(description="确定性 verifier 绑定。")
    failure_policy: FailurePolicy = Field(description="失败与重试策略。")
    repair_mappings: tuple[RepairMapping, ...] = Field(
        default=(),
        description="失败码到白名单修复任务的映射。",
    )
    prompt_projection: SkillDocProjection = Field(
        description="仅面向提示词与审计的 SKILL.md 投影。",
    )
    observability: SkillObservabilityContract = Field(
        description="SKILL 审计与追踪字段契约。",
    )

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        """执行单个 SkillSpec 的权威边界校验。

        :return: 返回可注册的权威 SKILL 契约。
        :raises SkillContractError: 所有权、schema、verifier 或投影冲突时抛出。
        """
        owned = list(self.owns)
        if len({item.path for item in owned}) != len(owned):
            raise SkillContractError("duplicate owned field path")
        for owned_path in self.owns:
            for excluded_path in self.does_not_own:
                if owned_path.conflicts_with(excluded_path):
                    raise SkillContractError(
                        "field cannot be owned and explicitly not owned"
                    )
        for owned_path in self.owns:
            for forbidden_path in self.forbidden_output:
                if owned_path.conflicts_with(forbidden_path):
                    raise SkillContractError("field cannot be owned and forbidden")
        if self.task_kind not in self.verifier_binding.accepted_task_kinds:
            raise SkillContractError("verifier does not accept skill task kind")
        for path in self.owns:
            if not self.output_contract.supports_path(path):
                raise SkillContractError(
                    f"owned path is absent from output schema: {path.path}"
                )
        if (
            self.execution_family == SkillExecutionFamily.STRUCTURED_GENERATION
            and self.observability.trace_kind != SkillTraceKind.GENERATION_SKILL
        ):
            raise SkillContractError("generation skill has invalid trace kind")
        if (
            self.execution_family == SkillExecutionFamily.STRUCTURED_REVIEW
            and self.observability.trace_kind != SkillTraceKind.REVIEW_SKILL
        ):
            raise SkillContractError("review skill has invalid trace kind")
        if (
            self.execution_family == SkillExecutionFamily.STRUCTURED_REPAIR
            and self.observability.trace_kind != SkillTraceKind.REPAIR_SKILL
        ):
            raise SkillContractError("repair skill has invalid trace kind")
        if self.repair_mappings and self.task_kind == SkillTaskKind.REPAIR:
            raise SkillContractError("repair skill cannot recursively map repairs")
        for mapping in self.repair_mappings:
            if mapping.failure_code in self.failure_policy.terminal_on:
                raise SkillContractError("terminal failure cannot map to repair")
        self.prompt_projection.validate_against_spec(self)
        return self

    def identity(self) -> tuple[str, str]:
        """返回 SKILL 的稳定身份键。

        :return: 返回 skill_id 与 skill_version 组成的元组。
        """
        return self.skill_id, self.skill_version

    def canonical_json(self) -> str:
        """生成 SkillSpec 的稳定契约 JSON 表示。

        :return: 返回用于目录摘要与版本审计的排序 JSON 字符串。
        """
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

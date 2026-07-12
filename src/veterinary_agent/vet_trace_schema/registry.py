##################################################################################################
# 文件: src/veterinary_agent/vet_trace_schema/registry.py
# 作用: 提供 VetTraceSchema 版本化 schema bundle、capture policy 注册表与 JSON Schema 校验能力。
# 边界: 仅管理本组件本地资源包和结构化校验，不访问 LogicTraceStore、不读取 RuntimeConfig、不执行业务推理。
##################################################################################################

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Final, TypeAlias, cast

from jsonschema import SchemaError, ValidationError
from jsonschema.protocols import Validator
from jsonschema.validators import validator_for

from veterinary_agent.vet_trace_schema.dto import (
    JsonMap,
    VetTraceCapturePolicyDto,
    VetTracePatchSchemaDto,
    VetTraceSchemaBundleDto,
)
from veterinary_agent.vet_trace_schema.enums import (
    VetTraceAuditTier,
    VetTraceErrorCode,
    VetTraceOperation,
)
from veterinary_agent.vet_trace_schema.errors import VetTraceSchemaError

DEFAULT_TRACE_SCHEMA_VERSION: Final[str] = "vet-trace-schema.v1"
DEFAULT_CAPTURE_POLICY_VERSION: Final[str] = "vet-trace-capture-policy.v1"
DEFAULT_PATCH_SCHEMA_REF: Final[str] = "vet.trace.patch.v1"
JsonSchemaValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | dict[str, "JsonSchemaValue"]
    | list["JsonSchemaValue"]
)

_BASE_JSON_SCHEMA: Final[JsonMap] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "audit_tier": {"enum": ["A", "B", "C"]},
        "segment_type": {"type": "string"},
        "generation_profile": {"type": "string"},
        "signals": {"type": "array", "items": {"type": "string"}},
        "guard_actions": {"type": "array"},
        "artifact_refs": {"type": "array", "items": {"type": "string"}},
        "final_response_ref": {"type": "string"},
        "draft_response_ref": {"type": "string"},
        "reviewed_draft_ref": {"type": "string"},
        "rag_used": {"type": "boolean"},
        "ocr_used": {"type": "boolean"},
        "fallback_triggered": {"type": "boolean"},
    },
}

_PATCH_SCHEMA_REFS: Final[tuple[tuple[str, str], ...]] = (
    (DEFAULT_PATCH_SCHEMA_REF, "*"),
    ("vet.task-decomposition.trace.v1", "task_decomposition"),
    ("vet.input-safety.trace.v1", "input_safety"),
    ("vet.context-builder.trace.v1", "context_builder"),
    ("vet.generation.trace.v1", "generation"),
    ("vet.output-review.trace.v1", "output_review"),
    ("vet.response-composer.trace.v1", "response_composer"),
    ("vet.reasoning-display.trace.v1", "reasoning_display"),
)

_COMMON_REDACT_FIELDS: Final[list[str]] = [
    "chain_of_thought",
    "hidden_chain_of_thought",
    "raw_chain_of_thought",
    "raw_prompt",
    "system_prompt",
    "developer_prompt",
    "dangerous_draft",
    "blocked_content",
]


def _json_schema_copy() -> JsonMap:
    """复制默认 JSON Schema 模板。

    :return: 独立的 JSON Schema 映射，避免调用方修改模块级常量。
    """

    return cast(JsonMap, deepcopy(_BASE_JSON_SCHEMA))


def _build_default_patch_schemas() -> list[VetTracePatchSchemaDto]:
    """构建默认 patch schema 资源列表。

    :return: 默认 VetTraceSchema 资源包内置的 payload schema 列表。
    """

    return [
        VetTracePatchSchemaDto(
            schema_ref=schema_ref,
            patch_type=patch_type,
            schema_version=schema_ref,
            json_schema=_json_schema_copy(),
        )
        for schema_ref, patch_type in _PATCH_SCHEMA_REFS
    ]


def _build_default_capture_policies() -> list[VetTraceCapturePolicyDto]:
    """构建默认 A/B/C capture policy 资源列表。

    :return: 默认 VetTraceSchema 资源包内置的 capture policy 列表。
    """

    return [
        VetTraceCapturePolicyDto(
            policy_version=DEFAULT_CAPTURE_POLICY_VERSION,
            audit_tier=VetTraceAuditTier.A,
            required_patch_types=["output_review", "response_composer"],
            required_artifact_types=["guard_triple"],
            redact_fields=[*_COMMON_REDACT_FIELDS, "draft_response", "reviewed_draft"],
            projection_views=[
                "timeline_view",
                "decision_view",
                "artifact_view",
                "reasoning_display",
            ],
        ),
        VetTraceCapturePolicyDto(
            policy_version=DEFAULT_CAPTURE_POLICY_VERSION,
            audit_tier=VetTraceAuditTier.B,
            required_patch_types=[],
            required_artifact_types=[],
            redact_fields=[*_COMMON_REDACT_FIELDS, "draft_response"],
            projection_views=["timeline_view", "decision_view", "reasoning_display"],
        ),
        VetTraceCapturePolicyDto(
            policy_version=DEFAULT_CAPTURE_POLICY_VERSION,
            audit_tier=VetTraceAuditTier.C,
            required_patch_types=[],
            required_artifact_types=[],
            redact_fields=[*_COMMON_REDACT_FIELDS, "draft_response", "medical_claims"],
            projection_views=["timeline_view"],
        ),
    ]


def _build_json_schema_validator(schema: JsonMap) -> Validator:
    """构建适配 JSON Schema 草案版本的 validator。

    :param schema: 待编译的 JSON Schema 文档。
    :return: 已完成 schema 自校验的 jsonschema validator。
    :raises SchemaError: 当 JSON Schema 自身不合法时抛出。
    """

    validator_cls = validator_for(schema)
    validator_cls.check_schema(schema)
    return validator_cls(schema)


def _format_json_path(path_parts: Iterable[object]) -> str:
    """格式化 jsonschema 错误路径。

    :param path_parts: jsonschema ValidationError.path 提供的路径片段集合。
    :return: 以 ``$`` 开头的稳定 JSONPath 文本。
    """

    path = "$"
    for part in path_parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path


def create_default_vet_trace_schema_bundle() -> VetTraceSchemaBundleDto:
    """创建默认 VetTraceSchema 版本化资源包。

    :return: 包含内置 patch schema 与 A/B/C capture policy 的资源包。
    """

    return VetTraceSchemaBundleDto(
        trace_schema_version=DEFAULT_TRACE_SCHEMA_VERSION,
        capture_policy_version=DEFAULT_CAPTURE_POLICY_VERSION,
        default_schema_ref=DEFAULT_PATCH_SCHEMA_REF,
        patch_schemas=_build_default_patch_schemas(),
        capture_policies=_build_default_capture_policies(),
    )


class VetTraceSchemaRegistry:
    """VetTraceSchema 版本化 schema 与 capture policy 注册表。"""

    def __init__(self, *, bundle: VetTraceSchemaBundleDto | None = None) -> None:
        """初始化 VetTraceSchema 注册表。

        :param bundle: 可选版本化 schema 资源包；未传入时使用内置默认资源包。
        :return: None。
        """

        self._bundle = (
            bundle if bundle is not None else create_default_vet_trace_schema_bundle()
        )
        self._schemas_by_ref = {
            schema.schema_ref: schema for schema in self._bundle.patch_schemas
        }
        self._schema_refs_by_patch_type = {
            schema.patch_type: schema.schema_ref
            for schema in self._bundle.patch_schemas
            if schema.patch_type != "*"
        }
        self._policies_by_key = {
            (policy.policy_version, policy.audit_tier): policy
            for policy in self._bundle.capture_policies
        }
        self._validators = self._compile_validators()

    @property
    def bundle(self) -> VetTraceSchemaBundleDto:
        """读取当前注册表使用的 schema 资源包。

        :return: 当前 VetTraceSchema 资源包。
        """

        return self._bundle

    def is_ready(self) -> bool:
        """判断注册表是否具备校验能力。

        :return: 若默认 schema、至少一个 schema validator 和 A/B/C 策略均可用，则返回 True。
        """

        expected_policy_keys = {
            (self._bundle.capture_policy_version, VetTraceAuditTier.A),
            (self._bundle.capture_policy_version, VetTraceAuditTier.B),
            (self._bundle.capture_policy_version, VetTraceAuditTier.C),
        }
        return (
            self._bundle.default_schema_ref in self._schemas_by_ref
            and bool(self._validators)
            and expected_policy_keys.issubset(self._policies_by_key)
        )

    def _compile_validators(self) -> dict[str, Validator]:
        """编译当前资源包中的 JSON Schema validator。

        :return: schema 引用到 jsonschema validator 的映射。
        :raises VetTraceSchemaError: 当任一 JSON Schema 资源非法时抛出。
        """

        validators: dict[str, Validator] = {}
        for schema in self._bundle.patch_schemas:
            try:
                validators[schema.schema_ref] = _build_json_schema_validator(
                    schema.json_schema
                )
            except SchemaError as exc:
                raise VetTraceSchemaError(
                    code=VetTraceErrorCode.VET_TRACE_SCHEMA_RESOURCE_UNAVAILABLE,
                    operation=VetTraceOperation.LOAD_SCHEMA_BUNDLE,
                    message="VetTraceSchema JSON Schema 资源非法",
                    retryable=False,
                    conflict_with={
                        "schema_ref": schema.schema_ref,
                        "reason": exc.message,
                    },
                ) from exc
        return validators

    def resolve_schema_ref(
        self,
        *,
        schema_ref: str | None,
        patch_type: str,
    ) -> str:
        """解析业务 patch 应使用的 payload schema 引用。

        :param schema_ref: 上游声明的 schema 引用。
        :param patch_type: 当前业务 patch 类型。
        :return: 已存在于注册表中的 schema 引用。
        :raises VetTraceSchemaError: 当 schema 引用不存在时抛出。
        """

        if schema_ref is not None and schema_ref in self._schemas_by_ref:
            return schema_ref
        schema_ref_from_patch_type = self._schema_refs_by_patch_type.get(patch_type)
        if schema_ref_from_patch_type is not None:
            return schema_ref_from_patch_type
        if (
            schema_ref is None
            and self._bundle.default_schema_ref in self._schemas_by_ref
        ):
            return self._bundle.default_schema_ref
        raise VetTraceSchemaError(
            code=VetTraceErrorCode.VET_TRACE_SCHEMA_VERSION_NOT_FOUND,
            operation=VetTraceOperation.VALIDATE_TRACE_PATCH,
            message="业务 trace patch 声明的 schema 不存在",
            retryable=False,
            conflict_with={"schema_ref": schema_ref, "patch_type": patch_type},
        )

    def resolve_capture_policy(
        self,
        *,
        audit_tier: VetTraceAuditTier,
        policy_version: str,
    ) -> VetTraceCapturePolicyDto:
        """解析指定审计等级的 capture policy。

        :param audit_tier: 当前业务 patch 的审计等级。
        :param policy_version: capture policy 版本。
        :return: 匹配的 capture policy 资源。
        :raises VetTraceSchemaError: 当 capture policy 不存在时抛出。
        """

        policy = self._policies_by_key.get((policy_version, audit_tier))
        if policy is not None:
            return policy
        raise VetTraceSchemaError(
            code=VetTraceErrorCode.VET_TRACE_CAPTURE_POLICY_NOT_FOUND,
            operation=VetTraceOperation.APPLY_CAPTURE_POLICY,
            message="VetTraceSchema capture policy 不存在",
            retryable=False,
            conflict_with={
                "audit_tier": audit_tier.value,
                "policy_version": policy_version,
            },
        )

    def validate_payload(
        self, *, schema_ref: str, payload: Mapping[str, object]
    ) -> list[str]:
        """使用指定 JSON Schema 校验业务 patch payload。

        :param schema_ref: 需要使用的 payload schema 引用。
        :param payload: 待校验的业务 payload。
        :return: 校验错误列表；空列表表示通过。
        :raises VetTraceSchemaError: 当 schema 引用不存在时抛出。
        """

        validator = self._validators.get(schema_ref)
        if validator is None:
            raise VetTraceSchemaError(
                code=VetTraceErrorCode.VET_TRACE_SCHEMA_VERSION_NOT_FOUND,
                operation=VetTraceOperation.VALIDATE_TRACE_PATCH,
                message="payload schema validator 不存在",
                retryable=False,
                conflict_with={"schema_ref": schema_ref},
            )
        errors = sorted(
            validator.iter_errors(cast(JsonSchemaValue, dict(payload))),
            key=lambda error: list(cast(ValidationError, error).path),
        )
        return [f"{_format_json_path(error.path)}: {error.message}" for error in errors]


__all__: tuple[str, ...] = (
    "DEFAULT_CAPTURE_POLICY_VERSION",
    "DEFAULT_PATCH_SCHEMA_REF",
    "DEFAULT_TRACE_SCHEMA_VERSION",
    "VetTraceSchemaRegistry",
    "create_default_vet_trace_schema_bundle",
)

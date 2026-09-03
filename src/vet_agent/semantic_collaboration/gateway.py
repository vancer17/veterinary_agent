"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/gateway.py
作用：实现受限语义协作 DAG M05 结构化 LLM Gateway。
范围：覆盖 Skill 契约解析、schema digest 校验、prompt 哈希、单次模型传输、
      严格 JSON object 解析、权威 JSON Schema 校验和 model proposal 生成。
说明：本文件不判断医学语义、不执行 M07 verifier、不提交 M11 artifact、
      不做内部重试或 fallback、不访问问诊 / 临床安全 / 长期记忆状态。
=============================================================================
"""

from __future__ import annotations

import json
from hashlib import sha256
from time import perf_counter
from typing import Any, NoReturn

from jsonschema import Draft202012Validator

from .catalog import SkillRegistry
from .contracts import SkillExecutionFamily, SkillSpec
from .errors import (
    StructuredLLMGatewayContractError,
    StructuredLLMModelCallError,
    StructuredLLMResponseParseError,
    StructuredLLMSchemaError,
)
from .gateway_contracts import (
    SemanticModelProposal,
    StructuredLLMCallMetadata,
    StructuredLLMCallRequest,
    StructuredModelTransport,
    StructuredModelTransportResponse,
)
from .plan_contracts import schema_reference_matches

_GATEWAY_EXECUTION_FAMILIES: tuple[SkillExecutionFamily, ...] = (
    SkillExecutionFamily.STRUCTURED_GENERATION,
    SkillExecutionFamily.TYPED_REPAIR,
)


def canonical_gateway_json(payload: object) -> str:
    """生成 M05 输入输出审计对象的 canonical JSON。

    :param payload: 待序列化的 prompt、schema 或 proposal 对象。
    :return: 返回排序键、紧凑分隔且保留 Unicode 的 JSON 字符串。
    :raises TypeError: 对象无法被 JSON 序列化时抛出。
    """
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def compute_gateway_digest(payload: object) -> str:
    """计算 M05 审计对象的 canonical SHA-256 摘要。

    :param payload: 待摘要的 prompt、schema 或 proposal 对象。
    :return: 返回 64 位小写十六进制摘要。
    :raises TypeError: 对象无法被 JSON 序列化时抛出。
    :raises ValueError: 对象包含 NaN 或 Infinity 时抛出。
    """
    return sha256(canonical_gateway_json(payload).encode("utf-8")).hexdigest()


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """拒绝 JSON object 中的重复键。

    :param pairs: JSON 解析器传入的键值对列表。
    :return: 返回无重复键的 object 字典。
    :raises ValueError: 发现重复键时抛出。
    """
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_non_finite_json_constant(value: str) -> NoReturn:
    """拒绝 JSON 标准以外的 NaN 与 Infinity 常量。

    :param value: JSON 解析器传入的非常量字符串。
    :return: 无返回值；该函数总是抛出解析失败。
    :raises ValueError: 内容包含 NaN 或 Infinity 时抛出。
    """
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _parse_model_payload(content: object | None) -> dict[str, object]:
    """把模型 structured content 解析为严格 JSON object。

    :param content: 底层传输返回的原始内容。
    :return: 返回根节点为 object 的待校验 payload。
    :raises StructuredLLMResponseParseError: 内容缺失、非 JSON 或根节点非法时抛出。
    """
    try:
        if isinstance(content, dict):
            payload: object = content
        elif isinstance(content, str):
            payload = json.loads(
                content,
                object_pairs_hook=_reject_duplicate_json_keys,
                parse_constant=_reject_non_finite_json_constant,
            )
        else:
            raise TypeError("structured content must be a JSON object or JSON string")
        if not isinstance(payload, dict):
            raise TypeError("structured content root must be a JSON object")
        canonical_gateway_json(payload)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StructuredLLMResponseParseError(
            "structured model response is not a strict JSON object",
        ) from exc
    return payload


def _schema_violation_path(error: Any) -> str:
    """读取 JSON Schema 校验错误的稳定字段路径。

    :param error: jsonschema 输出的 ValidationError。
    :return: 返回根节点或点分字段路径。
    """
    fragments = [str(item) for item in error.absolute_path]
    return ".".join(fragments) or "$"


def _contains_remote_schema_reference(value: object) -> bool:
    """判断 JSON Schema 是否包含远程引用。

    :param value: 任意 JSON Schema 节点。
    :return: 发现远程 `$ref`、`$dynamicRef` 或 `$schema` 时返回 True。
    """
    if isinstance(value, list):
        return any(_contains_remote_schema_reference(item) for item in value)
    if not isinstance(value, dict):
        return False
    for key in ("$ref", "$dynamicRef", "$schema"):
        reference = value.get(key)
        if (
            isinstance(reference, str)
            and ("://" in reference or reference.startswith("urn:"))
        ):
            return True
    return any(
        _contains_remote_schema_reference(item)
        for item in value.values()
        if isinstance(item, (dict, list))
    )


class StructuredLLMGateway:
    """表示受限语义协作 DAG 的 M05 结构化模型调用边界。

    :return: 无返回值；该网关只生成未验证 proposal，不产生业务终态。
    """

    def __init__(
        self,
        *,
        registry: SkillRegistry,
        transport: StructuredModelTransport,
    ) -> None:
        """初始化绑定权威 SkillCatalog 与单次模型传输端口的网关。

        :param registry: 已冻结的生产 SkillCatalog 只读注册门面。
        :param transport: 不做内部重试或隐藏 fallback 的结构化传输端口。
        :return: 无返回值。
        """
        self.registry = registry
        self.transport = transport

    async def generate(
        self,
        request: StructuredLLMCallRequest,
    ) -> SemanticModelProposal:
        """执行一次受限 SKILL 结构化模型调用。

        :param request: 绑定任务、attempt、prompt 和输出 schema 的调用请求。
        :return: 返回尚未经过 M07 验证的 SemanticModelProposal。
        :raises StructuredLLMGatewayContractError: Skill、schema、prompt 或上下文契约错配时抛出。
        :raises StructuredLLMModelCallError: 模型传输、finish reason 或模型快照失败时抛出。
        :raises StructuredLLMResponseParseError: 模型内容不是严格 JSON object 时抛出。
        :raises StructuredLLMSchemaError: 模型 payload 未通过权威输出 schema 时抛出。
        """
        spec = self._resolve_contract(request)
        validator = self._validator(spec)
        prompt_hash = compute_gateway_digest(
            [message.model_dump(mode="json") for message in request.prompt.messages],
        )
        started_at = perf_counter()
        try:
            response = await self.transport.structured_once(
                [
                    {
                        "role": message.role,
                        "content": message.content,
                    }
                    for message in request.prompt.messages
                ],
                json_schema=spec.output_contract.json_schema,
                schema_name=self._schema_name(spec),
                model=request.model,
                temperature=request.temperature,
                timeout_seconds=request.timeout_seconds,
            )
        except Exception as exc:
            latency_ms = (perf_counter() - started_at) * 1000
            metadata = self._metadata(
                request=request,
                spec=spec,
                prompt_hash=prompt_hash,
                response=None,
                latency_ms=latency_ms,
            )
            raise StructuredLLMModelCallError(
                "structured model transport call failed",
                metadata=metadata,
            ) from exc
        latency_ms = (perf_counter() - started_at) * 1000
        metadata = self._metadata(
            request=request,
            spec=spec,
            prompt_hash=prompt_hash,
            response=response,
            latency_ms=latency_ms,
        )
        self._validate_transport_response(request, response, metadata)
        payload = _parse_model_payload(response.content)
        errors = list(validator.iter_errors(payload))
        if errors:
            error = errors[0]
            raise StructuredLLMSchemaError(
                "structured model response failed authoritative output schema",
                schema_path=_schema_violation_path(error),
                metadata=metadata,
            )
        try:
            proposal_digest = compute_gateway_digest(payload)
        except TypeError as exc:
            raise StructuredLLMResponseParseError(
                "structured model response is not canonical JSON serializable",
                metadata=metadata,
            ) from exc
        return SemanticModelProposal(
            execution=request.execution,
            payload=payload,
            proposal_digest=proposal_digest,
            metadata=metadata,
        )

    def _resolve_contract(
        self,
        request: StructuredLLMCallRequest,
    ) -> SkillSpec:
        """解析并校验当前调用绑定的权威 SkillSpec。

        :param request: 当前结构化模型调用请求。
        :return: 返回版本精确匹配的生产 SkillSpec。
        :raises StructuredLLMGatewayContractError: Skill、执行家族、schema 或上下文身份错配时抛出。
        """
        task = request.execution.task
        try:
            spec = self.registry.require(task.skill_id, task.skill_version)
        except Exception as exc:
            raise StructuredLLMGatewayContractError(
                "structured gateway skill is not registered",
            ) from exc
        if spec.execution_family not in _GATEWAY_EXECUTION_FAMILIES:
            raise StructuredLLMGatewayContractError(
                "skill execution family is not accepted by structured gateway",
            )
        if not schema_reference_matches(task.expected_output_schema, spec.output_contract):
            raise StructuredLLMGatewayContractError(
                "task output schema reference does not match skill catalog",
            )
        if (
            request.prompt.skill_id,
            request.prompt.skill_version,
        ) != (
            task.skill_id,
            task.skill_version,
        ):
            raise StructuredLLMGatewayContractError(
                "prompt projection skill identity mismatch",
            )
        if request.prompt.context_digest != request.execution.turn_snapshot_digest:
            raise StructuredLLMGatewayContractError(
                "prompt projection context digest mismatch",
            )
        return spec

    def _schema_name(self, spec: SkillSpec) -> str:
        """生成传给模型网关的稳定输出 schema 名称。

        :param spec: 当前调用的权威 SkillSpec。
        :return: 返回由 SKILL 身份和版本派生的安全名称。
        """
        raw_name = f"{spec.skill_id}_{spec.skill_version}_output"
        return "".join(
            character if character.isalnum() or character in {"_", "-"} else "_"
            for character in raw_name
        )

    def _validator(self, spec: SkillSpec) -> Draft202012Validator:
        """构造权威输出 schema 的本地 JSON Schema validator。

        :param spec: 当前调用的权威 SkillSpec。
        :return: 返回绑定 SkillCatalog 输出契约的 validator。
        :raises StructuredLLMGatewayContractError: schema 非法或包含远程引用时抛出。
        """
        schema = spec.output_contract.json_schema
        if _contains_remote_schema_reference(schema):
            raise StructuredLLMGatewayContractError(
                "structured output schema contains remote reference",
            )
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:
            raise StructuredLLMGatewayContractError(
                "structured output schema is invalid",
            ) from exc
        return Draft202012Validator(schema)

    def _validate_transport_response(
        self,
        request: StructuredLLMCallRequest,
        response: StructuredModelTransportResponse,
        metadata: StructuredLLMCallMetadata,
    ) -> None:
        """校验底层传输没有发生隐藏模型切换或未完成响应。

        :param request: 当前结构化模型调用请求。
        :param response: 底层传输返回的单次响应。
        :param metadata: 已构建的调用审计元数据。
        :return: 无返回值。
        :raises StructuredLLMModelCallError: 精确模型、finish reason 或 usage 契约失败时抛出。
        """
        if response.requested_model != request.model:
            raise StructuredLLMModelCallError(
                "structured model transport changed requested model",
                metadata=metadata,
            )
        if response.finish_reason not in {None, "stop"}:
            raise StructuredLLMModelCallError(
                "structured model response did not finish normally",
                metadata=metadata,
            )
        token_values = (
            response.prompt_tokens,
            response.completion_tokens,
            response.total_tokens,
        )
        if response.usage_available != all(value is not None for value in token_values):
            raise StructuredLLMModelCallError(
                "structured model usage metadata is inconsistent",
                metadata=metadata,
            )

    def _metadata(
        self,
        *,
        request: StructuredLLMCallRequest,
        spec: SkillSpec,
        prompt_hash: str,
        response: StructuredModelTransportResponse | None,
        latency_ms: float,
    ) -> StructuredLLMCallMetadata:
        """构建一次模型 attempt 的审计元数据。

        :param request: 当前结构化模型调用请求。
        :param spec: 当前调用的权威 SkillSpec。
        :param prompt_hash: canonical prompt JSON 摘要。
        :param response: 底层传输响应；失败前可为 None。
        :param latency_ms: 当前 attempt 的底层传输耗时。
        :return: 返回不含完整 prompt、原始响应或密钥的调用元数据。
        """
        schema = spec.output_contract
        return StructuredLLMCallMetadata(
            run_id=request.execution.run_id,
            task_id=request.execution.task.task_id,
            attempt_number=request.execution.attempt_number,
            skill_id=spec.skill_id,
            skill_version=spec.skill_version,
            turn_snapshot_digest=request.execution.turn_snapshot_digest,
            prompt_hash=prompt_hash,
            skill_contract_digest=sha256(
                spec.canonical_json().encode("utf-8"),
            ).hexdigest(),
            output_schema_id=schema.schema_id,
            output_schema_version=schema.schema_version,
            output_schema_digest=task_schema_digest(request),
            requested_model=request.model,
            response_model=None if response is None else response.response_model,
            response_id=None if response is None else response.response_id,
            finish_reason=None if response is None else response.finish_reason,
            prompt_tokens=None if response is None else response.prompt_tokens,
            completion_tokens=None if response is None else response.completion_tokens,
            total_tokens=None if response is None else response.total_tokens,
            usage_available=False if response is None else response.usage_available,
            latency_ms=latency_ms,
        )


def task_schema_digest(request: StructuredLLMCallRequest) -> str:
    """读取任务内权威输出 schema 的内容摘要。

    :param request: 当前结构化模型调用请求。
    :return: 返回 PlanTask 绑定的 schema digest。
    """
    return request.execution.task.expected_output_schema.schema_digest


__all__ = [
    "SemanticModelProposal",
    "StructuredLLMCallMetadata",
    "StructuredLLMCallRequest",
    "StructuredLLMGateway",
    "StructuredModelTransport",
    "StructuredModelTransportResponse",
    "canonical_gateway_json",
    "compute_gateway_digest",
]

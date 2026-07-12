##################################################################################################
# 文件: src/veterinary_agent/agent_runner/parser.py
# 作用: 基于 LangChain JsonOutputParser 与 jsonschema 实现 AgentRunner 结构化输出解析和 schema 校验。
# 边界: 不修改业务语义、不执行兽医安全审查、不引入外部 schema 服务；仅处理模型输出结构契约。
##################################################################################################

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypeAlias, cast

from jsonschema import SchemaError, ValidationError
from jsonschema.protocols import Validator
from jsonschema.validators import validator_for
from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import JsonOutputParser

from veterinary_agent.agent_runner.dto import (
    AgentSpecDto,
    AgentValidationErrorDto,
    JsonMap,
)
from veterinary_agent.agent_runner.enums import (
    AgentResponseFormat,
    AgentRunnerErrorCode,
    AgentRunnerOperation,
)
from veterinary_agent.agent_runner.errors import AgentRunnerError

JsonSchemaValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | dict[str, "JsonSchemaValue"]
    | list["JsonSchemaValue"]
)


@dataclass(frozen=True, slots=True)
class StructuredOutputParseResult:
    """结构化输出解析结果。"""

    parsed_output: JsonMap
    schema_valid: bool
    validation_errors: list[AgentValidationErrorDto]


def _expects_json(spec: AgentSpecDto) -> bool:
    """判断当前 Agent 规格是否期望 JSON 输出。

    :param spec: 已解析的 Agent 规格。
    :return: 若响应格式或输出 schema 要求 JSON，则返回 True。
    """

    return spec.output_schema is not None or spec.response_format in {
        AgentResponseFormat.JSON_OBJECT,
        AgentResponseFormat.JSON_SCHEMA,
    }


def _to_json_map(value: object) -> JsonMap:
    """将解析出的 JSON 值转换为 AgentRunResult 可承载的映射。

    :param value: 解析出的 JSON 值。
    :return: 若输入为 object 则返回字符串键映射；否则包装到 value 字段。
    """

    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {"value": value}


def _to_schema_value(value: object) -> JsonSchemaValue:
    """将 LangChain parser 结果收窄为 jsonschema 可校验 JSON 值。

    :param value: LangChain JsonOutputParser 解析出的值。
    :return: jsonschema 可校验的 JSON 值。
    """

    return cast(JsonSchemaValue, value)


def _format_json_path(path_parts: Iterable[object]) -> str:
    """将 jsonschema 错误路径转换为稳定 JSONPath 文本。

    :param path_parts: jsonschema ValidationError.path 提供的路径片段。
    :return: 以 ``$`` 开头的稳定路径文本。
    """

    path = "$"
    for part in path_parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path


def _validation_error_to_dto(error: ValidationError) -> AgentValidationErrorDto:
    """将 jsonschema 校验错误转换为 AgentRunner 校验错误 DTO。

    :param error: jsonschema 校验错误。
    :return: AgentRunner 结构化校验错误。
    """

    error_type = (
        error.validator if isinstance(error.validator, str) else "schema_validation"
    )
    return AgentValidationErrorDto(
        path=_format_json_path(error.path),
        message=error.message,
        error_type=error_type,
    )


def _build_json_schema_validator(schema: JsonMap) -> Validator:
    """构建适配当前 JSON Schema 草案的 validator。

    :param schema: Agent 规格声明的 JSON Schema。
    :return: 已完成 schema 自校验的 jsonschema validator。
    :raises SchemaError: 当 JSON Schema 自身不合法时抛出。
    """

    validator_cls = validator_for(schema)
    validator_cls.check_schema(schema)
    return validator_cls(schema)


class DefaultStructuredOutputParser:
    """基于 LangChain 与 jsonschema 的 AgentRunner 默认结构化输出解析器。"""

    def __init__(self, *, json_parser: JsonOutputParser | None = None) -> None:
        """初始化默认结构化输出解析器。

        :param json_parser: 可选 LangChain JSON 输出解析器；未传入时创建默认实例。
        :return: None。
        """

        self._json_parser = json_parser or JsonOutputParser()

    def parse_and_validate(
        self,
        *,
        content: str | None,
        spec: AgentSpecDto,
    ) -> StructuredOutputParseResult:
        """解析并校验模型输出。

        :param content: LlmGateway 返回的模型文本结果。
        :param spec: 已解析的 Agent 规格。
        :return: 结构化输出解析结果。
        :raises AgentRunnerError: 当输出为空、JSON 解析失败或输出 schema 自身非法时抛出。
        """

        if content is None or not content.strip():
            raise AgentRunnerError(
                code=AgentRunnerErrorCode.OUTPUT_PARSE_FAILED,
                operation=AgentRunnerOperation.PARSE_OUTPUT,
                message="模型输出为空，无法解析结构化结果",
                agent_id=spec.agent_id,
                agent_version=spec.agent_version,
                model_profile_id=spec.model_profile,
            )
        if not _expects_json(spec):
            return StructuredOutputParseResult(
                parsed_output={"text": content},
                schema_valid=True,
                validation_errors=[],
            )
        parsed_value = self._parse_json_content(content=content, spec=spec)
        validation_errors = self._validate_schema(
            parsed_value=parsed_value,
            spec=spec,
        )
        return StructuredOutputParseResult(
            parsed_output=_to_json_map(parsed_value),
            schema_valid=not validation_errors,
            validation_errors=validation_errors,
        )

    def _parse_json_content(
        self,
        *,
        content: str,
        spec: AgentSpecDto,
    ) -> JsonSchemaValue:
        """使用 LangChain JSON parser 解析模型文本。

        :param content: 模型输出文本。
        :param spec: 已解析的 Agent 规格。
        :return: 解析后的 JSON 值。
        :raises AgentRunnerError: 当模型文本无法解析为 JSON 时抛出。
        """

        try:
            return _to_schema_value(self._json_parser.parse(content))
        except OutputParserException as exc:
            raise AgentRunnerError(
                code=AgentRunnerErrorCode.OUTPUT_PARSE_FAILED,
                operation=AgentRunnerOperation.PARSE_OUTPUT,
                message="模型输出不是合法 JSON",
                agent_id=spec.agent_id,
                agent_version=spec.agent_version,
                model_profile_id=spec.model_profile,
                conflict_with={"reason": str(exc)},
            ) from exc

    def _validate_schema(
        self,
        *,
        parsed_value: JsonSchemaValue,
        spec: AgentSpecDto,
    ) -> list[AgentValidationErrorDto]:
        """使用 jsonschema 校验解析后的模型输出。

        :param parsed_value: 已解析 JSON 值。
        :param spec: 已解析的 Agent 规格。
        :return: schema 校验错误列表；空列表表示通过。
        :raises AgentRunnerError: 当 Agent 规格中的 JSON Schema 自身非法时抛出。
        """

        if spec.output_schema is None:
            return []
        try:
            validator = _build_json_schema_validator(spec.output_schema)
        except SchemaError as exc:
            raise AgentRunnerError(
                code=AgentRunnerErrorCode.OUTPUT_SCHEMA_VALIDATION_FAILED,
                operation=AgentRunnerOperation.VALIDATE_OUTPUT_SCHEMA,
                message="Agent 输出 JSON Schema 自身非法",
                agent_id=spec.agent_id,
                agent_version=spec.agent_version,
                model_profile_id=spec.model_profile,
                conflict_with={"reason": exc.message},
            ) from exc
        errors = sorted(
            validator.iter_errors(parsed_value),
            key=lambda error: list(cast(ValidationError, error).path),
        )
        return [
            _validation_error_to_dto(cast(ValidationError, error)) for error in errors
        ]


__all__: tuple[str, ...] = (
    "DefaultStructuredOutputParser",
    "StructuredOutputParseResult",
)

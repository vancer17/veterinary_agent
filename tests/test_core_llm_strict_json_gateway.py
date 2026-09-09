"""
=============================================================================
文件：tests/test_core_llm_strict_json_gateway.py
作用：验证统一 Strict JSON Gateway 的结构拒绝边界。
范围：只覆盖模型输出结构协议，不测试任何兽医语义解释。
说明：这些用例防止 Markdown JSON、字符串化 JSON、多 JSON 与未知字段绕过
      语义预处理前的结构治理。
=============================================================================
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vet_agent.core import schema
from vet_agent.core.errors import SchemaValidationError, StrictJSONValidationError


class Output(schema.StrictModel):
    """测试用最小严格输出对象。

    :ivar value: 必须为整数的输出字段。
    """

    value: int


def test_strict_json_gateway_accepts_single_object() -> None:
    """验证合法单一 JSON 对象可被解析为不可变 schema 对象。

    :return: 无返回值；断言失败时表示严格解析入口不可用。
    """
    result = schema.StrictJSONGateway().parse(
        content='{"value": 12}', output_schema=Output
    )

    assert result.value == 12
    with pytest.raises(ValidationError):
        result.value = 13


@pytest.mark.parametrize(
    ("content", "error_type"),
    [
        ('```json\n{"value": 12}\n```', StrictJSONValidationError),
        ('{"value": 12} {"value": 13}', StrictJSONValidationError),
        ('"{"value": 12}"', StrictJSONValidationError),
        ('{"value": 12, "value": 13}', StrictJSONValidationError),
        ('{"value": 12} // tail', StrictJSONValidationError),
        ('{"value": "12"}', SchemaValidationError),
        ('{"value": 12, "extra": true}', SchemaValidationError),
    ],
)
def test_strict_json_gateway_rejects_invalid_content(
    content: str,
    error_type: type[Exception],
) -> None:
    """验证严格 JSON 网关拒绝协议外输出。

    :param content: 待测试的模型原始输出。
    :param error_type: 预期抛出的异常类型。
    :return: 无返回值；未显式抛错时测试失败。
    """
    with pytest.raises(error_type):
        schema.StrictJSONGateway().parse(content=content, output_schema=Output)

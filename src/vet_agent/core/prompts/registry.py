"""
版本化 prompt 绑定注册表。

该模块把已加载的 prompt 资产与 composition root 提供的严格输出 schema、
稳定模型角色目录绑定成 ``PromptBinding``。注册表只做工程绑定和 exact
version 查询，不加载语义文案，也不感知具体模型名或厂商 SDK。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from types import MappingProxyType

from vet_agent.core import errors as core_errors
from vet_agent.core.prompts.contracts import (
    LoadedPromptAsset,
    PromptBinding,
    PromptRecord,
)
from vet_agent.core.schema import StrictModel


class PromptRegistry:
    """维护 prompt 资产到模型调用协议的确定性绑定。

    registry 是模型调用前获取 prompt 绑定的入口，可保证同一个
    ``prompt_id + prompt_version`` 只对应一个模板摘要和输出 schema。
    """

    def __init__(
        self,
        *,
        assets: Sequence[LoadedPromptAsset],
        output_schemas: Mapping[str, type[StrictModel]],
        allowed_model_roles: AbstractSet[str],
    ) -> None:
        """初始化不可变 prompt 注册表。

        :param assets: 文件加载器输出的 prompt 资产集合。
        :param output_schemas: schema 标识到严格输出 schema 的目录。
        :param allowed_model_roles: composition root 提供的稳定模型角色集合。
        :return: 无返回值；构造完成 exact version 到绑定的不可变映射。
        :raises core_errors.ConfigurationError: 当资产、schema、角色或版本
            绑定非法或重复。
        """
        invalid_reasons: list[str] = []
        if not assets:
            invalid_reasons.append("assets_empty")
        if not output_schemas:
            invalid_reasons.append("output_schemas_empty")
        if not allowed_model_roles:
            invalid_reasons.append("allowed_model_roles_empty")
        for schema_id, catalog_schema in output_schemas.items():
            if not schema_id:
                invalid_reasons.append("output_schema_id_empty")
            if not (
                isinstance(catalog_schema, type)
                and issubclass(catalog_schema, StrictModel)
            ):
                invalid_reasons.append(f"output_schema_invalid:{schema_id}")

        bindings: dict[tuple[str, str], PromptBinding] = {}
        for asset in assets:
            key = (asset.spec.prompt_id, asset.spec.prompt_version)
            if key in bindings:
                invalid_reasons.append("prompt_version_duplicated")
            if asset.spec.model_role not in allowed_model_roles:
                invalid_reasons.append("model_role_not_allowed")
            output_schema = output_schemas.get(asset.spec.output_schema_id)
            if output_schema is None:
                invalid_reasons.append("output_schema_not_found")
                break
            expected_template_sha256 = hashlib.sha256(
                asset.template.encode(encoding="utf-8"),
            ).hexdigest()
            if (
                not asset.template
                or asset.template_sha256 != expected_template_sha256
                or len(asset.spec_sha256) != 64
            ):
                invalid_reasons.append("template_binding_invalid")
                break
            if invalid_reasons:
                break

            record = PromptRecord(
                prompt_id=asset.spec.prompt_id,
                prompt_version=asset.spec.prompt_version,
                prompt_sha256=asset.template_sha256,
                model_role=asset.spec.model_role,
                output_schema=output_schema,
                template=asset.template,
            )
            bindings[key] = PromptBinding(asset=asset, record=record)

        if invalid_reasons:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Prompt 注册表绑定非法",
                context={"reasons": ",".join(sorted(set(invalid_reasons)))},
            )
        self._bindings = MappingProxyType(bindings)

    def get(
        self,
        *,
        prompt_id: str,
        prompt_version: str,
    ) -> PromptBinding:
        """按 exact version 获取 prompt 绑定。

        :param prompt_id: 语义层定义的稳定 prompt 标识。
        :param prompt_version: 必须显式指定的不可变 prompt 版本。
        :return: 包含资产布局和最小调用记录的不可变绑定。
        :raises core_errors.ConfigurationError: 当请求使用 ``latest``、版本
            缺失或标识为空。
        """
        if not prompt_id:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Prompt 标识为空",
                context={"reasons": "prompt_id_empty"},
            )
        if not prompt_version or prompt_version.casefold() == "latest":
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Prompt 必须使用 exact version",
                context={
                    "prompt_id": prompt_id,
                    "reasons": "prompt_version_not_exact",
                },
            )
        binding = self._bindings.get((prompt_id, prompt_version))
        if binding is None:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="Prompt 版本不存在",
                context={
                    "prompt_id": prompt_id,
                    "reasons": "prompt_version_not_found",
                },
            )
        return binding


__all__ = [
    "PromptRegistry",
]

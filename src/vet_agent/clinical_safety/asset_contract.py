"""
文件：src/vet_agent/clinical_safety/asset_contract.py
作用：定义临床安全静态资产与向量 chunk 的发布态严格契约。
范围：位于离线资产治理、数据库导入 dry-run 与发布前校验阶段；不参与运行时文本召回或策略裁决。
说明：本文件只暴露发布态严格校验能力，不提供“基础结构校验通过”模式，避免不完整资产绕过治理进入运行时链路。
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Mapping, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator, model_validator


CLINICAL_SAFETY_PUBLISH_SCHEMA_VERSION = "1.0.0"
DISALLOWED_PUBLISH_CODES: frozenset[str] = frozenset({"CLINICAL_SAFETY_UNKNOWN"})
DISALLOWED_PUBLISH_CODE_PATTERN = re.compile(
    r"^(?:"
    r"CLINICAL_SAFETY_[0-9_]+|"
    r"TOXIC_SUBSTANCE_[0-9]{3}|"
    r"EMERGENCY_RED_FLAG_[0-9]{3}|"
    r"DANGER_PATTERN_[A-Z0-9_]+_[0-9]{3}"
    r")$"
)


class ClinicalSafetyAssetContractError(ValueError):
    """表示临床安全发布态资产契约校验失败。

    :return: 无返回值；异常用于阻断离线导入或发布流程继续执行。
    """


class ClinicalSafetyAssetTypeContract(StrEnum):
    """表示发布态临床安全资产允许使用的资产类型。

    :return: 无返回值；枚举值用于静态资产、数据库和策略输入之间保持一致。
    """

    TOXIN = "toxin"
    HUMAN_DRUG = "human_drug"
    PLANT_TOXIN = "plant_toxin"
    CHEMICAL_TOXIN = "chemical_toxin"
    EMERGENCY_RED_FLAG = "emergency_red_flag"
    DANGER_PATTERN = "danger_pattern"


class ClinicalSafetySeverityContract(StrEnum):
    """表示发布态临床安全资产允许声明的默认严重级别。

    :return: 无返回值；最终动作仍由策略裁决域基于结构化候选统一产生。
    """

    INFO = "info"
    CAUTION = "caution"
    URGENT = "urgent"
    BLOCKED = "blocked"


class ClinicalSafetyActionClassContract(StrEnum):
    """表示发布态临床安全资产允许声明的动作分类。

    :return: 无返回值；动作分类只作为策略输入，不在运行时代码中直接生成最终动作。
    """

    EMERGENCY = "emergency"
    SAME_DAY_VISIT = "same_day_visit"
    URGENT_VISIT = "urgent_visit"
    SAFETY_WARNING = "safety_warning"


class ClinicalSafetyChunkTypeContract(StrEnum):
    """表示发布态临床安全向量 chunk 允许使用的片段类型。

    :return: 无返回值；chunk 类型用于区分召回识别文本、风险解释文本和处置口径文本。
    """

    RECOGNITION = "recognition"
    CLINICAL_RISK = "clinical_risk"
    TRIAGE_ACTION = "triage_action"


class ClinicalSafetyDecisionHintKeyContract(StrEnum):
    """表示发布态资产中允许出现的结构化策略提示键。

    :return: 无返回值；该枚举防止 decision_hints 退化为自由格式策略 DSL。
    """

    ACTUAL_EXPOSURE = "actual_exposure"
    POSSIBLE_EXPOSURE = "possible_exposure"
    ACTIVE_SYMPTOM = "active_symptom"
    POSSIBLE_SYMPTOM = "possible_symptom"
    HISTORICAL_CONTEXT = "historical_context"
    KNOWLEDGE_QUESTION = "knowledge_question"
    PREVENTION_QUESTION = "prevention_question"


class ClinicalSafetyDecisionHintValueContract(StrEnum):
    """表示发布态资产中允许出现的结构化策略提示值。

    :return: 无返回值；提示值只作为策略输入，不允许在 Python 运行时解释为最终动作。
    """

    SAFETY_ESCALATED = "safety_escalated"
    CLINICAL_CAUTION = "clinical_caution"
    COMPLETED_WITH_SAFETY_WARNING = "completed_with_safety_warning"
    RECORD_AS_HISTORY = "record_as_history"


class ClinicalSafetyContextKeyContract(StrEnum):
    """表示发布态资产 required_context 允许声明的上下文字段。

    :return: 无返回值；上下文字段仅用于裁决域消费结构化上下文，不承载运行时关键词规则。
    """

    SPECIES = "species"
    SEX = "sex"
    AGE = "age"
    SYMPTOMS = "symptoms"


class ClinicalSafetyDocumentMetaContract(BaseModel):
    """表示临床安全发布态资产文档的元信息。

    :param document_schema: 静态资产文档类型，对应 JSON 中的 schema 字段。
    :param schema_version: 静态资产契约版本。
    :param version: 本批资产或 chunk 的业务版本。
    :param source_file: 原始来源文件路径。
    :param asset_count: 资产数量。
    :param chunk_count: chunk 数量；资产文档可为空。
    :param generated_at: 文档生成时间。
    :param source_meta: 原始来源元信息。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True, str_strip_whitespace=True)

    document_schema: Literal["clinical_safety_assets", "clinical_safety_chunks"] = Field(
        alias="schema",
        description="静态资产文档类型。",
    )
    schema_version: Literal["1.0.0"] = Field(description="静态资产契约版本。")
    version: str = Field(min_length=1, description="本批资产或 chunk 的业务版本。")
    source_file: str = Field(min_length=1, description="原始来源文件路径。")
    asset_count: int = Field(ge=0, description="文档声明的资产数量。")
    chunk_count: int | None = Field(default=None, ge=0, description="文档声明的 chunk 数量；资产文档可为空。")
    generated_at: datetime = Field(description="文档生成时间。")
    source_meta: dict[str, Any] = Field(default_factory=dict, description="原始来源元信息。")


class ClinicalSafetyAssetPublishContract(BaseModel):
    """表示单条发布态临床安全资产的严格契约。

    :param asset_id: 临床安全资产稳定标识。
    :param code: 已由资产治理域确认的对外安全信号编码。
    :param asset_type: 安全资产类型。
    :param canonical_name: 资产规范名称。
    :param category: 资产所属临床分类或原始资料分类。
    :param species_scope: 资产适用物种范围。
    :param sex_scope: 资产适用性别范围。
    :param age_scope: 资产适用年龄阶段范围。
    :param severity: 资产默认安全严重级别。
    :param action_class: 资产默认动作分类。
    :param aliases: 资产别名、英文名、商品名或俗称。
    :param carriers: 风险载体或暴露来源列表。
    :param user_expressions: 用户常见表达列表，仅用于离线向量文本生成。
    :param symptoms: 资产相关症状或风险线索。
    :param recognition_phrases: 离线生成 embedding 文本所需的召回短语集合。
    :param required_context: 后续裁决域消费的结构化上下文提示。
    :param decision_hints: 后续裁决域消费的枚举化策略提示。
    :param clinical_risk_summary: 临床风险摘要。
    :param triage_message: 对外分诊处置口径。
    :param source: 资料来源追踪信息。
    :param review_status: 审核状态；发布态严格契约只接受 approved。
    :param version: 资产版本。
    :param enabled: 是否允许进入运行时召回；发布态严格契约只接受 True。
    :param published_at: 资产发布时间。
    :param raw_text: 原始临床安全文本字段备份。
    :param metadata: 资产附加审计元数据。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    asset_id: str = Field(min_length=1, description="临床安全资产稳定标识。")
    code: str = Field(min_length=1, max_length=128, description="已由资产治理域确认的对外安全信号编码。")
    asset_type: ClinicalSafetyAssetTypeContract = Field(description="安全资产类型。")
    canonical_name: str = Field(min_length=1, max_length=160, description="资产规范名称。")
    category: str = Field(min_length=1, max_length=120, description="资产所属临床分类或原始资料分类。")
    species_scope: tuple[str, ...] = Field(default_factory=tuple, description="资产适用物种范围。")
    sex_scope: tuple[str, ...] = Field(default_factory=tuple, description="资产适用性别范围。")
    age_scope: tuple[str, ...] = Field(default_factory=tuple, description="资产适用年龄阶段范围。")
    severity: ClinicalSafetySeverityContract = Field(description="资产默认安全严重级别。")
    action_class: ClinicalSafetyActionClassContract = Field(description="资产默认动作分类。")
    aliases: tuple[str, ...] = Field(default_factory=tuple, description="资产别名、英文名、商品名或俗称。")
    carriers: tuple[str, ...] = Field(default_factory=tuple, description="风险载体或暴露来源列表。")
    user_expressions: tuple[str, ...] = Field(default_factory=tuple, description="用户常见表达列表，仅用于离线向量文本生成。")
    symptoms: tuple[str, ...] = Field(default_factory=tuple, description="资产相关症状或风险线索。")
    recognition_phrases: tuple[str, ...] = Field(min_length=1, description="离线生成 embedding 文本所需的召回短语集合。")
    required_context: dict[ClinicalSafetyContextKeyContract, tuple[str, ...]] = Field(
        default_factory=dict,
        description="后续裁决域消费的结构化上下文提示。",
    )
    decision_hints: dict[ClinicalSafetyDecisionHintKeyContract, ClinicalSafetyDecisionHintValueContract] = Field(
        default_factory=dict,
        description="后续裁决域消费的枚举化策略提示。",
    )
    clinical_risk_summary: str = Field(min_length=1, description="临床风险摘要。")
    triage_message: str = Field(min_length=1, description="对外分诊处置口径。")
    source: dict[str, str] = Field(description="资料来源追踪信息。")
    review_status: Literal["approved"] = Field(description="审核状态；发布态严格契约只接受 approved。")
    version: str = Field(min_length=1, description="资产版本。")
    enabled: Literal[True] = Field(description="是否允许进入运行时召回；发布态严格契约只接受 True。")
    published_at: datetime = Field(description="资产发布时间。")
    raw_text: dict[str, str] = Field(default_factory=dict, description="原始临床安全文本字段备份。")
    metadata: dict[str, Any] = Field(default_factory=dict, description="资产附加审计元数据。")

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        """校验发布态安全信号编码不得来自运行时兜底或临时 slug。

        :param value: 原始安全信号编码。
        :return: 返回通过发布态契约校验的安全信号编码。
        :raises ValueError: 编码为空、格式非法或命中禁止发布的兜底编码时抛出。
        """
        normalized = value.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", normalized):
            raise ValueError("clinical safety asset code must be an uppercase stable identifier")
        if normalized in DISALLOWED_PUBLISH_CODES or DISALLOWED_PUBLISH_CODE_PATTERN.fullmatch(normalized):
            raise ValueError("clinical safety asset code cannot be a generated fallback code")
        return normalized

    @field_validator("species_scope")
    @classmethod
    def validate_species_scope(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """校验资产适用物种范围。

        :param value: 原始物种范围。
        :return: 返回通过枚举校验的物种范围。
        :raises ValueError: 包含未知物种时抛出。
        """
        return _validate_tuple_values(value, allowed={"dog", "cat"}, field_name="species_scope")

    @field_validator("sex_scope")
    @classmethod
    def validate_sex_scope(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """校验资产适用性别范围。

        :param value: 原始性别范围。
        :return: 返回通过枚举校验的性别范围。
        :raises ValueError: 包含未知性别时抛出。
        """
        return _validate_tuple_values(value, allowed={"male", "female"}, field_name="sex_scope")

    @field_validator("age_scope")
    @classmethod
    def validate_age_scope(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """校验资产适用年龄范围。

        :param value: 原始年龄范围。
        :return: 返回通过枚举校验的年龄范围。
        :raises ValueError: 包含未知年龄阶段时抛出。
        """
        return _validate_tuple_values(value, allowed={"juvenile", "adult", "senior"}, field_name="age_scope")

    @model_validator(mode="after")
    def validate_scope_required_context_alignment(self) -> Self:
        """校验受限适用范围必须声明等值的裁决前置上下文。

        :return: 返回通过范围与前置上下文一致性校验的资产契约。
        :raises ValueError: 受限范围缺少等值 required_context 声明时抛出。
        """
        expectations: tuple[tuple[ClinicalSafetyContextKeyContract, tuple[str, ...]], ...] = (
            (ClinicalSafetyContextKeyContract.SPECIES, self.species_scope),
            (ClinicalSafetyContextKeyContract.SEX, self.sex_scope),
            (ClinicalSafetyContextKeyContract.AGE, self.age_scope),
        )
        for context_key, scope_values in expectations:
            if not scope_values:
                continue
            context_values = self.required_context.get(context_key, ())
            if tuple(context_values) != tuple(scope_values):
                raise ValueError(
                    "clinical safety asset restricted scope requires matching required_context: "
                    f"{context_key.value}"
                )
        return self

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: dict[str, str]) -> dict[str, str]:
        """校验发布态资产来源字段具备最小审计信息。

        :param value: 原始来源信息。
        :return: 返回通过审计字段校验的来源信息。
        :raises ValueError: 来源缺少 source_file、source_path 或 source_text 时抛出。
        """
        required = {"source_file", "source_path", "source_text"}
        missing = sorted(key for key in required if not value.get(key, "").strip())
        if missing:
            raise ValueError(f"clinical safety asset source missing required keys: {missing}")
        return value


class ClinicalSafetyChunkPublishContract(BaseModel):
    """表示单条发布态临床安全向量 chunk 的严格契约。

    :param chunk_id: 临床安全向量 chunk 稳定标识。
    :param asset_id: 关联的临床安全资产标识。
    :param chunk_type: chunk 用途类型。
    :param title: chunk 标题。
    :param embedding_text: 生成向量使用的标准文本。
    :param metadata: chunk 附加审计元数据。
    :param review_status: 审核状态；发布态严格契约只接受 approved。
    :param version: chunk 版本。
    :param enabled: 是否允许运行时召回；发布态严格契约只接受 True。
    :param embedding_model: 生成 embedding 使用的模型名称。
    :param embedding_dimension: embedding 向量维度。
    :param content_hash: embedding_text 的内容哈希。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    chunk_id: str = Field(min_length=1, description="临床安全向量 chunk 稳定标识。")
    asset_id: str = Field(min_length=1, description="关联的临床安全资产标识。")
    chunk_type: ClinicalSafetyChunkTypeContract = Field(description="chunk 用途类型。")
    title: str = Field(min_length=1, max_length=200, description="chunk 标题。")
    embedding_text: str = Field(min_length=1, description="生成向量使用的标准文本。")
    metadata: dict[str, Any] = Field(default_factory=dict, description="chunk 附加审计元数据。")
    review_status: Literal["approved"] = Field(description="审核状态；发布态严格契约只接受 approved。")
    version: str = Field(min_length=1, description="chunk 版本。")
    enabled: Literal[True] = Field(description="是否允许运行时召回；发布态严格契约只接受 True。")
    embedding_model: str | None = Field(default=None, description="生成 embedding 使用的模型名称。")
    embedding_dimension: int | None = Field(default=None, gt=0, description="embedding 向量维度。")
    content_hash: str = Field(min_length=16, description="embedding_text 的内容哈希。")

    @model_validator(mode="after")
    def validate_embedding_materialization(self, info: ValidationInfo) -> Self:
        """校验发布态 chunk 是否已具备可用于生产召回的 embedding 元信息。

        :param info: Pydantic 校验上下文，包含 require_embeddings 开关。
        :return: 返回通过发布态契约校验的 chunk。
        :raises ValueError: 生产发布要求 embedding 元信息但当前 chunk 缺失时抛出。
        """
        context = info.context if isinstance(info.context, dict) else {}
        require_embeddings = bool(context.get("require_embeddings", True))
        if require_embeddings and not self.embedding_model:
            raise ValueError("published clinical safety chunk requires embedding_model")
        if require_embeddings and self.embedding_dimension is None:
            raise ValueError("published clinical safety chunk requires embedding_dimension")
        return self


class ClinicalSafetyAssetDocumentPublishContract(BaseModel):
    """表示发布态临床安全资产文档的严格契约。

    :param meta: 文档元信息，对应 JSON 中的 _meta 字段。
    :param assets: 发布态临床安全资产列表。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    meta: ClinicalSafetyDocumentMetaContract = Field(alias="_meta", description="文档元信息。")
    assets: tuple[ClinicalSafetyAssetPublishContract, ...] = Field(min_length=1, description="发布态临床安全资产列表。")

    @model_validator(mode="after")
    def validate_asset_document_count(self) -> Self:
        """校验资产文档声明数量与实际数量一致。

        :return: 返回通过数量一致性校验的资产文档。
        :raises ValueError: 文档类型或资产数量不一致时抛出。
        """
        if self.meta.document_schema != "clinical_safety_assets":
            raise ValueError("clinical safety asset document schema must be clinical_safety_assets")
        if self.meta.asset_count != len(self.assets):
            raise ValueError("clinical safety asset document asset_count mismatch")
        return self


class ClinicalSafetyChunkDocumentPublishContract(BaseModel):
    """表示发布态临床安全 chunk 文档的严格契约。

    :param meta: 文档元信息，对应 JSON 中的 _meta 字段。
    :param chunks: 发布态临床安全向量 chunk 列表。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    meta: ClinicalSafetyDocumentMetaContract = Field(alias="_meta", description="文档元信息。")
    chunks: tuple[ClinicalSafetyChunkPublishContract, ...] = Field(min_length=1, description="发布态临床安全向量 chunk 列表。")

    @model_validator(mode="after")
    def validate_chunk_document_count(self) -> Self:
        """校验 chunk 文档声明数量与实际数量一致。

        :return: 返回通过数量一致性校验的 chunk 文档。
        :raises ValueError: 文档类型、资产数量或 chunk 数量不一致时抛出。
        """
        if self.meta.document_schema != "clinical_safety_chunks":
            raise ValueError("clinical safety chunk document schema must be clinical_safety_chunks")
        if self.meta.chunk_count != len(self.chunks):
            raise ValueError("clinical safety chunk document chunk_count mismatch")
        return self


class ClinicalSafetyPublishContract(BaseModel):
    """表示一组发布态临床安全资产与 chunk 的严格契约校验结果。

    :param asset_document: 已通过严格校验的资产文档。
    :param chunk_document: 已通过严格校验的 chunk 文档。
    :return: 无返回值。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    asset_document: ClinicalSafetyAssetDocumentPublishContract = Field(description="已通过严格校验的资产文档。")
    chunk_document: ClinicalSafetyChunkDocumentPublishContract = Field(description="已通过严格校验的 chunk 文档。")

    @property
    def asset_count(self) -> int:
        """读取发布态资产数量。

        :return: 返回已通过严格校验的资产数量。
        """
        return len(self.asset_document.assets)

    @property
    def chunk_count(self) -> int:
        """读取发布态 chunk 数量。

        :return: 返回已通过严格校验的 chunk 数量。
        """
        return len(self.chunk_document.chunks)


def validate_clinical_safety_publish_contract(
    asset_document: Mapping[str, Any],
    chunk_document: Mapping[str, Any],
    *,
    require_embeddings: bool = True,
) -> ClinicalSafetyPublishContract:
    """执行临床安全静态资产发布态严格契约校验。

    :param asset_document: 原始资产文档 JSON 对象。
    :param chunk_document: 原始 chunk 文档 JSON 对象。
    :param require_embeddings: 是否要求发布态 chunk 已具备 embedding 元信息；生产发布应保持 True。
    :return: 返回已通过严格校验的发布态资产契约对象。
    :raises ClinicalSafetyAssetContractError: 任一资产、chunk 或跨文档引用不满足发布态契约时抛出。
    """
    try:
        assets = ClinicalSafetyAssetDocumentPublishContract.model_validate(dict(asset_document))
        chunks = ClinicalSafetyChunkDocumentPublishContract.model_validate(
            dict(chunk_document),
            context={"require_embeddings": require_embeddings},
        )
        _validate_publish_bundle(assets, chunks)
    except (ValidationError, ValueError) as exc:
        raise ClinicalSafetyAssetContractError(str(exc)) from exc
    return ClinicalSafetyPublishContract(asset_document=assets, chunk_document=chunks)


def clinical_safety_asset_publish_json_schema() -> dict[str, Any]:
    """生成临床安全发布态资产文档 JSON Schema。

    :return: 返回可写入静态文档或工具链的 JSON Schema 字典。
    """
    return ClinicalSafetyAssetDocumentPublishContract.model_json_schema()


def clinical_safety_chunk_publish_json_schema() -> dict[str, Any]:
    """生成临床安全发布态 chunk 文档 JSON Schema。

    :return: 返回可写入静态文档或工具链的 JSON Schema 字典。
    """
    return ClinicalSafetyChunkDocumentPublishContract.model_json_schema()


def _validate_publish_bundle(
    assets: ClinicalSafetyAssetDocumentPublishContract,
    chunks: ClinicalSafetyChunkDocumentPublishContract,
) -> None:
    """校验发布态资产文档与 chunk 文档之间的跨文件一致性。

    :param assets: 已完成字段级校验的资产文档。
    :param chunks: 已完成字段级校验的 chunk 文档。
    :return: 无返回值。
    :raises ValueError: 资产编码、资产标识、chunk 标识或跨文件引用不一致时抛出。
    """
    asset_ids = [asset.asset_id for asset in assets.assets]
    chunk_ids = [chunk.chunk_id for chunk in chunks.chunks]
    _raise_on_duplicates(asset_ids, field_name="asset_id")
    _raise_on_duplicates(chunk_ids, field_name="chunk_id")

    asset_by_id = {asset.asset_id: asset for asset in assets.assets}
    recognition_asset_ids: set[str] = set()
    for chunk in chunks.chunks:
        asset = asset_by_id.get(chunk.asset_id)
        if asset is None:
            raise ValueError(f"clinical safety chunk references an unknown asset_id: {chunk.asset_id}")
        if chunk.chunk_type == ClinicalSafetyChunkTypeContract.RECOGNITION:
            recognition_asset_ids.add(chunk.asset_id)
        _validate_chunk_metadata(chunk, asset)

    missing_recognition = sorted(set(asset_ids) - recognition_asset_ids)
    if missing_recognition:
        raise ValueError(f"clinical safety assets missing recognition chunks: {missing_recognition[:10]}")

    if chunks.meta.asset_count != len(assets.assets):
        raise ValueError("clinical safety chunk document asset_count mismatch")


def _validate_chunk_metadata(
    chunk: ClinicalSafetyChunkPublishContract,
    asset: ClinicalSafetyAssetPublishContract,
) -> None:
    """校验 chunk 冗余审计 metadata 不得与权威资产字段冲突。

    :param chunk: 已完成字段级校验的 chunk。
    :param asset: chunk 关联的权威资产。
    :return: 无返回值。
    :raises ValueError: chunk metadata 中的资产字段与权威资产不一致时抛出。
    """
    metadata = chunk.metadata
    expected = {
        "asset_id": asset.asset_id,
        "code": asset.code,
        "asset_type": asset.asset_type.value,
        "canonical_name": asset.canonical_name,
        "severity": asset.severity.value,
        "action_class": asset.action_class.value,
    }
    for key, value in expected.items():
        if key in metadata and metadata[key] != value:
            raise ValueError(f"clinical safety chunk metadata {key} mismatch: {chunk.chunk_id}")


def _validate_tuple_values(
    value: tuple[str, ...],
    *,
    allowed: set[str],
    field_name: str,
) -> tuple[str, ...]:
    """校验字符串元组字段只包含允许枚举值。

    :param value: 原始字符串元组。
    :param allowed: 允许出现的枚举值集合。
    :param field_name: 当前字段名称，用于错误说明。
    :return: 返回去重后的字符串元组。
    :raises ValueError: 字段包含空值、重复值或未知值时抛出。
    """
    normalized = tuple(item.strip().lower() for item in value)
    invalid = sorted(item for item in normalized if not item or item not in allowed)
    if invalid:
        raise ValueError(f"{field_name} contains invalid values: {invalid}")
    _raise_on_duplicates(list(normalized), field_name=field_name)
    return normalized


def _raise_on_duplicates(values: list[str], *, field_name: str) -> None:
    """发现重复字段值时抛出发布态契约错误。

    :param values: 待检查的字符串列表。
    :param field_name: 字段名称，用于错误说明。
    :return: 无返回值。
    :raises ValueError: 字段中存在重复值时抛出。
    """
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicates:
        raise ValueError(f"clinical safety publish contract duplicate {field_name}: {duplicates[:10]}")

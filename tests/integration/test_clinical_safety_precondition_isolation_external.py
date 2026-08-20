"""
=============================================================================
文件：tests/integration/test_clinical_safety_precondition_isolation_external.py
作用：通过真实 LiteLLM/Qwen 评估自然语言前提批量评估的跨 item 污染率。
范围：仅覆盖 QwenClinicalSafetyPreconditionAssessor 的单 item 基线、批量上下文、
      顺序敏感性和 evidence 交叉，不依赖数据库、RAG、OPA 或完整 API。
说明：本测试显式开启后执行，并输出不含密钥的 JSON 评估报告；评估阈值用于
      阻断可逃逸到 OPA 的批量污染，不将测试结果扩大为完整医学质量结论。
=============================================================================
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

import pytest
from pydantic import BaseModel

from vet_agent import Settings
from vet_agent.clinical_safety import (
    CLINICAL_SAFETY_PRECONDITION_PROMPT_VERSION,
    CLINICAL_SAFETY_PRECONDITION_RESPONSE_SCHEMA_VERSION,
    ClinicalSafetyAsset,
    ClinicalSafetyCandidate,
    ClinicalSafetyObservedFeature,
    ClinicalSafetyPreconditionAssessment,
    ClinicalSafetyPreconditionAssessmentResult,
    ClinicalSafetySemanticResult,
    QwenClinicalSafetyPreconditionAssessor,
    clinical_safety_semantic_premise_hash,
)
from vet_agent.runtime import QwenClient

ISOLATION_FLAG = "RUN_CLINICAL_SAFETY_PRECONDITION_ISOLATION_TEST"
DEFAULT_REPEAT_COUNT = 3
DEFAULT_BATCH_SIZES = (1,)
DEFAULT_ORDERS = ("original", "reverse", "rotate")
_ResponseT = TypeVar("_ResponseT", bound=BaseModel)


class _ExternalEnvironment(dict[str, str]):
    """表示真实外部依赖配置的安全字典。

    :return: 无返回值；测试失败输出时隐藏模型网关密钥。
    """

    def __repr__(self) -> str:
        """构造隐藏敏感值的调试表示。

        :return: 返回脱敏后的外部依赖配置摘要。
        """
        return (
            "{"
            + ", ".join(
                f"{key}=***" if "API_KEY" in key else f"{key}={value!r}"
                for key, value in self.items()
            )
            + "}"
        )


@dataclass(frozen=True)
class _IsolationCase:
    """表示一个受控的前提评估隔离用例。

    :param case_id: 用例稳定标识。
    :param required_symptoms: 条目级 any_of 自然语言症状前提。
    :param expected_statuses: 单 item 基线允许的稳定状态。
    :param allowed_evidence_ids: 该用例语义上可引用的证据集合。
    :return: 无返回值；该对象只用于隔离评估，不进入生产裁决。
    """

    case_id: str
    required_symptoms: tuple[str, ...]
    expected_statuses: tuple[str, ...]
    allowed_evidence_ids: frozenset[str]

    @property
    def item_id(self) -> str:
        """构造当前用例的语义前提哈希。

        :return: 返回模型批量响应关联用的 semantic_premise_hash。
        """
        return clinical_safety_semantic_premise_hash(
            {"species": ("cat", "dog"), "symptoms": self.required_symptoms}
        )


@dataclass(frozen=True)
class _RawCallRecord:
    """记录一次真实模型调用的期望 item 和原始响应。

    :param expected_item_ids: 当前模型请求应返回的语义前提哈希集合。
    :param raw_items: 模型原始返回的 assessment 列表。
    :return: 无返回值；该对象用于归一化前污染分析。
    """

    expected_item_ids: tuple[str, ...]
    raw_items: tuple[dict[str, Any], ...]


@dataclass
class _RecordingQwenClient:
    """转发真实结构化模型调用并记录原始响应的测试客户端。

    :param wrapped: 真实 Qwen/LiteLLM 客户端。
    :return: 无返回值；本对象不改变请求和响应，仅用于评估采样。
    """

    wrapped: QwenClient
    calls: list[_RawCallRecord] = field(default_factory=list)

    @property
    def available(self) -> bool:
        """检查真实 LiteLLM 客户端是否可用。

        :return: 模型网关配置有效时返回 True。
        """
        return self.wrapped.available

    async def chat_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_model: type[_ResponseT],
        model: str | None = None,
        temperature: float = 0.0,
    ) -> _ResponseT:
        """原样转发真实结构化模型调用并记录返回对象。

        :param messages: 前提评估器构造的消息列表。
        :param response_model: 结构化响应模型。
        :param model: 可选模型名称。
        :param temperature: 采样温度。
        :return: 返回真实模型结构化输出。
        """
        result = await self.wrapped.chat_structured(
            messages,
            response_model=response_model,
            model=model,
            temperature=temperature,
        )
        raw_payload = result.model_dump()
        prompt_payload = json.loads(messages[-1]["content"])
        self.calls.append(
            _RawCallRecord(
                expected_item_ids=tuple(
                    str(item.get("item_id"))
                    for item in prompt_payload.get("items", [])
                    if item.get("item_id")
                ),
                raw_items=tuple(raw_payload.get("assessments", [])),
            )
        )
        return result


@dataclass(frozen=True)
class _BatchExposure:
    """表示一个目标用例在批量上下文中的一次暴露。

    :param case_id: 用例标识。
    :param batch_size: 当前评估配置的批量上限。
    :param order_name: 当前候选排列名称。
    :param repeat_index: 重复执行序号。
    :param status: 归一化后的前提状态。
    :param evidence_ids: 归一化后的证据引用。
    :param confidence: 归一化后的置信度。
    :param trusted: 评估结果是否可进入 OPA 有效前提。
    :param raw_status: 模型原始状态。
    :param raw_evidence_ids: 模型原始证据引用。
    :return: 无返回值；该对象是污染指标的计算样本。
    """

    case_id: str
    batch_size: int
    order_name: str
    repeat_index: int
    status: str
    evidence_ids: tuple[str, ...]
    confidence: float
    trusted: bool
    raw_status: str
    raw_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class _IsolationReport:
    """表示真实服务隔离评估结果。

    :param model: 本次评估使用的模型名称。
    :param metrics: 污染率和结构错误率指标。
    :param events: 需要人工复核的污染事件。
    :param isolated_results: 单 item 基线结果。
    :param batch_exposures: 批量上下文暴露样本。
    :return: 无返回值；该对象可序列化为 JSON 评估报告。
    """

    model: str
    metrics: dict[str, float | int | str]
    events: list[dict[str, Any]]
    isolated_results: list[dict[str, Any]]
    batch_exposures: list[dict[str, Any]]


_CASES: tuple[_IsolationCase, ...] = (
    _IsolationCase(
        case_id="complete_combination",
        required_symptoms=("呼吸急促 + 黏膜发紫",),
        expected_statuses=("satisfied",),
        allowed_evidence_ids=frozenset({"f1", "f2"}),
    ),
    _IsolationCase(
        case_id="any_of_entry",
        required_symptoms=("呼吸急促", "牙龈发紫"),
        expected_statuses=("satisfied",),
        allowed_evidence_ids=frozenset({"f1", "f2"}),
    ),
    _IsolationCase(
        case_id="partial_combination",
        required_symptoms=("呼吸急促 + 呕吐",),
        expected_statuses=("unknown", "not_satisfied"),
        allowed_evidence_ids=frozenset({"f1", "f4"}),
    ),
    _IsolationCase(
        case_id="related_not_entailed",
        required_symptoms=("牙龈苍白",),
        expected_statuses=("unknown", "not_satisfied"),
        allowed_evidence_ids=frozenset({"f2"}),
    ),
    _IsolationCase(
        case_id="denied_condition",
        required_symptoms=("呕吐",),
        expected_statuses=("unknown", "not_satisfied"),
        allowed_evidence_ids=frozenset({"f4"}),
    ),
    _IsolationCase(
        case_id="resolved_condition",
        required_symptoms=("当前抽搐",),
        expected_statuses=("unknown", "not_satisfied"),
        allowed_evidence_ids=frozenset({"f5"}),
    ),
    _IsolationCase(
        case_id="long_instruction",
        required_symptoms=("如果出现明显腹痛、频繁呕吐或精神萎靡，应尽快就诊",),
        expected_statuses=("unknown", "not_satisfied"),
        allowed_evidence_ids=frozenset({"f4", "f6"}),
    ),
    _IsolationCase(
        case_id="similar_condition",
        required_symptoms=("呕血",),
        expected_statuses=("unknown", "not_satisfied"),
        allowed_evidence_ids=frozenset({"f4"}),
    ),
)


@pytest.fixture
def isolation_env() -> _ExternalEnvironment:
    """读取真实前提隔离评估所需外部依赖配置。

    :return: 返回 LiteLLM 地址、密钥和模型配置。
    """
    if not _enabled(ISOLATION_FLAG):
        pytest.skip(f"未开启 {ISOLATION_FLAG}，跳过真实前提隔离评估。")
    env = {
        "LITELLM_BASE_URL": str(
            os.getenv("EXTERNAL_API_TEST_LITELLM_BASE_URL")
            or os.getenv("LITELLM_BASE_URL", "")
        ),
        "LITELLM_API_KEY": str(
            os.getenv("EXTERNAL_API_TEST_LITELLM_API_KEY")
            or os.getenv("LITELLM_API_KEY")
            or os.getenv("LITELLM_MASTER_KEY", "")
        ),
        "QWEN_MODEL": str(
            os.getenv(
                "EXTERNAL_API_TEST_QWEN_MODEL", os.getenv("QWEN_MODEL", "qwen-plus")
            )
        ),
    }
    missing = [key for key, value in env.items() if not value]
    if missing:
        pytest.fail(f"{ISOLATION_FLAG}=true 时缺少配置：{', '.join(missing)}。")
    return _ExternalEnvironment(env)


@pytest.mark.integration
def test_real_precondition_batch_isolation_prevents_cross_item_contamination(
    isolation_env: dict[str, str],
) -> None:
    """通过真实模型量化批量前提评估的跨 item 污染率。

    :param isolation_env: 真实 LiteLLM 配置。
    :return: 无返回值；断言通过表示当前批量配置未出现可逃逸污染。
    """
    settings = _isolation_settings(isolation_env)
    repeat_count = _repeat_count()
    batch_sizes = _batch_sizes()
    orders = _order_names()
    qwen_client = QwenClient(settings)
    isolated_results = _run_isolated_baseline(qwen_client, settings, repeat_count)
    batch_exposures, raw_records = _run_batch_contexts(
        qwen_client,
        settings,
        repeat_count,
        batch_sizes,
        orders,
    )
    report = _build_report(
        isolated_results=isolated_results,
        batch_exposures=batch_exposures,
        raw_records=raw_records,
        repeat_count=repeat_count,
        batch_sizes=batch_sizes,
        orders=orders,
        model=settings.default_model,
    )
    _write_report(report)

    _assert_baseline_quality(isolated_results)
    _assert_report_thresholds(report)


def _isolation_settings(env: dict[str, str]) -> Settings:
    """构造真实隔离评估专用配置。

    :param env: 外部依赖配置。
    :return: 返回低重试、高单批超时且便于受控分批的配置。
    """
    timeout_seconds = _timeout_seconds()
    return Settings(
        litellm_api_key=env["LITELLM_API_KEY"],
        litellm_base_url=env["LITELLM_BASE_URL"].rstrip("/"),
        default_model=env["QWEN_MODEL"],
        request_timeout_seconds=timeout_seconds,
        qwen_max_retries=0,
        clinical_safety_precondition_min_confidence=0.65,
        clinical_safety_precondition_batch_timeout_seconds=timeout_seconds,
        clinical_safety_precondition_total_timeout_seconds=timeout_seconds + 10.0,
    )


def _run_isolated_baseline(
    qwen_client: QwenClient,
    settings: Settings,
    repeat_count: int,
) -> list[dict[str, Any]]:
    """执行单 item 重复基线评估。

    :param qwen_client: 真实 Qwen 客户端。
    :param settings: 隔离评估配置。
    :param repeat_count: 每个用例重复执行次数。
    :return: 返回每个用例的单 item 状态、证据和基线噪声摘要。
    """
    results: list[dict[str, Any]] = []
    isolated_settings = replace(settings, clinical_safety_precondition_batch_size=1)
    for case in _CASES:
        statuses: list[str] = []
        evidence_ids: list[tuple[str, ...]] = []
        confidences: list[float] = []
        for repeat_index in range(1, repeat_count + 1):
            assessment = _run_assessment(
                qwen_client,
                isolated_settings,
                (case,),
                expected_case_ids=(case.case_id,),
            )[0][case.case_id]
            statuses.append(assessment.status)
            evidence_ids.append(assessment.evidence_ids)
            confidences.append(assessment.confidence)
        results.append(
            {
                "case_id": case.case_id,
                "repeat_statuses": statuses,
                "consensus": _consensus(statuses),
                "repeat_evidence_ids": [list(value) for value in evidence_ids],
                "repeat_confidences": confidences,
                "noise": len(set(statuses)) > 1,
                "expected_statuses": list(case.expected_statuses),
                "allowed_evidence_ids": sorted(case.allowed_evidence_ids),
            }
        )
    return results


def _run_batch_contexts(
    qwen_client: QwenClient,
    settings: Settings,
    repeat_count: int,
    batch_sizes: tuple[int, ...],
    orders: tuple[str, ...],
) -> tuple[list[_BatchExposure], list[_RawCallRecord]]:
    """执行多批量、多顺序的真实模型上下文评估。

    :param qwen_client: 真实 Qwen 客户端。
    :param settings: 隔离评估配置。
    :param repeat_count: 每种排列重复执行次数。
    :param batch_sizes: 待评估批量上限集合。
    :param orders: 待评估的候选排列名称。
    :return: 返回批量暴露样本和原始模型调用记录。
    """
    exposures: list[_BatchExposure] = []
    raw_records: list[_RawCallRecord] = []
    for batch_size in batch_sizes:
        batch_settings = replace(
            settings, clinical_safety_precondition_batch_size=batch_size
        )
        for order_name in orders:
            ordered_cases = _order_cases(order_name)
            for repeat_index in range(1, repeat_count + 1):
                assessments, records = _run_assessment(
                    qwen_client,
                    batch_settings,
                    ordered_cases,
                    expected_case_ids=tuple(case.case_id for case in ordered_cases),
                )
                raw_records.extend(records)
                raw_by_case = _raw_items_by_case(records)
                for case in ordered_cases:
                    assessment = assessments[case.case_id]
                    raw_item = raw_by_case[case.case_id]
                    exposures.append(
                        _BatchExposure(
                            case_id=case.case_id,
                            batch_size=batch_size,
                            order_name=order_name,
                            repeat_index=repeat_index,
                            status=assessment.status,
                            evidence_ids=assessment.evidence_ids,
                            confidence=assessment.confidence,
                            trusted=assessment.trusted,
                            raw_status=str(raw_item.get("status") or "unknown"),
                            raw_evidence_ids=tuple(
                                str(value) for value in raw_item.get("evidence_ids", [])
                            ),
                        )
                    )
    return exposures, raw_records


def _run_assessment(
    qwen_client: QwenClient,
    settings: Settings,
    cases: tuple[_IsolationCase, ...],
    *,
    expected_case_ids: tuple[str, ...],
) -> tuple[dict[str, ClinicalSafetyPreconditionAssessment], list[_RawCallRecord]]:
    """执行一次受控前提评估并保留原始响应。

    :param qwen_client: 真实 Qwen 客户端。
    :param settings: 当前批量配置。
    :param cases: 按当前顺序参与评估的用例。
    :param expected_case_ids: 期望模型返回的用例标识。
    :return: 返回 asset/case 映射后的评估结果和原始模型调用记录。
    :raises AssertionError: 真实评估发生依赖失败时抛出。
    """
    recording_client = _RecordingQwenClient(qwen_client)
    assessor = QwenClinicalSafetyPreconditionAssessor(recording_client, settings)
    semantic_result = _semantic_result()
    candidates = tuple(_candidate(case) for case in cases)

    async def run_assessment() -> ClinicalSafetyPreconditionAssessmentResult:
        """执行当前真实前提评估任务。

        :return: 返回归一化后的前提评估结果。
        """
        return await assessor.assess(semantic_result, candidates)

    result = asyncio.run(run_assessment())
    mapped = {
        expected_case_id: result.assessments[_candidate_asset_id(expected_case_id)]
        for expected_case_id in expected_case_ids
    }
    return mapped, list(recording_client.calls)


def _semantic_result() -> ClinicalSafetySemanticResult:
    """构造所有隔离用例共享的固定观察事实集合。

    :return: 返回包含 present、denied 和 resolved 状态的语义结果。
    """
    return ClinicalSafetySemanticResult(
        symptom_state="present",
        intent_type="symptom",
        risk_evidence_state="sufficient",
        observed_features=(
            _feature("f1", "present", "呼吸急促"),
            _feature("f2", "present", "牙龈发紫"),
            _feature("f3", "present", "不吃东西"),
            _feature("f4", "denied", "呕吐"),
            _feature("f5", "resolved", "抽搐"),
            _feature("f6", "present", "精神正常"),
        ),
        confidence=0.98,
        strategy="litellm_response_format",
        source_text="clinical safety precondition isolation fixture",
    )


def _feature(
    feature_id: str,
    state: str,
    normalized_text: str,
) -> ClinicalSafetyObservedFeature:
    """构造一个固定观察事实。

    :param feature_id: 事实引用标识。
    :param state: 事实状态。
    :param normalized_text: 归一化自然语言事实。
    :return: 返回观察事实对象。
    """
    return ClinicalSafetyObservedFeature(
        feature_id=feature_id,
        feature_kind="symptom",
        state=state,  # type: ignore[arg-type]
        normalized_text=normalized_text,
        temporal_scope="recent_past" if state == "resolved" else "ongoing",
        resolution_state="resolved" if state == "resolved" else "ongoing",
    )


def _candidate(case: _IsolationCase) -> ClinicalSafetyCandidate:
    """构造隔离用例对应的最小临床安全候选。

    :param case: 受控隔离用例。
    :return: 返回不依赖数据库和召回器的候选对象。
    """
    asset_id = _candidate_asset_id(case.case_id)
    asset = ClinicalSafetyAsset(
        asset_id=asset_id,
        asset_type="emergency_red_flag",
        canonical_name=case.case_id,
        category="precondition_isolation_external_test",
        species_scope=("cat", "dog"),
        sex_scope=(),
        age_scope=(),
        severity="caution",
        action_class="safety_warning",
        code=asset_id.upper(),
        required_context={
            "species": ("cat", "dog"),
            "symptoms": case.required_symptoms,
        },
    )
    return ClinicalSafetyCandidate(asset=asset, score=0.91, chunk_hits=())


def _candidate_asset_id(case_id: str) -> str:
    """构造隔离用例候选资产标识。

    :param case_id: 用例标识。
    :return: 返回测试专用 asset_id。
    """
    return f"clinical_isolation_{case_id}"


def _order_cases(order_name: str) -> tuple[_IsolationCase, ...]:
    """按评估排列名称重排用例。

    :param order_name: 排列名称。
    :return: 返回对应顺序的用例元组。
    """
    if order_name == "reverse":
        return tuple(reversed(_CASES))
    if order_name == "rotate":
        return (*_CASES[3:], *_CASES[:3])
    return _CASES


def _raw_items_by_case(
    records: list[_RawCallRecord],
) -> dict[str, dict[str, Any]]:
    """将原始模型 item 按隔离用例标识建立映射。

    :param records: 当前评估产生的原始模型调用记录。
    :return: 返回 case_id 到原始 item 的映射。
    """
    item_to_case = {case.item_id: case.case_id for case in _CASES}
    mapped: dict[str, dict[str, Any]] = {}
    for record in records:
        for raw_item in record.raw_items:
            item_id = str(raw_item.get("item_id") or "")
            case_id = item_to_case.get(item_id)
            if case_id is not None:
                mapped.setdefault(case_id, raw_item)
    return mapped


def _build_report(
    *,
    isolated_results: list[dict[str, Any]],
    batch_exposures: list[_BatchExposure],
    raw_records: list[_RawCallRecord],
    repeat_count: int,
    batch_sizes: tuple[int, ...],
    orders: tuple[str, ...],
    model: str,
) -> _IsolationReport:
    """统计隔离污染率并构造评估报告。

    :param isolated_results: 单 item 基线结果。
    :param batch_exposures: 批量上下文暴露样本。
    :param raw_records: 原始模型调用记录。
    :param repeat_count: 基线和批量重复次数。
    :param batch_sizes: 评估批量集合。
    :param orders: 评估排列集合。
    :param model: 真实模型名称。
    :return: 返回指标、事件和样本明细。
    """
    consensus = {
        str(item["case_id"]): str(item["consensus"]) for item in isolated_results
    }
    case_by_id = {case.case_id: case for case in _CASES}
    events: list[dict[str, Any]] = []
    structural_errors = _structural_errors(raw_records)
    model_contaminated_exposures: set[tuple[str, int, str, int]] = set()
    effective_contaminated_exposures: set[tuple[str, int, str, int]] = set()
    escaped_exposures: set[tuple[str, int, str, int]] = set()
    negative_exposure_count = 0

    for exposure in batch_exposures:
        case = case_by_id[exposure.case_id]
        key = (
            exposure.case_id,
            exposure.batch_size,
            exposure.order_name,
            exposure.repeat_index,
        )
        isolated_status = consensus[exposure.case_id]
        evidence_cross_over = not set(exposure.evidence_ids).issubset(
            case.allowed_evidence_ids
        )
        raw_evidence_cross_over = not set(exposure.raw_evidence_ids).issubset(
            case.allowed_evidence_ids
        )
        status_divergence = exposure.status != isolated_status
        raw_status_divergence = exposure.raw_status != isolated_status
        if raw_status_divergence or raw_evidence_cross_over:
            model_contaminated_exposures.add(key)
        if status_divergence or evidence_cross_over:
            effective_contaminated_exposures.add(key)
        if "satisfied" not in case.expected_statuses:
            negative_exposure_count += 1
            if exposure.status == "satisfied" and exposure.trusted:
                escaped_exposures.add(key)
        if evidence_cross_over or raw_evidence_cross_over:
            events.append(
                {
                    "type": "evidence_cross_over",
                    "case_id": exposure.case_id,
                    "batch_size": exposure.batch_size,
                    "order": exposure.order_name,
                    "repeat": exposure.repeat_index,
                    "normalized_evidence_ids": list(exposure.evidence_ids),
                    "raw_evidence_ids": list(exposure.raw_evidence_ids),
                    "allowed_evidence_ids": sorted(case.allowed_evidence_ids),
                }
            )
        if status_divergence or raw_status_divergence:
            events.append(
                {
                    "type": "status_divergence",
                    "case_id": exposure.case_id,
                    "batch_size": exposure.batch_size,
                    "order": exposure.order_name,
                    "repeat": exposure.repeat_index,
                    "isolated_consensus": isolated_status,
                    "normalized_status": exposure.status,
                    "raw_status": exposure.raw_status,
                }
            )
        if key in escaped_exposures:
            events.append(
                {
                    "type": "escaped_contamination",
                    "case_id": exposure.case_id,
                    "batch_size": exposure.batch_size,
                    "order": exposure.order_name,
                    "repeat": exposure.repeat_index,
                    "evidence_ids": list(exposure.evidence_ids),
                    "confidence": exposure.confidence,
                }
            )

    expected_raw_item_count = sum(
        len(record.expected_item_ids) for record in raw_records
    )
    total_exposures = len(batch_exposures)
    noisy_cases = sum(bool(item["noise"]) for item in isolated_results)
    order_sensitive_combinations = _order_sensitive_combinations(batch_exposures)
    metrics: dict[str, float | int | str] = {
        "case_count": len(_CASES),
        "repeat_count": repeat_count,
        "batch_sizes": ",".join(str(value) for value in batch_sizes),
        "orders": ",".join(orders),
        "raw_expected_item_count": expected_raw_item_count,
        "structural_error_count": len(structural_errors),
        "structural_error_rate": _rate(len(structural_errors), expected_raw_item_count),
        "single_item_noise_case_rate": _rate(noisy_cases, len(_CASES)),
        "batch_exposure_count": total_exposures,
        "negative_exposure_count": negative_exposure_count,
        "model_contamination_event_count": len(model_contaminated_exposures),
        "model_contamination_rate": _rate(
            len(model_contaminated_exposures),
            total_exposures,
        ),
        "effective_contamination_event_count": len(effective_contaminated_exposures),
        "effective_contamination_rate": _rate(
            len(effective_contaminated_exposures),
            total_exposures,
        ),
        "escaped_contamination_event_count": len(escaped_exposures),
        "escaped_contamination_rate": _rate(
            len(escaped_exposures),
            negative_exposure_count,
        ),
        "order_sensitive_combination_count": order_sensitive_combinations,
        "order_sensitivity_rate": _rate(
            order_sensitive_combinations,
            len(_CASES) * len(batch_sizes),
        ),
    }
    return _IsolationReport(
        model=model,
        metrics=metrics,
        events=[*structural_errors, *events],
        isolated_results=isolated_results,
        batch_exposures=[
            {
                "case_id": exposure.case_id,
                "batch_size": exposure.batch_size,
                "order": exposure.order_name,
                "repeat": exposure.repeat_index,
                "status": exposure.status,
                "evidence_ids": list(exposure.evidence_ids),
                "confidence": exposure.confidence,
                "trusted": exposure.trusted,
                "raw_status": exposure.raw_status,
                "raw_evidence_ids": list(exposure.raw_evidence_ids),
            }
            for exposure in batch_exposures
        ],
    )


def _structural_errors(records: list[_RawCallRecord]) -> list[dict[str, Any]]:
    """统计原始响应中的缺失、重复和未知 item。

    :param records: 原始模型调用记录。
    :return: 返回结构错误事件列表。
    """
    errors: list[dict[str, Any]] = []
    for call_index, record in enumerate(records, start=1):
        raw_ids = [str(item.get("item_id") or "") for item in record.raw_items]
        counts = Counter(raw_ids)
        for expected_id in record.expected_item_ids:
            if counts[expected_id] == 0:
                errors.append(
                    {"type": "missing_item", "call": call_index, "item_id": expected_id}
                )
            elif counts[expected_id] > 1:
                errors.append(
                    {
                        "type": "duplicate_item",
                        "call": call_index,
                        "item_id": expected_id,
                        "count": counts[expected_id],
                    }
                )
        for raw_id, count in counts.items():
            if raw_id and raw_id not in record.expected_item_ids:
                errors.append(
                    {
                        "type": "unknown_item",
                        "call": call_index,
                        "item_id": raw_id,
                        "count": count,
                    }
                )
    return errors


def _order_sensitive_combinations(exposures: list[_BatchExposure]) -> int:
    """统计同一用例在同一批量下的顺序敏感组合数。

    :param exposures: 批量上下文暴露样本。
    :return: 返回状态随排列变化的 case/batch 组合数量。
    """
    groups: dict[tuple[str, int], set[str]] = {}
    for exposure in exposures:
        groups.setdefault(
            (exposure.case_id, exposure.batch_size),
            set(),
        ).add(exposure.status)
    return sum(len(statuses) > 1 for statuses in groups.values())


def _assert_baseline_quality(isolated_results: list[dict[str, Any]]) -> None:
    """校验单 item 基线本身满足期望语义边界。

    :param isolated_results: 单 item 基线结果。
    :return: 无返回值；基线不合法时不进入批量污染判定。
    """
    for result in isolated_results:
        consensus = str(result["consensus"])
        expected = set(str(value) for value in result["expected_statuses"])
        assert consensus in expected


def _assert_report_thresholds(report: _IsolationReport) -> None:
    """按安全阈值阻断批量交叉污染。

    :param report: 真实服务隔离评估报告。
    :return: 无返回值；高风险指标越限时测试失败。
    """
    metrics = report.metrics
    assert int(metrics["structural_error_count"]) == 0
    assert int(metrics["escaped_contamination_event_count"]) == 0
    assert float(metrics["effective_contamination_rate"]) <= _threshold(
        "EFFECTIVE_CONTAMINATION_MAX_RATE",
        0.15,
    )
    assert float(metrics["order_sensitivity_rate"]) <= _threshold(
        "ORDER_SENSITIVITY_MAX_RATE",
        0.20,
    )


def _write_report(report: _IsolationReport) -> None:
    """将隔离评估报告写入本地 JSON 文件。

    :param report: 隔离评估结果。
    :return: 无返回值；报告不包含密钥和用户数据。
    """
    report_dir = Path(
        os.getenv(
            "CLINICAL_SAFETY_PRECONDITION_ISOLATION_REPORT_DIR",
            ".data/evaluations",
        )
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = (
        report_dir / f"clinical-safety-precondition-isolation-{uuid4().hex[:12]}.json"
    )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "prompt_version": CLINICAL_SAFETY_PRECONDITION_PROMPT_VERSION,
        "response_schema_version": CLINICAL_SAFETY_PRECONDITION_RESPONSE_SCHEMA_VERSION,
        **report.__dict__,
    }
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n临床安全前提隔离评估报告: {report_path}")


def _consensus(statuses: list[str]) -> str:
    """计算单 item 基线多数状态。

    :param statuses: 重复评估状态列表。
    :return: 返回出现次数最多的状态；平票时优先 stable 枚举。
    """
    if not statuses:
        return "unknown"
    counts = Counter(statuses)
    max_count = max(counts.values())
    return next(
        status
        for status in ("satisfied", "not_satisfied", "unknown")
        if counts.get(status, 0) == max_count
    )


def _rate(numerator: int, denominator: int) -> float:
    """计算比例指标。

    :param numerator: 分子。
    :param denominator: 分母。
    :return: 返回 0 到 1 之间的比例。
    """
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def _repeat_count() -> int:
    """读取隔离评估重复次数。

    :return: 返回大于等于 1 的重复次数。
    """
    value = int(
        os.getenv(
            "CLINICAL_SAFETY_PRECONDITION_ISOLATION_REPEATS", str(DEFAULT_REPEAT_COUNT)
        )
    )
    return max(1, value)


def _batch_sizes() -> tuple[int, ...]:
    """读取待评估批量上限。

    :return: 返回去重且按数值排序的批量集合。
    """
    raw_value = os.getenv(
        "CLINICAL_SAFETY_PRECONDITION_ISOLATION_BATCH_SIZES",
        ",".join(str(value) for value in DEFAULT_BATCH_SIZES),
    )
    values = tuple(
        sorted({int(value) for value in raw_value.split(",") if value.strip()})
    )
    if not values or min(values) < 1:
        pytest.fail("隔离评估 batch sizes 必须至少包含一个大于 0 的值。")
    return values


def _order_names() -> tuple[str, ...]:
    """读取待评估排列名称。

    :return: 返回受控排列名称集合。
    """
    raw_value = os.getenv(
        "CLINICAL_SAFETY_PRECONDITION_ISOLATION_ORDERS",
        ",".join(DEFAULT_ORDERS),
    )
    values = tuple(value.strip() for value in raw_value.split(",") if value.strip())
    if not values or any(value not in DEFAULT_ORDERS for value in values):
        pytest.fail("隔离评估 orders 只支持 original、reverse 或 rotate。")
    return values


def _timeout_seconds() -> float:
    """读取真实隔离评估模型超时。

    :return: 返回超时秒数。
    """
    return float(os.getenv("EXTERNAL_API_TEST_TIMEOUT_SECONDS", "60"))


def _threshold(name: str, default: float) -> float:
    """读取评估阈值。

    :param name: 环境变量名称。
    :param default: 默认阈值。
    :return: 返回 0 到 1 之间的阈值。
    """
    return max(0.0, min(1.0, float(os.getenv(name, str(default)))))


def _enabled(name: str) -> bool:
    """判断显式集成测试开关是否开启。

    :param name: 环境变量名称。
    :return: 开启时返回 True。
    """
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}

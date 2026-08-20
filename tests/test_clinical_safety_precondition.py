"""
文件：tests/test_clinical_safety_precondition.py
作用：验证临床安全候选自然语言前提评估器的数据边界、受控并发与 Fail Closed 行为。
说明：本文件只测试前提语义评估层，不断言最终临床动作；OPA 行为由 Rego 测试覆盖。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any, TypeVar

from pydantic import BaseModel

from vet_agent import Settings
from vet_agent.clinical_safety import (
    CLINICAL_SAFETY_PRECONDITION_MAX_BATCH_SIZE,
    CLINICAL_SAFETY_PRECONDITION_PROMPT_VERSION,
    CLINICAL_SAFETY_PRECONDITION_RESPONSE_SCHEMA_VERSION,
    ClinicalSafetyAsset,
    ClinicalSafetyCandidate,
    ClinicalSafetyObservedFeature,
    ClinicalSafetySemanticResult,
    QwenClinicalSafetyPreconditionAssessor,
    UnavailableClinicalSafetyPreconditionAssessor,
    clinical_safety_canonical_required_context,
    clinical_safety_required_context_hash,
    clinical_safety_semantic_premise_hash,
)

BaseModelT = TypeVar("BaseModelT", bound=BaseModel)


class FakePreconditionModelClient:
    """提供可解析请求并返回结构化结果的模型客户端替身。"""

    def __init__(
        self,
        *,
        delay_seconds: float = 0.0,
        confidence: float = 0.93,
        status: str = "satisfied",
    ) -> None:
        """初始化模型客户端替身。

        :param delay_seconds: 每次调用延迟时间，用于测试受控并发。
        :param confidence: 每次结构化评估返回的置信度。
        :param status: 每次结构化评估返回的前提状态。
        :return: 无返回值。
        """
        self.delay_seconds = delay_seconds
        self.confidence = confidence
        self.status = status
        self.requests: list[dict[str, Any]] = []
        self.active_count = 0
        self.max_active_count = 0

    @property
    def available(self) -> bool:
        """声明测试模型客户端始终可用。

        :return: 始终返回 True。
        """
        return True

    async def chat_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_model: type[BaseModelT],
        model: str | None = None,
        temperature: float = 0.0,
    ) -> BaseModelT:
        """按输入 item_id 返回满足前提的结构化评估结果。

        :param messages: 模型消息列表。
        :param response_model: 结构化响应模型。
        :param model: 模型名称。
        :param temperature: 采样温度。
        :return: 返回与输入 item 对应的模型输出。
        """
        del model, temperature
        self.active_count += 1
        self.max_active_count = max(self.max_active_count, self.active_count)
        try:
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            payload = json.loads(messages[-1]["content"])
            self.requests.append(payload)
            assessments = [
                {
                    "item_id": item["item_id"],
                    "status": self.status,
                    "evidence_ids": ["f1"],
                    "confidence": self.confidence,
                }
                for item in payload["items"]
            ]
            return response_model.model_validate_json(
                json.dumps({"assessments": assessments}, ensure_ascii=False)
            )
        finally:
            self.active_count -= 1


def _observed_semantic() -> ClinicalSafetySemanticResult:
    """构造带当前回合症状事实的可信语义结果。

    :return: 返回包含 f1 呼吸急促事实的语义结果。
    """
    return ClinicalSafetySemanticResult(
        species="cat",
        symptom_state="present",
        intent_type="symptom",
        risk_evidence_state="sufficient",
        observed_features=(
            ClinicalSafetyObservedFeature(
                feature_id="f1",
                feature_kind="symptom",
                state="present",
                normalized_text="呼吸急促",
                temporal_scope="ongoing",
                resolution_state="ongoing",
            ),
        ),
        confidence=0.95,
        strategy="litellm_response_format",
        source_text="猫现在呼吸特别快。",
    )


def _candidate(
    *,
    asset_id: str,
    required_symptom: str | tuple[str, ...],
    severity: str = "urgent",
    species: tuple[str, ...] = ("cat", "dog"),
) -> ClinicalSafetyCandidate:
    """构造带自然语言症状前提的测试候选。

    :param asset_id: 测试资产标识。
    :param required_symptom: 自然语言准入前提。
    :param severity: 测试资产严重级别。
    :param species: 候选适用物种范围。
    :return: 返回临床安全候选对象。
    """
    required_symptoms = (
        (required_symptom,) if isinstance(required_symptom, str) else required_symptom
    )
    asset = ClinicalSafetyAsset(
        asset_id=asset_id,
        asset_type="emergency_red_flag",
        canonical_name=asset_id,
        category="呼吸循环",
        species_scope=species,
        sex_scope=(),
        age_scope=(),
        severity=severity,  # type: ignore[arg-type]
        action_class="emergency",
        code=f"TEST_{asset_id.upper()}",
        required_context={"species": species, "symptoms": required_symptoms},
    )
    return ClinicalSafetyCandidate(asset=asset, score=0.91, chunk_hits=())


def test_precondition_assessor_excludes_decision_fields_from_model_input() -> None:
    """验证前提评估器不把候选分数和风险等级传给语义模型。

    :return: 无返回值；断言通过表示事实蕴含判断不会被候选严重级别污染。
    """
    model_client = FakePreconditionModelClient()
    assessor = QwenClinicalSafetyPreconditionAssessor(model_client, Settings())

    result = asyncio.run(
        assessor.assess(
            _observed_semantic(),
            (_candidate(asset_id="a1", required_symptom="呼吸急促"),),
        )
    )

    request_payload = model_client.requests[0]
    serialized_request = json.dumps(request_payload, ensure_ascii=False)
    assessment = result.assessments["a1"]
    assert assessment.status == "satisfied"
    assert assessment.trusted is True
    assert assessment.evidence_ids == ("f1",)
    assert result.state.required_count == 1
    assert result.state.satisfied_count == 1
    assert "severity" not in serialized_request
    assert "action_class" not in serialized_request
    assert "score" not in serialized_request
    assert "TEST_A1" not in serialized_request
    assert "triage_message" not in serialized_request
    assert "source_text" not in serialized_request
    assert "matched_terms" not in serialized_request
    assert "species" not in serialized_request


def test_precondition_prompt_declares_any_of_and_canonical_order() -> None:
    """验证前提 prompt 显式 any_of 且使用与哈希一致的规范顺序。

    :return: 无返回值；断言通过表示同一哈希不会因召回排序变化得到不同输入。
    """
    model_client = FakePreconditionModelClient()
    assessor = QwenClinicalSafetyPreconditionAssessor(model_client, Settings())
    symptoms = ("呼吸急促", "牙龈发紫", "呼吸急促")

    asyncio.run(
        assessor.assess(
            _observed_semantic(),
            (_candidate(asset_id="a1", required_symptom=symptoms),),
        )
    )

    request_payload = model_client.requests[0]
    assert request_payload["combination_logic"] == "any_of"
    assert request_payload["items"][0]["required_context"]["symptoms"] == [
        "呼吸急促",
        "牙龈发紫",
    ]
    assert (
        "required_context.symptoms 是 any_of 完整准入描述集合。"
        in request_payload["rules"]
    )
    assert (
        "items 数组中的每个 item 都是独立判断任务，必须逐项独立评估。"
        in (request_payload["rules"])
    )


def test_precondition_canonical_context_matches_hash_representation() -> None:
    """验证 canonical required context 与哈希输入保持同一表示。

    :return: 无返回值；断言通过表示 prompt、资产分组和版本哈希共享无序集合语义。
    """
    required_context = {
        "species": ("cat", "dog", "cat"),
        "symptoms": ("呼吸困难", "牙龈发紫", "呼吸困难"),
    }

    canonical = clinical_safety_canonical_required_context(required_context)

    assert canonical == {
        "species": ("cat", "dog"),
        "symptoms": ("呼吸困难", "牙龈发紫"),
    }
    assert clinical_safety_required_context_hash(required_context).startswith("sha256:")


def test_required_context_hash_is_canonical_and_order_independent() -> None:
    """验证前提内容哈希不会受字段或值集合顺序影响。

    :return: 无返回值；断言通过表示相同前提内容可稳定复用评估结果。
    """
    first = clinical_safety_required_context_hash(
        {"species": ("cat", "dog"), "symptoms": ("呼吸困难", "牙龈发紫")}
    )
    second = clinical_safety_required_context_hash(
        {"symptoms": ("牙龈发紫", "呼吸困难"), "species": ("dog", "cat")}
    )

    assert first == second
    assert first.startswith("sha256:")


def test_precondition_reuses_semantic_hash_while_preserving_candidate_binding() -> None:
    """验证症状前提相同的候选复用模型结果且保留完整哈希绑定。

    :return: 无返回值；断言通过表示去重粒度只覆盖模型输入而不弱化 OPA 绑定。
    """
    model_client = FakePreconditionModelClient()
    assessor = QwenClinicalSafetyPreconditionAssessor(model_client, Settings())
    first = _candidate(
        asset_id="a1",
        required_symptom="呼吸急促",
        species=("cat", "dog"),
    )
    second = _candidate(
        asset_id="a2",
        required_symptom="呼吸急促",
        species=("cat",),
    )

    result = asyncio.run(assessor.assess(_observed_semantic(), (first, second)))

    semantic_premise_hash = clinical_safety_semantic_premise_hash(
        first.asset.required_context
    )
    assert semantic_premise_hash == clinical_safety_semantic_premise_hash(
        second.asset.required_context
    )
    assert len(model_client.requests) == 1
    assert model_client.requests[0]["items"] == [
        {
            "item_id": semantic_premise_hash,
            "required_context": {"symptoms": ["呼吸急促"]},
        }
    ]
    assert result.state.deduplicated_group_count == 1
    assert result.state.batch_count == 1
    assert result.assessments["a1"].semantic_premise_hash == semantic_premise_hash
    assert result.assessments["a2"].semantic_premise_hash == semantic_premise_hash
    assert result.assessments["a1"].required_context_hash == (
        clinical_safety_required_context_hash(first.asset.required_context)
    )
    assert result.assessments["a2"].required_context_hash == (
        clinical_safety_required_context_hash(second.asset.required_context)
    )
    assert (
        result.assessments["a1"].required_context_hash
        != result.assessments["a2"].required_context_hash
    )


def test_precondition_assessor_fails_closed_without_present_evidence() -> None:
    """验证没有当前回合 present 症状事实时不调用模型且全部 unknown。

    :return: 无返回值；断言通过表示前提评估不会用召回相似度补足事实。
    """
    model_client = FakePreconditionModelClient()
    assessor = QwenClinicalSafetyPreconditionAssessor(model_client, Settings())
    semantic = ClinicalSafetySemanticResult(
        symptom_state="present",
        risk_evidence_state="sufficient",
        confidence=0.95,
        strategy="litellm_response_format",
    )

    result = asyncio.run(
        assessor.assess(
            semantic, (_candidate(asset_id="a1", required_symptom="呼吸急促"),)
        )
    )

    assert result.assessments["a1"].status == "unknown"
    assert result.assessments["a1"].strategy == "no_present_evidence"
    assert result.state.unknown_count == 1
    assert result.state.requires_information is True
    assert model_client.requests == []


def test_precondition_assessor_uses_controlled_concurrency() -> None:
    """验证多候选前提评估按配置批量并发且不超过并发上限。

    :return: 无返回值；断言通过表示评估链路可兼顾响应速度和服务稳定性。
    """
    model_client = FakePreconditionModelClient(delay_seconds=0.02)
    settings = Settings(
        clinical_safety_precondition_batch_size=1,
        clinical_safety_precondition_max_concurrency=2,
    )
    assessor = QwenClinicalSafetyPreconditionAssessor(model_client, settings)
    candidates = tuple(
        _candidate(asset_id=f"a{index}", required_symptom=f"前提-{index}")
        for index in range(4)
    )

    result = asyncio.run(assessor.assess(_observed_semantic(), candidates))

    assert len(model_client.requests) == 4
    assert model_client.max_active_count <= 2
    assert all(item.status == "satisfied" for item in result.assessments.values())
    assert result.state.required_count == 4
    assert result.state.satisfied_count == 4
    assert result.state.requested_model == Settings().default_model
    assert result.state.model_candidates == (
        Settings().default_model,
        *Settings().qwen_fallback_models,
    )
    assert result.state.prompt_version == CLINICAL_SAFETY_PRECONDITION_PROMPT_VERSION
    assert (
        result.state.response_schema_version
        == CLINICAL_SAFETY_PRECONDITION_RESPONSE_SCHEMA_VERSION
    )
    assert result.state.batch_count == 4
    assert result.state.deduplicated_group_count == 4
    assert result.state.latency_ms >= 0


def test_precondition_assessor_fails_closed_on_total_timeout() -> None:
    """验证总截止时间超时后未完成前提会显式保持 unknown。

    :return: 无返回值；断言通过表示评估超时不会回退为关键词或候选分数判断。
    """
    model_client = FakePreconditionModelClient(delay_seconds=0.06)
    settings = Settings(
        clinical_safety_precondition_batch_size=1,
        clinical_safety_precondition_max_concurrency=1,
        clinical_safety_precondition_batch_timeout_seconds=0.08,
        clinical_safety_precondition_total_timeout_seconds=0.1,
    )
    assessor = QwenClinicalSafetyPreconditionAssessor(model_client, settings)
    candidates = tuple(
        _candidate(asset_id=f"a{index}", required_symptom=f"前提-{index}")
        for index in range(3)
    )

    result = asyncio.run(assessor.assess(_observed_semantic(), candidates))

    assert result.state.unknown_count > 0
    assert result.state.degraded is True
    assert "clinical_safety_precondition_total_timeout" in result.state.reasons
    assert any(
        item.strategy == "qwen_total_timeout" for item in result.assessments.values()
    )
    assert all(
        item.status in {"satisfied", "unknown"} for item in result.assessments.values()
    )


def test_precondition_assessor_rejects_low_confidence_entailment() -> None:
    """验证低置信语义蕴含结果会显式降级为 unknown。

    :return: 无返回值；断言通过表示模型置信度不足时不能支撑候选升级。
    """
    model_client = FakePreconditionModelClient(confidence=0.31)
    settings = Settings(clinical_safety_precondition_min_confidence=0.9)
    assessor = QwenClinicalSafetyPreconditionAssessor(model_client, settings)

    result = asyncio.run(
        assessor.assess(
            _observed_semantic(),
            (_candidate(asset_id="a1", required_symptom="呼吸急促"),),
        )
    )

    assessment = result.assessments["a1"]
    assert assessment.status == "unknown"
    assert assessment.trusted is False
    assert assessment.strategy == "qwen_low_confidence"
    assert result.state.degraded is True
    assert result.state.requires_information is False


def test_precondition_model_unknown_requests_more_information() -> None:
    """验证模型明确返回 unknown 时会向问诊领域透出信息缺口。

    :return: 无返回值；断言通过表示前提事实不足可进入追问联动而不是被静默吞掉。
    """
    model_client = FakePreconditionModelClient(status="unknown")
    assessor = QwenClinicalSafetyPreconditionAssessor(model_client, Settings())

    result = asyncio.run(
        assessor.assess(
            _observed_semantic(),
            (_candidate(asset_id="a1", required_symptom="呼吸急促"),),
        )
    )

    assert result.assessments["a1"].status == "unknown"
    assert result.assessments["a1"].strategy == "qwen_response_format"
    assert result.state.requires_information is True


def test_unavailable_precondition_assessor_returns_explicit_unknown() -> None:
    """验证评估器缺失时显式 Fail Closed 而不是启用本地规则回退。

    :return: 无返回值；断言通过表示不可用评估器不会补造前提满足结果。
    """
    assessor = UnavailableClinicalSafetyPreconditionAssessor()

    result = asyncio.run(
        assessor.assess(
            _observed_semantic(),
            (_candidate(asset_id="a1", required_symptom="呼吸急促"),),
        )
    )

    assert result.assessments["a1"].status == "unknown"
    assert result.assessments["a1"].strategy == "qwen_unavailable"
    assert result.state.degraded is True
    assert "clinical_safety_precondition_assessor_unavailable" in result.state.reasons


def test_precondition_assessor_rejects_invalid_configuration() -> None:
    """验证非法批量、并发或超时配置在初始化阶段快速失败。

    :return: 无返回值；断言通过表示运行期不会携带不可执行的调度配置。
    """
    settings = replace(Settings(), clinical_safety_precondition_batch_size=0)
    try:
        QwenClinicalSafetyPreconditionAssessor(FakePreconditionModelClient(), settings)
    except ValueError as exc:
        assert "batch size" in str(exc)
    else:
        raise AssertionError("invalid precondition batch size was accepted")

    oversized_settings = replace(
        Settings(),
        clinical_safety_precondition_batch_size=(
            CLINICAL_SAFETY_PRECONDITION_MAX_BATCH_SIZE + 1
        ),
    )
    try:
        QwenClinicalSafetyPreconditionAssessor(
            FakePreconditionModelClient(), oversized_settings
        )
    except ValueError as exc:
        assert "exceeds" in str(exc)
    else:
        raise AssertionError("oversized precondition batch size was accepted")

    invalid_confidence_settings = replace(
        Settings(),
        clinical_safety_precondition_min_confidence=1.1,
    )
    try:
        QwenClinicalSafetyPreconditionAssessor(
            FakePreconditionModelClient(), invalid_confidence_settings
        )
    except ValueError as exc:
        assert "confidence" in str(exc)
    else:
        raise AssertionError("invalid precondition confidence was accepted")

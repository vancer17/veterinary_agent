"""
文件：tests/integration/test_clinical_safety_api_external.py
作用：通过真实外部依赖验证临床安全裁决 API 纵向链路。
范围：仅覆盖临床安全语义抽取、PostgreSQL/pgvector 候选召回、OPA 策略裁决与
      API 响应审计 metadata，不验证 Mem0、普通问诊完整链路或输入安全阻断场景。
说明：本测试仅在显式开启 RUN_CLINICAL_SAFETY_API_EXTERNAL_TEST 时执行，供
      try-run 或人工发布前加严验证使用。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ingress import create_app, set_orchestrator
from vet_agent import Container, Settings, VetAgentIngressOrchestrator, set_container
from vet_agent.clinical_safety import (
    ClinicalSafetyAsset,
    ClinicalSafetyCandidate,
    ClinicalSafetyObservedFeature,
    ClinicalSafetyPolicyInput,
    ClinicalSafetyPolicyRequestContext,
    ClinicalSafetyPreconditionAssessment,
    ClinicalSafetyRetrievalState,
    ClinicalSafetySemanticExtractorAgent,
    ClinicalSafetySemanticResult,
    ClinicalSafetyThresholds,
    OpaClinicalSafetyPolicyClient,
    QwenClinicalSafetyPreconditionAssessor,
    clinical_safety_required_context_hash,
    clinical_safety_semantic_premise_hash,
)
from vet_agent.db import (
    ClinicalSafetyAssetModel,
    ClinicalSafetyChunkModel,
    KnowledgeChunkModel,
    make_session_factory,
)
from vet_agent.observability import AgentPathNode
from vet_agent.runtime import QwenClient

EXTERNAL_CLINICAL_SAFETY_FLAG = "RUN_CLINICAL_SAFETY_API_EXTERNAL_TEST"
DEFAULT_EXTERNAL_TIMEOUT_SECONDS = 45.0
DEFAULT_DATABASE_RETRY_ATTEMPTS = 3
DEFAULT_DATABASE_RETRY_DELAY_SECONDS = 2.0
EXPECTED_ALEMBIC_VERSION = "0020_clinical_safety_scope_value_domains"
EXPECTED_EMBEDDING_DIMENSION = 1024

_T = TypeVar("_T")
_ResponseT = TypeVar("_ResponseT", bound=BaseModel)


class _ExternalEnvironment(dict[str, str]):
    """表示对外部依赖配置的安全字典。

    :return: 无返回值；测试失败输出时会隐藏模型网关密钥。
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


class _RecordingQwenClient:
    """转发真实结构化模型调用并记录请求消息的集成测试客户端。"""

    def __init__(self, wrapped: QwenClient) -> None:
        """初始化真实模型转发代理。

        :param wrapped: 真实 Qwen/LiteLLM 客户端。
        :return: 无返回值。
        """
        self.wrapped = wrapped
        self.structured_messages: list[list[dict[str, str]]] = []

    @property
    def available(self) -> bool:
        """检查真实模型客户端是否可用。

        :return: LiteLLM 配置有效时返回 True。
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
        """记录请求并原样转发给真实结构化模型。

        :param messages: 前提评估器构造的消息列表。
        :param response_model: 结构化响应模型。
        :param model: 可选模型名称。
        :param temperature: 采样温度。
        :return: 返回真实模型结构化输出。
        """
        self.structured_messages.append([dict(item) for item in messages])
        return await self.wrapped.chat_structured(
            messages,
            response_model=response_model,
            model=model,
            temperature=temperature,
        )


@pytest.fixture
def external_env() -> _ExternalEnvironment:
    """读取临床安全外部 API 集成测试所需配置。

    :return: 返回外部依赖配置字典。
    """
    if not _enabled(EXTERNAL_CLINICAL_SAFETY_FLAG):
        pytest.skip(
            f"未开启 {EXTERNAL_CLINICAL_SAFETY_FLAG}，跳过临床安全外部 API 集成测试。"
        )
    required = {
        "DATABASE_URL": os.getenv("EXTERNAL_API_TEST_DATABASE_URL")
        or os.getenv("DATABASE_URL"),
        "LITELLM_BASE_URL": os.getenv("EXTERNAL_API_TEST_LITELLM_BASE_URL")
        or os.getenv("LITELLM_BASE_URL"),
        "LITELLM_API_KEY": os.getenv("EXTERNAL_API_TEST_LITELLM_API_KEY")
        or os.getenv("LITELLM_API_KEY")
        or os.getenv("LITELLM_MASTER_KEY"),
        "INPUT_SAFETY_OPA_BASE_URL": os.getenv("EXTERNAL_API_TEST_OPA_BASE_URL")
        or os.getenv("INPUT_SAFETY_OPA_BASE_URL"),
        "CLINICAL_SAFETY_OPA_BASE_URL": os.getenv("EXTERNAL_API_TEST_OPA_BASE_URL")
        or os.getenv("CLINICAL_SAFETY_OPA_BASE_URL")
        or os.getenv("INPUT_SAFETY_OPA_BASE_URL"),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        pytest.fail(
            f"{EXTERNAL_CLINICAL_SAFETY_FLAG}=true 时缺少临床安全外部依赖配置：{', '.join(missing)}。"
        )
    optional = {
        "QWEN_MODEL": os.getenv(
            "EXTERNAL_API_TEST_QWEN_MODEL", os.getenv("QWEN_MODEL", "qwen-plus")
        ),
        "QWEN_EMBEDDING_MODEL": os.getenv(
            "EXTERNAL_API_TEST_QWEN_EMBEDDING_MODEL",
            os.getenv("QWEN_EMBEDDING_MODEL", "text-embedding-v4"),
        ),
    }
    return _ExternalEnvironment({**required, **optional})


@pytest.fixture
def external_prefix() -> str:
    """构造本轮测试唯一数据前缀。

    :return: 返回测试数据前缀。
    """
    return f"clinical_it_{uuid4().hex[:12]}"


@pytest.fixture
def external_database(
    external_env: dict[str, str], external_prefix: str
) -> Iterator[str]:
    """准备临床安全裁决测试所需的最小数据库基线。

    :param external_env: 外部依赖配置。
    :param external_prefix: 测试数据前缀。
    :return: 返回数据库连接串。
    """
    database_url = external_env["DATABASE_URL"]

    def cleanup_database() -> None:
        """清理临床安全外部 API 测试数据。

        :return: 无返回值。
        """
        _cleanup_database_prefix(database_url, external_prefix)

    def assert_schema_ready() -> None:
        """校验外部数据库迁移版本。

        :return: 无返回值。
        """
        _assert_database_schema_ready(database_url)

    def prepare_baseline() -> None:
        """写入临床安全裁决测试所需最小资产和向量 chunk。

        :return: 无返回值。
        """
        _prepare_clinical_safety_baseline(database_url, external_env, external_prefix)

    def assert_baseline_ready() -> None:
        """校验最小临床安全基线数据已经可召回。

        :return: 无返回值。
        """
        _assert_clinical_safety_baseline_ready(database_url, external_prefix)

    _with_database_retry(cleanup_database, action="清理临床安全测试前缀数据")
    _with_database_retry(assert_schema_ready, action="校验临床安全数据库迁移版本")
    _with_database_retry(prepare_baseline, action="写入临床安全测试基线数据")
    _with_database_retry(assert_baseline_ready, action="校验临床安全测试基线数据")
    try:
        yield database_url
    finally:
        _with_database_retry(cleanup_database, action="清理临床安全测试前缀数据")


@pytest.fixture
def external_client(
    monkeypatch: pytest.MonkeyPatch,
    external_env: dict[str, str],
    external_database: str,
) -> Iterator[TestClient]:
    """构造接入真实外部依赖的本地 API 测试客户端。

    :param monkeypatch: pytest 环境变量替换工具。
    :param external_env: 外部依赖配置。
    :param external_database: 已校验的数据库连接串。
    :return: 返回 FastAPI 测试客户端。
    """
    del external_database
    with _configured_environment(monkeypatch, external_env):
        container = Container(Settings.from_env())
        if not container.ready:
            readiness = {
                name: bool(getattr(getattr(container, name), "is_ready")())
                for name in dir(container)
                if not name.startswith("_")
                and callable(getattr(getattr(container, name, None), "is_ready", None))
            }
            pytest.fail(f"真实外部依赖容器未就绪: {readiness}")
        set_container(container)
        set_orchestrator(VetAgentIngressOrchestrator(container))
        try:
            with TestClient(create_app()) as client:
                yield client
        finally:
            set_orchestrator(None)
            set_container(None)


@pytest.fixture
def clean_external_runtime_data(
    external_database: str, external_prefix: str
) -> Iterator[None]:
    """清理临床安全 API 测试运行期写入的数据库数据。

    :param external_database: 数据库连接串。
    :param external_prefix: 测试数据前缀。
    :return: 返回 fixture 迭代器。
    """

    def cleanup_runtime_data() -> None:
        """清理本轮测试写入的运行期数据。

        :return: 无返回值。
        """
        _cleanup_runtime_database_prefix(external_database, external_prefix)

    _with_database_retry(cleanup_runtime_data, action="清理临床安全 API 运行数据")
    try:
        yield
    finally:
        _with_database_retry(cleanup_runtime_data, action="清理临床安全 API 运行数据")


@pytest.mark.integration
def test_clinical_safety_external_dependencies_are_reachable(
    external_env: dict[str, str],
    external_database: str,
) -> None:
    """验证临床安全裁决所需真实外部依赖可达。

    :param external_env: 外部依赖配置。
    :param external_database: 已校验的数据库连接串。
    :return: 无返回值；断言通过表示临床安全外部 API 测试具备执行条件。
    """
    del external_database
    with httpx.Client(timeout=_timeout()) as client:
        lite_llm = client.get(
            f"{external_env['LITELLM_BASE_URL'].rstrip('/')}/models",
            headers={"Authorization": f"Bearer {external_env['LITELLM_API_KEY']}"},
        )
        lite_llm.raise_for_status()
        model_ids = {item.get("id") for item in lite_llm.json().get("data", [])}
        assert external_env["QWEN_MODEL"] in model_ids
        assert external_env["QWEN_EMBEDDING_MODEL"] in model_ids

        vector = _embedding_vector(
            external_env, "猫牙龈发紫，呼吸很快。", client=client
        )
        assert len(vector) == EXPECTED_EMBEDDING_DIMENSION

        clinical_opa = client.post(
            _opa_data_url(
                external_env["CLINICAL_SAFETY_OPA_BASE_URL"],
                "vet_agent.clinical_safety",
                "decision",
            ),
            json={"input": _empty_clinical_safety_policy_input()},
        )
        clinical_opa.raise_for_status()
        assert clinical_opa.json()["result"]["action"] == "allow"


@pytest.mark.integration
def test_clinical_safety_real_semantic_extractor_builds_observed_features(
    external_env: dict[str, str],
) -> None:
    """验证真实 LiteLLM 语义抽取能产生可信当前回合观察事实。

    :param external_env: 外部依赖配置。
    :return: 无返回值；断言通过表示阶段 3 的模型事实源具备真实可用性。
    """
    settings = _real_qwen_settings(external_env)
    extractor = ClinicalSafetySemanticExtractorAgent(QwenClient(settings), settings)

    result = asyncio.run(
        extractor.extract(
            user_text="猫现在牙龈发紫，呼吸特别快。",
            pet_context_summary="宠物画像: 物种=猫，年龄=5岁，性别=母。",
            model=external_env["QWEN_MODEL"],
        )
    )

    assert result.is_trusted() is True
    assert result.risk_evidence_state == "sufficient"
    assert result.symptom_state == "present"
    present_texts = [
        feature.normalized_text
        for feature in result.observed_features
        if feature.state == "present" and feature.feature_kind == "symptom"
    ]
    joined_texts = " ".join(present_texts)
    assert present_texts
    assert "呼吸" in joined_texts
    assert any(term in joined_texts for term in ("牙龈", "发紫", "发绀"))
    assert result.to_metadata()["fallback_state"]["degraded"] is False


@pytest.mark.integration
def test_clinical_safety_real_semantic_extractor_does_not_create_denied_present_fact(
    external_env: dict[str, str],
) -> None:
    """验证真实语义抽取不会把用户明确否定变成当前症状事实。

    :param external_env: 外部依赖配置。
    :return: 无返回值；断言通过表示否定事实不会进入前提升级证据。
    """
    settings = _real_qwen_settings(external_env)
    extractor = ClinicalSafetySemanticExtractorAgent(QwenClient(settings), settings)

    result = asyncio.run(
        extractor.extract(
            user_text="猫今天没有呕吐，也没有腹泻，精神和食欲都正常。",
            pet_context_summary="宠物画像: 物种=猫，年龄=5岁，性别=母。",
            model=external_env["QWEN_MODEL"],
        )
    )

    assert result.symptom_state != "present"
    assert result.risk_evidence_state != "sufficient"
    assert not any(
        feature.state == "present" and feature.feature_kind == "symptom"
        for feature in result.observed_features
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    (
        "scenario",
        "required_symptoms",
        "feature_specs",
        "allowed_statuses",
        "expected_calls",
    ),
    [
        pytest.param(
            "complete_combination",
            ("呼吸急促 + 黏膜发紫", "牙龈发紫"),
            (
                ("f1", "present", "呼吸很快"),
                ("f2", "present", "牙龈发紫"),
            ),
            ("satisfied",),
            1,
            id="complete-combination",
        ),
        pytest.param(
            "any_of_entry",
            ("呼吸急促", "牙龈发紫"),
            (("f1", "present", "呼吸很快"),),
            ("satisfied",),
            1,
            id="any-of-entry",
        ),
        pytest.param(
            "partial_combination",
            ("呼吸急促 + 黏膜发紫",),
            (("f1", "present", "呼吸很快"),),
            ("unknown",),
            1,
            id="partial-combination",
        ),
        pytest.param(
            "related_but_not_entailed",
            ("牙龈发紫",),
            (("f1", "present", "不吃东西"),),
            ("unknown", "not_satisfied"),
            1,
            id="related-but-not-entailed",
        ),
        pytest.param(
            "denied_condition",
            ("呕吐",),
            (
                ("f1", "present", "不吃东西"),
                ("f2", "denied", "呕吐"),
            ),
            ("unknown", "not_satisfied"),
            1,
            id="denied-condition",
        ),
        pytest.param(
            "resolved_condition",
            ("当前抽搐",),
            (
                ("f1", "present", "精神变差"),
                ("f2", "resolved", "抽搐"),
            ),
            ("unknown", "not_satisfied"),
            1,
            id="resolved-condition",
        ),
        pytest.param(
            "no_present_evidence",
            ("呼吸困难",),
            (("f1", "denied", "呼吸困难"),),
            ("unknown",),
            0,
            id="no-present-evidence",
        ),
    ],
)
def test_clinical_safety_real_precondition_assessor_semantic_contract(
    external_env: dict[str, str],
    scenario: str,
    required_symptoms: tuple[str, ...],
    feature_specs: tuple[tuple[str, str, str], ...],
    allowed_statuses: tuple[str, ...],
    expected_calls: int,
) -> None:
    """通过真实模型验证自然语言前提蕴含、部分满足和否定边界。

    :param external_env: 外部依赖配置。
    :param scenario: 集成测试场景名称。
    :param required_symptoms: 测试候选声明的条目级 any_of 症状前提。
    :param feature_specs: 固定观察事实、状态和自然语言表达。
    :param allowed_statuses: 当前医学语义场景允许的稳定状态。
    :param expected_calls: 该场景应发起的真实模型请求数。
    :return: 无返回值；断言通过表示真实模型满足阶段 3 核心契约。
    """
    del scenario
    settings = _real_qwen_settings(external_env)
    recording_client = _RecordingQwenClient(QwenClient(settings))
    assessor = QwenClinicalSafetyPreconditionAssessor(recording_client, settings)
    candidate = _precondition_candidate(
        asset_id="clinical_it_real_precondition",
        required_symptoms=required_symptoms,
        species=("cat", "dog"),
    )
    semantic_result = _semantic_result_from_feature_specs(feature_specs)

    result = asyncio.run(assessor.assess(semantic_result, (candidate,)))
    assessment = result.assessments[candidate.asset.asset_id]

    assert assessment.status in allowed_statuses
    assert len(recording_client.structured_messages) == expected_calls
    assert assessment.required_context_hash == clinical_safety_required_context_hash(
        candidate.asset.required_context
    )
    assert assessment.semantic_premise_hash == clinical_safety_semantic_premise_hash(
        candidate.asset.required_context
    )
    assert result.state.required_count == 1
    assert result.state.requested_model == external_env["QWEN_MODEL"]
    assert result.state.prompt_version
    assert result.state.response_schema_version
    assert result.state.latency_ms >= 0
    if expected_calls:
        _assert_real_precondition_prompt_boundary(
            recording_client.structured_messages[0]
        )
    if assessment.status == "satisfied":
        assert assessment.trusted is True
        assert (
            assessment.confidence
            >= settings.clinical_safety_precondition_min_confidence
        )
        assert assessment.evidence_ids
        assert all(
            _semantic_result_has_present_symptom(semantic_result, evidence_id)
            for evidence_id in assessment.evidence_ids
        )
    else:
        assert assessment.trusted is (assessment.status == "not_satisfied")


@pytest.mark.integration
def test_clinical_safety_real_precondition_reuses_semantic_hash_for_candidates(
    external_env: dict[str, str],
) -> None:
    """验证真实模型评估按症状前提去重且保留完整候选哈希绑定。

    :param external_env: 外部依赖配置。
    :return: 无返回值；断言通过表示双哈希设计在真实服务调用中生效。
    """
    settings = _real_qwen_settings(external_env)
    recording_client = _RecordingQwenClient(QwenClient(settings))
    assessor = QwenClinicalSafetyPreconditionAssessor(recording_client, settings)
    first = _precondition_candidate(
        asset_id="clinical_it_real_precondition_shared_a",
        required_symptoms=("呼吸急促 + 黏膜发紫",),
        species=("cat", "dog"),
    )
    second = _precondition_candidate(
        asset_id="clinical_it_real_precondition_shared_b",
        required_symptoms=("呼吸急促 + 黏膜发紫",),
        species=("cat",),
    )
    semantic_result = _semantic_result_from_feature_specs(
        (
            ("f1", "present", "呼吸很快"),
            ("f2", "present", "牙龈发紫"),
        )
    )

    result = asyncio.run(assessor.assess(semantic_result, (first, second)))

    assert len(recording_client.structured_messages) == 1
    assert result.state.deduplicated_group_count == 1
    assert result.assessments[first.asset.asset_id].semantic_premise_hash == (
        result.assessments[second.asset.asset_id].semantic_premise_hash
    )
    assert result.assessments[first.asset.asset_id].required_context_hash != (
        result.assessments[second.asset.asset_id].required_context_hash
    )


@pytest.mark.integration
def test_clinical_safety_real_opa_plan_and_decision_consume_precondition_contract(
    external_env: dict[str, str],
) -> None:
    """验证真实 OPA 前提计划与最终裁决消费阶段 3 结构化契约。

    :param external_env: 外部依赖配置。
    :return: 无返回值；断言通过表示远程策略已加载当前前提裁决规则。
    """
    client = OpaClinicalSafetyPolicyClient(
        base_url=external_env["CLINICAL_SAFETY_OPA_BASE_URL"],
        version="v1",
        package_path="vet_agent.clinical_safety",
        rule_name="decision",
        timeout_seconds=_timeout(),
    )
    candidate = _precondition_candidate(
        asset_id="clinical_it_real_opa_precondition",
        required_symptoms=("呼吸急促 + 黏膜发紫",),
        species=("cat", "dog"),
        severity="urgent",
    )
    semantic_result = _semantic_result_from_feature_specs(
        (
            ("f1", "present", "呼吸很快"),
            ("f2", "present", "牙龈发紫"),
        )
    )
    assessment = ClinicalSafetyPreconditionAssessment(
        asset_id=candidate.asset.asset_id,
        required_context_hash=clinical_safety_required_context_hash(
            candidate.asset.required_context
        ),
        semantic_premise_hash=clinical_safety_semantic_premise_hash(
            candidate.asset.required_context
        ),
        status="satisfied",
        evidence_ids=("f1", "f2"),
        confidence=0.95,
        strategy="qwen_response_format",
    )
    policy_input = ClinicalSafetyPolicyInput(
        context=ClinicalSafetyPolicyRequestContext(
            request_id="req_clinical_real_precondition",
            trace_id="trace_clinical_real_precondition",
        ),
        semantic_result=semantic_result,
        retrieval_state=ClinicalSafetyRetrievalState(
            stage="vector",
            retrieval_source="clinical_safety_pgvector",
            vector_hit_count=1,
            candidate_count=1,
        ),
        candidates=(candidate,),
        thresholds=ClinicalSafetyThresholds(
            retrieval_min_score=0.2,
            signal_min_score=0.65,
            urgent_min_score=0.75,
        ),
        precondition_assessments={candidate.asset.asset_id: assessment},
    )
    mismatched_candidate = _precondition_candidate(
        asset_id="clinical_it_real_opa_mismatch",
        required_symptoms=("呼吸困难",),
        species=("cat",),
        severity="urgent",
    )
    mismatched_input = ClinicalSafetyPolicyInput(
        context=policy_input.context,
        semantic_result=_semantic_result_from_feature_specs(
            (("f1", "present", "呼吸很快"),),
            species="dog",
        ),
        retrieval_state=policy_input.retrieval_state,
        candidates=(mismatched_candidate,),
        thresholds=policy_input.thresholds,
    )

    planned_asset_ids = asyncio.run(client.plan_preconditions(policy_input))
    mismatched_asset_ids = asyncio.run(client.plan_preconditions(mismatched_input))
    decision = asyncio.run(client.decide(policy_input))

    assert planned_asset_ids == (candidate.asset.asset_id,)
    assert mismatched_asset_ids == ()
    assert decision.escalated is True
    assert decision.metadata["policy_backend"] == "opa"
    assert any(
        signal.code == candidate.asset.code and signal.severity == "urgent"
        for signal in decision.signals
    )


@pytest.mark.integration
def test_clinical_safety_api_uses_real_semantic_pgvector_and_opa(
    external_client: TestClient,
    clean_external_runtime_data: None,
    external_prefix: str,
) -> None:
    """验证 API 临床安全裁决链路使用真实语义抽取、pgvector 召回和 OPA 裁决。

    :param external_client: 接入真实外部依赖的 API 测试客户端。
    :param clean_external_runtime_data: 运行期数据清理 fixture。
    :param external_prefix: 测试数据前缀。
    :return: 无返回值；断言通过表示临床安全裁决迁移后的真实主路径可用。
    """
    del clean_external_runtime_data
    user_id, pet_id, session_id = _scope_ids(external_prefix)
    response = external_client.post(
        "/agent/turns",
        json=_payload(
            "猫现在牙龈发紫，呼吸很快。",
            user_id=user_id,
            pet_id=pet_id,
            session_id=session_id,
            profile={"species": "猫", "age": "5岁", "sex": "female", "weight_kg": 4.2},
        ),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "safety_escalated"
    assert data["output_text"].strip()
    assert any(
        signal["code"] == "CYANOSIS_RISK_PATTERN" for signal in data["safety_signals"]
    )

    metadata = data["metadata"]
    _assert_policy_path_is_named(metadata["multi_agent_path"])
    _assert_input_safety_allows_with_opa(metadata)
    _assert_clinical_semantic_uses_litellm(metadata)
    _assert_clinical_safety_uses_pgvector(metadata)
    _assert_clinical_safety_uses_opa(metadata)
    resolution = metadata["clinical_safety_resolution"]
    precondition = resolution["fallback_state"]["precondition"]
    assert precondition["required_count"] >= 1
    assert precondition["satisfied_count"] >= 1
    assert precondition["unknown_count"] == 0
    assert resolution["requires_precondition_information"] is False


@pytest.mark.integration
def test_clinical_safety_api_precondition_unknown_routes_to_real_followup(
    external_client: TestClient,
    clean_external_runtime_data: None,
    external_prefix: str,
) -> None:
    """验证真实链路中部分满足前提会进入问诊追问而不是误升级。

    :param external_client: 接入真实外部依赖的 API 测试客户端。
    :param clean_external_runtime_data: 运行期数据清理 fixture。
    :param external_prefix: 测试数据前缀。
    :return: 无返回值；断言通过表示真实模型、OPA 与问诊状态完成 unknown 联动。
    """
    del clean_external_runtime_data
    user_id, pet_id, session_id = _scope_ids(external_prefix)
    payload = _payload(
        "猫现在呼吸有一点快，但牙龈颜色我不确定。",
        user_id=user_id,
        pet_id=pet_id,
        session_id=session_id,
        profile={"species": "猫", "age": "5岁", "sex": "female", "weight_kg": 4.2},
    )
    payload["turn_options"]["idempotency_key"] = (
        f"idem_{session_id}_precondition_unknown"
    )
    response = external_client.post("/agent/turns", json=payload)

    assert response.status_code == 200, response.text
    data = response.json()
    metadata = data["metadata"]
    resolution = metadata["clinical_safety_resolution"]
    precondition = resolution["fallback_state"]["precondition"]
    assert data["status"] == "requires_followup"
    assert not any(
        signal["severity"] in {"urgent", "blocked"} for signal in data["safety_signals"]
    )
    assert precondition["unknown_count"] >= 1
    assert precondition["satisfied_count"] == 0
    assert resolution["requires_precondition_information"] is True
    assert metadata["answerability"]["mode"] == "clinical_safety_precondition_unknown"
    assert "symptom_detail" in metadata["answerability"]["blocking_slots"]


def _assert_policy_path_is_named(agent_path: list[str]) -> None:
    """验证临床安全 API 响应审计路径使用领域化策略节点。

    :param agent_path: API metadata.multi_agent_path 返回的审计路径。
    :return: 无返回值；断言通过表示审计路径未退化为裸 OPA 命名。
    """
    assert "OPA" not in agent_path
    assert AgentPathNode.INPUT_SAFETY_SERVICE.value in agent_path
    assert AgentPathNode.INPUT_SAFETY_POLICY_OPA.value in agent_path
    assert AgentPathNode.CLINICAL_SAFETY_SEMANTIC_EXTRACTOR_AGENT.value in agent_path
    assert AgentPathNode.CLINICAL_SAFETY_EVALUATOR.value in agent_path
    assert AgentPathNode.CLINICAL_SAFETY_POLICY_OPA.value in agent_path


def _assert_input_safety_allows_with_opa(metadata: dict[str, Any]) -> None:
    """验证临床安全链路前置输入安全裁决由 OPA 放行。

    :param metadata: API 响应 metadata 字典。
    :return: 无返回值；断言通过表示临床安全测试没有绕过输入安全前置门禁。
    """
    decision = metadata["input_safety_decision"]
    assert decision["allow"] is True
    assert decision["policy_backend"] == "opa"


def _assert_clinical_semantic_uses_litellm(metadata: dict[str, Any]) -> None:
    """验证临床安全语义抽取使用真实 LiteLLM response_format 路径。

    :param metadata: API 响应 metadata 字典。
    :return: 无返回值；断言通过表示语义抽取未被关闭或降级为本地空结果。
    """
    semantic = metadata["clinical_safety_semantic"]
    assert semantic["strategy"] in {
        "litellm_response_format",
        "litellm_response_format_low_confidence",
    }
    assert semantic["fallback_state"]["stage"] in {"llm", "llm_low_confidence"}


def _assert_clinical_safety_uses_pgvector(metadata: dict[str, Any]) -> None:
    """验证临床安全候选召回来自 PostgreSQL/pgvector 主路径。

    :param metadata: API 响应 metadata 字典。
    :return: 无返回值；断言通过表示候选未由文本关键词或硬编码回退生成。
    """
    retrieval = metadata["clinical_safety_resolution"]["fallback_state"]["retrieval"]
    assert retrieval["stage"] == "vector"
    assert retrieval["retrieval_source"] == "clinical_safety_pgvector"
    assert retrieval["vector_hit_count"] > 0
    assert retrieval["candidate_count"] > 0
    assert retrieval["degraded"] is False


def _assert_clinical_safety_uses_opa(metadata: dict[str, Any]) -> None:
    """验证临床安全动作裁决来自真实 OPA 策略后端。

    :param metadata: API 响应 metadata 字典。
    :return: 无返回值；断言通过表示临床安全裁决未走本地测试替身或旧规则路径。
    """
    decision = metadata["clinical_safety_resolution"]["policy_decision"]
    assert decision["action"] == "escalate"
    assert decision["allow"] is True
    assert decision["policy_backend"] == "opa"
    assert decision["policy_path"] == "vet_agent.clinical_safety/decision"
    assert any(
        signal["code"] == "CYANOSIS_RISK_PATTERN" for signal in decision["signals"]
    )


@contextmanager
def _configured_environment(
    monkeypatch: pytest.MonkeyPatch,
    external_env: dict[str, str],
) -> Iterator[None]:
    """向当前进程注入临床安全外部 API 集成测试配置。

    :param monkeypatch: pytest 环境变量替换工具。
    :param external_env: 外部依赖配置。
    :return: 返回上下文管理器迭代器。
    """
    values = {
        "DATABASE_URL": external_env["DATABASE_URL"],
        "LITELLM_BASE_URL": external_env["LITELLM_BASE_URL"].rstrip("/"),
        "LITELLM_API_KEY": external_env["LITELLM_API_KEY"],
        "QWEN_MODEL": external_env["QWEN_MODEL"],
        "QWEN_EMBEDDING_MODEL": external_env["QWEN_EMBEDDING_MODEL"],
        "ENABLE_RAG_EMBEDDINGS": "true",
        "CLINICAL_SAFETY_VECTOR_MIN_SCORE": "0.2",
        "ANSWER_RAG_VECTOR_MIN_SCORE": "0.1",
        "FOLLOWUP_RAG_VECTOR_MIN_SCORE": "0.1",
        "ENABLE_MEM0": "false",
        "ENABLE_INPUT_SAFETY": "true",
        "INPUT_SAFETY_POLICY_BACKEND": "opa",
        "INPUT_SAFETY_OPA_BASE_URL": external_env["INPUT_SAFETY_OPA_BASE_URL"].rstrip(
            "/"
        ),
        "CLINICAL_SAFETY_OPA_BASE_URL": external_env[
            "CLINICAL_SAFETY_OPA_BASE_URL"
        ].rstrip("/"),
        "CONSULTATION_ANSWERABILITY_OPA_BASE_URL": external_env[
            "CLINICAL_SAFETY_OPA_BASE_URL"
        ].rstrip("/"),
        "TASK_ROUTING_OPA_BASE_URL": external_env[
            "CLINICAL_SAFETY_OPA_BASE_URL"
        ].rstrip("/"),
        "ENABLE_INPUT_SAFETY_GUARDRAILS": "false",
        "ENABLE_LLM_SEMANTIC_EXTRACTION": "true",
        "ENABLE_LLM_TASK_SPLITTER": "false",
        "ENABLE_MEMORY_EXTRACTION": "false",
        "QWEN_MAX_RETRIES": "0",
        "QWEN_CIRCUIT_BREAKER_FAILURE_THRESHOLD": "20",
        "LITELLM_TIMEOUT_SECONDS": str(_timeout()),
        "REQUIRE_API_AUTH": "false",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    yield


def _payload(
    text_value: str,
    *,
    user_id: str,
    pet_id: str,
    session_id: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """构造临床安全 API 集成测试请求载荷。

    :param text_value: 用户输入文本。
    :param user_id: 测试用户标识。
    :param pet_id: 测试宠物标识。
    :param session_id: 测试会话标识。
    :param profile: 可信宠物画像。
    :return: 返回 API 请求载荷。
    """
    return {
        "input": text_value,
        "stream": False,
        "scope_assertion": _scope_assertion(
            user_id=user_id,
            pet_id=pet_id,
            session_id=session_id,
            profile=profile,
        ),
        "vet_context": {"pet_info": profile},
        "attachments": [],
        "turn_options": {"idempotency_key": f"idem_{session_id}"},
    }


def _scope_assertion(
    *,
    user_id: str,
    pet_id: str,
    session_id: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """构造临床安全 API 集成测试使用的可信范围声明。

    :param user_id: 测试用户标识。
    :param pet_id: 测试宠物标识。
    :param session_id: 测试会话标识。
    :param profile: 可信宠物画像。
    :return: 返回范围声明字典。
    """
    now = datetime.now(UTC).isoformat()
    return {
        "schema_version": "v1",
        "issuer": "clinical-safety-api-integration-test",
        "issued_at": now,
        "user_id": user_id,
        "pet_id": pet_id,
        "session_id": session_id,
        "authorization": {
            "ownership_verified": True,
            "pet_active": True,
            "pet_status": "active",
            "pet_deleted": False,
        },
        "profile": profile,
        "source": {
            "system": "clinical-safety-api-integration-test",
            "database": "vet_agent",
            "table": "master_pet_info",
            "record_id": pet_id,
            "record_updated_at": now,
            "data_source": "integration_test",
        },
        "session_policy": {"binding_mode": "single_user_pet_per_session"},
    }


def _assert_database_schema_ready(database_url: str) -> None:
    """确认外部数据库迁移版本满足临床安全裁决测试要求。

    :param database_url: 数据库连接串。
    :return: 无返回值。
    """
    session_factory = make_session_factory(database_url)
    with session_factory() as session:
        version = session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
        if version != EXPECTED_ALEMBIC_VERSION:
            pytest.fail(
                "外部数据库未迁移到当前临床安全 API 测试所需版本。"
                f" 当前版本：{version!r}，期望版本：{EXPECTED_ALEMBIC_VERSION!r}。"
            )


def _prepare_clinical_safety_baseline(
    database_url: str,
    external_env: dict[str, str],
    prefix: str,
) -> None:
    """写入临床安全裁决 API 测试的最小资产与向量 chunk。

    :param database_url: 数据库连接串。
    :param external_env: 外部依赖配置。
    :param prefix: 测试数据前缀。
    :return: 无返回值。
    """
    now = datetime.now(UTC)
    safety_asset_id = _safety_asset_id(prefix)
    safety_chunk_id = _safety_chunk_id(prefix)
    embedding_text = _clinical_safety_embedding_text()
    embedding = _embedding_vector(external_env, embedding_text)
    answer_embedding = _embedding_vector(
        external_env,
        "猫呼吸急促或黏膜发紫时需要优先排查缺氧、呼吸循环急症和近期变化。",
    )
    followup_embedding = _embedding_vector(
        external_env,
        "猫呼吸异常时应追问呼吸频率、牙龈颜色、精神状态、开始时间和是否运动后发作。",
    )
    for embedding_name, embedding_value in (
        ("clinical safety", embedding),
        ("answer RAG", answer_embedding),
        ("followup RAG", followup_embedding),
    ):
        if len(embedding_value) != EXPECTED_EMBEDDING_DIMENSION:
            pytest.fail(
                f"外部 embedding 维度不符合 {embedding_name} 要求：{len(embedding_value)}。"
            )
    if len(embedding) != EXPECTED_EMBEDDING_DIMENSION:
        pytest.fail(
            f"外部 embedding 维度不符合 clinical_safety_chunks.embedding 要求：{len(embedding)}。"
        )

    session_factory = make_session_factory(database_url)
    with session_factory.begin() as session:
        asset = session.get(ClinicalSafetyAssetModel, safety_asset_id)
        if asset is None:
            asset = ClinicalSafetyAssetModel(asset_id=safety_asset_id)
            session.add(asset)
        asset.code = "CYANOSIS_RISK_PATTERN"
        asset.asset_type = "emergency_red_flag"
        asset.canonical_name = "舌/牙龈发绀发紫"
        asset.category = "clinical_safety_external_api_test"
        asset.species_scope = ["dog", "cat"]
        asset.sex_scope = []
        asset.age_scope = []
        asset.severity = "urgent"
        asset.action_class = "emergency"
        asset.aliases = ["牙龈发紫", "舌头发青", "发绀"]
        asset.carriers = []
        asset.user_expressions = ["猫现在牙龈发紫，呼吸很快。"]
        asset.symptoms = ["牙龈发紫", "呼吸很快"]
        asset.recognition_phrases = ["牙龈发紫并呼吸很快", "舌头发青", "发绀"]
        asset.required_context = {
            "species": ["dog", "cat"],
            "symptoms": ["呼吸急促 + 黏膜发紫", "牙龈发紫"],
        }
        asset.decision_hints = {"symptom": "emergency"}
        asset.clinical_risk_summary = (
            "黏膜发紫或发绀可提示缺氧或循环异常，需要按急症处理。"
        )
        asset.triage_message = (
            "如仍在发紫、呼吸急促或精神明显异常，应立即联系线下急诊兽医。"
        )
        asset.source = {"system": "clinical_safety_external_api_test"}
        asset.raw_text = {"embedding_text": embedding_text}
        asset.version = "clinical_safety_external_api_test"
        asset.enabled = True
        asset.review_status = "approved"
        asset.published_at = now
        asset.metadata_json = {
            "clinical_safety_external_api_test": True,
            "prefix": prefix,
        }

        chunk = session.get(ClinicalSafetyChunkModel, safety_chunk_id)
        if chunk is None:
            chunk = ClinicalSafetyChunkModel(
                chunk_id=safety_chunk_id, asset_id=safety_asset_id
            )
            session.add(chunk)
        chunk.asset_id = safety_asset_id
        chunk.chunk_type = "recognition"
        chunk.title = "舌/牙龈发绀发紫 临床安全外部 API 识别 chunk"
        chunk.embedding_text = embedding_text
        chunk.embedding = embedding
        chunk.embedding_model = external_env["QWEN_EMBEDDING_MODEL"]
        chunk.embedding_dimension = len(embedding)
        chunk.content_hash = _content_hash(embedding_text)
        chunk.version = "clinical_safety_external_api_test"
        chunk.enabled = True
        chunk.review_status = "approved"
        chunk.metadata_json = {
            "clinical_safety_external_api_test": True,
            "prefix": prefix,
        }
        session.add(
            KnowledgeChunkModel(
                source="clinical_safety_external_api_test",
                title="猫呼吸循环急症回答基线",
                content="猫呼吸急促、牙龈或舌头发紫提示可能缺氧，需要按急症风险处理。",
                embedding=answer_embedding,
                public_citation=True,
                copyright_risk="low",
                domain="general",
                species="cat",
                version="v1",
                enabled=True,
                review_status="approved",
                quality_score=1.0,
                last_reviewed_at=now,
                ingestion_batch=prefix,
                metadata_json={
                    "chunk_type": "condition_overview",
                    "clinical_safety_external_api_test": True,
                    "prefix": prefix,
                },
            )
        )
        session.add(
            KnowledgeChunkModel(
                source="clinical_safety_external_api_test",
                title="猫呼吸循环急症追问基线",
                content=(
                    "猫呼吸异常时应追问呼吸频率、牙龈颜色、精神状态、开始时间、"
                    "是否运动后发作以及是否伴随张口呼吸。"
                ),
                embedding=followup_embedding,
                public_citation=True,
                copyright_risk="low",
                domain="general",
                species="cat",
                version="v1",
                enabled=True,
                review_status="approved",
                quality_score=1.0,
                last_reviewed_at=now,
                ingestion_batch=prefix,
                metadata_json={
                    "chunk_type": "followup_questions",
                    "clinical_safety_external_api_test": True,
                    "prefix": prefix,
                },
            )
        )


def _assert_clinical_safety_baseline_ready(database_url: str, prefix: str) -> None:
    """确认临床安全最小测试基线数据已经写入。

    :param database_url: 数据库连接串。
    :param prefix: 测试数据前缀。
    :return: 无返回值。
    """
    session_factory = make_session_factory(database_url)
    with session_factory() as session:
        chunk_count = int(
            session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM clinical_safety_chunks
                    WHERE enabled IS TRUE
                      AND review_status = 'approved'
                      AND embedding IS NOT NULL
                      AND chunk_id = :chunk_id
                    """
                ),
                {"chunk_id": _safety_chunk_id(prefix)},
            ).scalar_one()
            or 0
        )
        if chunk_count != 1:
            pytest.fail("外部数据库缺少临床安全裁决测试所需向量 chunk。")
        knowledge_chunk_types = set(
            session.execute(
                text(
                    """
                    SELECT coalesce(metadata ->> 'chunk_type', '')
                    FROM knowledge_chunks
                    WHERE ingestion_batch = :prefix
                      AND enabled IS TRUE
                      AND review_status = 'approved'
                      AND embedding IS NOT NULL
                    """
                ),
                {"prefix": prefix},
            ).scalars()
        )
        if knowledge_chunk_types != {"condition_overview", "followup_questions"}:
            pytest.fail("外部数据库缺少临床安全 API 测试所需回答和追问知识基线。")


def _cleanup_database_prefix(database_url: str, prefix: str) -> None:
    """按测试前缀清理临床安全基线和运行期数据。

    :param database_url: 数据库连接串。
    :param prefix: 测试数据前缀。
    :return: 无返回值。
    """
    _cleanup_runtime_database_prefix(database_url, prefix)
    _cleanup_baseline_database_prefix(database_url, prefix)


def _cleanup_runtime_database_prefix(database_url: str, prefix: str) -> None:
    """按测试前缀清理临床安全 API 运行期数据。

    :param database_url: 数据库连接串。
    :param prefix: 测试数据前缀。
    :return: 无返回值。
    """
    session_factory = make_session_factory(database_url)
    user_pattern = f"{prefix}%"
    pet_pattern = f"{prefix}%"
    session_pattern = f"{prefix}%"
    with session_factory.begin() as session:
        for sql in (
            "DELETE FROM idempotency_records WHERE user_id LIKE :user_pattern OR pet_id LIKE :pet_pattern OR session_id LIKE :session_pattern",
            "DELETE FROM logic_traces WHERE user_id LIKE :user_pattern OR pet_id LIKE :pet_pattern OR session_id LIKE :session_pattern",
            "DELETE FROM pet_memory_facts WHERE user_id LIKE :user_pattern OR pet_id LIKE :pet_pattern",
            "DELETE FROM pet_memory_episodes WHERE user_id LIKE :user_pattern OR pet_id LIKE :pet_pattern OR session_id LIKE :session_pattern",
            "DELETE FROM consultation_states WHERE user_id LIKE :user_pattern OR pet_id LIKE :pet_pattern OR session_id LIKE :session_pattern",
            "DELETE FROM conversation_turns WHERE user_id LIKE :user_pattern OR pet_id LIKE :pet_pattern OR session_id LIKE :session_pattern",
            "DELETE FROM pet_session_bindings WHERE user_id LIKE :user_pattern OR pet_id LIKE :pet_pattern OR session_id LIKE :session_pattern",
            "DELETE FROM pet_profiles WHERE user_id LIKE :user_pattern OR pet_id LIKE :pet_pattern",
        ):
            session.execute(
                text(sql),
                {
                    "user_pattern": user_pattern,
                    "pet_pattern": pet_pattern,
                    "session_pattern": session_pattern,
                },
            )


def _cleanup_baseline_database_prefix(database_url: str, prefix: str) -> None:
    """按测试前缀清理临床安全最小基线数据。

    :param database_url: 数据库连接串。
    :param prefix: 测试数据前缀。
    :return: 无返回值。
    """
    session_factory = make_session_factory(database_url)
    with session_factory.begin() as session:
        session.execute(
            text(
                """
                DELETE FROM knowledge_chunks
                WHERE ingestion_batch = :prefix
                   OR metadata ->> 'prefix' = :prefix
                """
            ),
            {"prefix": prefix},
        )
        session.execute(
            text(
                "DELETE FROM clinical_safety_chunks WHERE chunk_id = :chunk_id OR asset_id = :asset_id"
            ),
            {
                "chunk_id": _safety_chunk_id(prefix),
                "asset_id": _safety_asset_id(prefix),
            },
        )
        session.execute(
            text("DELETE FROM clinical_safety_assets WHERE asset_id = :asset_id"),
            {"asset_id": _safety_asset_id(prefix)},
        )


def _with_database_retry(operation: Callable[[], _T], *, action: str) -> _T:
    """对外部开发数据库短暂波动进行有限重试。

    :param operation: 需要执行的数据库操作。
    :param action: 当前操作说明，用于失败诊断。
    :return: 返回数据库操作结果。
    """
    attempts = int(
        os.getenv(
            "EXTERNAL_API_TEST_DATABASE_RETRY_ATTEMPTS",
            str(DEFAULT_DATABASE_RETRY_ATTEMPTS),
        )
    )
    delay_seconds = float(
        os.getenv(
            "EXTERNAL_API_TEST_DATABASE_RETRY_DELAY_SECONDS",
            str(DEFAULT_DATABASE_RETRY_DELAY_SECONDS),
        )
    )
    last_error: SQLAlchemyError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except SQLAlchemyError as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(delay_seconds)
    raise AssertionError(f"外部数据库操作失败：{action}。") from last_error


def _scope_ids(prefix: str) -> tuple[str, str, str]:
    """构造临床安全 API 测试的用户、宠物和会话标识。

    :param prefix: 测试数据前缀。
    :return: 返回 user_id、pet_id 和 session_id。
    """
    return (
        f"{prefix}_user_clinical",
        f"{prefix}_pet_clinical",
        f"{prefix}_session_clinical",
    )


def _real_qwen_settings(external_env: dict[str, str]) -> Settings:
    """构造面向真实 LiteLLM 服务的临床安全测试配置。

    :param external_env: 外部依赖配置。
    :return: 返回禁用生产重试放大并放宽模型调用超时的配置对象。
    """
    timeout_seconds = _timeout()
    return Settings(
        litellm_api_key=external_env["LITELLM_API_KEY"],
        litellm_base_url=external_env["LITELLM_BASE_URL"].rstrip("/"),
        default_model=external_env["QWEN_MODEL"],
        qwen_embedding_model=external_env["QWEN_EMBEDDING_MODEL"],
        request_timeout_seconds=timeout_seconds,
        qwen_max_retries=0,
        qwen_circuit_breaker_failure_threshold=20,
        clinical_safety_precondition_batch_size=1,
        clinical_safety_precondition_max_concurrency=2,
        clinical_safety_precondition_batch_timeout_seconds=timeout_seconds,
        clinical_safety_precondition_total_timeout_seconds=timeout_seconds + 2.0,
        clinical_safety_precondition_min_confidence=0.65,
    )


def _precondition_candidate(
    *,
    asset_id: str,
    required_symptoms: tuple[str, ...],
    species: tuple[str, ...],
    severity: str = "caution",
) -> ClinicalSafetyCandidate:
    """构造真实前提评估使用的最小临床安全候选。

    :param asset_id: 测试资产标识。
    :param required_symptoms: 条目级 any_of 自然语言症状前提。
    :param species: 候选适用物种范围。
    :param severity: 候选严重级别。
    :return: 返回不依赖数据库和召回器的临床安全候选。
    """
    asset = ClinicalSafetyAsset(
        asset_id=asset_id,
        asset_type="emergency_red_flag",
        canonical_name=asset_id,
        category="clinical_safety_external_precondition_test",
        species_scope=species,
        sex_scope=(),
        age_scope=(),
        severity=severity,  # type: ignore[arg-type]
        action_class="emergency" if severity == "urgent" else "safety_warning",
        code=asset_id.upper(),
        required_context={"species": species, "symptoms": required_symptoms},
    )
    return ClinicalSafetyCandidate(asset=asset, score=0.91, chunk_hits=())


def _semantic_result_from_feature_specs(
    feature_specs: tuple[tuple[str, str, str], ...],
    *,
    species: str = "cat",
) -> ClinicalSafetySemanticResult:
    """从固定事实描述构造真实前提评估输入。

    :param feature_specs: feature id、状态和自然语言事实三元组。
    :param species: 当前回合结构化物种。
    :return: 返回不经过召回链路的可信语义结果。
    """
    features = tuple(
        ClinicalSafetyObservedFeature(
            feature_id=feature_id,
            feature_kind="symptom",
            state=feature_state,  # type: ignore[arg-type]
            normalized_text=normalized_text,
            temporal_scope="recent_past" if feature_state == "resolved" else "ongoing",
            resolution_state="resolved" if feature_state == "resolved" else "ongoing",
        )
        for feature_id, feature_state, normalized_text in feature_specs
    )
    has_present = any(feature.state == "present" for feature in features)
    has_denied = any(feature.state == "denied" for feature in features)
    return ClinicalSafetySemanticResult(
        species=species,  # type: ignore[arg-type]
        symptom_state="present"
        if has_present
        else "denied"
        if has_denied
        else "unknown",
        intent_type="symptom",
        risk_evidence_state="sufficient" if has_present else "insufficient",
        observed_features=features,
        confidence=0.95,
        strategy="litellm_response_format",
        source_text="clinical safety external precondition fixture",
    )


def _semantic_result_has_present_symptom(
    semantic_result: ClinicalSafetySemanticResult,
    feature_id: str,
) -> bool:
    """判断指定证据是否为当前回合 present 症状事实。

    :param semantic_result: 当前回合固定语义结果。
    :param feature_id: 前提评估返回的证据标识。
    :return: 证据存在且为 present 症状时返回 True。
    """
    return any(
        feature.feature_id == feature_id
        and feature.feature_kind == "symptom"
        and feature.state == "present"
        for feature in semantic_result.observed_features
    )


def _assert_real_precondition_prompt_boundary(messages: list[dict[str, str]]) -> None:
    """验证真实前提评估 prompt 不包含候选风险和处置字段。

    :param messages: 转发到真实模型的消息列表。
    :return: 无返回值；断言通过表示事实蕴含判断未被候选等级污染。
    """
    serialized_messages = str(messages)
    for forbidden_field in (
        "severity",
        "action_class",
        "score",
        "triage_message",
        "source_text",
        "matched_terms",
    ):
        assert forbidden_field not in serialized_messages


def _embedding_vector(
    external_env: dict[str, str],
    text_value: str,
    *,
    client: httpx.Client | None = None,
) -> list[float]:
    """通过真实 LiteLLM embedding API 生成测试向量。

    :param external_env: 外部依赖配置。
    :param text_value: 待向量化文本。
    :param client: 可复用的 HTTP 客户端。
    :return: 返回浮点向量。
    """
    if client is None:
        with httpx.Client(timeout=_timeout()) as owned_client:
            return _embedding_vector(external_env, text_value, client=owned_client)
    embedding = client.post(
        f"{external_env['LITELLM_BASE_URL'].rstrip('/')}/embeddings",
        headers={"Authorization": f"Bearer {external_env['LITELLM_API_KEY']}"},
        json={
            "model": external_env["QWEN_EMBEDDING_MODEL"],
            "input": text_value,
        },
    )
    embedding.raise_for_status()
    vector = embedding.json()["data"][0]["embedding"]
    return [float(value) for value in vector]


def _empty_clinical_safety_policy_input() -> dict[str, Any]:
    """构造 OPA 临床安全依赖探测使用的空候选输入。

    :return: 返回 OPA Data API input 字典。
    """
    return {
        "context": {
            "request_id": "req_clinical_dependency_probe",
            "trace_id": "trace_clinical_dependency_probe",
            "user_id": "user_clinical_dependency_probe",
            "pet_id": "pet_clinical_dependency_probe",
            "session_id": "session_clinical_dependency_probe",
        },
        "semantic": {
            "trusted": False,
            "stage": "skipped",
            "strategy": "not_requested",
            "confidence": 0.0,
            "species": "unknown",
            "sex": "unknown",
            "age_group": "unknown",
            "exposure_state": "unknown",
            "symptom_state": "unknown",
            "temporal_state": "unknown",
            "temporal_scope": "unclear",
            "resolution_state": "unknown",
            "intent_type": "other",
            "risk_evidence_state": "unknown",
            "high_risk_terms": [],
            "negated_terms": [],
        },
        "retrieval": {
            "stage": "none",
            "degraded": False,
            "reasons": [],
            "retrieval_source": "",
            "vector_hit_count": 0,
            "candidate_count": 0,
        },
        "thresholds": {
            "signal_min_score": 0.65,
            "urgent_min_score": 0.75,
        },
        "candidates": [],
    }


def _opa_data_url(base_url: str, package_name: str, rule_name: str) -> str:
    """构造兼容网关前缀的 OPA Data API 地址。

    :param base_url: OPA 服务基础地址，可为服务根路径或已经包含 /v1 的地址。
    :param package_name: Rego package 名称。
    :param rule_name: Rego 规则名称。
    :return: 返回 OPA Data API 完整地址。
    """
    normalized_base_url = base_url.rstrip("/")
    if not normalized_base_url.endswith("/v1"):
        normalized_base_url = f"{normalized_base_url}/v1"
    policy_path = "/".join((*package_name.split("."), rule_name))
    return f"{normalized_base_url}/data/{policy_path}"


def _safety_asset_id(prefix: str) -> str:
    """构造临床安全测试资产标识。

    :param prefix: 测试数据前缀。
    :return: 返回资产标识。
    """
    return f"{prefix}_clinical_safety_cyanosis"


def _safety_chunk_id(prefix: str) -> str:
    """构造临床安全测试 chunk 标识。

    :param prefix: 测试数据前缀。
    :return: 返回 chunk 标识。
    """
    return f"{_safety_asset_id(prefix)}.recognition.v1"


def _clinical_safety_embedding_text() -> str:
    """返回临床安全测试向量 chunk 文本。

    :return: 返回用于生成测试向量的文本。
    """
    return (
        "临床安全向量候选：猫或犬出现牙龈发紫、舌头发青、发绀、呼吸很快、"
        "呼吸困难时，可能属于呼吸循环急症红旗。"
    )


def _content_hash(text_value: str) -> str:
    """生成测试基线文本内容哈希。

    :param text_value: 待哈希文本。
    :return: 返回十六进制哈希摘要。
    """
    return hashlib.sha256(text_value.encode("utf-8")).hexdigest()


def _enabled(name: str) -> bool:
    """判断布尔环境变量是否开启。

    :param name: 环境变量名称。
    :return: 开启时返回 True。
    """
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _timeout() -> float:
    """读取临床安全外部 API 集成测试超时时间。

    :return: 返回超时时间秒数。
    """
    return float(
        os.getenv(
            "EXTERNAL_API_TEST_TIMEOUT_SECONDS", str(DEFAULT_EXTERNAL_TIMEOUT_SECONDS)
        )
    )

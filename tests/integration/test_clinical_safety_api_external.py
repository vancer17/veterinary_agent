"""
文件：tests/integration/test_clinical_safety_api_external.py
作用：通过真实外部依赖验证临床安全裁决 API 纵向链路。
范围：仅覆盖临床安全语义抽取、PostgreSQL/pgvector 候选召回、OPA 策略裁决与
      API 响应审计 metadata，不验证 Mem0、普通问诊完整链路或输入安全阻断场景。
说明：本测试仅在显式开启 RUN_CLINICAL_SAFETY_API_EXTERNAL_TEST 时执行，供
      try-run 或人工发布前加严验证使用。
"""

from __future__ import annotations

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
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ingress import create_app, set_orchestrator
from vet_agent import Container, Settings, VetAgentIngressOrchestrator, set_container
from vet_agent.db import ClinicalSafetyAssetModel, ClinicalSafetyChunkModel, make_session_factory
from vet_agent.observability import AgentPathNode


EXTERNAL_CLINICAL_SAFETY_FLAG = "RUN_CLINICAL_SAFETY_API_EXTERNAL_TEST"
DEFAULT_EXTERNAL_TIMEOUT_SECONDS = 45.0
DEFAULT_DATABASE_RETRY_ATTEMPTS = 3
DEFAULT_DATABASE_RETRY_DELAY_SECONDS = 2.0
EXPECTED_ALEMBIC_VERSION = "0018_rag_retrieval_misses"
EXPECTED_EMBEDDING_DIMENSION = 1024

_T = TypeVar("_T")


@pytest.fixture
def external_env() -> dict[str, str]:
    """读取临床安全外部 API 集成测试所需配置。

    :return: 返回外部依赖配置字典。
    """
    if not _enabled(EXTERNAL_CLINICAL_SAFETY_FLAG):
        pytest.skip(f"未开启 {EXTERNAL_CLINICAL_SAFETY_FLAG}，跳过临床安全外部 API 集成测试。")
    required = {
        "DATABASE_URL": os.getenv("EXTERNAL_API_TEST_DATABASE_URL") or os.getenv("DATABASE_URL"),
        "LITELLM_BASE_URL": os.getenv("EXTERNAL_API_TEST_LITELLM_BASE_URL") or os.getenv("LITELLM_BASE_URL"),
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
        "QWEN_MODEL": os.getenv("EXTERNAL_API_TEST_QWEN_MODEL", os.getenv("QWEN_MODEL", "qwen-plus")),
        "QWEN_EMBEDDING_MODEL": os.getenv(
            "EXTERNAL_API_TEST_QWEN_EMBEDDING_MODEL",
            os.getenv("QWEN_EMBEDDING_MODEL", "text-embedding-v4"),
        ),
    }
    return {**required, **optional}


@pytest.fixture
def external_prefix() -> str:
    """构造本轮测试唯一数据前缀。

    :return: 返回测试数据前缀。
    """
    return f"clinical_it_{uuid4().hex[:12]}"


@pytest.fixture
def external_database(external_env: dict[str, str], external_prefix: str) -> Iterator[str]:
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
        set_container(container)
        set_orchestrator(VetAgentIngressOrchestrator(container))
        try:
            with TestClient(create_app()) as client:
                yield client
        finally:
            set_orchestrator(None)
            set_container(None)


@pytest.fixture
def clean_external_runtime_data(external_database: str, external_prefix: str) -> Iterator[None]:
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

        vector = _embedding_vector(external_env, "猫牙龈发紫，呼吸很快。", client=client)
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
    assert any(signal["code"] == "CYANOSIS_RISK_PATTERN" for signal in data["safety_signals"])

    metadata = data["metadata"]
    _assert_policy_path_is_named(metadata["multi_agent_path"])
    _assert_input_safety_allows_with_opa(metadata)
    _assert_clinical_semantic_uses_litellm(metadata)
    _assert_clinical_safety_uses_pgvector(metadata)
    _assert_clinical_safety_uses_opa(metadata)


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
    assert any(signal["code"] == "CYANOSIS_RISK_PATTERN" for signal in decision["signals"])


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
        "ENABLE_MEM0": "false",
        "ENABLE_INPUT_SAFETY": "true",
        "INPUT_SAFETY_POLICY_BACKEND": "opa",
        "INPUT_SAFETY_OPA_BASE_URL": external_env["INPUT_SAFETY_OPA_BASE_URL"].rstrip("/"),
        "CLINICAL_SAFETY_OPA_BASE_URL": external_env["CLINICAL_SAFETY_OPA_BASE_URL"].rstrip("/"),
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
        version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
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
    if len(embedding) != EXPECTED_EMBEDDING_DIMENSION:
        pytest.fail(f"外部 embedding 维度不符合 clinical_safety_chunks.embedding 要求：{len(embedding)}。")

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
        asset.required_context = {}
        asset.decision_hints = {"symptom": "emergency"}
        asset.clinical_risk_summary = "黏膜发紫或发绀可提示缺氧或循环异常，需要按急症处理。"
        asset.triage_message = "如仍在发紫、呼吸急促或精神明显异常，应立即联系线下急诊兽医。"
        asset.source = {"system": "clinical_safety_external_api_test"}
        asset.raw_text = {"embedding_text": embedding_text}
        asset.version = "clinical_safety_external_api_test"
        asset.enabled = True
        asset.review_status = "approved"
        asset.published_at = now
        asset.metadata_json = {"clinical_safety_external_api_test": True, "prefix": prefix}

        chunk = session.get(ClinicalSafetyChunkModel, safety_chunk_id)
        if chunk is None:
            chunk = ClinicalSafetyChunkModel(chunk_id=safety_chunk_id, asset_id=safety_asset_id)
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
        chunk.metadata_json = {"clinical_safety_external_api_test": True, "prefix": prefix}


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
            text("DELETE FROM clinical_safety_chunks WHERE chunk_id = :chunk_id OR asset_id = :asset_id"),
            {"chunk_id": _safety_chunk_id(prefix), "asset_id": _safety_asset_id(prefix)},
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
    attempts = int(os.getenv("EXTERNAL_API_TEST_DATABASE_RETRY_ATTEMPTS", str(DEFAULT_DATABASE_RETRY_ATTEMPTS)))
    delay_seconds = float(
        os.getenv("EXTERNAL_API_TEST_DATABASE_RETRY_DELAY_SECONDS", str(DEFAULT_DATABASE_RETRY_DELAY_SECONDS))
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
    return float(os.getenv("EXTERNAL_API_TEST_TIMEOUT_SECONDS", str(DEFAULT_EXTERNAL_TIMEOUT_SECONDS)))

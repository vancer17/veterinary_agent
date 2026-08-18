"""
=============================================================================
文件：tests/integration/test_task_routing_api_external.py
作用：通过真实 PostgreSQL、LiteLLM 与 OPA 验证多任务拆分 API 纵向链路。
范围：覆盖 TaskRouterAgent、LiteLLM response_format、task_routing_domains 任务域目录、
      OPA 任务路由策略准入与 Agent API 多任务响应 metadata。
说明：本测试仅在显式开启 RUN_TASK_ROUTING_API_EXTERNAL_TEST 时执行；测试客户端不注入
      本地内存模型、不使用本地任务域仓储或本地策略替身，SSH 隧道由配套脚本负责建立。
=============================================================================
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


EXTERNAL_TASK_ROUTING_FLAG = "RUN_TASK_ROUTING_API_EXTERNAL_TEST"
DEFAULT_EXTERNAL_TIMEOUT_SECONDS = 75.0
DEFAULT_DATABASE_RETRY_ATTEMPTS = 3
DEFAULT_DATABASE_RETRY_DELAY_SECONDS = 2.0
EXPECTED_ALEMBIC_VERSION = "0018_rag_retrieval_misses"
EXPECTED_EMBEDDING_DIMENSION = 1024
EXPECTED_TASK_ROUTING_DOMAINS = {
    "gastrointestinal",
    "behavior",
    "feeding",
    "general",
}

_T = TypeVar("_T")


@pytest.fixture
def external_env() -> dict[str, str]:
    """读取多任务拆分外部 API 集成测试所需配置。

    :return: 返回外部依赖配置字典。
    """
    if not _enabled(EXTERNAL_TASK_ROUTING_FLAG):
        pytest.skip(f"未开启 {EXTERNAL_TASK_ROUTING_FLAG}，跳过多任务拆分外部 API 集成测试。")
    required = {
        "DATABASE_URL": os.getenv("EXTERNAL_API_TEST_DATABASE_URL") or os.getenv("DATABASE_URL"),
        "LITELLM_BASE_URL": os.getenv("EXTERNAL_API_TEST_LITELLM_BASE_URL") or os.getenv("LITELLM_BASE_URL"),
        "LITELLM_API_KEY": os.getenv("EXTERNAL_API_TEST_LITELLM_API_KEY")
        or os.getenv("LITELLM_API_KEY")
        or os.getenv("LITELLM_MASTER_KEY"),
        "TASK_ROUTING_OPA_BASE_URL": os.getenv("EXTERNAL_API_TEST_OPA_BASE_URL")
        or os.getenv("TASK_ROUTING_OPA_BASE_URL")
        or os.getenv("INPUT_SAFETY_OPA_BASE_URL"),
        "INPUT_SAFETY_OPA_BASE_URL": os.getenv("EXTERNAL_API_TEST_OPA_BASE_URL")
        or os.getenv("INPUT_SAFETY_OPA_BASE_URL"),
        "CLINICAL_SAFETY_OPA_BASE_URL": os.getenv("EXTERNAL_API_TEST_OPA_BASE_URL")
        or os.getenv("CLINICAL_SAFETY_OPA_BASE_URL")
        or os.getenv("INPUT_SAFETY_OPA_BASE_URL"),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        pytest.fail(
            f"{EXTERNAL_TASK_ROUTING_FLAG}=true 时缺少多任务拆分外部依赖配置：{', '.join(missing)}。"
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
    """构造本轮多任务拆分测试唯一数据前缀。

    :return: 返回测试数据前缀。
    """
    return f"task_routing_it_{uuid4().hex[:12]}"


@pytest.fixture
def external_database(external_env: dict[str, str], external_prefix: str) -> Iterator[str]:
    """准备多任务拆分 API 测试所需的数据库基线。

    :param external_env: 外部依赖配置。
    :param external_prefix: 测试数据前缀。
    :return: 返回数据库连接串。
    """
    database_url = external_env["DATABASE_URL"]

    def cleanup_database() -> None:
        """清理多任务拆分外部 API 测试数据。

        :return: 无返回值。
        """
        _cleanup_database_prefix(database_url, external_prefix)

    def assert_schema_ready() -> None:
        """校验外部数据库迁移版本和任务域目录。

        :return: 无返回值。
        """
        _assert_database_schema_ready(database_url)
        _assert_input_safety_baseline_ready(database_url)
        _assert_task_routing_domain_catalog_ready(database_url)

    def prepare_baseline() -> None:
        """写入容器就绪检查所需的最小临床安全资产和向量 chunk。

        :return: 无返回值。
        """
        _prepare_clinical_safety_baseline(database_url, external_env, external_prefix)

    _with_database_retry(cleanup_database, action="清理多任务拆分测试前缀数据")
    _with_database_retry(assert_schema_ready, action="校验多任务拆分数据库基线")
    _with_database_retry(prepare_baseline, action="写入多任务拆分测试临床安全基线")
    try:
        yield database_url
    finally:
        _with_database_retry(cleanup_database, action="清理多任务拆分测试前缀数据")


@pytest.fixture
def clean_external_runtime_data(external_database: str, external_prefix: str) -> Iterator[tuple[str, str, str]]:
    """清理本轮多任务拆分测试运行期数据。

    :param external_database: 数据库连接串。
    :param external_prefix: 测试数据前缀。
    :return: 返回 user_id、pet_id 和 session_id。
    """
    user_id, pet_id, session_id = _scope_ids(external_prefix)
    _with_database_retry(
        lambda: _cleanup_runtime_database_prefix(external_database, external_prefix),
        action="清理多任务拆分 API 运行数据",
    )
    try:
        yield user_id, pet_id, session_id
    finally:
        _with_database_retry(
            lambda: _cleanup_runtime_database_prefix(external_database, external_prefix),
            action="清理多任务拆分 API 运行数据",
        )


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


@pytest.mark.integration
def test_task_routing_external_dependencies_are_reachable(
    external_env: dict[str, str],
    external_database: str,
) -> None:
    """验证多任务拆分 API 测试所需真实外部依赖可达。

    :param external_env: 外部依赖配置。
    :param external_database: 已校验的数据库连接串。
    :return: 无返回值；断言通过表示外部依赖具备执行条件。
    """
    _assert_database_schema_ready(external_database)
    _assert_task_routing_domain_catalog_ready(external_database)
    with httpx.Client(timeout=_timeout()) as client:
        lite_llm = client.get(
            f"{external_env['LITELLM_BASE_URL'].rstrip('/')}/models",
            headers={"Authorization": f"Bearer {external_env['LITELLM_API_KEY']}"},
        )
        lite_llm.raise_for_status()
        model_ids = {item.get("id") for item in lite_llm.json().get("data", [])}
        assert external_env["QWEN_MODEL"] in model_ids
        assert external_env["QWEN_EMBEDDING_MODEL"] in model_ids

        vector = _embedding_vector(external_env, "多任务拆分真实服务集成测试。", client=client)
        assert len(vector) == EXPECTED_EMBEDDING_DIMENSION

        task_routing_opa = client.post(
            _opa_data_url(
                external_env["TASK_ROUTING_OPA_BASE_URL"],
                "vet_agent.task_routing",
                "decision",
            ),
            json={"input": _valid_task_routing_policy_input()},
        )
        task_routing_opa.raise_for_status()
        decision = task_routing_opa.json().get("result") or {}
        assert decision["action"] == "allow"
        assert decision["allow"] is True


@pytest.mark.integration
def test_task_routing_api_uses_real_litellm_postgres_and_opa(
    external_client: TestClient,
    clean_external_runtime_data: tuple[str, str, str],
    external_prefix: str,
) -> None:
    """验证 Agent API 多任务拆分使用真实 LiteLLM、PostgreSQL 和 OPA。

    :param external_client: 接入真实外部依赖的 API 测试客户端。
    :param clean_external_runtime_data: 已清理的测试身份范围。
    :param external_prefix: 测试数据前缀。
    :return: 无返回值；断言通过表示多任务拆分真实主路径可用。
    """
    user_id, pet_id, session_id = clean_external_runtime_data
    profile = _profile()
    response = external_client.post(
        "/agent/turns",
        json=_payload(
            (
                f"{external_prefix}。请分别处理三个独立问题："
                "第一，我家三岁柯基犬今天软便两次，精神和食欲正常，没有呕吐；"
                "第二，它最近半夜会频繁乱叫；"
                "第三，我想知道这周能不能从旧粮换成新粮。"
            ),
            user_id=user_id,
            pet_id=pet_id,
            session_id=session_id,
            profile=profile,
            idempotency_key=f"{external_prefix}_idem_multi_task",
        ),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["vet_result"]["route"] == "multi_task_consultation"
    assert data["reasoning_display"]["metadata"]["kind"] == "user_visible_multi_task_routing"
    assert len(data["segments"]) >= 2

    metadata = data["metadata"]
    task_router = metadata["task_router"]
    tasks = list(task_router["tasks"])
    domains = {str(task["domain"]) for task in tasks}
    assert task_router["strategy"] == "litellm_response_format_task_router"
    assert task_router["task_count"] == len(tasks)
    assert task_router["policy"]["policy_backend"] == "opa"
    assert task_router["policy"]["allow"] is True
    assert {"gastrointestinal", "behavior"}.issubset(domains)
    assert domains.issubset(_task_routing_allowed_domains(task_router))
    assert AgentPathNode.TASK_ROUTER_AGENT.value in metadata["multi_agent_path"]
    assert metadata["input_safety_decision"]["policy_backend"] == "opa"
    assert metadata["clinical_safety_resolution"]["policy_decision"]["policy_backend"] == "opa"
    assert metadata["memory_read"]["audit"]["source"] == "postgres_authoritative_memory_with_mem0_projection"


@contextmanager
def _configured_environment(
    monkeypatch: pytest.MonkeyPatch,
    external_env: dict[str, str],
) -> Iterator[None]:
    """向当前进程注入多任务拆分外部 API 集成测试配置。

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
        "ENABLE_RAG_EMBEDDINGS": "false",
        "CLINICAL_SAFETY_VECTOR_MIN_SCORE": "0.999",
        "ENABLE_MEM0": "false",
        "ENABLE_INPUT_SAFETY": "true",
        "INPUT_SAFETY_POLICY_BACKEND": "opa",
        "INPUT_SAFETY_OPA_BASE_URL": external_env["INPUT_SAFETY_OPA_BASE_URL"].rstrip("/"),
        "CLINICAL_SAFETY_OPA_BASE_URL": external_env["CLINICAL_SAFETY_OPA_BASE_URL"].rstrip("/"),
        "TASK_ROUTING_OPA_BASE_URL": external_env["TASK_ROUTING_OPA_BASE_URL"].rstrip("/"),
        "ENABLE_INPUT_SAFETY_GUARDRAILS": "false",
        "ENABLE_LLM_SEMANTIC_EXTRACTION": "true",
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
    idempotency_key: str,
) -> dict[str, Any]:
    """构造多任务拆分 API 集成测试请求载荷。

    :param text_value: 用户输入文本。
    :param user_id: 测试用户标识。
    :param pet_id: 测试宠物标识。
    :param session_id: 测试会话标识。
    :param profile: 可信宠物画像。
    :param idempotency_key: 本轮幂等键。
    :return: 返回 API 请求载荷。
    """
    return {
        "model": "qwen-plus",
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
        "turn_options": {
            "idempotency_key": idempotency_key,
            "max_followup_questions": 1,
        },
    }


def _scope_assertion(
    *,
    user_id: str,
    pet_id: str,
    session_id: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """构造多任务拆分 API 集成测试使用的可信范围声明。

    :param user_id: 测试用户标识。
    :param pet_id: 测试宠物标识。
    :param session_id: 测试会话标识。
    :param profile: 可信宠物画像。
    :return: 返回范围声明字典。
    """
    now = datetime.now(UTC).isoformat()
    return {
        "schema_version": "v1",
        "issuer": "task-routing-api-integration-test",
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
            "system": "task-routing-api-integration-test",
            "database": "vet_agent",
            "table": "master_pet_info",
            "record_id": pet_id,
            "record_updated_at": now,
            "data_source": "integration_test",
        },
        "session_policy": {"binding_mode": "single_user_pet_per_session"},
    }


def _profile() -> dict[str, Any]:
    """构造多任务拆分 API 集成测试宠物画像。

    :return: 返回可信宠物画像字典。
    """
    return {
        "species": "犬",
        "name": "可可",
        "age": "3岁",
        "sex": "female",
        "weight_kg": 11.8,
        "breed": "柯基",
    }


def _assert_database_schema_ready(database_url: str) -> None:
    """确认外部数据库迁移版本满足多任务拆分测试要求。

    :param database_url: 数据库连接串。
    :return: 无返回值。
    """
    session_factory = make_session_factory(database_url)
    with session_factory() as session:
        version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    if version != EXPECTED_ALEMBIC_VERSION:
        pytest.fail(
            "外部数据库未迁移到当前多任务拆分 API 测试所需版本。"
            f" 当前版本：{version!r}，期望版本：{EXPECTED_ALEMBIC_VERSION!r}。"
        )


def _assert_input_safety_baseline_ready(database_url: str) -> None:
    """确认基础输入安全候选定义表具备运行时最小基线。

    :param database_url: 数据库连接串。
    :return: 无返回值。
    """
    session_factory = make_session_factory(database_url)
    with session_factory() as session:
        count = int(
            session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM input_safety_candidate_definitions
                    WHERE enabled IS TRUE
                    """
                )
            ).scalar_one()
            or 0
        )
    if count <= 0:
        pytest.fail("外部数据库缺少基础输入安全候选定义，无法执行真实 API 多任务拆分测试。")


def _assert_task_routing_domain_catalog_ready(database_url: str) -> None:
    """确认任务路由任务域目录具备本测试所需任务域。

    :param database_url: 数据库连接串。
    :return: 无返回值。
    """
    session_factory = make_session_factory(database_url)
    with session_factory() as session:
        rows = session.execute(
            text(
                """
                SELECT domain
                FROM task_routing_domains
                WHERE enabled IS TRUE
                """
            )
        ).scalars()
        domains = {str(domain) for domain in rows}
    missing_domains = sorted(EXPECTED_TASK_ROUTING_DOMAINS - domains)
    if missing_domains:
        pytest.fail(f"外部数据库缺少多任务拆分所需任务域：{missing_domains!r}。")


def _prepare_clinical_safety_baseline(
    database_url: str,
    external_env: dict[str, str],
    prefix: str,
) -> None:
    """写入容器就绪检查所需的最小临床安全资产与向量 chunk。

    :param database_url: 数据库连接串。
    :param external_env: 外部依赖配置。
    :param prefix: 测试数据前缀。
    :return: 无返回值。
    """
    now = datetime.now(UTC)
    safety_asset_id = _safety_asset_id(prefix)
    safety_chunk_id = _safety_chunk_id(prefix)
    embedding_text = _clinical_safety_embedding_text(prefix)
    embedding = _embedding_vector(external_env, embedding_text)
    if len(embedding) != EXPECTED_EMBEDDING_DIMENSION:
        pytest.fail(f"外部 embedding 维度不符合 clinical_safety_chunks.embedding 要求：{len(embedding)}。")

    session_factory = make_session_factory(database_url)
    with session_factory.begin() as session:
        asset = session.get(ClinicalSafetyAssetModel, safety_asset_id)
        if asset is None:
            asset = ClinicalSafetyAssetModel(asset_id=safety_asset_id)
            session.add(asset)
        asset.code = "TASK_ROUTING_EXTERNAL_OBSERVATION"
        asset.asset_type = "danger_pattern"
        asset.canonical_name = "多任务拆分外部 API 测试普通观察候选"
        asset.category = "task_routing_external_api_test"
        asset.species_scope = []
        asset.sex_scope = []
        asset.age_scope = []
        asset.severity = "info"
        asset.action_class = "safety_warning"
        asset.aliases = []
        asset.carriers = []
        asset.user_expressions = ["普通软便观察，无急症表现。"]
        asset.symptoms = ["软便"]
        asset.recognition_phrases = ["轻微软便但精神食欲正常"]
        asset.required_context = {}
        asset.decision_hints = {"purpose": "container_readiness_only"}
        asset.clinical_risk_summary = "该资产仅用于外部 API 集成测试中的临床安全仓储就绪检查。"
        asset.triage_message = "当前信息更适合作为普通观察建议处理。"
        asset.source = {"system": "task_routing_external_api_test"}
        asset.raw_text = {"embedding_text": embedding_text}
        asset.version = "task_routing_external_api_test"
        asset.enabled = True
        asset.review_status = "approved"
        asset.published_at = now
        asset.metadata_json = {"task_routing_external_api_test": True, "prefix": prefix}

        chunk = session.get(ClinicalSafetyChunkModel, safety_chunk_id)
        if chunk is None:
            chunk = ClinicalSafetyChunkModel(chunk_id=safety_chunk_id, asset_id=safety_asset_id)
            session.add(chunk)
        chunk.asset_id = safety_asset_id
        chunk.chunk_type = "recognition"
        chunk.title = "多任务拆分外部 API 测试普通观察 chunk"
        chunk.embedding_text = embedding_text
        chunk.embedding = embedding
        chunk.embedding_model = external_env["QWEN_EMBEDDING_MODEL"]
        chunk.embedding_dimension = len(embedding)
        chunk.content_hash = _content_hash(embedding_text)
        chunk.version = "task_routing_external_api_test"
        chunk.enabled = True
        chunk.review_status = "approved"
        chunk.metadata_json = {"task_routing_external_api_test": True, "prefix": prefix}


def _cleanup_database_prefix(database_url: str, prefix: str) -> None:
    """按测试前缀清理多任务拆分基线和运行期数据。

    :param database_url: 数据库连接串。
    :param prefix: 测试数据前缀。
    :return: 无返回值。
    """
    _cleanup_runtime_database_prefix(database_url, prefix)
    _cleanup_baseline_database_prefix(database_url, prefix)


def _cleanup_runtime_database_prefix(database_url: str, prefix: str) -> None:
    """按测试前缀清理多任务拆分 API 运行期数据。

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
    """按测试前缀清理多任务拆分最小临床安全基线数据。

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
    """构造多任务拆分 API 测试的用户、宠物和会话标识。

    :param prefix: 测试数据前缀。
    :return: 返回 user_id、pet_id 和 session_id。
    """
    return (
        f"{prefix}_user_task_routing",
        f"{prefix}_pet_task_routing",
        f"{prefix}_session_task_routing",
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


def _valid_task_routing_policy_input() -> dict[str, Any]:
    """构造 OPA 任务路由依赖探测使用的有效计划输入。

    :return: 返回 OPA Data API input 字典。
    """
    return {
        "context": {
            "request_id": "req_task_routing_dependency_probe",
            "trace_id": "trace_task_routing_dependency_probe",
            "user_id": "user_task_routing_dependency_probe",
            "pet_id": "pet_task_routing_dependency_probe",
            "session_id": "session_task_routing_dependency_probe",
        },
        "schema_version": "v1",
        "max_task_count": 5,
        "allowed_domains": sorted(EXPECTED_TASK_ROUTING_DOMAINS),
        "active_task_keys": [],
        "tasks": [
            {
                "task_id": "task_001",
                "task_key": "gastrointestinal",
                "domain": "gastrointestinal",
                "text_length": 20,
                "priority": 10,
                "existing_task_key": None,
            }
        ],
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


def _task_routing_allowed_domains(task_router_metadata: dict[str, Any]) -> set[str]:
    """从任务路由 metadata 中读取策略审计的任务域目录。

    :param task_router_metadata: API 响应中的 metadata.task_router 字典。
    :return: 返回策略审计中允许的任务域集合。
    """
    policy = dict(task_router_metadata.get("policy") or {})
    catalog = policy.get("domain_catalog") or []
    if not isinstance(catalog, list):
        return set()
    return {str(item.get("domain")) for item in catalog if isinstance(item, dict) and item.get("domain")}


def _safety_asset_id(prefix: str) -> str:
    """构造多任务拆分测试临床安全资产标识。

    :param prefix: 测试数据前缀。
    :return: 返回资产标识。
    """
    return f"{prefix}_task_routing_observation"


def _safety_chunk_id(prefix: str) -> str:
    """构造多任务拆分测试临床安全 chunk 标识。

    :param prefix: 测试数据前缀。
    :return: 返回 chunk 标识。
    """
    return f"{_safety_asset_id(prefix)}.recognition.v1"


def _clinical_safety_embedding_text(prefix: str) -> str:
    """返回多任务拆分测试所需临床安全向量 chunk 文本。

    :param prefix: 测试数据前缀。
    :return: 返回用于 embedding 的文本。
    """
    return f"{prefix} 普通软便观察，无急症表现，精神食欲正常。"


def _content_hash(value: str) -> str:
    """计算测试 chunk 内容哈希。

    :param value: 待计算哈希的文本。
    :return: 返回 SHA256 哈希。
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _timeout() -> float:
    """读取外部 API 集成测试 HTTP 超时时间。

    :return: 返回超时时间秒数。
    """
    return float(os.getenv("EXTERNAL_API_TEST_TIMEOUT_SECONDS", str(DEFAULT_EXTERNAL_TIMEOUT_SECONDS)))


def _enabled(name: str) -> bool:
    """读取布尔环境变量。

    :param name: 环境变量名称。
    :return: 环境变量表示启用时返回 True。
    """
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}

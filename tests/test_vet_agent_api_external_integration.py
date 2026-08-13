"""
文件：tests/test_vet_agent_api_external_integration.py
作用：通过真实远程依赖服务验证 Agent API 主链路、临床安全向量召回、OPA 裁决和 Mem0 可达性。
范围：仅在显式开启 RUN_EXTERNAL_API_SMOKE 时执行，不进入默认快速 CI 门禁。
说明：本测试使用本地 TestClient 执行当前代码，外部依赖使用环境变量配置的真实 LiteLLM、OPA、PostgreSQL 与 Mem0。
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
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from ingress import create_app, set_orchestrator
from vet_agent import Container, Settings, VetAgentIngressOrchestrator, set_container
from vet_agent.db import (
    ClinicalSafetyAssetModel,
    ClinicalSafetyChunkModel,
    ConsultationDomainModel,
    ConsultationSlotModel,
    KnowledgeChunkModel,
    make_session_factory,
)


EXTERNAL_SMOKE_FLAG = "RUN_EXTERNAL_API_SMOKE"
DEFAULT_EXTERNAL_TIMEOUT_SECONDS = 45.0
DEFAULT_DATABASE_RETRY_ATTEMPTS = 3
DEFAULT_DATABASE_RETRY_DELAY_SECONDS = 2.0

_T = TypeVar("_T")


@pytest.fixture
def external_env() -> dict[str, str]:
    """读取外部 API 集成测试所需的真实依赖配置。

    :return: 返回外部依赖配置字典。
    """
    if not _enabled(EXTERNAL_SMOKE_FLAG):
        pytest.skip(f"未开启 {EXTERNAL_SMOKE_FLAG}，跳过真实外部服务 API 集成测试。")
    required = {
        "DATABASE_URL": os.getenv("EXTERNAL_API_TEST_DATABASE_URL") or os.getenv("DATABASE_URL"),
        "LITELLM_BASE_URL": os.getenv("EXTERNAL_API_TEST_LITELLM_BASE_URL") or os.getenv("LITELLM_BASE_URL"),
        "LITELLM_API_KEY": os.getenv("EXTERNAL_API_TEST_LITELLM_API_KEY")
        or os.getenv("LITELLM_API_KEY")
        or os.getenv("LITELLM_MASTER_KEY"),
        "INPUT_SAFETY_OPA_BASE_URL": os.getenv("EXTERNAL_API_TEST_OPA_BASE_URL")
        or os.getenv("INPUT_SAFETY_OPA_BASE_URL"),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        pytest.fail(f"{EXTERNAL_SMOKE_FLAG}=true 时缺少外部依赖配置：{', '.join(missing)}。")
    optional = {
        "MEM0_BASE_URL": os.getenv("EXTERNAL_API_TEST_MEM0_BASE_URL") or os.getenv("MEM0_BASE_URL", ""),
        "MEM0_API_KEY": os.getenv("EXTERNAL_API_TEST_MEM0_API_KEY") or os.getenv("MEM0_API_KEY", ""),
        "QWEN_MODEL": os.getenv("EXTERNAL_API_TEST_QWEN_MODEL", os.getenv("QWEN_MODEL", "qwen-plus")),
        "QWEN_EMBEDDING_MODEL": os.getenv(
            "EXTERNAL_API_TEST_QWEN_EMBEDDING_MODEL",
            os.getenv("QWEN_EMBEDDING_MODEL", "text-embedding-v4"),
        ),
    }
    return {**required, **optional}


@pytest.fixture
def external_prefix() -> str:
    """构造本轮测试独有的数据前缀。

    :return: 返回测试数据前缀。
    """
    return f"it_{uuid4().hex[:12]}"


@pytest.fixture
def external_database(external_env: dict[str, str], external_prefix: str) -> Iterator[str]:
    """确认外部 PostgreSQL 具备当前代码所需迁移和基础 seed。

    :param external_env: 外部依赖配置。
    :param external_prefix: 测试数据前缀。
    :return: 返回数据库连接串。
    """
    database_url = external_env["DATABASE_URL"]

    def cleanup_database() -> None:
        """清理本轮外部 API 测试前缀数据。

        :return: 无返回值。
        """
        _cleanup_database_prefix(database_url, external_prefix)

    def assert_schema_ready() -> None:
        """校验外部数据库迁移版本满足当前测试要求。

        :return: 无返回值。
        """
        _assert_database_schema_ready(database_url)

    def prepare_baseline() -> None:
        """写入本轮外部 API 测试最小基线数据。

        :return: 无返回值。
        """
        _prepare_external_smoke_baseline(database_url, external_env, external_prefix)

    def assert_baseline_ready() -> None:
        """校验本轮外部 API 测试最小基线数据已经可用。

        :return: 无返回值。
        """
        _assert_database_ready(database_url, external_prefix)

    _with_database_retry(cleanup_database, action="清理测试前缀数据")
    _with_database_retry(assert_schema_ready, action="校验数据库迁移版本")
    _with_database_retry(prepare_baseline, action="写入测试基线数据")
    _with_database_retry(assert_baseline_ready, action="校验测试基线数据")
    try:
        yield database_url
    finally:
        _with_database_retry(cleanup_database, action="清理测试前缀数据")


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
def clean_external_data(external_database: str, external_prefix: str) -> Iterator[None]:
    """清理集成测试写入远程开发数据库的业务数据。

    :param external_database: 数据库连接串。
    :param external_prefix: 测试数据前缀。
    :return: 返回 fixture 迭代器。
    """

    def cleanup_runtime_data() -> None:
        """清理本轮外部 API 测试写入的运行数据。

        :return: 无返回值。
        """
        _cleanup_runtime_database_prefix(external_database, external_prefix)

    _with_database_retry(cleanup_runtime_data, action="清理 API 运行数据")
    try:
        yield
    finally:
        _with_database_retry(cleanup_runtime_data, action="清理 API 运行数据")


@pytest.mark.integration
def test_external_dependencies_are_reachable(external_env: dict[str, str], external_database: str) -> None:
    """验证真实 LiteLLM、OPA、PostgreSQL 与可选 Mem0 服务可达。

    :param external_env: 外部依赖配置。
    :param external_database: 数据库连接串。
    :return: 无返回值；断言通过表示外部依赖具备 API 冒烟测试条件。
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
        assert len(vector) == 1024

        opa = client.post(
            _opa_data_url(
                external_env["INPUT_SAFETY_OPA_BASE_URL"],
                "vet_agent.input_safety",
                "decision",
            ),
            json={
                "input": {
                    "context": _policy_context("dependency_probe"),
                    "candidates": [],
                }
            },
        )
        opa.raise_for_status()
        assert opa.json()["result"]["allow"] is True

        if external_env.get("MEM0_BASE_URL"):
            mem0 = client.get(
                f"{external_env['MEM0_BASE_URL'].rstrip('/')}/openapi.json",
                headers=_mem0_headers(external_env),
            )
            mem0.raise_for_status()
            assert mem0.json()["info"]["title"]


@pytest.mark.integration
def test_external_api_ready_uses_real_dependencies(
    external_client: TestClient,
    clean_external_data: None,
) -> None:
    """验证本地 API 客户端在真实外部依赖下可以通过 ready 检查。

    :param external_client: 接入真实外部依赖的 API 测试客户端。
    :param clean_external_data: 测试数据清理 fixture。
    :return: 无返回值；断言通过表示容器装配的真实依赖均就绪。
    """
    del clean_external_data
    health = external_client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    ready = external_client.get("/ready")
    assert ready.status_code == 200, ready.text
    checks = ready.json()["checks"]
    assert checks["orchestrator"] is True
    assert checks["knowledge_repository"] is True


@pytest.mark.integration
def test_external_api_turn_uses_real_litellm_opa_postgres_and_mem0(
    external_client: TestClient,
    clean_external_data: None,
    external_prefix: str,
    external_database: str,
) -> None:
    """验证真实服务下普通问诊回合可以完成响应、策略裁决、落库和语义记忆投影。

    :param external_client: 接入真实外部依赖的 API 测试客户端。
    :param clean_external_data: 测试数据清理 fixture。
    :param external_prefix: 测试数据前缀。
    :param external_database: 数据库连接串。
    :return: 无返回值；断言通过表示真实主链路可用。
    """
    del clean_external_data
    user_id, pet_id, session_id = _scope_ids(external_prefix, "normal")
    response = external_client.post(
        "/agent/turns",
        json=_payload(
            "我家狗今天轻微软便，但精神还可以，应该先观察哪些情况？",
            user_id=user_id,
            pet_id=pet_id,
            session_id=session_id,
            profile={"species": "犬", "age": "3岁", "sex": "male", "weight_kg": 12},
        ),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["request_id"]
    assert data["trace_id"]
    assert data["output_text"].strip()
    assert data["status"] in {"requires_followup", "completed", "safety_escalated"}
    assert "InputSafetyService" in data["metadata"]["multi_agent_path"]
    assert data["metadata"]["input_safety_decision"]["allow"] is True

    def count_conversation_turns() -> int:
        """统计普通外部 API 回合落库结果。

        :return: 返回当前测试用户对应的会话回合行数。
        """
        return _count_rows(external_database, "conversation_turns", "user_id", user_id)

    row_count = _with_database_retry(count_conversation_turns, action="统计 API 回合落库结果")
    assert row_count >= 1


@pytest.mark.integration
def test_external_api_clinical_safety_uses_pgvector_candidates(
    external_client: TestClient,
    clean_external_data: None,
    external_prefix: str,
) -> None:
    """验证临床安全候选在真实 embedding 与 PostgreSQL/pgvector 下进入向量召回路径。

    :param external_client: 接入真实外部依赖的 API 测试客户端。
    :param clean_external_data: 测试数据清理 fixture。
    :param external_prefix: 测试数据前缀。
    :return: 无返回值；断言通过表示临床安全候选召回主路径为 pgvector。
    """
    del clean_external_data
    user_id, pet_id, session_id = _scope_ids(external_prefix, "clinical")
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
    retrieval = data["metadata"]["clinical_safety_resolution"]["fallback_state"]["retrieval"]
    assert retrieval["stage"] == "vector"
    assert retrieval["retrieval_source"] == "clinical_safety_pgvector"
    assert retrieval["vector_hit_count"] > 0
    assert any(signal["code"] == "CYANOSIS_RISK_PATTERN" for signal in data["safety_signals"])


@pytest.mark.integration
def test_external_api_input_safety_block_uses_real_opa(
    external_client: TestClient,
    clean_external_data: None,
    external_prefix: str,
) -> None:
    """验证真实 OPA 会阻断结构化影像能力边界候选。

    :param external_client: 接入真实外部依赖的 API 测试客户端。
    :param clean_external_data: 测试数据清理 fixture。
    :param external_prefix: 测试数据前缀。
    :return: 无返回值；断言通过表示输入安全真实 OPA 策略已参与 API 主链路。
    """
    del clean_external_data
    user_id, pet_id, session_id = _scope_ids(external_prefix, "blocked")
    response = external_client.post(
        "/agent/turns",
        json=_payload(
            "帮我看看这张 X 光片。",
            user_id=user_id,
            pet_id=pet_id,
            session_id=session_id,
            profile={"species": "犬", "age": "3岁"},
            attachments=[
                {
                    "attachment_id": f"{external_prefix}_xray",
                    "mime_type": "image/jpeg",
                    "purpose": "radiology",
                    "storage_ref": "oss://external-api-smoke/xray.jpg",
                }
            ],
        ),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    decision = data["metadata"]["input_safety_decision"]
    assert data["status"] == "blocked"
    assert decision["action"] == "block"
    assert decision["allow"] is False
    assert any(signal["code"] == "RADIOLOGY_GATE" for signal in data["safety_signals"])


@contextmanager
def _configured_environment(
    monkeypatch: pytest.MonkeyPatch,
    external_env: dict[str, str],
) -> Iterator[None]:
    """向当前进程注入真实外部依赖配置。

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
        "ENABLE_MEM0": "true" if external_env.get("MEM0_BASE_URL") else "false",
        "MEM0_BASE_URL": external_env.get("MEM0_BASE_URL", "").rstrip("/"),
        "MEM0_API_KEY": external_env.get("MEM0_API_KEY", ""),
        "ENABLE_INPUT_SAFETY": "true",
        "INPUT_SAFETY_POLICY_BACKEND": "opa",
        "INPUT_SAFETY_OPA_BASE_URL": external_env["INPUT_SAFETY_OPA_BASE_URL"].rstrip("/"),
        "ENABLE_INPUT_SAFETY_GUARDRAILS": "false",
        "ENABLE_LLM_SEMANTIC_EXTRACTION": "true",
        "ENABLE_LLM_TASK_SPLITTER": "false",
        "ENABLE_MEMORY_EXTRACTION": "true",
        "ENABLE_LLM_MEMORY_EXTRACTION": "true",
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
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构造外部 API 集成测试请求载荷。

    :param text_value: 用户输入文本。
    :param user_id: 测试用户标识。
    :param pet_id: 测试宠物标识。
    :param session_id: 测试会话标识。
    :param profile: 可信宠物画像。
    :param attachments: 附件列表。
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
        "attachments": attachments or [],
        "turn_options": {"idempotency_key": f"idem_{session_id}"},
    }


def _scope_assertion(
    *,
    user_id: str,
    pet_id: str,
    session_id: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """构造外部 API 集成测试使用的可信范围声明。

    :param user_id: 测试用户标识。
    :param pet_id: 测试宠物标识。
    :param session_id: 测试会话标识。
    :param profile: 可信宠物画像。
    :return: 返回范围声明字典。
    """
    now = datetime.now(UTC).isoformat()
    return {
        "schema_version": "v1",
        "issuer": "external-api-integration-test",
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
            "system": "external-api-integration-test",
            "database": "vet_agent",
            "table": "master_pet_info",
            "record_id": pet_id,
            "record_updated_at": now,
            "data_source": "integration_test",
        },
        "session_policy": {"binding_mode": "single_user_pet_per_session"},
    }


def _assert_database_schema_ready(database_url: str) -> None:
    """确认外部数据库已经完成当前代码所需迁移。

    :param database_url: 数据库连接串。
    :return: 无返回值。
    """
    session_factory = make_session_factory(database_url)
    with session_factory() as session:
        version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        if version != "0011_clinical_safety_vector_comments":
            pytest.fail(
                "外部数据库未迁移到当前测试所需版本。"
                f" 当前版本：{version!r}，期望版本：'0011_clinical_safety_vector_comments'。"
            )


def _prepare_external_smoke_baseline(
    database_url: str,
    external_env: dict[str, str],
    prefix: str,
) -> None:
    """为外部 API 冒烟测试写入最小可运行基线数据。

    :param database_url: 数据库连接串。
    :param external_env: 外部依赖配置。
    :param prefix: 测试数据前缀。
    :return: 无返回值。
    """
    now = datetime.now(UTC)
    safety_asset_id = f"{prefix}_clinical_safety_cyanosis"
    safety_chunk_id = f"{safety_asset_id}.recognition.v1"
    consultation_domain = f"{prefix}_general"
    consultation_slot = f"{prefix}_placeholder_slot"
    knowledge_source = _knowledge_source(prefix)
    embedding_text = (
        "临床安全向量候选：猫或犬出现牙龈发紫、舌头发青、发绀、呼吸很快、"
        "呼吸困难时，可能属于呼吸循环急症红旗。"
    )
    embedding = _embedding_vector(external_env, embedding_text)
    if len(embedding) != 1024:
        pytest.fail(f"外部 embedding 维度不符合 clinical_safety_chunks.embedding 要求：{len(embedding)}。")

    session_factory = make_session_factory(database_url)
    with session_factory.begin() as session:
        domain = session.get(ConsultationDomainModel, consultation_domain)
        if domain is None:
            domain = ConsultationDomainModel(domain=consultation_domain)
            session.add(domain)
        domain.required_slots = []
        domain.classifier_keywords = []
        domain.enabled = True
        domain.priority = 10
        domain.version = "external_api_smoke"
        domain.updated_at = now

        slot = session.get(ConsultationSlotModel, consultation_slot)
        if slot is None:
            slot = ConsultationSlotModel(slot_name=consultation_slot)
            session.add(slot)
        slot.question = "请补充最影响分诊判断的一项关键信息。"
        slot.label = "外部 API 冒烟占位槽位"
        slot.extraction_rules = []
        slot.enabled = True
        slot.priority = 10
        slot.version = "external_api_smoke"
        slot.updated_at = now

        knowledge = session.scalar(
            select(KnowledgeChunkModel).where(
                KnowledgeChunkModel.source == knowledge_source,
                KnowledgeChunkModel.title == f"{prefix} 外部 API 冒烟知识片段",
            )
        )
        if knowledge is None:
            knowledge = KnowledgeChunkModel(
                source=knowledge_source,
                title=f"{prefix} 外部 API 冒烟知识片段",
                content="外部 API 冒烟测试知识片段。",
            )
            session.add(knowledge)
        knowledge.content = (
            "外部 API 冒烟测试知识片段：宠物轻微软便但精神食欲尚可时，"
            "线上建议应优先提示观察精神、食欲、饮水、呕吐、便血、持续时间和恶化迹象。"
        )
        knowledge.embedding = None
        knowledge.public_citation = False
        knowledge.copyright_risk = "low"
        knowledge.domain = consultation_domain
        knowledge.species = None
        knowledge.source_url = None
        knowledge.version = "external_api_smoke"
        knowledge.enabled = True
        knowledge.review_status = "approved"
        knowledge.quality_score = 0.8
        knowledge.last_reviewed_at = now
        knowledge.metadata_json = {"external_api_smoke": True, "prefix": prefix}

        asset = session.get(ClinicalSafetyAssetModel, safety_asset_id)
        if asset is None:
            asset = ClinicalSafetyAssetModel(asset_id=safety_asset_id)
            session.add(asset)
        asset.code = "CYANOSIS_RISK_PATTERN"
        asset.asset_type = "emergency_red_flag"
        asset.canonical_name = "舌/牙龈发绀发紫"
        asset.category = "external_api_smoke"
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
        asset.source = {"system": "external_api_smoke_test"}
        asset.raw_text = {"embedding_text": embedding_text}
        asset.version = "external_api_smoke"
        asset.enabled = True
        asset.review_status = "approved"
        asset.published_at = now
        asset.metadata_json = {"external_api_smoke": True, "prefix": prefix}

        chunk = session.get(ClinicalSafetyChunkModel, safety_chunk_id)
        if chunk is None:
            chunk = ClinicalSafetyChunkModel(chunk_id=safety_chunk_id, asset_id=safety_asset_id)
            session.add(chunk)
        chunk.asset_id = safety_asset_id
        chunk.chunk_type = "recognition"
        chunk.title = "舌/牙龈发绀发紫 外部 API 冒烟识别 chunk"
        chunk.embedding_text = embedding_text
        chunk.embedding = embedding
        chunk.embedding_model = external_env["QWEN_EMBEDDING_MODEL"]
        chunk.embedding_dimension = len(embedding)
        chunk.content_hash = _content_hash(embedding_text)
        chunk.version = "external_api_smoke"
        chunk.enabled = True
        chunk.review_status = "approved"
        chunk.metadata_json = {"external_api_smoke": True, "prefix": prefix}


def _assert_database_ready(database_url: str, prefix: str) -> None:
    """确认外部数据库已经具备本轮测试写入的最小运行数据。

    :param database_url: 数据库连接串。
    :param prefix: 测试数据前缀。
    :return: 无返回值。
    """
    session_factory = make_session_factory(database_url)
    with session_factory() as session:
        checks = {
            "input_safety_candidate_definitions": """
                SELECT count(*) FROM input_safety_candidate_definitions WHERE enabled IS TRUE
            """,
            "clinical_safety_chunks": """
                SELECT count(*) FROM clinical_safety_chunks
                WHERE enabled IS TRUE
                  AND review_status = 'approved'
                  AND embedding IS NOT NULL
                  AND chunk_id LIKE :prefix_pattern
            """,
            "knowledge_chunks": """
                SELECT count(*) FROM knowledge_chunks
                WHERE enabled IS TRUE
                  AND review_status = 'approved'
                  AND source = :knowledge_source
            """,
            "consultation_domains": """
                SELECT count(*) FROM consultation_domains
                WHERE enabled IS TRUE AND domain LIKE :prefix_pattern
            """,
            "consultation_slots": """
                SELECT count(*) FROM consultation_slots
                WHERE enabled IS TRUE AND slot_name LIKE :prefix_pattern
            """,
        }
        params = {"prefix_pattern": f"{prefix}%", "knowledge_source": _knowledge_source(prefix)}
        for name, sql in checks.items():
            count = int(session.execute(text(sql), params).scalar_one() or 0)
            if count <= 0:
                pytest.fail(f"外部数据库缺少集成测试所需基础数据：{name}。")


def _cleanup_database_prefix(database_url: str, prefix: str) -> None:
    """按测试前缀清理远程开发数据库中的测试基线和运行数据。

    :param database_url: 数据库连接串。
    :param prefix: 测试数据前缀。
    :return: 无返回值。
    """
    _cleanup_runtime_database_prefix(database_url, prefix)
    _cleanup_baseline_database_prefix(database_url, prefix)


def _cleanup_runtime_database_prefix(database_url: str, prefix: str) -> None:
    """按测试前缀清理远程开发数据库中的 API 运行数据。

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
    """按测试前缀清理远程开发数据库中的最小基线数据。

    :param database_url: 数据库连接串。
    :param prefix: 测试数据前缀。
    :return: 无返回值。
    """
    session_factory = make_session_factory(database_url)
    prefix_pattern = f"{prefix}%"
    with session_factory.begin() as session:
        for sql in (
            "DELETE FROM clinical_safety_chunks WHERE chunk_id LIKE :prefix_pattern OR asset_id LIKE :prefix_pattern",
            "DELETE FROM clinical_safety_assets WHERE asset_id LIKE :prefix_pattern",
            "DELETE FROM knowledge_chunks WHERE source = :knowledge_source OR title LIKE :prefix_pattern",
            "DELETE FROM consultation_domains WHERE domain LIKE :prefix_pattern",
            "DELETE FROM consultation_slots WHERE slot_name LIKE :prefix_pattern",
        ):
            session.execute(
                text(sql),
                {
                    "prefix_pattern": prefix_pattern,
                    "knowledge_source": _knowledge_source(prefix),
                },
            )


def _count_rows(database_url: str, table_name: str, column_name: str, value: str) -> int:
    """统计指定表中某个测试标识对应的数据行数量。

    :param database_url: 数据库连接串。
    :param table_name: 表名。
    :param column_name: 字段名。
    :param value: 字段值。
    :return: 返回行数。
    """
    if table_name not in {"conversation_turns"}:
        raise ValueError(f"unsupported table for external smoke count: {table_name}")
    if column_name not in {"user_id"}:
        raise ValueError(f"unsupported column for external smoke count: {column_name}")
    session_factory = make_session_factory(database_url)
    with session_factory() as session:
        return int(
            session.execute(
                text(f"SELECT count(*) FROM {table_name} WHERE {column_name} = :value"),
                {"value": value},
            ).scalar_one()
            or 0
        )


def _with_database_retry(operation: Callable[[], _T], *, action: str) -> _T:
    """对外部开发数据库的短暂连接波动进行有限重试。

    :param operation: 需要执行的数据库操作。
    :param action: 当前操作的人类可读名称，用于失败诊断。
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


def _scope_ids(prefix: str, suffix: str) -> tuple[str, str, str]:
    """构造同一测试前缀下的用户、宠物和会话标识。

    :param prefix: 测试数据前缀。
    :param suffix: 场景后缀。
    :return: 返回 user_id、pet_id 和 session_id。
    """
    return (
        f"{prefix}_user_{suffix}",
        f"{prefix}_pet_{suffix}",
        f"{prefix}_session_{suffix}",
    )


def _policy_context(suffix: str) -> dict[str, str]:
    """构造 OPA 依赖探测使用的结构化上下文。

    :param suffix: 场景后缀。
    :return: 返回 OPA 输入上下文字典。
    """
    return {
        "request_id": f"req_{suffix}",
        "trace_id": f"trace_{suffix}",
        "user_id": f"user_{suffix}",
        "pet_id": f"pet_{suffix}",
        "session_id": f"session_{suffix}",
        "text": "当前请求仅用于外部依赖探测。",
    }


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


def _opa_data_url(base_url: str, package_name: str, rule_name: str) -> str:
    """构造兼容网关前缀的 OPA data API 地址。

    :param base_url: OPA 服务基础地址，可为服务根路径或已经包含 /v1 的地址。
    :param package_name: Rego package 名称。
    :param rule_name: Rego 规则名称。
    :return: 返回 OPA data API 完整地址。
    """
    normalized_base_url = base_url.rstrip("/")
    if not normalized_base_url.endswith("/v1"):
        normalized_base_url = f"{normalized_base_url}/v1"
    policy_path = "/".join((*package_name.split("."), rule_name))
    return f"{normalized_base_url}/data/{policy_path}"


def _knowledge_source(prefix: str) -> str:
    """构造外部 API 冒烟知识片段来源标识。

    :param prefix: 测试数据前缀。
    :return: 返回知识来源标识。
    """
    return f"external_api_smoke:{prefix}"


def _content_hash(text_value: str) -> str:
    """生成测试基线文本内容哈希。

    :param text_value: 待哈希文本。
    :return: 返回十六进制哈希摘要。
    """
    return hashlib.sha256(text_value.encode("utf-8")).hexdigest()


def _mem0_headers(external_env: dict[str, str]) -> dict[str, str]:
    """构造 Mem0 集成测试请求头。

    :param external_env: 外部依赖配置。
    :return: 返回 HTTP 请求头。
    """
    if not external_env.get("MEM0_API_KEY"):
        return {}
    return {"X-API-Key": external_env["MEM0_API_KEY"]}


def _enabled(name: str) -> bool:
    """判断布尔环境变量是否开启。

    :param name: 环境变量名称。
    :return: 开启时返回 True。
    """
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _timeout() -> float:
    """读取外部 API 集成测试超时时间。

    :return: 返回超时时间秒数。
    """
    return float(os.getenv("EXTERNAL_API_TEST_TIMEOUT_SECONDS", str(DEFAULT_EXTERNAL_TIMEOUT_SECONDS)))

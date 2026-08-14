"""
=============================================================================
文件：tests/integration/test_consultation_state_api_external.py
作用：通过真实 PostgreSQL、LiteLLM 与 OPA 验证问诊状态与回答充分性 API 纵向链路。
范围：覆盖 ConsultationSemanticExtractorAgent、ConsultationStateService、OPA 问诊回答充分性策略与 API 响应审计 metadata。
说明：本测试仅在显式开启 RUN_CONSULTATION_STATE_API_EXTERNAL_TEST 时执行，供 try-run
      或人工发布前加严验证使用；可按需通过 SSH 隧道连接远程开发依赖。
=============================================================================
"""

from __future__ import annotations

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
from vet_agent.db import ConsultationDomainModel, ConsultationSlotModel, make_session_factory
from vet_agent.observability import AgentPathNode


EXTERNAL_CONSULTATION_STATE_FLAG = "RUN_CONSULTATION_STATE_API_EXTERNAL_TEST"
DEFAULT_EXTERNAL_TIMEOUT_SECONDS = 75.0
DEFAULT_DATABASE_RETRY_ATTEMPTS = 3
DEFAULT_DATABASE_RETRY_DELAY_SECONDS = 2.0
EXPECTED_EMBEDDING_DIMENSION = 1024
SUPPORTED_ALEMBIC_VERSIONS = {
    "0014_task_routing_domain_catalog",
    "0015_consultation_semantic_extraction_contract",
}

# 外部集成测试写入的问诊目录基线仅包含结构化领域与槽位展示信息；
# 不包含关键词、正则或文本抽取规则，避免恢复旧版硬匹配路径。
CONSULTATION_DOMAIN_BASELINE: tuple[dict[str, Any], ...] = (
    {
        "domain": "gastrointestinal",
        "required_slots": ["species", "onset", "mental_status", "appetite", "vomiting", "stool"],
        "priority": 10,
    },
    {
        "domain": "respiratory",
        "required_slots": ["species", "onset", "mental_status", "breathing"],
        "priority": 20,
    },
    {
        "domain": "mobility",
        "required_slots": ["species", "onset", "mental_status", "pain_or_mobility"],
        "priority": 30,
    },
    {
        "domain": "behavior",
        "required_slots": ["species", "onset", "mental_status", "appetite", "behavior_context"],
        "priority": 40,
    },
    {
        "domain": "feeding",
        "required_slots": ["species", "life_stage_or_age", "weight", "current_food"],
        "priority": 50,
    },
    {
        "domain": "general",
        "required_slots": ["species", "onset", "mental_status", "appetite", "symptom_detail"],
        "priority": 100,
    },
)
CONSULTATION_SLOT_BASELINE: tuple[dict[str, Any], ...] = (
    {
        "slot_name": "species",
        "label": "物种",
        "question": "它是猫还是狗？如果是其他宠物，也请直接说物种。",
    },
    {
        "slot_name": "life_stage_or_age",
        "label": "年龄/生命阶段",
        "question": "它大概多大了？是幼年、成年还是老年？",
    },
    {
        "slot_name": "weight",
        "label": "体重",
        "question": "最近体重大约是多少？如果不确定，可以给一个大概范围。",
    },
    {
        "slot_name": "onset",
        "label": "起病时间",
        "question": "这个情况从什么时候开始的？是突然发生还是慢慢出现的？",
    },
    {
        "slot_name": "mental_status",
        "label": "精神状态",
        "question": "现在精神状态和平时比怎么样？正常、变差，还是明显萎靡？",
    },
    {
        "slot_name": "appetite",
        "label": "食欲饮水",
        "question": "食欲和饮水怎么样？和平时比有没有明显减少？",
    },
    {
        "slot_name": "vomiting",
        "label": "呕吐情况",
        "question": "有没有呕吐？如果有，今天大概吐了几次？",
    },
    {
        "slot_name": "stool",
        "label": "大便情况",
        "question": "大便是什么状态？有没有血、黑便、黏液，排便次数有没有变多？",
    },
    {
        "slot_name": "breathing",
        "label": "呼吸情况",
        "question": "呼吸有没有变快、费力、咳嗽、张口呼吸或喘不上气？",
    },
    {
        "slot_name": "pain_or_mobility",
        "label": "疼痛/活动",
        "question": "走路、站立、跳跃或被触碰时有没有疼痛或异常？",
    },
    {
        "slot_name": "behavior_context",
        "label": "行为场景",
        "question": "这个行为通常在什么场景发生？比如独处、见陌生人、吃饭、夜里或出门时。",
    },
    {
        "slot_name": "current_food",
        "label": "当前饮食",
        "question": "现在主要吃什么粮/食物？最近有没有换粮、加零食或吃到新东西？",
    },
    {
        "slot_name": "symptom_detail",
        "label": "症状补充",
        "question": "除了你说的情况，还有没有呕吐、腹泻、咳喘、疼痛、排尿排便异常？",
    },
)

_T = TypeVar("_T")


@pytest.fixture
def external_env() -> dict[str, str]:
    """读取问诊状态外部 API 集成测试所需配置。

    :return: 返回外部依赖配置字典。
    """
    if not _enabled(EXTERNAL_CONSULTATION_STATE_FLAG):
        pytest.skip(
            f"未开启 {EXTERNAL_CONSULTATION_STATE_FLAG}，跳过问诊状态外部 API 集成测试。"
        )
    enable_mem0 = _enabled("EXTERNAL_API_TEST_ENABLE_MEM0")
    required = {
        "DATABASE_URL": os.getenv("EXTERNAL_API_TEST_DATABASE_URL") or os.getenv("DATABASE_URL"),
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
        "TASK_ROUTING_OPA_BASE_URL": os.getenv("EXTERNAL_API_TEST_OPA_BASE_URL")
        or os.getenv("TASK_ROUTING_OPA_BASE_URL")
        or os.getenv("INPUT_SAFETY_OPA_BASE_URL"),
        "CONSULTATION_ANSWERABILITY_OPA_BASE_URL": os.getenv("EXTERNAL_API_TEST_OPA_BASE_URL")
        or os.getenv("CONSULTATION_ANSWERABILITY_OPA_BASE_URL")
        or os.getenv("INPUT_SAFETY_OPA_BASE_URL"),
    }
    if enable_mem0:
        required["MEM0_BASE_URL"] = os.getenv("EXTERNAL_API_TEST_MEM0_BASE_URL") or os.getenv("MEM0_BASE_URL")
        required["MEM0_API_KEY"] = os.getenv("EXTERNAL_API_TEST_MEM0_API_KEY") or os.getenv("MEM0_API_KEY")
    missing = [key for key, value in required.items() if not value]
    if missing:
        pytest.fail(
            f"{EXTERNAL_CONSULTATION_STATE_FLAG}=true 时缺少问诊状态外部依赖配置：{', '.join(missing)}。"
        )
    optional = {
        "QWEN_MODEL": os.getenv("EXTERNAL_API_TEST_QWEN_MODEL", os.getenv("QWEN_MODEL", "qwen-plus")),
        "QWEN_EMBEDDING_MODEL": os.getenv(
            "EXTERNAL_API_TEST_QWEN_EMBEDDING_MODEL",
            os.getenv("QWEN_EMBEDDING_MODEL", "text-embedding-v4"),
        ),
        "OPA_AUTH_TOKEN": os.getenv("EXTERNAL_API_TEST_OPA_AUTH_TOKEN")
        or os.getenv("CONSULTATION_ANSWERABILITY_OPA_AUTH_TOKEN")
        or os.getenv("INPUT_SAFETY_OPA_AUTH_TOKEN")
        or "",
    }
    return {**required, **optional, "ENABLE_MEM0": "true" if enable_mem0 else "false"}


@pytest.fixture
def external_prefix() -> str:
    """构造本轮问诊状态测试唯一数据前缀。

    :return: 返回测试数据前缀。
    """
    return f"consultation_it_{uuid4().hex[:12]}"


@pytest.fixture
def external_database(external_env: dict[str, str], external_prefix: str) -> Iterator[str]:
    """准备问诊状态 API 测试所需的数据库基线。

    :param external_env: 外部依赖配置。
    :param external_prefix: 测试数据前缀。
    :return: 返回数据库连接串。
    """
    database_url = external_env["DATABASE_URL"]
    user_id, pet_id, _session_id = _scope_ids(external_prefix)

    def cleanup_database() -> None:
        """清理问诊状态外部 API 测试数据。

        :return: 无返回值。
        """
        _cleanup_database_prefix(database_url, external_prefix)
        if external_env["ENABLE_MEM0"] == "true":
            _cleanup_mem0_scope(external_env, user_id=user_id, pet_id=pet_id)

    def assert_schema_ready() -> None:
        """校验外部数据库迁移版本与通用运行时基线。

        :return: 无返回值。
        """
        _assert_database_schema_ready(database_url)
        _assert_input_safety_candidates_ready(database_url)
        _assert_task_routing_domain_catalog_ready(database_url)

    def prepare_catalog_baseline() -> None:
        """写入问诊状态测试所需最小结构化目录基线。

        :return: 无返回值。
        """
        _prepare_consultation_catalog_baseline(database_url)

    def assert_catalog_ready() -> None:
        """校验问诊领域与槽位目录已经可供运行时读取。

        :return: 无返回值。
        """
        _assert_consultation_catalog_ready(database_url)

    _with_database_retry(cleanup_database, action="清理问诊状态测试前缀数据")
    _with_database_retry(assert_schema_ready, action="校验问诊状态数据库基线")
    _with_database_retry(prepare_catalog_baseline, action="写入问诊状态目录基线")
    _with_database_retry(assert_catalog_ready, action="校验问诊状态目录基线")
    try:
        yield database_url
    finally:
        _with_database_retry(cleanup_database, action="清理问诊状态测试前缀数据")


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
def test_consultation_state_external_dependencies_are_reachable(
    external_env: dict[str, str],
    external_database: str,
) -> None:
    """验证问诊状态外部测试所需真实依赖可达。

    :param external_env: 外部依赖配置。
    :param external_database: 已校验的数据库连接串。
    :return: 无返回值；断言通过表示外部依赖具备执行条件。
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

        embedding = _embedding_vector(external_env, "我家狗今天有点软便，精神正常。", client=client)
        assert len(embedding) == EXPECTED_EMBEDDING_DIMENSION

        consultation_opa = client.post(
            _opa_data_url(
                external_env["CONSULTATION_ANSWERABILITY_OPA_BASE_URL"],
                "vet_agent.consultation_state",
                "decision",
            ),
            headers=_opa_headers(external_env),
            json={"input": _consultation_policy_input()},
        )
        consultation_opa.raise_for_status()
        decision = consultation_opa.json().get("result") or {}
        assert decision["action"] == "answer"
        assert decision["mode"] == "user_requested_answer_now"
        assert decision["allow"] is True

        if external_env["ENABLE_MEM0"] == "true":
            mem0 = client.get(f"{external_env['MEM0_BASE_URL'].rstrip('/')}/docs")
            mem0.raise_for_status()


@pytest.mark.integration
def test_consultation_state_api_requires_followup_with_real_services(
    external_client: TestClient,
    external_env: dict[str, str],
    external_prefix: str,
) -> None:
    """验证问诊状态 API 首轮会走真实追问分支。

    :param external_client: 接入真实外部依赖的 API 测试客户端。
    :param external_env: 外部依赖配置。
    :param external_prefix: 测试数据前缀。
    :return: 无返回值；断言通过表示真实问诊状态主链路可用。
    """
    profile = _profile()
    response = external_client.post(
        "/agent/turns",
        json=_payload(
            "我家狗今天有点软便，精神正常。",
            user_id=f"{external_prefix}_user",
            pet_id=f"{external_prefix}_pet",
            session_id=f"{external_prefix}_session",
            profile=profile,
            idempotency_key=f"{external_prefix}_turn_1",
        ),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "requires_followup"
    assert data["vet_result"]["route"] == "rag_guided_followup"
    assert data["metadata"]["consultation_phase"] == "collecting_info"
    assert data["metadata"]["answerability"]["decision"] == "ask"
    assert data["metadata"]["answerability"]["mode"] == "needs_high_value_evidence"
    assert data["metadata"]["input_safety_decision"]["policy_backend"] == "opa"
    assert data["metadata"]["clinical_safety_resolution"]["policy_decision"]["policy_backend"] == "opa"

    memory_read = data["metadata"]["memory_read"]
    assert memory_read["audit"]["source"] == "postgres_authoritative_memory_with_mem0_projection"
    if external_env["ENABLE_MEM0"] == "true":
        assert memory_read["audit"]["details"]["mem0_enabled"] is True
        assert memory_read["audit"]["semantic_status"] in {"empty", "queried"}
    else:
        assert memory_read["audit"]["details"]["mem0_enabled"] is False
        assert memory_read["audit"]["semantic_status"] == "disabled"

    path = data["metadata"]["multi_agent_path"]
    assert AgentPathNode.MEMORY_AGENT.value in path
    assert AgentPathNode.CONSULTATION_SEMANTIC_EXTRACTOR_AGENT.value in path
    assert AgentPathNode.CONSULTATION_STATE_SERVICE.value in path
    assert AgentPathNode.CONSULTATION_ANSWERABILITY_POLICY_OPA.value in path
    assert AgentPathNode.RAG_QUESTION_PLANNER_AGENT.value in path
    assert AgentPathNode.QWEN_RESPONSE_AGENT.value not in path
    assert AgentPathNode.ANSWERABILITY_EVALUATOR.value not in path
    assert "我先不武断下结论" in data["output_text"]
    assert data["segments"][0]["type"] == "followup_consultation"


@pytest.mark.integration
def test_consultation_state_api_completes_when_user_requests_answer_now(
    external_client: TestClient,
    external_env: dict[str, str],
    external_prefix: str,
) -> None:
    """验证问诊状态 API 在真实回答充分性裁决下可完成阶段性回答。

    :param external_client: 接入真实外部依赖的 API 测试客户端。
    :param external_env: 外部依赖配置。
    :param external_prefix: 测试数据前缀。
    :return: 无返回值；断言通过表示真实问诊状态主链路可用。
    """
    profile = _profile()
    user_id = f"{external_prefix}_user"
    pet_id = f"{external_prefix}_pet"
    session_id = f"{external_prefix}_session"

    first = external_client.post(
        "/agent/turns",
        json=_payload(
            "我家狗今天有点软便，精神正常。",
            user_id=user_id,
            pet_id=pet_id,
            session_id=session_id,
            profile=profile,
            idempotency_key=f"{external_prefix}_turn_1",
        ),
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "requires_followup"

    second = external_client.post(
        "/agent/turns",
        json=_payload(
            "别再追问了，直接说目前怎么看。它是狗，3岁，12公斤，今天早上开始，精神食欲正常，没有呕吐，大便软便但没有血。",
            user_id=user_id,
            pet_id=pet_id,
            session_id=session_id,
            profile=profile,
            idempotency_key=f"{external_prefix}_turn_2",
        ),
    )

    assert second.status_code == 200, second.text
    data = second.json()
    assert data["status"] == "completed"
    assert data["vet_result"]["route"] == "standard_consultation"
    assert data["metadata"]["consultation_phase"] == "ready_to_answer"
    assert data["metadata"]["missing_slots"] == []
    assert data["metadata"]["answerability"]["decision"] == "answer"
    assert data["metadata"]["answerability"]["mode"] in {
        "user_requested_answer_now",
        "slot_complete",
        "sufficient_semantic_evidence",
    }
    assert data["metadata"]["input_safety_decision"]["policy_backend"] == "opa"
    assert data["metadata"]["clinical_safety_resolution"]["policy_decision"]["policy_backend"] == "opa"

    memory_read = data["metadata"]["memory_read"]
    assert memory_read["audit"]["source"] == "postgres_authoritative_memory_with_mem0_projection"
    if external_env["ENABLE_MEM0"] == "true":
        assert memory_read["audit"]["details"]["mem0_enabled"] is True
    else:
        assert memory_read["audit"]["details"]["mem0_enabled"] is False
        assert memory_read["audit"]["semantic_status"] == "disabled"

    path = data["metadata"]["multi_agent_path"]
    assert AgentPathNode.MEMORY_AGENT.value in path
    assert AgentPathNode.CONSULTATION_SEMANTIC_EXTRACTOR_AGENT.value in path
    assert AgentPathNode.CONSULTATION_STATE_SERVICE.value in path
    assert AgentPathNode.CONSULTATION_ANSWERABILITY_POLICY_OPA.value in path
    assert AgentPathNode.QWEN_RESPONSE_AGENT.value in path
    assert AgentPathNode.RAG_QUESTION_PLANNER_AGENT.value not in path
    assert AgentPathNode.ANSWERABILITY_EVALUATOR.value not in path


@contextmanager
def _configured_environment(
    monkeypatch: pytest.MonkeyPatch,
    external_env: dict[str, str],
) -> Iterator[None]:
    """向当前进程注入问诊状态外部 API 集成测试配置。

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
        "ENABLE_MEM0": external_env["ENABLE_MEM0"],
        "ENABLE_INPUT_SAFETY": "true",
        "INPUT_SAFETY_POLICY_BACKEND": "opa",
        "INPUT_SAFETY_OPA_BASE_URL": external_env["INPUT_SAFETY_OPA_BASE_URL"].rstrip("/"),
        "CLINICAL_SAFETY_OPA_BASE_URL": external_env["CLINICAL_SAFETY_OPA_BASE_URL"].rstrip("/"),
        "TASK_ROUTING_OPA_BASE_URL": external_env["TASK_ROUTING_OPA_BASE_URL"].rstrip("/"),
        "CONSULTATION_ANSWERABILITY_OPA_BASE_URL": external_env["CONSULTATION_ANSWERABILITY_OPA_BASE_URL"].rstrip("/"),
        "ENABLE_INPUT_SAFETY_GUARDRAILS": "false",
        "ENABLE_LLM_SEMANTIC_EXTRACTION": "true",
        "ENABLE_MEMORY_EXTRACTION": "false",
        "ENABLE_LLM_TASK_SPLITTER": "false",
        "CONSULTATION_MAX_FOLLOWUP_ROUNDS": "2",
        "QWEN_MAX_RETRIES": "0",
        "QWEN_CIRCUIT_BREAKER_FAILURE_THRESHOLD": "20",
        "LITELLM_TIMEOUT_SECONDS": str(_timeout()),
        "REQUIRE_API_AUTH": "false",
    }
    if external_env["ENABLE_MEM0"] == "true":
        values["MEM0_BASE_URL"] = external_env["MEM0_BASE_URL"].rstrip("/")
        values["MEM0_API_KEY"] = external_env["MEM0_API_KEY"]
        values["MEMORY_READ_ALLOW_SEMANTIC_DEGRADED"] = "false"
    else:
        monkeypatch.delenv("MEM0_BASE_URL", raising=False)
        monkeypatch.delenv("MEM0_API_KEY", raising=False)
    if external_env["OPA_AUTH_TOKEN"]:
        values["INPUT_SAFETY_OPA_AUTH_TOKEN"] = external_env["OPA_AUTH_TOKEN"]
        values["CLINICAL_SAFETY_OPA_AUTH_TOKEN"] = external_env["OPA_AUTH_TOKEN"]
        values["TASK_ROUTING_OPA_AUTH_TOKEN"] = external_env["OPA_AUTH_TOKEN"]
        values["CONSULTATION_ANSWERABILITY_OPA_AUTH_TOKEN"] = external_env["OPA_AUTH_TOKEN"]
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
    """构造问诊状态 API 集成测试请求载荷。

    :param text_value: 用户输入文本。
    :param user_id: 测试用户标识。
    :param pet_id: 测试宠物标识。
    :param session_id: 测试会话标识。
    :param profile: 可信宠物画像。
    :param idempotency_key: 本轮幂等键。
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
        "turn_options": {
            "idempotency_key": idempotency_key,
            "max_followup_questions": 2,
        },
    }


def _scope_assertion(
    *,
    user_id: str,
    pet_id: str,
    session_id: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """构造问诊状态 API 集成测试使用的可信范围声明。

    :param user_id: 测试用户标识。
    :param pet_id: 测试宠物标识。
    :param session_id: 测试会话标识。
    :param profile: 可信宠物画像。
    :return: 返回范围声明字典。
    """
    now = datetime.now(UTC).isoformat()
    return {
        "schema_version": "v1",
        "issuer": "consultation-state-api-integration-test",
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
            "system": "consultation-state-api-integration-test",
            "database": "vet_agent",
            "table": "master_pet_info",
            "record_id": pet_id,
            "record_updated_at": now,
            "data_source": "integration_test",
        },
        "session_policy": {"binding_mode": "single_user_pet_per_session"},
    }


def _profile() -> dict[str, Any]:
    """构造问诊状态 API 集成测试宠物画像。

    :return: 返回可信宠物画像字典。
    """
    return {
        "species": "dog",
        "name": "豆豆",
        "age": "3岁",
        "sex": "female",
        "weight_kg": 12.0,
        "breed": "柯基",
    }


def _consultation_policy_input() -> dict[str, Any]:
    """构造 OPA 问诊回答充分性依赖探测输入。

    :return: 返回 OPA Data API input 字典。
    """
    return {
        "context": {
            "request_id": "req_consultation_dependency_probe",
            "trace_id": "trace_consultation_dependency_probe",
            "user_id": "user_consultation_dependency_probe",
            "pet_id": "pet_consultation_dependency_probe",
            "session_id": "session_consultation_dependency_probe",
        },
        "state": {
            "domain": "gastrointestinal",
            "phase": "collecting_info",
            "followup_rounds": 0,
            "asked_question_count": 0,
            "has_chief_complaint": True,
            "has_species": True,
        },
        "intent": {
            "answer_now": True,
            "wants_triage": False,
            "correction": False,
            "raw_intent": "先给阶段性判断",
        },
        "limits": {
            "max_followup_rounds": 2,
            "min_known_categories": 2,
            "max_questions": 3,
        },
        "evidence_profile": {
            "minimum_context": True,
            "known_category_count": 1,
            "known_categories": ["patient_identity"],
            "advisory_slots": ["onset"],
            "unresolved_slots": ["onset"],
        },
        "unresolved_slots": ["onset"],
        "advisory_slots": ["onset"],
    }


def _assert_database_schema_ready(database_url: str) -> None:
    """确认外部数据库迁移版本满足问诊状态测试要求。

    :param database_url: 数据库连接串。
    :return: 无返回值。
    """
    session_factory = make_session_factory(database_url)
    with session_factory() as session:
        version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    if version not in SUPPORTED_ALEMBIC_VERSIONS:
        pytest.fail(
            "外部数据库未迁移到当前问诊状态 API 测试所需版本。"
            f" 当前版本：{version!r}，期望版本之一：{sorted(SUPPORTED_ALEMBIC_VERSIONS)!r}。"
        )


def _assert_input_safety_candidates_ready(database_url: str) -> None:
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
        pytest.fail("外部数据库缺少基础输入安全候选定义，无法执行真实问诊状态 API 测试。")


def _assert_task_routing_domain_catalog_ready(database_url: str) -> None:
    """确认任务路由任务域目录具备运行时最小基线。

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
                    FROM task_routing_domains
                    WHERE enabled IS TRUE
                    """
                )
            ).scalar_one()
            or 0
        )
    if count <= 0:
        pytest.fail("外部数据库缺少任务路由域目录，无法执行真实问诊状态 API 测试。")


def _prepare_consultation_catalog_baseline(database_url: str) -> None:
    """向外部数据库写入问诊状态测试所需的最小目录基线。

    :param database_url: 数据库连接串。
    :return: 无返回值。
    """
    session_factory = make_session_factory(database_url)
    with session_factory.begin() as session:
        for item in CONSULTATION_DOMAIN_BASELINE:
            domain = str(item["domain"])
            if session.get(ConsultationDomainModel, domain) is not None:
                continue
            session.add(
                ConsultationDomainModel(
                    domain=domain,
                    required_slots=list(item["required_slots"]),
                    enabled=True,
                    priority=int(item["priority"]),
                    version="v1",
                )
            )
        for index, item in enumerate(CONSULTATION_SLOT_BASELINE, start=1):
            slot_name = str(item["slot_name"])
            if session.get(ConsultationSlotModel, slot_name) is not None:
                continue
            session.add(
                ConsultationSlotModel(
                    slot_name=slot_name,
                    question=str(item["question"]),
                    label=str(item["label"]),
                    priority=index * 10,
                    enabled=True,
                    version="v1",
                )
            )


def _assert_consultation_catalog_ready(database_url: str) -> None:
    """确认问诊领域与槽位目录具备本测试所需的最小基线。

    :param database_url: 数据库连接串。
    :return: 无返回值。
    """
    session_factory = make_session_factory(database_url)
    with session_factory() as session:
        domain_rows = session.execute(
            text(
                """
                SELECT domain, required_slots
                FROM consultation_domains
                WHERE enabled IS TRUE
                """
            )
        ).mappings()
        domains = {
            str(row["domain"]): [str(slot) for slot in (row["required_slots"] or [])]
            for row in domain_rows
        }
        slots = {
            str(slot)
            for slot in session.execute(
                text(
                    """
                    SELECT slot_name
                    FROM consultation_slots
                    WHERE enabled IS TRUE
                    """
                )
            ).scalars()
        }
    expected_domains = {
        str(item["domain"]): {str(slot) for slot in item["required_slots"]}
        for item in CONSULTATION_DOMAIN_BASELINE
    }
    expected_slots = {str(item["slot_name"]) for item in CONSULTATION_SLOT_BASELINE}
    missing_domains = sorted(set(expected_domains) - set(domains))
    invalid_domains = {
        domain: sorted(required_slots - set(domains.get(domain, [])))
        for domain, required_slots in expected_domains.items()
        if domain in domains and required_slots - set(domains.get(domain, []))
    }
    missing_slots = sorted(expected_slots - slots)
    if missing_domains:
        pytest.fail(f"外部数据库缺少问诊状态测试所需领域：{missing_domains!r}。")
    if invalid_domains:
        pytest.fail(f"外部数据库问诊领域缺少必要槽位：{invalid_domains!r}。")
    if missing_slots:
        pytest.fail(f"外部数据库缺少问诊状态测试所需槽位：{missing_slots!r}。")


def _cleanup_database_prefix(database_url: str, prefix: str) -> None:
    """按测试前缀清理问诊状态相关运行期数据。

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


def _cleanup_mem0_scope(external_env: dict[str, str], *, user_id: str, pet_id: str) -> None:
    """按用户与宠物范围清理真实 Mem0 测试记忆。

    :param external_env: 外部依赖配置。
    :param user_id: 测试用户标识。
    :param pet_id: 测试宠物标识。
    :return: 无返回值。
    """
    if external_env["ENABLE_MEM0"] != "true":
        return
    with httpx.Client(timeout=_timeout()) as client:
        response = client.delete(
            f"{external_env['MEM0_BASE_URL'].rstrip('/')}/memories",
            headers=_mem0_headers(external_env),
            params={"user_id": user_id, "run_id": pet_id},
        )
        response.raise_for_status()


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
    """构造问诊状态 API 测试的用户、宠物和会话标识。

    :param prefix: 测试数据前缀。
    :return: 返回 user_id、pet_id 和 session_id。
    """
    return (
        f"{prefix}_user_consultation",
        f"{prefix}_pet_consultation",
        f"{prefix}_session_consultation",
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


def _mem0_headers(external_env: dict[str, str]) -> dict[str, str]:
    """构造 Mem0 API 请求头。

    :param external_env: 外部依赖配置。
    :return: 返回 Mem0 请求头。
    """
    return {
        "Content-Type": "application/json",
        "X-API-Key": external_env["MEM0_API_KEY"],
    }


def _opa_headers(external_env: dict[str, str]) -> dict[str, str]:
    """构造 OPA API 请求头。

    :param external_env: 外部依赖配置。
    :return: 返回 OPA 请求头。
    """
    headers = {"Content-Type": "application/json"}
    if external_env["OPA_AUTH_TOKEN"]:
        headers["Authorization"] = f"Bearer {external_env['OPA_AUTH_TOKEN']}"
    return headers


def _enabled(value: str | None) -> bool:
    """判断布尔环境变量是否开启。

    :param value: 环境变量名称。
    :return: 开启时返回 True。
    """
    return os.getenv(value, "").strip().lower() in {"1", "true", "yes", "on"}


def _timeout() -> float:
    """读取外部 API 集成测试超时时间。

    :return: 返回超时时间秒数。
    """
    return float(os.getenv("EXTERNAL_API_TEST_TIMEOUT_SECONDS", str(DEFAULT_EXTERNAL_TIMEOUT_SECONDS)))

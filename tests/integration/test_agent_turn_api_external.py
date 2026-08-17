"""
=============================================================================
文件：tests/integration/test_agent_turn_api_external.py
作用：验证兽医 Agent 主链路从入口请求转换到输出清洗与安全复核的真实集成行为。
范围：覆盖 HTTP 入口、请求适配、真实 PostgreSQL、LiteLLM、OPA、问诊状态、
      回答相关与追问相关 RAG、回复生成上下文编译、输出安全复核和流式出口。
说明：本测试仅在显式开启 RUN_AGENT_TURN_API_EXTERNAL_TEST 时执行；可选使用 SSH
      隧道将远程开发环境端口转发到本地，再由测试进程连接本地转发端口。
=============================================================================
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from ingress import create_app, set_orchestrator
from vet_agent import Container, Settings, VetAgentIngressOrchestrator, set_container
from vet_agent.answer_rag import AnswerRagStrategy
from vet_agent.followup_rag import FollowupRagStrategy
from vet_agent.db import (
    ClinicalSafetyAssetModel,
    ClinicalSafetyChunkModel,
    ConsultationDomainModel,
    ConsultationSlotModel,
    KnowledgeChunkModel,
    TaskRoutingDomainModel,
    make_session_factory,
)
from vet_agent.observability import AgentPathNode


EXTERNAL_AGENT_TURN_FLAG = "RUN_AGENT_TURN_API_EXTERNAL_TEST"
DEFAULT_EXTERNAL_TIMEOUT_SECONDS = 90.0
DEFAULT_DATABASE_RETRY_ATTEMPTS = 3
DEFAULT_DATABASE_RETRY_DELAY_SECONDS = 2.0
EXPECTED_EMBEDDING_DIMENSION = 1024
DEFAULT_SSH_HOST = "devlop@47.97.19.58"
DEFAULT_SSH_KEY_PATH = Path("/home/vancer17/.ssh/AlibabaCloudLinux")
DEFAULT_POSTGRES_LOCAL_PORT = 15432
DEFAULT_LITELLM_LOCAL_PORT = 14000
DEFAULT_OPA_LOCAL_PORT = 18181
DEFAULT_MEM0_LOCAL_PORT = 18001
SUPPORTED_ALEMBIC_VERSIONS = {
    "0014_task_routing_domain_catalog",
    "0015_consultation_semantic_extraction_contract",
    "0016_output_safety_candidate_definitions",
    "0017_persistent_background_tasks",
    "0018_rag_retrieval_misses",
}

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
TASK_ROUTING_DOMAIN_BASELINE: tuple[dict[str, Any], ...] = (
    {
        "domain": "gastrointestinal",
        "title": "胃肠道",
        "description": "处理软便、腹泻、呕吐、食欲下降与消化道相关问题。",
        "priority": 10,
    },
    {
        "domain": "respiratory",
        "title": "呼吸系统",
        "description": "处理咳嗽、喘气、呼吸费力与气道相关问题。",
        "priority": 20,
    },
    {
        "domain": "mobility",
        "title": "运动与疼痛",
        "description": "处理跛行、疼痛、跳跃异常与活动受限问题。",
        "priority": 30,
    },
    {
        "domain": "behavior",
        "title": "行为与情绪",
        "description": "处理行为改变、焦虑、夜叫与环境触发问题。",
        "priority": 40,
    },
    {
        "domain": "feeding",
        "title": "饮食与营养",
        "description": "处理换粮、喂养管理、饮食结构和营养问题。",
        "priority": 50,
    },
    {
        "domain": "general",
        "title": "综合问诊",
        "description": "处理暂未明确归类的综合性宠物健康问题。",
        "priority": 100,
    },
)

_T = TypeVar("_T")


@dataclass(frozen=True)
class ExternalAgentTurnEnvironment:
    """表示主链路外部集成测试所需的运行环境配置。

    :return: 无返回值；该类型用于承载脱敏后的外部依赖地址与测试开关。
    """

    database_url: str
    litellm_base_url: str
    litellm_api_key: str
    qwen_model: str
    qwen_embedding_model: str
    input_safety_opa_base_url: str
    clinical_safety_opa_base_url: str
    task_routing_opa_base_url: str
    consultation_answerability_opa_base_url: str
    mem0_base_url: str | None
    mem0_api_key: str | None
    opa_auth_token: str | None
    enable_mem0: bool

    def __repr__(self) -> str:
        """生成用于测试日志的脱敏表示。

        :return: 返回脱敏后的配置摘要。
        """
        return (
            f"{type(self).__name__}("
            f"database_url=<redacted>, "
            f"litellm_base_url={self.litellm_base_url!r}, "
            f"qwen_model={self.qwen_model!r}, "
            f"qwen_embedding_model={self.qwen_embedding_model!r}, "
            f"enable_mem0={self.enable_mem0!r})"
        )


@pytest.fixture(scope="session")
def external_env() -> Iterator[ExternalAgentTurnEnvironment]:
    """读取主链路外部集成测试所需环境。

    :return: 返回外部依赖配置。
    """
    if not _enabled(EXTERNAL_AGENT_TURN_FLAG):
        pytest.skip(f"未开启 {EXTERNAL_AGENT_TURN_FLAG}，跳过主链路外部集成测试。")
    with _ssh_tunnel_context():
        raw = _load_external_env()
        if _enabled("AGENT_TURN_EXTERNAL_SSH_TUNNEL"):
            raw = _rewrite_external_env_with_tunnel(raw)
        yield raw


@pytest.fixture
def external_prefix() -> str:
    """构造主链路外部测试使用的唯一前缀。

    :return: 返回测试数据前缀。
    """
    return f"agent_turn_it_{uuid4().hex[:12]}"


@pytest.fixture
def external_database(
    external_env: ExternalAgentTurnEnvironment,
    external_prefix: str,
) -> Iterator[str]:
    """准备主链路外部集成测试所需的数据库基线。

    :param external_env: 外部依赖配置。
    :param external_prefix: 测试数据前缀。
    :return: 返回数据库连接串。
    """
    database_url = external_env.database_url
    user_id, pet_id, session_id = _scope_ids(external_prefix)

    def cleanup_database() -> None:
        """清理本轮主链路测试写入的运行期数据。

        :return: 无返回值。
        """
        _cleanup_runtime_database_prefix(database_url, external_prefix)
        if external_env.enable_mem0 and external_env.mem0_base_url and external_env.mem0_api_key:
            _cleanup_mem0_scope(external_env, user_id=user_id, pet_id=pet_id)

    def assert_schema_ready() -> None:
        """校验数据库迁移版本与候选定义基线。

        :return: 无返回值。
        """
        _assert_database_schema_ready(database_url)
        _assert_input_safety_candidates_ready(database_url)
        _assert_output_safety_candidates_ready(database_url)

    def prepare_catalog_baseline() -> None:
        """写入任务路由与问诊目录基线。

        :return: 无返回值。
        """
        _prepare_consultation_catalog_baseline(database_url)
        _prepare_task_routing_catalog_baseline(database_url)

    def assert_catalog_ready() -> None:
        """校验任务路由与问诊目录基线已可供运行时读取。

        :return: 无返回值。
        """
        _assert_task_routing_domain_catalog_ready(database_url)
        _assert_consultation_catalog_ready(database_url)

    def prepare_clinical_safety_baseline() -> None:
        """写入临床安全最小资产与向量基线。

        :return: 无返回值。
        """
        _prepare_clinical_safety_baseline(database_url, external_env, external_prefix)

    def prepare_followup_rag_baseline() -> None:
        """写入追问相关 RAG 真实知识基线。

        :return: 无返回值。
        """
        _prepare_followup_rag_baseline(database_url, external_env, external_prefix)

    def prepare_answer_rag_baseline() -> None:
        """写入回答相关 RAG 真实知识基线。

        :return: 无返回值。
        """
        _prepare_answer_rag_baseline(database_url, external_env, external_prefix)

    _with_database_retry(cleanup_database, action="清理主链路测试前缀数据")
    _with_database_retry(assert_schema_ready, action="校验主链路数据库基线")
    _with_database_retry(prepare_catalog_baseline, action="写入主链路目录基线")
    _with_database_retry(assert_catalog_ready, action="校验主链路目录基线")
    _with_database_retry(prepare_clinical_safety_baseline, action="写入临床安全最小基线")
    _with_database_retry(prepare_followup_rag_baseline, action="写入追问 RAG 真实知识基线")
    _with_database_retry(prepare_answer_rag_baseline, action="写入回答 RAG 真实知识基线")
    try:
        yield database_url
    finally:
        _with_database_retry(cleanup_database, action="清理主链路测试前缀数据")


@pytest.fixture
def external_client(
    monkeypatch: pytest.MonkeyPatch,
    external_env: ExternalAgentTurnEnvironment,
    external_database: str,
    tmp_path: Path,
) -> Iterator[TestClient]:
    """构造接入真实外部依赖的 API 测试客户端。

    :param monkeypatch: pytest 环境变量替换工具。
    :param external_env: 外部依赖配置。
    :param external_database: 已校验的数据库连接串。
    :param tmp_path: 当前测试进程使用的临时数据目录。
    :return: 返回 FastAPI 测试客户端。
    """
    del external_database
    with _configured_environment(monkeypatch, external_env, data_dir=tmp_path):
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
def test_agent_turn_external_dependencies_are_reachable(
    external_env: ExternalAgentTurnEnvironment,
    external_database: str,
) -> None:
    """验证主链路外部集成测试所需真实依赖可达。

    :param external_env: 外部依赖配置。
    :param external_database: 已校验的数据库连接串。
    :return: 无返回值；断言通过表示真实依赖具备执行条件。
    """
    del external_database
    with httpx.Client(timeout=_timeout()) as client:
        lite_llm = client.get(
            f"{external_env.litellm_base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {external_env.litellm_api_key}"},
        )
        lite_llm.raise_for_status()
        model_ids = {item.get("id") for item in lite_llm.json().get("data", [])}
        assert external_env.qwen_model in model_ids
        assert external_env.qwen_embedding_model in model_ids

        vector = _embedding_vector(external_env, "主链路真实服务集成测试。", client=client)
        assert len(vector) == EXPECTED_EMBEDDING_DIMENSION

        input_opa = client.post(
            _opa_data_url(external_env.input_safety_opa_base_url, "vet_agent.input_safety", "decision"),
            json={"input": _empty_input_safety_policy_input()},
        )
        input_opa.raise_for_status()
        assert input_opa.json().get("result", {}).get("action") == "allow"

        clinical_opa = client.post(
            _opa_data_url(external_env.clinical_safety_opa_base_url, "vet_agent.clinical_safety", "decision"),
            json={"input": _empty_clinical_safety_policy_input()},
        )
        clinical_opa.raise_for_status()
        assert clinical_opa.json().get("result", {}).get("action") == "allow"

        task_opa = client.post(
            _opa_data_url(external_env.task_routing_opa_base_url, "vet_agent.task_routing", "decision"),
            json={"input": _valid_task_routing_policy_input()},
        )
        task_opa.raise_for_status()
        assert task_opa.json().get("result", {}).get("action") == "allow"

        consultation_opa = client.post(
            _opa_data_url(
                external_env.consultation_answerability_opa_base_url,
                "vet_agent.consultation_state",
                "decision",
            ),
            json={"input": _consultation_policy_input()},
        )
        consultation_opa.raise_for_status()
        consultation_result = consultation_opa.json().get("result") or {}
        assert consultation_result["action"] == "answer"
        assert consultation_result["allow"] is True

        if external_env.enable_mem0:
            assert external_env.mem0_base_url is not None
            assert external_env.mem0_api_key is not None
            mem0 = client.get(
                f"{external_env.mem0_base_url.rstrip('/')}/docs",
                headers=_mem0_headers(external_env),
            )
            mem0.raise_for_status()


@pytest.mark.integration
def test_agent_turn_api_completes_answer_branch_with_output_safety(
    external_client: TestClient,
    external_env: ExternalAgentTurnEnvironment,
    external_prefix: str,
) -> None:
    """验证主链路在真实服务下能够完成回答分支并收口到输出安全复核。

    :param external_client: 接入真实外部依赖的 API 测试客户端。
    :param external_env: 外部依赖配置。
    :param external_prefix: 测试数据前缀。
    :return: 无返回值；断言通过表示主回答链路可用。
    """
    data = _resolved_answer_turn(external_client, external_prefix)

    assert data["status"] == "completed"
    assert data["output_text"].strip()
    assert data["vet_result"]["route"] == "standard_consultation"
    assert data["metadata"]["consultation_phase"] == "ready_to_answer"
    assert data["metadata"]["answerability"]["decision"] == "answer"
    assert data["metadata"]["input_safety_decision"]["policy_backend"] == "opa"
    assert data["metadata"]["clinical_safety_resolution"]["policy_decision"]["policy_backend"] == "opa"
    assert data["metadata"]["output_safety_decision"]["enabled"] is True
    assert data["metadata"]["output_safety_decision"]["mode"] == "observe"
    assert data["metadata"]["output_safety_decision"]["policy_backend"] == "local"
    assert data["metadata"]["output_safety_decision"]["candidate_count"] == 0
    assert data["metadata"]["output_safety_decision"]["action"] == "allow"

    path = data["metadata"]["multi_agent_path"]
    assert path.index(AgentPathNode.ANSWER_RAG_SERVICE.value) < path.index(AgentPathNode.QWEN_RESPONSE_AGENT.value)
    assert path.index(AgentPathNode.QWEN_RESPONSE_AGENT.value) < path.index(AgentPathNode.OUTPUT_SAFETY_SERVICE.value)
    assert path[-2:] == [
        AgentPathNode.OUTPUT_SAFETY_SERVICE.value,
        AgentPathNode.OUTPUT_SAFETY_POLICY_LOCAL.value,
    ]
    assert AgentPathNode.INPUT_SAFETY_SERVICE.value in path
    assert AgentPathNode.INPUT_SAFETY_POLICY_OPA.value in path
    assert AgentPathNode.PET_CONTEXT_AGENT.value in path
    assert AgentPathNode.CLINICAL_SAFETY_SEMANTIC_EXTRACTOR_AGENT.value in path
    assert AgentPathNode.CLINICAL_SAFETY_EVALUATOR.value in path
    assert AgentPathNode.CLINICAL_SAFETY_POLICY_OPA.value in path
    assert AgentPathNode.MEMORY_AGENT.value in path
    assert AgentPathNode.TASK_ROUTER_AGENT.value in path
    assert AgentPathNode.CONSULTATION_SEMANTIC_EXTRACTOR_AGENT.value in path
    assert AgentPathNode.CONSULTATION_STATE_SERVICE.value in path
    assert AgentPathNode.CONSULTATION_ANSWERABILITY_POLICY_OPA.value in path
    assert AgentPathNode.RESPONSE_GENERATION_CONTEXT_BUILDER.value in path
    assert AgentPathNode.QWEN_RESPONSE_AGENT.value in path
    assert AgentPathNode.OUTPUT_SAFETY_SERVICE.value in path

    answer_rag = data["metadata"]["answer_rag"]
    assert answer_rag["strategy"] == AnswerRagStrategy.LLAMA_INDEX_PGVECTOR.value
    assert answer_rag["retrieval"]["hit_count"] >= 1
    assert any(
        hit["metadata"].get("prefix") == external_prefix
        for hit in answer_rag["retrieval"]["hits"]
    )

    memory_read = data["metadata"]["memory_read"]
    assert memory_read["audit"]["source"] == "postgres_authoritative_memory_with_mem0_projection"
    if external_env.enable_mem0:
        assert memory_read["audit"]["details"]["mem0_enabled"] is True
    else:
        assert memory_read["audit"]["details"]["mem0_enabled"] is False
        assert memory_read["audit"]["semantic_status"] == "disabled"


@pytest.mark.integration
def test_agent_turn_api_requires_followup_branch_with_output_safety(
    external_client: TestClient,
    external_env: ExternalAgentTurnEnvironment,
    external_prefix: str,
) -> None:
    """验证主链路在真实服务下能够收束到追问分支并完成输出安全复核。

    :param external_client: 接入真实外部依赖的 API 测试客户端。
    :param external_env: 外部依赖配置。
    :param external_prefix: 测试数据前缀。
    :return: 无返回值；断言通过表示主追问链路可用。
    """
    data = _followup_turn(external_client, external_prefix)

    assert data["status"] == "requires_followup"
    assert data["vet_result"]["route"] == "rag_guided_followup"
    assert data["metadata"]["consultation_phase"] == "collecting_info"
    assert data["metadata"]["output_safety_decision"]["enabled"] is True
    assert data["metadata"]["output_safety_decision"]["mode"] == "observe"
    assert data["metadata"]["output_safety_decision"]["policy_backend"] == "local"
    assert data["metadata"]["output_safety_decision"]["candidate_count"] == 0
    assert data["metadata"]["output_safety_decision"]["action"] == "allow"

    followup_plan = data["metadata"]["followup_question_plan"]
    assert followup_plan is not None
    assert followup_plan["strategy"] == FollowupRagStrategy.LLAMA_INDEX_PGVECTOR_STRUCTURED.value
    assert followup_plan["retrieval"]["hit_count"] >= 1

    path = data["metadata"]["multi_agent_path"]
    assert AgentPathNode.FOLLOWUP_RAG_SERVICE.value in path
    assert AgentPathNode.FOLLOWUP_RAG_RETRIEVER.value in path
    assert AgentPathNode.FOLLOWUP_RAG_PLANNER.value in path
    assert AgentPathNode.OUTPUT_SAFETY_SERVICE.value in path
    assert path[-2:] == [
        AgentPathNode.OUTPUT_SAFETY_SERVICE.value,
        AgentPathNode.OUTPUT_SAFETY_POLICY_LOCAL.value,
    ]
    assert path.index(AgentPathNode.FOLLOWUP_RAG_PLANNER.value) < path.index(AgentPathNode.OUTPUT_SAFETY_SERVICE.value)

    if external_env.enable_mem0:
        assert data["metadata"]["memory_read"]["audit"]["details"]["mem0_enabled"] is True
    else:
        assert data["metadata"]["memory_read"]["audit"]["details"]["mem0_enabled"] is False


@pytest.mark.integration
def test_agent_turn_api_openai_compatible_response_with_output_safety(
    external_client: TestClient,
    external_prefix: str,
) -> None:
    """验证 OpenAI 兼容入口仍能穿过完整主链路并返回同构响应。

    :param external_client: 接入真实外部依赖的 API 测试客户端。
    :param external_prefix: 测试数据前缀。
    :return: 无返回值；断言通过表示 OpenAI 兼容契约可用。
    """
    user_id, pet_id, session_id = _scope_ids(external_prefix)
    response = external_client.post(
        "/openai/v1/responses",
        json=_payload(
            "别再追问了，直接说目前怎么看。它是狗，3岁，12公斤，今天早上开始软便，精神和食欲正常，没有呕吐，也没有血便。",
            user_id=user_id,
            pet_id=pet_id,
            session_id=session_id,
            profile=_profile(),
            idempotency_key=f"{external_prefix}_openai_turn_1",
        ),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["object"] == "response"
    assert data["status"] == "completed"
    assert data["metadata"]["output_safety_decision"]["enabled"] is True
    assert data["metadata"]["output_safety_decision"]["policy_backend"] == "local"
    assert data["metadata"]["output_safety_decision"]["mode"] == "observe"
    assert data["metadata"]["output_safety_decision"]["action"] == "allow"

    path = data["metadata"]["multi_agent_path"]
    assert AgentPathNode.OUTPUT_SAFETY_SERVICE.value in path
    assert path[-2:] == [
        AgentPathNode.OUTPUT_SAFETY_SERVICE.value,
        AgentPathNode.OUTPUT_SAFETY_POLICY_LOCAL.value,
    ]


@pytest.mark.integration
def test_agent_turn_api_streams_finalized_events(
    external_client: TestClient,
    external_prefix: str,
) -> None:
    """验证流式出口仍基于已完成的主链路响应生成事件。

    :param external_client: 接入真实外部依赖的 API 测试客户端。
    :param external_prefix: 测试数据前缀。
    :return: 无返回值；断言通过表示流式出口未绕过主链路收口。
    """
    user_id, pet_id, session_id = _scope_ids(external_prefix)
    with external_client.stream(
        "POST",
        "/agent/turns",
        json=_payload(
            "别再追问了，直接说目前怎么看。它是狗，3岁，12公斤，今天早上开始软便，精神和食欲正常，没有呕吐，也没有血便。",
            user_id=user_id,
            pet_id=pet_id,
            session_id=session_id,
            profile=_profile(),
            idempotency_key=f"{external_prefix}_stream_turn_1",
            stream=True,
        ),
    ) as response:
        body = response.read().decode("utf-8")

    assert response.status_code == 200, body
    events = _parse_sse_events(body)
    event_names = [event["event"] for event in events]
    assert "turn.started" in event_names
    assert "segment.started" in event_names
    assert "segment.completed" in event_names
    assert "turn.completed" in event_names
    assert "turn.failed" not in event_names


@contextmanager
def _configured_environment(
    monkeypatch: pytest.MonkeyPatch,
    external_env: ExternalAgentTurnEnvironment,
    *,
    data_dir: Path,
) -> Iterator[None]:
    """向当前进程注入主链路外部集成测试配置。

    :param monkeypatch: pytest 环境变量替换工具。
    :param external_env: 外部依赖配置。
    :param data_dir: 当前测试进程使用的临时数据目录。
    :return: 返回上下文管理器迭代器。
    """
    values: dict[str, str] = {
        "DATABASE_URL": external_env.database_url,
        "LITELLM_BASE_URL": external_env.litellm_base_url.rstrip("/"),
        "LITELLM_API_KEY": external_env.litellm_api_key,
        "QWEN_MODEL": external_env.qwen_model,
        "QWEN_EMBEDDING_MODEL": external_env.qwen_embedding_model,
        "ENABLE_RAG_EMBEDDINGS": "false",
        "ENABLE_MEM0": "true" if external_env.enable_mem0 else "false",
        "ENABLE_INPUT_SAFETY": "true",
        "INPUT_SAFETY_POLICY_BACKEND": "opa",
        "INPUT_SAFETY_POLICY_ALWAYS_CALL": "true",
        "INPUT_SAFETY_OPA_BASE_URL": external_env.input_safety_opa_base_url.rstrip("/"),
        "CLINICAL_SAFETY_OPA_BASE_URL": external_env.clinical_safety_opa_base_url.rstrip("/"),
        "TASK_ROUTING_OPA_BASE_URL": external_env.task_routing_opa_base_url.rstrip("/"),
        "CONSULTATION_ANSWERABILITY_OPA_BASE_URL": external_env.consultation_answerability_opa_base_url.rstrip("/"),
        "ENABLE_INPUT_SAFETY_GUARDRAILS": "false",
        "ENABLE_OUTPUT_SAFETY": "true",
        "OUTPUT_SAFETY_MODE": "observe",
        "OUTPUT_SAFETY_POLICY_BACKEND": "local",
        "OUTPUT_SAFETY_POLICY_ALWAYS_CALL": "true",
        "ENABLE_OUTPUT_SAFETY_GUARDRAILS": "false",
        "OUTPUT_SAFETY_OPA_BASE_URL": external_env.input_safety_opa_base_url.rstrip("/"),
        "OUTPUT_SAFETY_OPA_PACKAGE_PATH": "vet_agent.output_safety",
        "OUTPUT_SAFETY_OPA_RULE_NAME": "decision",
        "OUTPUT_SAFETY_MAX_CHARS": "16000",
        "OUTPUT_SAFETY_SYSTEM_PROMPT_LEAKAGE_THRESHOLD": "40",
        "CLINICAL_SAFETY_VECTOR_MIN_SCORE": "0.999",
        "FOLLOWUP_RAG_TOP_K": "5",
        "FOLLOWUP_RAG_VECTOR_MIN_SCORE": "0.0",
        "ANSWER_RAG_TOP_K": "5",
        "ANSWER_RAG_VECTOR_MIN_SCORE": "0.0",
        "ANSWER_RAG_FILTER_BY_DOMAIN": "true",
        "ENABLE_LLM_SEMANTIC_EXTRACTION": "true",
        "ENABLE_MEMORY_EXTRACTION": "false",
        "ENABLE_LLM_TASK_SPLITTER": "false",
        "CONSULTATION_MAX_FOLLOWUP_ROUNDS": "2",
        "QWEN_MAX_RETRIES": "0",
        "QWEN_CIRCUIT_BREAKER_FAILURE_THRESHOLD": "20",
        "LITELLM_TIMEOUT_SECONDS": str(_timeout()),
        "REQUIRE_API_AUTH": "false",
        "VET_AGENT_DATA_DIR": str(data_dir),
        "VET_AGENT_SEED_DIR": str(data_dir / "unused-seeds"),
    }
    if external_env.enable_mem0:
        assert external_env.mem0_base_url is not None
        assert external_env.mem0_api_key is not None
        values["MEM0_BASE_URL"] = external_env.mem0_base_url.rstrip("/")
        values["MEM0_API_KEY"] = external_env.mem0_api_key
        values["MEMORY_READ_ALLOW_SEMANTIC_DEGRADED"] = "false"
    else:
        monkeypatch.delenv("MEM0_BASE_URL", raising=False)
        monkeypatch.delenv("MEM0_API_KEY", raising=False)
    if external_env.opa_auth_token:
        values["INPUT_SAFETY_OPA_AUTH_TOKEN"] = external_env.opa_auth_token
        values["CLINICAL_SAFETY_OPA_AUTH_TOKEN"] = external_env.opa_auth_token
        values["TASK_ROUTING_OPA_AUTH_TOKEN"] = external_env.opa_auth_token
        values["CONSULTATION_ANSWERABILITY_OPA_AUTH_TOKEN"] = external_env.opa_auth_token
        values["OUTPUT_SAFETY_OPA_AUTH_TOKEN"] = external_env.opa_auth_token
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
    stream: bool = False,
) -> dict[str, Any]:
    """构造主链路外部集成测试请求载荷。

    :param text_value: 用户输入文本。
    :param user_id: 测试用户标识。
    :param pet_id: 测试宠物标识。
    :param session_id: 测试会话标识。
    :param profile: 可信宠物画像。
    :param idempotency_key: 本轮幂等键。
    :param stream: 是否请求流式响应。
    :return: 返回 API 请求载荷。
    """
    return {
        "input": text_value,
        "stream": stream,
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
    """构造主链路外部集成测试使用的可信范围声明。

    :param user_id: 测试用户标识。
    :param pet_id: 测试宠物标识。
    :param session_id: 测试会话标识。
    :param profile: 可信宠物画像。
    :return: 返回范围声明字典。
    """
    now = datetime.now(UTC).isoformat()
    return {
        "schema_version": "v1",
        "issuer": "agent-turn-api-external-test",
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
            "system": "agent-turn-api-external-test",
            "database": "vet_agent",
            "table": "master_pet_info",
            "record_id": pet_id,
            "record_updated_at": now,
            "data_source": "integration_test",
        },
        "session_policy": {"binding_mode": "single_user_pet_per_session"},
    }


def _profile() -> dict[str, Any]:
    """构造主链路外部集成测试宠物画像。

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


def _resolved_answer_turn(external_client: TestClient, external_prefix: str) -> dict[str, Any]:
    """执行一次可用于回答分支断言的真实主链路回合。

    :param external_client: 接入真实外部依赖的 API 测试客户端。
    :param external_prefix: 测试数据前缀。
    :return: 返回最终完成态的 API 响应 JSON。
    """
    user_id, pet_id, session_id = _scope_ids(external_prefix)
    response = external_client.post(
        "/agent/turns",
        json=_payload(
            "别再追问了，直接说目前怎么看。它是狗，3岁，12公斤，今天早上开始软便，精神和食欲正常，没有呕吐，也没有血便。",
            user_id=user_id,
            pet_id=pet_id,
            session_id=session_id,
            profile=_profile(),
            idempotency_key=f"{external_prefix}_answer_turn_1",
        ),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    if data["status"] == "requires_followup":
        response = external_client.post(
            "/agent/turns",
            json=_payload(
                "补充上一轮信息：没有其他异常，我明确要求现在基于已有资料给出阶段性回答，不再继续追问。",
                user_id=user_id,
                pet_id=pet_id,
                session_id=session_id,
                profile=_profile(),
                idempotency_key=f"{external_prefix}_answer_turn_2",
            ),
        )
        assert response.status_code == 200, response.text
        data = response.json()
    return data


def _followup_turn(external_client: TestClient, external_prefix: str) -> dict[str, Any]:
    """执行一次更可能进入追问分支的真实主链路回合。

    :param external_client: 接入真实外部依赖的 API 测试客户端。
    :param external_prefix: 测试数据前缀。
    :return: 返回最终响应 JSON。
    """
    user_id, pet_id, session_id = _scope_ids(external_prefix)
    response = external_client.post(
        "/agent/turns",
        json=_payload(
            "我家狗今天有点软便，精神正常。",
            user_id=user_id,
            pet_id=pet_id,
            session_id=session_id,
            profile=_profile(),
            idempotency_key=f"{external_prefix}_followup_turn_1",
        ),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _cleanup_runtime_database_prefix(database_url: str, prefix: str) -> None:
    """按测试前缀清理主链路相关运行期数据。

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
            "DELETE FROM background_tasks WHERE user_id LIKE :user_pattern OR pet_id LIKE :pet_pattern OR session_id LIKE :session_pattern",
            "DELETE FROM clinical_safety_chunks WHERE metadata ->> 'prefix' = :prefix",
            "DELETE FROM clinical_safety_assets WHERE metadata ->> 'prefix' = :prefix",
            "DELETE FROM knowledge_chunks WHERE ingestion_batch = :prefix OR metadata ->> 'prefix' = :prefix",
        ):
            session.execute(
                text(sql),
                {
                    "user_pattern": user_pattern,
                    "pet_pattern": pet_pattern,
                    "session_pattern": session_pattern,
                    "prefix": prefix,
                },
            )


def _prepare_consultation_catalog_baseline(database_url: str) -> None:
    """向外部数据库写入问诊目录测试基线。

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


def _prepare_task_routing_catalog_baseline(database_url: str) -> None:
    """向外部数据库写入任务路由目录测试基线。

    :param database_url: 数据库连接串。
    :return: 无返回值。
    """
    session_factory = make_session_factory(database_url)
    with session_factory.begin() as session:
        for item in TASK_ROUTING_DOMAIN_BASELINE:
            domain = str(item["domain"])
            if session.get(TaskRoutingDomainModel, domain) is not None:
                continue
            session.add(
                TaskRoutingDomainModel(
                    domain=domain,
                    title=str(item["title"]),
                    description=str(item["description"]),
                    priority=int(item["priority"]),
                    enabled=True,
                    version="v1",
                )
            )


def _prepare_clinical_safety_baseline(
    database_url: str,
    external_env: ExternalAgentTurnEnvironment,
    prefix: str,
) -> None:
    """向外部数据库写入临床安全最小资产与向量基线。

    :param database_url: 数据库连接串。
    :param external_env: 外部依赖配置。
    :param prefix: 测试数据前缀。
    :return: 无返回值。
    """
    embedding_text = _clinical_safety_embedding_text()
    embedding = _embedding_vector(external_env, embedding_text)
    if len(embedding) != EXPECTED_EMBEDDING_DIMENSION:
        pytest.fail(f"外部 embedding 维度不符合临床安全 chunk 要求：{len(embedding)}。")

    asset_id = f"{prefix}_clinical_asset"
    chunk_id = f"{prefix}_clinical_chunk"
    session_factory = make_session_factory(database_url)
    with session_factory.begin() as session:
        if session.get(ClinicalSafetyAssetModel, asset_id) is None:
            session.add(
                ClinicalSafetyAssetModel(
                    asset_id=asset_id,
                    code=f"CLINICAL_SAFETY_{prefix.upper()}_SOFT_STOOL",
                    asset_type="danger_pattern",
                    canonical_name="犬软便临床安全基线",
                    category="gastrointestinal",
                    species_scope=["dog"],
                    sex_scope=[],
                    age_scope=[],
                    severity="caution",
                    action_class="safety_warning",
                    aliases=["soft stool"],
                    carriers=["clinical_pattern"],
                    user_expressions=["狗今天有点软便"],
                    symptoms=["soft stool"],
                    recognition_phrases=["软便", "便便稀", "大便偏软"],
                    required_context={"species": "dog"},
                    decision_hints={"default_action": "observe"},
                    clinical_risk_summary="用于验证临床安全最小资产与 pgvector 召回底座就绪。",
                    triage_message="当前输入仅用于测试临床安全召回链路。",
                    source={"prefix": prefix, "origin": "main_chain_external_test"},
                    raw_text={"prefix": prefix},
                    version="v1",
                    enabled=True,
                    review_status="approved",
                    published_at=datetime.now(UTC),
                    metadata_json={"prefix": prefix, "main_chain_external_test": True},
                )
            )
        if session.get(ClinicalSafetyChunkModel, chunk_id) is None:
            session.add(
                ClinicalSafetyChunkModel(
                    chunk_id=chunk_id,
                    asset_id=asset_id,
                    chunk_type="recognition",
                    title="犬软便临床安全向量基线",
                    embedding_text=embedding_text,
                    embedding=embedding,
                    embedding_model=external_env.qwen_embedding_model,
                    embedding_dimension=len(embedding),
                    content_hash=hashlib.sha256(embedding_text.encode("utf-8")).hexdigest(),
                    version="v1",
                    enabled=True,
                    review_status="approved",
                    metadata_json={"prefix": prefix, "main_chain_external_test": True},
                )
            )


def _prepare_followup_rag_baseline(
    database_url: str,
    external_env: ExternalAgentTurnEnvironment,
    prefix: str,
) -> None:
    """向外部数据库写入追问相关 RAG 的真实知识基线。

    :param database_url: 数据库连接串。
    :param external_env: 外部依赖配置。
    :param prefix: 测试数据前缀。
    :return: 无返回值。
    """
    embedding_text = _followup_rag_embedding_text()
    embedding = _embedding_vector(external_env, embedding_text)
    if len(embedding) != EXPECTED_EMBEDDING_DIMENSION:
        pytest.fail(f"外部 embedding 维度不符合 knowledge_chunks.embedding 要求：{len(embedding)}。")

    source = f"{prefix}_followup_rag_source"
    session_factory = make_session_factory(database_url)
    with session_factory.begin() as session:
        session.execute(
            text(
                """
                DELETE FROM knowledge_chunks
                WHERE source = :source
                   OR ingestion_batch = :prefix
                   OR metadata ->> 'prefix' = :prefix
                """
            ),
            {"source": source, "prefix": prefix},
        )
        session.add(
            KnowledgeChunkModel(
                source=source,
                title="犬软便追问知识基线",
                content=embedding_text,
                embedding=embedding,
                public_citation=True,
                copyright_risk="low",
                domain="gastrointestinal",
                species="dog",
                source_url=None,
                version="v1",
                enabled=True,
                review_status="approved",
                quality_score=1.0,
                last_reviewed_at=datetime.now(UTC),
                disabled_reason=None,
                ingestion_batch=prefix,
                metadata_json={
                    "chunk_type": "followup_questions",
                    "main_chain_external_test": True,
                    "prefix": prefix,
                },
            )
        )


def _prepare_answer_rag_baseline(
    database_url: str,
    external_env: ExternalAgentTurnEnvironment,
    prefix: str,
) -> None:
    """向外部数据库写入回答相关 RAG 的真实知识基线。

    :param database_url: 数据库连接串。
    :param external_env: 外部依赖配置。
    :param prefix: 测试数据前缀。
    :return: 无返回值。
    """
    embedding_text = _answer_rag_embedding_text()
    embedding = _embedding_vector(external_env, embedding_text)
    if len(embedding) != EXPECTED_EMBEDDING_DIMENSION:
        pytest.fail(f"外部 embedding 维度不符合 knowledge_chunks.embedding 要求：{len(embedding)}。")

    source = f"{prefix}_answer_rag_source"
    session_factory = make_session_factory(database_url)
    with session_factory.begin() as session:
        session.execute(
            text(
                """
                DELETE FROM knowledge_chunks
                WHERE source = :source
                   OR ingestion_batch = :prefix
                   OR metadata ->> 'prefix' = :prefix
                """
            ),
            {"source": source, "prefix": prefix},
        )
        session.add(
            KnowledgeChunkModel(
                source=source,
                title="犬软便回答知识基线",
                content=embedding_text,
                embedding=embedding,
                public_citation=True,
                copyright_risk="low",
                domain="gastrointestinal",
                species="dog",
                source_url=None,
                version="v1",
                enabled=True,
                review_status="approved",
                quality_score=1.0,
                last_reviewed_at=datetime.now(UTC),
                disabled_reason=None,
                ingestion_batch=prefix,
                metadata_json={
                    "chunk_type": "home_advice",
                    "main_chain_external_test": True,
                    "prefix": prefix,
                },
            )
        )


def _assert_database_schema_ready(database_url: str) -> None:
    """确认外部数据库迁移版本满足主链路测试要求。

    :param database_url: 数据库连接串。
    :return: 无返回值。
    """
    session_factory = make_session_factory(database_url)
    with session_factory() as session:
        version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    if version not in SUPPORTED_ALEMBIC_VERSIONS:
        pytest.fail(
            "外部数据库未迁移到当前主链路集成测试所需版本。"
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
        pytest.fail("外部数据库缺少基础输入安全候选定义，无法执行主链路集成测试。")


def _assert_output_safety_candidates_ready(database_url: str) -> None:
    """确认输出安全候选定义表具备运行时最小基线。

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
                    FROM output_safety_candidate_definitions
                    WHERE enabled IS TRUE
                    """
                )
            ).scalar_one()
            or 0
        )
    if count <= 0:
        pytest.fail("外部数据库缺少输出安全候选定义，无法执行主链路集成测试。")


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
        pytest.fail("外部数据库缺少任务路由域目录，无法执行主链路集成测试。")


def _assert_consultation_catalog_ready(database_url: str) -> None:
    """确认问诊领域与槽位目录具备主链路测试所需基线。

    :param database_url: 数据库连接串。
    :return: 无返回值。
    """
    session_factory = make_session_factory(database_url)
    with session_factory() as session:
        domains = {
            str(row["domain"]): [str(slot) for slot in (row["required_slots"] or [])]
            for row in session.execute(
                text(
                    """
                    SELECT domain, required_slots
                    FROM consultation_domains
                    WHERE enabled IS TRUE
                    """
                )
            ).mappings()
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
        pytest.fail(f"外部数据库缺少主链路测试所需问诊领域：{missing_domains!r}。")
    if invalid_domains:
        pytest.fail(f"外部数据库问诊领域缺少必要槽位：{invalid_domains!r}。")
    if missing_slots:
        pytest.fail(f"外部数据库缺少主链路测试所需问诊槽位：{missing_slots!r}。")


def _cleanup_mem0_scope(external_env: ExternalAgentTurnEnvironment, *, user_id: str, pet_id: str) -> None:
    """按用户与宠物范围清理真实 Mem0 测试记忆。

    :param external_env: 外部依赖配置。
    :param user_id: 测试用户标识。
    :param pet_id: 测试宠物标识。
    :return: 无返回值。
    """
    if not external_env.enable_mem0 or external_env.mem0_base_url is None or external_env.mem0_api_key is None:
        return
    with httpx.Client(timeout=_timeout()) as client:
        response = client.delete(
            f"{external_env.mem0_base_url.rstrip('/')}/memories",
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
    """构造主链路测试的用户、宠物和会话标识。

    :param prefix: 测试数据前缀。
    :return: 返回 user_id、pet_id 和 session_id。
    """
    return (
        f"{prefix}_user",
        f"{prefix}_pet",
        f"{prefix}_session",
    )


def _embedding_vector(
    external_env: ExternalAgentTurnEnvironment,
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
        f"{external_env.litellm_base_url.rstrip('/')}/embeddings",
        headers={"Authorization": f"Bearer {external_env.litellm_api_key}"},
        json={
            "model": external_env.qwen_embedding_model,
            "input": text_value,
        },
    )
    embedding.raise_for_status()
    vector = embedding.json()["data"][0]["embedding"]
    return [float(value) for value in vector]


def _clinical_safety_embedding_text() -> str:
    """构造临床安全外部 API 测试使用的真实知识正文。

    :return: 返回用于真实 embedding 的知识文本。
    """
    return (
        "犬软便临床安全基线：当狗出现软便、精神正常、食欲正常、没有呕吐和血便时，"
        "用于验证临床安全候选召回链路是否能够在真实 pgvector 底座中正常读取。"
        "这段文本只用于测试资产可用性，不参与最终医学建议，也不应作为硬编码规则。"
    )


def _followup_rag_embedding_text() -> str:
    """构造追问相关 RAG 外部 API 测试使用的真实知识正文。

    :return: 返回用于真实 embedding 的知识文本。
    """
    return (
        "犬软便追问知识基线：当狗今天有点软便、精神正常，或饭后缩成一团趴着时，"
        "优先追问腹部是否紧绷或疼痛、碰肚子会不会躲开、有没有呼吸变快、费力、张口呼吸，"
        "以及是否伴随呕吐、干呕、腹泻、便血、食欲下降、饮水变化或精神变差。"
        "这条知识不要求优先追问起病时间，而是用于帮助判断当前更需要补充哪类高价值证据。"
        "这条知识只用于指导追问顺序，不提供诊断或治疗结论。"
    )


def _answer_rag_embedding_text() -> str:
    """构造回答相关 RAG 外部 API 测试使用的真实知识正文。

    :return: 返回用于真实 embedding 的知识文本。
    """
    return (
        "犬软便回答知识基线：当狗 3 岁、12 公斤，今天早上开始软便，精神和食欲正常，没有呕吐、"
        "没有血便时，可以先给阶段性观察建议。重点关注接下来 24 到 48 小时的精神状态、饮水、"
        "排便次数、便便是否变稀或带血、是否出现腹痛、呕吐、黑便、持续腹泻或明显萎靡。"
        "如果出现红旗信号，或者软便持续加重，就应尽快线下就医。"
    )


def _opa_data_url(base_url: str, package_name: str, rule_name: str) -> str:
    """构造兼容网关前缀的 OPA Data API 地址。

    :param base_url: OPA 服务基础地址。
    :param package_name: Rego package 名称。
    :param rule_name: Rego 规则名称。
    :return: 返回 OPA Data API 完整地址。
    """
    normalized_base_url = base_url.rstrip("/")
    if not normalized_base_url.endswith("/v1"):
        normalized_base_url = f"{normalized_base_url}/v1"
    policy_path = "/".join((*package_name.split("."), rule_name))
    return f"{normalized_base_url}/data/{policy_path}"


def _mem0_headers(external_env: ExternalAgentTurnEnvironment) -> dict[str, str]:
    """构造 Mem0 API 请求头。

    :param external_env: 外部依赖配置。
    :return: 返回 Mem0 请求头。
    """
    assert external_env.mem0_api_key is not None
    return {
        "Content-Type": "application/json",
        "X-API-Key": external_env.mem0_api_key,
    }


def _empty_input_safety_policy_input() -> dict[str, Any]:
    """构造 OPA 输入安全依赖探测使用的空候选输入。

    :return: 返回 OPA Data API input 字典。
    """
    return {
        "context": {
            "request_id": "req_main_chain_dependency_probe",
            "trace_id": "trace_main_chain_dependency_probe",
            "user_id": "user_main_chain_dependency_probe",
            "pet_id": "pet_main_chain_dependency_probe",
            "session_id": "session_main_chain_dependency_probe",
        },
        "candidates": [],
    }


def _empty_clinical_safety_policy_input() -> dict[str, Any]:
    """构造 OPA 临床安全依赖探测使用的空候选输入。

    :return: 返回 OPA Data API input 字典。
    """
    return {
        "context": {
            "request_id": "req_main_chain_clinical_dependency_probe",
            "trace_id": "trace_main_chain_clinical_dependency_probe",
            "user_id": "user_main_chain_clinical_dependency_probe",
            "pet_id": "pet_main_chain_clinical_dependency_probe",
            "session_id": "session_main_chain_clinical_dependency_probe",
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


def _valid_task_routing_policy_input() -> dict[str, Any]:
    """构造 OPA 任务路由依赖探测使用的有效计划输入。

    :return: 返回 OPA Data API input 字典。
    """
    return {
        "context": {
            "request_id": "req_main_chain_task_routing_probe",
            "trace_id": "trace_main_chain_task_routing_probe",
            "user_id": "user_main_chain_task_routing_probe",
            "pet_id": "pet_main_chain_task_routing_probe",
            "session_id": "session_main_chain_task_routing_probe",
        },
        "schema_version": "v1",
        "max_task_count": 5,
        "allowed_domains": sorted({item["domain"] for item in TASK_ROUTING_DOMAIN_BASELINE}),
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


def _consultation_policy_input() -> dict[str, Any]:
    """构造 OPA 问诊回答充分性依赖探测输入。

    :return: 返回 OPA Data API input 字典。
    """
    return {
        "context": {
            "request_id": "req_main_chain_consultation_probe",
            "trace_id": "trace_main_chain_consultation_probe",
            "user_id": "user_main_chain_consultation_probe",
            "pet_id": "pet_main_chain_consultation_probe",
            "session_id": "session_main_chain_consultation_probe",
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


def _timeout() -> float:
    """读取主链路外部集成测试超时时间。

    :return: 返回超时时间秒数。
    """
    return float(os.getenv("AGENT_TURN_EXTERNAL_TIMEOUT_SECONDS", str(DEFAULT_EXTERNAL_TIMEOUT_SECONDS)))


def _enabled(name: str) -> bool:
    """读取布尔环境变量。

    :param name: 环境变量名称。
    :return: 返回解析后的布尔值。
    """
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _load_external_env() -> ExternalAgentTurnEnvironment:
    """读取主链路外部集成测试所需配置并完成基础校验。

    :return: 返回外部依赖配置。
    """
    database_url = _env_any("AGENT_TURN_EXTERNAL_DATABASE_URL", "EXTERNAL_API_TEST_DATABASE_URL", "DATABASE_URL")
    litellm_base_url = _env_any("AGENT_TURN_EXTERNAL_LITELLM_BASE_URL", "EXTERNAL_API_TEST_LITELLM_BASE_URL", "LITELLM_BASE_URL")
    litellm_api_key = _env_any("AGENT_TURN_EXTERNAL_LITELLM_API_KEY", "EXTERNAL_API_TEST_LITELLM_API_KEY", "LITELLM_API_KEY", "LITELLM_MASTER_KEY")
    qwen_model = _env_any("AGENT_TURN_EXTERNAL_QWEN_MODEL", "EXTERNAL_API_TEST_QWEN_MODEL", "QWEN_MODEL", default="qwen-plus")
    qwen_embedding_model = _env_any(
        "AGENT_TURN_EXTERNAL_QWEN_EMBEDDING_MODEL",
        "EXTERNAL_API_TEST_QWEN_EMBEDDING_MODEL",
        "QWEN_EMBEDDING_MODEL",
        default="text-embedding-v4",
    )
    input_safety_opa_base_url = _env_any(
        "AGENT_TURN_EXTERNAL_INPUT_SAFETY_OPA_BASE_URL",
        "EXTERNAL_API_TEST_OPA_BASE_URL",
        "INPUT_SAFETY_OPA_BASE_URL",
        default="http://127.0.0.1:8181/v1",
    )
    clinical_safety_opa_base_url = _env_any(
        "AGENT_TURN_EXTERNAL_CLINICAL_SAFETY_OPA_BASE_URL",
        "CLINICAL_SAFETY_OPA_BASE_URL",
        default=input_safety_opa_base_url,
    )
    task_routing_opa_base_url = _env_any(
        "AGENT_TURN_EXTERNAL_TASK_ROUTING_OPA_BASE_URL",
        "TASK_ROUTING_OPA_BASE_URL",
        default=input_safety_opa_base_url,
    )
    consultation_answerability_opa_base_url = _env_any(
        "AGENT_TURN_EXTERNAL_CONSULTATION_ANSWERABILITY_OPA_BASE_URL",
        "CONSULTATION_ANSWERABILITY_OPA_BASE_URL",
        default=input_safety_opa_base_url,
    )
    mem0_base_url = _optional_env("AGENT_TURN_EXTERNAL_MEM0_BASE_URL", "EXTERNAL_API_TEST_MEM0_BASE_URL", "MEM0_BASE_URL")
    mem0_api_key = _optional_env("AGENT_TURN_EXTERNAL_MEM0_API_KEY", "EXTERNAL_API_TEST_MEM0_API_KEY", "MEM0_API_KEY")
    opa_auth_token = _optional_env(
        "AGENT_TURN_EXTERNAL_OPA_AUTH_TOKEN",
        "EXTERNAL_API_TEST_OPA_AUTH_TOKEN",
        "CONSULTATION_ANSWERABILITY_OPA_AUTH_TOKEN",
        "INPUT_SAFETY_OPA_AUTH_TOKEN",
    )
    enable_mem0 = _enabled("AGENT_TURN_EXTERNAL_ENABLE_MEM0") or _enabled("EXTERNAL_API_TEST_ENABLE_MEM0") or _enabled("ENABLE_MEM0")
    if enable_mem0 and (not mem0_base_url or not mem0_api_key):
        pytest.fail("启用 MEM0 时必须同时提供 MEM0_BASE_URL 与 MEM0_API_KEY。")
    return ExternalAgentTurnEnvironment(
        database_url=database_url,
        litellm_base_url=litellm_base_url,
        litellm_api_key=litellm_api_key,
        qwen_model=qwen_model,
        qwen_embedding_model=qwen_embedding_model,
        input_safety_opa_base_url=input_safety_opa_base_url,
        clinical_safety_opa_base_url=clinical_safety_opa_base_url,
        task_routing_opa_base_url=task_routing_opa_base_url,
        consultation_answerability_opa_base_url=consultation_answerability_opa_base_url,
        mem0_base_url=mem0_base_url,
        mem0_api_key=mem0_api_key,
        opa_auth_token=opa_auth_token,
        enable_mem0=enable_mem0,
    )


def _env_any(*names: str, default: str | None = None) -> str:
    """按优先级读取环境变量。

    :param names: 候选环境变量名称。
    :param default: 全部缺失时使用的默认值。
    :return: 返回读取到的字符串。
    :raises AssertionError: 所有候选都缺失且未提供默认值时抛出。
    """
    for name in names:
        value = os.getenv(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if default is not None:
        return default
    raise AssertionError(f"缺少必要环境变量：{', '.join(names)}")


def _optional_env(*names: str) -> str | None:
    """按优先级读取可选环境变量。

    :param names: 候选环境变量名称。
    :return: 返回读取到的字符串或 None。
    """
    for name in names:
        value = os.getenv(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


@contextmanager
def _ssh_tunnel_context() -> Iterator[None]:
    """按需启动 SSH 隧道，将远程开发服务转发到本地。

    :return: 返回上下文管理器迭代器。
    """
    if not _enabled("AGENT_TURN_EXTERNAL_SSH_TUNNEL"):
        yield
        return

    ssh_host = os.getenv("AGENT_TURN_EXTERNAL_SSH_HOST", DEFAULT_SSH_HOST).strip()
    ssh_key = Path(os.getenv("AGENT_TURN_EXTERNAL_SSH_KEY", str(DEFAULT_SSH_KEY_PATH))).expanduser()
    postgres_port = int(os.getenv("AGENT_TURN_EXTERNAL_POSTGRES_LOCAL_PORT", str(DEFAULT_POSTGRES_LOCAL_PORT)))
    litellm_port = int(os.getenv("AGENT_TURN_EXTERNAL_LITELLM_LOCAL_PORT", str(DEFAULT_LITELLM_LOCAL_PORT)))
    opa_port = int(os.getenv("AGENT_TURN_EXTERNAL_OPA_LOCAL_PORT", str(DEFAULT_OPA_LOCAL_PORT)))
    mem0_port = int(os.getenv("AGENT_TURN_EXTERNAL_MEM0_LOCAL_PORT", str(DEFAULT_MEM0_LOCAL_PORT)))

    forwards = (
        f"{postgres_port}:127.0.0.1:5432",
        f"{litellm_port}:127.0.0.1:4000",
        f"{opa_port}:127.0.0.1:8181",
        f"{mem0_port}:127.0.0.1:8001",
    )
    command = [
        "ssh",
        "-i",
        str(ssh_key),
        "-N",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
    ]
    for forward in forwards:
        command.extend(["-L", forward])
    command.append(ssh_host)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        _wait_for_local_ports((postgres_port, litellm_port, opa_port, mem0_port), process)
        yield
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def _wait_for_local_ports(ports: tuple[int, ...], process: subprocess.Popen[bytes]) -> None:
    """等待 SSH 隧道本地端口就绪。

    :param ports: 待检查的本地端口。
    :param process: SSH 进程句柄。
    :return: 无返回值。
    :raises AssertionError: SSH 隧道在超时时间内未就绪时抛出。
    """
    deadline = time.monotonic() + max(10.0, _timeout())
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=5)
            raise AssertionError(
                "SSH 隧道启动失败。"
                f" stdout={stdout.decode('utf-8', errors='ignore')} stderr={stderr.decode('utf-8', errors='ignore')}"
            )
        if all(_port_open(port) for port in ports):
            return
        time.sleep(0.5)
    raise AssertionError(f"SSH 隧道未在超时时间内就绪，端口：{ports!r}")


def _port_open(port: int) -> bool:
    """检查本地端口是否可连接。

    :param port: 本地端口。
    :return: 端口可连接时返回 True。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _rewrite_external_env_with_tunnel(external_env: ExternalAgentTurnEnvironment) -> ExternalAgentTurnEnvironment:
    """将外部依赖地址重写为 SSH 隧道本地转发地址。

    :param external_env: 原始外部依赖配置。
    :return: 返回重写后的外部依赖配置。
    """
    postgres_port = int(os.getenv("AGENT_TURN_EXTERNAL_POSTGRES_LOCAL_PORT", str(DEFAULT_POSTGRES_LOCAL_PORT)))
    litellm_port = int(os.getenv("AGENT_TURN_EXTERNAL_LITELLM_LOCAL_PORT", str(DEFAULT_LITELLM_LOCAL_PORT)))
    opa_port = int(os.getenv("AGENT_TURN_EXTERNAL_OPA_LOCAL_PORT", str(DEFAULT_OPA_LOCAL_PORT)))
    mem0_port = int(os.getenv("AGENT_TURN_EXTERNAL_MEM0_LOCAL_PORT", str(DEFAULT_MEM0_LOCAL_PORT)))
    return ExternalAgentTurnEnvironment(
        database_url=_rewrite_database_url(external_env.database_url, postgres_port),
        litellm_base_url=_rewrite_http_url(external_env.litellm_base_url, litellm_port),
        litellm_api_key=external_env.litellm_api_key,
        qwen_model=external_env.qwen_model,
        qwen_embedding_model=external_env.qwen_embedding_model,
        input_safety_opa_base_url=_rewrite_http_url(external_env.input_safety_opa_base_url, opa_port),
        clinical_safety_opa_base_url=_rewrite_http_url(external_env.clinical_safety_opa_base_url, opa_port),
        task_routing_opa_base_url=_rewrite_http_url(external_env.task_routing_opa_base_url, opa_port),
        consultation_answerability_opa_base_url=_rewrite_http_url(
            external_env.consultation_answerability_opa_base_url,
            opa_port,
        ),
        mem0_base_url=_rewrite_http_url(external_env.mem0_base_url, mem0_port) if external_env.mem0_base_url else None,
        mem0_api_key=external_env.mem0_api_key,
        opa_auth_token=external_env.opa_auth_token,
        enable_mem0=external_env.enable_mem0,
    )


def _rewrite_http_url(url: str, port: int) -> str:
    """重写 HTTP(S) URL 的主机与端口。

    :param url: 原始 URL。
    :param port: 目标本地端口。
    :return: 返回重写后的 URL。
    """
    parsed = urlparse(url)
    netloc = f"127.0.0.1:{port}"
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def _rewrite_database_url(database_url: str, port: int) -> str:
    """重写 PostgreSQL 连接串到本地 SSH 隧道端口。

    :param database_url: 原始数据库连接串。
    :param port: 目标本地端口。
    :return: 返回重写后的数据库连接串。
    """
    url = make_url(database_url)
    return str(url.set(host="127.0.0.1", port=port))


def _parse_sse_events(body: str) -> list[dict[str, Any]]:
    """解析 SSE 文本为结构化事件列表。

    :param body: SSE 原始响应正文。
    :return: 返回结构化事件列表。
    """
    events: list[dict[str, Any]] = []
    current_event: str | None = None
    current_data: list[str] = []
    for line in body.splitlines():
        if line.startswith("event: "):
            current_event = line.removeprefix("event: ").strip()
            continue
        if line.startswith("data: "):
            current_data.append(line.removeprefix("data: ").strip())
            continue
        if line.strip() == "" and current_event is not None:
            payload = json.loads("\n".join(current_data) or "{}")
            events.append({"event": current_event, "data": payload})
            current_event = None
            current_data = []
    if current_event is not None:
        payload = json.loads("\n".join(current_data) or "{}")
        events.append({"event": current_event, "data": payload})
    return events

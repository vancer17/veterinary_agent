from __future__ import annotations

from fastapi.testclient import TestClient

from src.ingress.app import app
from src.ingress.orchestrator import set_orchestrator
from src.vet_agent.agents.task_splitter import TaskSplitterAgent
from src.vet_agent.config import Settings
from src.vet_agent.container import get_container
from src.vet_agent.ingress_adapter import VetAgentIngressOrchestrator
from src.vet_agent.repositories.rules import FileRuleRepository


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("ALLOW_MOCK_LLM", "true")
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("VET_AGENT_DATA_DIR", str(tmp_path))
    get_container.cache_clear()
    set_orchestrator(VetAgentIngressOrchestrator(get_container()))
    return TestClient(app)


def _payload(text: str, **extra):
    payload = {
        "input": text,
        "stream": False,
        "vet_context": {
            "user_id": "u1",
            "session_id": "s1",
            "pet_id": "p1",
            "pet_info": {
                "species": "犬",
                "breed": "柯基",
                "age": "3岁",
                "weight_kg": 12,
            },
        },
    }
    payload.update(extra)
    return payload


def _payload_without_pet_info(text: str, session_id: str = "s_ctx"):
    return {
        "input": text,
        "stream": False,
        "vet_context": {
            "user_id": "u_ctx",
            "session_id": session_id,
            "pet_id": "p_ctx",
        },
    }


def test_health_and_ready(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"]["orchestrator"] is True


def test_sync_turn_uses_mock_qwen_and_evidence(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.post("/agent/turns", json=_payload("我家狗今天有点拉稀，应该怎么办？"))

    assert response.status_code == 200
    data = response.json()
    assert data["request_id"]
    assert data["trace_id"]
    assert "线下兽医" in data["output_text"]
    assert data["evidence"]
    assert "SafetyAgent" in data["metadata"]["multi_agent_path"]


def test_toxic_substance_is_escalated(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.post("/agent/turns", json=_payload("狗误食了巧克力，还能观察一下吗？"))

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "safety_escalated"
    assert "立即联系线下兽医" in data["output_text"]
    assert any(signal["code"] == "TOXIC_SUBSTANCE" for signal in data["safety_signals"])


def test_emergency_red_flag_skips_followup(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.post("/agent/turns", json=_payload("猫现在呼吸困难，站不起来"))

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "safety_escalated"
    assert "不建议先在线上反复追问" in data["output_text"]
    assert any(signal["code"] == "EMERGENCY_RED_FLAG" for signal in data["safety_signals"])


def test_radiology_attachment_is_blocked(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/agent/turns",
        json=_payload(
            "帮我看看这张 X 光片",
            attachments=[
                {
                    "attachment_id": "a1",
                    "mime_type": "image/jpeg",
                    "purpose": "radiology",
                    "storage_ref": "s3://bucket/xray.jpg",
                }
            ],
        ),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "blocked"
    assert "不能做影像判读" in data["output_text"]
    assert any(signal["code"] == "RADIOLOGY_GATE" for signal in data["safety_signals"])


def test_memory_read_correct_delete(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    correction = {
        "user_id": "u1",
        "session_id": "s1",
        "pet_id": "p1",
        "summary": "主人偏好先给简短结论，再看依据。",
    }
    assert client.put("/memories", json=correction).status_code == 200

    memory = client.get("/memories?user_id=u1&session_id=s1&pet_id=p1").json()
    assert memory["pet"]["last_summary"] == correction["summary"]

    assert client.delete("/memories/pets/p1").status_code == 200
    memory_after_delete = client.get("/memories?user_id=u1&session_id=s1&pet_id=p1").json()
    assert memory_after_delete["pet"] == {}


def test_pet_fact_memory_can_be_persisted_and_read(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    fact = {
        "user_id": "u_fact",
        "session_id": "s_fact",
        "pet_id": "p_fact",
        "fact_type": "medical",
        "fact_key": "allergy",
        "fact_value": "疑似鸡肉过敏",
        "confidence": 0.9,
    }
    assert client.put("/memories/facts", json=fact).status_code == 200

    memory = client.get("/memories?user_id=u_fact&session_id=s_fact&pet_id=p_fact").json()
    facts = memory["pet"]["facts"]
    assert facts[0]["fact_value"] == "疑似鸡肉过敏"


def test_idempotency_key_reuses_first_response(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    payload = _payload(
        "我家狗今天有点拉稀，应该怎么办？",
        turn_options={"idempotency_key": "idem_same_turn"},
    )

    first = client.post("/agent/turns", json=payload)
    second = client.post("/agent/turns", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


def test_openai_compatible_response_shape(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.post("/openai/v1/responses", json=_payload("我家狗最近乱叫"))

    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "response"
    assert data["output"][0]["role"] == "assistant"
    assert data["output"][0]["content"][0]["type"] == "output_text"
    assert data["reasoning_display"]["text"]
    assert data["segments"][0]["reasoning_display"]["text"]
    assert data["vet_result"]["route"]
    assert data["metadata"]["request_id"]


def test_agent_turn_external_contract_includes_reasoning_display(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.post("/agent/turns", json=_payload_without_pet_info("它有点拉稀，怎么办？", session_id="s_reasoning"))

    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "agent.turn"
    assert data["created_at"]
    assert data["output"][0]["content"][0]["type"] == "output_text"
    assert data["reasoning_display"]["title"] == "本轮思考过程"
    assert data["reasoning_display"]["metadata"]["kind"] == "user_visible_diagnostic_evidence"
    assert data["reasoning_display"]["text"]
    assert data["segments"][0]["reasoning_display"]["projection_id"] == data["reasoning_display"]["projection_id"]
    assert data["segments"][0]["output_text"] == data["output_text"]
    assert data["vet_result"]["route"]


def test_stream_turn_emits_reasoning_display_events(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/agent/turns",
        json={**_payload_without_pet_info("它有点拉稀，怎么办？", session_id="s_stream_reasoning"), "stream": True},
    )

    assert response.status_code == 200
    body = response.text
    assert "event: reasoning_display.started" in body
    assert "event: reasoning_display.delta" in body
    assert "event: reasoning_display.completed" in body
    assert "event: segment.delta" in body


def test_multi_task_turn_splits_into_independent_segments(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/agent/turns",
        json=_payload(
            "我家狗今天拉稀，精神正常，食欲正常，没有呕吐。还有最近夜里乱叫。顺便问下能不能换粮？",
            vet_context={
                "user_id": "u_multi",
                "session_id": "s_multi",
                "pet_id": "p_multi",
                "pet_info": {
                    "species": "犬",
                    "breed": "柯基",
                    "age": "3岁",
                    "weight_kg": 12,
                },
            },
        ),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["vet_result"]["route"] == "multi_task_consultation"
    assert data["metadata"]["task_count"] == 3
    assert len(data["segments"]) == 3
    assert data["reasoning_display"]["metadata"]["kind"] == "user_visible_multi_task_routing"
    titles = [segment["title"] for segment in data["segments"]]
    assert any("消化道问题" in title for title in titles)
    assert any("行为问题" in title for title in titles)
    assert any("喂养问题" in title for title in titles)
    assert all(segment["reasoning_display"]["text"] for segment in data["segments"])


def test_llm_task_router_can_drive_task_splitting():
    class FakeQwen:
        available = True

        async def chat(self, messages, *, model=None, temperature=0.2):
            return """
            {
              "tasks": [
                {"domain": "behavior", "title": "夜里乱叫", "text": "最近夜里乱叫", "priority": 20, "reason": "行为场景"},
                {"domain": "gastrointestinal", "title": "拉稀", "text": "今天拉稀，精神正常", "priority": 10, "reason": "消化道症状"}
              ]
            }
            """

    splitter = TaskSplitterAgent(
        FileRuleRepository(Settings().seed_dir),
        FakeQwen(),
        Settings(enable_llm_task_splitter=True, qwen_api_key="test"),
    )

    import asyncio

    decision = asyncio.run(splitter.split("我家狗今天拉稀，精神正常。还有最近夜里乱叫。"))

    assert decision.strategy == "llm_task_router"
    assert [task.domain for task in decision.tasks] == ["gastrointestinal", "behavior"]
    assert decision.tasks[0].reason == "消化道症状"


def test_header_body_id_conflict_returns_invalid_request(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    payload = _payload_without_pet_info("它有点拉稀，怎么办？", session_id="s_header_conflict")
    payload["request_id"] = "req_body"

    response = client.post("/agent/turns", json=payload, headers={"X-Request-ID": "req_header"})

    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_REQUEST"
    assert data["request_id"] == "req_body"


def test_consultation_first_turn_collects_slots_without_final_advice(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.post("/agent/turns", json=_payload_without_pet_info("它有点拉稀，怎么办？"))

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "requires_followup"
    assert data["metadata"]["consultation_phase"] == "collecting_info"
    assert "我先不武断下结论" in data["output_text"]
    assert "它是猫还是狗" in data["output_text"]
    assert "请先回答" in data["output_text"]
    assert "QwenResponseAgent" not in data["metadata"]["multi_agent_path"]


def test_consultation_second_turn_completes_after_context_is_built(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    session_id = "s_ctx_2"

    first = client.post("/agent/turns", json=_payload_without_pet_info("它有点拉稀，怎么办？", session_id=session_id))
    assert first.json()["status"] == "requires_followup"

    second = client.post(
        "/agent/turns",
        json=_payload_without_pet_info(
            "是狗，3岁，12公斤，今天早上开始，精神食欲正常，没有呕吐，大便拉稀但没有血。",
            session_id=session_id,
        ),
    )

    assert second.status_code == 200
    data = second.json()
    assert data["status"] == "completed"
    assert data["metadata"]["consultation_phase"] == "ready_to_answer"
    assert data["metadata"]["missing_slots"] == []
    assert "QwenResponseAgent" in data["metadata"]["multi_agent_path"]
    assert "阶段性最终建议" not in data["output_text"]
    assert "请先回答" not in data["output_text"]
    assert "线下兽医" in data["output_text"]
def test_api_key_auth_can_be_required(tmp_path, monkeypatch):
    monkeypatch.setenv("REQUIRE_API_AUTH", "true")
    monkeypatch.setenv("VET_AGENT_API_KEYS", "secret-token")
    client = _client(tmp_path, monkeypatch)
    payload = _payload_without_pet_info("My dog has mild diarrhea.", session_id="s_auth")

    missing = client.post("/agent/turns", json=payload)
    wrong = client.post("/agent/turns", json=payload, headers={"Authorization": "Bearer wrong"})
    ok = client.post("/agent/turns", json=payload, headers={"Authorization": "Bearer secret-token"})

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert ok.status_code == 200


def test_session_policy_blocks_switching_pet_in_same_session(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    first = _payload_without_pet_info("My dog has mild diarrhea.", session_id="s_one_pet")
    second = _payload_without_pet_info("My cat has mild diarrhea.", session_id="s_one_pet")
    second["vet_context"]["pet_id"] = "another_pet"

    assert client.post("/agent/turns", json=first).status_code == 200
    response = client.post("/agent/turns", json=second)

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_memory_extraction_persists_pet_info_facts(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    payload = {
        "input": "Please remember this profile.",
        "stream": False,
        "vet_context": {
            "user_id": "u_extract",
            "session_id": "s_extract",
            "pet_id": "p_extract",
            "pet_info": {
                "species": "dog",
                "breed": "corgi",
                "age": "3 years",
                "weight_kg": 12,
            },
        },
    }

    assert client.post("/agent/turns", json=payload).status_code == 200
    memory = client.get("/memories?user_id=u_extract&session_id=s_extract&pet_id=p_extract").json()
    fact_keys = {item["fact_key"] for item in memory["pet"]["facts"]}

    assert {"species", "breed", "age", "weight_kg"}.issubset(fact_keys)


def test_safety_review_removes_dosage_expression():
    from src.vet_agent.agents.safety import SafetyAgent
    from src.vet_agent.agents.safety_review import SafetyReviewAgent

    reviewer = SafetyReviewAgent(SafetyAgent(FileRuleRepository(Settings().seed_dir)))
    result = reviewer.review_text("You can give 5 mg/kg twice daily.")

    assert "5 mg/kg" not in result.text
    assert any(signal.code == "DOSAGE_REMOVED" for signal in result.signals)

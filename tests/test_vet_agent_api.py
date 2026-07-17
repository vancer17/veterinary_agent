"""
文件：tests/test_vet_agent_api.py
作用：提供项目自动化测试用例与测试辅助函数。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from fastapi.testclient import TestClient

from ingress import create_app, set_orchestrator
from vet_agent import Settings, VetAgentIngressOrchestrator, get_container
from vet_agent.agents import TaskSplitterAgent
from vet_agent.repositories import FileRuleRepository
from vet_agent.runtime import QwenClient


app = create_app()


def _client(tmp_path, monkeypatch) -> TestClient:
    """执行 _client 内部辅助逻辑。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 返回函数执行结果。
    """
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test-litellm")
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm.test/v1")
    monkeypatch.setenv("ENABLE_MEM0", "false")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("VET_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(QwenClient, "_send_chat", _fake_litellm_send_chat)
    get_container.cache_clear()
    set_orchestrator(VetAgentIngressOrchestrator(get_container()))
    return TestClient(app)


async def _fake_litellm_send_chat(self, messages, *, model: str, temperature: float) -> str:
    """执行 _fake_litellm_send_chat 内部辅助逻辑。

    :param messages: 参数 messages。
    :param model: 模型名称。
    :param temperature: 参数 temperature。
    :return: 返回函数执行结果。
    """
    del self, model, temperature
    user_text = _message_text(messages)
    if "TaskRouterAgent" in user_text and "主餐都会清空" in user_text:
        return """
        {
          "tasks": [
            {"domain": "general", "title": "一般补充", "text": "主餐都会清空，前天第一次看到", "priority": 10, "reason": "补充问诊信息"},
            {"domain": "behavior", "title": "互动状态", "text": "叫名字会抬头，结束后会自己拿玩具过来", "priority": 20, "reason": "互动和行为信息"},
            {"domain": "gastrointestinal", "title": "腹部反应", "text": "轻碰腹部会把身体绷紧", "priority": 30, "reason": "腹部相关线索"}
          ]
        }
        """
    if "RagQuestionPlannerAgent" in user_text and "缩成一团" in user_text:
        return """
        {
          "questions": [
            {
              "slot": "mental_status",
              "question": "它缩成一团时，腹部有没有明显紧绷，或被抱起、轻碰肚子时躲开？",
              "reason": "知识库提示姿势改变要优先区分疼痛、腹部不适和普通休息状态。",
              "evidence_titles": ["消化道症状"],
              "priority": 10
            },
            {
              "slot": "onset",
              "question": "这种缩着趴通常是在饭后多久出现，每次会持续多长时间？",
              "reason": "发生时间和进食关系能帮助判断是否更偏向短暂胃肠不适。",
              "evidence_titles": ["消化道症状"],
              "priority": 20
            }
          ]
        }
        """
    if "image_url" in user_text:
        return """
        {
          "summary": "Parsed visible lab items from the OSS image.",
          "ocr_text": "ALT 126 U/L 10-100 H\\nWBC 18.5 10^9/L 6-17 H\\nHGB 145 g/L 120-180",
          "items": [
            {"item_name": "ALT", "value_text": "126", "numeric_value": 126, "unit": "U/L", "reference_range": "10-100", "abnormal_flag": "high", "confidence": 0.86},
            {"item_name": "WBC", "value_text": "18.5", "numeric_value": 18.5, "unit": "10^9/L", "reference_range": "6-17", "abnormal_flag": "high", "confidence": 0.86},
            {"item_name": "HGB", "value_text": "145", "numeric_value": 145, "unit": "g/L", "reference_range": "120-180", "abnormal_flag": null, "confidence": 0.82}
          ]
        }
        """
    if "结构化问诊状态已足够" in user_text:
        return (
            "分诊/紧急度: 目前根据已补充的信息，暂未看到必须立即急诊的红旗，但仍需要继续观察变化。\n"
            "可能方向与依据: 更偏向轻度、短时的消化道不适或饮食刺激。\n"
            "现在可以做什么: 先保证饮水，暂停零食和新食物，少量多餐，观察精神、食欲、呕吐、腹泻次数和是否出现血便。不要自行喂人药。\n"
            "线下兽医兜底: 如果症状加重、持续超过 24 小时、出现血便/频繁呕吐/精神明显变差，请尽快线下就诊。"
        )
    if "行为" in user_text or "乱叫" in user_text or "拆家" in user_text:
        return "这更像行为和环境管理问题，但仍要先排除突然疼痛、食欲下降或神经异常等医疗红旗。"
    if "喂" in user_text or "吃" in user_text or "粮" in user_text:
        return "饲养建议应结合物种、年龄、体重、体况和活动量，并避免突然换粮。"
    return "我会先做分诊:目前还需要确认症状开始时间、精神食欲、是否呕吐腹泻或咳喘。如果加重或出现红旗症状，请尽快就医。"


def _message_text(messages) -> str:
    """执行 _message_text 内部辅助逻辑。

    :param messages: 参数 messages。
    :return: 返回函数执行结果。
    """
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item))
    return "\n".join(parts)


def _payload(text: str, **extra):
    """执行 _payload 内部辅助逻辑。

    :param text: 待处理文本。
    :param extra: 参数 extra。
    :return: 返回函数执行结果。
    """
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
    """执行 _payload_without_pet_info 内部辅助逻辑。

    :param text: 待处理文本。
    :param session_id: 参数 session_id。
    :return: 返回函数执行结果。
    """
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
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"]["orchestrator"] is True


def test_sync_turn_uses_litellm_gateway_and_evidence(tmp_path, monkeypatch):
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
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
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post("/agent/turns", json=_payload("狗误食了巧克力，还能观察一下吗？"))

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "safety_escalated"
    assert "立即联系线下兽医" in data["output_text"]
    assert any(signal["code"] == "TOXIC_SUBSTANCE" for signal in data["safety_signals"])


def test_emergency_red_flag_skips_followup(tmp_path, monkeypatch):
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post("/agent/turns", json=_payload("猫现在呼吸困难，站不起来"))

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "safety_escalated"
    assert "不建议先在线上反复追问" in data["output_text"]
    assert any(signal["code"] == "EMERGENCY_RED_FLAG" for signal in data["safety_signals"])


def test_radiology_attachment_is_blocked(tmp_path, monkeypatch):
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
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
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
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
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
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
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
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
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
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
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
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
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
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
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
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
    """验证对应业务场景是否符合预期。

    :return: 无返回值；断言通过表示场景符合预期。
    """
    class FakeQwen:
        available = True

        async def chat(self, messages, *, model=None, temperature=0.2):
            """执行 chat 业务逻辑。

            :param messages: 参数 messages。
            :param model: 模型名称。
            :param temperature: 参数 temperature。
            :return: 返回异步执行结果。
            """
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
        Settings(enable_llm_task_splitter=True, litellm_api_key="test"),
    )

    import asyncio

    decision = asyncio.run(splitter.split("我家狗今天拉稀，精神正常。还有最近夜里乱叫。"))

    assert decision.strategy == "llm_task_router"
    assert [task.domain for task in decision.tasks] == ["gastrointestinal", "behavior"]
    assert decision.tasks[0].reason == "消化道症状"


def test_header_body_id_conflict_returns_invalid_request(tmp_path, monkeypatch):
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)
    payload = _payload_without_pet_info("它有点拉稀，怎么办？", session_id="s_header_conflict")
    payload["request_id"] = "req_body"

    response = client.post("/agent/turns", json=payload, headers={"X-Request-ID": "req_header"})

    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_REQUEST"
    assert data["request_id"] == "req_body"


def test_consultation_first_turn_collects_slots_without_final_advice(tmp_path, monkeypatch):
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
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


def test_rag_guided_followup_uses_knowledge_to_plan_questions(tmp_path, monkeypatch):
    """验证知识库命中结果可反推动态追问。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/agent/turns",
        json=_payload(
            "饭后总是缩成一团趴着，看起来不太舒服。",
            vet_context={
                "user_id": "u_rag_followup",
                "session_id": "s_rag_followup",
                "pet_id": "p_rag_followup",
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
    assert data["status"] == "requires_followup"
    assert data["vet_result"]["route"] == "rag_guided_followup"
    assert "RagQuestionPlannerAgent" in data["metadata"]["multi_agent_path"]
    assert "QwenResponseAgent" not in data["metadata"]["multi_agent_path"]
    assert "腹部有没有明显紧绷" in data["output_text"]
    assert "饭后多久出现" in data["output_text"]
    assert "为什么先问这些" in data["output_text"]
    plan = data["metadata"]["followup_question_plan"]
    assert plan["strategy"] == "rag_llm_question_planner"
    assert plan["questions"][0]["evidence_titles"] == ["消化道症状"]
    assert data["evidence"]


def test_unfinished_consultation_state_skips_task_splitting(tmp_path, monkeypatch):
    """验证未完成问诊状态会优先吸收下一轮回答。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)
    session_id = "s_skip_task_router"
    vet_context = {
        "user_id": "u_skip_task_router",
        "session_id": session_id,
        "pet_id": "p_skip_task_router",
        "pet_info": {
            "species": "犬",
            "breed": "柯基",
            "age": "3岁",
            "weight_kg": 12,
        },
    }

    first = client.post(
        "/agent/turns",
        json=_payload(
            "饭后总是缩成一团趴着，看起来不太舒服。",
            vet_context=vet_context,
        ),
    )
    assert first.status_code == 200
    assert first.json()["status"] == "requires_followup"

    second = client.post(
        "/agent/turns",
        json=_payload(
            "主餐都会清空，平时喜欢的小块奖励也主动来拿，叫名字会抬头并且结束后会自己拿玩具过来，前天第一次看到，轻碰腹部会把身体绷紧但不会躲开。",
            vet_context=vet_context,
        ),
    )

    assert second.status_code == 200
    data = second.json()
    assert data["vet_result"]["route"] == "rag_guided_followup"
    assert data["metadata"]["task_router_skipped"] is True
    assert data["metadata"]["task_router_strategy"] == "skipped_unfinished_consultation_state"
    assert "TaskRouterAgent" not in data["metadata"]["multi_agent_path"]
    assert "任务 1" not in data["output_text"]


def test_consultation_second_turn_completes_after_context_is_built(tmp_path, monkeypatch):
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
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
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
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
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)
    first = _payload_without_pet_info("My dog has mild diarrhea.", session_id="s_one_pet")
    second = _payload_without_pet_info("My cat has mild diarrhea.", session_id="s_one_pet")
    second["vet_context"]["pet_id"] = "another_pet"

    assert client.post("/agent/turns", json=first).status_code == 200
    response = client.post("/agent/turns", json=second)

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_memory_extraction_persists_pet_info_facts(tmp_path, monkeypatch):
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
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
    """验证对应业务场景是否符合预期。

    :return: 无返回值；断言通过表示场景符合预期。
    """
    from vet_agent.agents import SafetyAgent, SafetyReviewAgent

    reviewer = SafetyReviewAgent(SafetyAgent(FileRuleRepository(Settings().seed_dir)))
    result = reviewer.review_text("You can give 5 mg/kg twice daily.")

    assert "5 mg/kg" not in result.text
    assert any(signal.code == "DOSAGE_REMOVED" for signal in result.signals)


def test_report_parse_extracts_structured_lab_items(tmp_path, monkeypatch):
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/reports/parse",
        json={
            "user_id": "u_report",
            "session_id": "s_report",
            "pet_id": "p_report",
            "report_type": "bloodwork",
            "oss_image_url": "https://infra-dev-file-storage.oss-cn-hangzhou-internal.aliyuncs.com/uploads/reports/lab.jpg",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "parsed"
    assert data["source_type"] == "oss_image_url"
    assert data["report_id"].startswith("rpt_")
    assert len(data["items"]) >= 3
    assert data["attachments"][0]["storage_ref"] == "oss://infra-dev-file-storage/uploads/reports/lab.jpg"
    assert any(item["item_name"] == "ALT" and item["abnormal_flag"] == "high" for item in data["items"])


def test_radiology_report_is_blocked_from_online_interpretation(tmp_path, monkeypatch):
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/reports/parse",
        json={
            "user_id": "u_xray_report",
            "session_id": "s_xray_report",
            "pet_id": "p_xray_report",
            "report_type": "xray",
            "oss_image_url": "oss://infra-dev-file-storage/uploads/reports/xray.jpg",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "blocked"
    assert data["items"] == []
    assert data["safety_flags"][0]["code"] == "RADIOLOGY_REPORT_GATE"


def test_report_parse_rejects_non_oss_image_url(tmp_path, monkeypatch):
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/reports/parse",
        json={
            "user_id": "u_bad_report",
            "session_id": "s_bad_report",
            "pet_id": "p_bad_report",
            "report_type": "bloodwork",
            "oss_image_url": "https://example.com/lab.jpg",
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"


def test_rag_governance_admin_can_list_and_update_seed_chunks(tmp_path, monkeypatch):
    """验证对应业务场景是否符合预期。

    :param tmp_path: 参数 tmp_path。
    :param monkeypatch: 参数 monkeypatch。
    :return: 无返回值；断言通过表示场景符合预期。
    """
    client = _client(tmp_path, monkeypatch)

    stats = client.get("/admin/rag/stats")
    chunks = client.get("/admin/rag/chunks?limit=1")
    update = client.patch(
        "/admin/rag/chunks/1",
        json={"review_status": "rejected", "enabled": False, "reason": "test quarantine"},
    )

    assert stats.status_code == 200
    assert stats.json()["total"] >= 1
    assert chunks.status_code == 200
    assert chunks.json()["items"]
    assert update.status_code == 200
    assert update.json()["review_status"] == "rejected"
    assert update.json()["enabled"] is False

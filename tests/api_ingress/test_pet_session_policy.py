##################################################################################################
# 文件: tests/api_ingress/test_pet_session_policy.py
# 作用: 验证 ApiIngress 在进入编排并发闸门前执行 PetSessionPolicy，并正确映射允许与阻断结果。
# 边界: 使用 ConversationStore 测试替身与编排 TODO 响应；不接入真实数据库、GraphRuntime 或 LogicTraceStore。
##################################################################################################

from datetime import UTC, datetime
from typing import cast

from fastapi.testclient import TestClient

from veterinary_agent import (
    AppendMessageCommandDto,
    AppendMessageResultDto,
    ConversationErrorCode,
    ConversationMessageDto,
    ConversationMessageStatus,
    ConversationOperation,
    ConversationSessionDto,
    ConversationSessionStatus,
    ConversationStore,
    ConversationStoreError,
    ConversationStoreSettings,
    EnsureSessionCommandDto,
    EnsureSessionResultDto,
    TodoConversationStore,
    create_app,
)


class _StatefulConversationStore(TodoConversationStore):
    """维护 session 锚点的 ApiIngress 测试 ConversationStore。"""

    def __init__(self) -> None:
        """初始化测试 ConversationStore。

        :return: None。
        """

        self.sessions: dict[str, ConversationSessionDto] = {}
        self.ensure_calls: list[EnsureSessionCommandDto] = []
        self.append_calls: list[AppendMessageCommandDto] = []
        self.messages_by_idempotency_key: dict[str, ConversationMessageDto] = {}

    async def ensure_session(
        self,
        command: EnsureSessionCommandDto,
    ) -> EnsureSessionResultDto:
        """创建或确认测试 session 锚点。

        :param command: PetSessionPolicy 传入的 EnsureSession 命令。
        :return: 创建或确认后的 session 结果。
        :raises ConversationStoreError: 当请求 user_id 或 pet_id 与既有锚点冲突时抛出。
        """

        self.ensure_calls.append(command)
        existing = self.sessions.get(command.session_id)
        if existing is not None:
            if existing.user_id != command.user_id:
                raise ConversationStoreError(
                    code=ConversationErrorCode.SESSION_USER_CONFLICT,
                    operation=ConversationOperation.ENSURE_SESSION,
                    message="session user conflict",
                    request_id=command.request_id,
                    trace_id=command.trace_id,
                )
            if existing.pet_id != command.pet_id:
                raise ConversationStoreError(
                    code=ConversationErrorCode.SESSION_PET_CONFLICT,
                    operation=ConversationOperation.ENSURE_SESSION,
                    message="session pet conflict",
                    request_id=command.request_id,
                    trace_id=command.trace_id,
                )
            return EnsureSessionResultDto(
                session=existing,
                created_new=False,
            )

        now = datetime.now(UTC)
        session = ConversationSessionDto(
            session_id=command.session_id,
            user_id=command.user_id,
            pet_id=command.pet_id,
            status=ConversationSessionStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            next_sequence_no=1,
        )
        self.sessions[command.session_id] = session
        return EnsureSessionResultDto(
            session=session,
            created_new=True,
        )

    async def append_message(
        self,
        command: AppendMessageCommandDto,
    ) -> AppendMessageResultDto:
        """幂等追加测试用户消息。

        :param command: AgentApplicationService 传入的追加消息命令。
        :return: 已写入或幂等命中的测试消息结果。
        """

        self.append_calls.append(command)
        if command.idempotency_key is not None:
            existing_message = self.messages_by_idempotency_key.get(
                command.idempotency_key
            )
            if existing_message is not None:
                return AppendMessageResultDto(
                    message=existing_message,
                    idempotent=True,
                )
        now = datetime.now(UTC)
        message = ConversationMessageDto(
            message_id=f"msg_{len(self.append_calls)}",
            session_id=command.session_id,
            user_id=command.user_id,
            pet_id=command.pet_id,
            role=command.role,
            content_type=command.content_type,
            content=command.content,
            sequence_no=len(self.append_calls),
            status=ConversationMessageStatus.FINALIZED,
            idempotency_key=command.idempotency_key,
            metadata=dict(command.metadata),
            created_at=now,
            finalized_at=now,
        )
        if command.idempotency_key is not None:
            self.messages_by_idempotency_key[command.idempotency_key] = message
        return AppendMessageResultDto(message=message, idempotent=False)


class _ConversationStoreFactory:
    """向 FastAPI lifespan 返回固定测试 store 的工厂。"""

    def __init__(self, store: ConversationStore) -> None:
        """初始化测试 ConversationStore 工厂。

        :param store: 需要注入 FastAPI lifespan 的 ConversationStore。
        :return: None。
        """

        self.store = store

    def __call__(
        self,
        settings: ConversationStoreSettings,
    ) -> ConversationStore:
        """返回固定测试 ConversationStore。

        :param settings: ConversationStore RuntimeConfig；测试工厂不读取具体字段。
        :return: 测试用 ConversationStore。
        """

        del settings
        return self.store


def _payload(
    *,
    request_id: str,
    pet_id: str = "pet_1",
) -> dict[str, object]:
    """构建可抵达 PetSessionPolicy 的合法入口请求。

    :param request_id: 当前测试请求 ID。
    :param pet_id: 当前请求显式携带的宠物 ID。
    :return: 合法一轮对话请求体。
    """

    return {
        "request_id": request_id,
        "trace_id": f"trace_{request_id}",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "小狗今天精神一般，需要观察什么？",
                    }
                ],
            }
        ],
        "vet_context": {
            "user_id": "user_1",
            "session_id": "session_1",
            "pet_id": pet_id,
        },
    }


def _response_body(response_json: object) -> dict[str, object]:
    """将响应 JSON 约束为字典。

    :param response_json: HTTP 响应解析后的 JSON 对象。
    :return: 字典形式的响应体。
    """

    assert isinstance(response_json, dict)
    return cast(dict[str, object], response_json)


def _detail_reasons(body: dict[str, object]) -> set[str]:
    """提取统一错误响应中的明细原因集合。

    :param body: 统一错误响应体。
    :return: details 数组中的 reason 字段集合。
    """

    details = body.get("details")
    assert isinstance(details, list)
    reasons: set[str] = set()
    for detail in details:
        assert isinstance(detail, dict)
        reason = detail.get("reason")
        if isinstance(reason, str):
            reasons.add(reason)
    return reasons


def test_router_runs_pet_session_policy_before_orchestrator() -> None:
    """验证会话策略允许后请求才抵达编排 TODO 依赖。

    :return: None。
    """

    store = _StatefulConversationStore()
    app = create_app(
        conversation_store_factory=_ConversationStoreFactory(store),
    )

    with TestClient(app) as client:
        response = client.post(
            "/agent/turns",
            json=_payload(request_id="req_allow"),
        )
    body = _response_body(response.json())

    assert response.status_code == 503
    assert body["message"] == "service unavailable"
    assert len(store.ensure_calls) == 1
    assert store.sessions["session_1"].pet_id == "pet_1"


def test_router_maps_session_pet_mismatch_to_conflict() -> None:
    """验证同一 session 切宠被 PetSessionPolicy 阻断并映射为 HTTP 409。

    :return: None。
    """

    store = _StatefulConversationStore()
    app = create_app(
        conversation_store_factory=_ConversationStoreFactory(store),
    )

    with TestClient(app) as client:
        first_response = client.post(
            "/agent/turns",
            json=_payload(request_id="req_first"),
        )
        mismatch_response = client.post(
            "/agent/turns",
            json=_payload(request_id="req_mismatch", pet_id="pet_2"),
        )
    body = _response_body(mismatch_response.json())

    assert first_response.status_code == 503
    assert mismatch_response.status_code == 409
    assert body["code"] == "INVALID_REQUEST"
    assert body["message"] == "session is bound to another pet; create a new session"
    assert "PET_SESSION_PET_MISMATCH" in _detail_reasons(body)
    assert "BLOCK_SESSION_PET_MISMATCH" in _detail_reasons(body)


def test_router_fails_closed_when_default_conversation_store_is_todo() -> None:
    """验证默认 ConversationStore TODO 空壳使 PetSessionPolicy fail-closed。

    :return: None。
    """

    with TestClient(create_app()) as client:
        response = client.post(
            "/agent/turns",
            json=_payload(request_id="req_store_unavailable"),
        )
    body = _response_body(response.json())

    assert response.status_code == 503
    assert body["code"] == "SERVICE_UNAVAILABLE"
    assert body["message"] == "pet session policy is unavailable"

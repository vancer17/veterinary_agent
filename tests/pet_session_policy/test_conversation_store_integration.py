##################################################################################################
# 文件: tests/pet_session_policy/test_conversation_store_integration.py
# 作用: 验证 PetSessionPolicy 与真实 SQLAlchemy ConversationStore 公开契约的组件级联动。
# 边界: 使用临时 SQLite 与 ConversationStore 公共工厂；不接入 FastAPI、CheckpointStore、LogicTraceStore 或业务图。
##################################################################################################

import asyncio

import pytest

from veterinary_agent.config import RuntimeConfigProvider
from veterinary_agent.conversation_store import (
    ArchiveSessionCommandDto,
    CloseSessionCommandDto,
    ConversationStore,
    GetSessionQueryDto,
)
from veterinary_agent.pet_session_policy import (
    PetSessionDecision,
    PetSessionPolicyError,
    PetSessionPolicyErrorCode,
    PetSessionTraceWriteStatus,
)

from .helpers import build_policy, build_request


def test_real_store_new_and_existing_session_flow(
    conversation_store: ConversationStore,
    runtime_config_provider: RuntimeConfigProvider,
) -> None:
    """验证真实 ConversationStore 支持新建、同宠重试和绑定事实读取。

    :param conversation_store: 测试用真实 ConversationStore。
    :param runtime_config_provider: 测试用 RuntimeConfig provider。
    :return: None。
    """

    policy = build_policy(
        store=conversation_store,
        runtime_config_provider=runtime_config_provider,
    )

    first = asyncio.run(policy.ensure_context(build_request(request_id="req_first")))
    second = asyncio.run(policy.ensure_context(build_request(request_id="req_second")))
    persisted = asyncio.run(
        conversation_store.get_session(
            GetSessionQueryDto(
                request_id="req_read",
                trace_id="trace_read",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
            )
        )
    )

    assert first.decision is PetSessionDecision.ALLOW_NEW_SESSION_BOUND
    assert second.decision is PetSessionDecision.ALLOW_EXISTING_SESSION
    assert first.current_pet_id == second.current_pet_id == persisted.pet_id == "pet_1"
    assert first.trace_delivery_status is PetSessionTraceWriteStatus.DEGRADED


def test_real_store_rejects_session_pet_mismatch(
    conversation_store: ConversationStore,
    runtime_config_provider: RuntimeConfigProvider,
) -> None:
    """验证真实 ConversationStore 的宠物锚点冲突映射为切宠阻断。

    :param conversation_store: 测试用真实 ConversationStore。
    :param runtime_config_provider: 测试用 RuntimeConfig provider。
    :return: None。
    """

    policy = build_policy(
        store=conversation_store,
        runtime_config_provider=runtime_config_provider,
    )
    asyncio.run(policy.ensure_context(build_request(request_id="req_first")))

    with pytest.raises(PetSessionPolicyError) as exc_info:
        asyncio.run(
            policy.ensure_context(
                build_request(
                    request_id="req_mismatch",
                    pet_id="pet_2",
                )
            )
        )

    assert exc_info.value.code is PetSessionPolicyErrorCode.PET_MISMATCH


def test_real_store_rejects_session_user_mismatch(
    conversation_store: ConversationStore,
    runtime_config_provider: RuntimeConfigProvider,
) -> None:
    """验证真实 ConversationStore 的用户锚点冲突映射为用户阻断。

    :param conversation_store: 测试用真实 ConversationStore。
    :param runtime_config_provider: 测试用 RuntimeConfig provider。
    :return: None。
    """

    policy = build_policy(
        store=conversation_store,
        runtime_config_provider=runtime_config_provider,
    )
    asyncio.run(policy.ensure_context(build_request(request_id="req_first")))

    with pytest.raises(PetSessionPolicyError) as exc_info:
        asyncio.run(
            policy.ensure_context(
                build_request(
                    request_id="req_user_mismatch",
                    user_id="user_2",
                )
            )
        )

    assert exc_info.value.code is PetSessionPolicyErrorCode.USER_MISMATCH


def test_real_store_closed_session_is_blocked(
    conversation_store: ConversationStore,
    runtime_config_provider: RuntimeConfigProvider,
) -> None:
    """验证真实 ConversationStore 返回 closed session 时策略层阻断。

    :param conversation_store: 测试用真实 ConversationStore。
    :param runtime_config_provider: 测试用 RuntimeConfig provider。
    :return: None。
    """

    policy = build_policy(
        store=conversation_store,
        runtime_config_provider=runtime_config_provider,
    )
    asyncio.run(policy.ensure_context(build_request(request_id="req_first")))
    asyncio.run(
        conversation_store.close_session(
            CloseSessionCommandDto(
                request_id="req_close",
                trace_id="trace_close",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
            )
        )
    )

    with pytest.raises(PetSessionPolicyError) as exc_info:
        asyncio.run(policy.ensure_context(build_request(request_id="req_after_close")))

    assert exc_info.value.code is PetSessionPolicyErrorCode.SESSION_CLOSED


def test_real_store_archived_session_is_blocked(
    conversation_store: ConversationStore,
    runtime_config_provider: RuntimeConfigProvider,
) -> None:
    """验证真实 ConversationStore 返回 archived session 时策略层阻断。

    :param conversation_store: 测试用真实 ConversationStore。
    :param runtime_config_provider: 测试用 RuntimeConfig provider。
    :return: None。
    """

    policy = build_policy(
        store=conversation_store,
        runtime_config_provider=runtime_config_provider,
    )
    asyncio.run(policy.ensure_context(build_request(request_id="req_first")))
    asyncio.run(
        conversation_store.archive_session(
            ArchiveSessionCommandDto(
                request_id="req_archive",
                trace_id="trace_archive",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
            )
        )
    )

    with pytest.raises(PetSessionPolicyError) as exc_info:
        asyncio.run(
            policy.ensure_context(build_request(request_id="req_after_archive"))
        )

    assert exc_info.value.code is PetSessionPolicyErrorCode.SESSION_ARCHIVED

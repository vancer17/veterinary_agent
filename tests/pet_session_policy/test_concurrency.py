##################################################################################################
# 文件: tests/pet_session_policy/test_concurrency.py
# 作用: 验证 PetSessionPolicy 与真实 ConversationStore 在新 session 并发初始化时的一宠绑定语义。
# 边界: 使用临时 SQLite 验证组件级原子契约；生产 PostgreSQL 并发能力仍应由部署级集成测试覆盖。
##################################################################################################

import asyncio

from veterinary_agent.config import RuntimeConfigProvider
from veterinary_agent.conversation_store import ConversationStore
from veterinary_agent.pet_session_policy import (
    DefaultPetSessionPolicy,
    PetSessionContextDto,
    PetSessionDecision,
    PetSessionPolicyError,
    PetSessionPolicyErrorCode,
    PetSessionRequestContextDto,
)

from .helpers import build_policy, build_request


async def _collect_policy_outcomes(
    *,
    policy: DefaultPetSessionPolicy,
    requests: list[PetSessionRequestContextDto],
) -> list[PetSessionContextDto | PetSessionPolicyError]:
    """并发执行策略请求并收集成功上下文或领域错误。

    :param policy: 待测试的 PetSessionPolicy 默认实现。
    :param requests: 需要并发执行的策略请求列表。
    :return: 与输入顺序一致的成功上下文或领域错误列表。
    """

    tasks: list[asyncio.Task[PetSessionContextDto]] = [
        asyncio.create_task(policy.ensure_context(request_context))
        for request_context in requests
    ]
    outcomes: list[PetSessionContextDto | PetSessionPolicyError] = []
    for task in tasks:
        try:
            outcomes.append(await task)
        except PetSessionPolicyError as exc:
            outcomes.append(exc)
    return outcomes


def test_same_pet_concurrent_initialization_is_idempotent(
    conversation_store: ConversationStore,
    runtime_config_provider: RuntimeConfigProvider,
) -> None:
    """验证相同宠物并发初始化仅创建一次 session 绑定。

    :param conversation_store: 测试用真实 ConversationStore。
    :param runtime_config_provider: 测试用 RuntimeConfig provider。
    :return: None。
    """

    policy = build_policy(
        store=conversation_store,
        runtime_config_provider=runtime_config_provider,
    )
    requests = [build_request(request_id=f"req_same_{index}") for index in range(4)]

    outcomes = asyncio.run(
        _collect_policy_outcomes(
            policy=policy,
            requests=requests,
        )
    )

    assert all(isinstance(outcome, PetSessionContextDto) for outcome in outcomes)
    decisions = [
        outcome.decision
        for outcome in outcomes
        if isinstance(outcome, PetSessionContextDto)
    ]
    assert decisions.count(PetSessionDecision.ALLOW_NEW_SESSION_BOUND) == 1
    assert decisions.count(PetSessionDecision.ALLOW_EXISTING_SESSION) == 3


def test_different_pet_concurrent_initialization_has_single_winner(
    conversation_store: ConversationStore,
    runtime_config_provider: RuntimeConfigProvider,
) -> None:
    """验证不同宠物并发初始化只能形成一个最终宠物绑定。

    :param conversation_store: 测试用真实 ConversationStore。
    :param runtime_config_provider: 测试用 RuntimeConfig provider。
    :return: None。
    """

    policy = build_policy(
        store=conversation_store,
        runtime_config_provider=runtime_config_provider,
    )
    requests = [
        build_request(request_id="req_pet_1", pet_id="pet_1"),
        build_request(request_id="req_pet_2", pet_id="pet_2"),
    ]

    outcomes = asyncio.run(
        _collect_policy_outcomes(
            policy=policy,
            requests=requests,
        )
    )
    successes = [
        outcome for outcome in outcomes if isinstance(outcome, PetSessionContextDto)
    ]
    errors = [
        outcome for outcome in outcomes if isinstance(outcome, PetSessionPolicyError)
    ]

    assert len(successes) == 1
    assert successes[0].decision is PetSessionDecision.ALLOW_NEW_SESSION_BOUND
    assert len(errors) == 1
    assert errors[0].code is PetSessionPolicyErrorCode.PET_MISMATCH

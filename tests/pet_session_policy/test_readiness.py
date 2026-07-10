##################################################################################################
# 文件: tests/pet_session_policy/test_readiness.py
# 作用: 验证 PetSessionPolicy readiness 对 RuntimeConfig 可用性与安全锁状态的判定。
# 边界: 使用测试 RuntimeConfig provider 与 ConversationStore TODO 空壳；不执行真实会话绑定。
##################################################################################################

from veterinary_agent.config import create_runtime_config_provider

from .helpers import (
    FakeConversationStore,
    UnavailableRuntimeConfigProvider,
    build_disabled_runtime_config_provider,
    build_policy,
)


def test_policy_is_ready_with_valid_runtime_config() -> None:
    """验证有效 RuntimeConfig 且安全锁开启时策略服务就绪。

    :return: None。
    """

    policy = build_policy(
        store=FakeConversationStore(),
        runtime_config_provider=create_runtime_config_provider(),
    )

    assert policy.is_ready() is True


def test_policy_is_not_ready_without_runtime_config_provider_readiness() -> None:
    """验证 RuntimeConfig provider 未就绪时策略服务短路返回未就绪。

    :return: None。
    """

    provider = UnavailableRuntimeConfigProvider(ready=False)
    policy = build_policy(
        store=FakeConversationStore(),
        runtime_config_provider=provider,
    )

    assert policy.is_ready() is False
    assert provider.snapshot_calls == 0


def test_policy_is_not_ready_when_runtime_snapshot_cannot_be_read() -> None:
    """验证 RuntimeConfig 快照读取失败时策略服务返回未就绪。

    :return: None。
    """

    provider = UnavailableRuntimeConfigProvider(ready=True)
    policy = build_policy(
        store=FakeConversationStore(),
        runtime_config_provider=provider,
    )

    assert policy.is_ready() is False
    assert provider.snapshot_calls == 1


def test_policy_is_not_ready_when_safety_lock_is_disabled() -> None:
    """验证宠物会话安全锁关闭时策略服务返回未就绪。

    :return: None。
    """

    policy = build_policy(
        store=FakeConversationStore(),
        runtime_config_provider=build_disabled_runtime_config_provider(),
    )

    assert policy.is_ready() is False

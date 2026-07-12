##################################################################################################
# 文件: tests/llm_gateway/test_concurrency.py
# 作用: 验证 LlmGateway 实例、profile 与路由三级并发租约的获取、超时、释放与错误语义。
# 边界: 仅测试进程内并发控制器；不访问模型代理、不测试入口限流、RPM/TPM 或分布式配额。
##################################################################################################

import asyncio

import pytest

from veterinary_agent.llm_gateway import (
    LlmConcurrencyController,
    LlmGatewayError,
    LlmGatewayErrorCode,
    LlmGatewayOperation,
)

from . import build_test_settings


def test_concurrency_controller_times_out_when_capacity_is_exhausted() -> None:
    """验证三级并发额度耗尽时返回稳定并发限制错误。

    :return: None。
    """

    async def run_scenario() -> None:
        """持有唯一租约并验证第二次获取超时。

        :return: None。
        """

        controller = LlmConcurrencyController(
            settings=build_test_settings(
                global_max_concurrency=1,
                profile_max_concurrency=1,
                route_max_concurrency=1,
                concurrency_acquire_timeout_seconds=0.01,
            )
        )
        lease = await controller.acquire(
            operation=LlmGatewayOperation.INVOKE_LLM,
            call_id="llm_concurrency_1",
            request_id="req_concurrency",
            trace_id="trace_concurrency",
            model_profile_id="profile_primary",
            provider_route_id="route_primary",
        )
        try:
            with pytest.raises(LlmGatewayError) as exc_info:
                await controller.acquire(
                    operation=LlmGatewayOperation.INVOKE_LLM,
                    call_id="llm_concurrency_2",
                    request_id="req_concurrency",
                    trace_id="trace_concurrency",
                    model_profile_id="profile_primary",
                    provider_route_id="route_primary",
                )
            assert exc_info.value.code is LlmGatewayErrorCode.LLM_CONCURRENCY_LIMITED
        finally:
            lease.release()

    asyncio.run(run_scenario())


def test_concurrency_lease_release_is_idempotent_and_reusable() -> None:
    """验证租约重复释放安全，释放后额度可以再次获取。

    :return: None。
    """

    async def run_scenario() -> None:
        """获取、重复释放并重新获取同一组并发额度。

        :return: None。
        """

        controller = LlmConcurrencyController(
            settings=build_test_settings(
                global_max_concurrency=1,
                profile_max_concurrency=1,
                route_max_concurrency=1,
            )
        )
        first = await controller.acquire(
            operation=LlmGatewayOperation.INVOKE_LLM,
            call_id="llm_release_1",
            request_id="req_release",
            trace_id="trace_release",
            model_profile_id="profile_primary",
            provider_route_id="route_primary",
        )
        first.release()
        first.release()
        second = await controller.acquire(
            operation=LlmGatewayOperation.INVOKE_LLM,
            call_id="llm_release_2",
            request_id="req_release",
            trace_id="trace_release",
            model_profile_id="profile_primary",
            provider_route_id="route_primary",
        )
        second.release()

        assert first.released is True
        assert second.released is True

    asyncio.run(run_scenario())


def test_concurrency_controller_rejects_unknown_profile_or_route() -> None:
    """验证并发控制器对未知 profile 或路由返回稳定不可用错误。

    :return: None。
    """

    async def run_scenario() -> None:
        """使用未知 profile 获取额度并断言错误上下文。

        :return: None。
        """

        controller = LlmConcurrencyController(settings=build_test_settings())

        with pytest.raises(LlmGatewayError) as exc_info:
            await controller.acquire(
                operation=LlmGatewayOperation.STREAM_LLM,
                call_id="llm_unknown",
                request_id="req_unknown",
                trace_id="trace_unknown",
                model_profile_id="profile_missing",
                provider_route_id="route_primary",
            )

        assert exc_info.value.code is LlmGatewayErrorCode.LLM_PROFILE_UNAVAILABLE
        assert exc_info.value.to_dto().model_profile_id == "profile_missing"

    asyncio.run(run_scenario())

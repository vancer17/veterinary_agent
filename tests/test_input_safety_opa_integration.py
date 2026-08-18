"""
文件：tests/test_input_safety_opa_integration.py
作用：通过真实 OPA Server 验证输入安全策略客户端与 Rego 策略的 HTTP 集成。
范围：覆盖 OPA Data API、allow、observe、escalate、block、多候选裁决和异常 Fail Fast。
说明：测试默认跳过本地 OPA Server，优先通过 INPUT_SAFETY_TEST_OPA_BASE_URL
      指向已部署的 OPA 或 Nginx 前缀服务；需要本地 CLI 临时服务时显式开启 RUN_LOCAL_OPA_SMOKE。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from vet_agent import SafetySignal
from vet_agent.input_safety import (
    InputSafetyCandidate,
    InputSafetyCandidateCategory,
    InputSafetyCandidateSource,
    InputSafetyDecisionAction,
    InputSafetyRequestContext,
    OpaInputSafetyPolicyClient,
)


@pytest.mark.integration
def test_opa_policy_cli_gate_is_available_when_enabled() -> None:
    """验证当前测试环境已安装 OPA CLI。

    :return: 无返回值；断言通过表示真实 OPA 策略测试具备执行条件。
    """
    if os.getenv("RUN_LOCAL_OPA_SMOKE", "").strip().lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("未开启 RUN_LOCAL_OPA_SMOKE，跳过本地 OPA CLI 可用性检查。")
    opa_command = os.getenv("OPA_BIN", "opa")
    assert shutil.which(opa_command), f"缺少 OPA CLI: {opa_command}"


@pytest.fixture(scope="module")
def local_opa_base_url() -> Iterator[str]:
    """启动临时真实 OPA Server 并返回其 HTTP 基地址。

    :return: 返回临时 OPA Server 基地址。
    :raises AssertionError: OPA CLI 不可用或服务无法就绪时抛出。
    """
    if os.getenv("RUN_LOCAL_OPA_SMOKE", "").strip().lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("未开启 RUN_LOCAL_OPA_SMOKE，跳过本地 OPA Server 集成测试。")
    opa_command = os.getenv("OPA_BIN", "opa")
    opa_path = shutil.which(opa_command)
    assert opa_path, f"缺少 OPA CLI: {opa_command}"

    repo_root = Path(__file__).resolve().parents[1]
    policy_dir = repo_root / "docker" / "opa" / "policies"
    port = _free_port()
    diagnostic_port = _free_port()
    process = subprocess.Popen(
        [
            opa_path,
            "run",
            "--server",
            "--addr",
            f"127.0.0.1:{port}",
            "--diagnostic-addr",
            f"127.0.0.1:{diagnostic_port}",
            "--skip-version-check",
            str(policy_dir),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}/v1"
    try:
        _wait_for_opa(base_url, process)
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.integration
def test_real_opa_client_blocks_structured_candidate(local_opa_base_url: str) -> None:
    """验证真实 OPA 会阻断 blocked 输入安全候选。

    :param local_opa_base_url: 临时 OPA Server 基地址。
    :return: 无返回值；断言通过表示 Python 客户端与 Rego 阻断语义一致。
    """
    decision = asyncio.run(
        _decide(
            local_opa_base_url,
            (
                _candidate(
                    code="RADIOLOGY_GATE",
                    category=InputSafetyCandidateCategory.UNOPENED_CAPABILITY,
                    severity="blocked",
                    message="当前服务未开放影像判读能力。",
                ),
            ),
        )
    )

    assert decision.action == InputSafetyDecisionAction.BLOCK
    assert decision.allow is False
    assert decision.blocked is True
    assert decision.signals[0].code == "RADIOLOGY_GATE"
    assert decision.signals[0].severity == "blocked"


@pytest.mark.integration
def test_real_opa_client_observes_caution_candidate(local_opa_base_url: str) -> None:
    """验证真实 OPA 会观测 caution 输入安全候选并允许继续。

    :param local_opa_base_url: 临时 OPA Server 基地址。
    :return: 无返回值；断言通过表示观测语义未被误判为阻断。
    """
    decision = asyncio.run(
        _decide(
            local_opa_base_url,
            (
                _candidate(
                    code="ATTACHMENT_PURPOSE_UNKNOWN",
                    category=InputSafetyCandidateCategory.INTEGRITY,
                    severity="caution",
                    message="附件用途未明确声明。",
                    matched_terms=("attachment-1",),
                ),
            ),
        )
    )

    assert decision.action == InputSafetyDecisionAction.OBSERVE
    assert decision.allow is True
    assert decision.blocked is False
    assert decision.escalated is False
    assert decision.signals[0].matched_terms == ["attachment-1"]


@pytest.mark.integration
def test_real_opa_client_escalates_urgent_candidate(local_opa_base_url: str) -> None:
    """验证真实 OPA 会升级 urgent 输入安全候选。

    :param local_opa_base_url: 临时 OPA Server 基地址。
    :return: 无返回值；断言通过表示升级动作能够被 Python 客户端正确解析。
    """
    decision = asyncio.run(
        _decide(
            local_opa_base_url,
            (
                _candidate(
                    code="PROMPT_INJECTION_ATTEMPT",
                    category=InputSafetyCandidateCategory.PROMPT_ATTACK,
                    severity="urgent",
                    message="输入存在需要优先处理的提示注入风险。",
                ),
            ),
        )
    )

    assert decision.action == InputSafetyDecisionAction.ESCALATE
    assert decision.allow is True
    assert decision.escalated is True
    assert decision.signals[0].severity == "urgent"


@pytest.mark.integration
def test_real_opa_client_allows_empty_candidate_set(local_opa_base_url: str) -> None:
    """验证真实 OPA 对空候选集合返回 allow。

    :param local_opa_base_url: 临时 OPA Server 基地址。
    :return: 无返回值；断言通过表示无输入安全候选时主链路可以继续。
    """
    decision = asyncio.run(_decide(local_opa_base_url, ()))

    assert decision.action == InputSafetyDecisionAction.ALLOW
    assert decision.allow is True
    assert decision.signals == ()


@pytest.mark.integration
def test_real_opa_client_blocks_when_any_candidate_is_blocked(local_opa_base_url: str) -> None:
    """验证多候选组合中任一 blocked 候选会使整体裁决阻断。

    :param local_opa_base_url: 临时 OPA Server 基地址。
    :return: 无返回值；断言通过表示 OPA 组合裁决优先级符合预期。
    """
    decision = asyncio.run(
        _decide(
            local_opa_base_url,
            (
                _candidate(
                    code="ATTACHMENT_PURPOSE_UNKNOWN",
                    category=InputSafetyCandidateCategory.INTEGRITY,
                    severity="caution",
                    message="附件用途未明确声明。",
                ),
                _candidate(
                    code="INPUT_TOO_LONG",
                    category=InputSafetyCandidateCategory.INTEGRITY,
                    severity="blocked",
                    message="输入文本超过当前服务允许的最大长度。",
                ),
            ),
        )
    )

    assert decision.action == InputSafetyDecisionAction.BLOCK
    assert decision.allow is False
    assert {signal.code for signal in decision.signals} == {
        "ATTACHMENT_PURPOSE_UNKNOWN",
        "INPUT_TOO_LONG",
    }


@pytest.mark.integration
def test_configured_opa_service_supports_external_prefix_when_enabled() -> None:
    """验证配置的外部 OPA 服务可通过真实 Data API 进行裁决。

    :return: 无返回值；未配置外部地址时跳过。
    """
    base_url = os.getenv("INPUT_SAFETY_TEST_OPA_BASE_URL", "").strip()
    if not base_url:
        pytest.skip("未配置 INPUT_SAFETY_TEST_OPA_BASE_URL，跳过外部 OPA 服务测试。")

    decision = asyncio.run(
        _decide(
            base_url,
            (
                _candidate(
                    code="INPUT_TOO_LONG",
                    category=InputSafetyCandidateCategory.INTEGRITY,
                    severity="blocked",
                    message="输入文本超过当前服务允许的最大长度。",
                ),
            ),
            auth_token=os.getenv("INPUT_SAFETY_TEST_OPA_AUTH_TOKEN") or None,
        )
    )

    assert decision.action == InputSafetyDecisionAction.BLOCK
    assert decision.allow is False


@pytest.mark.integration
def test_configured_opa_service_observes_and_allows_external_prefix_when_enabled() -> None:
    """验证配置的外部 OPA 服务会观测 caution 候选并允许继续。

    :return: 无返回值；未配置外部地址时跳过。
    """
    base_url = os.getenv("INPUT_SAFETY_TEST_OPA_BASE_URL", "").strip()
    if not base_url:
        pytest.skip("未配置 INPUT_SAFETY_TEST_OPA_BASE_URL，跳过外部 OPA 服务测试。")

    decision = asyncio.run(
        _decide(
            base_url,
            (
                _candidate(
                    code="ATTACHMENT_PURPOSE_UNKNOWN",
                    category=InputSafetyCandidateCategory.INTEGRITY,
                    severity="caution",
                    message="附件用途未明确声明。",
                    matched_terms=("attachment-remote",),
                ),
            ),
            auth_token=os.getenv("INPUT_SAFETY_TEST_OPA_AUTH_TOKEN") or None,
        )
    )

    assert decision.action == InputSafetyDecisionAction.OBSERVE
    assert decision.allow is True
    assert decision.blocked is False
    assert decision.signals[0].matched_terms == ["attachment-remote"]


@pytest.mark.integration
def test_configured_opa_service_escalates_external_prefix_when_enabled() -> None:
    """验证配置的外部 OPA 服务会升级 urgent 候选。

    :return: 无返回值；未配置外部地址时跳过。
    """
    base_url = os.getenv("INPUT_SAFETY_TEST_OPA_BASE_URL", "").strip()
    if not base_url:
        pytest.skip("未配置 INPUT_SAFETY_TEST_OPA_BASE_URL，跳过外部 OPA 服务测试。")

    decision = asyncio.run(
        _decide(
            base_url,
            (
                _candidate(
                    code="PROMPT_INJECTION_ATTEMPT",
                    category=InputSafetyCandidateCategory.PROMPT_ATTACK,
                    severity="urgent",
                    message="输入存在需要优先处理的提示注入风险。",
                ),
            ),
            auth_token=os.getenv("INPUT_SAFETY_TEST_OPA_AUTH_TOKEN") or None,
        )
    )

    assert decision.action == InputSafetyDecisionAction.ESCALATE
    assert decision.allow is True
    assert decision.escalated is True


async def _decide(
    base_url: str,
    candidates: tuple[InputSafetyCandidate, ...],
    *,
    auth_token: str | None = None,
) -> Any:
    """通过真实 OPA Data API 执行一次输入安全裁决。

    :param base_url: OPA Data API 基地址。
    :param candidates: 待裁决的结构化候选。
    :param auth_token: 可选 OPA Bearer 令牌。
    :return: 返回输入安全裁决对象。
    """
    client = OpaInputSafetyPolicyClient(
        base_url=base_url,
        version="v1",
        package_path="vet_agent.input_safety",
        rule_name="decision",
        auth_token=auth_token,
    )
    return await client.decide(_context(), candidates)


def _context() -> InputSafetyRequestContext:
    """构造 OPA 集成测试使用的请求上下文。

    :return: 返回结构化输入安全请求上下文。
    """
    return InputSafetyRequestContext(
        request_id="req_opa_integration",
        trace_id="trace_opa_integration",
        user_id="user_opa_integration",
        pet_id="pet_opa_integration",
        session_id="session_opa_integration",
        text="当前测试请求不应被 OPA 文本扫描。",
    )


def _candidate(
    *,
    code: str,
    category: InputSafetyCandidateCategory,
    severity: str,
    message: str,
    matched_terms: tuple[str, ...] = (),
) -> InputSafetyCandidate:
    """构造 OPA 集成测试使用的结构化候选。

    :param code: 候选编码。
    :param category: 候选类别。
    :param severity: 候选严重级别。
    :param message: 候选说明。
    :param matched_terms: 结构化关联线索。
    :return: 返回输入安全候选。
    """
    return InputSafetyCandidate(
        code=code,
        category=category,
        source=InputSafetyCandidateSource.STRUCTURED_REQUEST,
        severity=severity,
        message=message,
        matched_terms=matched_terms,
    )


def _free_port() -> int:
    """申请一个当前主机可用的临时 TCP 端口。

    :return: 返回已释放的临时端口号。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_opa(base_url: str, process: subprocess.Popen[str]) -> None:
    """等待临时 OPA Server 的健康接口就绪。

    :param base_url: OPA Data API 基地址。
    :param process: OPA Server 子进程。
    :return: 无返回值。
    :raises AssertionError: OPA 在超时时间内未就绪时抛出。
    """
    del base_url
    deadline = time.monotonic() + 10
    diagnostic_port = process.args[process.args.index("--diagnostic-addr") + 1].split(":")[-1]
    health_url = f"http://127.0.0.1:{diagnostic_port}/health"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"OPA Server 启动失败。stdout={stdout} stderr={stderr}")
        try:
            response = httpx.get(health_url, timeout=0.5)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    stdout, stderr = process.communicate(timeout=1)
    raise AssertionError(f"OPA Server 未在超时时间内就绪。stdout={stdout} stderr={stderr}")

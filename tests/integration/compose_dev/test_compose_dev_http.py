##################################################################################################
# 文件: tests/integration/compose_dev/test_compose_dev_http.py
# 作用: 验证 compose.dev 应用容器的基础 HTTP 探针、metrics 暴露与入口层受控错误响应。
# 边界: 默认仅检查 Compose HTTP 命令契约；真实 HTTP 黑盒测试需显式环境变量开启，不接入真实外部 LLM。
##################################################################################################

from collections.abc import Sequence
from textwrap import dedent

from tests.integration.compose_dev import (
    assert_compose_success,
    build_compose_project_name,
    exec_app_python,
    load_compose_config,
    require_compose_cli,
    require_compose_lifecycle_enabled,
    run_compose_smoke,
    service_config,
    start_compose_app_stack,
    stop_compose_stack,
)


def _command_text(command: Sequence[object]) -> str:
    """将 Compose 命令序列拼接为便于断言的文本。

    :param command: Compose 展开后的命令序列。
    :return: 拼接后的命令文本。
    """

    return "\n".join(str(item) for item in command)


def _http_probe_script() -> str:
    """构建 app 容器内基础 HTTP 探针脚本。

    :return: 可传递给 ``python -c`` 的脚本文本。
    """

    return dedent(
        """
        import urllib.request


        def get(path: str) -> None:
            '''请求 app 容器本地 HTTP 探针并校验成功响应。

            :param path: 需要请求的 HTTP 路径。
            :return: None。
            '''

            response = urllib.request.urlopen(
                f"http://127.0.0.1:8080{path}",
                timeout=5,
            )
            with response:
                body = response.read()
                content_type = response.headers.get("content-type", "")
                assert response.status == 200, (path, response.status, body)
                if path == "/metrics":
                    assert "text/plain" in content_type, content_type
                print(f"{path} {response.status}")


        for endpoint in ("/health", "/ready", "/metrics"):
            get(endpoint)
        """
    ).strip()


def _invalid_entry_request_script() -> str:
    """构建 app 容器内入口层非法 JSON 请求脚本。

    :return: 可传递给 ``python -c`` 的脚本文本。
    """

    return dedent(
        """
        import json
        import urllib.error
        import urllib.request


        def post_invalid_json(path: str, request_id: str, trace_id: str) -> None:
            '''向指定入口发送非法 JSON 并校验统一错误响应。

            :param path: 需要请求的 HTTP 路径。
            :param request_id: 本次请求使用的请求 ID。
            :param trace_id: 本次请求使用的链路 ID。
            :return: None。
            '''

            request = urllib.request.Request(
                f"http://127.0.0.1:8080{path}",
                data=b"{not-json",
                headers={
                    "Content-Type": "application/json",
                    "X-Request-ID": request_id,
                    "X-Trace-ID": trace_id,
                },
                method="POST",
            )
            try:
                urllib.request.urlopen(request, timeout=5)
            except urllib.error.HTTPError as exc:
                body = json.loads(exc.read().decode("utf-8"))
                assert exc.code == 400, (path, exc.code, body)
                assert body["code"] == "INVALID_REQUEST", body
                assert body["request_id"] == request_id, body
                assert body["trace_id"] == trace_id, body
                print(f"{path} {exc.code} {body['code']}")
                return
            raise AssertionError(f"{path} accepted invalid JSON")


        post_invalid_json("/agent/turns", "req_compose_invalid_agent", "trace_compose_invalid_agent")
        post_invalid_json("/openai/v1/responses", "req_compose_invalid_openai", "trace_compose_invalid_openai")
        """
    ).strip()


def test_smoke_profile_targets_health_and_readiness_endpoints() -> None:
    """验证 smoke profile 覆盖基础 HTTP 探针端点。

    :return: None。
    """

    config = load_compose_config(include_smoke_profile=True)
    smoke = service_config(config, "smoke")
    command = smoke.get("command")
    assert isinstance(command, list)
    command_text = _command_text(tuple(command))

    assert "http://app:8080/ready" in command_text
    assert "http://app:8080/health" in command_text
    assert "compose.dev smoke check passed" in command_text


def test_compose_dev_app_serves_probes_metrics_and_controlled_entry_errors() -> None:
    """验证真实 compose.dev 应用服务暴露探针并对入口非法请求返回受控错误。

    :return: None。
    """

    require_compose_lifecycle_enabled()
    require_compose_cli()
    project_name = build_compose_project_name()
    try:
        up_result = start_compose_app_stack(project_name)
        assert_compose_success(
            up_result,
            description="启动 compose.dev HTTP 黑盒测试栈",
        )

        smoke_result = run_compose_smoke(project_name)
        assert_compose_success(
            smoke_result,
            description="运行 compose.dev 基础 smoke 服务",
        )

        probe_result = exec_app_python(
            project_name,
            _http_probe_script(),
        )
        assert_compose_success(
            probe_result,
            description="检查 compose.dev app HTTP 探针与 metrics",
        )
        assert "/health 200" in probe_result.stdout
        assert "/ready 200" in probe_result.stdout
        assert "/metrics 200" in probe_result.stdout

        entry_result = exec_app_python(
            project_name,
            _invalid_entry_request_script(),
        )
        assert_compose_success(
            entry_result,
            description="检查 compose.dev app 入口层受控错误响应",
        )
        assert "/agent/turns 400 INVALID_REQUEST" in entry_result.stdout
        assert "/openai/v1/responses 400 INVALID_REQUEST" in entry_result.stdout
    finally:
        down_result = stop_compose_stack(project_name)
        assert_compose_success(
            down_result,
            description="清理 compose.dev HTTP 黑盒测试栈",
        )

##################################################################################################
# 文件: tests/integration/compose_dev/helpers.py
# 作用: 提供 compose.dev 基础集成测试使用的 Docker Compose 命令封装与配置断言辅助能力。
# 边界: 仅执行黑盒命令与测试断言；不导入生产实现模块，不实现业务领域逻辑。
##################################################################################################

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Final, TypeAlias, cast
from uuid import uuid4

import pytest


JsonMap: TypeAlias = dict[str, object]
ComposeConfig: TypeAlias = JsonMap

COMPOSE_LIFECYCLE_ENV_NAME: Final[str] = "VETERINARY_AGENT_RUN_COMPOSE_DEV_TESTS"
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_COMPOSE_FILE: Final[Path] = _REPO_ROOT / "compose.dev.yml"
_ENV_EXAMPLE_FILE: Final[Path] = _REPO_ROOT / ".env.example"

REQUIRED_SERVICES: Final[frozenset[str]] = frozenset(
    {
        "postgres",
        "migrate",
        "app",
    }
)
REQUIRED_ALEMBIC_REVISION: Final[str] = "20260712_0003"
REQUIRED_RUNTIME_ENV_KEYS: Final[frozenset[str]] = frozenset(
    {
        "DATABASE_URL",
        "LANGGRAPH_POSTGRES_SETUP_ON_STARTUP",
        "LANGGRAPH_STRICT_MSGPACK",
        "LOG_LEVEL",
        "PYTHONPATH",
    }
)
REQUIRED_DATABASE_TABLES: Final[frozenset[str]] = frozenset(
    {
        "alembic_version",
        "checkpoint_run_lock",
        "checkpoint_segment_publish",
        "checkpoint_thread",
        "conversation_attachment_ref",
        "conversation_message",
        "conversation_message_segment",
        "conversation_session",
        "logic_trace",
        "logic_trace_artifact",
        "logic_trace_call_summary",
        "logic_trace_event",
        "logic_trace_outbox",
        "logic_trace_projection",
    }
)
BANNED_PLACEHOLDER_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "TRIAGE_CORE_PATH",
        "KB_TPL_DIR",
        "assets/triage-core",
        "kb-tpl",
    }
)


@dataclass(frozen=True, slots=True)
class ComposeCommandResult:
    """Docker Compose 命令执行结果。"""

    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    def combined_output(self) -> str:
        """合并标准输出与标准错误，便于失败断言展示。

        :return: 拼接后的命令输出文本。
        """

        return "\n".join(part for part in (self.stdout, self.stderr) if part)


def _truthy_env_value(value: str | None) -> bool:
    """判断环境变量字符串是否表示开启状态。

    :param value: 原始环境变量值。
    :return: 若值为常见真值字符串，则返回 True。
    """

    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _compose_command(
    args: Sequence[str],
    *,
    project_name: str | None = None,
    include_smoke_profile: bool = False,
) -> list[str]:
    """构建 Docker Compose 命令参数。

    :param args: 需要传递给 docker compose 的子命令参数。
    :param project_name: 可选 Compose project name，用于隔离真实生命周期测试。
    :param include_smoke_profile: 是否启用 smoke profile。
    :return: 可直接传递给 subprocess 的命令参数列表。
    """

    command = ["docker", "compose", "-f", str(_COMPOSE_FILE)]
    if include_smoke_profile:
        command.extend(("--profile", "smoke"))
    if project_name is not None:
        command.extend(("-p", project_name))
    command.extend(args)
    return command


def require_compose_cli() -> None:
    """确认当前环境可执行 Docker Compose 命令。

    :return: None。
    """

    if shutil.which("docker") is None:
        pytest.skip("当前环境未安装 docker CLI，跳过 compose.dev 集成测试")
    result = run_compose(("version",), timeout_seconds=30)
    if result.returncode != 0:
        pytest.skip("当前环境无法执行 docker compose，跳过 compose.dev 集成测试")


def require_compose_lifecycle_enabled(
    environ: Mapping[str, str] | None = None,
) -> None:
    """确认真实 Docker 生命周期测试已显式开启。

    :param environ: 可选环境变量映射；未传入时读取当前进程环境。
    :return: None。
    """

    resolved_environ = os.environ if environ is None else environ
    if not _truthy_env_value(resolved_environ.get(COMPOSE_LIFECYCLE_ENV_NAME)):
        pytest.skip(
            f"设置 {COMPOSE_LIFECYCLE_ENV_NAME}=1 后才运行 compose.dev 生命周期测试"
        )


def run_compose(
    args: Sequence[str],
    *,
    project_name: str | None = None,
    include_smoke_profile: bool = False,
    timeout_seconds: float = 120.0,
    env: Mapping[str, str] | None = None,
) -> ComposeCommandResult:
    """执行一次 Docker Compose 命令并捕获输出。

    :param args: 需要传递给 docker compose 的子命令参数。
    :param project_name: 可选 Compose project name。
    :param include_smoke_profile: 是否启用 smoke profile。
    :param timeout_seconds: 命令允许的最大耗时，单位为秒。
    :param env: 可选环境变量覆盖。
    :return: Docker Compose 命令执行结果。
    """

    command = _compose_command(
        args,
        project_name=project_name,
        include_smoke_profile=include_smoke_profile,
    )
    process_env = os.environ.copy()
    if env is not None:
        process_env.update(env)
    completed = subprocess.run(
        command,
        cwd=_REPO_ROOT,
        env=process_env,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    return ComposeCommandResult(
        command=tuple(command),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def assert_compose_success(
    result: ComposeCommandResult,
    *,
    description: str,
) -> None:
    """断言 Docker Compose 命令执行成功。

    :param result: Docker Compose 命令执行结果。
    :param description: 当前命令的中文业务说明，用于失败信息。
    :return: None。
    """

    if result.returncode == 0:
        return
    output = result.combined_output()
    pytest.fail(
        f"{description}失败，退出码 {result.returncode}，命令: "
        f"{' '.join(result.command)}\n{output}"
    )


def load_compose_config(*, include_smoke_profile: bool = False) -> ComposeConfig:
    """读取 Docker Compose 展开后的 JSON 配置。

    :param include_smoke_profile: 是否启用 smoke profile 后再展开配置。
    :return: 展开后的 Compose 配置映射。
    """

    require_compose_cli()
    result = run_compose(
        ("config", "--format", "json"),
        include_smoke_profile=include_smoke_profile,
        timeout_seconds=60,
    )
    assert_compose_success(result, description="解析 compose.dev.yml")
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, dict):
        pytest.fail("docker compose config --format json 未返回对象")
    return cast(ComposeConfig, parsed)


def service_config(config: ComposeConfig, service_name: str) -> JsonMap:
    """读取指定服务的 Compose 配置。

    :param config: 展开后的 Compose 配置映射。
    :param service_name: 需要读取的服务名称。
    :return: 指定服务配置。
    """

    services = config.get("services")
    if not isinstance(services, dict):
        pytest.fail("Compose 配置缺少 services 对象")
    service = services.get(service_name)
    if not isinstance(service, dict):
        pytest.fail(f"Compose 配置缺少 {service_name} 服务")
    return cast(JsonMap, service)


def read_env_example_values() -> dict[str, str]:
    """读取 .env.example 中声明的键值对。

    :return: .env.example 键值对映射。
    """

    values: dict[str, str] = {}
    for raw_line in _ENV_EXAMPLE_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def read_compose_related_file_text() -> str:
    """读取 compose.dev.yml 与 .env.example 文本，用于悬空占位扫描。

    :return: 两个文件拼接后的文本。
    """

    return "\n".join(
        (
            _COMPOSE_FILE.read_text(encoding="utf-8"),
            _ENV_EXAMPLE_FILE.read_text(encoding="utf-8"),
        )
    )


def build_compose_project_name() -> str:
    """构建用于真实生命周期测试的唯一 Compose project name。

    :return: 当前测试进程使用的 Compose project name。
    """

    return f"veterinary-agent-it-{uuid4().hex[:12]}"


def stdout_lines(stdout: str) -> set[str]:
    """将命令标准输出拆分为去空白后的非空行集合。

    :param stdout: 命令标准输出。
    :return: 去除空行后的输出行集合。
    """

    return {line.strip() for line in stdout.splitlines() if line.strip()}


def start_compose_app_stack(project_name: str) -> ComposeCommandResult:
    """启动 compose.dev app 基础集成测试栈并等待健康检查完成。

    :param project_name: 当前测试使用的 Compose project name。
    :return: Docker Compose 启动命令执行结果。
    """

    return run_compose(
        (
            "up",
            "--build",
            "-d",
            "--wait",
            "--wait-timeout",
            "180",
            "app",
        ),
        project_name=project_name,
        timeout_seconds=360,
    )


def stop_compose_stack(project_name: str) -> ComposeCommandResult:
    """停止并清理 compose.dev 测试栈及其数据卷。

    :param project_name: 当前测试使用的 Compose project name。
    :return: Docker Compose 清理命令执行结果。
    """

    return run_compose(
        ("down", "-v", "--remove-orphans"),
        project_name=project_name,
        include_smoke_profile=True,
        timeout_seconds=120,
    )


def run_compose_smoke(project_name: str) -> ComposeCommandResult:
    """运行 compose.dev smoke 服务以检查基础 HTTP 探针。

    :param project_name: 当前测试使用的 Compose project name。
    :return: Docker Compose smoke 命令执行结果。
    """

    return run_compose(
        ("run", "--rm", "smoke"),
        project_name=project_name,
        include_smoke_profile=True,
        timeout_seconds=120,
    )


def rerun_compose_migration(project_name: str) -> ComposeCommandResult:
    """再次运行 compose.dev migrate 服务，用于验证迁移幂等性。

    :param project_name: 当前测试使用的 Compose project name。
    :return: Docker Compose migrate 命令执行结果。
    """

    return run_compose(
        ("run", "--rm", "migrate"),
        project_name=project_name,
        timeout_seconds=120,
    )


def exec_postgres_query(
    project_name: str,
    query: str,
    *,
    timeout_seconds: float = 60.0,
) -> ComposeCommandResult:
    """在 compose.dev PostgreSQL 容器内执行只读 SQL 查询。

    :param project_name: 当前测试使用的 Compose project name。
    :param query: 需要通过 psql 执行的 SQL 查询。
    :param timeout_seconds: 查询命令允许的最大耗时，单位为秒。
    :return: psql 命令执行结果。
    """

    return run_compose(
        (
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "veterinary_agent",
            "-d",
            "veterinary_agent_dev",
            "-tAc",
            query,
        ),
        project_name=project_name,
        timeout_seconds=timeout_seconds,
    )


def exec_app_python(
    project_name: str,
    script: str,
    *,
    timeout_seconds: float = 60.0,
) -> ComposeCommandResult:
    """在 compose.dev app 容器内执行 Python 脚本。

    :param project_name: 当前测试使用的 Compose project name。
    :param script: 需要传递给 ``python -c`` 的脚本文本。
    :param timeout_seconds: 脚本允许的最大耗时，单位为秒。
    :return: Python 脚本执行结果。
    """

    return run_compose(
        (
            "exec",
            "-T",
            "app",
            "python",
            "-c",
            script,
        ),
        project_name=project_name,
        timeout_seconds=timeout_seconds,
    )

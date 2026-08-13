#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/integration/run-external-api-smoke.sh
# 作用: 运行接入真实 LiteLLM、OPA、PostgreSQL 与 Mem0 的 Agent API 集成冒烟测试。
# 范围: 仅执行 tests/test_vet_agent_api_external_integration.py，不替代默认快速 CI 门禁。
# 说明: 本脚本只读取调用方显式注入的环境变量，不写入或推导真实密钥；可按需通过 SSH 隧道访问远程 PostgreSQL。
# =============================================================================

set -euo pipefail

required_vars=(
    "EXTERNAL_API_TEST_DATABASE_URL"
    "EXTERNAL_API_TEST_LITELLM_BASE_URL"
    "EXTERNAL_API_TEST_LITELLM_API_KEY"
    "EXTERNAL_API_TEST_OPA_BASE_URL"
)

for var_name in "${required_vars[@]}"; do
    if [[ -z "${!var_name:-}" ]]; then
        echo "缺少外部 API 集成测试环境变量: ${var_name}" >&2
        exit 1
    fi
done

ssh_tunnel_pid=""

cleanup() {
    if [[ -n "${ssh_tunnel_pid}" ]]; then
        kill "${ssh_tunnel_pid}" >/dev/null 2>&1 || true
        wait "${ssh_tunnel_pid}" >/dev/null 2>&1 || true
    fi
}

trap cleanup EXIT

if [[ -n "${EXTERNAL_API_TEST_SSH_HOST:-}" ]]; then
    ssh_port="${EXTERNAL_API_TEST_SSH_PORT:-22}"
    ssh_user="${EXTERNAL_API_TEST_SSH_USER:-}"
    ssh_key="${EXTERNAL_API_TEST_SSH_KEY:-}"
    local_host="${EXTERNAL_API_TEST_DB_TUNNEL_LOCAL_HOST:-127.0.0.1}"
    local_port="${EXTERNAL_API_TEST_DB_TUNNEL_LOCAL_PORT:-55432}"
    remote_host="${EXTERNAL_API_TEST_DB_TUNNEL_REMOTE_HOST:-127.0.0.1}"
    remote_port="${EXTERNAL_API_TEST_DB_TUNNEL_REMOTE_PORT:-5432}"

    if [[ -z "${ssh_user}" || -z "${ssh_key}" ]]; then
        echo "启用 SSH 数据库隧道时必须配置 EXTERNAL_API_TEST_SSH_USER 与 EXTERNAL_API_TEST_SSH_KEY" >&2
        exit 1
    fi

    # 外部开发库公网入口可能受到网络抖动或非业务扫描流量影响；
    # 隧道模式通过 SSH 到远程主机后访问本机 PostgreSQL，可降低 API 冒烟测试误报。
    ssh \
        -i "${ssh_key}" \
        -p "${ssh_port}" \
        -o StrictHostKeyChecking=no \
        -o ExitOnForwardFailure=yes \
        -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=2 \
        -N \
        -L "${local_host}:${local_port}:${remote_host}:${remote_port}" \
        "${ssh_user}@${EXTERNAL_API_TEST_SSH_HOST}" &
    ssh_tunnel_pid="$!"
    sleep "${EXTERNAL_API_TEST_DB_TUNNEL_READY_SECONDS:-1}"
    if ! kill -0 "${ssh_tunnel_pid}" >/dev/null 2>&1; then
        wait "${ssh_tunnel_pid}" || true
        echo "SSH 数据库隧道启动失败，请检查端口占用、密钥权限和远程主机连通性。" >&2
        exit 1
    fi

    export EXTERNAL_API_TEST_DB_TUNNEL_LOCAL_HOST="${local_host}"
    export EXTERNAL_API_TEST_DB_TUNNEL_LOCAL_PORT="${local_port}"
    EXTERNAL_API_TEST_DATABASE_URL="$(
        python3 - <<'PY'
import os
from urllib.parse import urlsplit, urlunsplit

database_url = os.environ["EXTERNAL_API_TEST_DATABASE_URL"]
local_host = os.environ["EXTERNAL_API_TEST_DB_TUNNEL_LOCAL_HOST"]
local_port = os.environ["EXTERNAL_API_TEST_DB_TUNNEL_LOCAL_PORT"]
parts = urlsplit(database_url)
netloc_parts = parts.netloc.rsplit("@", 1)
userinfo = f"{netloc_parts[0]}@" if len(netloc_parts) == 2 else ""
print(urlunsplit((parts.scheme, f"{userinfo}{local_host}:{local_port}", parts.path, parts.query, parts.fragment)))
PY
    )"
    export EXTERNAL_API_TEST_DATABASE_URL
fi

export RUN_EXTERNAL_API_SMOKE=true

pytest_args=("$@")
if [[ "${#pytest_args[@]}" -eq 0 ]]; then
    pytest_args=("tests/test_vet_agent_api_external_integration.py")
fi

uv run pytest "${pytest_args[@]}" -m integration -q

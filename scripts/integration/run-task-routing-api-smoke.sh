#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/integration/run-task-routing-api-smoke.sh
# 作用: 运行接入真实 PostgreSQL、LiteLLM 与 OPA 的多任务拆分 API 集成测试。
# 范围: 覆盖 TaskRouterAgent、LiteLLM response_format、任务域目录、OPA 准入与 API 响应审计。
# 说明: 默认通过 SSH 隧道访问远程开发依赖服务；本脚本只读取调用方显式注入的环境变量。
# =============================================================================

set -euo pipefail

case "${EXTERNAL_API_TEST_TUNNEL_HTTP_SERVICES:-true}" in
    1|true|TRUE|yes|YES|on|ON)
        tunnel_http_services=true
        ;;
    *)
        tunnel_http_services=false
        ;;
esac

required_vars=(
    "EXTERNAL_API_TEST_DATABASE_URL"
    "EXTERNAL_API_TEST_LITELLM_API_KEY"
)

if [[ "${tunnel_http_services}" != "true" ]]; then
    required_vars+=(
        "EXTERNAL_API_TEST_LITELLM_BASE_URL"
        "EXTERNAL_API_TEST_OPA_BASE_URL"
    )
fi

for var_name in "${required_vars[@]}"; do
    if [[ -z "${!var_name:-}" ]]; then
        echo "缺少多任务拆分外部 API 集成测试环境变量: ${var_name}" >&2
        exit 1
    fi
done

ssh_tunnel_pids=()

cleanup() {
    for ssh_tunnel_pid in "${ssh_tunnel_pids[@]}"; do
        kill "${ssh_tunnel_pid}" >/dev/null 2>&1 || true
        wait "${ssh_tunnel_pid}" >/dev/null 2>&1 || true
    done
}

trap cleanup EXIT

if [[ -n "${EXTERNAL_API_TEST_SSH_HOST:-}" ]]; then
    ssh_port="${EXTERNAL_API_TEST_SSH_PORT:-22}"
    ssh_user="${EXTERNAL_API_TEST_SSH_USER:-}"
    ssh_key="${EXTERNAL_API_TEST_SSH_KEY:-}"
    if [[ -z "${ssh_user}" || -z "${ssh_key}" ]]; then
        echo "启用 SSH 隧道时必须配置 EXTERNAL_API_TEST_SSH_USER 与 EXTERNAL_API_TEST_SSH_KEY" >&2
        exit 1
    fi

    start_ssh_tunnel() {
        local tunnel_name="$1"
        local local_host="$2"
        local local_port="$3"
        local remote_host="$4"
        local remote_port="$5"

        # 外部开发环境公网入口可能受网关路径、代理缓冲或网络波动影响；
        # 隧道模式直接连接远端主机本机端口，便于区分业务链路问题和公网代理问题。
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
        local ssh_tunnel_pid="$!"
        ssh_tunnel_pids+=("${ssh_tunnel_pid}")
        sleep "${EXTERNAL_API_TEST_TUNNEL_READY_SECONDS:-1}"
        if ! kill -0 "${ssh_tunnel_pid}" >/dev/null 2>&1; then
            wait "${ssh_tunnel_pid}" || true
            echo "SSH ${tunnel_name} 隧道启动失败，请检查端口占用、密钥权限和远程主机连通性。" >&2
            exit 1
        fi
    }

    db_local_host="${EXTERNAL_API_TEST_DB_TUNNEL_LOCAL_HOST:-127.0.0.1}"
    db_local_port="${EXTERNAL_API_TEST_DB_TUNNEL_LOCAL_PORT:-55434}"
    db_remote_host="${EXTERNAL_API_TEST_DB_TUNNEL_REMOTE_HOST:-127.0.0.1}"
    db_remote_port="${EXTERNAL_API_TEST_DB_TUNNEL_REMOTE_PORT:-5432}"

    start_ssh_tunnel "PostgreSQL" "${db_local_host}" "${db_local_port}" "${db_remote_host}" "${db_remote_port}"

    export EXTERNAL_API_TEST_DB_TUNNEL_LOCAL_HOST="${db_local_host}"
    export EXTERNAL_API_TEST_DB_TUNNEL_LOCAL_PORT="${db_local_port}"
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

    case "${tunnel_http_services}" in
        true)
            litellm_local_host="${EXTERNAL_API_TEST_LITELLM_TUNNEL_LOCAL_HOST:-127.0.0.1}"
            litellm_local_port="${EXTERNAL_API_TEST_LITELLM_TUNNEL_LOCAL_PORT:-54001}"
            litellm_remote_host="${EXTERNAL_API_TEST_LITELLM_TUNNEL_REMOTE_HOST:-127.0.0.1}"
            litellm_remote_port="${EXTERNAL_API_TEST_LITELLM_TUNNEL_REMOTE_PORT:-4000}"
            opa_local_host="${EXTERNAL_API_TEST_OPA_TUNNEL_LOCAL_HOST:-127.0.0.1}"
            opa_local_port="${EXTERNAL_API_TEST_OPA_TUNNEL_LOCAL_PORT:-58182}"
            opa_remote_host="${EXTERNAL_API_TEST_OPA_TUNNEL_REMOTE_HOST:-127.0.0.1}"
            opa_remote_port="${EXTERNAL_API_TEST_OPA_TUNNEL_REMOTE_PORT:-8181}"

            start_ssh_tunnel "LiteLLM" "${litellm_local_host}" "${litellm_local_port}" "${litellm_remote_host}" "${litellm_remote_port}"
            start_ssh_tunnel "OPA" "${opa_local_host}" "${opa_local_port}" "${opa_remote_host}" "${opa_remote_port}"

            export EXTERNAL_API_TEST_LITELLM_BASE_URL="http://${litellm_local_host}:${litellm_local_port}${EXTERNAL_API_TEST_LITELLM_TUNNEL_BASE_PATH:-/v1}"
            export EXTERNAL_API_TEST_OPA_BASE_URL="http://${opa_local_host}:${opa_local_port}"
            ;;
    esac
fi

export RUN_TASK_ROUTING_API_EXTERNAL_TEST=true

pytest_args=("$@")
case "${pytest_args[0]:-}" in
    ""|-*)
        pytest_args=("tests/integration/test_task_routing_api_external.py" "${pytest_args[@]}")
        ;;
esac

uv run pytest "${pytest_args[@]}" -m integration -q

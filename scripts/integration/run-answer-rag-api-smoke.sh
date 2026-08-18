#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/integration/run-answer-rag-api-smoke.sh
# 作用: 运行接入真实 PostgreSQL、LiteLLM 与 OPA 的回答相关 RAG API 集成测试。
# 范围: 仅覆盖回答充分性为 answer 时的回答 RAG 召回与 API 响应 metadata，不验证 Mem0。
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
        echo "缺少回答相关 RAG 外部 API 集成测试环境变量: ${var_name}" >&2
        exit 1
    fi
done

ssh_tunnel_pids=()

# 清理脚本启动的 SSH 隧道，避免本地端口在测试失败后被占用。
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

    # 启动单条 SSH 本地端口转发隧道。
    start_ssh_tunnel() {
        local tunnel_name="$1"
        local local_host="$2"
        local local_port="$3"
        local remote_host="$4"
        local remote_port="$5"

        # 远程开发环境公网入口可能受到网关路径和网络波动影响；
        # 隧道模式通过远程主机本机端口访问依赖，可将失败原因聚焦到业务链路本身。
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
    db_local_port="${EXTERNAL_API_TEST_DB_TUNNEL_LOCAL_PORT:-55436}"
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
            litellm_local_port="${EXTERNAL_API_TEST_LITELLM_TUNNEL_LOCAL_PORT:-54003}"
            litellm_remote_host="${EXTERNAL_API_TEST_LITELLM_TUNNEL_REMOTE_HOST:-127.0.0.1}"
            litellm_remote_port="${EXTERNAL_API_TEST_LITELLM_TUNNEL_REMOTE_PORT:-4000}"
            opa_local_host="${EXTERNAL_API_TEST_OPA_TUNNEL_LOCAL_HOST:-127.0.0.1}"
            opa_local_port="${EXTERNAL_API_TEST_OPA_TUNNEL_LOCAL_PORT:-58184}"
            opa_remote_host="${EXTERNAL_API_TEST_OPA_TUNNEL_REMOTE_HOST:-127.0.0.1}"
            opa_remote_port="${EXTERNAL_API_TEST_OPA_TUNNEL_REMOTE_PORT:-8181}"

            start_ssh_tunnel "LiteLLM" "${litellm_local_host}" "${litellm_local_port}" "${litellm_remote_host}" "${litellm_remote_port}"
            start_ssh_tunnel "OPA" "${opa_local_host}" "${opa_local_port}" "${opa_remote_host}" "${opa_remote_port}"

            export EXTERNAL_API_TEST_LITELLM_BASE_URL="http://${litellm_local_host}:${litellm_local_port}${EXTERNAL_API_TEST_LITELLM_TUNNEL_BASE_PATH:-/v1}"
            export EXTERNAL_API_TEST_OPA_BASE_URL="http://${opa_local_host}:${opa_local_port}"
            export EXTERNAL_API_TEST_INPUT_SAFETY_OPA_BASE_URL="${EXTERNAL_API_TEST_OPA_BASE_URL}"
            export EXTERNAL_API_TEST_CLINICAL_SAFETY_OPA_BASE_URL="${EXTERNAL_API_TEST_OPA_BASE_URL}"
            export EXTERNAL_API_TEST_TASK_ROUTING_OPA_BASE_URL="${EXTERNAL_API_TEST_OPA_BASE_URL}"
            export EXTERNAL_API_TEST_CONSULTATION_ANSWERABILITY_OPA_BASE_URL="${EXTERNAL_API_TEST_OPA_BASE_URL}"
            ;;
    esac
fi

# 回答相关 RAG 烟测仅需要真实 PostgreSQL、LiteLLM 与 OPA；Mem0 在此链路中关闭，避免引入无关依赖。
export EXTERNAL_API_TEST_ENABLE_MEM0=false
export ENABLE_MEM0=false
export RUN_CONSULTATION_STATE_API_EXTERNAL_TEST=true

case "${EXTERNAL_API_TEST_SYNC_CONSULTATION_OPA_POLICY:-true}" in
    1|true|TRUE|yes|YES|on|ON)
        consultation_opa_policy_file="${EXTERNAL_API_TEST_CONSULTATION_OPA_POLICY_FILE:-docker/opa/policies/consultation_answerability.rego}"
        consultation_opa_policy_id="${EXTERNAL_API_TEST_CONSULTATION_OPA_POLICY_ID:-consultation_answerability}"
        if [[ -z "${EXTERNAL_API_TEST_OPA_BASE_URL:-}" ]]; then
            echo "同步问诊回答充分性 OPA 策略时必须配置 EXTERNAL_API_TEST_OPA_BASE_URL" >&2
            exit 1
        fi
        if [[ ! -f "${consultation_opa_policy_file}" ]]; then
            echo "问诊回答充分性 OPA 策略文件不存在: ${consultation_opa_policy_file}" >&2
            exit 1
        fi
        normalized_opa_base_url="${EXTERNAL_API_TEST_OPA_BASE_URL%/}"
        if [[ "${normalized_opa_base_url}" != */v1 ]]; then
            normalized_opa_base_url="${normalized_opa_base_url}/v1"
        fi
        opa_policy_headers=()
        if [[ -n "${EXTERNAL_API_TEST_OPA_AUTH_TOKEN:-}" ]]; then
            opa_policy_headers=(-H "Authorization: Bearer ${EXTERNAL_API_TEST_OPA_AUTH_TOKEN}")
        fi
        curl \
            -fsS \
            -X PUT \
            "${normalized_opa_base_url}/policies/${consultation_opa_policy_id}" \
            -H "Content-Type: text/plain" \
            "${opa_policy_headers[@]}" \
            --data-binary "@${consultation_opa_policy_file}" \
            >/dev/null
        ;;
esac

pytest_args=("$@")
case "${pytest_args[0]:-}" in
    ""|-*)
        pytest_args=(
            "tests/integration/test_consultation_state_api_external.py::test_consultation_state_external_dependencies_are_reachable"
            "tests/integration/test_consultation_state_api_external.py::test_consultation_state_api_completes_when_user_requests_answer_now"
            "${pytest_args[@]}"
        )
        ;;
esac

uv run pytest "${pytest_args[@]}" -m integration -q

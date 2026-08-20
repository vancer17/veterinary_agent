#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/integration/run-clinical-safety-api-smoke.sh
# 作用: 通过远程开发环境执行临床安全阶段 3 真实服务集成测试。
# 范围: 建立 PostgreSQL、LiteLLM 与 OPA 的 SSH 隧道，注入安全环境变量并执行
#       tests/integration/test_clinical_safety_api_external.py。
# 说明: 脚本不会回显密钥；远程策略同步与数据库迁移可通过环境变量关闭。
# =============================================================================

set -Eeuo pipefail

ssh_host="${EXTERNAL_API_TEST_SSH_HOST:-47.97.19.58}"
ssh_port="${EXTERNAL_API_TEST_SSH_PORT:-22}"
ssh_user="${EXTERNAL_API_TEST_SSH_USER:-devlop}"
ssh_key="${EXTERNAL_API_TEST_SSH_KEY:-/home/vancer17/.ssh/AlibabaCloudLinux}"
remote_project_dir="${EXTERNAL_API_TEST_REMOTE_PROJECT_DIR:-/home/devlop/veterinary_agent}"

sync_remote_policy="${CLINICAL_SAFETY_SMOKE_SYNC_REMOTE_POLICY:-true}"
upgrade_remote_database="${CLINICAL_SAFETY_SMOKE_UPGRADE_REMOTE_DATABASE:-true}"
tunnel_http_services="${EXTERNAL_API_TEST_TUNNEL_HTTP_SERVICES:-true}"
tunnel_ready_seconds="${EXTERNAL_API_TEST_TUNNEL_READY_SECONDS:-2}"

ssh_tunnel_pids=()

cleanup() {
    local tunnel_pid
    for tunnel_pid in "${ssh_tunnel_pids[@]}"; do
        kill "${tunnel_pid}" >/dev/null 2>&1 || true
        wait "${tunnel_pid}" >/dev/null 2>&1 || true
    done
}

trap cleanup EXIT

start_ssh_tunnel() {
    local tunnel_name="$1"
    local local_host="$2"
    local local_port="$3"
    local remote_host="$4"
    local remote_port="$5"

    ssh \
        -i "${ssh_key}" \
        -p "${ssh_port}" \
        -o StrictHostKeyChecking=no \
        -o ExitOnForwardFailure=yes \
        -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=2 \
        -N \
        -L "${local_host}:${local_port}:${remote_host}:${remote_port}" \
        "${ssh_user}@${ssh_host}" &
    local tunnel_pid="$!"
    ssh_tunnel_pids+=("${tunnel_pid}")
    sleep "${tunnel_ready_seconds}"
    if ! kill -0 "${tunnel_pid}" >/dev/null 2>&1; then
        wait "${tunnel_pid}" || true
        echo "SSH ${tunnel_name} 隧道启动失败。" >&2
        exit 1
    fi
}

if [[ "${sync_remote_policy}" == "true" ]]; then
    echo "同步本地 OPA 策略到远程开发环境..."
    remote_policy_stage="$(
        ssh -i "${ssh_key}" -p "${ssh_port}" -o StrictHostKeyChecking=no \
            "${ssh_user}@${ssh_host}" "mktemp -d"
    )"
    scp \
        -i "${ssh_key}" \
        -P "${ssh_port}" \
        -o StrictHostKeyChecking=no \
        docker/opa/policies/*.rego \
        "${ssh_user}@${ssh_host}:${remote_policy_stage}/"
    ssh \
        -i "${ssh_key}" \
        -p "${ssh_port}" \
        -o StrictHostKeyChecking=no \
        "${ssh_user}@${ssh_host}" \
        "sudo cp ${remote_policy_stage}/*.rego ${remote_project_dir}/docker/opa/policies/ && rm -rf ${remote_policy_stage} && sudo docker restart vet-agent-dev-opa >/dev/null"
    opa_ready=false
    for _ in $(seq 1 30); do
        if ssh -i "${ssh_key}" -p "${ssh_port}" -o StrictHostKeyChecking=no \
            "${ssh_user}@${ssh_host}" \
            "curl -fsS http://127.0.0.1:8282/health >/dev/null"; then
            opa_ready=true
            break
        fi
        sleep 1
    done
    if [[ "${opa_ready}" != "true" ]]; then
        echo "远程 OPA 策略重启后未能就绪。" >&2
        exit 1
    fi
fi

db_local_host="${EXTERNAL_API_TEST_DB_TUNNEL_LOCAL_HOST:-127.0.0.1}"
db_local_port="${EXTERNAL_API_TEST_DB_TUNNEL_LOCAL_PORT:-55436}"
db_remote_host="${EXTERNAL_API_TEST_DB_TUNNEL_REMOTE_HOST:-127.0.0.1}"
db_remote_port="${EXTERNAL_API_TEST_DB_TUNNEL_REMOTE_PORT:-5432}"
export DB_LOCAL_HOST="${db_local_host}"
export DB_LOCAL_PORT="${db_local_port}"

start_ssh_tunnel "PostgreSQL" \
    "${db_local_host}" "${db_local_port}" "${db_remote_host}" "${db_remote_port}"

if [[ -z "${EXTERNAL_API_TEST_DATABASE_URL:-}" ]]; then
    EXTERNAL_API_TEST_DATABASE_URL="$(
        ssh -i "${ssh_key}" -p "${ssh_port}" -o StrictHostKeyChecking=no \
            "${ssh_user}@${ssh_host}" \
            "sudo docker inspect vet-agent-dev-postgres --format '{{range .Config.Env}}{{println .}}{{end}}'" \
        | python3 -c '
import os
import sys
from urllib.parse import quote

values = dict(line.rstrip("\n").split("=", 1) for line in sys.stdin if "=" in line)
user = quote(values["VET_AGENT_POSTGRES_USER"], safe="")
password = quote(values["VET_AGENT_POSTGRES_PASSWORD"], safe="")
database = quote(values["VET_AGENT_POSTGRES_DB"], safe="")
host = os.environ["DB_LOCAL_HOST"]
port = os.environ["DB_LOCAL_PORT"]
print(f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}")
'
    )"
else
    EXTERNAL_API_TEST_DATABASE_URL="$(
        python3 -c '
import os
from urllib.parse import urlsplit, urlunsplit

parts = urlsplit(os.environ["EXTERNAL_API_TEST_DATABASE_URL"])
netloc_parts = parts.netloc.rsplit("@", 1)
userinfo = f"{netloc_parts[0]}@" if len(netloc_parts) == 2 else ""
local_netloc = f"{userinfo}{os.environ[\"DB_LOCAL_HOST\"]}:{os.environ[\"DB_LOCAL_PORT\"]}"
print(urlunsplit((parts.scheme, local_netloc, parts.path, parts.query, parts.fragment)))
'
    )"
fi
export EXTERNAL_API_TEST_DATABASE_URL

if [[ -z "${EXTERNAL_API_TEST_LITELLM_API_KEY:-}" ]]; then
    EXTERNAL_API_TEST_LITELLM_API_KEY="$(
        ssh -i "${ssh_key}" -p "${ssh_port}" -o StrictHostKeyChecking=no \
            "${ssh_user}@${ssh_host}" \
            "sudo docker inspect vet-agent-dev-litellm --format '{{range .Config.Env}}{{println .}}{{end}}'" \
        | python3 -c 'import sys; values = dict(line.rstrip("\n").split("=", 1) for line in sys.stdin if "=" in line); print(values["LITELLM_MASTER_KEY"])'
    )"
fi
export EXTERNAL_API_TEST_LITELLM_API_KEY

if [[ "${tunnel_http_services}" == "true" ]]; then
    litellm_local_host="${EXTERNAL_API_TEST_LITELLM_TUNNEL_LOCAL_HOST:-127.0.0.1}"
    litellm_local_port="${EXTERNAL_API_TEST_LITELLM_TUNNEL_LOCAL_PORT:-54002}"
    opa_local_host="${EXTERNAL_API_TEST_OPA_TUNNEL_LOCAL_HOST:-127.0.0.1}"
    opa_local_port="${EXTERNAL_API_TEST_OPA_TUNNEL_LOCAL_PORT:-58183}"

    start_ssh_tunnel "LiteLLM" \
        "${litellm_local_host}" "${litellm_local_port}" "127.0.0.1" "4000"
    start_ssh_tunnel "OPA" \
        "${opa_local_host}" "${opa_local_port}" "127.0.0.1" "8181"

    export EXTERNAL_API_TEST_LITELLM_BASE_URL="http://${litellm_local_host}:${litellm_local_port}/v1"
    export EXTERNAL_API_TEST_OPA_BASE_URL="http://${opa_local_host}:${opa_local_port}"
else
    export EXTERNAL_API_TEST_LITELLM_BASE_URL="${EXTERNAL_API_TEST_LITELLM_BASE_URL:-http://47.97.19.58/litellm/v1}"
    export EXTERNAL_API_TEST_OPA_BASE_URL="${EXTERNAL_API_TEST_OPA_BASE_URL:-http://47.97.19.58/opa}"
fi

export RUN_CLINICAL_SAFETY_API_EXTERNAL_TEST=true

if [[ "${upgrade_remote_database}" == "true" ]]; then
    echo "通过 SSH 隧道执行远程开发数据库迁移..."
    DATABASE_URL="${EXTERNAL_API_TEST_DATABASE_URL}" uv run alembic upgrade head
fi

echo "执行临床安全阶段 3 真实服务集成测试..."
uv run pytest tests/integration/test_clinical_safety_api_external.py -m integration -q "$@"

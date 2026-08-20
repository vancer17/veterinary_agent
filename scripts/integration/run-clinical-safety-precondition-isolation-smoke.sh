#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/integration/run-clinical-safety-precondition-isolation-smoke.sh
# 作用: 通过远程开发 LiteLLM 执行临床安全前提批量隔离真实评估。
# 范围: 仅建立 LiteLLM SSH 隧道、安全注入模型网关配置并运行独立 integration
#       测试，不访问数据库、RAG、OPA 或完整 Agent API。
# =============================================================================

set -Eeuo pipefail

ssh_host="${EXTERNAL_API_TEST_SSH_HOST:-47.97.19.58}"
ssh_port="${EXTERNAL_API_TEST_SSH_PORT:-22}"
ssh_user="${EXTERNAL_API_TEST_SSH_USER:-devlop}"
ssh_key="${EXTERNAL_API_TEST_SSH_KEY:-/home/vancer17/.ssh/AlibabaCloudLinux}"
litellm_local_host="${EXTERNAL_API_TEST_LITELLM_TUNNEL_LOCAL_HOST:-127.0.0.1}"
litellm_local_port="${EXTERNAL_API_TEST_LITELLM_TUNNEL_LOCAL_PORT:-54003}"
litellm_remote_host="${EXTERNAL_API_TEST_LITELLM_TUNNEL_REMOTE_HOST:-127.0.0.1}"
litellm_remote_port="${EXTERNAL_API_TEST_LITELLM_TUNNEL_REMOTE_PORT:-4000}"
tunnel_ready_seconds="${EXTERNAL_API_TEST_TUNNEL_READY_SECONDS:-2}"

ssh_tunnel_pid=""

cleanup() {
    if [[ -n "${ssh_tunnel_pid}" ]]; then
        kill "${ssh_tunnel_pid}" >/dev/null 2>&1 || true
        wait "${ssh_tunnel_pid}" >/dev/null 2>&1 || true
    fi
}

trap cleanup EXIT

ssh \
    -i "${ssh_key}" \
    -p "${ssh_port}" \
    -o StrictHostKeyChecking=no \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=2 \
    -N \
    -L "${litellm_local_host}:${litellm_local_port}:${litellm_remote_host}:${litellm_remote_port}" \
    "${ssh_user}@${ssh_host}" &
ssh_tunnel_pid="$!"
sleep "${tunnel_ready_seconds}"
if ! kill -0 "${ssh_tunnel_pid}" >/dev/null 2>&1; then
    wait "${ssh_tunnel_pid}" || true
    echo "SSH LiteLLM 隧道启动失败。" >&2
    exit 1
fi

if [[ -z "${EXTERNAL_API_TEST_LITELLM_API_KEY:-}" ]]; then
    EXTERNAL_API_TEST_LITELLM_API_KEY="$(
        ssh -i "${ssh_key}" -p "${ssh_port}" -o StrictHostKeyChecking=no \
            "${ssh_user}@${ssh_host}" \
            "sudo docker inspect vet-agent-dev-litellm --format '{{range .Config.Env}}{{println .}}{{end}}'" \
        | python3 -c 'import sys; values = dict(line.rstrip("\n").split("=", 1) for line in sys.stdin if "=" in line); print(values["LITELLM_MASTER_KEY"])'
    )"
fi
export EXTERNAL_API_TEST_LITELLM_API_KEY
export EXTERNAL_API_TEST_LITELLM_BASE_URL="http://${litellm_local_host}:${litellm_local_port}/v1"
export RUN_CLINICAL_SAFETY_PRECONDITION_ISOLATION_TEST=true

echo "执行临床安全前提批量隔离真实评估..."
uv run pytest \
    tests/integration/test_clinical_safety_precondition_isolation_external.py \
    -m integration \
    -q \
    -s \
    "$@"

#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/integration/run-input-preprocessing-shadow-smoke.sh
# 作用: 通过远程 LiteLLM 与 OPA 执行输入前置预处理 ideal/shadow 快速验证。
# 范围: 覆盖 A/B/C/D/E 样本、两阶段结构化分析、质量门禁、领域投影和行为模拟。
# 说明: 新链路仅输出评估报告，不写业务状态，不影响生产主路径；失败显式返回非零。
# =============================================================================

set -euo pipefail

ssh_host="${INPUT_PREPROCESSING_SSH_HOST:-47.97.19.58}"
ssh_port="${INPUT_PREPROCESSING_SSH_PORT:-22}"
ssh_user="${INPUT_PREPROCESSING_SSH_USER:-devlop}"
ssh_key="${INPUT_PREPROCESSING_SSH_KEY:-/home/vancer17/.ssh/AlibabaCloudLinux}"
litellm_local_port="${INPUT_PREPROCESSING_LITELLM_TUNNEL_PORT:-54003}"
opa_local_port="${INPUT_PREPROCESSING_OPA_TUNNEL_PORT:-58184}"
repeat="${INPUT_PREPROCESSING_REPEAT:-1}"
mode="${INPUT_PREPROCESSING_MODE:-both}"

if [[ -z "${INPUT_PREPROCESSING_LITELLM_API_KEY:-}" && -z "${LITELLM_API_KEY:-}" ]]; then
    echo "缺少 LiteLLM API Key：请设置 INPUT_PREPROCESSING_LITELLM_API_KEY。" >&2
    exit 1
fi

if [[ ! -f "${ssh_key}" ]]; then
    echo "SSH 密钥不存在: ${ssh_key}" >&2
    exit 1
fi

tunnel_pids=()
cleanup() {
    for pid in "${tunnel_pids[@]:-}"; do
        kill "${pid}" >/dev/null 2>&1 || true
        wait "${pid}" >/dev/null 2>&1 || true
    done
}
trap cleanup EXIT

start_tunnel() {
    local name="$1"
    local local_port="$2"
    local remote_host="$3"
    local remote_port="$4"
    ssh \
        -i "${ssh_key}" \
        -p "${ssh_port}" \
        -o StrictHostKeyChecking=no \
        -o ExitOnForwardFailure=yes \
        -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=2 \
        -N \
        -L "127.0.0.1:${local_port}:${remote_host}:${remote_port}" \
        "${ssh_user}@${ssh_host}" &
    local pid="$!"
    tunnel_pids+=("${pid}")
    sleep "${INPUT_PREPROCESSING_TUNNEL_READY_SECONDS:-2}"
    if ! kill -0 "${pid}" >/dev/null 2>&1; then
        echo "SSH ${name} 隧道启动失败。" >&2
        exit 1
    fi
}

start_tunnel "LiteLLM" "${litellm_local_port}" "127.0.0.1" "4000"
start_tunnel "OPA" "${opa_local_port}" "127.0.0.1" "8181"

opa_base="http://127.0.0.1:${opa_local_port}/v1"
policy_file="${INPUT_PREPROCESSING_OPA_POLICY_FILE:-docker/opa/policies/consultation_answerability.rego}"
if [[ ! -f "${policy_file}" ]]; then
    echo "OPA 策略文件不存在: ${policy_file}" >&2
    exit 1
fi

curl \
    -fsS \
    -X DELETE \
    "${opa_base}/policies/opa%2Fpolicies%2Fconsultation_answerability.rego" \
    >/dev/null 2>&1 || true

curl \
    -fsS \
    -X PUT \
    "${opa_base}/policies/consultation_answerability" \
    -H 'Content-Type: text/plain' \
    --data-binary "@${policy_file}" \
    >/dev/null

export INPUT_PREPROCESSING_LITELLM_BASE_URL="http://127.0.0.1:${litellm_local_port}/v1"
export INPUT_PREPROCESSING_OPA_BASE_URL="${opa_base}"

uv run python -m vet_agent.input_preprocessing.evaluation \
    --mode "${mode}" \
    --repeat "${repeat}" \
    --policy opa \
    --opa-base-url "${opa_base}" \
    "${@}"

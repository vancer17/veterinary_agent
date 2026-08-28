#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/integration/run-input-preprocessing-experiment-smoke.sh
# 作用: 通过远程 LiteLLM / OPA 执行“尚未证明”部分的专项 shadow 实验矩阵。
# 范围: 覆盖并列否定、主体歧义、非法 canonical、重复稳定性、决策分支、
#       临床安全结构对比和 canonical 词表治理审计。
# 说明: 实验结果只写评估报告，不改业务状态、不影响生产响应或临床安全主路径。
# =============================================================================

set -euo pipefail

ssh_host="${INPUT_PREPROCESSING_SSH_HOST:-47.97.19.58}"
ssh_port="${INPUT_PREPROCESSING_SSH_PORT:-22}"
ssh_user="${INPUT_PREPROCESSING_SSH_USER:-devlop}"
ssh_key="${INPUT_PREPROCESSING_SSH_KEY:-/home/vancer17/.ssh/AlibabaCloudLinux}"
litellm_local_port="${INPUT_PREPROCESSING_LITELLM_TUNNEL_PORT:-54004}"
opa_local_port="${INPUT_PREPROCESSING_OPA_TUNNEL_PORT:-58185}"

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
    local name="$1" local_port="$2" remote_host="$3" remote_port="$4"
    ssh -i "${ssh_key}" -p "${ssh_port}" \
        -o StrictHostKeyChecking=no -o ExitOnForwardFailure=yes \
        -o ServerAliveInterval=30 -o ServerAliveCountMax=2 -N \
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

start_tunnel LiteLLM "${litellm_local_port}" 127.0.0.1 4000
start_tunnel OPA "${opa_local_port}" 127.0.0.1 8181

opa_base="http://127.0.0.1:${opa_local_port}/v1"
policy_file="${INPUT_PREPROCESSING_OPA_POLICY_FILE:-docker/opa/policies/consultation_answerability.rego}"
curl -fsS -X DELETE \
    "${opa_base}/policies/opa%2Fpolicies%2Fconsultation_answerability.rego" \
    >/dev/null 2>&1 || true
curl -fsS -X PUT \
    "${opa_base}/policies/consultation_answerability" \
    -H 'Content-Type: text/plain' \
    --data-binary "@${policy_file}" >/dev/null

export INPUT_PREPROCESSING_LITELLM_BASE_URL="http://127.0.0.1:${litellm_local_port}/v1"
export INPUT_PREPROCESSING_OPA_BASE_URL="${opa_base}"

args=(
    --mode shadow
    --policy opa
    --opa-base-url "${opa_base}"
)
if [[ -n "${INPUT_PREPROCESSING_EXPERIMENTS:-}" ]]; then
    IFS=',' read -ra experiment_ids <<<"${INPUT_PREPROCESSING_EXPERIMENTS}"
    for experiment_id in "${experiment_ids[@]}"; do
        args+=(--experiment "${experiment_id}")
    done
fi

uv run python -m vet_agent.input_preprocessing.experiments \
    "${args[@]}" "${@}"

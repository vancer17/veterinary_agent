#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/integration/run-input-preprocessing-v3-experiment-smoke.sh
# 作用: 通过远程 LiteLLM 执行第三轮 input-preprocessing V3 架构 shadow 实验。
# 范围: 覆盖 Stage 1 拆分、derived count、item-keyed verifier、参与者继承、
#       constrained canonical linking、负例 gate、持久化异步 shadow 隔离和
#       clinical safety report-only 边界。
# 说明: 实验只写评估报告；不写业务状态、不调用临床安全 evaluator/OPA。
# =============================================================================

set -euo pipefail

ssh_host="${INPUT_PREPROCESSING_SSH_HOST:-}"
ssh_port="${INPUT_PREPROCESSING_SSH_PORT:-22}"
ssh_user="${INPUT_PREPROCESSING_SSH_USER}"
ssh_key="${INPUT_PREPROCESSING_SSH_KEY}"
litellm_local_port="${INPUT_PREPROCESSING_LITELLM_TUNNEL_PORT:-54007}"

if [[ -z "${INPUT_PREPROCESSING_LITELLM_API_KEY:-}" && -z "${LITELLM_API_KEY:-}" ]]; then
    echo "缺少 LiteLLM API Key：请设置 INPUT_PREPROCESSING_LITELLM_API_KEY。" >&2
    exit 1
fi
if [[ -z "${ssh_host}" || -z "${ssh_user}" || -z "${ssh_key}" ]]; then
    echo "缺少远程实验主机配置：请设置 INPUT_PREPROCESSING_SSH_HOST / SSH_USER / SSH_KEY。" >&2
    exit 1
fi

if [[ ! -f "${ssh_key}" ]]; then
    echo "SSH 密钥不存在: ${ssh_key}" >&2
    exit 1
fi

tunnel_pid=""
cleanup() {
    if [[ -n "${tunnel_pid}" ]]; then
        kill "${tunnel_pid}" >/dev/null 2>&1 || true
        wait "${tunnel_pid}" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

ssh -i "${ssh_key}" -p "${ssh_port}" \
    -o StrictHostKeyChecking=no -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 -o ServerAliveCountMax=2 -N \
    -L "127.0.0.1:${litellm_local_port}:127.0.0.1:4000" \
    "${ssh_user}@${ssh_host}" &
tunnel_pid=$!
sleep "${INPUT_PREPROCESSING_TUNNEL_READY_SECONDS:-2}"
if ! kill -0 "${tunnel_pid}" >/dev/null 2>&1; then
    echo "LiteLLM SSH 隧道启动失败。" >&2
    exit 1
fi

export INPUT_PREPROCESSING_LITELLM_BASE_URL="http://127.0.0.1:${litellm_local_port}/v1"

args=(
    --mode shadow
    --phase exploratory
)
if [[ "${INPUT_PREPROCESSING_WITH_CLINICAL_BASELINE:-false}" == "true" ]]; then
    args+=(--with-clinical-baseline)
fi
if [[ -n "${INPUT_PREPROCESSING_V3_EXPERIMENTS:-}" ]]; then
    IFS=',' read -ra experiment_ids <<<"${INPUT_PREPROCESSING_V3_EXPERIMENTS}"
    for experiment_id in "${experiment_ids[@]}"; do
        args+=(--experiment "${experiment_id}")
    done
fi

uv run python -m vet_agent.input_preprocessing.v3_experiments \
    "${args[@]}" "${@}"

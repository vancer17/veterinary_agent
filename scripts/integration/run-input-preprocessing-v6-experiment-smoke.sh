#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/integration/run-input-preprocessing-v6-experiment-smoke.sh
# 作用: 通过远程 LiteLLM 执行第六轮 input-preprocessing V6 薄声明批量富化 shadow。
# 范围: 覆盖独立意图路由、ThinUserClaim、quote 分层治理、per-claim graph、
#       candidate-only participant、确定性时间/度量解析、批量 Enrichment Planner、
#       canonical under-confirmation 诊断、负例 gate 和异步批量任务隔离。
# 说明: 实验只写评估报告；不写业务状态、不调用临床安全 evaluator/OPA。
# =============================================================================

set -euo pipefail

ssh_host="${INPUT_PREPROCESSING_SSH_HOST:-47.97.19.58}"
ssh_port="${INPUT_PREPROCESSING_SSH_PORT:-22}"
ssh_user="${INPUT_PREPROCESSING_SSH_USER:-devlop}"
ssh_key="${INPUT_PREPROCESSING_SSH_KEY:-/home/vancer17/.ssh/AlibabaCloudLinux}"
litellm_local_port="${INPUT_PREPROCESSING_LITELLM_TUNNEL_PORT:-54010}"

if [[ -z "${INPUT_PREPROCESSING_LITELLM_API_KEY:-}" && -z "${LITELLM_API_KEY:-}" ]]; then
    echo "缺少 LiteLLM API Key：请设置 INPUT_PREPROCESSING_LITELLM_API_KEY。" >&2
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

ssh -F /dev/null -i "${ssh_key}" -p "${ssh_port}" \
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
if [[ -n "${INPUT_PREPROCESSING_V6_EXPERIMENTS:-}" ]]; then
    IFS=',' read -ra experiment_ids <<<"${INPUT_PREPROCESSING_V6_EXPERIMENTS}"
    for experiment_id in "${experiment_ids[@]}"; do
        args+=(--experiment "${experiment_id}")
    done
fi

uv run python -m vet_agent.input_preprocessing.v6_experiments \
    "${args[@]}" "${@}"

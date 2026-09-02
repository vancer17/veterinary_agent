#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/integration/run-input-preprocessing-v10-remote-runner.sh
# 作用: 在远程 V10 独立环境中执行 boundary calibration / macro / relation /
#       regression / early-exit 探索性实验。
# 范围: report-only；不写业务状态，不调用临床安全 evaluator/OPA/required context，
#       默认拒绝 held-out，DSPy 保持冻结。
# =============================================================================

set -euo pipefail

ssh_host="${INPUT_PREPROCESSING_SSH_HOST:-}"
ssh_port="${INPUT_PREPROCESSING_SSH_PORT:-22}"
ssh_user="${INPUT_PREPROCESSING_SSH_USER:-}"
ssh_key="${INPUT_PREPROCESSING_SSH_KEY:-}"
remote_root="${INPUT_PREPROCESSING_V10_REMOTE_ROOT:-}"
remote_timeout_seconds="${INPUT_PREPROCESSING_V10_REMOTE_TIMEOUT_SECONDS:-900}"


: "${ssh_host:?INPUT_PREPROCESSING_SSH_HOST is required}"
: "${ssh_user:?INPUT_PREPROCESSING_SSH_USER is required}"
: "${ssh_key:?INPUT_PREPROCESSING_SSH_KEY is required}"
: "${remote_root:?remote repository path is required}"
if [[ ! -f "${ssh_key}" ]]; then
    echo "SSH key not found: ${ssh_key}" >&2
    exit 1
fi

remote_args=""
for argument in "$@"; do
    remote_args+=" $(printf '%q' "$argument")"
done

remote_command="
set -euo pipefail
cd $(printf '%q' "${remote_root}")
set -a
source .env.v10.local
set +a
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=\${OMP_NUM_THREADS:-4}
export MKL_NUM_THREADS=\${MKL_NUM_THREADS:-4}
export INPUT_PREPROCESSING_TIMEOUT_SECONDS=\${INPUT_PREPROCESSING_V10_REQUEST_TIMEOUT_SECONDS:-120}
export NO_PROXY=127.0.0.1,localhost
export no_proxy=127.0.0.1,localhost
timeout --signal=TERM --kill-after=15s ${remote_timeout_seconds}s \\
  .venv-v10/bin/python -m vet_agent.input_preprocessing.v10_experiments${remote_args}
"

ssh -F /dev/null \
    -i "${ssh_key}" \
    -p "${ssh_port}" \
    -o StrictHostKeyChecking=no \
    "${ssh_user}@${ssh_host}" \
    "$remote_command"

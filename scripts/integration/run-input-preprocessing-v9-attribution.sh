#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/integration/run-input-preprocessing-v9-attribution.sh
# 作用: 在远程 V8 环境中执行第九轮专项归因实验。
# 范围: 仅执行 development fixture 上的 V9 attribution runner；不写业务状态，
#       不调用临床安全 evaluator/OPA/required_context，不读取 held-out。
# =============================================================================

set -euo pipefail

ssh_host="${INPUT_PREPROCESSING_SSH_HOST:-}"
ssh_port="${INPUT_PREPROCESSING_SSH_PORT:-22}"
ssh_user="${INPUT_PREPROCESSING_SSH_USER:-}"
ssh_key="${INPUT_PREPROCESSING_SSH_KEY:-}"
remote_root="${INPUT_PREPROCESSING_V8_REMOTE_ROOT:-}"

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
source .env.v8.local
set +a
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4
export NO_PROXY=127.0.0.1,localhost
export no_proxy=127.0.0.1,localhost
.venv-v8/bin/python -m vet_agent.input_preprocessing.v9_experiments${remote_args}
"

ssh -F /dev/null \
    -i "${ssh_key}" \
    -p "${ssh_port}" \
    -o StrictHostKeyChecking=no \
    "${ssh_user}@${ssh_host}" \
    "$remote_command"

#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/integration/deploy-input-preprocessing-v14-remote.sh
# 作用: 将 V14 one-pass 治理收敛实现部署到远程开发服务器并执行基础验证。
# 范围: 只同步 V14 代码、Qwen metadata client、测试和 runner；不修改密钥。
# =============================================================================

set -euo pipefail

ssh_host="${INPUT_PREPROCESSING_SSH_HOST:-}"
ssh_port="${INPUT_PREPROCESSING_SSH_PORT:-22}"
ssh_user="${INPUT_PREPROCESSING_SSH_USER:-}"
ssh_key="${INPUT_PREPROCESSING_SSH_KEY:-}"
remote_root="${INPUT_PREPROCESSING_V14_REMOTE_ROOT:-}"

: "${ssh_host:?INPUT_PREPROCESSING_SSH_HOST is required}"
: "${ssh_user:?INPUT_PREPROCESSING_SSH_USER is required}"
: "${ssh_key:?INPUT_PREPROCESSING_SSH_KEY is required}"
: "${remote_root:?remote repository path is required}"

if [[ ! -f "${ssh_key}" ]]; then
    echo "SSH key not found: ${ssh_key}" >&2
    exit 1
fi

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

tar -czf - \
  src/vet_agent/runtime/qwen.py \
  src/vet_agent/input_preprocessing/v14_contracts.py \
  src/vet_agent/input_preprocessing/v14_intent.py \
  src/vet_agent/input_preprocessing/v14_generation_options.py \
  src/vet_agent/input_preprocessing/v14_prompt_skills.py \
  src/vet_agent/input_preprocessing/v14_alignment.py \
  src/vet_agent/input_preprocessing/v14_participant_resolver.py \
  src/vet_agent/input_preprocessing/v14_canonical_selector.py \
  src/vet_agent/input_preprocessing/v14_governance.py \
  src/vet_agent/input_preprocessing/v14_experiments.py \
  tests/test_input_preprocessing_v14.py \
  scripts/integration/run-input-preprocessing-v14-remote-runner.sh \
| ssh -F /dev/null \
    -i "${ssh_key}" \
    -p "${ssh_port}" \
    -o StrictHostKeyChecking=no \
    "${ssh_user}@${ssh_host}" "
set -euo pipefail
cd $(printf '%q' "${remote_root}")
tar -xzf -
chmod +x scripts/integration/run-input-preprocessing-v14-remote-runner.sh
.venv-v11/bin/python -m compileall -q src/vet_agent/runtime/qwen.py src/vet_agent/input_preprocessing/v14_*.py
.venv-v11/bin/python -m ruff check src/vet_agent/input_preprocessing/v14_*.py tests/test_input_preprocessing_v14.py
.venv-v11/bin/python -m mypy src/vet_agent/input_preprocessing/v14_*.py
.venv-v11/bin/python -m pytest tests/test_input_preprocessing_v14.py -q
"

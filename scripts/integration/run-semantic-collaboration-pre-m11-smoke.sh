#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/integration/run-semantic-collaboration-pre-m11-smoke.sh
# 作用: 建立 Pre-M11 语义协作集成测试环境并执行真实外部服务测试。
# 范围: 覆盖 LiteLLM、Temporal、PostgreSQL 配置检查、可选 SSH tunnel、
#       integration marker 测试执行与显式语义矩阵开启。
# 说明: 不自动重试失败测试，不在服务不可用时回退 mock，不将密钥写入报告。
# =============================================================================

set -euo pipefail

usage() {
  cat <<'USAGE'
用法: run-semantic-collaboration-pre-m11-smoke.sh [选项] [-- pytest 参数]

选项:
  --semantic        额外开启真实模型语义验证矩阵
  --full-pre-m11    执行当前已实现的 semantic suite，等价于 --semantic
  --ssh-tunnel      通过 SSH 建立 LiteLLM / Temporal / PostgreSQL tunnel
  -h, --help        显示帮助

环境变量:
  无 tunnel 时必须显式提供:
    EXTERNAL_SEMANTIC_TEST_LITELLM_BASE_URL
    EXTERNAL_SEMANTIC_TEST_LITELLM_API_KEY
    EXTERNAL_SEMANTIC_TEST_MODEL
    EXTERNAL_SEMANTIC_TEST_TEMPORAL_ADDRESS
    EXTERNAL_SEMANTIC_TEST_TEMPORAL_NAMESPACE
    EXTERNAL_SEMANTIC_TEST_TEMPORAL_TASK_QUEUE
    EXTERNAL_SEMANTIC_TEST_DATABASE_URL

  --ssh-tunnel 时必须提供:
    SEMANTIC_TEST_SSH_KEY
    SEMANTIC_TEST_SSH_USER
    SEMANTIC_TEST_SSH_HOST
USAGE
}

semantic_enabled=false
ssh_tunnel_enabled=false
pytest_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --semantic|--full-pre-m11)
      semantic_enabled=true
      shift
      ;;
    --ssh-tunnel)
      ssh_tunnel_enabled=true
      shift
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        pytest_args+=("$1")
        shift
      done
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf '未知参数: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "${semantic_enabled}" == true ]]; then
  export RUN_SEMANTIC_COLLABORATION_SEMANTIC_TEST=true
else
  export RUN_SEMANTIC_COLLABORATION_SEMANTIC_TEST="${RUN_SEMANTIC_COLLABORATION_SEMANTIC_TEST:-false}"
fi

tunnel_pids=()

cleanup() {
  local pid
  for pid in "${tunnel_pids[@]:-}"; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
      wait "${pid}" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT INT TERM

start_tunnel() {
  local name="$1"
  local local_host="$2"
  local local_port="$3"
  local remote_host="$4"
  local remote_port="$5"
  local pid

  ssh -i "${SEMANTIC_TEST_SSH_KEY}" \
    -N \
    -L "${local_host}:${local_port}:${remote_host}:${remote_port}" \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    "${SEMANTIC_TEST_SSH_USER}@${SEMANTIC_TEST_SSH_HOST}" \
    >/dev/null 2>&1 &
  pid=$!
  tunnel_pids+=("${pid}")

  local ready=false
  local attempt
    for attempt in $(seq 1 30); do
    if kill -0 "${pid}" 2>/dev/null && python3 - <<PY 2>/dev/null
import socket

sock = socket.socket()
sock.settimeout(0.5)
try:
    sock.connect(("${local_host}", ${local_port}))
finally:
    sock.close()
PY
    then
      ready=true
      break
    fi
    sleep 1
  done

  if [[ "${ready}" != true ]]; then
    printf '%s SSH tunnel 未就绪。\n' "${name}" >&2
    exit 1
  fi
  printf '%s SSH tunnel 已就绪: %s:%s\n' "${name}" "${local_host}" "${local_port}"
}

require_environment() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    printf '缺少环境变量: %s\n' "${name}" >&2
    exit 1
  fi
}

if [[ "${ssh_tunnel_enabled}" == true ]]; then
  require_environment EXTERNAL_SEMANTIC_TEST_DATABASE_URL
  require_environment SEMANTIC_TEST_SSH_KEY
  require_environment SEMANTIC_TEST_SSH_USER
  require_environment SEMANTIC_TEST_SSH_HOST

  litellm_host="${SEMANTIC_TEST_LITELLM_TUNNEL_LOCAL_HOST:-127.0.0.1}"
  litellm_port="${SEMANTIC_TEST_LITELLM_TUNNEL_LOCAL_PORT:-15400}"
  temporal_host="${SEMANTIC_TEST_TEMPORAL_TUNNEL_LOCAL_HOST:-127.0.0.1}"
  temporal_port="${SEMANTIC_TEST_TEMPORAL_TUNNEL_LOCAL_PORT:-17233}"
  postgres_host="${SEMANTIC_TEST_POSTGRES_TUNNEL_LOCAL_HOST:-127.0.0.1}"
  postgres_port="${SEMANTIC_TEST_POSTGRES_TUNNEL_LOCAL_PORT:-15432}"

  start_tunnel "LiteLLM" "${litellm_host}" "${litellm_port}" \
    "${SEMANTIC_TEST_LITELLM_TUNNEL_REMOTE_HOST:-127.0.0.1}" \
    "${SEMANTIC_TEST_LITELLM_TUNNEL_REMOTE_PORT:-4000}"
  start_tunnel "Temporal" "${temporal_host}" "${temporal_port}" \
    "${SEMANTIC_TEST_TEMPORAL_TUNNEL_REMOTE_HOST:-127.0.0.1}" \
    "${SEMANTIC_TEST_TEMPORAL_TUNNEL_REMOTE_PORT:-7233}"
  start_tunnel "PostgreSQL" "${postgres_host}" "${postgres_port}" \
    "${SEMANTIC_TEST_POSTGRES_TUNNEL_REMOTE_HOST:-127.0.0.1}" \
    "${SEMANTIC_TEST_POSTGRES_TUNNEL_REMOTE_PORT:-5432}"

  export EXTERNAL_SEMANTIC_TEST_LITELLM_BASE_URL="http://${litellm_host}:${litellm_port}/v1"
  export EXTERNAL_SEMANTIC_TEST_TEMPORAL_ADDRESS="${temporal_host}:${temporal_port}"
  export EXTERNAL_SEMANTIC_TEST_DATABASE_URL="$(
    SEMANTIC_TEST_DB_HOST="${postgres_host}" \
    SEMANTIC_TEST_DB_PORT="${postgres_port}" \
    python3 - <<'PY'
import os
from urllib.parse import urlsplit, urlunsplit

source = os.environ.get("EXTERNAL_SEMANTIC_TEST_DATABASE_URL", "")
parts = urlsplit(source)
netloc_parts = parts.netloc.rsplit("@", 1)
userinfo = f"{netloc_parts[0]}@" if len(netloc_parts) == 2 else ""
netloc = f"{userinfo}{os.environ['SEMANTIC_TEST_DB_HOST']}:{os.environ['SEMANTIC_TEST_DB_PORT']}"
print(urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment)))
PY
  )"
fi

require_environment EXTERNAL_SEMANTIC_TEST_LITELLM_BASE_URL
require_environment EXTERNAL_SEMANTIC_TEST_LITELLM_API_KEY
require_environment EXTERNAL_SEMANTIC_TEST_TEMPORAL_ADDRESS
require_environment EXTERNAL_SEMANTIC_TEST_TEMPORAL_NAMESPACE
require_environment EXTERNAL_SEMANTIC_TEST_TEMPORAL_TASK_QUEUE
require_environment EXTERNAL_SEMANTIC_TEST_DATABASE_URL

export EXTERNAL_SEMANTIC_TEST_MODEL="${EXTERNAL_SEMANTIC_TEST_MODEL:-qwen-plus}"
export EXTERNAL_SEMANTIC_TEST_MODEL_TIMEOUT_SECONDS="${EXTERNAL_SEMANTIC_TEST_MODEL_TIMEOUT_SECONDS:-60}"
export RUN_SEMANTIC_COLLABORATION_EXTERNAL_TEST=true

printf '检查 LiteLLM 模型列表...\n'
litellm_base="${EXTERNAL_SEMANTIC_TEST_LITELLM_BASE_URL%/}"
litellm_http_code="$(
  curl -sS \
    -o /dev/null \
    -w '%{http_code}' \
    -H "Authorization: Bearer ${EXTERNAL_SEMANTIC_TEST_LITELLM_API_KEY}" \
    "${litellm_base}/models"
)"
if [[ "${litellm_http_code}" != "200" ]]; then
  printf 'LiteLLM 模型列表检查失败: HTTP %s\n' "${litellm_http_code}" >&2
  exit 1
fi

if [[ ${#pytest_args[@]} -eq 0 ]]; then
  pytest_args=(
    "tests/integration/test_semantic_collaboration_pre_m11_external.py"
  )
elif [[ "${pytest_args[0]}" == -* ]]; then
  pytest_args=(
    "tests/integration/test_semantic_collaboration_pre_m11_external.py"
    "${pytest_args[@]}"
  )
fi

uv run pytest "${pytest_args[@]}" -m integration -q

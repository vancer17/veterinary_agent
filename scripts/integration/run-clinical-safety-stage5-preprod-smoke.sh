#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/integration/run-clinical-safety-stage5-preprod-smoke.sh
# 作用: 通过 SSH 隧道执行临床安全阶段 5 预发布黑盒冒烟。
# 范围: 只访问真实预发布 app API，不修改数据库、不同步策略、不部署镜像。
# 说明: API key 从预发布容器安全注入进程环境，不写入文件、不输出到终端。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

ssh_host="${CLINICAL_SAFETY_STAGE5_SSH_HOST:-121.41.58.20}"
ssh_port="${CLINICAL_SAFETY_STAGE5_SSH_PORT:-22}"
ssh_user="${CLINICAL_SAFETY_STAGE5_SSH_USER:-deploy}"
ssh_key="${CLINICAL_SAFETY_STAGE5_SSH_KEY:-/home/vancer17/.ssh/infra-ci-deploy}"
deploy_path="${CLINICAL_SAFETY_STAGE5_DEPLOY_PATH:-/opt/vancer-saas/veterinary_agent-preprod}"
compose_project="${CLINICAL_SAFETY_STAGE5_COMPOSE_PROJECT:-vet-agent-preprod}"
remote_app_port="${CLINICAL_SAFETY_STAGE5_REMOTE_APP_PORT:-18000}"
local_host="${CLINICAL_SAFETY_STAGE5_TUNNEL_LOCAL_HOST:-127.0.0.1}"
local_port="${CLINICAL_SAFETY_STAGE5_TUNNEL_LOCAL_PORT:-18080}"
tunnel_ready_seconds="${CLINICAL_SAFETY_STAGE5_TUNNEL_READY_SECONDS:-2}"
run_id="${CLINICAL_SAFETY_STAGE5_RUN_ID:-stage5-$(date +%Y%m%d%H%M%S)-$(python3 -c 'import uuid; print(uuid.uuid4().hex[:8])')}"

if [[ ! -f "$ssh_key" ]]; then
    echo "SSH 私钥不存在: ${ssh_key}" >&2
    exit 1
fi

if [[ -z "${CLINICAL_SAFETY_STAGE5_API_KEY:-}" ]]; then
    CLINICAL_SAFETY_STAGE5_API_KEY="$(
        ssh \
            -i "$ssh_key" \
            -p "$ssh_port" \
            -o BatchMode=yes \
            -o StrictHostKeyChecking=no \
            "${ssh_user}@${ssh_host}" \
            "cd ${deploy_path}/docker && sudo docker compose --project-name ${compose_project} --env-file compose.prod.env --env-file compose.stage5-release.env -f compose.yml exec -T app sh -c 'printf \"%s\n\" \"\$VET_AGENT_API_KEYS\"'"
    )" || true
    CLINICAL_SAFETY_STAGE5_API_KEY="$(
        python3 - "$CLINICAL_SAFETY_STAGE5_API_KEY" <<'PY'
import sys

value = sys.argv[1].strip()
if not value:
    raise SystemExit("no-api-key")
print(value.split(",", 1)[0].strip())
PY
    )"
fi
export CLINICAL_SAFETY_STAGE5_API_KEY

ssh_tunnel_pid=""
cleanup() {
    if [[ -n "$ssh_tunnel_pid" ]]; then
        kill "$ssh_tunnel_pid" >/dev/null 2>&1 || true
        wait "$ssh_tunnel_pid" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

ssh \
    -i "$ssh_key" \
    -p "$ssh_port" \
    -o BatchMode=yes \
    -o StrictHostKeyChecking=no \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=2 \
    -N \
    -L "${local_host}:${local_port}:127.0.0.1:${remote_app_port}" \
    "${ssh_user}@${ssh_host}" &
ssh_tunnel_pid="$!"
sleep "$tunnel_ready_seconds"
if ! kill -0 "$ssh_tunnel_pid" >/dev/null 2>&1; then
    wait "$ssh_tunnel_pid" || true
    echo "阶段 5 预发布 SSH 隧道启动失败。" >&2
    exit 1
fi

CLINICAL_SAFETY_STAGE5_BASE_URL="${CLINICAL_SAFETY_STAGE5_BASE_URL:-http://${local_host}:${local_port}}" \
CLINICAL_SAFETY_STAGE5_RUN_ID="$run_id" \
    uv run python scripts/integration/clinical_safety_stage5_preprod_smoke.py "$@"

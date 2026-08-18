#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/cd/repository/deploy-production.sh
# 作用: 在生产服务器拉取 GitHub Release 对应镜像并执行正式环境部署。
# 范围: 通过 SSH 执行 docker compose pull、迁移和 up --no-build，不在生产环境构建镜像。
# 说明: 生产真实 env 文件必须已由人工或运维基线下发，本脚本不会生成密钥配置。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"

eval "$(bash scripts/cd/common/resolve-release-images.sh)"

ssh_host="${CD_PROD_SSH_HOST:-}"
ssh_user="${CD_PROD_SSH_USER:-}"
ssh_port="${CD_PROD_SSH_PORT:-22}"
ssh_key_path="${CD_PROD_SSH_KEY_PATH:-}"
deploy_path="${CD_PROD_DEPLOY_PATH:-/opt/vancer-saas/veterinary_agent}"
registry_host="${CD_REGISTRY_HOST:-crpi-efmmpn9a6t9mspwy.cn-hangzhou.personal.cr.aliyuncs.com}"
registry_username="${CD_REGISTRY_USERNAME:-}"
registry_password="${CD_REGISTRY_PASSWORD:-}"
run_migration="${CD_RUN_MIGRATION:-true}"

if [ -z "$ssh_host" ] || [ -z "$ssh_user" ]; then
    echo "缺少 CD_PROD_SSH_HOST 或 CD_PROD_SSH_USER。" >&2
    exit 1
fi

if [ -z "$registry_username" ] || [ -z "$registry_password" ]; then
    echo "缺少 CD_REGISTRY_USERNAME 或 CD_REGISTRY_PASSWORD，生产服务器无法拉取私有镜像。" >&2
    exit 1
fi

ssh_args=(-p "$ssh_port" -o BatchMode=yes)
if [ -n "$ssh_key_path" ]; then
    ssh_args+=(-i "$ssh_key_path")
fi

remote="${ssh_user}@${ssh_host}"

# 生产服务器只负责拉取镜像；Registry 登录密码通过 SSH 标准输入传递，不进入远端命令参数。
printf '%s' "$registry_password" | ssh "${ssh_args[@]}" "$remote" \
    "docker login $(printf '%q' "$registry_host") --username $(printf '%q' "$registry_username") --password-stdin" \
    >/dev/null

remote_env=(
    "CD_PROD_DEPLOY_PATH=$(printf '%q' "$deploy_path")"
    "CD_APP_IMAGE=$(printf '%q' "$CD_APP_IMAGE")"
    "CD_MEM0_IMAGE=$(printf '%q' "$CD_MEM0_IMAGE")"
    "CD_MEM0_DASHBOARD_IMAGE=$(printf '%q' "$CD_MEM0_DASHBOARD_IMAGE")"
    "CD_OPA_IMAGE=$(printf '%q' "$CD_OPA_IMAGE")"
    "CD_RUN_MIGRATION=$(printf '%q' "$run_migration")"
)

ssh "${ssh_args[@]}" "$remote" "${remote_env[*]} bash -s" <<'REMOTE'
set -Eeuo pipefail

cd "$CD_PROD_DEPLOY_PATH"

if ! docker compose version >/dev/null 2>&1; then
    echo "生产服务器缺少 Docker Compose v2。" >&2
    exit 1
fi

required_real_env_files=(
    docker/compose.prod.env
    docker/app/template/app.prod.env
    docker/litellm/template/litellm.prod.env
    docker/mem0/template/mem0.prod.env
    docker/mem0-dashboard/template/mem0-dashboard.prod.env
    docker/opa/template/opa.prod.env
    docker/postgres/template/postgres.prod.env
)

for required_file in "${required_real_env_files[@]}"; do
    if [ ! -f "$required_file" ]; then
        echo "生产服务器缺少真实 env 文件: ${required_file}" >&2
        exit 1
    fi
done

export VET_AGENT_IMAGE="$CD_APP_IMAGE"
export MEM0_IMAGE="$CD_MEM0_IMAGE"
export MEM0_DASHBOARD_IMAGE="$CD_MEM0_DASHBOARD_IMAGE"
export OPA_IMAGE="$CD_OPA_IMAGE"

compose_cmd=(
    docker compose
    --env-file docker/compose.prod.env
    -f docker/compose.yml
)

# 先解析生产编排，确保 Release tag 注入的镜像和真实 env 文件足以渲染完整运行拓扑。
"${compose_cmd[@]}" config --quiet

lock_dir=".deploy-lock"
if ! mkdir "$lock_dir" 2>/dev/null; then
    echo "检测到已有部署任务正在执行，拒绝并发部署。" >&2
    exit 1
fi
trap 'rmdir "$lock_dir" >/dev/null 2>&1 || true' EXIT

# 拉取发布镜像后再启动服务；app 与 worker 复用同一个预编译镜像。
"${compose_cmd[@]}" pull app worker mem0 mem0-dashboard opa
"${compose_cmd[@]}" up -d --no-build --pull missing --wait postgres litellm mem0 opa

case "$CD_RUN_MIGRATION" in
    1|true|TRUE|yes|YES|on|ON)
        "${compose_cmd[@]}" run --rm --pull never migrate
        ;;
    *)
        echo "跳过 Alembic 迁移：CD_RUN_MIGRATION 未启用。"
        ;;
esac

# 数据库迁移完成后再同时启动 API 与后台 worker，避免 worker 抢先访问尚未创建的任务表。
"${compose_cmd[@]}" up -d --no-build --pull never --no-deps --force-recreate --wait app worker
"${compose_cmd[@]}" ps
REMOTE

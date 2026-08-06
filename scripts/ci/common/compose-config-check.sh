#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/ci/common/compose-config-check.sh
# 作用: 校验开发与生产 Docker Compose 拓扑能被完整解析。
# 范围: 只执行 compose config 静态解析，不启动容器、不读取真实生产密钥。
# 说明: 默认文件名适配本仓库；其他业务仓库可通过 CI_DEV_COMPOSE_FILE 等变量覆盖。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"

if ! docker compose version >/dev/null 2>&1; then
    echo "缺少 docker compose。请在具备 Docker Compose v2 的环境中执行本检查。" >&2
    exit 1
fi

dev_env_file="${CI_DEV_COMPOSE_ENV_FILE:-docker/compose.dev.env.template}"
dev_compose_file="${CI_DEV_COMPOSE_FILE:-docker/compose.dev.yml}"
prod_env_file="${CI_PROD_COMPOSE_ENV_FILE:-docker/compose.prod.env.template}"
prod_compose_file="${CI_PROD_COMPOSE_FILE:-docker/compose.yml}"
prod_app_image="${CI_PROD_APP_IMAGE:-crpi-efmmpn9a6t9mspwy.cn-hangzhou.personal.cr.aliyuncs.com/vancer-saas/veterinary_agent:ci-config}"
prod_mem0_image="${CI_PROD_MEM0_IMAGE:-crpi-efmmpn9a6t9mspwy.cn-hangzhou.personal.cr.aliyuncs.com/vancer-saas/veterinary_agent-mem0:ci-config}"

# 开发拓扑覆盖端口映射、源码挂载、自动迁移与 seed 启动链路。
docker compose \
    --env-file "$dev_env_file" \
    -f "$dev_compose_file" \
    config --quiet

# 生产拓扑覆盖无源码挂载、显式 migrate/seed 运维任务与受控启动顺序。
VET_AGENT_IMAGE="$prod_app_image" \
MEM0_IMAGE="$prod_mem0_image" \
docker compose \
    --env-file "$prod_env_file" \
    -f "$prod_compose_file" \
    config --quiet

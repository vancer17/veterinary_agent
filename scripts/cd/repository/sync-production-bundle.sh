#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/cd/repository/sync-production-bundle.sh
# 作用: 将正式环境运行编排文件同步到生产服务器部署目录。
# 范围: 只同步 docker/ 下的正式编排、服务配置、env 模板和挂载脚本，不同步真实 env 密钥。
# 说明: 生产服务器使用同步后的 yml 与本地真实 env 文件拉取 GitHub Release 镜像。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"

ssh_host="${CD_PROD_SSH_HOST:-}"
ssh_user="${CD_PROD_SSH_USER:-}"
ssh_port="${CD_PROD_SSH_PORT:-22}"
ssh_key_path="${CD_PROD_SSH_KEY_PATH:-}"
deploy_path="${CD_PROD_DEPLOY_PATH:-/opt/vancer-saas/veterinary_agent}"

if [ -z "$ssh_host" ] || [ -z "$ssh_user" ]; then
    echo "缺少 CD_PROD_SSH_HOST 或 CD_PROD_SSH_USER。" >&2
    exit 1
fi

ssh_args=(-p "$ssh_port" -o BatchMode=yes)
if [ -n "$ssh_key_path" ]; then
    ssh_args+=(-i "$ssh_key_path")
fi

remote="${ssh_user}@${ssh_host}"
quoted_deploy_path="$(printf '%q' "$deploy_path")"

bundle_paths=(
    docker/compose.yml
    docker/compose.prod.env.template
    docker/app/template/app.prod.env.template
    docker/litellm/template/litellm.prod.env.template
    docker/mem0/template/mem0.prod.env.template
    docker/mem0-dashboard/template/mem0-dashboard.prod.env.template
    docker/postgres/template/postgres.prod.env.template
    docker/litellm/litellm.yml
    docker/mem0/application.yml
    docker/mem0-dashboard/application.yml
    docker/postgres/postgresql.conf
    docker/postgres/init/10-bootstrap-logical-databases.sh
    docker/postgres/ops/ensure-extensions.sh
    docker/postgres/ops/vector-smoke-check.sh
)

for bundle_path in "${bundle_paths[@]}"; do
    if [ ! -e "$bundle_path" ]; then
        echo "生产编排包缺少必要文件: ${bundle_path}" >&2
        exit 1
    fi
done

# 只打包可审查的 yml、模板和挂载脚本；真实 *.env 文件必须保留在生产服务器本地。
tar -czf - "${bundle_paths[@]}" | ssh "${ssh_args[@]}" "$remote" \
    "mkdir -p ${quoted_deploy_path} && tar -xzf - -C ${quoted_deploy_path}"

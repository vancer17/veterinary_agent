#!/usr/bin/env bash
# =============================================================================
# 文件: docker/mem0-dashboard/entrypoint.sh
# 作用: 作为 Mem0 运维 Dashboard 镜像的统一启动入口。
# 范围: 渲染 application.yml、替换 Next.js 公开变量占位符并启动 standalone server。
# 说明: 本脚本在镜像构建期复制进入容器；生产 Compose 不挂载脚本，不现场编译镜像。
# =============================================================================

set -Eeuo pipefail

if [ "${1:-serve}" != "serve" ]; then
    exec "$@"
fi

config_file="${MEM0_DASHBOARD_CONFIG_FILE:-/app/config/application.yml}"

if [ ! -f "$config_file" ]; then
    echo "Mem0 Dashboard 配置文件不存在: ${config_file}" >&2
    exit 1
fi

# 将非敏感服务配置从 application.yml 渲染为上游 Dashboard 当前识别的环境变量。
eval "$(python3 /opt/vet-agent-mem0-dashboard/render_env.py "$config_file")"

cd /app

# Next.js 会在构建阶段内联 NEXT_PUBLIC_* 变量。镜像构建时写入同名占位符，
# 容器启动时再替换为 application.yml 渲染出的真实值，避免为不同环境重复构建镜像。
while IFS='=' read -r key value; do
    escaped="$(printf '%s' "$value" | sed -e 's/[\\&|]/\\&/g')"
    find .next -type f -exec sed -i "s|${key}|${escaped}|g" {} +
done < <(env | grep '^NEXT_PUBLIC_')

exec node server.js

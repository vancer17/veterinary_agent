#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/cd/common/docker-login.sh
# 作用: 登录 CD 使用的私有 Docker Registry。
# 范围: 只处理镜像仓库认证，不构建镜像、不推送镜像、不连接生产环境。
# 说明: 通过标准输入传递密码，避免在命令参数和日志中暴露敏感值。
# =============================================================================

set -Eeuo pipefail

registry_host="${CD_REGISTRY_HOST:-crpi-efmmpn9a6t9mspwy.cn-hangzhou.personal.cr.aliyuncs.com}"
registry_username="${CD_REGISTRY_USERNAME:-}"
registry_password="${CD_REGISTRY_PASSWORD:-}"

if [ -z "$registry_host" ]; then
    echo "缺少 CD_REGISTRY_HOST。" >&2
    exit 1
fi

if [ -z "$registry_username" ] || [ -z "$registry_password" ]; then
    echo "缺少 CD_REGISTRY_USERNAME 或 CD_REGISTRY_PASSWORD。" >&2
    exit 1
fi

printf '%s' "$registry_password" | docker login "$registry_host" --username "$registry_username" --password-stdin >/dev/null

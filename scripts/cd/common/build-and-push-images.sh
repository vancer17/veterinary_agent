#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/cd/common/build-and-push-images.sh
# 作用: 在 CD Runner 构建并推送 GitHub Release 对应的预编译镜像。
# 范围: 构建主应用镜像和可选 Mem0 薄封装镜像，不连接生产服务器。
# 说明: 生产环境只拉取本脚本推送的镜像，禁止在生产环境现场编译。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"

if ! docker version >/dev/null 2>&1; then
    echo "缺少可用 Docker daemon，无法构建发布镜像。" >&2
    exit 1
fi

bash scripts/cd/common/docker-login.sh
eval "$(bash scripts/cd/common/resolve-release-images.sh)"

release_tag="${CD_RELEASE_TAG}"
release_sha="${CD_RELEASE_SHA}"
app_dockerfile="${CD_APP_DOCKERFILE:-docker/app/Dockerfile}"
mem0_dockerfile="${CD_MEM0_DOCKERFILE:-docker/mem0/Dockerfile}"
mem0_base_image="${CD_MEM0_BASE_IMAGE:-crpi-efmmpn9a6t9mspwy.cn-hangzhou.personal.cr.aliyuncs.com/vancer-saas/mem0:latest}"

app_release_image="${CD_APP_IMAGE}"
mem0_release_image="${CD_MEM0_IMAGE}"

# 主应用镜像只使用 GitHub Release tag 作为可部署版本标签。
docker build \
    -f "$app_dockerfile" \
    --label "org.opencontainers.image.version=${release_tag}" \
    --label "org.opencontainers.image.revision=${release_sha}" \
    -t "$app_release_image" \
    .
docker push "$app_release_image"

case "${CD_BUILD_MEM0_IMAGE:-true}" in
    1|true|TRUE|yes|YES|on|ON)
        # Mem0 薄封装镜像也只使用同一个 GitHub Release tag。
        docker build \
            -f "$mem0_dockerfile" \
            --build-arg "MEM0_BASE_IMAGE=${mem0_base_image}" \
            --label "org.opencontainers.image.version=${release_tag}" \
            --label "org.opencontainers.image.revision=${release_sha}" \
            -t "$mem0_release_image" \
            .
        docker push "$mem0_release_image"
        ;;
    *)
        echo "跳过 Mem0 镜像构建：CD_BUILD_MEM0_IMAGE 未启用。"
        ;;
esac

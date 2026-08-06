#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/ci/common/docker-build-check.sh
# 作用: 构建生产应用镜像，验证 Dockerfile 与冻结依赖可生成运行产物。
# 范围: 默认只构建主应用镜像；附属私有基础镜像由业务仓库按需启用。
# 说明: 其他业务仓库可通过 CI_APP_DOCKERFILE、CI_APP_IMAGE_TAG 等变量复用本脚本。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"

if ! docker version >/dev/null 2>&1; then
    echo "缺少可用 Docker daemon。请在具备 Docker 构建能力的环境中执行本检查。" >&2
    exit 1
fi

app_dockerfile="${CI_APP_DOCKERFILE:-docker/app/Dockerfile}"
app_image_tag="${CI_APP_IMAGE_TAG:-vet-agent-ci-app:${GITHUB_SHA:-local}}"
mem0_image_tag="${CI_MEM0_IMAGE_TAG:-vet-agent-ci-mem0:${GITHUB_SHA:-local}}"

# 主应用镜像是业务仓库的核心交付物，PR 与主干门禁必须保证其可构建。
docker build \
    -f "$app_dockerfile" \
    -t "$app_image_tag" \
    .

case "${CI_BUILD_MEM0_IMAGE:-false}" in
    1|true|TRUE|yes|YES|on|ON)
        # Mem0 镜像基于上游私有基础镜像；仅在 Runner 已完成仓库登录时启用。
            docker build \
                -f docker/mem0/Dockerfile \
            -t "$mem0_image_tag" \
            .
        ;;
    *)
        echo "跳过 Mem0 镜像构建：CI_BUILD_MEM0_IMAGE 未启用。"
        ;;
esac

#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/cd/common/build-and-push-images.sh
# 作用: 在 CD Runner 构建并推送 GitHub Release 对应的预编译镜像。
# 范围: 构建主应用镜像、自托管 Mem0 镜像、Mem0 运维 Dashboard 镜像和 OPA 策略服务镜像，不连接生产服务器。
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
mem0_dashboard_dockerfile="${CD_MEM0_DASHBOARD_DOCKERFILE:-docker/mem0-dashboard/Dockerfile}"
opa_dockerfile="${CD_OPA_DOCKERFILE:-docker/opa/Dockerfile}"
mem0_python_base_image="${CD_MEM0_PYTHON_BASE_IMAGE:-python:3.12-slim-bookworm}"
mem0_dashboard_node_base_image="${CD_MEM0_DASHBOARD_NODE_BASE_IMAGE:-node:20-bookworm-slim}"
opa_version="${CD_OPA_VERSION:-v1.19.0}"
opa_runtime_base_image="${CD_OPA_RUNTIME_BASE_IMAGE:-debian:bookworm-slim}"

app_release_image="${CD_APP_IMAGE}"
mem0_release_image="${CD_MEM0_IMAGE}"
mem0_dashboard_release_image="${CD_MEM0_DASHBOARD_IMAGE}"
opa_release_image="${CD_OPA_IMAGE}"

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
        if [ ! -f vendor/mem0/server/requirements.txt ]; then
            echo "Mem0 git submodule 未初始化，缺少 vendor/mem0/server/requirements.txt。" >&2
            exit 1
        fi

        mem0_source_commit="$(git -C vendor/mem0 rev-parse HEAD)"

        # Mem0 镜像从固定 submodule commit 构建，也只使用同一个 GitHub Release tag。
        docker build \
            -f "$mem0_dockerfile" \
            --build-arg "PYTHON_BASE_IMAGE=${mem0_python_base_image}" \
            --build-arg "MEM0_SOURCE_COMMIT=${mem0_source_commit}" \
            --label "org.opencontainers.image.version=${release_tag}" \
            --label "org.opencontainers.image.revision=${release_sha}" \
            --label "io.vancer.mem0.source-revision=${mem0_source_commit}" \
            -t "$mem0_release_image" \
            .
        docker push "$mem0_release_image"
        ;;
    *)
        echo "跳过 Mem0 镜像构建：CD_BUILD_MEM0_IMAGE 未启用。"
        ;;
esac

case "${CD_BUILD_OPA_IMAGE:-true}" in
    1|true|TRUE|yes|YES|on|ON)
        # OPA 镜像封装官方 Release 二进制、默认策略与排障工具，并使用同一个 GitHub Release tag。
        docker build \
            -f "$opa_dockerfile" \
            --build-arg "OPA_VERSION=${opa_version}" \
            --build-arg "OPA_RUNTIME_BASE_IMAGE=${opa_runtime_base_image}" \
            --label "org.opencontainers.image.version=${release_tag}" \
            --label "org.opencontainers.image.revision=${release_sha}" \
            --label "io.vancer.opa.version=${opa_version}" \
            -t "$opa_release_image" \
            .
        docker push "$opa_release_image"
        ;;
    *)
        echo "跳过 OPA 镜像构建：CD_BUILD_OPA_IMAGE 未启用。"
        ;;
esac

case "${CD_BUILD_MEM0_DASHBOARD_IMAGE:-true}" in
    1|true|TRUE|yes|YES|on|ON)
        if [ ! -f vendor/mem0/server/dashboard/package.json ]; then
            echo "Mem0 git submodule 未初始化，缺少 vendor/mem0/server/dashboard/package.json。" >&2
            exit 1
        fi

        mem0_source_commit="$(git -C vendor/mem0 rev-parse HEAD)"

        # Mem0 Dashboard 镜像同样从固定 submodule commit 构建，并使用同一个 GitHub Release tag。
        docker build \
            -f "$mem0_dashboard_dockerfile" \
            --build-arg "NODE_BASE_IMAGE=${mem0_dashboard_node_base_image}" \
            --build-arg "MEM0_SOURCE_COMMIT=${mem0_source_commit}" \
            --label "org.opencontainers.image.version=${release_tag}" \
            --label "org.opencontainers.image.revision=${release_sha}" \
            --label "io.vancer.mem0.source-revision=${mem0_source_commit}" \
            -t "$mem0_dashboard_release_image" \
            .
        docker push "$mem0_dashboard_release_image"
        ;;
    *)
        echo "跳过 Mem0 Dashboard 镜像构建：CD_BUILD_MEM0_DASHBOARD_IMAGE 未启用。"
        ;;
esac

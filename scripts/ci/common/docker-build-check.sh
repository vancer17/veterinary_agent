#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/ci/common/docker-build-check.sh
# 作用: 构建生产应用镜像，验证 Dockerfile 与冻结依赖可生成运行产物。
# 范围: 默认只构建主应用镜像；附属服务镜像由业务仓库按需启用。
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
mem0_dashboard_image_tag="${CI_MEM0_DASHBOARD_IMAGE_TAG:-vet-agent-ci-mem0-dashboard:${GITHUB_SHA:-local}}"
opa_image_tag="${CI_OPA_IMAGE_TAG:-vet-agent-ci-opa:${GITHUB_SHA:-local}}"

case "${CI_BUILD_APP_IMAGE:-true}" in
    1|true|TRUE|yes|YES|on|ON)
        # 主应用镜像是业务仓库的核心交付物，PR 与主干门禁必须保证其可构建。
        docker build \
            -f "$app_dockerfile" \
            -t "$app_image_tag" \
            .
        ;;
    *)
        echo "跳过主应用镜像构建：CI_BUILD_APP_IMAGE 未启用。"
        ;;
esac

case "${CI_BUILD_MEM0_IMAGE:-false}" in
    1|true|TRUE|yes|YES|on|ON)
        if [ ! -f vendor/mem0/server/requirements.txt ]; then
            echo "Mem0 git submodule 未初始化，缺少 vendor/mem0/server/requirements.txt。" >&2
            exit 1
        fi

        mem0_source_commit="$(git -C vendor/mem0 rev-parse HEAD)"

        # Mem0 镜像基于 vendor/mem0/server 固定源码构建，用于验证自托管封装可生成运行产物。
        docker build \
            -f docker/mem0/Dockerfile \
            --build-arg "PYTHON_BASE_IMAGE=${CI_MEM0_PYTHON_BASE_IMAGE:-python:3.12-slim-bookworm}" \
            --build-arg "MEM0_SOURCE_COMMIT=${mem0_source_commit}" \
            -t "$mem0_image_tag" \
            .
        ;;
    *)
        echo "跳过 Mem0 镜像构建：CI_BUILD_MEM0_IMAGE 未启用。"
        ;;
esac

case "${CI_BUILD_MEM0_DASHBOARD_IMAGE:-false}" in
    1|true|TRUE|yes|YES|on|ON)
        if [ ! -f vendor/mem0/server/dashboard/package.json ]; then
            echo "Mem0 git submodule 未初始化，缺少 vendor/mem0/server/dashboard/package.json。" >&2
            exit 1
        fi

        mem0_source_commit="$(git -C vendor/mem0 rev-parse HEAD)"

        # Mem0 Dashboard 镜像基于 vendor/mem0/server/dashboard 固定源码构建，用于验证运维配套服务可生成运行产物。
        docker build \
            -f docker/mem0-dashboard/Dockerfile \
            --build-arg "NODE_BASE_IMAGE=${CI_MEM0_DASHBOARD_NODE_BASE_IMAGE:-node:20-bookworm-slim}" \
            --build-arg "MEM0_SOURCE_COMMIT=${mem0_source_commit}" \
            -t "$mem0_dashboard_image_tag" \
            .
        ;;
    *)
        echo "跳过 Mem0 Dashboard 镜像构建：CI_BUILD_MEM0_DASHBOARD_IMAGE 未启用。"
        ;;
esac

case "${CI_BUILD_OPA_IMAGE:-false}" in
    1|true|TRUE|yes|YES|on|ON)
        # OPA 镜像封装官方 Release 二进制与本仓库 bootstrap policy，用于验证策略服务交付物可构建。
        docker build \
            -f docker/opa/Dockerfile \
            --build-arg "OPA_VERSION=${CI_OPA_VERSION:-v1.19.0}" \
            --build-arg "OPA_RUNTIME_BASE_IMAGE=${CI_OPA_RUNTIME_BASE_IMAGE:-debian:bookworm-slim}" \
            -t "$opa_image_tag" \
            .

        docker run --rm "$opa_image_tag" opa test /opa/policies /opa/tests --fail-on-empty
        ;;
    *)
        echo "跳过 OPA 镜像构建：CI_BUILD_OPA_IMAGE 未启用。"
        ;;
esac

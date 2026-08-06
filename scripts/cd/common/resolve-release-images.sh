#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/cd/common/resolve-release-images.sh
# 作用: 将 GitHub Release 标签解析为 CD 所需的镜像标签。
# 范围: 只做版本解析与变量导出，不构建镜像、不连接生产环境。
# 说明: 输出可直接被 shell eval，亦可写入 GITHUB_OUTPUT 供 GitHub Actions 使用。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"

bash scripts/cd/common/verify-release-input.sh

release_tag="${CD_RELEASE_TAG}"
release_sha="${CD_RELEASE_SHA:-$(git rev-list -n 1 "$release_tag")}"
registry_image="${CD_REGISTRY_IMAGE:-crpi-efmmpn9a6t9mspwy.cn-hangzhou.personal.cr.aliyuncs.com/vancer-saas/veterinary_agent}"
mem0_registry_image="${CD_MEM0_REGISTRY_IMAGE:-crpi-efmmpn9a6t9mspwy.cn-hangzhou.personal.cr.aliyuncs.com/vancer-saas/veterinary_agent-mem0}"

if [[ "$registry_image" == *@* ]] || [[ "$mem0_registry_image" == *@* ]]; then
    echo "镜像仓库变量不得包含 digest；CD 只允许使用 GitHub Release 标签。" >&2
    exit 1
fi

app_release_image="${registry_image}:${release_tag}"
mem0_release_image="${mem0_registry_image}:${release_tag}"

if [ -n "${GITHUB_OUTPUT:-}" ]; then
    {
        printf 'release_tag=%s\n' "$release_tag"
        printf 'release_sha=%s\n' "$release_sha"
        printf 'app_image=%s\n' "$app_release_image"
        printf 'mem0_image=%s\n' "$mem0_release_image"
    } >>"$GITHUB_OUTPUT"
fi

printf 'CD_RELEASE_TAG=%q\n' "$release_tag"
printf 'CD_RELEASE_SHA=%q\n' "$release_sha"
printf 'CD_APP_IMAGE=%q\n' "$app_release_image"
printf 'CD_MEM0_IMAGE=%q\n' "$mem0_release_image"

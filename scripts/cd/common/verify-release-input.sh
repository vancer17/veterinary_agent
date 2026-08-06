#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/cd/common/verify-release-input.sh
# 作用: 校验 CD 流程使用的 GitHub Release 标签与本地 Git 引用。
# 范围: 只做版本输入与 Git tag 存在性检查，不构建镜像、不连接生产环境。
# 说明: 通用业务仓库可复用本脚本，按需调整 CD_RELEASE_TAG_PATTERN。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"

release_tag="${CD_RELEASE_TAG:-${1:-}}"
release_tag_pattern="${CD_RELEASE_TAG_PATTERN:-}"
if [ -z "$release_tag_pattern" ]; then
    release_tag_pattern='^v[0-9]+(\.[0-9]+){2}([.-][0-9A-Za-z][0-9A-Za-z.-]*)?$'
fi

if [ -z "$release_tag" ]; then
    echo "缺少 CD_RELEASE_TAG，无法确定 GitHub Release 版本。" >&2
    exit 1
fi

if ! [[ "$release_tag" =~ $release_tag_pattern ]]; then
    echo "Release 标签不符合版本命名规则: ${release_tag}" >&2
    echo "默认规则要求形如 v1.2.3、v1.2.3-rc.1 或 v1.2.3.hotfix.1。" >&2
    exit 1
fi

if ! git rev-parse --verify --quiet "refs/tags/${release_tag}^{commit}" >/dev/null; then
    echo "当前仓库中不存在 Release 标签: ${release_tag}" >&2
    exit 1
fi

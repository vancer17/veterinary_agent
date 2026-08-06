#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/ci/common/run-common-gate.sh
# 作用: 聚合可分发到其他业务仓库的通用 CI 门禁。
# 范围: 静态检查、Python 快速检查、Compose 解析、密钥边界和主应用镜像构建。
# 说明: 业务仓库特色检查应放入 scripts/ci/repository，不应混入通用门禁。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"

bash scripts/ci/common/static-check.sh
bash scripts/ci/common/python-check.sh
bash scripts/ci/common/compose-config-check.sh
bash scripts/ci/common/secret-boundary-check.sh
bash scripts/ci/common/docker-build-check.sh

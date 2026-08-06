#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/ci/run-all.sh
# 作用: 聚合执行业务仓库本地全量 CI 门禁。
# 范围: 先执行可分发的通用门禁，再执行兽医 Agent 仓库特色门禁。
# 说明: 本脚本为 Makefile 全量入口；GitHub Actions 可按 common/repository 拆分并行执行。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

bash scripts/ci/common/run-common-gate.sh
bash scripts/ci/repository/run-repository-gate.sh

#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/ci/repository/run-repository-gate.sh
# 作用: 聚合兽医 Agent 仓库特色 CI 门禁。
# 范围: CD 布局约束、pgvector 数据库初始化、Alembic 迁移验证与 Mem0 封装镜像构建。
# 说明: 该入口包含强业务语义，不应作为其他业务仓库的通用模板直接分发。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"

bash scripts/ci/repository/cd-layout-check.sh
bash scripts/ci/repository/database-migration-check.sh

# Mem0 是本仓库生产交付的一部分，默认在仓库特色门禁中构建；
# try-run 等轻量场景可通过 CI_BUILD_MEM0_IMAGE=false 显式跳过。
CI_BUILD_APP_IMAGE=false CI_BUILD_MEM0_IMAGE="${CI_BUILD_MEM0_IMAGE:-true}" bash scripts/ci/common/docker-build-check.sh

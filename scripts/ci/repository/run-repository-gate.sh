#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/ci/repository/run-repository-gate.sh
# 作用: 聚合兽医 Agent 仓库特色 CI 门禁。
# 范围: CD 布局约束、pgvector 数据库初始化与 Alembic 迁移验证。
# 说明: 该入口包含强业务语义，不应作为其他业务仓库的通用模板直接分发。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"

bash scripts/ci/repository/cd-layout-check.sh
bash scripts/ci/repository/database-migration-check.sh

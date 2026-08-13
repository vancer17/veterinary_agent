#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/ci/try-run.sh
# 作用: 为手动触发的 try-run 提供统一入口，可选择 dry-run、通用门禁、仓库特色门禁、外部真实 API 门禁或全量门禁。
# 范围: 默认不读取生产密钥，不推送镜像，不部署环境；具体强度由 CI_TRY_RUN_SCOPE 控制。
# 说明: 该脚本便于在 PR 合并前、主干合并后或生产手动部署前执行不同级别验证。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

scope="${CI_TRY_RUN_SCOPE:-${1:-full}}"

case "$scope" in
    dry-run)
        bash scripts/ci/common/dry-run-check.sh
        bash scripts/ci/repository/cd-layout-check.sh
        ;;
    common)
        bash scripts/ci/common/run-common-gate.sh
        ;;
    repository)
        bash scripts/ci/repository/run-repository-gate.sh
        ;;
    clinical-safety-api)
        bash scripts/integration/run-external-api-smoke.sh
        ;;
    memory-read-api)
        bash scripts/integration/run-memory-read-api-smoke.sh
        ;;
    task-routing-api)
        bash scripts/integration/run-task-routing-api-smoke.sh
        ;;
    external-api)
        # 聚合外部 API 门禁，用于发布前一次性验证当前已迁移的真实依赖主路径。
        bash scripts/integration/run-external-api-smoke.sh
        bash scripts/integration/run-memory-read-api-smoke.sh
        bash scripts/integration/run-task-routing-api-smoke.sh
        ;;
    full)
        bash scripts/ci/run-all.sh
        ;;
    *)
        echo "不支持的 try-run scope: ${scope}" >&2
        echo "可选值: dry-run, common, repository, clinical-safety-api, memory-read-api, task-routing-api, external-api, full" >&2
        exit 1
        ;;
esac

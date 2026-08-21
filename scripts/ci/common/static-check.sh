#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/ci/common/static-check.sh
# 作用: 执行不依赖业务运行时的通用静态检查。
# 范围: 覆盖 Shell 语法、配置文件解析、CI 文件头规范和 Makefile 入口可解析性。
# 说明: 该脚本不启动容器、不安装项目依赖，适合作为其他业务仓库的通用 CI 基线。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"

check_header_block() {
    # 校验指定文件顶部存在标准注释块。
    #
    # :param target_file: 待校验文件路径。
    # :return: 无返回值；校验失败时退出脚本。
    local target_file="$1"

    if ! sed -n '1,3p' "$target_file" | grep -q '^# .*=\{20,\}$'; then
        echo "文件缺少顶部标准注释块: ${target_file}" >&2
        exit 1
    fi
}

if ! command -v python3 >/dev/null 2>&1; then
    echo "缺少 python3，无法执行通用静态解析。" >&2
    exit 1
fi

while IFS= read -r shell_file; do
    bash -n "$shell_file"
done < <(find scripts docker -type f -name '*.sh' | sort)

python3 - <<'PY'
from __future__ import annotations

import pathlib
import tomllib

pyproject_path = pathlib.Path("pyproject.toml")
with pyproject_path.open("rb") as file:
    pyproject = tomllib.load(file)

project = pyproject.get("project")
if not isinstance(project, dict):
    raise SystemExit("pyproject.toml 缺少 [project] 配置。")

if not project.get("requires-python"):
    raise SystemExit("pyproject.toml 缺少 project.requires-python。")

if not pathlib.Path("uv.lock").is_file():
    raise SystemExit("缺少 uv.lock，无法保证依赖解析可复现。")
PY

shopt -s nullglob
for workflow_file in .github/workflows/*.yml; do
    check_header_block "$workflow_file"
done

for make_entrypoint in Makefile make/*.mk; do
    check_header_block "$make_entrypoint"
done

for shell_entrypoint in scripts/ci/*.sh scripts/ci/common/*.sh scripts/ci/repository/*.sh scripts/cd/common/*.sh scripts/cd/repository/*.sh; do
    check_header_block "$shell_entrypoint"
done

# Makefile 入口只做 dry-run 解析，避免静态检查阶段执行实际命令。
make_entrypoints=(
    help
    ci
    ci-common
    ci-repository
    ci-static
    ci-python
    ci-compose
    ci-secret-boundary
    ci-cd-layout
    ci-db
    ci-image
    ci-image-core
    ci-image-guardrails
    ci-image-variants
    ci-offline-image
    ci-dry-run
    ci-try-run
    ci-response-generation-api
    cd-verify-release
    cd-resolve-images
    cd-build-images
    cd-build-images-guardrails
    cd-sync-production
    cd-deploy-production
    cd-health-check
    preprod-deploy-clinical-safety-stage5
    preprod-rollback-clinical-safety-stage5
    docker-dev-config
    docker-prod-config
    dev-build
    dev-build-core
    dev-build-guardrails
    dev-up-no-wait
    dev-up-core
    dev-up-guardrails
    dev-down
    dev-test
    prod-config
    prod-pull
    prod-up
    prod-ready
    db-shell
    db-dev-migrate
    db-prod-migrate
    request-ready
    request-business-all
    runtime-offline-check
    dev-offline-check
    smoke-clinical-safety-stage5-preprod
    smoke-external-api
    mem0-build
    mem0-config
)
make -n "${make_entrypoints[@]}" >/dev/null

if command -v actionlint >/dev/null 2>&1; then
    actionlint .github/workflows/*.yml
else
    echo "未检测到 actionlint，跳过 GitHub Actions 深度语义检查。"
fi

#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/ci/common/python-check.sh
# 作用: 执行 Python 依赖锁定安装、语法编译检查与离线单元测试。
# 范围: 仅覆盖不依赖真实外部服务密钥的快速门禁，可分发到其他 Python 业务仓库。
# 说明: 本脚本供本地 Makefile 与 GitHub Actions 共同调用，避免门禁逻辑分叉。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"

if ! command -v uv >/dev/null 2>&1; then
    echo "缺少 uv 命令。请先安装 uv，或在 CI 中通过 python -m pip install uv 安装。" >&2
    exit 1
fi

export UV_PYTHON_DOWNLOADS="${UV_PYTHON_DOWNLOADS:-automatic}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"
read -r -a pytest_paths <<<"${CI_PYTEST_PATHS:-tests}"

# 使用 uv.lock 的锁定依赖解析结果，并要求锁文件不会在检查过程中被改写。
uv sync --locked

# compileall 先于 pytest 执行，用于提前暴露导入前的语法与字节码编译问题。
uv run python -m compileall src tests

# vendor/ 仅作为第三方源码快照或子模块，不应纳入本仓库快速单元测试集合。
uv run pytest -q "${pytest_paths[@]}"

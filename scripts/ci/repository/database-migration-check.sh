#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/ci/repository/database-migration-check.sh
# 作用: 启动临时 pgvector PostgreSQL，验证初始化脚本与 Alembic 迁移。
# 范围: 不启动 LiteLLM、Mem0、Agent app，不导入 seed 或 RAG 静态资产。
# 说明: 仓库内静态资产样式尚未稳定，CI 暂不以其内容结构作为门禁依据。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"

if ! command -v uv >/dev/null 2>&1; then
    echo "缺少 uv 命令。请先安装 uv，或在 CI 中通过 python -m pip install uv 安装。" >&2
    exit 1
fi

if ! docker version >/dev/null 2>&1; then
    echo "缺少可用 Docker daemon。请在具备 Docker 运行能力的环境中执行本检查。" >&2
    exit 1
fi

container_name="vet-agent-ci-postgres-${GITHUB_RUN_ID:-local}-$$"
postgres_image="${PGVECTOR_IMAGE:-pgvector/pgvector:pg16}"

while IFS= read -r revision_name; do
    if [ "${#revision_name}" -gt 32 ]; then
        echo "Alembic revision 超过默认 version_num 长度限制: ${revision_name}" >&2
        echo "请将 revision 控制在 32 个字符以内，或显式迁移 alembic_version 表字段长度。" >&2
        exit 1
    fi
done < <(
    python3 - <<'PY'
from __future__ import annotations

import ast
import pathlib

for revision_file in sorted(pathlib.Path("alembic/versions").glob("*.py")):
    module = ast.parse(revision_file.read_text(encoding="utf-8"), filename=str(revision_file))
    for node in module.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "revision" for target in node.targets)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            print(node.value.value)
            break
PY
)

cleanup() {
    # 清理本次数据库检查创建的临时 PostgreSQL 容器。
    #
    # :return: 无返回值。
    docker rm -f "$container_name" >/dev/null 2>&1 || true
}

trap cleanup EXIT

# 挂载正式初始化脚本，确保 CI 覆盖逻辑库、登录角色和 pgvector/pg_trgm 扩展创建。
docker run -d \
    --name "$container_name" \
    -e POSTGRES_DB=postgres \
    -e POSTGRES_USER=postgres \
    -e POSTGRES_PASSWORD=postgres \
    -e VET_AGENT_POSTGRES_DB=vet_agent \
    -e VET_AGENT_POSTGRES_USER=vet_agent \
    -e VET_AGENT_POSTGRES_PASSWORD=vet_agent \
    -e LITELLM_POSTGRES_DB=litellm \
    -e LITELLM_POSTGRES_USER=litellm \
    -e LITELLM_POSTGRES_PASSWORD=litellm \
    -e MEM0_POSTGRES_DB=mem0_vector \
    -e MEM0_POSTGRES_USER=mem0 \
    -e MEM0_POSTGRES_PASSWORD=mem0 \
    -e MEM0_APP_DB_NAME=mem0_app \
    -v "$repo_root/docker/postgres/init:/docker-entrypoint-initdb.d:ro" \
    -v "$repo_root/docker/postgres/postgresql.conf:/etc/postgresql/postgresql.conf:ro" \
    -p "127.0.0.1::5432" \
    "$postgres_image" \
    postgres -c config_file=/etc/postgresql/postgresql.conf >/dev/null

for attempt in $(seq 1 60); do
    if docker exec "$container_name" \
        sh -c 'PGPASSWORD=vet_agent psql -v ON_ERROR_STOP=1 -U vet_agent -d vet_agent -c "SELECT 1" >/dev/null' \
        >/dev/null 2>&1; then
        break
    fi

    if [ "$attempt" -eq 60 ]; then
        echo "临时 PostgreSQL 业务库在超时时间内未就绪，容器日志如下：" >&2
        docker logs "$container_name" >&2 || true
        exit 1
    fi

    sleep 2
done

postgres_port="$(docker port "$container_name" 5432/tcp | sed -E 's/.*:([0-9]+)$/\1/' | head -n 1)"
database_url="postgresql://vet_agent:vet_agent@127.0.0.1:${postgres_port}/vet_agent"

export UV_PYTHON_DOWNLOADS="${UV_PYTHON_DOWNLOADS:-automatic}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

uv sync --locked

# 关闭 embedding 与 Mem0，保证数据库门禁只验证确定性 schema 迁移链路。
DATABASE_URL="$database_url" \
ENABLE_RAG_EMBEDDINGS=false \
ENABLE_MEM0=false \
uv run alembic upgrade head

docker exec -i \
    -e POSTGRES_HOST=127.0.0.1 \
    -e POSTGRES_PORT=5432 \
    -e POSTGRES_USER=postgres \
    -e POSTGRES_PASSWORD=postgres \
    -e VET_AGENT_POSTGRES_DB=vet_agent \
    -e MEM0_POSTGRES_DB=mem0_vector \
    "$container_name" \
    bash < docker/postgres/ops/vector-smoke-check.sh

DATABASE_URL="$database_url" uv run alembic current

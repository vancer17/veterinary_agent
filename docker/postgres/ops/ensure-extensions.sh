#!/usr/bin/env bash
# =============================================================================
# 文件: docker/postgres/ops/ensure-extensions.sh
# 作用: 使用 PostgreSQL 管理员账号为各逻辑库补齐必要扩展。
# 范围: 覆盖 Agent 业务库与 Mem0 向量库所需的 pgvector、pg_trgm 扩展。
# 说明: 本脚本供 Docker Compose 一次性运维任务调用；业务表结构仍由 Alembic 管理。
# =============================================================================

set -Eeuo pipefail

postgres_host="${POSTGRES_HOST:-postgres}"
postgres_port="${POSTGRES_PORT:-5432}"
admin_user="${POSTGRES_USER:-postgres}"
admin_password="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

vet_agent_database="${VET_AGENT_POSTGRES_DB:-vet_agent}"
mem0_vector_database="${MEM0_POSTGRES_DB:-mem0_vector}"

export PGPASSWORD="$admin_password"

wait_for_postgres_host() {
    # Compose 重建 backend 网络后，嵌入式 DNS 的服务别名可能晚于容器启动。
    # 这里等待主机名可解析，避免一次性扩展任务在首个解析窗口内误报失败。
    local attempt
    for attempt in $(seq 1 30); do
        if getent hosts "$postgres_host" >/dev/null 2>&1; then
            return 0
        fi
        echo "Waiting for PostgreSQL host resolution: ${postgres_host} (${attempt}/30)" >&2
        sleep 1
    done
    echo "PostgreSQL host cannot be resolved: ${postgres_host}" >&2
    return 1
}

wait_for_postgres_host

create_extensions() {
    # 在目标逻辑库内安装指定扩展。
    #
    # :param database_name: 目标逻辑库名称。
    # :param ...: 需要安装的扩展名称列表。
    # :return: 无返回值。
    local database_name="$1"
    shift

    if [ -z "$database_name" ]; then
        echo "Database name must be non-empty." >&2
        exit 1
    fi

    for extension_name in "$@"; do
        if [ -z "$extension_name" ]; then
            echo "Extension name must be non-empty." >&2
            exit 1
        fi

        psql \
            -v ON_ERROR_STOP=1 \
            --host "$postgres_host" \
            --port "$postgres_port" \
            --username "$admin_user" \
            --dbname "$database_name" \
            --set=extension_name="$extension_name" <<-'EOSQL'
CREATE EXTENSION IF NOT EXISTS :"extension_name";
EOSQL
    done
}

create_extensions "$vet_agent_database" vector pg_trgm
create_extensions "$mem0_vector_database" vector

#!/usr/bin/env bash
# 文件：docker/postgres/ops/ensure-extensions.sh
# 作用：使用 PostgreSQL 管理员账号为各逻辑库补齐必要扩展。
# 说明：本脚本供 Docker Compose 一次性运维任务调用；业务表结构仍由 Alembic 管理。

set -Eeuo pipefail

postgres_host="${POSTGRES_HOST:-postgres}"
postgres_port="${POSTGRES_PORT:-5432}"
admin_user="${POSTGRES_USER:-postgres}"
admin_password="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

vet_agent_database="${VET_AGENT_POSTGRES_DB:-vet_agent}"
mem0_vector_database="${MEM0_POSTGRES_DB:-mem0_vector}"

export PGPASSWORD="$admin_password"

create_extensions() {
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

#!/usr/bin/env bash
# =============================================================================
# 文件: docker/postgres/ops/vector-smoke-check.sh
# 作用: 校验 PostgreSQL 逻辑库中的 pgvector 与 pg_trgm 扩展及基础向量运算能力。
# 范围: 只读取扩展状态并执行常量向量运算，不创建业务表、不导入数据、不修改数据库状态。
# 说明: 可供 CI 临时容器、正式环境运维任务或人工排障复用。
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
    # 与扩展初始化任务保持一致，先等待 Compose 服务 DNS 记录可用。
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

psql_scalar() {
    # 执行只返回单个标量值的 SQL。
    #
    # :param database_name: 目标逻辑库名称。
    # :param sql_text: 待执行的 SQL 文本。
    # :return: 输出去空白后的 SQL 标量结果。
    local database_name="$1"
    local sql_text="$2"

    psql \
        -v ON_ERROR_STOP=1 \
        --host "$postgres_host" \
        --port "$postgres_port" \
        --username "$admin_user" \
        --dbname "$database_name" \
        --tuples-only \
        --no-align \
        --command "$sql_text" | tr -d '[:space:]'
}

assert_extension() {
    # 校验目标逻辑库已安装指定扩展。
    #
    # :param database_name: 目标逻辑库名称。
    # :param extension_name: PostgreSQL 扩展名称。
    # :return: 无返回值；缺少扩展时退出脚本。
    local database_name="$1"
    local extension_name="$2"
    local installed

    installed="$(psql_scalar "$database_name" "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = '${extension_name}');")"
    if [ "$installed" != "t" ]; then
        echo "数据库 ${database_name} 缺少扩展: ${extension_name}" >&2
        exit 1
    fi
}

assert_vector_operator() {
    # 校验 pgvector 余弦距离运算符可正常执行。
    #
    # :param database_name: 目标逻辑库名称。
    # :return: 无返回值；运算异常时由 psql 退出。
    local database_name="$1"

    psql_scalar "$database_name" "SELECT ('[1,0,0]'::vector <=> '[1,0,0]'::vector)::float8;" >/dev/null
}

assert_extension "$vet_agent_database" vector
assert_extension "$vet_agent_database" pg_trgm
assert_extension "$mem0_vector_database" vector
assert_vector_operator "$vet_agent_database"
assert_vector_operator "$mem0_vector_database"

echo "PostgreSQL pgvector 冒烟检查通过。"

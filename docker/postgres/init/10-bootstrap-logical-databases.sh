#!/usr/bin/env bash
# =============================================================================
# 文件: docker/postgres/init/10-bootstrap-logical-databases.sh
# 作用: 初始化共享 PostgreSQL 实例中的登录角色、逻辑库与基础扩展。
# 范围: 仅在官方 PostgreSQL 镜像空数据卷首次初始化阶段执行。
# 说明: 本脚本不创建业务表；业务 schema 由 Alembic、LiteLLM 与 Mem0 各自迁移管理。
# =============================================================================

set -Eeuo pipefail

admin_database="${POSTGRES_DB:-postgres}"
admin_user="${POSTGRES_USER:-postgres}"
admin_password="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

vet_agent_database="${VET_AGENT_POSTGRES_DB:-vet_agent}"
vet_agent_user="${VET_AGENT_POSTGRES_USER:-vet_agent}"
vet_agent_password="${VET_AGENT_POSTGRES_PASSWORD:?VET_AGENT_POSTGRES_PASSWORD is required}"

litellm_database="${LITELLM_POSTGRES_DB:-litellm}"
litellm_user="${LITELLM_POSTGRES_USER:-litellm}"
litellm_password="${LITELLM_POSTGRES_PASSWORD:?LITELLM_POSTGRES_PASSWORD is required}"

mem0_vector_database="${MEM0_POSTGRES_DB:-mem0_vector}"
mem0_app_database="${MEM0_APP_DB_NAME:-mem0_app}"
mem0_user="${MEM0_POSTGRES_USER:-mem0}"
mem0_password="${MEM0_POSTGRES_PASSWORD:?MEM0_POSTGRES_PASSWORD is required}"

created_roles=" ${admin_user} "
created_databases=" ${admin_database} "

create_login_role() {
    # 创建登录角色，并防止服务角色复用管理员账号时密码不一致。
    #
    # :param role_name: 登录角色名称。
    # :param role_password: 登录角色密码。
    # :return: 无返回值。
    local role_name="$1"
    local role_password="$2"

    if [ -z "$role_name" ] || [ -z "$role_password" ]; then
        echo "Role name and password must be non-empty." >&2
        exit 1
    fi

    case "$created_roles" in
        *" ${role_name} "*)
            if [ "$role_name" = "$admin_user" ] && [ "$role_password" != "$admin_password" ]; then
                echo "Service role '${role_name}' reuses the admin role but has a different password." >&2
                exit 1
            fi
            return
            ;;
    esac

    psql \
        -v ON_ERROR_STOP=1 \
        --username "$admin_user" \
        --dbname "$admin_database" \
        --set=role_name="$role_name" \
        --set=role_password="$role_password" <<-'EOSQL'
CREATE ROLE :"role_name" WITH LOGIN PASSWORD :'role_password';
EOSQL

    created_roles="${created_roles}${role_name} "
}

create_owned_database() {
    # 创建由指定角色拥有的逻辑库。
    #
    # :param database_name: 逻辑库名称。
    # :param owner_name: 逻辑库拥有者。
    # :return: 无返回值。
    local database_name="$1"
    local owner_name="$2"

    if [ -z "$database_name" ] || [ -z "$owner_name" ]; then
        echo "Database name and owner must be non-empty." >&2
        exit 1
    fi

    case "$created_databases" in
        *" ${database_name} "*)
            return
            ;;
    esac

    createdb \
        --username "$admin_user" \
        --owner "$owner_name" \
        --encoding "UTF8" \
        "$database_name"

    created_databases="${created_databases}${database_name} "
}

create_database_extensions() {
    # 在目标逻辑库内安装扩展；该操作需使用管理员账号执行。
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
            --username "$admin_user" \
            --dbname "$database_name" \
            --set=extension_name="$extension_name" <<-'EOSQL'
CREATE EXTENSION IF NOT EXISTS :"extension_name";
EOSQL
    done
}

create_login_role "$vet_agent_user" "$vet_agent_password"
create_owned_database "$vet_agent_database" "$vet_agent_user"
create_database_extensions "$vet_agent_database" vector pg_trgm

create_login_role "$litellm_user" "$litellm_password"
create_owned_database "$litellm_database" "$litellm_user"

create_login_role "$mem0_user" "$mem0_password"
create_owned_database "$mem0_vector_database" "$mem0_user"
create_database_extensions "$mem0_vector_database" vector
create_owned_database "$mem0_app_database" "$mem0_user"

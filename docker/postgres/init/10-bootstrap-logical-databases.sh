#!/usr/bin/env bash
# File: docker/postgres/init/10-bootstrap-logical-databases.sh
# Purpose: Bootstrap login roles and logical databases for the production compose
#          topology that shares one PostgreSQL instance across Vet Agent,
#          LiteLLM, and Mem0.
# Scope: Runs only during the official PostgreSQL image first-init phase. It does
#        not create application tables; schema migrations stay with Alembic,
#        LiteLLM, and Mem0's upstream Alembic command.

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

create_login_role "$vet_agent_user" "$vet_agent_password"
create_owned_database "$vet_agent_database" "$vet_agent_user"

create_login_role "$litellm_user" "$litellm_password"
create_owned_database "$litellm_database" "$litellm_user"

create_login_role "$mem0_user" "$mem0_password"
create_owned_database "$mem0_vector_database" "$mem0_user"
create_owned_database "$mem0_app_database" "$mem0_user"

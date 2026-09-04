#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/dev/deploy-temporal-dev.sh
# 作用: 将 Temporal 开发栈部署到远端 vet-agent-dev 环境。
# 范围: 同步 Compose 配置、复用共享 PostgreSQL 创建独立逻辑库、启动 Temporal
#       Server / UI 并注册 semantic-collaboration-dev namespace。
# 说明: 真实密码仅保存在远端 docker/temporal/compose.dev.env；不会写入仓库或日志。
# =============================================================================

set -Eeuo pipefail

remote_host="${VET_AGENT_REMOTE_HOST:-devlop@47.97.19.58}"
remote_repo="${VET_AGENT_REMOTE_REPO:-/home/devlop/veterinary_agent}"
ssh_key="${VET_AGENT_TEMPORAL_SSH_KEY:-$HOME/.ssh/AlibabaCloudLinux}"
postgres_container="${VET_AGENT_TEMPORAL_POSTGRES_CONTAINER:-vet-agent-dev-postgres}"

if [[ ! -f "$ssh_key" ]]; then
    echo "SSH key not found: $ssh_key" >&2
    exit 1
fi
if [[ ! -f "docker/temporal/compose.dev.yml" ]]; then
    echo "Run this script from the repository root." >&2
    exit 1
fi

ssh_options=(
    -F /dev/null
    -i "$ssh_key"
    -o BatchMode=yes
    -o ConnectTimeout=10
    -o StrictHostKeyChecking=accept-new
)

# The host expression is intentionally expanded from the local deployment shell.
# shellcheck disable=SC2029
remote_execute() {
    ssh "${ssh_options[@]}" "$remote_host" "$@"
}

printf 'Syncing Temporal configuration to %s:%s...\n' "$remote_host" "$remote_repo"
remote_execute "mkdir -p '$remote_repo/docker/temporal/dynamicconfig'"
tar -czf - \
    docker/temporal/compose.dev.yml \
    docker/temporal/compose.dev.env.template \
    docker/temporal/dynamicconfig/development-sql.yaml \
    | remote_execute "tar -C '$remote_repo' -xzf -"

printf 'Preparing PostgreSQL logical databases and starting Temporal...\n'
remote_execute \
    REMOTE_REPO="$remote_repo" \
    POSTGRES_CONTAINER="$postgres_container" \
    bash -s <<'REMOTE'
set -Eeuo pipefail

remote_repo="$REMOTE_REPO"
postgres_container="$POSTGRES_CONTAINER"
compose_file="$remote_repo/docker/temporal/compose.dev.yml"
env_file="$remote_repo/docker/temporal/compose.dev.env"
template_file="$remote_repo/docker/temporal/compose.dev.env.template"

cd "$remote_repo"

if [[ ! -f "$env_file" ]]; then
    generated_password="$(openssl rand -hex 24)"
    cp "$template_file" "$env_file"
    chmod 600 "$env_file"
    sed -i "s/^TEMPORAL_POSTGRES_PASSWORD=.*/TEMPORAL_POSTGRES_PASSWORD=$generated_password/" "$env_file"
    unset generated_password
    echo "Created remote Temporal env file with a generated PostgreSQL password."
fi
chmod 600 "$env_file"

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

: "${COMPOSE_PROJECT_NAME:?COMPOSE_PROJECT_NAME is required}"
: "${TEMPORAL_IMAGE:?TEMPORAL_IMAGE is required}"
: "${TEMPORAL_UI_IMAGE:?TEMPORAL_UI_IMAGE is required}"
: "${TEMPORAL_ADMIN_TOOLS_IMAGE:?TEMPORAL_ADMIN_TOOLS_IMAGE is required}"
: "${TEMPORAL_NETWORK_NAME:?TEMPORAL_NETWORK_NAME is required}"
: "${TEMPORAL_POSTGRES_HOST:?TEMPORAL_POSTGRES_HOST is required}"
: "${TEMPORAL_POSTGRES_USER:?TEMPORAL_POSTGRES_USER is required}"
: "${TEMPORAL_POSTGRES_PASSWORD:?TEMPORAL_POSTGRES_PASSWORD is required}"
: "${TEMPORAL_POSTGRES_DB:?TEMPORAL_POSTGRES_DB is required}"
: "${TEMPORAL_VISIBILITY_DB:?TEMPORAL_VISIBILITY_DB is required}"
: "${TEMPORAL_NAMESPACE:?TEMPORAL_NAMESPACE is required}"

[[ "$TEMPORAL_POSTGRES_USER" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]] || {
    echo "Invalid TEMPORAL_POSTGRES_USER." >&2
    exit 1
}
[[ "$TEMPORAL_POSTGRES_DB" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]] || {
    echo "Invalid TEMPORAL_POSTGRES_DB." >&2
    exit 1
}
[[ "$TEMPORAL_VISIBILITY_DB" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]] || {
    echo "Invalid TEMPORAL_VISIBILITY_DB." >&2
    exit 1
}
[[ "$TEMPORAL_NAMESPACE" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$ ]] || {
    echo "Invalid TEMPORAL_NAMESPACE." >&2
    exit 1
}
[[ "$TEMPORAL_POSTGRES_PASSWORD" =~ ^[A-Za-z0-9_-]{24,128}$ ]] || {
    echo "TEMPORAL_POSTGRES_PASSWORD must contain 24-128 letters, digits, underscores, or hyphens." >&2
    exit 1
}

sudo docker inspect "$postgres_container" >/dev/null
sudo docker network inspect "$TEMPORAL_NETWORK_NAME" >/dev/null

pg_health="$(sudo docker inspect -f '{{.State.Health.Status}}' "$postgres_container")"
if [[ "$pg_health" != "healthy" ]]; then
    echo "Shared PostgreSQL container $postgres_container is not healthy: $pg_health" >&2
    exit 1
fi

# 角色和逻辑库独立于 Agent / LiteLLM / Mem0。这里不安装 pgvector，
# 也不授予 temporal 角色 CREATEDB / CREATEROLE / SUPERUSER 权限。
sudo docker exec -i "$postgres_container" \
    psql -U postgres -d postgres -v ON_ERROR_STOP=1 <<SQL
DO \$db_role\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${TEMPORAL_POSTGRES_USER}') THEN
        CREATE ROLE ${TEMPORAL_POSTGRES_USER}
            LOGIN
            PASSWORD '${TEMPORAL_POSTGRES_PASSWORD}'
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOREPLICATION;
    ELSE
        ALTER ROLE ${TEMPORAL_POSTGRES_USER}
            LOGIN
            PASSWORD '${TEMPORAL_POSTGRES_PASSWORD}'
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOREPLICATION;
    END IF;
END
\$db_role\$;

SELECT 'CREATE DATABASE ${TEMPORAL_POSTGRES_DB} OWNER ${TEMPORAL_POSTGRES_USER}'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${TEMPORAL_POSTGRES_DB}') \gexec
SELECT 'CREATE DATABASE ${TEMPORAL_VISIBILITY_DB} OWNER ${TEMPORAL_POSTGRES_USER}'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${TEMPORAL_VISIBILITY_DB}') \gexec

ALTER DATABASE ${TEMPORAL_POSTGRES_DB} OWNER TO ${TEMPORAL_POSTGRES_USER};
ALTER DATABASE ${TEMPORAL_VISIBILITY_DB} OWNER TO ${TEMPORAL_POSTGRES_USER};
ALTER DATABASE ${TEMPORAL_POSTGRES_DB} CONNECTION LIMIT 40;
ALTER DATABASE ${TEMPORAL_VISIBILITY_DB} CONNECTION LIMIT 40;
REVOKE ALL ON DATABASE ${TEMPORAL_POSTGRES_DB} FROM PUBLIC;
REVOKE ALL ON DATABASE ${TEMPORAL_VISIBILITY_DB} FROM PUBLIC;
GRANT ALL PRIVILEGES ON DATABASE ${TEMPORAL_POSTGRES_DB} TO ${TEMPORAL_POSTGRES_USER};
GRANT ALL PRIVILEGES ON DATABASE ${TEMPORAL_VISIBILITY_DB} TO ${TEMPORAL_POSTGRES_USER};
SQL

compose_command=(
    sudo docker compose
    -f "$compose_file"
    --env-file "$env_file"
)

printf 'Validating Compose configuration...\n'
"${compose_command[@]}" config >/dev/null

printf 'Pulling fixed Temporal images...\n'
"${compose_command[@]}" pull temporal temporal-ui temporal-admin-tools

printf 'Starting Temporal Server and UI...\n'
"${compose_command[@]}" up -d temporal temporal-ui

printf 'Waiting for Temporal Frontend readiness...\n'
ready=false
for attempt in $(seq 1 90); do
    if "${compose_command[@]}" --profile ops run --rm --no-deps \
        temporal-admin-tools operator cluster describe >/dev/null 2>&1; then
        ready=true
        break
    fi
    if ! sudo docker inspect vet-agent-dev-temporal >/dev/null 2>&1; then
        echo 'Temporal container disappeared during startup.' >&2
        "${compose_command[@]}" logs temporal || true
        exit 1
    fi
    sleep 2
done
if [[ "$ready" != true ]]; then
    echo 'Temporal Frontend did not become ready within 180 seconds.' >&2
    "${compose_command[@]}" logs --tail=200 temporal || true
    exit 1
fi

printf 'Ensuring namespace %s exists...\n' "$TEMPORAL_NAMESPACE"
if ! "${compose_command[@]}" --profile ops run --rm --no-deps \
    temporal-admin-tools operator namespace describe \
    --namespace "$TEMPORAL_NAMESPACE" >/dev/null 2>&1; then
    "${compose_command[@]}" --profile ops run --rm --no-deps \
        temporal-admin-tools operator namespace create \
        --namespace "$TEMPORAL_NAMESPACE" \
        --retention 7d
fi

printf 'Running final smoke checks...\n'
"${compose_command[@]}" --profile ops run --rm --no-deps \
    temporal-admin-tools operator namespace describe \
    --namespace "$TEMPORAL_NAMESPACE" >/dev/null
"${compose_command[@]}" --profile ops run --rm --no-deps \
    temporal-admin-tools workflow list \
    --namespace "$TEMPORAL_NAMESPACE" >/dev/null

printf '\nDeployment summary:\n'
"${compose_command[@]}" ps
printf '\nTemporal connection for remote containers:\n'
printf '  TEMPORAL_ADDRESS=semantic-collaboration-temporal:7233\n'
printf '  TEMPORAL_NAMESPACE=%s\n' "$TEMPORAL_NAMESPACE"
printf '\nLocal SSH tunnel example:\n'
printf '  ssh -F /dev/null -i <key> -N -L 7233:127.0.0.1:7233 -L 8080:127.0.0.1:8080 %s\n' "$remote_host"
REMOTE

printf 'Temporal development deployment completed.\n'

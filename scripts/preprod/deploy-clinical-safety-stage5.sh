#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/preprod/deploy-clinical-safety-stage5.sh
# 作用: 将临床安全阶段 4 之后的 Release 部署到预发布环境并执行契约预检。
# 范围: 仅部署 core 应用镜像与 OPA 策略镜像，按 seed → migrate 顺序处理阶段 4
#       存量资产契约，不在预发布服务器现场构建镜像或修复医学数据。
# 说明: 预发布真实 env、API key 与模型密钥保留在服务器本地，本脚本不回显密钥。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

action="${1:-deploy}"
release_tag="${CLINICAL_SAFETY_STAGE5_RELEASE_TAG:-}"
rollback_run_id="${CLINICAL_SAFETY_STAGE5_ROLLBACK_RUN_ID:-}"
run_id="${CLINICAL_SAFETY_STAGE5_RUN_ID:-stage5-$(date +%Y%m%d%H%M%S)-$(python3 -c 'import uuid; print(uuid.uuid4().hex[:8])')}"

ssh_host="${CLINICAL_SAFETY_STAGE5_SSH_HOST:-121.41.58.20}"
ssh_port="${CLINICAL_SAFETY_STAGE5_SSH_PORT:-22}"
ssh_user="${CLINICAL_SAFETY_STAGE5_SSH_USER:-deploy}"
ssh_key="${CLINICAL_SAFETY_STAGE5_SSH_KEY:-/home/vancer17/.ssh/infra-ci-deploy}"
deploy_path="${CLINICAL_SAFETY_STAGE5_DEPLOY_PATH:-/opt/vancer-saas/veterinary_agent-preprod}"
compose_project="${CLINICAL_SAFETY_STAGE5_COMPOSE_PROJECT:-vet-agent-preprod}"
database_name="${CLINICAL_SAFETY_STAGE5_DATABASE_NAME:-vet_agent}"
app_port="${CLINICAL_SAFETY_STAGE5_APP_PORT:-18000}"
asset_manifest="${CLINICAL_SAFETY_STAGE5_ASSET_MANIFEST:-assets/clinical_safety/vet_safety_assets.v1.json}"
policy_file="${CLINICAL_SAFETY_STAGE5_POLICY_FILE:-docker/opa/policies/clinical_safety.rego}"
registry_username="${CLINICAL_SAFETY_STAGE5_REGISTRY_USERNAME:-}"
registry_password="${CLINICAL_SAFETY_STAGE5_REGISTRY_PASSWORD:-}"

case "$action" in
    deploy|rollback-runtime)
        ;;
    *)
        echo "不支持的操作: ${action}。可选值: deploy, rollback-runtime。" >&2
        exit 1
        ;;
esac

if [[ ! -f "$ssh_key" ]]; then
    echo "SSH 私钥不存在: ${ssh_key}" >&2
    exit 1
fi

if [[ ! -f "$asset_manifest" ]] || [[ ! -f "$policy_file" ]]; then
    echo "缺少临床安全资产 manifest 或 OPA 策略文件。" >&2
    exit 1
fi

if ! [[ "$database_name" =~ ^[A-Za-z0-9_]+$ ]]; then
    echo "预发布数据库名称只能包含字母、数字和下划线: ${database_name}" >&2
    exit 1
fi
if ! [[ "$run_id" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "阶段 5 运行标识只能包含字母、数字、点、下划线和连字符: ${run_id}" >&2
    exit 1
fi

ssh_args=(
    ssh
    -i "$ssh_key"
    -p "$ssh_port"
    -o BatchMode=yes
    -o StrictHostKeyChecking=no
    -o ServerAliveInterval=30
    -o ServerAliveCountMax=2
)
remote="${ssh_user}@${ssh_host}"

run_remote_script_file() {
    local prepared_script="$1"
    local remote_script="/tmp/vet-agent-stage5-${run_id}-$$.sh"
    local status=0

    scp \
        -i "$ssh_key" \
        -P "$ssh_port" \
        -o BatchMode=yes \
        -o StrictHostKeyChecking=no \
        "$prepared_script" \
        "${remote}:${remote_script}" >/dev/null
    if ! "${ssh_args[@]}" "$remote" \
        "$(remote_env) bash $(printf '%q' "$remote_script")"; then
        status=$?
    fi
    "${ssh_args[@]}" "$remote" "rm -f $(printf '%q' "$remote_script")" >/dev/null 2>&1 || true
    rm -f "$prepared_script"
    return "$status"
}

if [[ "$action" == "deploy" ]]; then
    if [[ -z "$release_tag" ]]; then
        echo "deploy 操作必须设置 CLINICAL_SAFETY_STAGE5_RELEASE_TAG。" >&2
        exit 1
    fi

    eval "$(CD_RELEASE_TAG="$release_tag" bash scripts/cd/common/resolve-release-images.sh)"
    registry_host="${CD_APP_IMAGE%%/*}"
else
    if [[ -z "$rollback_run_id" ]]; then
        echo "rollback-runtime 操作必须设置 CLINICAL_SAFETY_STAGE5_ROLLBACK_RUN_ID。" >&2
        exit 1
    fi
fi

expected_emergency_count="$(
    python3 - "$asset_manifest" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as file:
    document = json.load(file)
assets = document.get("assets", [])
count = sum(
    item.get("asset_type") == "emergency_red_flag"
    and item.get("review_status", "approved") == "approved"
    and item.get("enabled", True)
    for item in assets
)
if count <= 0:
    raise SystemExit("Release 资产 manifest 中没有已发布急诊资产。")
print(count)
PY
)"
local_policy_hash="$(sha256sum "$policy_file" | awk '{print $1}')"

remote_env() {
    local -a values=(
        "ACTION=${action}"
        "RUN_ID=${run_id}"
        "ROLLBACK_RUN_ID=${rollback_run_id}"
        "DEPLOY_PATH=${deploy_path}"
        "COMPOSE_PROJECT=${compose_project}"
        "DATABASE_NAME=${database_name}"
        "APP_PORT=${app_port}"
        "EXPECTED_EMERGENCY_COUNT=${expected_emergency_count}"
        "LOCAL_POLICY_HASH=${local_policy_hash}"
    )
    if [[ -n "${CD_RELEASE_TAG:-}" ]]; then
        values+=(
            "CD_RELEASE_TAG=${CD_RELEASE_TAG}"
            "CD_RELEASE_SHA=${CD_RELEASE_SHA}"
            "CD_APP_IMAGE=${CD_APP_IMAGE}"
            "CD_OPA_IMAGE=${CD_OPA_IMAGE}"
        )
    fi

    local item
    local -a quoted=()
    for item in "${values[@]}"; do
        quoted+=("$(printf '%q' "$item")")
    done
    printf '%s\n' "${quoted[*]}"
}

preflight_remote() {
    local local_script
    local_script="$(mktemp)"
    cat > "$local_script" <<'REMOTE'
set -Eeuo pipefail

cd "$DEPLOY_PATH"
if ! sudo docker compose version >/dev/null 2>&1; then
    echo "预发布服务器缺少 Docker Compose v2。" >&2
    exit 1
fi

required_files=(
    docker/compose.yml
    docker/compose.prod.env
    docker/app/template/app.prod.env
    docker/litellm/template/litellm.prod.env
    docker/mem0/template/mem0.prod.env
    docker/mem0-dashboard/template/mem0-dashboard.prod.env
    docker/opa/template/opa.prod.env
    docker/postgres/template/postgres.prod.env
)
for required_file in "${required_files[@]}"; do
    if [[ ! -f "$required_file" ]]; then
        echo "预发布环境缺少真实配置文件: ${required_file}" >&2
        exit 1
    fi
done

if [[ -f docker/compose.stage5-release.env ]]; then
    compose_cmd=(
        sudo docker compose
        --project-name "$COMPOSE_PROJECT"
        --env-file docker/compose.prod.env
        --env-file docker/compose.stage5-release.env
        -f docker/compose.yml
    )
else
    compose_cmd=(
        sudo docker compose
        --project-name "$COMPOSE_PROJECT"
        --env-file docker/compose.prod.env
        -f docker/compose.yml
    )
fi
"${compose_cmd[@]}" config --quiet

database_exists="$(
    "${compose_cmd[@]}" exec -T postgres sh -c \
        "psql -U \"\$POSTGRES_USER\" -d postgres -Atc \"SELECT 1 FROM pg_database WHERE datname = '$DATABASE_NAME'\""
        </dev/null
)"
if [[ "$database_exists" != "1" ]]; then
    echo "预发布 PostgreSQL 中不存在目标数据库: ${DATABASE_NAME}" >&2
    exit 1
fi

echo "预发布配置解析与数据库连通性检查通过。"
REMOTE
    run_remote_script_file "$local_script"
}

create_backup_remote() {
    local local_script
    local_script="$(mktemp)"
    cat > "$local_script" <<'REMOTE'
set -Eeuo pipefail

cd "$DEPLOY_PATH"
backup_dir="${DEPLOY_PATH}/stage5-backups/${RUN_ID}"
mkdir -p "$backup_dir"
chmod 0700 "$backup_dir"

if [[ ! -f docker/compose.stage5-release.env ]]; then
    compose_cmd=(
        sudo docker compose
        --project-name "$COMPOSE_PROJECT"
        --env-file docker/compose.prod.env
        -f docker/compose.yml
    )
else
    compose_cmd=(
        sudo docker compose
        --project-name "$COMPOSE_PROJECT"
        --env-file docker/compose.prod.env
        --env-file docker/compose.stage5-release.env
        -f docker/compose.yml
    )
fi

app_container="$("${compose_cmd[@]}" ps --all --quiet app)"
opa_container="$("${compose_cmd[@]}" ps --all --quiet opa)"
if [[ -z "$app_container" ]] || [[ -z "$opa_container" ]]; then
    echo "无法备份镜像版本：app 或 OPA 容器未运行。" >&2
    exit 1
fi

previous_app_image="$(sudo docker inspect --format '{{.Config.Image}}' "$app_container")"
previous_opa_image="$(sudo docker inspect --format '{{.Config.Image}}' "$opa_container")"
printf 'PREVIOUS_APP_IMAGE=%q\n' "$previous_app_image" > "$backup_dir/previous-images.env"
printf 'PREVIOUS_OPA_IMAGE=%q\n' "$previous_opa_image" >> "$backup_dir/previous-images.env"

alembic_version="$(
    "${compose_cmd[@]}" exec -T postgres sh -c \
        "psql -U \"\$POSTGRES_USER\" -d \"$DATABASE_NAME\" -Atc 'SELECT version_num FROM alembic_version'"
        </dev/null
)"
printf '%s\n' "$alembic_version" > "$backup_dir/alembic_version.txt"

"${compose_cmd[@]}" exec -T postgres sh -c \
    "pg_dump -U \"\$POSTGRES_USER\" \"$DATABASE_NAME\"" \
    </dev/null | gzip > "$backup_dir/${DATABASE_NAME}.sql.gz"
"${compose_cmd[@]}" ps > "$backup_dir/compose-ps-before.txt"
[[ -s "$backup_dir/alembic_version.txt" ]]
[[ -s "$backup_dir/${DATABASE_NAME}.sql.gz" ]]
[[ -s "$backup_dir/compose-ps-before.txt" ]]

echo "$backup_dir"
REMOTE
    run_remote_script_file "$local_script"
}

if [[ "$action" == "deploy" ]]; then
    if [[ -n "$registry_username" ]] || [[ -n "$registry_password" ]]; then
        if [[ -z "$registry_username" ]] || [[ -z "$registry_password" ]]; then
            echo "镜像仓库用户名和密码必须同时提供。" >&2
            exit 1
        fi
        printf '%s' "$registry_password" | "${ssh_args[@]}" "$remote" \
            "sudo docker login $(printf '%q' "$registry_host") --username $(printf '%q' "$registry_username") --password-stdin" >/dev/null
    fi

    echo "执行阶段 5 预发布预检..."
    preflight_remote

    echo "创建阶段 5 部署前备份..."
    backup_dir="$(create_backup_remote)"
    if [[ -z "$backup_dir" ]]; then
        echo "阶段 5 备份脚本未返回备份目录，拒绝继续部署。" >&2
        exit 1
    fi
    echo "备份目录: ${backup_dir}"

    echo "同步预发布编排包..."
    CD_PROD_SSH_HOST="$ssh_host" \
    CD_PROD_SSH_PORT="$ssh_port" \
    CD_PROD_SSH_USER="$ssh_user" \
    CD_PROD_SSH_KEY_PATH="$ssh_key" \
    CD_PROD_DEPLOY_PATH="$deploy_path" \
        bash scripts/cd/repository/sync-production-bundle.sh

    echo "部署 core 应用镜像与 OPA 策略镜像..."
    deploy_script="$(mktemp)"
    cat > "$deploy_script" <<'REMOTE'
set -Eeuo pipefail

cd "$DEPLOY_PATH"
lock_dir="${DEPLOY_PATH}/.stage5-deploy-lock"
if ! mkdir "$lock_dir" 2>/dev/null; then
    echo "检测到已有阶段 5 部署任务，拒绝并发执行。" >&2
    exit 1
fi
trap 'rmdir "$lock_dir" >/dev/null 2>&1 || true' EXIT

compose_cmd=(
    sudo docker compose
    --project-name "$COMPOSE_PROJECT"
    --env-file docker/compose.prod.env
    --env-file docker/compose.stage5-release.env
    -f docker/compose.yml
)

umask 027
cat > docker/compose.stage5-release.env <<ENV
VET_AGENT_IMAGE=${CD_APP_IMAGE}
OPA_IMAGE=${CD_OPA_IMAGE}
ENV

"${compose_cmd[@]}" config --quiet
"${compose_cmd[@]}" pull app worker opa

app_variant="$(sudo docker image inspect "$CD_APP_IMAGE" --format '{{index .Config.Labels "io.vancer.vet-agent.image-variant"}}')"
app_revision="$(sudo docker image inspect "$CD_APP_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
opa_revision="$(sudo docker image inspect "$CD_OPA_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
if [[ "$app_variant" != "core" ]]; then
    echo "阶段 5 只允许部署 core 应用镜像，当前变体: ${app_variant}。" >&2
    exit 1
fi
if [[ "$app_revision" != "$CD_RELEASE_SHA" ]] || [[ "$opa_revision" != "$CD_RELEASE_SHA" ]]; then
    echo "Release 镜像 revision 与 Git tag 不一致。" >&2
    exit 1
fi

# 预发布没有双 OPA 热切换拓扑。先停止旧 app / worker，避免旧应用 payload
# 在 seed 和迁移窗口内调用阶段 4 OPA，形成跨版本策略契约。Dashboard 也可能
# 持有 backend network endpoint；一并停止，避免 Compose 重建网络时失败。
"${compose_cmd[@]}" stop app worker mem0-dashboard

"${compose_cmd[@]}" up \
    -d \
    --no-build \
    --pull never \
    --wait \
    postgres \
    litellm \
    mem0 \
    mem0-dashboard \
    opa

# 当前预发布库仍可能处于 0019 并携带旧 EMERGENCY_RED_FLAG。
# 0021 迁移按设计拒绝修复存量数据，因此必须先用 Release 镜像导入已审核资产。
echo "执行发布态资产 seed..."
"${compose_cmd[@]}" run --rm --pull never seed </dev/null

echo "执行 Alembic 迁移..."
"${compose_cmd[@]}" run --rm --pull never migrate </dev/null

echo "校验阶段 4 数据库契约..."
asset_result="$(
    "${compose_cmd[@]}" exec -T postgres sh -c \
        "psql -U \"\$POSTGRES_USER\" -d \"$DATABASE_NAME\" -v ON_ERROR_STOP=1 -At -F '|' -c \"
            SELECT
                count(*),
                count(DISTINCT code),
                count(*) FILTER (
                    WHERE code !~ '^EMERGENCY_MODE_[A-Z0-9]{10}$'
                ),
                count(*) FILTER (
                    WHERE metadata -> 'code_governance' ->> 'strategy'
                        IS DISTINCT FROM 'opaque_asset_identity_v1'
                    OR metadata -> 'code_governance' ->> 'legacy_code' IS NULL
                ),
                (
                    SELECT count(*)
                    FROM (
                        SELECT code
                        FROM clinical_safety_assets
                        WHERE asset_type = 'emergency_red_flag'
                          AND review_status = 'approved'
                          AND enabled IS TRUE
                        GROUP BY code
                        HAVING count(*) > 1
                    ) duplicated_codes
                )
            FROM clinical_safety_assets
            WHERE asset_type = 'emergency_red_flag'
              AND review_status = 'approved'
              AND enabled IS TRUE;\"" \
        </dev/null
)"
IFS='|' read -r asset_count distinct_code_count invalid_code_count governance_missing_count duplicate_code_count <<< "$asset_result"

chunk_result="$(
    "${compose_cmd[@]}" exec -T postgres sh -c \
        "psql -U \"\$POSTGRES_USER\" -d \"$DATABASE_NAME\" -v ON_ERROR_STOP=1 -At -F '|' -c \"
            SELECT
                count(*) FILTER (
                    WHERE chunk.metadata ->> 'code' IS DISTINCT FROM asset.code
                ),
                count(*) FILTER (
                    WHERE chunk.embedding IS NULL
                        OR chunk.embedding_model IS NULL
                        OR chunk.embedding_dimension IS NULL
                )
            FROM clinical_safety_assets AS asset
            JOIN clinical_safety_chunks AS chunk
              ON chunk.asset_id = asset.asset_id
            WHERE asset.asset_type = 'emergency_red_flag'
              AND asset.review_status = 'approved'
              AND asset.enabled IS TRUE
              AND chunk.review_status = 'approved'
              AND chunk.enabled IS TRUE;\"" \
        </dev/null
)"
IFS='|' read -r chunk_code_mismatch_count invalid_embedding_count <<< "$chunk_result"

alembic_version="$(
    "${compose_cmd[@]}" exec -T postgres sh -c \
        "psql -U \"\$POSTGRES_USER\" -d \"$DATABASE_NAME\" -Atc 'SELECT version_num FROM alembic_version'"
        </dev/null
)"

if [[ "$alembic_version" != "0021_clinical_safety_emergency_asset_codes" ]]; then
    echo "Alembic 版本不符合阶段 5 期望: ${alembic_version}" >&2
    exit 1
fi
if [[ "$asset_count" != "$EXPECTED_EMERGENCY_COUNT" ]] \
    || [[ "$asset_count" != "$distinct_code_count" ]] \
    || [[ "$invalid_code_count" != "0" ]] \
    || [[ "$governance_missing_count" != "0" ]] \
    || [[ "$duplicate_code_count" != "0" ]] \
    || [[ "$chunk_code_mismatch_count" != "0" ]] \
    || [[ "$invalid_embedding_count" != "0" ]]; then
    echo "阶段 4 临床安全资产契约校验失败。" >&2
    echo "asset_count=${asset_count}, distinct_code_count=${distinct_code_count}, invalid_code_count=${invalid_code_count}" >&2
    echo "governance_missing_count=${governance_missing_count}, duplicate_code_count=${duplicate_code_count}" >&2
    echo "chunk_code_mismatch_count=${chunk_code_mismatch_count}, invalid_embedding_count=${invalid_embedding_count}" >&2
    exit 1
fi

remote_policy_hash="$(
    "${compose_cmd[@]}" exec -T opa sha256sum /opa/policies/clinical_safety.rego \
        </dev/null \
        | awk '{print $1}'
)"
if [[ "$remote_policy_hash" != "$LOCAL_POLICY_HASH" ]]; then
    echo "OPA 镜像内 clinical_safety.rego 与 Release 策略哈希不一致。" >&2
    exit 1
fi

"${compose_cmd[@]}" up \
    -d \
    --no-build \
    --pull never \
    --no-deps \
    --force-recreate \
    --wait \
    app \
    worker

"${compose_cmd[@]}" exec -T app sh -c '
    test "${VET_AGENT_IMAGE_VARIANT:-core}" = "core"
    test "${ENABLE_INPUT_SAFETY_GUARDRAILS:-false}" = "false"
    test "${ENABLE_OUTPUT_SAFETY_GUARDRAILS:-false}" = "false"
    test "${ENABLE_OUTPUT_SAFETY:-true}" = "true"
    test "${OUTPUT_SAFETY_MODE:-observe}" = "observe"
' </dev/null

ready=false
for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:${APP_PORT}/ready" >/dev/null 2>&1; then
        ready=true
        break
    fi
    sleep 2
done
if [[ "$ready" != "true" ]]; then
    echo "阶段 5 部署后 /ready 检查失败。" >&2
    exit 1
fi

"${compose_cmd[@]}" ps
backup_dir="${DEPLOY_PATH}/stage5-backups/${RUN_ID}"
cat > "$backup_dir/deployment-summary.env" <<SUMMARY
RUN_ID=${RUN_ID}
RELEASE_TAG=${CD_RELEASE_TAG}
RELEASE_SHA=${CD_RELEASE_SHA}
APP_IMAGE=${CD_APP_IMAGE}
OPA_IMAGE=${CD_OPA_IMAGE}
APP_PORT=${APP_PORT}
ALEMBIC_VERSION=${alembic_version}
EMERGENCY_ASSET_COUNT=${asset_count}
SUMMARY
chmod 0600 "$backup_dir/deployment-summary.env"

echo "阶段 5 预发布部署完成。"
REMOTE
    run_remote_script_file "$deploy_script"
else
    echo "回滚阶段 5 app / worker / OPA 运行时镜像..."
    rollback_script="$(mktemp)"
    cat > "$rollback_script" <<'REMOTE'
set -Eeuo pipefail

cd "$DEPLOY_PATH"
backup_dir="${DEPLOY_PATH}/stage5-backups/${ROLLBACK_RUN_ID}"
if [[ ! -f "$backup_dir/previous-images.env" ]]; then
    echo "找不到回滚镜像记录: ${backup_dir}/previous-images.env" >&2
    exit 1
fi

source "$backup_dir/previous-images.env"
if [[ -z "${PREVIOUS_APP_IMAGE:-}" ]] || [[ -z "${PREVIOUS_OPA_IMAGE:-}" ]]; then
    echo "回滚镜像记录不完整。" >&2
    exit 1
fi

lock_dir="${DEPLOY_PATH}/.stage5-deploy-lock"
if ! mkdir "$lock_dir" 2>/dev/null; then
    echo "检测到已有阶段 5 部署任务，拒绝并发回滚。" >&2
    exit 1
fi
trap 'rmdir "$lock_dir" >/dev/null 2>&1 || true' EXIT

umask 027
cat > docker/compose.stage5-release.env <<ENV
VET_AGENT_IMAGE=${PREVIOUS_APP_IMAGE}
OPA_IMAGE=${PREVIOUS_OPA_IMAGE}
ENV

compose_cmd=(
    sudo docker compose
    --project-name "$COMPOSE_PROJECT"
    --env-file docker/compose.prod.env
    --env-file docker/compose.stage5-release.env
    -f docker/compose.yml
)
"${compose_cmd[@]}" config --quiet
"${compose_cmd[@]}" pull app worker opa
"${compose_cmd[@]}" stop app worker mem0-dashboard
"${compose_cmd[@]}" up \
    -d \
    --no-build \
    --pull never \
    --wait \
    postgres \
    litellm \
    mem0 \
    mem0-dashboard \
    opa
"${compose_cmd[@]}" up \
    -d \
    --no-build \
    --pull never \
    --no-deps \
    --force-recreate \
    --wait \
    app \
    worker

ready=false
for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:${APP_PORT}/ready" >/dev/null 2>&1; then
        ready=true
        break
    fi
    sleep 2
done
if [[ "$ready" != "true" ]]; then
    echo "回滚后 /ready 检查失败。" >&2
    exit 1
fi

echo "运行时镜像已回滚；数据库未自动降级。如需恢复数据，请单独评估备份 ${backup_dir}。"
REMOTE
    run_remote_script_file "$rollback_script"
fi

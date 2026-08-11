#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/ci/repository/cd-layout-check.sh
# 作用: 校验兽医 Agent 仓库的 CD 文件树、Release 镜像规则与生产构建边界。
# 范围: 只执行文件和文本静态检查，不构建镜像、不连接 Registry 或生产服务器。
# 说明: 本检查绑定当前仓库的 docker 服务分层布局，不属于可直接分发的通用 CI 门禁。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"

required_files=(
    .github/workflows/cd.yml
    docker/app/Dockerfile
    docker/app/Dockerfile.dev
    docker/app/template/app.dev.env.template
    docker/app/template/app.prod.env.template
    docker/compose.dev.yml
    docker/compose.yml
    docker/compose.dev.env.template
    docker/compose.prod.env.template
    docker/litellm/litellm.yml
    docker/litellm/template/litellm.dev.env.template
    docker/litellm/template/litellm.prod.env.template
    docker/mem0/Dockerfile
    docker/mem0/application.yml
    docker/mem0/configure_mem0.py
    docker/mem0/entrypoint.sh
    docker/mem0/render_env.py
    docker/mem0/template/mem0.dev.env.template
    docker/mem0/template/mem0.prod.env.template
    docker/mem0-dashboard/Dockerfile
    docker/mem0-dashboard/application.yml
    docker/mem0-dashboard/entrypoint.sh
    docker/mem0-dashboard/render_env.py
    docker/mem0-dashboard/template/mem0-dashboard.dev.env.template
    docker/mem0-dashboard/template/mem0-dashboard.prod.env.template
    docker/postgres/init/10-bootstrap-logical-databases.sh
    docker/postgres/ops/ensure-extensions.sh
    docker/postgres/ops/vector-smoke-check.sh
    docker/postgres/postgresql.conf
    docker/postgres/template/postgres.dev.env.template
    docker/postgres/template/postgres.prod.env.template
)

for required_file in "${required_files[@]}"; do
    if [ ! -f "$required_file" ]; then
        echo "CD 布局缺少必要文件: ${required_file}" >&2
        exit 1
    fi
done

# deploy/、release.env 等旧结构会形成第二套部署可信源，必须保持不存在。
for forbidden_path in \
    deploy \
    release.env \
    release.env.template \
    docker/release.env \
    docker/release.env.template \
    scripts/cd/common/render-release-env.sh; do
    if [ -e "$forbidden_path" ]; then
        echo "检测到已废弃的部署或版本文件: ${forbidden_path}" >&2
        exit 1
    fi
done

# docker 配置文件采用白名单约束，避免新增第二套 compose/env/yml 可信源。
while IFS= read -r configuration_file; do
    case "$configuration_file" in
        docker/compose.dev.env.template|\
        docker/compose.prod.env.template|\
        docker/compose.dev.yml|\
        docker/compose.yml|\
        docker/app/template/app.dev.env.template|\
        docker/app/template/app.prod.env.template|\
        docker/litellm/litellm.yml|\
        docker/litellm/template/litellm.dev.env.template|\
        docker/litellm/template/litellm.prod.env.template|\
        docker/mem0/application.yml|\
        docker/mem0/template/mem0.dev.env.template|\
        docker/mem0/template/mem0.prod.env.template|\
        docker/mem0-dashboard/application.yml|\
        docker/mem0-dashboard/template/mem0-dashboard.dev.env.template|\
        docker/mem0-dashboard/template/mem0-dashboard.prod.env.template|\
        docker/postgres/postgresql.conf|\
        docker/postgres/template/postgres.dev.env.template|\
        docker/postgres/template/postgres.prod.env.template)
            ;;
        *)
            echo "检测到未纳入 CD 布局白名单的 docker 配置文件: ${configuration_file}" >&2
            exit 1
            ;;
    esac
done < <(
    find docker -type f \
        \( -name '*.env' -o -name '*.env.*' -o -name '*.yml' -o -name '*.yaml' \) \
        -print \
        | sort
)

if grep -n -E '^[[:space:]]+build:' docker/compose.yml; then
    echo "正式 Compose 不得声明 build，生产环境只能使用预编译镜像。" >&2
    exit 1
fi

if grep -R -n -E '@sha256:[[:xdigit:]]{32,}' \
    .github/workflows/cd.yml \
    scripts/cd \
    docker/compose.yml; then
    echo "CD 部署不得使用 digest，镜像版本必须由 GitHub Release tag 解析。" >&2
    exit 1
fi

if grep -R -n -E '(^|[[:space:]])--build([[:space:]]|$)' scripts/cd/repository; then
    echo "生产部署脚本不得包含 --build 参数。" >&2
    exit 1
fi

for cd_script in scripts/cd/common/*.sh scripts/cd/repository/*.sh; do
    if [ ! -x "$cd_script" ]; then
        echo "CD 脚本缺少可执行权限: ${cd_script}" >&2
        exit 1
    fi
done

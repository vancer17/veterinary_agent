#!/usr/bin/env bash
# =============================================================================
# 文件: scripts/ci/common/dry-run-check.sh
# 作用: 执行无状态变更的 CI/CD dry-run 前置校验。
# 范围: 只渲染配置、校验文件和输出镜像计划，不启动数据库、不构建镜像、不推送镜像。
# 说明: 适合生产手动部署前或 CD 接入前验证仓库配置完整性。
# =============================================================================

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"

registry_image="${CI_REGISTRY_IMAGE:-crpi-efmmpn9a6t9mspwy.cn-hangzhou.personal.cr.aliyuncs.com/vancer-saas/veterinary_agent}"
guardrails_registry_image="${CI_APP_GUARDRAILS_REGISTRY_IMAGE:-crpi-efmmpn9a6t9mspwy.cn-hangzhou.personal.cr.aliyuncs.com/vancer-saas/veterinary_agent-guardrails}"
mem0_registry_image="${CI_MEM0_REGISTRY_IMAGE:-crpi-efmmpn9a6t9mspwy.cn-hangzhou.personal.cr.aliyuncs.com/vancer-saas/veterinary_agent-mem0}"
mem0_dashboard_registry_image="${CI_MEM0_DASHBOARD_REGISTRY_IMAGE:-crpi-efmmpn9a6t9mspwy.cn-hangzhou.personal.cr.aliyuncs.com/vancer-saas/veterinary_agent-mem0-dashboard}"
opa_registry_image="${CI_OPA_REGISTRY_IMAGE:-crpi-efmmpn9a6t9mspwy.cn-hangzhou.personal.cr.aliyuncs.com/vancer-saas/veterinary_agent-opa}"
release_tag_example="${CI_RELEASE_TAG_EXAMPLE:-vX.Y.Z}"

bash scripts/ci/common/static-check.sh
bash scripts/ci/common/compose-config-check.sh
bash scripts/ci/common/secret-boundary-check.sh

required_paths=(
    docker/app/Dockerfile
    docker/app/Dockerfile.dev
    docker/app/entrypoint.sh
    docker/app/template/app.dev.env.template
    docker/app/template/app.prod.env.template
    docker/compose.yml
    docker/compose.dev.yml
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
    docker/opa/Dockerfile
    docker/opa/application.yml
    docker/opa/entrypoint.sh
    docker/opa/policies/bootstrap.rego
    docker/opa/policies/clinical_safety.rego
    docker/opa/policies/consultation_answerability.rego
    docker/opa/policies/input_safety.rego
    docker/opa/policies/system_log.rego
    docker/opa/policies/task_routing.rego
    docker/opa/template/opa.dev.env.template
    docker/opa/template/opa.prod.env.template
    docker/opa/tests/bootstrap_test.rego
    docker/postgres/init/10-bootstrap-logical-databases.sh
    docker/postgres/ops/ensure-extensions.sh
    docker/postgres/ops/vector-smoke-check.sh
    docker/postgres/postgresql.conf
    docker/postgres/template/postgres.dev.env.template
    docker/postgres/template/postgres.prod.env.template
    scripts/runtime/install_nltk_data.py
)

for required_path in "${required_paths[@]}"; do
    if [ ! -e "$required_path" ]; then
        echo "dry-run 缺少必要路径: ${required_path}" >&2
        exit 1
    fi
done

echo "dry-run 应用镜像模板: ${registry_image}:${release_tag_example}"
echo "dry-run Guardrails 增强应用镜像模板: ${guardrails_registry_image}:${release_tag_example}"
echo "dry-run Mem0 镜像模板: ${mem0_registry_image}:${release_tag_example}"
echo "dry-run Mem0 Dashboard 镜像模板: ${mem0_dashboard_registry_image}:${release_tag_example}"
echo "dry-run OPA 镜像模板: ${opa_registry_image}:${release_tag_example}"
echo "dry-run 不执行镜像构建、镜像推送或环境部署。"

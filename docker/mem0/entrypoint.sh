#!/usr/bin/env bash
# =============================================================================
# 文件: docker/mem0/entrypoint.sh
# 作用: 作为自托管 Mem0 REST Server 镜像的统一启动入口。
# 范围: 渲染 application.yml、执行 Mem0 Alembic、写入项目配置覆盖并启动 uvicorn。
# 说明: 本脚本在镜像构建期复制进入容器；生产 Compose 不挂载脚本，不现场编译镜像。
# =============================================================================

set -Eeuo pipefail

if [ "${1:-serve}" != "serve" ]; then
    exec "$@"
fi

config_file="${MEM0_CONFIG_FILE:-/app/config/application.yml}"

if [ ! -f "$config_file" ]; then
    echo "Mem0 配置文件不存在: ${config_file}" >&2
    exit 1
fi

# 将非敏感服务配置从 application.yml 渲染为上游 Mem0 server 当前识别的环境变量。
rendered_env="$(python /opt/vet-agent-mem0/render_env.py "$config_file")"
eval "$rendered_env"

: "${OPENAI_API_KEY:?OPENAI_API_KEY 必须通过 Mem0 env 文件注入。}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD 必须通过 Mem0 env 文件注入。}"

if [ "${AUTH_DISABLED:-false}" != "true" ]; then
    : "${JWT_SECRET:?JWT_SECRET 必须通过 Mem0 env 文件注入。}"
fi

alembic upgrade head
python /opt/vet-agent-mem0/configure_mem0.py

uvicorn_args=(
    main:app
    --host "${MEM0_SERVER_HOST:-0.0.0.0}"
    --port "${MEM0_SERVER_PORT:-8000}"
)

if [ "${MEM0_SERVER_WORKERS:-1}" -gt 1 ]; then
    uvicorn_args+=(--workers "${MEM0_SERVER_WORKERS}")
fi

exec uvicorn "${uvicorn_args[@]}"

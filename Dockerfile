# =============================================================================
# Dockerfile — veterinary_agent 运行时镜像
# =============================================================================
#
# 【用途】
#   供 Backend CD 构建并推送至阿里云 ACR；由 infra-ops Ansible app_deploy
#   在 dev-01 上 docker compose pull && up -d 拉取运行。
#
# 【约定】
#   - Python 3.13，依赖由 uv 锁定安装（不含 dev 组）
#   - 监听 8080（与 infra app.listen.port / 容器映射一致）
#   - 当前为脚手架占位入口；FastAPI /healthz /readyz 由后续应用实现替换
#
# =============================================================================

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev --no-editable

# -----------------------------------------------------------------------------

FROM python:3.13-slim-bookworm AS runtime

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --system app && useradd --system --gid app app

COPY --from=builder --chown=app:app /app /app

USER app

EXPOSE 8080

# 占位：保持进程存活，待 ASGI 应用就绪后改为 uvicorn 启动
CMD ["sleep", "infinity"]

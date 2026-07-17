# -----------------------------------------------------------------------------
# File: Dockerfile
# Purpose: Build the production Vet Agent application image from uv.lock.
# Dependency source: uv uses the Tsinghua PyPI mirror from pyproject.toml and UV_DEFAULT_INDEX.
# -----------------------------------------------------------------------------

ARG PYTHON_BASE_IMAGE=ghcr.io/astral-sh/uv:python3.12-bookworm
FROM ${PYTHON_BASE_IMAGE}

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_DEFAULT_INDEX=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH=/app/src

COPY pyproject.toml uv.lock README.md ./
COPY alembic.ini ./
COPY alembic ./alembic
COPY data ./data
COPY scripts ./scripts
COPY src ./src

RUN uv sync --frozen --no-dev \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app/.data \
    && chown -R app:app /app /opt/venv

USER app

EXPOSE 8000

CMD ["uvicorn", "vet_agent.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

# -----------------------------------------------------------------------------
# File: docker/mem0/Dockerfile
# Purpose: Build a production-ready Mem0 REST Server image from the upstream
#          Mem0 image used by this project.
# Notes: The current upstream/private Mem0 image may include psycopg without a
#        working libpq wrapper. This thin image adds psycopg[binary] at build
#        time so Mem0 Alembic can connect to PostgreSQL reliably.
# -----------------------------------------------------------------------------

ARG MEM0_BASE_IMAGE=crpi-efmmpn9a6t9mspwy.cn-hangzhou.personal.cr.aliyuncs.com/vancer-saas/mem0:latest
FROM ${MEM0_BASE_IMAGE}

USER root

ENV PIP_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
    PIP_NO_CACHE_DIR=1

RUN python -m pip install "psycopg[binary]>=3.2,<4"

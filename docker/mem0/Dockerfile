# =============================================================================
# 文件: docker/mem0/Dockerfile
# 作用: 基于项目使用的上游 Mem0 镜像构建生产可用的 Mem0 REST Server 镜像。
# 范围: 仅补齐 Mem0 运行所需的 PostgreSQL 驱动能力，不实现业务逻辑。
# 说明: 当前上游或私有 Mem0 镜像可能缺少可用 libpq wrapper，本镜像在构建期
#       安装 psycopg[binary]，保证 Mem0 Alembic 可稳定连接 PostgreSQL。
# =============================================================================

ARG MEM0_BASE_IMAGE=crpi-efmmpn9a6t9mspwy.cn-hangzhou.personal.cr.aliyuncs.com/vancer-saas/mem0:latest
FROM ${MEM0_BASE_IMAGE}

USER root

ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_NO_CACHE_DIR=1

RUN python -m pip install "psycopg[binary]>=3.2,<4"

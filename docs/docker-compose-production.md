<!--
File: docs/docker-compose-production.md
Purpose: Production Docker Compose operating notes for Vet Agent.
Database topology: One pgvector PostgreSQL instance with isolated logical databases.
-->

# Docker Compose Production

生产环境使用 `docker-compose.yml`，不挂载宿主机源码，不依赖平台专用脚本，不在 app 容器启动时自动迁移数据库。迁移和 seed 由 Make 的显式步骤执行。

## 准备环境文件

```bash
cp deploy/env/prod/compose.env.template deploy/env/prod/compose.env
cp deploy/env/prod/services/postgres.env.template deploy/env/prod/services/postgres.env
cp deploy/env/prod/services/litellm.env.template deploy/env/prod/services/litellm.env
cp deploy/env/prod/services/mem0.env.template deploy/env/prod/services/mem0.env
cp deploy/env/prod/services/app.env.template deploy/env/prod/services/app.env
```

`Makefile` 会优先使用 `deploy/env/prod/compose.env`；如果该文件不存在，则回退到 `compose.env.template` 仅用于配置检查。真实生产启动前必须创建上述 `.env` 文件。

必须填写：

```text
deploy/env/prod/services/postgres.env:
  POSTGRES_PASSWORD
  VET_AGENT_POSTGRES_PASSWORD
  LITELLM_POSTGRES_PASSWORD
  MEM0_POSTGRES_PASSWORD

deploy/env/prod/services/litellm.env:
  DATABASE_URL
  LITELLM_MASTER_KEY
  DASHSCOPE_API_KEY

deploy/env/prod/services/mem0.env:
  OPENAI_API_KEY
  ADMIN_API_KEY
  JWT_SECRET
  POSTGRES_PASSWORD

deploy/env/prod/services/app.env:
  DATABASE_URL
  LITELLM_API_KEY
  MEM0_API_KEY
  VET_AGENT_API_KEYS
  OSS_BUCKET
  OSS_ENDPOINT
```

`OPENAI_API_KEY`、`LITELLM_MASTER_KEY`、`LITELLM_API_KEY` 应保持同一 LiteLLM master key；`ADMIN_API_KEY` 与 `MEM0_API_KEY` 应保持同一 Mem0 API key。

生产默认开启：

```text
REQUIRE_API_AUTH=true
REQUIRE_AUTH_USER_MATCH=true
PET_AUTHORIZATION_MODE=strict
SESSION_POLICY_MODE=strict
ENABLE_MEM0=true
ENABLE_RAG_EMBEDDINGS=true
SEED_WITH_EMBEDDINGS=true
```

## 上线

```bash
make prod-config
make prod-up
make prod-ready
```

`make prod-up` 会按顺序执行：

```text
prod-build -> prod-deps -> prod-migrate -> prod-seed -> start app
```

如果只需要执行迁移或 seed：

```bash
make prod-db-extensions
make prod-migrate
make prod-seed
```

## 常用运维命令

```bash
make prod-ps
make prod-logs
make prod-app-logs
make prod-litellm-logs
make prod-mem0-logs
make prod-mem0-db-logs
make prod-restart
make prod-down
```

进入容器：

```bash
make prod-shell
make prod-db-shell
```

## 手动请求

```bash
export VET_AGENT_API_KEY="<one value from VET_AGENT_API_KEYS>"

curl http://127.0.0.1:8000/health

curl -H "Authorization: Bearer ${VET_AGENT_API_KEY}" \
  http://127.0.0.1:8000/ready

curl -X POST http://127.0.0.1:8000/agent/turns \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${VET_AGENT_API_KEY}" \
  --data-binary "@scripts/dev_payloads/business_followup_first.json"
```

## 镜像站

如果生产主机拉取镜像较慢，在 `deploy/env/prod/compose.env` 中覆盖：

```text
deploy/env/prod/compose.env:
PYTHON_BASE_IMAGE=你的镜像站/astral-sh/uv:python3.12-bookworm
PGVECTOR_IMAGE=你的镜像站/pgvector/pgvector:pg16
MEM0_IMAGE=你的镜像站/vancer-saas/mem0:latest
LITELLM_IMAGE=你的镜像站/berriai/litellm:main-stable
```

`MEM0_IMAGE` 默认指向私有仓库中的 Mem0 REST Server 镜像；`make prod-build` 只构建 Agent app，Mem0 不再依赖本地 Mem0 源码缓存，也不会在启动前拉取 GitHub 仓库。

## 数据库拓扑

生产 Compose 只启动一个 `postgres` 服务，使用 `pgvector/pgvector` 镜像。首次初始化时，`docker/postgres/init/10-bootstrap-logical-databases.sh` 通过 PostgreSQL 官方初始化钩子创建逻辑库和登录角色：

```text
vet_agent     -> Agent 业务数据、RAG、记忆与 trace
litellm       -> LiteLLM 元数据
mem0_vector   -> Mem0 pgvector 语义记忆
mem0_app      -> Mem0 REST Server 用户、API key、请求日志等
```

表结构迁移不在初始化脚本中手写：Agent 使用本项目 Alembic，Mem0 使用镜像内官方 Alembic，LiteLLM 使用自身启动迁移。

PostgreSQL 扩展由初始化脚本和 `postgres-extensions` 一次性任务负责：

```text
vet_agent   -> vector, pg_trgm
mem0_vector -> vector
```

重要：`docker/postgres/init` 只会在 PostgreSQL 数据卷为空时执行。若生产机已经存在数据卷，首次迁移前需先执行 `make prod-db-extensions`，补齐扩展后再运行 `make prod-migrate`。若从旧的多 PostgreSQL 容器编排升级，需先用 `pg_dump` / `pg_restore` 或等价备份工具迁移旧 `litellm-postgres`、`mem0-postgres` 数据；新环境直接初始化则无需手工建表。

## 服务边界

生产 Compose 包含：

- `app`: FastAPI Agent API
- `postgres`: 共享 PostgreSQL + pgvector，内部按逻辑库隔离 Agent、LiteLLM 和 Mem0
- `litellm`: LiteLLM Proxy，持有通义千问 API Key
- `mem0`: 官方 Mem0 REST Server
- `migrate`: 一次性 Alembic 迁移任务
- `seed`: 一次性规则/RAG seed 任务

只有 `app` 发布宿主机端口；PostgreSQL、LiteLLM、Mem0 默认仅在 Compose 网络内访问。

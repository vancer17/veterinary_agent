<!--
File: docs/docker-compose-production.md
Purpose: Production Docker Compose operating notes for Vet Agent.
Database topology: One pgvector PostgreSQL instance with isolated logical databases.
-->

# Docker Compose Production

生产环境使用 `docker/compose.yml`，不挂载宿主机源码，不声明 `build`，不依赖平台专用脚本。正式 CD 通过 GitHub Release tag 注入 `VET_AGENT_IMAGE`、`MEM0_IMAGE` 与 `MEM0_DASHBOARD_IMAGE`，生产服务器只拉取预编译镜像并执行迁移与启动。

## 准备环境文件

```bash
cp docker/compose.prod.env.template docker/compose.prod.env
cp docker/postgres/template/postgres.prod.env.template docker/postgres/template/postgres.prod.env
cp docker/litellm/template/litellm.prod.env.template docker/litellm/template/litellm.prod.env
cp docker/mem0/template/mem0.prod.env.template docker/mem0/template/mem0.prod.env
cp docker/mem0-dashboard/template/mem0-dashboard.prod.env.template docker/mem0-dashboard/template/mem0-dashboard.prod.env
cp docker/app/template/app.prod.env.template docker/app/template/app.prod.env
```

`Makefile` 会优先使用 `docker/compose.prod.env`；如果该文件不存在，则回退到 `docker/compose.prod.env.template` 仅用于配置检查。真实生产启动前必须创建上述 `.env` 文件。

必须填写：

```text
docker/postgres/template/postgres.prod.env:
  POSTGRES_PASSWORD
  VET_AGENT_POSTGRES_PASSWORD
  LITELLM_POSTGRES_PASSWORD
  MEM0_POSTGRES_PASSWORD

docker/litellm/template/litellm.prod.env:
  DATABASE_URL
  LITELLM_MASTER_KEY
  LITELLM_SALT_KEY
  UI_USERNAME
  UI_PASSWORD
  DASHSCOPE_API_KEY

docker/mem0/template/mem0.prod.env:
  OPENAI_API_KEY
  ADMIN_API_KEY
  JWT_SECRET
  POSTGRES_PASSWORD

docker/mem0-dashboard/template/mem0-dashboard.prod.env:
  当前无必填敏感参数；保留文件用于统一 env 挂载结构

docker/app/template/app.prod.env:
  DATABASE_URL
  LITELLM_API_KEY
  MEM0_API_KEY
  VET_AGENT_API_KEYS
  OSS_BUCKET
  OSS_ENDPOINT
```

`LITELLM_MASTER_KEY` 仅用于 LiteLLM Admin UI/API 和创建 virtual key，不应长期作为业务调用 key。生产首次启动 LiteLLM 后，应创建 Agent app 专用 LiteLLM virtual key 并写入 `docker/app/template/app.prod.env` 的 `LITELLM_API_KEY`，创建 Mem0 专用 LiteLLM virtual key 并写入 `docker/mem0/template/mem0.prod.env` 的 `OPENAI_API_KEY`。`ADMIN_API_KEY` 与 `MEM0_API_KEY` 应保持同一 Mem0 API key。`LITELLM_SALT_KEY` 用于加密 LiteLLM 数据库中保存的供应商凭据，一旦生产数据库中已有加密凭据，不应更换，并需与 `litellm` 逻辑库备份成对管理。`UI_USERNAME` 与 `UI_PASSWORD` 用于 LiteLLM Admin UI 登录，必须使用强随机值。

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

生产真实 env 文件由人工或基础设施基线预先放置在部署目录，不由 GitHub Actions 覆盖。手动执行生产 Compose 前，必须先指定同一个 GitHub Release tag 对应的三个业务镜像：

```bash
export VET_AGENT_IMAGE="crpi-efmmpn9a6t9mspwy.cn-hangzhou.personal.cr.aliyuncs.com/vancer-saas/veterinary_agent:v1.2.3"
export MEM0_IMAGE="crpi-efmmpn9a6t9mspwy.cn-hangzhou.personal.cr.aliyuncs.com/vancer-saas/veterinary_agent-mem0:v1.2.3"
export MEM0_DASHBOARD_IMAGE="crpi-efmmpn9a6t9mspwy.cn-hangzhou.personal.cr.aliyuncs.com/vancer-saas/veterinary_agent-mem0-dashboard:v1.2.3"
make prod-config
make prod-up
make prod-ready
```

`make prod-up` 会按顺序执行：

```text
prod-pull -> prod-deps -> prod-migrate -> start app
```

该链路不会在生产主机执行镜像构建，也不会自动导入 seed 或 RAG 静态资产。如果只需要执行迁移或经过审核的 seed：

```bash
make prod-db-extensions
make prod-migrate
make prod-seed
```

## GitHub Actions CD

`.github/workflows/cd.yml` 支持两种触发方式：

- 发布 GitHub Release：检出 Release tag，在 GitHub Runner 构建并推送应用和 Mem0 镜像，进入 `production` environment 审批后同步正式编排包并部署。
- 手动 `workflow_dispatch`：输入已存在的 `release_tag`；仅当 `deploy_production=true` 时进入生产审批，直接拉取该 Release tag 的既有镜像，不重新构建或覆盖镜像标签。

GitHub `production` environment 需要配置以下 Secrets：

```text
CD_REGISTRY_USERNAME
CD_REGISTRY_PASSWORD
CD_PROD_SSH_HOST
CD_PROD_SSH_PORT
CD_PROD_SSH_USER
CD_PROD_SSH_PRIVATE_KEY
CD_PROD_SSH_KNOWN_HOSTS
```

需要配置以下 Variables：

```text
CD_PROD_DEPLOY_PATH
CD_PROD_BASE_URL
```

CD 只同步 `docker/compose.yml`、正式 env 模板、`docker/litellm/litellm.yml`、`docker/mem0/application.yml`、`docker/mem0-dashboard/application.yml`、`docker/postgres/postgresql.conf` 和必要挂载脚本。生产真实 `docker/*/template/*.prod.env` 文件保留在服务器本地，不进入 Git，不由同步任务覆盖；生产服务器只拉取 Release tag 对应的预编译镜像，不现场执行 Mem0 或 Mem0 Dashboard 镜像构建。

## 常用运维命令

```bash
make prod-ps
make prod-logs
make prod-app-logs
make prod-mem0-dashboard-up
make prod-mem0-dashboard-logs
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

如果生产主机拉取公共中间件镜像较慢，可在 `docker/compose.prod.env` 中覆盖：

```text
docker/compose.prod.env:
PGVECTOR_IMAGE=你的镜像站/pgvector/pgvector:pg16
LITELLM_IMAGE=你的镜像站/berriai/litellm-database:main-stable
```

`VET_AGENT_IMAGE`、`MEM0_IMAGE` 与 `MEM0_DASHBOARD_IMAGE` 不在 compose env 文件中维护，由 CD 使用 GitHub Release tag 直接注入。生产 Compose 不包含任何 `build` 配置。

## 数据库拓扑

生产 Compose 只启动一个 `postgres` 服务，使用 `pgvector/pgvector` 镜像，并挂载 `docker/postgres/postgresql.conf` 作为非敏感 PostgreSQL 运行参数。首次初始化时，`docker/postgres/init/10-bootstrap-logical-databases.sh` 通过 PostgreSQL 官方初始化钩子创建逻辑库和登录角色：

```text
vet_agent     -> Agent 业务数据、RAG、记忆与 trace
litellm       -> LiteLLM 元数据
mem0_vector   -> Mem0 pgvector 语义记忆
mem0_app      -> Mem0 REST Server 用户、API key、请求日志等
```

表结构迁移不在初始化脚本中手写：Agent 使用本项目 Alembic，Mem0 使用镜像内官方 Alembic，LiteLLM 使用数据库版镜像自身启动迁移。若未来横向扩展多个 LiteLLM 实例，应单独设计迁移任务，并避免多个实例同时执行 schema 更新。

PostgreSQL 扩展由初始化脚本和 `postgres-extensions` 一次性任务负责：

```text
vet_agent   -> vector, pg_trgm
mem0_vector -> vector
```

`postgres-extensions` 会在补齐扩展后执行 `docker/postgres/ops/vector-smoke-check.sh`，确认 `vector`、`pg_trgm` 扩展和 pgvector 余弦距离运算可用。Agent 应用侧通过 `DATABASE_POOL_SIZE`、`DATABASE_MAX_OVERFLOW`、`DATABASE_STATEMENT_TIMEOUT_MS` 等变量控制 SQLAlchemy 连接池和连接级 SQL 超时。

重要：`docker/postgres/init` 只会在 PostgreSQL 数据卷为空时执行。若生产机已经存在数据卷，首次迁移前需先执行 `make prod-db-extensions`，补齐扩展后再运行 `make prod-migrate`。若从旧的多 PostgreSQL 容器编排升级，需先用 `pg_dump` / `pg_restore` 或等价备份工具迁移旧 `litellm-postgres`、`mem0-postgres` 数据；新环境直接初始化则无需手工建表。

## 服务边界

生产 Compose 包含：

- `app`: FastAPI Agent API
- `postgres`: 共享 PostgreSQL + pgvector，内部按逻辑库隔离 Agent、LiteLLM 和 Mem0
- `litellm`: LiteLLM Proxy，持有通义千问 API Key，并内置 LiteLLM Admin UI
- `mem0`: 基于 `vendor/mem0/server` 封装的自托管 Mem0 REST Server，镜像内核心 `mem0ai` 包同样来自 `vendor/mem0` 子模块源码
- `mem0-dashboard`: 基于 `vendor/mem0/server/dashboard` 封装的 Mem0 运维 Dashboard，默认位于 `ops` profile，浏览器侧通过 Nginx 网关的同源 `/api/mem0` 路径访问内部 Mem0 REST API
- `migrate`: 一次性 Alembic 迁移任务
- `seed`: 一次性规则/RAG seed 任务

默认只有 `app` 发布宿主机端口；PostgreSQL、LiteLLM、Mem0 默认仅在 Compose 网络内访问。LiteLLM Admin UI 位于 LiteLLM Proxy 的 `/ui` 路径，生产环境建议由 Nginx 独立运维入口转发到 `litellm:4000`，并对整个 LiteLLM Proxy 运维入口施加内网访问控制。`mem0-dashboard` 仅在启用 `ops` profile 时绑定宿主机回环地址，建议通过 SSH 隧道或内网运维入口访问。

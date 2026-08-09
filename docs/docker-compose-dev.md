<!--
File: docs/docker-compose-dev.md
Purpose: Development Docker Compose operating notes for Vet Agent.
Database topology: Mirrors production with one pgvector PostgreSQL instance.
-->

# Docker Compose Dev

开发环境可以使用 `docker/compose.dev.yml` 一键启动 PostgreSQL + LiteLLM Proxy + Mem0 REST Server + Agent API。PostgreSQL 使用一个 `pgvector/pgvector` 容器，并按逻辑库隔离 Agent、LiteLLM 和 Mem0；app 容器启动时会自动执行 Alembic 迁移和 `scripts/seed_database.py`。

如果本地之前已经启动过旧的多 PostgreSQL 容器编排，建议执行 `make dev-clean` 后重新启动；PostgreSQL 官方初始化脚本只会在空数据卷首次初始化时创建逻辑库。

## 启动

```bash
# 必填: 默认启用真实 Qwen / embedding 链路，需复制并填入真实 DASHSCOPE_API_KEY、UI 登录信息和加密盐值
cp docker/litellm/template/litellm.dev.env.template docker/litellm/template/litellm.dev.env
# 可选: 修改 docker/compose.dev.env.template 中的端口和镜像，或复制为 compose.env 后用 DEV_ENV_FILE 指定
make dev-up
make dev-ready
```

`Makefile` 会优先使用 `docker/compose.dev.env`；如果该文件不存在，则回退到 `docker/compose.dev.env.template`。

`DASHSCOPE_API_KEY` 只注入 LiteLLM 容器；Agent API 通过 `LITELLM_MASTER_KEY` 访问 `http://litellm:4000/v1`，不会直接读取通义千问 Key。`UI_USERNAME`、`UI_PASSWORD` 用于登录 LiteLLM Admin UI，`LITELLM_SALT_KEY` 用于加密 LiteLLM 写入数据库的供应商凭据。开发环境默认 `ENABLE_RAG_EMBEDDINGS=true` 和 `SEED_WITH_EMBEDDINGS=true`，因此启动时会真实调用 embedding 模型。

LiteLLM Admin UI 由 LiteLLM Proxy 内置提供。开发环境可通过 `http://127.0.0.1:4000/ui` 或正式开发环境 Nginx 网关访问；若后续允许在 UI 中写入模型配置，需先评估 `docker/litellm/litellm.yml` 中 `store_model_in_db` 是否应从 `false` 调整为 `true`。

若需要使用镜像站，可在 `docker/compose.dev.env.template` 或复制后的 `docker/compose.dev.env` 中覆盖：

```text
PGVECTOR_IMAGE=你的镜像站/pgvector/pgvector:pg16
MEM0_PYTHON_BASE_IMAGE=你的镜像站/python:3.12-slim-bookworm
MEM0_DASHBOARD_NODE_BASE_IMAGE=你的镜像站/node:20-bookworm-slim
LITELLM_IMAGE=你的镜像站/berriai/litellm:main-stable
```

Mem0 开发镜像由 `docker/mem0/Dockerfile` 从 `vendor/mem0/server` 构建，核心 `mem0ai` 包从同一子模块源码安装。Mem0 Dashboard 开发镜像由 `docker/mem0-dashboard/Dockerfile` 从 `vendor/mem0/server/dashboard` 构建，浏览器侧默认访问同源 `/api/mem0`，该路径需由正式开发环境 Nginx 网关转发至容器内 Mem0 REST API。需要记录本地镜像的子模块版本时，可在执行 build 前导出 `MEM0_SOURCE_COMMIT=$(git -C vendor/mem0 rev-parse HEAD)`。

修改 Key 后重启中间件和 app：

```bash
docker compose --env-file docker/compose.dev.env.template -f docker/compose.dev.yml up -d --force-recreate postgres litellm mem0 app
```

如果当前 Docker Compose 版本不支持 `--wait`：

```bash
make dev-up-no-wait
make dev-app-logs
```

如果本机没有 `make`，直接使用 `docker compose --env-file docker/compose.dev.env.template -f docker/compose.dev.yml ...`。

## 常用命令

```bash
make dev-up              # 构建并启动 app + postgres + LiteLLM + 自托管 Mem0 + Mem0 Dashboard
make dev-down            # 停止容器
make dev-clean           # 停止并删除 dev 数据卷
make dev-app-logs        # 查看 app 日志
make dev-db-logs         # 查看共享 PostgreSQL 日志
make dev-litellm-logs    # 查看 LiteLLM 日志
make dev-mem0-logs       # 查看 Mem0 日志
make dev-mem0-dashboard-logs # 查看 Mem0 Dashboard 日志
make dev-mem0-db-logs    # 查看共享 PostgreSQL 日志
make dev-migrate         # 手动执行 Alembic
make dev-seed            # 手动导入 seed
make dev-test            # 在 app 容器内跑测试
make dev-shell           # 进入 app 容器
make db-shell            # 进入 PostgreSQL psql
```

## 请求检查

```bash
make request-health
make request-ready
make request-followup-first
make request-followup-second
make request-multitask
make request-safety-toxic
make request-idempotency
make request-profile-memory
make request-memory-read
make request-report-parse
make request-rag-stats
make request-rag-chunks
make request-all
```

## 手动 curl

请求 payload 位于 `scripts/dev_payloads`：

```bash
curl http://127.0.0.1:8000/health

curl -X POST http://127.0.0.1:8000/agent/turns \
  -H "Content-Type: application/json" \
  --data-binary "@scripts/dev_payloads/followup_first.json"

curl -X POST http://127.0.0.1:8000/agent/turns \
  -H "Content-Type: application/json" \
  --data-binary "@scripts/dev_payloads/followup_second.json"
```

打印完整 curl 样例：

```bash
make request-curl
```

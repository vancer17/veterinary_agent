<!--
File: docs/docker-compose-dev.md
Purpose: Development Docker Compose operating notes for Vet Agent.
Database topology: Mirrors production with one pgvector PostgreSQL instance.
-->

# Docker Compose Dev

开发环境可以使用 `docker-compose.dev.yml` 一键启动 PostgreSQL + LiteLLM Proxy + 官方 Mem0 REST Server + Agent API。PostgreSQL 使用一个 `pgvector/pgvector` 容器，并按逻辑库隔离 Agent、LiteLLM 和 Mem0；app 容器启动时会自动执行 Alembic 迁移和 `scripts/seed_database.py`。

如果本地之前已经启动过旧的多 PostgreSQL 容器编排，建议执行 `make dev-clean` 后重新启动；PostgreSQL 官方初始化脚本只会在空数据卷首次初始化时创建逻辑库。

## 启动

```bash
# 必填: 默认启用真实 Qwen / embedding 链路，需复制并填入真实 DASHSCOPE_API_KEY
cp deploy/env/dev/services/litellm.env.template deploy/env/dev/services/litellm.env
# 可选: 修改 deploy/env/dev/compose.env.template 中的端口和镜像，或复制为 compose.env 后用 DEV_ENV_FILE 指定
make dev-up
make dev-ready
```

`Makefile` 会优先使用 `deploy/env/dev/compose.env`；如果该文件不存在，则回退到 `compose.env.template`。

`DASHSCOPE_API_KEY` 只注入 LiteLLM 容器；Agent API 通过 `LITELLM_MASTER_KEY` 访问 `http://litellm:4000/v1`，不会直接读取通义千问 Key。开发环境默认 `ENABLE_RAG_EMBEDDINGS=true` 和 `SEED_WITH_EMBEDDINGS=true`，因此启动时会真实调用 embedding 模型。

若需要使用镜像站，可在 `deploy/env/dev/compose.env.template` 或复制后的 `deploy/env/dev/compose.env` 中覆盖：

```text
PGVECTOR_IMAGE=你的镜像站/pgvector/pgvector:pg16
MEM0_IMAGE=你的镜像站/vancer-saas/mem0:latest
LITELLM_IMAGE=你的镜像站/berriai/litellm:main-stable
```

修改 Key 后重启中间件和 app：

```bash
docker compose --env-file deploy/env/dev/compose.env.template -f docker-compose.dev.yml up -d --force-recreate postgres litellm mem0 app
```

如果当前 Docker Compose 版本不支持 `--wait`：

```bash
make dev-up-no-wait
make dev-app-logs
```

如果本机没有 `make`，直接使用 `docker compose --env-file deploy/env/dev/compose.env.template -f docker-compose.dev.yml ...`。

## 常用命令

```bash
make dev-up              # 构建并启动 app + postgres + LiteLLM + 官方 Mem0
make dev-down            # 停止容器
make dev-clean           # 停止并删除 dev 数据卷
make dev-app-logs        # 查看 app 日志
make dev-db-logs         # 查看共享 PostgreSQL 日志
make dev-litellm-logs    # 查看 LiteLLM 日志
make dev-mem0-logs       # 查看 Mem0 日志
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

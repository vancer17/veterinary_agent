# Docker Compose Dev

开发环境可以使用 `docker-compose.dev.yml` 一键启动 PostgreSQL + Agent API。app 容器启动时会自动执行 Alembic 迁移和 `scripts/seed_database.py`。

## 启动

```powershell
Copy-Item .env.template .env
# 可选: 修改 .env 中的 APP_PORT、PGVECTOR_IMAGE、QWEN_API_KEY、ALLOW_MOCK_LLM
make dev-up
make dev-ready
```

如果当前 Docker Compose 版本不支持 `--wait`：

```powershell
make dev-up-no-wait
make dev-app-logs
```

如果本机没有 `make`，Windows PowerShell 可以使用等价封装：

```powershell
.\scripts\dev.ps1 dev-up
.\scripts\dev.ps1 request-ready
.\scripts\dev.ps1 request-followup-first
```

## 常用命令

```powershell
make dev-up              # 构建并启动 app + postgres
make dev-down            # 停止容器
make dev-clean           # 停止并删除 dev 数据卷
make dev-app-logs        # 查看 app 日志
make dev-db-logs         # 查看 postgres 日志
make dev-migrate         # 手动执行 Alembic
make dev-seed            # 手动导入 seed
make dev-test            # 在 app 容器内跑测试
make dev-shell           # 进入 app 容器
make db-shell            # 进入 PostgreSQL psql
```

## 请求检查

```powershell
make request-health
make request-ready
make request-followup-first
make request-followup-second
make request-multitask
make request-safety-toxic
make request-idempotency
make request-profile-memory
make request-memory-read
make request-all
```

## 手动 curl

请求 payload 位于 `scripts/dev_payloads`：

```powershell
curl.exe http://127.0.0.1:8000/health

curl.exe -X POST http://127.0.0.1:8000/agent/turns `
  -H "Content-Type: application/json" `
  --data-binary "@scripts/dev_payloads/followup_first.json"

curl.exe -X POST http://127.0.0.1:8000/agent/turns `
  -H "Content-Type: application/json" `
  --data-binary "@scripts/dev_payloads/followup_second.json"
```

打印完整 curl 样例：

```powershell
make request-curl
```

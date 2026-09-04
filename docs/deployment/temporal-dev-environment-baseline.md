<!--
=============================================================================
文件: temporal-dev-environment-baseline.md
作用: 固化远端开发环境 Temporal Server、Web UI、namespace 与持久化边界，
      作为受限语义协作 DAG M04 / M15 集成测试的环境基线。
范围: 覆盖开发环境服务拓扑、网络地址、SSH tunnel、PostgreSQL 逻辑库隔离、
      namespace / task queue、健康检查、运维边界和生产拓扑差异。
说明: 本文只记录非敏感环境契约；真实密码仅保存在远端 compose.dev.env，
      不进入 Git、正式文档、CI、测试快照或日志。本文不定义生产部署方案。
维护: 当 Temporal 镜像版本、Compose 网络、namespace、逻辑库、访问策略或
      集成测试配置边界调整时，必须同步更新本文。
=============================================================================
-->

# Temporal 远端开发环境基线

> **文档状态**：环境已就绪并通过基础连通性验证
>
> **基线日期**：2026-09-04
>
> **适用范围**：受限语义协作 DAG M04 Temporal workflow / activity 集成测试、
> M15 生产接入前的真实 durable execution 验证
>
> **不适用范围**：生产 Temporal 拓扑、医学或语义业务规则、LiteLLM 调用、
> 问诊状态存储、临床安全裁决、长期记忆写入和通用任务队列服务

## 1. 基线结论

远端开发环境已部署独立 Compose 项目：

```text
vet-agent-dev-temporal
```

当前可用服务：

```text
vet-agent-dev-temporal       # Temporal Server / auto-setup
vet-agent-dev-temporal-ui    # Temporal Web UI
```

`temporal-admin-tools` 不常驻运行，只能通过 `ops` profile 和一次性
`docker compose run` 命令执行运维操作。

该环境已通过以下验证：

```text
Compose 配置校验: PASS
PostgreSQL 专用角色连接: PASS
Temporal schema 初始化: PASS
Temporal cluster health: SERVING
namespace describe: PASS
Temporal Web UI HTTP: 200
Python temporalio SDK 连接: PASS
```

本基线只表示基础设施可用，不表示 M04 已完成真实 workflow 联调，也不表示
VetOrchestrator 已接入生产主路径。

## 2. 版本与服务拓扑

| 项目 | 值 |
|---|---|
| Compose 项目 | `vet-agent-dev-temporal` |
| Compose 文件 | `docker/temporal/compose.dev.yml` |
| Temporal Server 镜像 | `temporalio/auto-setup:1.29.1` |
| Temporal Web UI 镜像 | `temporalio/ui:2.34.0` |
| admin-tools 镜像 | `temporalio/admin-tools:1.29.1-tctl-1.18.4-cli-1.5.0` |
| Python SDK | `temporalio==1.32.0` |
| Docker 网络 | `vet-agent-dev_default` |
| 容器网络别名 | `semantic-collaboration-temporal` |

服务拓扑：

```text
vet-agent-dev-postgres
        ↓
vet-agent-dev-temporal
        ↓
vet-agent-dev-temporal-ui

temporal-admin-tools  # ops profile，按需一次性运行
```

## 3. 地址与访问方式

| 执行位置 | Temporal Frontend | Temporal UI |
|---|---|---|
| 远端 Compose 容器内 | `semantic-collaboration-temporal:7233` | 不直接访问 |
| 远端服务器本机 | `127.0.0.1:7233` | `http://127.0.0.1:8080` |
| 本地开发机，经 SSH tunnel | `127.0.0.1:7233` | `http://127.0.0.1:8080` |
| 公网 | 无 | 无 |

当前开发环境未启用 Temporal mTLS、API key 或 RPC 授权。为降低暴露面，
Compose 已固定：

```text
7233 -> 仅绑定远端 127.0.0.1
8080 -> 仅绑定远端 127.0.0.1
```

Nginx 不提供 Temporal 公网路由，云安全组不应开放 `7233` 或 `8080`。

本地访问应先建立 SSH tunnel：

```bash
ssh -N \
  -L 7233:127.0.0.1:7233 \
  -L 8080:127.0.0.1:8080 \
  devlop@47.97.19.58
```

随后访问：

```text
Temporal gRPC: 127.0.0.1:7233
Temporal UI:   http://127.0.0.1:8080
```

## 4. 网络边界

当前 Temporal Server 加入既有外部网络：

```text
vet-agent-dev_default
```

并在该网络内使用别名：

```text
semantic-collaboration-temporal
```

远端容器访问地址固定为：

```text
semantic-collaboration-temporal:7233
```

如果后续 app 或 semantic worker 使用独立 Compose 网络，必须显式加入该外部网络：

```yaml
networks:
  temporal:
    external: true
    name: vet-agent-dev_default
```

禁止通过公网回环地址、公网 IP 或临时端口映射访问本服务。

## 5. PostgreSQL 持久化边界

Temporal 复用现有共享 PostgreSQL 容器：

```text
vet-agent-dev-postgres
```

但使用独立角色和逻辑库：

| 逻辑库 | 角色 | 用途 |
|---|---|---|
| `temporal` | `temporal` | Temporal 主存储 |
| `temporal_visibility` | `temporal` | Temporal SQL visibility |

角色权限：

```text
NOSUPERUSER
NOCREATEDB
NOCREATEROLE
NOREPLICATION
```

数据库边界：

```text
temporal / temporal_visibility owner = temporal
PUBLIC database privileges = revoked
```

真实密码仅保存在远端：

```text
docker/temporal/compose.dev.env
```

该文件必须保持 `600` 权限，不得提交 Git，不得写入正式文档、CI、测试快照或日志。

角色和逻辑库由部署步骤显式创建。Temporal `auto-setup` 使用：

```text
SKIP_DB_CREATE=true
```

因此 `temporal` 角色不需要 `CREATEDB` 权限。

禁止：

```text
把 Temporal 表写入 vet_agent、LiteLLM 或 Mem0 业务库
把 Agent 业务表写入 Temporal 逻辑库
使用 PostgreSQL superuser 运行 Temporal
手工修改 Temporal 内部表来驱动 workflow 状态
```

## 6. Namespace 与 Task Queue

集成测试 namespace：

```text
semantic-collaboration-dev
```

当前状态：

```text
Registered
Retention = 168h / 7 days
Description = Semantic collaboration DAG integration tests
```

建议 task queue：

```text
semantic-collaboration-dev
```

普通集成测试不得默认使用 Temporal `default` namespace。新增 namespace 必须显式注册，
并同步更新本文。

## 7. 集成测试配置

本地测试进程通过 SSH tunnel 访问：

```bash
TEMPORAL_ADDRESS="127.0.0.1:7233"
TEMPORAL_NAMESPACE="semantic-collaboration-dev"
TEMPORAL_TASK_QUEUE="semantic-collaboration-dev"
```

远端 Compose 容器访问：

```bash
TEMPORAL_ADDRESS="semantic-collaboration-temporal:7233"
TEMPORAL_NAMESPACE="semantic-collaboration-dev"
TEMPORAL_TASK_QUEUE="semantic-collaboration-dev"
```

后续生产接入时应通过 M15 显式配置面管理，建议配置名：

```bash
SEMANTIC_TEMPORAL_ADDRESS
SEMANTIC_TEMPORAL_NAMESPACE
SEMANTIC_TEMPORAL_TASK_QUEUE
```

上述生产配置名是目标契约，不代表当前 VetOrchestrator 已经接入。

## 8. admin-tools 使用边界

`temporalio/admin-tools` 镜像默认 entrypoint 会执行：

```text
sleep infinity
```

因此 `docker/temporal/compose.dev.yml` 已显式设置：

```yaml
entrypoint: ["temporal"]
```

使用 Compose 运行一次性 CLI 时，命令不应再以 `temporal` 开头。

正确：

```bash
docker compose ... run --rm temporal-admin-tools operator cluster health
```

错误：

```bash
docker compose ... run --rm temporal-admin-tools temporal operator cluster health
```

## 9. 健康检查与冒烟命令

首次部署或重建环境时，可从仓库根目录执行：

```bash
scripts/dev/deploy-temporal-dev.sh
```

该脚本会：

```text
同步 Temporal Compose 配置
生成或复用远端 compose.dev.env
校验共享 PostgreSQL 与 Docker 网络
创建 temporal 角色和独立逻辑库
拉取固定版本镜像
启动 Temporal Server / Web UI
等待 Frontend ready
确保 semantic-collaboration-dev namespace 存在
执行最终 smoke check
```

脚本不会把真实密码写回仓库，也不会在失败时隐式降级。

以下命令假设远端当前目录为：

```text
/home/devlop/veterinary_agent
```

查看 Compose 状态：

```bash
sudo docker compose \
  -f docker/temporal/compose.dev.yml \
  --env-file docker/temporal/compose.dev.env \
  ps
```

检查 cluster health：

```bash
sudo docker compose \
  -f docker/temporal/compose.dev.yml \
  --env-file docker/temporal/compose.dev.env \
  --profile ops \
  run --rm \
  temporal-admin-tools \
  operator cluster health
```

期望输出：

```text
SERVING
```

查看 namespace：

```bash
sudo docker compose \
  -f docker/temporal/compose.dev.yml \
  --env-file docker/temporal/compose.dev.env \
  --profile ops \
  run --rm \
  temporal-admin-tools \
  operator namespace describe \
  --namespace semantic-collaboration-dev
```

查看 workflow：

```bash
sudo docker compose \
  -f docker/temporal/compose.dev.yml \
  --env-file docker/temporal/compose.dev.env \
  --profile ops \
  run --rm \
  temporal-admin-tools \
  workflow list \
  --namespace semantic-collaboration-dev
```

检查远端本机 Web UI：

```bash
curl --noproxy '*' \
  -sS \
  -o /dev/null \
  -w '%{http_code}\n' \
  http://127.0.0.1:8080/
```

期望输出：

```text
200
```

远端 shell 存在 `http_proxy` 时，访问 `127.0.0.1` 必须带 `--noproxy '*'`，
否则请求可能被本机代理转换为 `502`。

## 10. 运维边界

查看 Temporal 日志：

```bash
sudo docker compose \
  -f docker/temporal/compose.dev.yml \
  --env-file docker/temporal/compose.dev.env \
  logs -f temporal
```

查看 Web UI 日志：

```bash
sudo docker compose \
  -f docker/temporal/compose.dev.yml \
  --env-file docker/temporal/compose.dev.env \
  logs -f temporal-ui
```

停止服务：

```bash
sudo docker compose \
  -f docker/temporal/compose.dev.yml \
  --env-file docker/temporal/compose.dev.env \
  down
```

由于持久化位于共享 PostgreSQL 逻辑库中，停止 Compose 项目不会删除 Temporal 数据。
清理 workflow history 必须通过 Temporal CLI 或专门治理脚本执行，不得手工修改
Temporal 内部表。

## 11. 测试工程质量边界

1. 默认单元测试不得访问远端 Temporal。
2. 真实 Temporal 集成测试必须显式开启。
3. 集成测试缺少 `TEMPORAL_ADDRESS`、namespace 或 task queue 时必须显式失败。
4. 测试不得自动创建 namespace。
5. 测试不得默认使用 `default` namespace。
6. 测试配置不得硬编码远端 IP、SSH tunnel 或容器网络别名。
7. 测试不得把远端环境可达作为契约测试通过条件。
8. 测试输出不得包含 `compose.dev.env` 中的真实密码。

Temporal 不可用时必须显式失败：

```text
dependency_unavailable
temporal_unavailable
```

禁止回退：

```text
数据库扫表调度器
数据库 worker 租约
进程内任务队列
asyncio 调度器
静默跳过 durable execution
旧问诊语义抽取器
```

## 12. 生产边界

当前使用：

```text
temporalio/auto-setup
```

该选择只适用于远端开发集成环境。

生产环境不得直接复用本拓扑。生产阶段必须：

1. 使用 `temporalio/server`，不使用 `auto-setup`。
2. 拆分 frontend、history、matching、worker 等 Server 角色。
3. 使用显式 schema migration 和发布流程。
4. 配置 TLS / mTLS。
5. 配置认证与授权。
6. 配置监控、告警、日志脱敏和备份恢复。
7. 独立评估 PostgreSQL 连接池、容量和迁移策略。
8. 不在每次容器启动时隐式执行数据库结构初始化。

## 13. 关联材料

1. [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
2. [semantic-collaboration-dag-production-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-implementation-plan.md)
3. [semantic-collaboration-dag-m04-scheduler-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m04-scheduler-change-summary.md)
4. [public-ip-access-guide.md](/home/vancer17/veterinary_agent/docs/deployment/public-ip-access-guide.md)
5. `tmp/dev-external-services/README.md`

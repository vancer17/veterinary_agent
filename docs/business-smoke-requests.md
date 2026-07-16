# 业务模拟请求

这些请求用于检查真实 LiteLLM、Qwen、PostgreSQL、官方 Mem0 REST Server 和 Agent 编排链路。
请求正文尽量使用语义改写，避开当前规则中的症状、任务分类和毒物直命中词。

## 一次运行全部场景

```bash
make request-business-all
```

`business-all` 每次会自动生成新的 `business_run_id`，避免旧会话状态影响结果。

没有安装 Make 时：

```bash
docker compose -f docker-compose.dev.yml exec -T app python scripts/dev_request.py business-all
```

查看完整响应：

```bash
docker compose -f docker-compose.dev.yml exec -T app python scripts/dev_request.py business-all --full
```

容器内运行时，runner 会读取 app 容器的 `VET_AGENT_API_KEYS`。从宿主机直接调用业务环境时，可以设置：

```bash
export VET_AGENT_DEV_API_KEY="<业务环境 Agent API Token>"
python scripts/dev_request.py business-all --base-url "https://<业务环境地址>"
```

## 场景与验收点

| 场景 | 命令 | 主要验收点 |
| --- | --- | --- |
| 中性首轮追问 | `make request-business-followup-first` | `status=requires_followup`；存在 `missing_slots`；路径中不应提前出现 `QwenResponseAgent` |
| 下一轮补齐上下文 | `make request-business-followup-second` | 与首轮使用相同 user/session/pet；应进入 `completed`；`missing_slots=[]`；路径包含 `QwenResponseAgent` |
| 语义多任务 | `make request-business-multitask` | 重点检查 `route=multi_task_consultation`、`task_count` 和多个 `segments` |
| 事实与语义记忆 | `make request-business-memory` | 检查 PostgreSQL `facts`、Mem0 `semantic_memories`，以及是否只属于指定 user/pet |
| 间接毒物表达 | `make request-business-safety-semantic` | 目标应为 `safety_escalated` 并出现毒物安全信号；未升级表示语义安全识别仍依赖关键词 |
| SSE 推理摘要 | `make request-business-stream` | 应出现 `reasoning_display.completed`、`segment.delta` 和 `turn.completed` |

单独执行多轮场景时，第一轮和第二轮必须使用相同的 `BUSINESS_RUN_ID`：

```bash
make BUSINESS_RUN_ID=case001 request-business-followup-first
make BUSINESS_RUN_ID=case001 request-business-followup-second
```

使用 Docker 命令时：

```bash
docker compose -f docker-compose.dev.yml exec -T app python scripts/dev_request.py business-followup-first --run-id case001
docker compose -f docker-compose.dev.yml exec -T app python scripts/dev_request.py business-followup-second --run-id case001
```

## 原生 curl

以下 curl 使用 payload 文件中的固定业务 ID。重复测试前请修改三个 ID，或优先使用前面的 runner 自动追加 `run-id`。

```bash
curl -X POST http://127.0.0.1:8000/agent/turns \
  -H "Content-Type: application/json" \
  --data-binary "@scripts/dev_payloads/business_followup_first.json"

curl -X POST http://127.0.0.1:8000/agent/turns \
  -H "Content-Type: application/json" \
  --data-binary "@scripts/dev_payloads/business_followup_second.json"

curl -X POST http://127.0.0.1:8000/agent/turns \
  -H "Content-Type: application/json" \
  --data-binary "@scripts/dev_payloads/business_multitask.json"

curl -X POST http://127.0.0.1:8000/agent/turns \
  -H "Content-Type: application/json" \
  --data-binary "@scripts/dev_payloads/business_memory.json"

curl -X POST http://127.0.0.1:8000/agent/turns \
  -H "Content-Type: application/json" \
  --data-binary "@scripts/dev_payloads/business_safety_semantic.json"

curl -N -X POST http://127.0.0.1:8000/agent/turns \
  -H "Content-Type: application/json" \
  --data-binary "@scripts/dev_payloads/business_stream.json"
```

开启鉴权时，在每条命令中增加：

```bash
-H "Authorization: Bearer ${VET_AGENT_DEV_API_KEY}"
```

记忆写入后可直接检查持久化结果：

```bash
curl "http://127.0.0.1:8000/memories?user_id=business_user_memory_001&session_id=business_session_memory_001&pet_id=business_pet_memory_001"
```

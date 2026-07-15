# 兽医多 Agent 服务

这是一个面向宠物主人的 FastAPI 兽医 Agent 原型，按需求文档实现第一阶段能力:

- API 接入层: `/agent/turns`、`/openai/v1/responses`、`/health`、`/ready`
- 多 Agent 编排: `SafetyAgent -> PetContextAgent -> MemoryAgent -> KnowledgeAgent -> QuestionPlannerAgent -> QwenResponseAgent -> SafetyReviewAgent`
- 硬安全规则: 急症红旗、有毒食物/危险人药、不给具体剂量、片子不判读
- 记忆与留痕: 本地 JSON 存储主人/宠物/会话记忆和涉诊涉药 trace
- 通义千问: 通过 DashScope OpenAI 兼容接口调用，默认模型 `qwen-plus`
- PostgreSQL + pgvector: 安全规则、问诊槽位和 RAG 语料可从数据库读取，代码里不再内置规则语料

## 快速启动

```powershell
Copy-Item .env.template .env
# 在 .env 中填入 QWEN_API_KEY，或保持 ALLOW_MOCK_LLM=true 使用本地 mock
uv run uvicorn main:app --reload
```

健康检查:

```powershell
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

同步对话示例:

```powershell
curl -X POST http://127.0.0.1:8000/agent/turns `
  -H "Content-Type: application/json" `
  -d '{
    "input": "我家狗今天有点拉稀，应该怎么办？",
    "stream": false,
    "vet_context": {
      "user_id": "u1",
      "session_id": "s1",
      "pet_id": "p1",
      "pet_info": {"species": "犬", "breed": "柯基", "age": "3岁", "weight_kg": 12}
    }
  }'
```

SSE 流式响应将 `stream` 设为 `true`。

## 逐轮问诊状态

系统现在会为同一个 `session_id + pet_id` 保存结构化问诊状态。第一轮信息不足时不会直接给最终判断，而是返回 `requires_followup` 并提出 1-3 个关键问题；下一轮用户补充后，系统会把回答解析进槽位，达到最小信息集后才返回 `completed`。

第一轮示例:

```powershell
$body = @{
  input = "它有点拉稀，怎么办？"
  vet_context = @{ user_id = "u_state"; session_id = "s_state"; pet_id = "p_state" }
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post "$base/agent/turns" -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

第二轮补充同一 `session_id`:

```powershell
$body = @{
  input = "是狗，3岁，12公斤，今天早上开始，精神食欲正常，没有呕吐，大便拉稀但没有血。"
  vet_context = @{ user_id = "u_state"; session_id = "s_state"; pet_id = "p_state" }
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post "$base/agent/turns" -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

## 环境变量

- `QWEN_API_KEY` / `DASHSCOPE_API_KEY`: 通义千问 API Key
- `QWEN_MODEL`: 默认 `qwen-plus`
- `QWEN_BASE_URL`: 默认 `https://dashscope.aliyuncs.com/compatible-mode/v1`
- `ALLOW_MOCK_LLM`: 未配置 Key 时是否允许 mock 回复
- `VET_AGENT_DATA_DIR`: 记忆和留痕数据目录
- `DATABASE_URL`: PostgreSQL 连接串；配置后优先读取数据库规则/RAG，失败时回退 `data/seeds`
- `VET_AGENT_SEED_DIR`: 本地规则和知识 seed 目录，默认 `data/seeds`

## PostgreSQL + pgvector

启动数据库:

```powershell
docker compose up -d postgres
```

如果默认镜像拉取慢或失败，可以换成镜像站同步过的 pgvector 镜像:

```powershell
$env:PGVECTOR_IMAGE = "你的镜像站/pgvector/pgvector:pg16"
docker compose up -d postgres
```

设置连接串，执行 Alembic 迁移，再初始化规则/知识数据:

```powershell
$env:DATABASE_URL = "postgresql://vet_agent:vet_agent@127.0.0.1:5432/vet_agent"
uv run alembic upgrade head
uv run python scripts/seed_database.py
```

如果你的 `vet_agent` 数据库之前已经用旧的 `db/init/*.sql` 创建过同名表，首次 `alembic upgrade head` 可能提示表已存在。建议新环境直接使用空库；若确认现有表结构与当前 Alembic 初始版本一致，可执行:

```powershell
$env:DATABASE_URL = "postgresql://vet_agent:vet_agent@127.0.0.1:5432/vet_agent"
uv run alembic stamp head
uv run python scripts/seed_database.py
```

导入合法/授权的 RAG 文档:

```powershell
# 把 .txt/.md 放到 rag_sources 下，例如合法导出的 Merck 免费页面或内部授权资料
uv run python scripts/import_knowledge_dir.py `
  --source-dir rag_sources `
  --source "Merck Veterinary Manual free pages" `
  --source-url "https://www.merckvetmanual.com/" `
  --public-citation true `
  --copyright-risk low
```

如果已经配置 `QWEN_API_KEY`，可以在导入时同时写入 pgvector 向量:

```powershell
$env:ENABLE_RAG_EMBEDDINGS = "true"
uv run python scripts/seed_database.py --with-embeddings
uv run python scripts/import_knowledge_dir.py `
  --source-dir rag_sources `
  --source "Merck Veterinary Manual free pages" `
  --source-url "https://www.merckvetmanual.com/" `
  --public-citation true `
  --copyright-risk low `
  --with-embeddings
```

运行服务时如需优先走向量检索:

```powershell
$env:ENABLE_RAG_EMBEDDINGS = "true"
$env:DATABASE_URL = "postgresql://vet_agent:vet_agent@127.0.0.1:5432/vet_agent"
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

数据库表:

- `safety_rules`: 急症、毒物、用药、影像 gate、输出剂量清洗等确定性安全规则
- `consultation_domains`: 各问诊方向的关键词和必填槽位
- `consultation_slots`: 每个槽位的问题和抽取规则
- `knowledge_chunks`: RAG chunk，包含 `embedding vector`、版权/引用元数据
- `conversation_turns`: 短记忆，按 `user_id + pet_id + session_id` 保存最近对话轮次
- `consultation_states`: 短记忆/任务状态，唯一键为 `user_id + pet_id + session_id + task_key`
- `pet_memory_episodes`: 中记忆，保存一次咨询 episode 摘要
- `pet_memory_facts`: 长记忆，保存宠物事实、用户纠正、置信度和来源
- `logic_traces`: 涉诊涉药输出、证据和 reasoning_display 留痕
- `idempotency_records`: 幂等键到响应快照的映射，避免客户端重试重复落库

原则: 安全规则和问诊槽位走确定性表；医学知识走 `knowledge_chunks` RAG。`embedding vector` 有值且 `ENABLE_RAG_EMBEDDINGS=true` 时走 pgvector 相似度检索；否则走数据库文本检索，并仍然替代代码硬编码语料。

配置 `DATABASE_URL` 后，记忆和 trace 会自动切换到 PostgreSQL；未配置时仍使用本地 JSON 文件，方便开发测试。

可选 mem0 语义记忆:

```powershell
uv sync --extra agent-memory
$env:ENABLE_MEM0 = "true"
$env:MEM0_API_KEY = "你的 mem0 key，如使用本地 mem0 可不填"
```

mem0 只作为语义检索/长期记忆增强层；`pet_memory_facts` 仍是医疗事实、用户纠正和删除治理的可信事实源。

迁移说明:

- 数据库结构由 Alembic 管理：[alembic/versions](D:/agent/alembic/versions)
- 不再使用 `db/init/*.sql` 这类 Docker 初始化 SQL
- 应用和导入脚本使用 SQLAlchemy model/query，避免手写 SQL 查询和参数拼接

## 记忆接口

- `GET /memories?user_id=...&session_id=...&pet_id=...`
- `PUT /memories`
- `PUT /memories/facts`
- `DELETE /memories/pets/{pet_id}`

## 验证

```powershell
uv run python -m compileall main.py src tests
uv run pytest
```

## P0 生产化补强

本项目已加入上线前 P0 保护层，默认保持本地开发友好；生产环境建议显式开启严格配置：

```powershell
$env:VET_AGENT_API_KEYS = "replace-with-service-key"
$env:REQUIRE_API_AUTH = "true"
$env:PET_AUTHORIZATION_MODE = "strict"
$env:SESSION_POLICY_MODE = "strict"
$env:ALLOW_MOCK_LLM = "false"
```

新增能力：

- API 鉴权：配置 `VET_AGENT_API_KEYS` 或 `REQUIRE_API_AUTH=true` 后，入口要求 `Authorization: Bearer ...` 或 `X-API-Key`。
- 宠物授权：`pet_profiles` 作为 `pet_id -> user_id` 归属源；strict 模式下未登记或归属不匹配会返回 `403`。
- 一 session 一宠：`pet_session_bindings` 绑定 `session_id + user_id + pet_id`，同一会话切到另一只宠物会被拒绝。
- 幂等并发：`idempotency_records` 支持 `processing/completed/failed`，同一幂等键并发请求会等待并复用首个响应。
- session 串行化：PostgreSQL 模式使用 advisory lock 序列化同一 `user_id + pet_id + session_id` 下的 turn，降低状态竞争。
- Qwen 韧性：支持并发限流、最小请求间隔、重试、fallback model、熔断冷却和模型不可用时的保守降级回复。
- 自动事实记忆：`MemoryExtractionAgent` 抽取宠物画像、过敏、既往史、用药、饮食等候选事实，经过写入策略后落到 `pet_memory_facts`。
- 输出后审：`SafetyReviewAgent` 在返回和持久化前二次检查输出，移除剂量表达、补充线下兽医兜底、弱化绝对化诊断。

新增迁移：

- `alembic/versions/0003_access_control_and_idempotency.py`
- 新增表：`pet_profiles`、`pet_session_bindings`
- 调整：`idempotency_records.response_snapshot` 允许在 `processing` 状态为空

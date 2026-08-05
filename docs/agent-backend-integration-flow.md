<!--
文件：docs/agent-backend-integration-flow.md
作用：梳理当前兽医 Agent 项目的接口、状态与内部逻辑流转，供后端/BFF 同事对接。
说明：本文档基于当前 src/ingress 与 src/vet_agent 实现编纂；接口字段以 docs/external_api.md 为主契约，本文补充运行时状态和业务流转。
-->

# 兽医 Agent 后端对接与状态流转文档

## 1. 当前系统定位

当前 Agent 是一个面向后端/BFF 调用的兽医问诊编排服务。主入口是 `POST /agent/turns`，同步和流式共用同一接口，通过请求体 `stream` 字段区分。

系统当前不使用 LangGraph。核心编排由 `VetOrchestrator` 串联多个内部 Agent/Service：

| 模块 | 职责 |
| --- | --- |
| `AccessControlService` | API 鉴权、宠物归属、会话绑定 |
| `SafetyAgent` / `SafetyReviewAgent` | 输入安全分诊、输出安全复审 |
| `TaskSplitterAgent` | 单轮多任务拆分；LLM 优先、规则兜底 |
| `ConsultationStateAgent` | 多轮问诊状态构建、槽位抽取、是否可回答判断 |
| `KnowledgeService` | RAG 知识检索，优先 PostgreSQL/pgvector，失败回退 seed 文件 |
| `RagQuestionPlannerAgent` | 根据知识库证据反推下一轮追问 |
| `ResponseComposer` | 基于上下文、记忆、知识库生成最终答复 |
| `MemoryExtractionAgent` | 从本轮问答中抽取事实记忆 |
| `MemoryService` / `PostgresMemoryService` | 对话、短中长记忆、问诊状态、幂等记录持久化 |
| `ReportIngestionService` | OSS 图片报告解析与持久化 |
| `LogicTraceStore` | 保存逻辑留痕 |

## 2. 运行依赖与数据归属

生产链路默认依赖：

| 依赖 | 作用 |
| --- | --- |
| PostgreSQL + pgvector | 业务表、RAG、问诊状态、记忆、trace、报告记录 |
| LiteLLM | 统一代理通义千问 chat / vision / embedding 请求 |
| Mem0 REST Server | 语义记忆增强，Agent 通过 HTTP 调用 |
| Alembic | Agent 业务库迁移 |
| Docker Compose | 本地/生产依赖编排 |

数据库逻辑库规划：

| 逻辑库 | 使用方 | 内容 |
| --- | --- | --- |
| `vet_agent` | Agent | 业务表、RAG、问诊状态、记忆、报告、trace |
| `litellm` | LiteLLM | LiteLLM 元数据 |
| `mem0_vector` | Mem0 | 语义记忆向量 |
| `mem0_app` | Mem0 | Mem0 REST Server 元数据 |

注意：`vector` / `pg_trgm` 扩展由 PostgreSQL 初始化或 `postgres-extensions` 运维任务创建，不由业务账号在 Alembic 中创建。

## 3. 后端对接接口总览

| 方法 | 路径 | 用途 | 是否主链路 |
| --- | --- | --- | --- |
| `GET` | `/health` | 存活检查 | 是 |
| `GET` | `/ready` | 就绪检查，检查编排器、规则库、知识库 | 是 |
| `POST` | `/agent/turns` | 生产主问诊入口 | 是 |
| `POST` | `/openai/v1/responses` | OpenAI Responses 风格兼容入口 | 可选 |
| `GET` | `/memories` | 查询指定 user/session/pet 的记忆 | 辅助 |
| `PUT` | `/memories` | 写入人工纠正记忆摘要 | 辅助 |
| `PUT` | `/memories/facts` | 写入/修正结构化宠物事实 | 辅助 |
| `DELETE` | `/memories/pets/{pet_id}` | 删除宠物记忆 | 辅助，谨慎使用 |
| `POST` | `/reports/parse` | 解析 OSS 图片报告 | 辅助 |
| `GET` | `/reports` | 查询报告列表 | 辅助 |
| `GET` | `/reports/{report_id}` | 查询报告详情 | 辅助 |
| `GET` | `/admin/rag/stats` | RAG 治理统计 | 运维 |
| `GET` | `/admin/rag/chunks` | RAG chunk 列表 | 运维 |
| `PATCH` | `/admin/rag/chunks/{chunk_id}` | RAG chunk 启停/审核 | 运维 |

## 4. BFF 与 Agent 的职责边界

建议后端/BFF 承担：

1. 用户登录态、业务权限、会员/租户/设备等平台侧鉴权。
2. 将业务用户、宠物、会话稳定映射为 Agent 所需的 `user_id`、`pet_id`、`session_id`。
3. 上传文件到 OSS，并将 OSS 图片地址或对象 key 传给 Agent 的报告解析接口。
4. 对外部客户端隐藏 Agent 内网地址、API Key、LiteLLM/Mem0 配置。

Agent 承担：

1. 单轮/多轮问诊状态管理。
2. 根据用户补充逐步构建上下文，避免信息不足时一次性武断回答。
3. 安全分诊和安全兜底。
4. RAG 检索、动态追问、最终答复。
5. 宠物事实记忆、语义记忆、逻辑留痕。

如果 Agent 只暴露给内网 BFF，当前更符合业务联调的配置是：

```env
REQUIRE_API_AUTH=false
VET_AGENT_API_KEYS=
REQUIRE_AUTH_USER_MATCH=false
PET_AUTHORIZATION_MODE=permissive
SESSION_POLICY_MODE=strict
```

含义：

- `REQUIRE_API_AUTH=false`：Agent 不再要求 `Authorization: Bearer ...`。
- `PET_AUTHORIZATION_MODE=permissive`：宠物第一次出现时自动注册到当前 `user_id`；后续其他用户复用同一 `pet_id` 仍会被拒绝。
- `SESSION_POLICY_MODE=strict`：同一个 `session_id` 不允许跨 user/pet 复用，避免串话。

若 Agent 直接暴露给公网或多 BFF，建议重新开启 `REQUIRE_API_AUTH=true`。

## 5. 主接口请求契约

主入口：

```http
POST /agent/turns
Content-Type: application/json
Accept: application/json 或 text/event-stream
```

关键字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `request_id` | 否 | 请求幂等/追踪 ID；也可通过 `X-Request-ID` 传入 |
| `trace_id` | 否 | 链路 ID；也可通过 `X-Trace-ID` 传入 |
| `model` | 否 | 默认 `qwen-plus` |
| `input` | 是 | 用户输入，支持 string / object / list |
| `stream` | 否 | `true` 返回 SSE；`false` 返回 JSON |
| `metadata` | 否 | 业务透传元数据 |
| `vet_context.user_id` | 是 | 业务用户 ID |
| `vet_context.session_id` | 是 | 一次连续问诊会话 ID |
| `vet_context.pet_id` | 是 | 业务宠物 ID |
| `vet_context.pet_info` | 否 | 宠物资料，用于预填槽位和自动注册 |
| `attachments` | 否 | 附件引用；报告解析建议走 `/reports/parse` |
| `turn_options.idempotency_key` | 否 | 幂等键 |
| `turn_options.timeout_ms` | 否 | 调用方期望超时 |
| `turn_options.max_followup_questions` | 否 | 单轮最多追问，范围 1-3 |

最小同步请求：

```json
{
  "input": "我的猫这两天和平时不太一样，想先确认还需要观察什么。",
  "stream": false,
  "vet_context": {
    "user_id": "u_001",
    "session_id": "s_001",
    "pet_id": "p_001",
    "pet_info": {
      "species": "feline",
      "age": "4 years",
      "weight_kg": 4.6
    }
  },
  "turn_options": {
    "max_followup_questions": 3
  }
}
```

流式请求需要：

```http
Accept: text/event-stream
```

且请求体：

```json
{
  "stream": true
}
```

## 6. 主链路整体流转

`POST /agent/turns` 的执行顺序如下：

```text
HTTP 请求
  -> FastAPI/Pydantic 请求体校验
  -> 合并 X-Request-ID / X-Trace-ID
  -> AccessControlService 鉴权、宠物归属、会话绑定
  -> ready 检查
  -> VetOrchestrator.run_turn
      -> 同 user/pet/session 加 PostgreSQL advisory lock
      -> 幂等处理
      -> 输入安全分诊
      -> 读取宠物上下文、记忆、问诊状态
      -> 判断是否已有未完成问诊状态
      -> 任务拆分或跳过任务拆分
      -> 问诊状态更新与槽位抽取
      -> 未就绪：RAG 检索 + 动态追问
      -> 已就绪：RAG 检索 + LLM 生成最终答复
      -> 输出安全复审
      -> 事实记忆抽取
      -> 持久化 turn / episode / facts / trace / idempotency
  -> JSON 或 SSE 返回
```

## 7. 访问控制与自动注册流转

入口路由在编排前调用：

```text
AccessControlService.authenticate(headers)
AccessControlService.authorize(identity, pet_info, principal)
```

### 7.1 API 鉴权

鉴权策略由 `REQUIRE_API_AUTH` 和 `VET_AGENT_API_KEYS` 共同决定：

| 配置 | 行为 |
| --- | --- |
| `REQUIRE_API_AUTH=false` 且 `VET_AGENT_API_KEYS` 为空 | 不要求 API Key |
| `REQUIRE_API_AUTH=true` | 必须传 `Authorization: Bearer <key>` 或 `x-api-key` |
| `VET_AGENT_API_KEYS` 非空 | 即使 `REQUIRE_API_AUTH=false`，也会要求 key |

### 7.2 宠物归属

`PET_AUTHORIZATION_MODE` 支持：

| 模式 | 行为 |
| --- | --- |
| `off` | 不检查宠物归属，也不自动注册 |
| `permissive` | 第一次看到 `pet_id` 自动注册到当前 `user_id`；后续禁止其他用户复用 |
| `strict` | 要求 `pet_id` 已提前注册到当前 `user_id` |

生产/BFF 内网联调建议 `permissive`，否则新宠物第一次请求会返回：

```json
{
  "code": "FORBIDDEN",
  "message": "pet_id is not registered for this user"
}
```

### 7.3 会话绑定

`SESSION_POLICY_MODE` 支持：

| 模式 | 行为 |
| --- | --- |
| `off` | 不绑定会话 |
| `permissive` / `strict` | 第一次看到 `session_id` 绑定当前 user/pet；后续同一 session 不允许换 user/pet |

后端对接要求：

1. 同一次连续问诊必须复用同一个 `session_id`。
2. 新一轮独立问诊应创建新的 `session_id`。
3. 不要把同一 `session_id` 用在不同宠物或不同用户上。

## 8. 问诊状态机

问诊状态存储在 `consultation_states` 表。

状态结构：

```json
{
  "chief_complaint": "用户首次主诉",
  "domain": "general",
  "phase": "collecting_info",
  "slots": {
    "species": "feline",
    "life_stage_or_age": "4 years",
    "weight": "4.6kg"
  },
  "asked_questions": [],
  "followup_rounds": 0
}
```

核心字段：

| 字段 | 说明 |
| --- | --- |
| `chief_complaint` | 首次主诉摘要 |
| `domain` | 问诊领域，如 `general`、`gastrointestinal`、`respiratory` 等 |
| `phase` | `collecting_info` 或 `ready_to_answer` |
| `slots` | 已收集槽位 |
| `asked_questions` | 已问过的问题，避免重复追问 |
| `followup_rounds` | 追问轮次 |

状态流转：

```text
空状态
  -> 本轮输入写入 chief_complaint
  -> 基于规则和 pet_info 预填槽位
  -> 从用户文本抽取槽位
  -> 检查当前 domain required_slots
      -> 缺槽位：phase=collecting_info，返回 requires_followup
      -> 不缺槽位：phase=ready_to_answer，进入最终回答
```

`requires_followup` 是系统满足 PRD 的关键状态：当上下文不足时，Agent 不会直接给结论，而是先输出追问。

## 9. 已有未完成问诊状态时的任务拆分策略

如果当前 `user_id + pet_id + session_id` 已存在未完成默认问诊状态：

```text
phase=collecting_info
且已有 chief_complaint / asked_questions / followup_rounds / slots
```

本轮会优先视为“用户在回答上一轮追问”，并跳过任务拆分。

响应 `metadata` 会包含：

```json
{
  "task_router_skipped": true,
  "task_router_strategy": "skipped_unfinished_consultation_state",
  "task_router_skip_reason": "当前 session 存在未完成问诊状态，本轮优先作为上一轮追问回答处理。"
}
```

这避免了同一 session 中“追问回答”被误判成新任务。

## 10. 单轮多任务拆分

当没有未完成默认问诊状态时，系统才会进入任务拆分：

```text
TaskSplitterAgent
  -> LLM TaskRouterAgent 可用：由 LLM 输出 1-5 个任务
  -> LLM 不可用或解析失败：规则关键词兜底
```

拆分结果超过 1 个任务时，进入多任务模式：

1. 每个任务按 `domain` 拥有独立 `task_key`。
2. 每个任务各自维护问诊状态。
3. 每个任务可以独立进入 `requires_followup` 或 `completed`。
4. 总体响应 status：只要有一个任务需要追问，总状态就是 `requires_followup`。

多任务响应关键字段：

```json
{
  "vet_result": {
    "route": "multi_task_consultation",
    "task_count": 2
  },
  "metadata": {
    "task_count": 2,
    "task_router_strategy": "llm_task_router",
    "tasks": [
      {
        "task_id": "task_001",
        "title": "消化道问题",
        "domain": "gastrointestinal",
        "status": "requires_followup",
        "missing_slots": []
      }
    ]
  }
}
```

## 11. RAG 动态追问流转

当问诊状态未就绪时：

```text
ConsultationStateAgent 给出缺失槽位和规则兜底问题
  -> Orchestrator 构造知识库检索 query
  -> KnowledgeService 检索 knowledge_chunks
  -> RagQuestionPlannerAgent 基于知识证据反推追问
      -> 成功：使用 rag_llm_question_planner 问题
      -> 失败：回退 rule_slot_fallback
  -> 保存 consultation_state
  -> 返回 requires_followup
```

输出中会带：

| 字段 | 说明 |
| --- | --- |
| `status` | `requires_followup` |
| `vet_result.route` | `rag_guided_followup` |
| `metadata.missing_slots` | 当前仍缺的槽位 |
| `metadata.followup_question_plan` | 动态追问规划 |
| `reasoning_display` | 面向用户展示的“思考过程” |
| `evidence` | 用户输入、宠物上下文、知识库证据 |

重要：`reasoning_display` 是面向用户的诊断证据摘要，不暴露模型隐藏推理链。

## 12. 最终回答流转

当问诊状态已补齐：

```text
ConsultationStateAgent ready=true
  -> 保存 ready_to_answer 状态
  -> KnowledgeService 检索相关知识
  -> ResponseComposer 结合用户文本、pet_context、memory、knowledge_hits 生成回答
  -> SafetyAgent sanitize 输出
  -> ReasoningDisplayBuilder 生成证据展示
  -> SafetyReviewAgent 复审
  -> MemoryExtractionAgent 抽取事实
  -> 持久化
  -> 返回 completed
```

典型响应：

```json
{
  "status": "completed",
  "vet_result": {
    "generation_profile": "standard",
    "route": "standard_consultation",
    "audit_tier": "A"
  },
  "metadata": {
    "consultation_phase": "ready_to_answer",
    "missing_slots": [],
    "memory_extraction": {
      "agent": "MemoryExtractionAgent",
      "stored_fact_count": 2
    }
  }
}
```

## 13. 安全分诊

输入进入问诊前会先走 `SafetyAgent.analyze`：

| 结果 | 行为 |
| --- | --- |
| 普通 | 继续问诊 |
| `escalated` | 返回安全升级建议，status=`safety_escalated` |
| `blocked` | 返回安全阻断文案，status=`blocked` |

安全分诊返回也会持久化，并带：

```json
{
  "vet_result": {
    "route": "safety_triage",
    "generation_profile": "safety",
    "audit_tier": "A"
  },
  "safety_signals": []
}
```

## 14. 流式 SSE 响应

流式接口仍然会先完整执行一轮 `run_turn`，再把结果按 SSE 分片输出。因此首包时间取决于内部编排和 LLM 调用耗时。

事件顺序：

```text
turn.started
reasoning_display.started
reasoning_display.delta
reasoning_display.completed
segment.started
segment.delta
segment.completed
turn.completed
```

多 segment 或多任务时，会重复出现 `reasoning_display.*` 和 `segment.*`。

curl 观测建议：

```bash
curl -N --http1.1 \
  -X POST "http://127.0.0.1:18081/agent/turns" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  --data-binary @payload.json
```

`-N` 用于关闭 curl 缓冲。

## 15. 记忆系统

记忆分三层：

| 层级 | 表/服务 | 内容 |
| --- | --- | --- |
| 短期 | `conversation_turns` | 同 session 最近 turn |
| 中期 | `pet_memory_episodes` | 医疗对话 episode 摘要 |
| 长期事实 | `pet_memory_facts` | 宠物事实、用户纠正、结构化偏好 |
| 语义记忆 | Mem0 | turn 语义向量检索 |

每轮结束时：

1. `remember_turn` 写入 `conversation_turns`。
2. 医疗回合写入 `pet_memory_episodes`。
3. `MemoryExtractionAgent` 抽取事实并 upsert 到 `pet_memory_facts`。
4. 若 `ENABLE_MEM0=true`，向 Mem0 写入语义记忆；Mem0 失败不会阻塞主回答。
5. `logic_traces` 写入逻辑留痕。

查询记忆：

```http
GET /memories?user_id=<user>&session_id=<session>&pet_id=<pet>
```

写入人工事实修正：

```http
PUT /memories/facts
Content-Type: application/json
```

```json
{
  "user_id": "u_001",
  "session_id": "s_001",
  "pet_id": "p_001",
  "fact_type": "medical_profile",
  "fact_key": "allergy",
  "fact_value": "对某药物过敏",
  "confidence": 1.0,
  "metadata": {
    "source": "bff_manual_correction"
  }
}
```

## 16. 幂等与并发

同一 `user_id + pet_id + session_id` 会使用 PostgreSQL advisory lock 串行化同宠物同会话的回合处理，避免多请求同时改同一问诊状态。

若请求带 `turn_options.idempotency_key`：

| 幂等状态 | 行为 |
| --- | --- |
| `claimed` | 当前请求获得处理权 |
| `replayed` | 返回之前保存的 response snapshot |
| `busy` | 等待超时，抛出 `TimeoutError`，外层映射为 504 |
| `failed` | 异常时标记失败 |

幂等作用域：

```text
user_id + pet_id + session_id + idempotency_key
```

BFF 建议：

1. 用户点击重试时复用同一个 `idempotency_key`。
2. 新问题不要复用旧 `idempotency_key`。
3. 请求超时后可以用同一个 key 重试，以便拿到已完成快照。

## 17. 报告解析流转

报告解析入口：

```http
POST /reports/parse
Content-Type: application/json
```

请求：

```json
{
  "user_id": "u_001",
  "session_id": "s_001",
  "pet_id": "p_001",
  "report_type": "blood_test",
  "oss_image_url": "oss://infra-prod-file-storage/path/to/report.jpg",
  "metadata": {
    "source": "bff"
  }
}
```

`oss_image_url` 兼容别名：

- `image_url`
- `storage_ref`
- `oss_url`
- `file_url`

支持输入：

| 形式 | 示例 |
| --- | --- |
| OSS URL | `oss://bucket/key.jpg` |
| HTTPS OSS URL | `https://bucket.endpoint/key.jpg` |
| 对象 key | `path/to/key.jpg`，bucket 使用 `OSS_BUCKET` |

校验逻辑：

1. bucket 必须等于 `OSS_BUCKET`。
2. key 若配置 `OSS_PREFIX`，必须以该前缀开头。
3. endpoint 必须匹配 `OSS_ENDPOINT` 或其公网/内网等价 host。
4. 文件后缀必须是支持的图片类型。
5. 放射影像类报告会被安全阻断，不在线解释。

解析逻辑：

```text
校验 OSS 图片来源
  -> Qwen Vision 解析图片
  -> 结构化 items / summary / raw_text
  -> 写入 pet_reports + pet_report_items
  -> 返回 report_id
```

如果视觉模型失败，状态为 `needs_ocr`，并返回人工复核建议。

## 18. 数据表速查

| 表 | 作用 |
| --- | --- |
| `safety_rules` | 安全规则 |
| `consultation_domains` | 问诊领域和 required_slots |
| `consultation_slots` | 槽位问题、标签、抽取规则 |
| `knowledge_chunks` | RAG 知识 chunk 和 embedding |
| `conversation_turns` | 每轮对话记录和 response snapshot |
| `pet_profiles` | 宠物归属与 pet_info |
| `pet_session_bindings` | session 与 user/pet 绑定 |
| `consultation_states` | 默认/多任务问诊状态 |
| `pet_memory_facts` | 长期结构化事实 |
| `pet_memory_episodes` | 中期 episode 摘要 |
| `pet_reports` | 报告主表 |
| `pet_report_items` | 报告结构化指标 |
| `rag_audit_events` | RAG 治理审计 |
| `logic_traces` | 逻辑留痕 |
| `idempotency_records` | 幂等记录 |

## 19. 响应状态与路由速查

| `status` | 含义 | 常见 `vet_result.route` |
| --- | --- | --- |
| `requires_followup` | 信息不足，需要用户补充 | `rag_guided_followup` / `multi_task_consultation` |
| `completed` | 已形成最终回答 | `standard_consultation` / `multi_task_consultation` |
| `safety_escalated` | 安全升级，建议线下或紧急处理 | `safety_triage` |
| `blocked` | 安全阻断 | `safety_triage` |

后端展示建议：

1. 用户主文本使用 `output_text`。
2. 分段展示使用 `segments[]`。
3. “思考过程/诊断证据”展示使用 `reasoning_display.text` 或 `segments[].reasoning_display.text`。
4. 引用/证据来源使用 `segments[].references` 与 `evidence`。
5. 不要把 `metadata.consultation_state` 原样展示给用户，可用于调试。

## 20. 错误码与常见原因

统一错误响应：

```json
{
  "code": "INVALID_REQUEST",
  "message": "Invalid request",
  "request_id": "req_xxx",
  "trace_id": "trace_xxx",
  "details": {}
}
```

常见错误：

| HTTP | code | 常见原因 | 处理建议 |
| --- | --- | --- | --- |
| 400 | `INVALID_REQUEST` | JSON 格式错误、header/request_id 冲突、OSS URL 非法 | 修正请求 |
| 401 | `UNAUTHORIZED` | 缺 API Key 或 key 错误 | 若内网 BFF 对接，关闭 Agent 鉴权；否则传正确 key |
| 403 | `FORBIDDEN` | pet 未注册、pet 属于其他 user、session 复用到其他 user/pet | 使用稳定 ID；`PET_AUTHORIZATION_MODE=permissive` 支持首次自动注册 |
| 413 | `PAYLOAD_TOO_LARGE` | 输入/附件超过限制 | BFF 截断或拆分 |
| 422 | `MISSING_REQUIRED_CONTEXT` | 缺 `vet_context.user_id/session_id/pet_id` | 补齐上下文 |
| 503 | `SERVICE_UNAVAILABLE` | 编排器未就绪，LiteLLM/规则库/知识库不可用 | 检查 `/ready` 和容器日志 |
| 504 | `ORCHESTRATOR_TIMEOUT` | 编排超时或幂等 busy | BFF 可重试 |

JSON 注意事项：

- JSON 不允许最后一个字段后带尾随逗号。
- `stream` 必须是 boolean，不要传字符串 `"true"`。
- `vet_context.pet_info` 可为空对象，但 `vet_context` 本身和三个 ID 必填。

## 21. 后端推荐接入流程

### 21.1 新问诊

```text
BFF 生成 session_id
BFF 传稳定 user_id / pet_id / pet_info
调用 POST /agent/turns stream=false 或 true
若 status=requires_followup：
  展示 output_text 中的问题
  下一轮继续使用同一个 session_id
若 status=completed：
  展示最终回答
```

### 21.2 用户回答追问

```text
BFF 继续使用同一 user_id + pet_id + session_id
input 填用户补充内容
Agent 自动读取 consultation_states
如果仍缺上下文：继续 requires_followup
如果已补齐：completed
```

### 21.3 新一轮独立问题

```text
BFF 创建新的 session_id
继续复用 user_id + pet_id
Agent 可读取宠物长期事实和 episode，但不会把旧 session 的未完成问诊状态混入
```

### 21.4 修改记忆

当用户明确纠正宠物事实时，BFF 应调用：

```http
PUT /memories/facts
```

而不是仅通过自然语言让 Agent 猜测修正。

### 21.5 报告解析

```text
BFF 上传图片到 OSS
BFF 调 /reports/parse 传 oss_image_url
Agent 校验 bucket / endpoint / prefix
Agent 调视觉模型解析
BFF 保存 report_id 或直接展示解析结果
```

## 22. 生产部署与网络提醒

当前生产服务在宿主机暴露端口由 `APP_PORT` 决定；你当前环境使用 `18081`。

Docker Compose 内部网络若与阿里云 VPC 冲突，应通过生产 override 指定不冲突网段，例如：

```yaml
networks:
  backend:
    driver: bridge
    ipam:
      driver: default
      config:
        - subnet: "${DOCKER_BACKEND_SUBNET}"
          gateway: "${DOCKER_BACKEND_GATEWAY}"
```

生产命令需同时带：

```bash
-f docker-compose.yml -f docker-compose.prod.override.yml
```

不要使用 `docker compose down -v`，否则会删除 PostgreSQL 数据卷。

## 23. 对接验收清单

后端/BFF 联调时建议逐项验证：

1. `/health` 返回 `{"status":"ok"}`。
2. `/ready` 返回 `status=ready`。
3. 新宠物首次请求不会 403，并在 `PET_AUTHORIZATION_MODE=permissive` 下自动注册。
4. 第一轮信息不足时返回 `status=requires_followup`。
5. 第二轮同 session 补充信息后，能逐步减少 `missing_slots` 并最终 `completed`。
6. 同一未完成 session 下不会误触发任务拆分。
7. 单轮多任务输入能返回多个 `segments`。
8. `stream=true` 时能看到 `reasoning_display.completed`、`segment.delta`、`turn.completed`。
9. `/memories` 能查到事实记忆和最近 turn。
10. `/reports/parse` 能接受后端传来的 OSS 图片地址。
11. 错误场景返回统一 `code/message/request_id/trace_id/details`。

## 24. 源码对应关系

后端同事排查问题时，可按下表快速定位：

| 主题 | 主要文件 |
| --- | --- |
| FastAPI 应用入口 | `src/vet_agent/main.py`、`src/ingress/routes.py` |
| 请求 DTO / 响应 DTO | `src/ingress/dto.py`、`src/vet_agent/contracts.py` |
| 统一错误响应 | `src/ingress/errors.py` |
| 容器与依赖装配 | `src/vet_agent/container.py` |
| 主编排流程 | `src/vet_agent/orchestrator.py` |
| 鉴权、宠物归属、会话绑定 | `src/vet_agent/services/access_control.py` |
| 问诊状态机 | `src/vet_agent/agents/consultation.py` |
| 任务拆分 | `src/vet_agent/agents/task_splitter.py` |
| RAG 动态追问 | `src/vet_agent/agents/rag_question_planner.py` |
| 记忆持久化 | `src/vet_agent/services/postgres_memory.py` |
| Mem0 语义记忆 | `src/vet_agent/services/semantic_memory.py` |
| 报告解析 | `src/vet_agent/api/report_routes.py`、`src/vet_agent/services/reports.py` |
| 记忆修正接口 | `src/vet_agent/api/memory_routes.py` |
| RAG 管理接口 | `src/vet_agent/api/admin_routes.py` |
| SQLAlchemy 模型 | `src/vet_agent/db/models.py` |
| Alembic 迁移 | `alembic/versions/` |
| 外部接口主契约 | `docs/external_api.md` |

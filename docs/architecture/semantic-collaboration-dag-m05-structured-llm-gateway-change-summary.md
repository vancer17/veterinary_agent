<!--
=============================================================================
文件: semantic-collaboration-dag-m05-structured-llm-gateway-change-summary.md
作用: 记录受限语义协作 DAG M05 结构化 LLM Gateway 的生产实现边界。
范围: 覆盖 Gateway 契约、单次底层传输、strict JSON 解析、权威 schema 校验、
      attempt metadata、失败分类、可观测性边界、复用边界、有意 TODO 与
      后续 M06～M11 对接方式。
说明: 本文只描述已实现的生产工程边界；不接入 VetOrchestrator，不生成
      verified artifact，不调用问诊、临床安全或长期记忆领域。
维护: 当 M05 契约、模型传输 metadata、失败分类或生产接入边界调整时同步更新。
=============================================================================
-->

# 受限语义协作 DAG M05 结构化 LLM Gateway 变更总结

> **文档状态**：M05 生产契约与实现已完成；待 M06 prompt renderer、M07
> verifier、M11 artifact store 和 SemanticTaskExecutor 组合接入后关闭

## 1. 当前状态

| 项目 | 当前状态 |
|---|---|
| M05 调用契约 | 已实现 |
| Qwen 单次结构化传输 | 已实现 |
| strict JSON object 解析 | 已实现 |
| SkillCatalog schema 绑定 | 已实现 |
| prompt / proposal / contract digest | 已实现 |
| usage 与模型快照 metadata | 已实现 |
| M06 SKILL prompt renderer | 未实现 |
| M07 Deterministic Verifier | 未实现 |
| M11 Artifact Store | 未实现 |
| SemanticTaskExecutor 接入 | 未实现 |
| VetOrchestrator 接入 | 未接入 |
| 真实 LiteLLM 外部冒烟 | 未执行 |
| 真实 Temporal workflow 联调 | 未执行 |

M05 当前只返回 `SemanticModelProposal`。该对象是模型 proposal，不是 verified
artifact，不能进入 claim graph 或任何领域投影。

## 2. 稳定契约边界

M05 对上游暴露的稳定输入是：

```text
StructuredLLMCallRequest
SkillPromptProjection
SemanticTaskExecutionRequest
SchemaContractReference
```

对下游暴露的稳定输出是：

```text
SemanticModelProposal
StructuredLLMCallMetadata
proposal_digest
```

输入身份必须闭合：

```text
run_id
task_id
attempt_number
turn_snapshot_digest
skill_id
skill_version
output schema reference
prompt context digest
```

输出中的 `payload` 只代表模型 proposal 已通过 M05 的权威 JSON Schema 校验；
它尚未通过字段所有权、evidence binding、claim binding、Review 或领域语义
验证，因此不能被称为 verified artifact。

## 3. 生产实现边界

```text
StructuredLLMCallRequest
→ SkillRegistry 契约解析
→ PlanTask schema digest 校验
→ SkillPromptProjection 身份与 context digest 校验
→ StructuredModelTransport.structured_once
→ strict JSON object 解析
→ SkillCatalog 权威 JSON Schema 校验
→ SemanticModelProposal + attempt metadata
```

M05 保持以下不变量：

1. 一个 `attempt_number` 只对应一次底层模型传输。
2. 底层传输不做内部 retry、隐藏 fallback 或宽松 JSON 修复。
3. `SkillSpec`、schema 引用、prompt 身份和 TurnSnapshot digest 错配时，在模型调用前阻断。
4. extra field、重复 JSON key、非 object 根节点、NaN 和 Infinity 均显式失败。
5. usage 缺失保持 `None` 和 `usage_available=false`，不伪造零值。
6. 调用 metadata 只记录摘要与快照，不记录完整 prompt、原始响应或密钥。
7. 输出仅通过 SkillCatalog JSON Schema 校验；字段所有权和证据绑定留给 M07。

## 4. 运行时复用

M05 复用 `QwenClient` 的 LiteLLM OpenAI-compatible 基础设施：

```text
配置读取
认证
response_format=json_schema
并发信号量
pacing
circuit breaker
HTTP 超时
```

新增 `QwenClient.structured_once()` 作为单次传输原语。它与既有
`chat_structured()` 的差异是：

| 能力 | `chat_structured()` | `structured_once()` |
|---|---|---|
| 服务对象 | 既有生产结构化调用 | M05 |
| attempt 边界 | 客户端内部 retry | 单次调用 |
| 模型选择 | 可按配置 fallback | 精确模型 |
| 返回值 | Pydantic 输出 | 原始 content 与 metadata |
| 响应修复 | 不修复 | 不修复 |

旧 `chat_structured()` 保持兼容，供既有业务继续使用；M05 不得调用该旧路径。

## 5. 失败语义

| 异常 | 稳定失败码 | 语义 |
|---|---|---|
| `StructuredLLMGatewayContractError` | `structured_gateway_contract_violation` | Skill、schema、prompt 或上下文契约错配 |
| `StructuredLLMModelCallError` | `model_call_failed` | 传输失败、模型切换、finish reason 或 usage 不一致 |
| `StructuredLLMResponseParseError` | `response_parse_failed` | 非 JSON、重复 key、非 object 根节点或非法常量 |
| `StructuredLLMSchemaError` | `schema_invalid` | 未通过权威输出 schema |

契约错配发生在模型调用前；模型调用、解析和 schema 失败均保留 attempt metadata。
这些异常不是 fallback 信号，单次请求内不得回退旧问诊语义抽取器。

## 6. 后续对接

M05 不直接替换 `TODOSemanticTaskExecutor`。后续组合方式固定为：

```text
M06 prompt renderer
→ M05 StructuredLLMGateway
→ M07 Deterministic Verifier
→ M11 Artifact Store
→ SemanticTaskExecutor
```

在 M06～M11 完成前，M04 的 TODO 执行器继续 Fail Fast。

## 7. 有意预留 TODO

| TODO | 责任模块 | M05 当前边界 | 后续对接方式 |
|---|---|---|---|
| SKILL prompt renderer | M06 | 只接收 `SkillPromptProjection`，不生成语义 prompt | 按 SkillSpec、TurnSnapshot 投影和 envelope 生成提示词 |
| Turn Intent 生成 SKILL | M06 | 不拥有任何语义输出字段 | 生成 intent proposal 并交给 M07 |
| Claim Inventory 生成 SKILL | M06 | 不拆分 claim | 生成 claim envelope / inventory proposal |
| Statement Semantics 生成 SKILL | M06 | 不判断断言语义 | 生成 statement semantics proposal |
| Phrase / Canonical 生成 SKILL | M06 | 不解析 participant、temporal、measurement 或 canonical | 生成对应 phrase / descriptor proposal |
| Deterministic Verifier | M07 | 只做 JSON Schema 校验 | 复核 schema、所有权、evidence、binding 与 forbidden output |
| Artifact Store | M11 | 不提交、版本化或 stale 标记 proposal | 只提交 M07 verified 后的 artifact |
| SemanticTaskExecutor 组合 | M04 / M06 / M07 / M11 | 不返回任务业务终态 | 组合 prompt、gateway、verifier 与 artifact commit |
| 真实 LiteLLM 冒烟 | 集成验证 | 单元测试使用测试替身 | 显式外部测试验证模型响应与 metadata |
| 真实 Temporal workflow 联调 | 集成验证 | 未接入 activity 执行器 | 验证 attempt、失败重试与 event history |

这些 TODO 是职责边界，不是可以由 M05 代实现的技术债。M05 不得为了
“先跑通”生成 prompt、补 verifier、提交 artifact 或调用下游领域。

## 8. 可观测性边界

每次 M05 调用至少可审计：

```text
run_id
task_id
attempt_number
skill_id
skill_version
turn_snapshot_digest
prompt_hash
skill_contract_digest
output_schema_id
output_schema_version
output_schema_digest
requested_model
response_model
response_id
finish_reason
usage_available
prompt_tokens
completion_tokens
total_tokens
latency_ms
proposal_digest
failure_code
```

禁止把以下内容写入默认 trace、日志或报告：

```text
完整 prompt
完整原始模型响应
API key
Authorization header
LiteLLM base URL
数据库连接串
```

`usage_available=false` 表示模型网关没有返回完整可用 usage，对应 token 数保持
`None`；不得用 `0` 伪装成功或污染指标。

## 9. 安全边界

本轮实现保持：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
long_term_memory_written = false
```

未接入：

```text
VetOrchestrator
问诊状态
临床安全召回与裁决
长期记忆
```

## 10. 验证结果与边界

```text
ruff check M05 相关模块与测试: PASS
mypy M05 相关模块与测试: PASS
pytest tests/test_semantic_collaboration_gateway.py: 10 passed
```

同时已通过本地快速 Python 门禁：

```text
compileall src tests: PASS
全量默认单元测试: 271 passed, 43 skipped
```

当前验证只覆盖契约与测试替身路径，不等于：

```text
真实 LiteLLM 调用已验证
真实 Temporal workflow 已验证
端到端 verified claim graph 已验证
生产主路径已切换
```

## 11. 文档同步触发条件

以下变化发生时必须同步更新本文：

```text
StructuredLLMCallRequest / SemanticModelProposal 契约调整
StructuredLLMCallMetadata 字段调整
失败码或失败分类调整
attempt / retry / fallback 语义调整
prompt projection 契约调整
M06 / M07 / M11 接入状态变化
真实 LiteLLM 或 Temporal 联调完成
生产接入或回滚策略变化
```

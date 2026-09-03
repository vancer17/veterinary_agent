<!--
=============================================================================
文件: semantic-collaboration-dag-m06-generation-skill-change-summary.md
作用: 记录受限语义协作 DAG M06 生成 SKILL 的生产实现边界与验证状态。
范围: 覆盖 M01 生产目录收窄、M03 确定性 Root Plan、标准化 SKILL.md、受限
      Jinja 模板、版本化 Prompt Renderer、结构化 tag 输入、极薄 strict JSON
      输出、Generation Runner、精确模型策略、最小结构 verifier 与测试边界。
说明: 本文记录已实现的生产工程边界；不接入 VetOrchestrator，不提交 artifact，
      不执行语义 Review / Evidence Binding，不调用问诊、临床安全或长期记忆领域。
维护: 当生成 SKILL 输出契约、Prompt Renderer、模型策略或 M06 / M07 接入状态
      调整时，必须同步更新本文。
=============================================================================
-->

# 受限语义协作 DAG M06 生成 SKILL 变更总结

> **文档状态**：M06 生产契约与实现已完成；待 M08 Review、M11 Artifact Store、
> `SemanticTaskExecutor` 组合和真实 LiteLLM / Temporal 联调后关闭

## 1. 当前状态

| 项目 | 当前状态 |
|---|---|
| Turn Intent fixed-field schema | 已实现 |
| Claim Proposition Inventory schema | 已实现 |
| 确定性 Root Plan Compiler | 已实现 |
| 规划 LLM / PlanSelection | 已移除 |
| 标准化 SKILL.md | 已实现 |
| 受限 Jinja 模板 | 已实现 |
| 版本化 Prompt Renderer | 已实现 |
| RendererRegistry 启动闭合校验 | 已实现 |
| GenerationModelPolicy | 已实现 |
| StructuredGenerationSkillRunner | 已实现 |
| M05 Gateway 组合 | 已实现 |
| 最小 M07 结构 verifier | 已实现 |
| 持久化 TurnSnapshotReader | TODO，显式 Fail Fast |
| M08 Coverage / Faithfulness Review | 未实现 |
| M11 Artifact Store | 未实现 |
| `SemanticTaskExecutor` 生产组合 | 未替换 TODO |
| VetOrchestrator 接入 | 未接入 |
| 真实 LiteLLM 冒烟 | 未执行 |
| 真实 Temporal workflow 联调 | 未执行 |

M06 Runner 当前只返回 `SemanticModelProposal`。该 proposal 不是 verified artifact，
不能进入 claim graph、问诊状态、临床安全或长期记忆。

## 2. 契约收敛

生产生成面只保留：

```text
turn_intent
claim_inventory
```

以下 lane 从当前生产 SkillCatalog 与 PlanPolicy 中移除，保持 deferred：

```text
statement_semantics
participant_phrase
temporal_phrase
measurement_phrase
canonical_descriptor
```

当前不存在规划 LLM，也不存在 `PlanSelection`。初始 Root Plan 由确定性代码生成：

```text
turn_root envelope
turn_intent task
claim_inventory task
```

两个根任务互不依赖，可并行调度。claim 数量由后续 M07 从 `claims.length`
确定性派生；claim envelope 分配后置到语义审查稳定之后。当前 PlanPolicy 契约
版本为 `3.0.0`。

### 2.1 当前稳定交接边界

当前 M06 的上游输入是：

```text
TurnSnapshot
权威 PlanTask
SkillCatalog 精确版本
受限上下文投影
```

当前 M06 的输出是：

```text
Turn Intent proposal
Claim Proposition Inventory proposal
```

当前 M06 的下游只能消费：

```text
M07 结构 verifier
M08 Coverage / Faithfulness Review
```

当前 M06 的输出不能直接进入：

```text
M11 Artifact Store
M12 Claim Graph
问诊状态
临床安全链路
长期记忆
RAG 查询主路径
```

原因不是输出格式不完整，而是语义正确性与 coverage 尚未经过 M08 审查，也没有
M11 artifact 版本与 lineage。

## 3. Turn Intent 输出

Turn Intent 输出七个 required boolean：

```json
{
  "answer_now": false,
  "wants_triage": false,
  "correction": false,
  "clarification_request": false,
  "fact_statement_present": true,
  "question_present": false,
  "report_context_present": false
}
```

不输出：

```text
evidence
reason
confidence
claim
medical_decision
```

## 4. Claim Proposition Inventory 输出

Claim Inventory 输出自包含中文自然语言 proposition：

```json
{
  "claims": [
    "英短没有呕吐",
    "英短大便没有血"
  ]
}
```

Claim proposition 使用对象层主语义。主语义必须是当前宠物、宠物状态、宠物
行为或宠物相关事件，例如 `英短没有呕吐`。不得把 `用户报告`、`用户认为`、
`用户询问` 作为 proposition 主语义；来源、说话人和观察方式由系统 metadata
与后续审查状态承载，避免 RAG 向量被“用户报告”行为语义污染。

禁止输出主题词、结构化语义字段和自证字段：

```text
呕吐
血便
claim_id
ordinal
target
unit_type
shared_parent
evidence_phrase
assertion_state
certainty
scope
entity_id
canonical_id
reason
confidence
```

工程身份由 PlanTask、TurnSnapshot digest、SkillSpec 与 M05 metadata 绑定。

## 5. 标准化 SKILL 与 Prompt Renderer

当前生产包内提供两份标准化文档：

```text
src/vet_agent/semantic_collaboration/skills/turn_intent/SKILL.md
src/vet_agent/semantic_collaboration/skills/claim_inventory/SKILL.md
```

文档结构：

```text
文件头部静态元数据区
Identity / Scope / Context Policy / Output Authority / Failure And Repair
Role
Workflow
Output Constraints
Exception And Boundary Rules
Memory And Context Rules
Prompt Context Template
Safety Boundary
```

文件头部元数据区声明：

```text
skill_id / skill_version / prompt_version
task_kind / execution_family
verifier id / version
output schema id / version
context resources
prompt variables
model-visible sections
```

该区域仅确定性代码可见，启动期与 SkillSpec、输出 schema、上下文契约和
verifier 绑定逐项校验，不进入模型消息。

M06 输入 prompt 使用结构化 tag：

```text
<current_turn>
...
</current_turn>

<last_assistant_questions>
none
</last_assistant_questions>
```

Renderer 不向模型展示：

```text
task_id
run_id
attempt_number
turn_snapshot_digest
skill_id / skill_version
prompt_version
front matter / metadata
owned_fields / forbidden_fields
完整 JSON schema
claim envelope count
```

Renderer 版本：

```text
turn_intent: skill 2.0.0 + prompt 1.1.0
claim_inventory: skill 2.0.0 + prompt 1.2.0
```

遇到保留 tag 冲突时在模型调用前 Fail Fast，不得截断或改写原文。

Prompt Context Template 使用受限 Jinja 子集，只允许顶层白名单字符串变量：

```text
current_turn
last_assistant_questions
verified_prior_facts
trusted_pet_context
```

启动期通过 AST 白名单禁止：

```text
条件
循环
过滤器
属性访问
方法调用
宏 / import / include
任意表达式
```

渲染变量集合必须与文档声明完全一致，并使用 StrictUndefined。用户原文只作为
变量值传入，不会作为模板源执行。

## 6. 生成执行链路

```text
SemanticTaskExecutionRequest
→ SkillRegistry.resolve
→ TurnSnapshotReader.load
→ snapshot.verify_digest
→ TurnSnapshotProjector.project
→ RendererRegistry.render
→ GenerationModelPolicy.require
→ M05 StructuredLLMGateway
→ SemanticModelProposal
```

`TODOTurnSnapshotReader` 是有意保留的持久化读取空壳，始终抛出
`NotImplementedError`，不伪造快照、不返回空上下文、不做旧链路回退。

### 6.1 当前执行断点

当前链路停在：

```text
M06 Generation Runner
→ M05 StructuredLLMGateway
→ SemanticModelProposal
→ 最小 M07 结构 verifier
```

当前不存在以下后续链路：

```text
M08 Review
Repair / Clarification Router
Claim Envelope Allocator
M11 Artifact Store
M12 Claim Graph
M13 Domain Projection
```

因此 `SemanticModelProposal` 不能被包装成 `verified artifact`，也不能携带伪造的
artifact reference。

## 7. 最小结构 verifier

`SemanticGenerationVerifier` 只验证：

```text
Turn Intent required boolean shape
claims required array shape
claim 非空
claim 单行
claim 重复
claim 数量由 claims.length 确定性派生
proposal 与 task 身份一致
```

它不做：

```text
evidence 字面锚定
Coverage Review
Faithfulness Review
医学语义判断
artifact 提交
```

claim 数量当前只作为结构验证结果派生：

```text
claim_count = claims.length
```

该数量不能反向约束 Claim Inventory 输出，也不能触发自动截断、合并或重试。
claim envelope 分配尚未实现，必须等 M08 审查与可能 repair 后的 claim 列表稳定。

## 8. 有意预留 TODO 与后续责任

这些 TODO 是职责边界，不是待绕过的错误，也不是允许由 M06 代实现的技术债。

| TODO | 当前行为 | 后续责任 | 启用条件 |
|---|---|---|---|
| 持久化 TurnSnapshotReader | 显式 Fail Fast | M02 / M06 运行时接入 | 定义 snapshot 持久化、按 digest 读取与生命周期契约 |
| Coverage Review | 未实现 | M08 | 输入 current_turn 与 generated claims，输出固定布尔覆盖矩阵 |
| Faithfulness Review | 未实现 | M08 | 单 claim 输入，输出中文语义漂移布尔矩阵 |
| Evidence Binding | 未实现，当前可人工审查 | M08 / 后置证据绑定 | 独立 anchor 契约或人工审查记录契约明确 |
| Claim Envelope Allocator | 未实现 | M08 / M12 | claim 列表通过 coverage、faithfulness 与 repair 后稳定 |
| Artifact Store | 未实现 | M11 | append-only、版本、lineage、stale 与幂等提交契约明确 |
| `SemanticTaskExecutor` 生产组合 | TODO executor 继续显式失败 | M04 / M06 / M07 / M11 | M07、M08、M11 的消费时序与失败终态闭合 |
| 真实 LiteLLM 冒烟 | 未执行 | 集成验证 | 显式外部测试环境与模型 snapshot |
| 真实 Temporal workflow 联调 | 未执行 | M04 / M15 | Temporal server、worker、task queue 与投影就绪 |
| VetOrchestrator 生产接入 | 未接入 | M15 | 显式配置切换、超时策略、回滚与观测就绪 |

### 8.1 明确不是 TODO 的路径

以下内容已被当前生产边界移除或禁止，不应作为后续待办重新引入：

```text
任务规划 LLM
PlanSelection
claim_envelope_count
claim 数量前置预估
初始 Root Plan 预分配 claim envelope
Statement Semantics lane
Participant Phrase lane
Temporal Phrase lane
Measurement Phrase lane
Canonical Descriptor lane
生成器 evidence 字面锚定
生成器 reason / confidence 自证
深层结构化语义 schema
宽松 JSON 修复
硬关键词规则
旧问诊语义抽取器 fallback
```

若后续要恢复其中任何能力，必须先修订生产架构基线、SkillSpec、PlanPolicy 与
验收测试，不允许在实现中静默兼容。

## 9. 后续模块对接边界

### 9.1 M08 Coverage Review

输入：

```text
current_turn
必要的受限上下文
generated claims
```

必须发现：

```text
漏抽显式事实
多事实合并
重复 claim
原文不支持的 claim
非自包含 proposition
shared scope 拆分错误
```

Coverage Review 不得直接修改 claims；missing claim 只能作为 repair hint 或人工
审查线索。

### 9.2 M08 Faithfulness Review

输入：

```text
current_turn
必要的受限上下文
单条 claim proposition
```

输出应为固定中文布尔矩阵，而非泛化 verdict。至少需覆盖：

```text
主体或指代范围改变
否定方向改变
否定范围改变
正常状态误写为否认
事实类型改变
时间范围改变
频率或数量改变
程度或强度改变
确定性改变
因果关系改变
医学推断或建议添加
命题不自包含
指代对象不明
时间基准不明
否定范围不明
比较基线不明
未分类语义改变
```

业务结果由确定性规则从布尔矩阵派生，不由 Review LLM 输出自由 verdict。

### 9.3 Claim Envelope Allocator

未来 allocator 只能在 claim 列表稳定后执行：

```text
claims[0] → claim_env_0000
claims[1] → claim_env_0001
...
```

分配前必须满足：

```text
claim 数量未超过 schema 上限
无重复 proposition
Coverage Review 无未处理漏抽或合并问题
Faithfulness Review 无未处理语义漂移
repair 后列表已重新审查
```

不得在 M06 proposal 阶段预分配 envelope。

### 9.4 M11 Artifact Store

M11 只能消费完整门禁后的结果，不能消费：

```text
SemanticModelProposal
未审查 claims
结构 verifier accepted 但语义审查未完成的 proposition
```

artifact 必须保留：

```text
source plan identity
turn snapshot digest
skill / prompt identity
model snapshot
review outcome
claim proposition
artifact version
lineage
stale 状态
```

具体持久化模型由 M11 文档定义，本文不规定表结构。

## 10. 验证状态

本地进程内验证：

```text
ruff check semantic_collaboration src/tests: PASS
mypy semantic_collaboration: PASS
semantic collaboration tests: 66 passed
full tests: 284 passed, 43 skipped
wheel package: 包含两份 SKILL.md
```

上述验证只使用测试替身，不等于：

```text
真实 LiteLLM 调用已验证
真实 Temporal workflow 已验证
端到端 reviewed claim graph 已验证
生产主路径已切换
```

## 11. 文档同步触发条件

以下变化发生时必须同步更新本文：

```text
Turn Intent / Claim Inventory 输出 schema 调整
标准化 SKILL.md 章节或模型可见策略调整
受限模板变量或语法边界调整
Root Plan 任务结构调整
claim 数量派生或 envelope 分配时序调整
M07 结构 verifier 边界调整
M08 Review 接入状态变化
M11 Artifact Store 接入状态变化
SemanticTaskExecutor 组合完成
真实 LiteLLM / Temporal / VetOrchestrator 联调完成
生产切换或回滚策略变化
```

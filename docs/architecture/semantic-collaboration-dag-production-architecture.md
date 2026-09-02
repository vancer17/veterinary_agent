<!--
=============================================================================
文件: semantic-collaboration-dag-production-architecture.md
作用: 定义受限语义协作 DAG 在生产工程实现中的稳定架构、任务边界、
      契约状态机、审查与局部重写治理、上下文策略和领域投影边界。
范围: 适用于用户原始表达到可投影结构化语义 claim graph 之间的生产主路径，
      包括受限任务规划、正交 SKILL、审查、局部修复、typed patch、
      artifact 版本、一致性门禁和问诊 / 临床安全 / 长期记忆投影契约。
说明: 本文是生产工程实现基线，固定 Temporal 作为 durable execution 边界，
      不包含实验计划，不展开软件包内部实现、提示词全文或测试替身实现。
维护: 当 SKILL 目录、Plan IR、TurnSnapshot、artifact 状态机、review / repair
      契约、上下文访问策略或领域投影边界调整时，必须同步更新本文。
=============================================================================
-->

# 受限语义协作 DAG 生产架构基线

> **文档状态**：生产工程实现基线
>
> **适用范围**：输入前置语义协作、窄域语义生成、结构化审查、局部重写、
> typed patch、claim graph 组装、质量门禁与领域投影契约
>
> **不适用范围**：临床安全医学准入内容、临床安全 OPA 策略细节、问诊状态存储实现、
> RAG 知识证据、自然语言最终回复生成、长期记忆写入策略、前端展示编排

## 1. 背景与架构结论

输入前置预处理 V8～V14 的结论是：

1. GLiNER / span-first candidate 路线与当前 claim 划分、论元角色和话语功能任务错配，
   不能作为权威语义事实来源。
2. 单体 one-pass full schema 虽然比 support-first 路线有更好的 claim 覆盖潜力，
   但同时输出 intent、claim inventory、statement semantics、participant、temporal、
   measurement、relation 和 canonical descriptor 会导致字段交替缺失和冷执行漂移。
3. 继续主要依赖 prompt 微调、整体 retry 或后处理补丁，不能收敛当前稳定性问题。
4. 完整当前回合上下文对 shared scope、指代、否定和时间绑定仍然必要，不能把子任务
   降级为只看局部 phrase 的碎片输入。

因此生产架构采用：

```text
contract-first
+ deterministic orchestration
+ 受限语义协作
+ 正交窄域 SKILL
+ 独立 verifier
+ 正交 review
+ deterministic repair planning
+ 局部 typed patch
+ 显式 disagreement / failure state
```

核心判断：

> 把一个高负载语义任务拆成多个边界清晰、可验证、可并行、可局部修复的小任务；
> 每个任务可以看到足够的全局只读上下文，但只能写自己的局部权威字段。

## 2. 目标与非目标

### 2.1 目标

1. 将用户当前回合显式语义稳定转换为可审计的结构化 claim graph。
2. 显式区分 `present`、`denied`、`reported_normal`、`uncertain`、`unknown`、`corrected`。
3. 支持 shared scope、多事实陈述、多轮指代、用户纠正和控制意图。
4. 为每个结构化结果保留 evidence binding 和上下文版本。
5. 通过正交 Review SKILL 发现 schema 无法判断的语义忠实性问题。
6. 通过局部 typed patch 修复可恢复错误，而不是整轮自由重写。
7. 让每个任务都有显式终态，失败不得表现为空事实。
8. 维护 artifact 版本、repair lineage 和下游 stale 关系。
9. 以独立领域投影 adapter 输出问诊、临床安全和长期记忆可消费契约。
10. 为生产观测、回归测试和排障提供稳定 metadata。

### 2.2 非目标

1. 不做医学诊断、治疗方案或临床风险判断。
2. 不产生 urgent / blocked 安全信号。
3. 不直接写问诊状态、宠物资料或长期记忆。
4. 不直接调用临床安全召回、`required_context` 或临床安全 OPA。
5. 不恢复硬关键词、正则或静态 seed 补抽路径。
6. 不使用宽松文本 JSON 检索或手工修复 JSON。
7. 不让 OPA 或 Python 扫描原始用户文本做医学判断。
8. 不把临床安全 `observed_features` 未经契约转换写入问诊状态。
9. 不通过放宽回答充分性策略掩盖上游语义失败。
10. 不引入无界审查、修复或 retry 循环。

## 3. 生产目标架构

```text
用户当前回合
→ TurnSnapshot Assembly
→ 受限任务规划 LLM
→ Plan IR
→ Plan Validator
→ Temporal SemanticDAGWorkflow
   ├─ Turn Intent Generator
   ├─ Claim Inventory Generator
   ├─ Claim Statement Semantics Generator
   ├─ Participant Phrase Generator
   ├─ Temporal Phrase Generator
   ├─ Measurement Phrase Generator
   └─ Canonical Descriptor Generator
→ Deterministic Verifier
→ 正交 Review SKILL
→ Review Verifier
→ Deterministic Repair Planner
→ 局部 Repair SKILL
→ Patch Verifier
→ Patch Applier
→ Artifact Store / Version
→ Claim Graph Assembly
→ Graph Consistency Gate
→ Domain Projection Adapter
```

架构要求：

1. 生成、审查、修复均是受限 SKILL 任务，不是自由 Agent 对话。
2. Plan IR 只能选择已注册 SKILL 和已声明依赖。
3. 全局上下文对任务只读，输出权限由 SKILL 契约限制。
4. Review 只诊断，不直接修改 artifact。
5. Repair 只输出 typed patch proposal。
6. patch 由 deterministic applier 校验并应用。
7. claim graph 在局部结果通过验证后组装。
8. 下游领域只能通过 adapter 消费 verified graph。
9. 任务队列、worker 租约、基础设施重试、超时与中断恢复由 Temporal 负责。
10. PostgreSQL 只保存语义终态投影，不保存 ready / running / attempt 调度状态。

## 4. 核心不变量

### 4.1 全局可读、局部可写

每个任务可以使用当前回合全文和有界历史上下文进行语义消歧，但只能输出自己
`owns` 声明的字段。

禁止：

```text
看到全局输入后新增其他领域的 claim
看到全局输入后输出 canonical_id
看到全局输入后输出临床风险
看到全局输入后直接改写其他任务的 artifact
```

### 4.2 一个字段只有一个权威来源

字段所有权由 SkillCatalog 全局校验。禁止两个 SKILL 同时拥有同一权威字段的
最终写入权。

### 4.3 Review 不改结果

Review SKILL 只能输出：

```text
review_verdict
failure_code
check result
repair_hint
confidence
```

不得直接输出 corrected artifact，也不得绕过 Repair SKILL 和 Patch Applier。

### 4.4 Repair 只做局部 patch

Repair SKILL 只能针对注册过的 failure code 和白名单 patch path 输出
`RepairPatchProposal`。

禁止：

```text
自由重写完整 schema
修复 forbidden field
补造无证据事实
修复一次失败后继续递归修复
```

### 4.5 失败必须有终态

任何任务不能以悬空状态结束。特别是：

```text
模型漏抽 ≠ 用户未提供
审查失败 ≠ 原任务通过
修复耗尽 ≠ 修复成功
canonical 无候选 ≠ 确认 canonical
上下文不足 ≠ unknown 事实
```

### 4.6 领域隔离

语义协作 DAG 不因下游需要而提前实现其他领域职责。若投影 adapter 尚未实现，
应保留显式 TODO 空壳并抛出：

```text
projection_adapter_not_implemented
```

不得在 preprocessing 中替问诊、临床安全或长期记忆实现业务逻辑。

### 4.7 Durable execution 边界

Temporal 是执行基础设施权威：

```text
activity 队列
worker 恢复
基础设施 retry
语义 retryable failure 的下一次 attempt
workflow / activity timeout
执行 event history
```

自有代码只保留：

```text
Plan IR 依赖推进
任务业务终态
语义 retryable failure code
artifact lineage
repair budget
claim graph 准入
```

禁止：

```text
数据库任务队列
数据库 worker 租约
数据库 attempt 调度状态
自研 worker 抢占与恢复协议
```

## 5. TurnSnapshot 与受限全局视图

### 5.1 契约

TurnSnapshot 是不可变上下文对象，至少包含：

```text
turn_id
turn_index
original_user_text
last_assistant_questions
verified_prior_fact_summary
trusted_pet_context
context_digest
snapshot_version
```

要求：

1. `original_user_text` 必须保留原文，不得被摘要替代。
2. 历史上下文必须有界，只允许进入上一轮追问和已验证事实摘要。
3. 宠物画像只作为可信上下文，不得被模型输出覆盖。
4. `context_digest` 必须贯穿 generator、reviewer 和 repairer。
5. Snapshot 一旦创建不得修改。

### 5.2 上下文访问矩阵

| 任务 | 当前回合全文 | 有界历史 | 宠物画像 | 其他任务输出 | 下游领域状态 |
|---|---:|---:|---:|---:|---:|
| 任务规划 LLM | 必需 | 按需 | 按需 | 禁止 | 禁止 |
| 生成 SKILL | 必需 | 按需 | 按需 | 禁止 | 禁止 |
| 局部 Review SKILL | 必需 | 按需 | 按需 | 只看被审查输出 | 禁止 |
| 局部 Repair SKILL | 必需 | 按需 | 按需 | 只看失败输出与 hint | 禁止 |
| Graph Consistency Review | 必需 | 按需 | 按需 | 只看已验证摘要 | 禁止 |

禁止进入 TurnSnapshot：

```text
问诊状态
临床安全召回结果
required_context 评估
临床安全 OPA 输入或输出
长期记忆
未验证同伴任务输出
```

上下文预算不足时必须输出：

```text
context_budget_exceeded
```

不得静默截断当前回合原文。

## 6. 受限任务规划与 Plan IR

### 6.1 规划器职责

任务规划 LLM 只能输出固定字段 `PlanSelection`：

```text
claim_envelope_count
run_statement_semantics
run_participant_phrase
run_temporal_phrase
run_measurement_phrase
run_canonical_descriptor
```

其中 `claim_envelope_count` 只是执行槽位估计，不是最终 claim 权威数量；最终
claim envelope 必须由后续 Claim Inventory verified artifact 校验或修复。

以下结构由 Deterministic Plan Compiler 根据 `PlanPolicy` 生成：

1. turn / claim envelope 与稳定标识。
2. 必选任务与被选择的语义 lane。
3. task_id 与 canonical dependency edge。
4. exact skill version 与 expected output schema reference。
5. turn / snapshot / catalog / policy digest 绑定。
6. canonical plan_id。

禁止：

```text
发明新 skill
发明新 task_type
输出任务图、任务引用或依赖
输出 skill version 或 schema
输出自由自然语言命令
声明未注册依赖
访问或调用下游领域
```

### 6.2 Plan IR

Plan IR 至少包含：

```text
plan_id
skill_catalog_digest
plan_policy_digest
turn_id
snapshot_digest
plan_version
tasks
dependencies
expected_outputs
```

每个 task 至少包含：

```text
task_id
skill_id
skill_version
target_envelope
depends_on
expected_output_schema
selection_source
```

### 6.3 Plan 校验

Plan Validator 必须校验：

```text
skill 已注册
skill version 已注册
task_id 唯一
依赖存在
依赖无环
envelope schema 合法
context policy 合法
expected output 与 SkillSpec 一致
未选择禁止 lane
PlanPolicy digest 合法
catalog digest 合法
plan_id 与 canonical 内容一致
```

失败状态：

```text
plan_schema_invalid
unknown_skill_selected
skill_version_invalid
dependency_cycle_detected
context_policy_violation
plan_budget_exceeded
mandatory_task_missing
forbidden_skill_selected
output_schema_mismatch
plan_id_invalid
```

规划失败不得触发硬编码默认任务。

## 7. SKILL 契约与正交性

### 7.1 权威契约

权威契约由机器可读的 SkillSpec、schema 和 verifier 承担，至少声明：

```text
skill_id
version
task_type
input contract
output contract
owns
does_not_own
forbidden_output
context requirements
verifier bindings
failure policy
repair mappings
```

`SKILL.md` 可以作为面向 LLM 的多段说明，但只是契约的提示词投影，不是运行时
权威来源。运行时不得解析 Markdown 正文来决定字段所有权。

### 7.2 正交粒度

正交粒度是语义权威域，不是业务槽位或症状词。

正确划分：

```text
turn intent
claim inventory
statement semantics
participant phrase
temporal phrase
measurement phrase
canonical descriptor
```

错误划分：

```text
呕吐抽取 skill
软便抽取 skill
食欲抽取 skill
换粮抽取 skill
```

后者会退化成模型版关键词状态机。

### 7.3 所有权矩阵

| SKILL | 权威输出 | 明确不拥有 |
|---|---|---|
| Turn Intent | fixed-field intent 与 intent evidence | 医学事实、claim 语义 |
| Claim Inventory | claim envelope、ordinal、parent scope | assertion state、canonical |
| Statement Semantics | statement_type、assertion_state、certainty、scope | participant、temporal、canonical |
| Participant Phrase | subject / agent / recipient / object phrase | entity_id、诊断、风险 |
| Temporal Phrase | temporal phrase 与 claim binding | normalized temporal authority |
| Measurement Phrase | measurement phrase 与 claim binding | normalized measurement authority |
| Canonical Descriptor | descriptor、target query、claim binding | canonical_id、医学结论 |
| Review SKILL | verdict、failure_code、repair_hint | 被审查字段最终权威 |
| Repair SKILL | typed patch proposal | artifact 直接应用权 |
| Patch Applier | artifact 新版本 | 语义猜测 |

## 8. 生成任务契约

### 8.1 Turn Intent

输出 fixed-field intent：

```text
answer_now
wants_triage
correction
clarification_request
fact_statement_present
question_present
report_context_present
```

要求：

1. 每个信号在当前回合最多输出一次。
2. intent 必须绑定 evidence phrase。
3. `answer_now` 是控制意图，不是医学事实。
4. 不按 claim 重复输出 `fact_statement_present`。

### 8.2 Claim Inventory

输出可独立审计的候选事实单元：

```text
claim_id
ordinal
evidence_phrase
parent_scope
shared_scope_hint
unit_type
```

职责：

1. 拆分多事实输入。
2. 保留 shared scope 的父子关系。
3. 为下游任务提供稳定 envelope。

禁止：

```text
判断 normal / denied
输出 canonical_id
合并多个 target 为一个粗 claim
丢失否定、时间或主体范围
```

示例：

```text
饭和水都正常
```

应表达为一个 parent scope 下的两个 claim envelope：

```text
食欲正常
饮水正常
```

### 8.3 Claim Statement Semantics

对单个 claim 或极小批量 claim 输出：

```text
statement_type
assertion_state
certainty
scope
```

`assertion_state` 必须区分：

| 状态 | 语义 |
|---|---|
| `present` | 用户报告现象存在 |
| `denied` | 用户明确否认现象存在 |
| `reported_normal` | 用户明确报告状态正常 |
| `uncertain` | 用户不确定或证据不足 |
| `corrected` | 用户纠正先前信息 |
| `unknown` | 当前证据无法建立该事实 |

禁止把 `精神正常` 表示为 `denied`。

### 8.4 Participant Phrase

LLM 只输出 phrase：

```text
subject_phrase
agent_phrase
recipient_phrase
object_phrase
```

实体绑定由 deterministic candidate-only resolver 完成：

```text
唯一候选 → resolved
无候选 → not_found
多候选 → ambiguous
模型发明 entity_id → blocked
```

### 8.5 Temporal / Measurement Phrase

LLM 只输出 phrase 和 claim binding。归一化权威属于 deterministic parser / verifier：

```text
temporal parser
measurement parser
unit policy
```

禁止模型自由输出不可验证的 normalized value 并伪装为 verified。

### 8.6 Canonical Descriptor

LLM 只输出：

```text
descriptor
target query
claim binding
```

canonical 绑定必须 candidate-only：

```text
无候选 → not_found
候选模糊 → ambiguous
模型发明 canonical_id → blocked
```

## 9. Deterministic Verifier

每个生成任务输出必须经过 verifier，至少包括：

```text
schema check
enum check
extra field check
field ownership check
target existence check
claim binding check
evidence phrase check
parent scope check
context digest check
cross-field consistency check
```

Verifier 职责是验证结构和证据，不做医学判断。

典型失败：

```text
schema_invalid
forbidden_field_present
field_ownership_violation
evidence_not_found
evidence_outside_parent_scope
evidence_ambiguous
claim_binding_invalid
context_digest_mismatch
semantic_conflict
```

禁止：

```text
静默删除 forbidden field 后继续
宽松解析非法 JSON
把模型 proposal 标记为 verified
用关键词规则补造语义
```

## 10. 正交 Review SKILL

### 10.1 拆分原则

Review SKILL 必须按任务域拆分，避免一次调用审查所有输出：

```text
review.turn_intent
review.claim_inventory
review.claim_statement_semantics
review.participant_phrase
review.temporal_phrase
review.measurement_phrase
review.canonical_descriptor
review.graph_consistency
```

单次审查必须限制目标数量。Statement semantics review 推荐一次只审查一个 claim；
如必须批量，应设置小上限并按 claim_id 一一对应。

### 10.2 输入

局部 Review SKILL 输入：

```text
同一 TurnSnapshot
target envelope
candidate output
被审查 SKILL 契约
必要的候选集或 parser 结果
```

Review 必须与 generator 使用同一 `context_digest`。

### 10.3 输出

```json
{
  "review_id": "string",
  "review_skill_id": "string",
  "target_task_id": "string",
  "target_artifact_id": "string",
  "reviewed_skill_id": "string",
  "reviewed_skill_version": "string",
  "verdict": "approved | rejected | needs_repair | inconclusive",
  "failure_code": "string | null",
  "checks": "object",
  "repair_hint": "object | null",
  "confidence": "number"
}
```

### 10.4 审查边界

Review 可以判断：

```text
输出是否忠实于证据
normal / denied / present / uncertain 是否误用
shared scope 是否漏拆
participant phrase 是否缺失或越界
temporal / measurement binding 是否错误
canonical candidate 是否支持选择
intent 与 claim 是否冲突
```

Review 禁止：

```text
直接修改 artifact
输出最终 corrected assertion
输出诊断或临床风险
读取下游领域状态
把审查失败当作原任务通过
```

### 10.5 Review 终态

```text
review_verified
review_failed
review_target_missing
review_schema_invalid
review_scope_violation
review_disagreement
review_timeout
review_inconclusive
```

当审查是必需门槛时，`review_failed` 不得让原 artifact 进入 verified 状态。

## 11. 局部重写与 typed patch

### 11.1 Repair Planner

Repair Planner 是 deterministic 组件，根据注册的 failure code 创建修复任务。
禁止由 Review LLM 直接决定自由修复。

映射示例：

| failure code | 允许的 Repair SKILL |
|---|---|
| `assertion_state_semantic_mismatch` | `repair.claim.assertion_semantics` |
| `shared_scope_under_split` | `repair.claim_inventory.shared_scope` |
| `evidence_phrase_boundary_invalid` | `repair.evidence.claim_phrase` |
| `participant_role_phrase_missing` | `repair.participant.role_phrase` |
| `temporal_phrase_missing` | `repair.temporal.claim_phrase` |
| `measurement_phrase_missing` | `repair.measurement.claim_phrase` |
| `canonical_descriptor_recall_miss` | `repair.canonical.descriptor_query` |

未注册 failure code 输出 `repair_unavailable`。

### 11.2 RepairPatchProposal

```json
{
  "patch_id": "string",
  "repair_skill_id": "string",
  "target_task_id": "string",
  "target_artifact_id": "string",
  "base_version": "number",
  "failure_code": "string",
  "operations": "array",
  "evidence_binding": "object",
  "reason_code": "string"
}
```

### 11.3 Patch 应用校验

Patch Applier 必须校验：

```text
target artifact 存在
base_version 一致
repair skill 已注册
failure code 匹配
patch path 在白名单
patch value 符合 schema
未修改 forbidden path
无并行 patch 冲突
evidence binding 存在
repair budget 未超限
```

通过后应用 patch、递增 artifact version 并记录 repair lineage；
失败时输出 `patch_rejected`，不得静默修正 patch。

### 11.4 修复边界

可修复：

```text
断言语义误判
evidence phrase 边界不准
claim shared scope 漏拆
participant phrase 缺失
temporal / measurement phrase 缺失或绑定错误
canonical descriptor 查询表达不当
```

不可修复：

```text
forbidden field 出现
schema 根本非法
原文无证据
候选模糊
canonical 无候选
临床风险或安全动作越权
下游 adapter 未实现
```

### 11.5 修复预算

生产默认约束：

```text
repair_depth = 1
不允许 repair of repair
每个字段最多一次修复
每个 claim 的修复任务数量有限
每轮全局 repair budget 固定
必须有全局 deadline
```

超过预算输出 `repair_exhausted`。这是合法终态，不是可静默吞掉的失败。

## 12. Artifact 状态机与 DAG 闭环

### 12.1 任务终态

每个任务必须有显式终态：

```text
verified
repair_verified
not_applicable
blocked
disagreement
repair_exhausted
repair_failed
dependency_failed
review_failed
context_budget_exceeded
timeout
```

### 12.2 空结果语义

空结果必须区分：

```text
no_explicit_fact
suspicious_empty
model_returned_empty
low_confidence
schema_invalid
extraction_failed
dependency_failed
review_failed
```

禁止把系统失败静默转换为 `facts=[]`、`unknown` 或 `not_found`。

### 12.3 disagreement

生成结果与审查结果不一致时，不得默认任一方正确。必须保留：

```text
field
candidate values
generator confidence
review confidence
evidence binding
```

终态为 `disagreement`。除非存在显式 adjudicator 契约，否则不得自动裁决。

### 12.4 下游 stale

上游 artifact 修复导致结构变化时，必须标记下游 stale。

示例：

```text
claim inventory 修复
→ 原 claim 拆分
→ 原 statement semantics / participant / temporal / canonical 输出 stale
→ 相关下游任务重新执行
```

禁止把旧 claim 的语义结果直接迁移到新 claim，或修复上游后直接投影下游。

## 13. Claim Graph 与一致性门禁

### 13.1 组装

Claim graph 由 deterministic orchestrator 根据 verified artifact 组装：

```text
claim envelope
statement semantics
participant binding
temporal binding
measurement binding
canonical binding
intent summary
relation / graph edges
```

LLM 不直接输出最终完整图。

### 13.2 Graph Consistency Gate

优先 deterministic 检查：

```text
ID 引用存在
依赖完整
claim 无重复
binding 唯一
枚举不冲突
field ownership 不冲突
```

需要语义判断的冲突可交给受限 graph consistency reviewer，例如：

```text
intent 与 claim 冲突
时间范围与断言 scope 冲突
participant 与关系需求冲突
canonical polarity 与 assertion state 冲突
```

图级终态：

```text
graph_verified
graph_partial_with_gaps
graph_disagreement
graph_blocked
```

## 14. 领域投影

### 14.1 投影原则

语义协作 DAG 只输出 verified graph 和显式 gap / disagreement。

下游消费必须通过 adapter：

```text
ConsultationProjectionAdapter
ClinicalSafetyProjectionAdapter
LongTermMemoryProjectionAdapter
```

每个 adapter 必须声明：

```text
接受的 claim type
忽略的 claim type
normal / denied / uncertain 映射
冲突处理策略
必需字段
禁止字段
失败状态
```

### 14.2 问诊投影

可消费：

```text
控制意图
起病时间
当前食物 / 换粮
大便形态
精神状态
食欲状态
饮水状态
呕吐否认
血便否认
用户纠正
```

禁止：

```text
把 reported_normal 映射为 denied
把 unknown 映射为追问已完成
在 adapter 内做医学风险判断
```

### 14.3 临床安全投影

临床安全投影只提供其声明允许的结构化事实，不产生：

```text
urgent signal
blocked signal
临床动作
诊断
```

临床安全仍由既有链路独立完成：

```text
临床安全语义
候选召回
required_context 评估
临床安全 OPA
```

### 14.4 长期记忆投影

长期记忆投影只输出候选，不直接写入长期事实。写入仍由独立候选抽取和策略裁决负责。

## 15. 可观测性

每次任务至少记录：

```text
turn_id
snapshot_digest
plan_id
task_id
skill_id
skill_version
prompt_hash
model snapshot
input envelope digest
artifact id / version
review verdict
failure code
repair lineage
latency
token usage
terminal state
```

关键指标：

```text
schema invalid rate
forbidden field blocked rate
evidence mismatch rate
review disagreement rate
repair required rate
repair success rate
repair regression rate
repair exhausted rate
context budget exceeded rate
suspicious empty rate
graph partial rate
cross-agent inconsistency rate
terminal state distribution
```

## 16. 生产工程交付顺序

以下顺序是工程依赖顺序，不是实验计划。

### 阶段 A：契约与目录

交付 `SkillSpec`、`SkillCatalog`、所有权校验、context policy、failure code 目录和
repair mapping 目录。

验收：重复字段所有权、forbidden 与 owns 冲突、缺失 verifier 均在启动时失败。

### 阶段 B：TurnSnapshot 与 Plan IR

交付 TurnSnapshot、context digest、Plan IR 和 Plan Validator。

验收：未知 skill、非法依赖和上下文越权全部 blocked。

### 阶段 C：生成 SKILL 与 verifier

交付 intent、claim inventory、statement semantics、participant / temporal /
measurement / canonical phrase 和 strict verifier。

验收：所有输出 strict schema；forbidden field 不被清洗后放行；evidence /
binding 失败显式 blocked。

### 阶段 D：Review 与 Repair

交付正交 Review SKILL、review verifier、deterministic repair planner、typed patch、
patch verifier 和 applier。

验收：review 不直接修改 artifact；repair 只能修改白名单 path；修复预算和终态有效。

### 阶段 E：Artifact 与 Claim Graph

交付 artifact store、版本管理、repair lineage、stale 标记、graph assembly 和
consistency gate。

验收：每个任务有终态；上游变化触发下游 stale；graph 只消费 verified artifact。

### 阶段 F：领域投影与生产接入

交付问诊投影 adapter、临床安全投影 adapter、长期记忆候选投影 adapter 和
orchestrator 接入边界。

验收：preprocessing 不直接写领域状态；adapter 未实现时显式失败；安全裁决仍由
既有领域链路负责。

## 17. 防退化测试要求

生产测试至少覆盖：

```text
SkillCatalog 所有权冲突
Plan IR 非法 skill / 依赖环 / 上下文越权
strict schema extra field 拒绝
forbidden field blocked
normal / denied / uncertain / unknown 语义回归
shared scope 拆分
多轮指代与 answer_now
evidence outside scope 拒绝
review 输出越权拒绝
repair patch 越权拒绝
base version 冲突拒绝
repair budget 耗尽终态
上游修复后的下游 stale
graph consistency 冲突
领域 adapter 隔离
失败不得转换为空 facts
```

禁止使用：

```text
按医学症状词组织的正向测试全集
以关键词命中作为验收标准
以 retry 后结果冒充单次稳定结果
```

## 18. 文档与实现同步

以下内容变化时必须同步更新本文：

```text
Skill 目录
字段所有权
Plan IR schema
TurnSnapshot 契约
artifact 状态机
review 状态机
repair patch 契约
context policy
领域投影边界
durable execution 边界
生产验收口径
```

代码、manifest、测试或 `SKILL.md` 与本文冲突时，必须通过显式架构变更同步，
不得留下双权威解释。

## 19. 关联材料

1. [semantic-collaboration-dag-production-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-implementation-plan.md)
2. [agent-input-preprocessing-domain-extraction-migration-plan.md](/home/vancer17/veterinary_agent/docs/architecture/agent-input-preprocessing-domain-extraction-migration-plan.md)
3. [input-preprocessing-v13-llm-first-structured-claim-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v13-llm-first-structured-claim-change-summary.md)
4. [input-preprocessing-v14-onepass-governance-convergence-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v14-onepass-governance-convergence-change-summary.md)
5. [consultation-semantic-extraction-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-semantic-extraction-change-summary.md)
6. [consultation-state-answerability-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-state-answerability-change-summary.md)
7. [clinical-safety-semantic-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-semantic-change-summary.md)
8. [semantic-collaboration-dag-m04-scheduler-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m04-scheduler-change-summary.md)

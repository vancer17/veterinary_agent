<!--
=============================================================================
文件: agent-input-preprocessing-domain-extraction-migration-plan.md
作用: 保留 V14 one-pass LLM-first Structured Claim 治理收敛路线的历史结论、
      阶段记录和当时完成判定，作为受限语义协作 DAG 的背景材料。
范围: 覆盖 fixed-field turn intent、approximate one-pass claim generation、
      claim-local fuzzy alignment、语义 proposal 治理、TurnContext 实体解析、
      constrained canonical linking、post-hoc claim graph、minimal lane 成本、
      异步 shadow 和渐进消费评估。
说明: 本文不再是当前生产架构权威；V8～V12 candidate-first 路线和 V13
      two-stage 对照仅保留为历史对照。
维护: 本文转为历史记录后，仅在实际修正历史事实或关联材料时更新。
=============================================================================
-->

# Agent 输入前置预处理与领域抽取层迁移方案

> **文档状态**：V14 one-pass governance convergence 待执行；未达到生产消费准入
>
> **架构状态**：生产工程实现基线已转为
> [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)；
> 本文仅保留 V8～V14 历史结论和 one-pass 治理收敛背景，不再作为当前生产架构权威。
>
> **适用范围**：用户原始表达到结构化 UserClaim / claim graph 之间的输入语义链路
>
> **不适用范围**：临床安全医学准入内容、OPA 策略细节、问诊状态存储实现、RAG 知识证据、自然语言最终回复生成、长期记忆写入策略、前端展示编排

## 1. 当前定位

V2～V13 的有效结论是：

1. 不能要求 LLM 精确复制 quote、输出 offset 或绑定 span_id。
2. GLiNER candidate-first 与当前论元角色 / 话语功能任务错配。
3. V13 approximate one-pass 显著优于 V12 support-first structural seed。
4. One-pass 优于 two-stage，因为完整原文上下文对 shared scope 和论元结构不可缺。
5. Frozen relation、canonical direct recall、TurnContext resolver、temporal / measurement parser 在 gold 输入下可用，应后置为 verifier / enricher。
6. 当前 blocker 是 V13 输出稳定性、fixed-field intent、field false alignment、participant binding、canonical 假确认、temporal conflict 和 minimal lane 成本。

因此当前主线为：

```text
LLM-first one-pass approximate claim generation
+ deterministic source-grounded alignment
+ field-level governance
+ targeted enrichment
+ post-hoc claim graph
```

历史路线：

```text
GLiNER candidate pool
→ SpanGraph
→ support anchor
→ role menu
→ structural seed
→ Macro span binding
```

仅作为历史对照，不再继续加厚。

## 2. 目标与非目标

### 2.1 目标

1. 用 fixed-field intent 消灭 `fact_statement` 重复。
2. 用 one-pass claim inventory 和 shared-scope skill 提升 claim 稳定性。
3. 用 claim-local fuzzy alignment 降低 false alignment。
4. 用 TurnContext candidate-only resolver 修复 participant role。
5. 用 parser verifier 治理 temporal / measurement proposal。
6. 用 constrained selector 治理 canonical descriptor。
7. 在治理完成后构建 post-hoc claim graph。
8. 建立 minimal lane 的调用、延迟、token 和成本口径。
9. 保持 report-only、异步 shadow、fresh held-out 和 DSPy 防护。

### 2.2 非目标

1. 不重做 V8～V12 GLiNER 后处理路线。
2. 不恢复 support anchor / structural seed / role menu 前置。
3. 不把 fuzzy matching 变成无审计的自由接受。
4. 不让 LLM 输出 entity_id 或 canonical_id。
5. 不把 model_proposed 伪装成 verified。
6. 不接入生产问诊状态或临床安全 evaluator / OPA。
7. 不读取 held-out。
8. 不启用 DSPy。
9. 不引入无界修复循环。

## 3. 目标架构

```text
TurnContext Assembly
→ Fixed-field Turn Intent Analyzer
→ One-pass LLM Flat Claim Generation
   └─ claim inventory / shared scope constraints
→ Claim-local Deterministic Fuzzy Alignment
→ Field-level Governance
→ Targeted Enrichment
   ├─ TurnContext participant resolver
   ├─ temporal parser verifier
   ├─ measurement parser verifier
   ├─ relation classifier
   └─ canonical constrained selector
→ Post-hoc Claim Graph
→ Quality Gates / Review
→ Report-only Projection
→ Trace / Metrics / Minimal Lane Cost
```

### 3.1 分层职责

| 层 | 职责 | 禁止事项 |
|---|---|---|
| TurnContext | 提供可信 user / pet / actor / time / session / previous question | 不让模型发明或覆盖可信实体 |
| Fixed-field Intent | 识别 dialogue acts 与 input properties | 不把 fact statement 按 claim 重复输出 |
| One-pass Claim Generation | 生成 claim inventory 和 flat claim record | 不输出 ID、offset、自由 canonical_id |
| Evidence Alignment | 将 approximate phrase 落回原文 | 不用模型 phrase 替代原文 |
| Semantic Governance | 验证 statement、polarity、modality、temporal、measurement | 不把 proposal 伪装 verified |
| Entity / Canonical | TurnContext resolver 与候选内 canonical selector | 不自由发明 entity / canonical |
| Claim Graph | 治理后组织 claim、证据、主体、时间和冲突 | 不作为 LLM 前置笼子 |
| Projection | report-only 问诊 / 临床安全结构投影 | 不写业务状态，不触发 evaluator / OPA |
| Minimal Lane | 测量最小路径成本 | 不把实验矩阵耗时当生产路径耗时 |

## 4. Fixed-field Turn Intent

意图输出使用固定字段：

```text
answer_now
wants_triage
correction
clarification_request

fact_statement_present
question_present
report_context_present
```

其中：

```text
dialogue acts:
  answer_now / wants_triage / correction / clarification_request

input properties:
  fact_statement_present / question_present / report_context_present
```

`fact_statement_present` 是 turn-level 属性，不随 claim 数量重复。完整事实证据由 claim records 承担。

Claim generation 后必须对账：

```text
fact_statement_present 是否与 governed_claim_count 一致
```

不一致时：

```text
intent_claim_mismatch
review_required
```

## 5. One-pass Flat Claim Generation

### 5.1 输出形态

One-pass 输出包含：

```text
claim_inventory
claims
```

Claim record 字段：

```text
inventory_ordinal
claim_type

evidence_phrase
target_phrase
relation_phrase

subject_phrase
action_agent_phrase
action_recipient_phrase
object_phrase

user_statement_type
polarity
modality_type
modality_strength
epistemic_status

temporal_phrase
temporal_relation
temporal_value
temporal_precision

measurement_phrase
measurement_value
measurement_unit
measurement_relation

canonical_descriptor

confidence
needs_review
missing_field_reason
```

禁止输出：

```text
span_id
start
end
entity_id
canonical_id
selected_candidate_id
```

### 5.2 Approximate phrase policy

Phrase 是 semantic proposal：

```text
不要求逐字复制原文；
不要求连续；
不得引入原文没有的信息；
不得丢失否定、时间、主体、数量或关键关系；
能逐字复制时优先逐字复制。
```

最终 evidence 只能来自 deterministic aligner 反查原文。

### 5.3 Shared scope

共享断言必须逐项展开：

```text
没有呕吐、干呕、反流
→ vomiting denied
→ retching denied
→ regurgitation denied
```

共享范围必须继承：

```text
relation phrase
polarity
modality
epistemic status
subject context
```

不得只输出第一个 target，也不得合并为一个粗 claim。

## 6. Claim-local Fuzzy Alignment

### 6.1 流程

```text
1. 对齐 claim evidence phrase；
2. 建立 claim evidence envelope；
3. 字段 phrase 默认只在 envelope 内搜索；
4. fuzzy result 执行 field-specific verifier；
5. ambiguous / not found 显式 review 或 blocked。
```

例外：

```text
previous question target；
TurnContext owner occurrence；
显式省略主体；
```

必须记录：

```text
alignment_scope = outside_parent
resolution_method
```

### 6.2 状态

```text
exact
exact_normalized
fuzzy_verified
fuzzy_ambiguous
fuzzy_not_found
wrong_occurrence
outside_parent
cross_source_block
semantic_mismatch
negation_lost
temporal_lost
subject_lost
empty_phrase
```

### 6.3 接受策略

```text
exact / exact_normalized:
  pass

unique fuzzy:
  fuzzy_verified only after verifier

ambiguous:
  review

not found:
  blocked / review
```

不得使用：

```text
编辑距离修复 quote；
embedding 改写 quote；
LLM 重写 quote；
unrestricted fuzzy acceptance。
```

## 7. 语义 Proposal 治理

### 7.1 Statement semantics

必须分离：

```text
user_statement_type
polarity
modality
epistemic_status
```

特别区分：

```text
denies
reports_normal
reports_abnormal
no_change
historical
hypothetical
uncertain
```

### 7.2 Temporal / measurement

LLM proposal 状态：

```text
model_proposed
verified
parser_conflict
unresolved
```

Parser verifier 是 verified 数值的权威来源。

模糊表达不得被静默精确化。

## 8. 实体与 Canonical 后置富化

### 8.1 TurnContext resolver

LLM 输出 phrase，代码解析：

```text
subject_phrase → subject entity
action_agent_phrase → agent entity
action_recipient_phrase → recipient entity
object_phrase → object mention
```

规则：

```text
候选来自 TurnContext；
resolved 不能为空；
ambiguous 必须有候选；
多宠物不得默认 current_pet；
entity type 必须与 role 兼容。
```

### 8.2 Canonical constrained selector

流程：

```text
target_phrase + canonical_descriptor
→ candidate retriever
→ candidates[]
→ constrained selector
→ selected_candidate_id
→ code resolves canonical_id
```

无候选：

```text
not_found
review_required
new_concept_request
```

Descriptor 只用于召回，不得直接确认。

## 9. Post-hoc Claim Graph

Claim graph 在以下治理完成后构建：

```text
evidence alignment；
subject resolution；
semantic proposal governance；
canonical linking；
```

节点：

```text
UserClaim
AlignedEvidence
Subject
Participant
TemporalProposal
MeasurementProposal
CanonicalMapping
DiscourseAct
```

用途：

```text
field lineage；
冲突解释；
review routing；
claim state governance；
后续投影准备。
```

## 10. Minimal lane 与异步 shadow

Minimal lane 默认只包含：

```text
Turn Intent Analyzer
One-pass claim generation
```

Targeted verifier 仅在冲突或歧义时触发。

必须单独记录：

```text
model_call_count
stage latency
p50 / p95
token usage
cost
```

API metadata shadow 仍需满足：

```text
异步 worker；
采样；
限流；
阶段超时；
熔断；
有界队列；
死信 / review；
trace 持久化；
主链路零影响；
```

## 11. 阶段迁移

### 阶段 0：执行与观测修复

交付：

```text
usage / response metadata；
raw attempt 与 production retry 分离；
generation option audit；
minimal lane 延迟口径。
```

验收：

```text
token / cost 可观测或显式 unsupported；
完整实验矩阵耗时不再混入 minimal lane。
```

### 阶段 1：Fixed-field intent

交付：

```text
V14TurnIntentRaw；
intent / claim reconciliation；
INTENT-SPLIT 实验。
```

验收：

```text
fact_statement_duplicate_count = 0；
answer_now / fact / question 可并行识别。
```

### 阶段 2：One-pass 稳定性

交付：

```text
claim inventory；
shared scope skill；
null semantics skill；
最小 generation option 对照。
```

验收：

```text
claim quality 下限提升；
blocked count 波动收窄；
输出不是 stable-but-wrong。
```

### 阶段 3：Claim-local alignment

交付：

```text
claim evidence envelope；
field-local search；
occurrence disambiguation；
fuzzy verifier。
```

验收：

```text
false alignment 低于 V13；
ambiguous / not found 显式 review。
```

### 阶段 4：Participant 与语义 proposal 治理

交付：

```text
TurnContext candidate-only resolver；
participant role compatibility；
temporal / measurement parser verifier。
```

验收：

```text
participant 不发明实体；
resolved-empty 为 0；
model_proposed 不伪装 verified。
```

### 阶段 5：Canonical 与 claim graph

交付：

```text
dual query candidate recall；
candidate-only selector；
post-hoc claim graph。
```

验收：

```text
confirmed_without_candidates = 0；
false confirmation 显著下降；
claim graph 只消费 governed claims。
```

### 阶段 6：Minimal lane 与 REP

交付：

```text
minimal lane；
全 representative units 三次冷调用；
NEG / ASYNC 回归。
```

验收：

```text
minimal lane 成本可观测；
REP stable-and-correct 提升；
负例全部阻断。
```

### 阶段 7：Fresh held-out

只有 development winner 冻结后执行。

冻结：

```text
model
model snapshot
prompt
skill
schema
aligner
parser
policy
vocabulary
gate
fixture
```

## 12. 完成判定

V14 迁移探索完成时，应能明确回答：

```text
1. fixed-field intent 是否消灭重复 fact_statement；
2. claim inventory / shared scope 是否提升 one-pass 下限；
3. claim-local alignment 是否降低 false alignment；
4. participant 是否不发明实体且错绑可控；
5. temporal / measurement proposal 是否可治理；
6. canonical descriptor 是否降低 under-confirmation 且不引入假确认；
7. minimal lane 成本是否可接受；
8. 全 representative REP 是否 3/3 stable-and-correct；
9. fresh held-out 是否通过；
10. 安全边界是否保持 report-only。
```

在这些条件满足前，不允许：

```text
生产消费问诊事实；
生产消费 clinical safety projection；
接入 ClinicalSafetyEvaluator；
接入 VetOrchestrator；
解除 V8 live gate；
读取 held-out；
启用 DSPy。
```

## 13. 关联材料

1. [agent-input-preprocessing-shadow-experiment-architecture-guidance.md](/home/vancer17/veterinary_agent/docs/architecture/agent-input-preprocessing-shadow-experiment-architecture-guidance.md)
2. [input-preprocessing-v14-onepass-governance-convergence-experiment-plan.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v14-onepass-governance-convergence-experiment-plan.md)
3. [input-preprocessing-v13-llm-first-structured-claim-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v13-llm-first-structured-claim-change-summary.md)
4. [input-preprocessing-v12-support-first-graph-ranking-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v12-support-first-graph-ranking-change-summary.md)
5. [input-preprocessing-v11-candidate-view-reranking-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v11-candidate-view-reranking-change-summary.md)
6. [input-preprocessing-v10-shadow-runner-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v10-shadow-runner-change-summary.md)
7. [input-preprocessing-v9-attribution-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v9-attribution-change-summary.md)
8. [input-preprocessing-v8-shadow-runner-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v8-shadow-runner-change-summary.md)

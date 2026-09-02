<!--
=============================================================================
文件: input-preprocessing-v11-candidate-view-reranking-experiment-plan.md
作用: 定义 V11 candidate reranking、role-specific view、claim skeleton seeds、
      macro 语义修复、relation 冷启动和 early-exit 的专项实验设计。
范围: 覆盖 V10 candidate snapshot、cross-encoder rerank、candidate budget、
      span graph / claim-local view、deterministic seeds、statement verifier、
      relation fixed contract、winner regression、REP / NEG / held-out 防护。
说明: 本文是 V11 可执行实验计划；不改变生产问诊、临床安全召回、required_context
      或 OPA 裁决，不解除 V8 live phase gate。
维护: 当 V11 契约、实验矩阵、权威报告或准入条件变化时同步更新。
=============================================================================
-->

# Input preprocessing V11 candidate view 与 reranking 实验计划

> **文档状态**：待执行的 V11 专项归因实验计划
>
> **目标**：修复 V10 暴露的 candidate overload、ranking、role coverage、macro skeleton / statement type 漂移和 relation 冷启动不确定性。
>
> **硬边界**：所有报告默认 `diagnostic_only=true`、`can_unblock_v8_phase=false`；held-out 与 DSPy 保持冻结。

## 1. V10 问题分解

| V10 blocker | 目标归因 |
|---|---|
| Candidate count 621、precision 0.124 | Cross-encoder role-conditioned ranking 是否能把正确候选排到 top-k |
| Role coverage 0.512、label accuracy 0.268 | Role eligibility / claim-local view 是否提升可用候选覆盖率 |
| Macro claim precision / statement type 0.344 | Deterministic seeds + local verifier 是否修复骨架和言语行为 |
| role-ineligible binding 2 | Role-specific menu 是否能物理阻断跨角色绑定 |
| Relation 仅 development 单次通过 | 固定契约是否能三次冷启动稳定 |
| REP 2/3 | 固定 view / seed / deterministic ID 是否提升 signature 稳定性 |
| Downstream availability 依赖 macro | Live macro target / mention 可用时 winner 是否可回归 |

## 2. 实验总原则

1. 一个实验只回答一个主问题。
2. 先冻结 V10 candidate snapshot，再评估 reranker。
3. 先证明 view coverage，再交给 Macro。
4. 先证明 seed recall，再评估 statement type。
5. development 单次通过不等于 winner。
6. cache replay 不计入冷启动证据。
7. held-out 不参与探索期调参。
8. 不使用医学词典、关键词规则或动态 calibration。
9. 模型不得输出自由 quote。
10. 所有失败输出 attribution code，而不是解释为用户未提供事实。

## 3. Phase 0：Snapshot 与 view 基线

### 3.1 SNAP-INTEGRITY

#### 目标

固化 V10 candidate pool，排除 GLiNER / calibration 漂移对 V11 的干扰。

#### 输入

```text
V10 boundary calibration authoritative snapshot
explicit-offset development fixture
```

#### 要求

1. 快照保留 candidate id、start/end、text、label candidates、source extractor、score。
2. 不重新执行 GLiNER。
3. 快照 SHA256 固定。
4. V10 baseline 指标可复现。

#### 指标

```text
candidate_count
exact_field_recall
boundary_precision
role_coverage
near_or_exact
offset_valid_rate
text_match_rate
```

#### 退出

```text
candidate_count ≈ V10；
exact_field_recall 不低于 0.6585；
offset_valid_rate = 1.0。
```

### 3.2 VIEW-COVERAGE

#### 目标

验证 role / claim-local candidate view 是否保留 gold candidate。

#### 对照组

```text
global candidate pool
```

#### 实验组

```text
role-specific primary view
role-specific primary + fallback view
claim-local role view
```

#### 指标

```text
gold_in_view_rate
empty_role_menu_rate
primary_gold_rate
fallback_gold_rate
false_pruned_candidate_rate
view_candidate_count
```

#### 退出

```text
gold_in_view_rate 不低于 global pool；
empty menu 全部有 reason；
view candidate count 显著下降。
```

## 4. Phase 1：Reranking 与 candidate budget

### 4.1 RANK-CROSS

#### 目标

验证 cross-encoder 是否能按角色优先排序正确 span。

#### Query 设计

```text
target query：当前 claim region 的核心目标对象 mention
relation query：否定、变化、比较或状态关系表达
temporal query：时间、起点、持续或频率表达
measurement query：数量、单位、频率或量度表达
participant query：动作执行者、承受者、体验者或对象 mention
```

#### Document 设计

```text
claim-local raw context
highlighted candidate span
candidate label / parser provenance
```

#### 候选 adapter

```text
BGE reranker
local HuggingFace cross-encoder
TEI reranker
等价中文 reranker
```

#### 禁止

```text
reranker 生成 span
修改 offset / text
输出 canonical
判断医学事实
```

#### 指标

```text
precision@1 / @3 / @5 / @16
recall@1 / @3 / @5 / @16
gold_in_top_1 / top_3 / top_5
role_coverage@k
candidate_count@k
latency
cost
score calibration
```

#### 退出

```text
候选数显著下降；
exact recall 不低于 0.6585；
gold-in-top-k 显著提升；
role coverage 高于 0.512；
latency 可接受。
```

### 4.2 RANK-BUDGET

#### 目标

找到 candidate count、recall、precision 和 Macro binding 的 Pareto 点。

#### 变量

```text
per-role top-k: 2 / 3 / 5 / 8
per-turn cap: 32 / 48 / 64 / 96 / 128 / 192
per-region cap
```

#### 指标

```text
candidate_count
exact_recall
precision
gold_in_view
macro_input_token_count
latency
```

#### 退出

```text
在更小 candidate pool 中保留 V10 recall；
不能通过简单截断导致 recall 崩溃。
```

### 4.3 RANK-MACRO-LOAD

#### 目标

验证 candidate 规模和视图是否改善 Macro 注意力与绑定。

#### 对照组

```text
V10 global / role index input
```

#### 实验组

```text
reranked role view
reranked claim-local role view
```

#### 指标

```text
claim_precision
claim_recall
statement_type_accuracy
binding_accuracy
role_ineligible_binding
invalid_span_reference
latency
```

#### 退出

```text
role_ineligible_binding 下降；
claim / binding accuracy 提升；
input token count 下降。
```

## 5. Phase 2：Role-specific view 与 span graph

### 5.1 RoleEligibility Contract

#### 目标

将角色资格声明式化，避免散落 if/else。

#### 契约

```text
role
hard_sources
soft_sources
fallback_sources
prohibited_sources
required_provenance
```

示例：

```text
temporal:
  hard = temporal parser parsed_type
  soft = temporal_expression label
  fallback = reranker temporal score

target:
  soft = target_mention / state_mention / action_event label
  fallback = reranker target score
```

#### 禁止

```text
医学词表
症状关键词
疾病词表
canonical 命中反推 role
```

### 5.2 SpanGraph

#### 目标

用结构组件管理 span 关系和 claim-local view。

#### 节点

```text
SpanNode
ClaimRegionNode
EntityNode
ParserNode
```

#### 边

```text
CONTAINED_IN
OVERLAPS
ADJACENT_TO
SAME_SOURCE_BLOCK
PARSED_AS
RESOLVES_TO
ROLE_CANDIDATE_FOR
```

#### 推荐实现

```text
SpaCy Doc / SpanGroup 或等价 offset adapter
+ NetworkX in-memory graph
+ Pydantic / BAML validator
```

#### 指标

```text
graph_edge_valid_rate
claim_region_assignment_accuracy
cross_region_binding_count
graph_latency
```

### 5.3 MACRO-VIEW-PRUNE

#### 目标

验证字段专属菜单是否能消灭跨角色绑定。

#### 输入

```text
claim-local role menus
primary / fallback candidates
raw claim-local context
```

#### 输出规则

```text
target_span_id ∈ target_candidates
relation_span_id ∈ relation_candidates
subject_span_id ∈ subject_candidates
temporal_span_id ∈ temporal_candidates
measurement_span_id ∈ measurement_candidates
```

无候选输出 `null`，不得跨界。

#### 指标

```text
role_ineligible_binding
invalid_span_reference
invalid_span_binding
fallback_selection_rate
fallback_without_reason
binding_accuracy
```

#### 退出

```text
role_ineligible_binding = 0；
invalid reference / binding = 0。
```

## 6. Phase 3：Deterministic claim skeleton seeds

### 6.1 Seed 生成规则

只使用结构关系，不做医学判断：

1. 每个 claim region 的 top target candidate 可生成 seed。
2. 同一 support + relation 内多个 target candidates 可生成 shared scope seeds。
3. action event candidate 可生成 action seed。
4. state target candidate 可生成 state seed。
5. temporal / measurement parser span 只能绑定，不单独生成事实 seed。
6. seed_id 由 source block、support、target 和 relation 派生。

### 6.2 SEED-SHARED

#### 样本类型

```text
没有呕吐、干呕、反流、流涎或舔唇
精神、食欲和饮水都正常
无血便和黑便
```

#### 指标

```text
seed_recall
seed_precision
shared_relation_inheritance_rate
statement_type_accuracy
claim_id_stability
```

#### 退出

```text
shared scope target 全覆盖；
relation 正确继承；
claim_id 稳定。
```

### 6.3 SEED-ACTION

#### 样本类型

```text
我前天开始给它换新猫粮
医生给它开了药
主人昨天喂了罐头
护士给它打针
```

#### 指标

```text
action_seed_recall
action_agent_binding_accuracy
action_recipient_binding_accuracy
object_binding_accuracy
temporal_binding_accuracy
```

#### 退出

```text
participant mention 可用；
resolved_empty = 0；
cross_claim_assignment = 0。
```

### 6.4 MACRO-FULL

#### 输入

```text
reranked claim-local role views
deterministic claim seeds
turn-level act views
TurnContext summary
```

#### 输出

```text
turn acts
seed decisions
statement types
role-eligible bindings
coverage gap suspicion
```

#### 仍保持一次 Macro 调用

V11 不把 act / claim / binding 拆成运行时多个模型任务；只通过输入视图和输出 section 约束。

#### 指标

```text
act_precision / recall
claim_precision / recall
statement_type_accuracy
binding_accuracy
role_ineligible_binding
coverage_gap_suspected_count
latency
```

## 7. Phase 4：Statement verifier

### 7.1 STATE-VERIFY

#### 触发条件

```text
macro confidence 低
statement 与 relation 冲突
shared scope 中 statement 不一致
denies / reports_normal / reports_abnormal 疑似混淆
relation=no_change 但 statement=reports_normal
```

#### 输入

```text
support quote
target quote
relation quote
macro proposed statement type
```

不输入完整原文，不生成新 claim。

#### 输出

```text
confirmed / mismatch / uncertain
corrected statement type
confidence
review_required
```

#### 指标

```text
statement_type_accuracy
denies_as_reports
normal_as_no_change
no_change_as_normal
verifier_correction_accuracy
latency
```

## 8. Phase 5：Relation cold stability

### 8.1 REL-COLD3

#### 输入

冻结 V10 relation fixed contract：

```text
serialization format
field order
batch size
missing field representation
prompt version
frozen few-shot
```

#### 执行

```text
同一输入三次冷调用
禁用 cache
```

#### 指标

```text
relation_accuracy
unclear_rate
signature_stability
stable_and_correct_rate
format_error_count
p50 / p95 latency
```

#### 退出

```text
3/3 signature 一致且正确；
format_error = 0。
```

### 8.2 REL-BATCH-SENSITIVITY

#### 变量

```text
batch 1 / 4 / 8
forward order
reverse order
```

#### 指标

```text
accuracy
unclear_rate
order_sensitivity
format_error
```

#### 退出

```text
batch order 不改变结果。
```

## 9. Phase 6：Winner regression

### 9.1 DOWNSTREAM-GOLD

#### 目标

确认 V11 view / seed 未破坏 gold winner。

#### 流程

```text
gold target span → canonical direct recall
gold participant mention → TurnContext resolver
gold temporal / measurement quote → deterministic parser
```

#### 指标

```text
candidate_recall
canonical_accuracy
participant_resolution_accuracy
temporal_binding_accuracy
measurement_binding_accuracy
```

### 9.2 DOWNSTREAM-LIVE

#### 目标

验证 live macro 输出能否供给 downstream。

#### 指标

```text
target_span_availability
relation_span_availability
participant_span_availability
temporal_span_availability
measurement_span_availability
canonical_accuracy
participant_accuracy
blocked_reason_distribution
```

#### 归因

```text
downstream 失败优先归因 macro availability；
gold 输入失败才归因 downstream component。
```

## 10. Phase 7：Early exit

### 10.1 EARLY-MINIMAL

比较：

```text
full path
minimal path
policy-driven path
```

#### 指标

```text
model_call_count
latency
cost
decision_quality
review_rate
false_early_exit
safety_path_preserved_rate
```

### 10.2 EARLY-VOI

逐个关闭组件，观察下游决策是否变化：

```text
reranker
relation verifier
canonical
participant
temporal
measurement
```

若关闭组件不改变决策，则该组件对该输入无继续执行价值。

### 10.3 EARLY-FAILURE

验证上游失败后：

```text
downstream_call_count = 0
blocked_reason_correct
false_pass = 0
失败未被解释为用户未提供
```

## 11. Phase 8：REP / NEG / Adapter / Held-out

### 11.1 REP-MACRO

#### 要求

```text
三次冷调用
禁用 cache
固定 candidate view / seeds / prompt / schema
```

#### 指标

```text
raw_output_stability
semantic_claim_stability
semantic_binding_stability
stable_and_correct_rate
stable_but_wrong_rate
unstable_rate
p50 / p95 latency
```

#### 退出

```text
3/3 semantic signature 一致且正确。
```

### 11.2 NEG-V11

覆盖：

```text
menu 外 span 引用
模型自由 quote
模型新增无 seed claim
fallback 无 reason
temporal menu 为空时伪造 span
target 不在 support 内
canonical selected 不在候选内
resolved participant 为空
projection 消费 blocked claim
early exit 误解释 upstream failure
reranker 修改 offset / text
```

#### 退出

```text
gate_blocked_as_expected_rate = 1.0
false_pass = 0
```

### 11.3 Adapter Cold

只有 Macro golden 达标后执行：

```text
STRUCT-BASE
STRUCT-INSTRUCTOR
STRUCT-BAML
```

统一输入、fixture、gate 和三次冷调用。

### 11.4 HELD-OUT-V11

默认 blocked。

进入条件：

```text
reranker winner 冻结；
candidate view 冻结；
seed contract 冻结；
macro development 达标；
relation 3/3；
REP 3/3；
NEG 全部阻断。
```

冻结版本：

```text
model
reranker
prompt
schema
candidate budget
seed rules
relation contract
fixture
gate
```

## 12. 报告要求

每个实验输出：

```text
experiment_id
phase
control_group
experimental_group
mode: quick / shadow / cold
diagnostic_only
can_unblock_v8_phase
changed_variables
snapshot sha256
model / prompt / schema / reranker / relation prompt version
cache_status

candidate metrics
view metrics
seed metrics
macro metrics
statement metrics
relation metrics
downstream metrics
early exit metrics
cost metrics

first_attempt_status
retry_count
failure_attribution
safety_boundary
```

## 13. 安全边界

所有命令保持：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
```

不接入：

```text
VetOrchestrator
ClinicalSafetyEvaluator
clinical safety pgvector
required_context
clinical safety OPA
```

真实模型依赖不可用时必须失败，不得回退关键词、宽松 JSON 或本地医学规则。

## 14. 推荐执行顺序

```text
1. SNAP-INTEGRITY
2. RANK-CROSS
3. RANK-BUDGET
4. VIEW-COVERAGE
5. SEED-SHARED
6. SEED-ACTION
7. MACRO-VIEW-PRUNE
8. MACRO-FULL
9. STATE-VERIFY
10. REL-COLD3
11. DOWNSTREAM-LIVE
12. EARLY-V11
13. REP-MACRO
14. NEG-V11
15. Adapter Cold
16. Held-Out
```

禁止在 candidate view 或 seed 未达标时执行 integration / held-out。

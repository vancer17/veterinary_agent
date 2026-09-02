<!--
=============================================================================
文件: input-preprocessing-v12-support-first-graph-ranking-experiment-plan.md
作用: 定义第十二轮 support-first graph ranking / claim-local view 专项实验的
      实现边界、实验矩阵、指标、准入条件和复现口径。
范围: 覆盖 metric align、SpanGraph reduction、support anchor、role-aware
      conflict resolution、claim-local view、seed recovery、seeded Macro、
      frozen relation regression、early exit、REP 和安全边界。
说明: 本文是待执行实验计划；不解除 V8 live phase gate，不构成生产消费准入。
维护: 当 V12 契约、实验矩阵、报告结论或准入条件变化时同步更新本文。
=============================================================================
-->

# Input preprocessing V12 support-first graph ranking 实验计划

> **文档状态**：已执行；权威实验结论见 [input-preprocessing-v12-support-first-graph-ranking-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/input-preprocessing-v12-support-first-graph-ranking-change-summary.md)
>
> **阶段定位**：V12 修复轮，目标是恢复 live support anchor / role-local view / structural seed，而不是进入 integration
>
> **核心假设**：full candidate pool 中存在 gold，当前失败来自全局 ranking 和 claim-local view 构造；support-first 图层级可以提升 gold-in-view 与 seed recall

## 1. 实验目标

V12 回答以下问题：

1. 统一 metric 口径后，V11 candidate / view 指标是否可比？
2. SpanGraph reduction 能否降低边数并保留 gold path？
3. 图拓扑能否找到 gold support anchor？
4. role-aware conflict resolution 能否减少候选冗余而不误删 gold？
5. claim-local role view 能否提升 gold-in-view？
6. live structural seed recall 能否从 0 恢复？
7. seeded Macro 是否因更小且更正确的局部视图而改善？
8. frozen relation contract 是否仍保持三次冷调用稳定？
9. early exit 是否能降低成本且不产生 false early exit？
10. REP 是否能从 2/3 走向 3/3 stable-and-correct？

## 2. 非目标

1. 不继续扩大 BGE reranker 矩阵。
2. 不更换生产主路径。
3. 不接入 `VetOrchestrator`。
4. 不写 `consultation_states`。
5. 不触发 ClinicalSafetyEvaluator、clinical safety pgvector、required_context 或 clinical safety OPA。
6. 不读取 held-out。
7. 不触发 DSPy。
8. 不修改 relation prompt。
9. 不引入医学词典或症状关键词规则。
10. 不放宽 quote、candidate menu、seed、coverage gap 或 projection gate。

## 3. 输入与版本

### 3.1 Live snapshot

默认使用 V11 冻结 snapshot：

```text
.data/evaluations/input-preprocessing-v11/snapshots/v10-candidates.json
sha256=ccd573276b6b4a956cc967e4d2c829109da7931107418856602eee011400f6d8
```

Snapshot 基线：

```text
candidate_count = 935
fixture_field_count = 82
exact_field_recall = 0.6585365853658537
role_coverage = 0.5121951219512195
```

### 3.2 Ideal snapshot

Ideal control 使用：

```text
.data/evaluations/input-preprocessing-v11/snapshots/ideal-golden.json
sha256=96d9ee14d4c284a939dcd8b4b548308b74543917df5f7e86e3db33cc978d51d
```

Ideal 只验证契约和工具链，不作为生产 fallback。

### 3.3 版本冻结

实验前冻结：

```text
snapshot version
graph schema version
anchor eligibility version
conflict resolution version
seed version
macro schema / prompt version
relation frozen prompt version
fixture sha256
gate version
```

实验过程中不得根据失败即时修改权重或阈值。

## 4. 实现组件

建议新增：

```text
src/vet_agent/input_preprocessing/v12_contracts.py
src/vet_agent/input_preprocessing/v12_graph.py
src/vet_agent/input_preprocessing/v12_anchor.py
src/vet_agent/input_preprocessing/v12_views.py
src/vet_agent/input_preprocessing/v12_conflict.py
src/vet_agent/input_preprocessing/v12_experiments.py
tests/test_input_preprocessing_v12.py
scripts/integration/run-input-preprocessing-v12-remote-runner.sh
```

组件职责：

| 组件 | 职责 | 禁止事项 |
|---|---|---|
| `v12_graph.py` | containment DAG、overlap / adjacency 边、transitive reduction | 不把 overlap 放入 DAG |
| `v12_anchor.py` | anchor hard filter、结构优先级、top alternatives | 不用医学词和复杂手写加权 |
| `v12_views.py` | direct / bounded children、role menu、primary / fallback | 不提供全局候选池 |
| `v12_conflict.py` | 同 role / 同 anchor conflict resolution | 不做全局 NMS |
| `v12_experiments.py` | 实验编排、指标、报告、gate | 不读取 held-out |

## 5. Phase 0：METRIC-ALIGN

### 目标

统一 V11 中混乱的 candidate count 口径。

### 输出指标

```text
snapshot_unique_candidate_count
view_presented_slot_count
duplicate_presentation_count
unique_candidates_sent_to_macro
macro_input_token_count
primary_menu_count
fallback_menu_count
empty_menu_count
```

### 验收

1. 能从报告中区分 unique span 与 role / region 重复呈现 slot。
2. V11 snapshot baseline 可复现。
3. 后续所有 view 报告均包含上述字段。

## 6. Phase 1：GRAPH-REDUCE

### 目标

降低 V11 SpanGraph 的 68199 条边密度，同时保留 gold path。

### 图规则

1. 唯一节点 key：`source_block_id + start + end + normalized_text`。
2. 同一节点的多角色保留在 metadata。
3. 严格 containment 进入 DAG。
4. overlap、adjacency、same source block 独立存边。
5. containment DAG 执行 transitive reduction。
6. 保留 direct parent-child 边。
7. 移除无子节点且无 role 价值的碎片。
8. 阻断跨 source block 边。

### 指标

```text
node_count
edge_count
containment_edge_count
overlap_edge_count
adjacency_edge_count
direct_child_edge_count
gold_path_retention
graph_latency_ms
transitive_reduction_latency_ms
```

### 验收

```text
edge_count 显著下降；
gold_path_retention = 1.0；
graph latency 可接受。
```

## 7. Phase 2：ANCHOR-TOPO

### 目标

验证图拓扑和结构优先级能否找到 support anchor。

### 对照组

```text
A. V11 base ranking selected view；
B. raw topological roots；
C. eligible anchor + structural priority；
D. eligible anchor + structural priority + alternatives。
```

### Anchor hard filter

```text
source block 正确；
非纯标点 / 单字碎片；
宽度在配置上限内；
不覆盖整段输入，除非无更小 anchor；
至少包含一个 target / relation / parser child；
边界兼容；
不跨不可解释边界；
有 extractor 或结构证据。
```

### 结构优先级

使用版本化字典序，不使用复杂加权公式：

```text
1. parser child 存在；
2. child role diversity；
3. direct child 数量合理；
4. boundary completeness；
5. length 合理；
6. extractor agreement；
7. parent / child score。
```

### 指标

```text
gold_support_anchor_recall@1
gold_support_anchor_recall@2
gold_support_anchor_recall@3
anchor_precision
selected_anchor_count
over_broad_anchor_rate
anchor_without_valid_child_rate
anchor_latency
```

### 验收

```text
gold anchor top1/2/3 显著优于 V11；
不默认选择整段输入；
备选 anchor 保留 ambiguity。
```

## 8. Phase 3：ANCHOR-NMS / conflict resolution

### 目标

减少同角色冗余，同时不删除必要嵌套。

### 对照组

```text
A. no pruning；
B. global filter_spans 负面对照；
C. same-role pruning；
D. same-anchor same-role pruning；
E. score-margin preserving pruning。
```

### 硬规则

只允许在以下范围竞争：

```text
同 role；
同 anchor；
同竞争组；
同父子层级。
```

必须保留：

```text
support-target 嵌套；
relation-target 嵌套；
subject-recipient 同 boundary；
target-measurement 同 boundary；
多角色同 boundary。
```

分数接近时保留 top 2 / 3 alternatives。

### 指标

```text
unique_candidate_count
presented_slot_count
candidate_reduction_rate
gold_retention_rate
false_suppression_count
ambiguity_preserved_rate
suppression_audit_completeness
conflict_latency
```

### 验收

```text
candidate count 下降；
gold retention 不下降；
false suppression 可归因为 0 或进入 review；
global filter_spans 不作为主路径。
```

## 9. Phase 4：ROLE-LOCAL-VIEW

### 目标

只向 Macro 提供 selected anchor 内的 role-specific menus。

### 输入

```text
top anchors + alternatives；
direct children；
bounded descendants；
adjacent parser candidates；
TurnContext entity candidates。
```

### 输出

每个 anchor / seed 提供：

```text
support menu
target menu
relation menu
subject menu
participant menus
temporal menu
measurement menu
```

### 指标

```text
gold_in_view
gold_in_primary
gold_in_fallback
empty_menu_rate
fallback_rate
unique_candidates_sent_to_macro
presented_slot_count
cross_region_assignment_count
macro_input_token_count
```

### 验收

```text
gold_in_view 高于 V11 base top16 的 0.2195；
gold_in_primary 提升；
empty menu 减少；
fallback 不接近全局池；
unique candidates sent to macro 下降。
```

## 10. Phase 5：SEED-RECOVERY

### 目标

恢复 V11 live structural seed recall = 0 的问题。

### Seed 生成

```text
support anchor
→ anchor 内 target / relation / participant / parser candidates
→ target seed
→ shared scope seed
→ action seed
→ state seed
```

### 指标

```text
seed_recall
shared_seed_recall
action_seed_recall
state_seed_recall
seed_precision
coverage_gap_rate
coverage_gap_reason_distribution
claim_id_stability
```

### 验收

```text
live seed recall > 0；
shared seed recall 恢复；
action seed 有可解释覆盖；
seed 未命中时 coverage gap 显式。
```

此阶段未通过前，不得继续 MACRO-LOAD。

## 11. Phase 6：MACRO-LOAD

### 目标

验证 support-first view 是否改善 seeded Macro。

### 输入

```text
raw text / TurnContext summary；
turn act menus；
claim-local seed views；
role-specific candidate menus。
```

### 输出

```text
turn acts；
seed decisions；
statement types；
role bindings；
coverage gap。
```

### 对照组

```text
A. V11 ideal base top3；
B. V11 live base top3；
C. V12 support-first view；
D. V12 support-first + conflict resolution。
```

### 指标

```text
act_precision
act_recall
act_evidence_span_valid_rate
claim_precision
claim_recall
statement_type_accuracy
binding_accuracy
invalid_span_reference
invalid_span_binding
candidate_menu_violation
fallback_without_reason
unseeded_claim_count
model_free_quote_output
empty_claim_without_coverage_gap
macro_latency
```

### 验收

```text
claim / statement / act 指标高于 V11；
menu violation 下降；
空 claims 均显式 coverage gap。
```

## 12. Phase 7：REL-FROZEN-REGRESSION

### 目标

验证 V11 relation fixed contract 在版本不变时仍稳定。

### 执行

```text
batch 1；
batch 4 forward；
batch 4 reverse；
三次冷调用；
禁用 cache。
```

### 指标

```text
relation_accuracy
unclear_rate
format_error_count
signature_stability
stable_and_correct_rate
p50_latency
p95_latency
model_call_count
```

### 验收

```text
3/3 stable-and-correct；
format error = 0；
batch order 不敏感。
```

## 13. Phase 8：EARLY-EXIT-V12

### Continuation gate

每个组件记录：

```text
component；
prerequisite_status；
decision = execute / skip / early_exit / blocked；
reason；
consumer；
value_of_information；
latency；
model_call_count。
```

### 早退规则

```text
无 support anchor → 阻断；
anchor 内无 target → 不生成 seed；
seed 被拒绝 → 不进入 enrichment；
无 relation span → 不调用 relation classifier；
无 target span → 不调用 canonical recall；
上游 blocked → 不进入 projection。
```

### 指标

```text
model_call_count
total_latency
false_early_exit
blocked_reason_correct_rate
safety_path_preserved_rate
decision_quality_delta
```

### 验收

```text
成本下降；
false early exit = 0；
safety path preserved = 1.0。
```

## 14. Phase 9：REP-V12

### 执行条件

只有 support anchor、role-local view 和 seed recovery 达到阶段目标后才执行。

### 执行方式

```text
三次冷调用；
禁用 cache；
固定 graph / anchor / view / seed / macro 版本。
```

### 指标

```text
raw_output_stability
semantic_claim_stability
semantic_binding_stability
stable_and_correct_rate
stable_but_wrong_rate
unstable_rate
p50_latency
p95_latency
```

### 验收

```text
3/3 signature 一致且正确。
```

## 15. NEG / ASYNC

### NEG-V12

覆盖：

```text
offset / text mismatch；
跨 source block anchor；
全局 NMS 删除必要嵌套；
menu 外引用；
模型自由 quote；
新增 unseeded claim；
fallback 无 reason；
空菜单伪造 span；
coverage gap 缺失；
projection 消费 blocked claim；
early exit 误解释 upstream_failed。
```

指标：

```text
gate_blocked_as_expected_rate = 1.0
false_pass = 0
```

### ASYNC-V12

继续验证：

```text
queue full；
dead letter；
trace completeness；
worker failure isolation；
不写业务状态；
不触发临床安全 evaluator / OPA。
```

## 16. 报告契约

顶层报告必须包含：

```text
diagnostic_only = true
can_unblock_v8_phase = false
phase / suite / mode
changed_variables
snapshot path / sha256
graph schema version
anchor eligibility version
conflict resolution version
seed version
macro schema / prompt version
relation frozen prompt version
fixture sha256
gate version
cache status
safety boundary
```

每个实验输出：

```text
experiment_id
control_group
experimental_group
primary_metrics
gate_results
failure_attribution
partial_success_summary
latency
model_call_count
token_count_available
cost_available
```

## 17. 归因码

```text
metric口径_mismatch
graph_edge_invalid
graph_gold_path_lost
anchor_not_eligible
anchor_over_broad
anchor_missing_child
anchor_ambiguous
role_view_gold_missing
primary_menu_empty
fallback_overused
conflict_false_suppression
necessary_nesting_removed
seed_support_anchor_missing
seed_target_missing
seed_coverage_gap_missing
macro_act_semantic_error
macro_claim_skeleton_error
macro_statement_type_error
macro_menu_violation
relation_frozen_regression_failed
early_exit_upstream_failed
gate_blocked_expected
```

## 18. 安全边界

所有 V12 命令保持：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
held_out_read = false
dspy_used = false
```

未接入：

```text
VetOrchestrator
ClinicalSafetyEvaluator
clinical safety pgvector
required_context
clinical safety OPA
```

所有 V12 graph / anchor / view / seed / Macro 结果不得：

```text
进入 production projection；
作为生产 fallback；
解除 V8 live phase admission；
替代 live span gate。
```

## 19. 执行顺序

```text
1. METRIC-ALIGN
2. GRAPH-REDUCE
3. ANCHOR-TOPO
4. ANCHOR-NMS
5. ROLE-LOCAL-VIEW
6. SEED-RECOVERY
7. MACRO-LOAD
8. REL-FROZEN-REGRESSION
9. EARLY-EXIT-V12
10. REP-V12
11. NEG / ASYNC
12. 总结并更新 architecture guidance
```

## 20. 预期复现入口

实现后应提供统一 runner：

```bash
scripts/integration/run-input-preprocessing-v12-remote-runner.sh \
  --suite quick \
  --mode quick \
  --no-cache \
  --output-dir .data/evaluations/input-preprocessing-v12/quick
```

其余 suite：

```text
snapshot
metric
graph
anchor
conflict
view
seed
macro
relation
early
rep
negative
async
all
```

真实模型依赖不可用时必须失败，不得回退关键词、宽松 JSON 或本地医学规则。

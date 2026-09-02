<!--
=============================================================================
文件: input-preprocessing-v12-support-first-graph-ranking-change-summary.md
作用: 记录第十二轮 support-first graph ranking / claim-local view 修复实验。
范围: 覆盖 metric align、graph reduction、anchor、view、seed、Macro、relation、
      early-exit、NEG/ASYNC 与 held-out 防护。
说明: 本文证明 V12 修复链路可执行并记录当前 blocker；不解除 V8 phase gate。
维护: 当 V12 runner、snapshot、报告、prompt 或实验结论变化时更新本文。
=============================================================================
-->

# Input preprocessing V12 support-first graph ranking 实验记录

> **结论**：V12 runner 已实现并部署到远程 `.venv-v11`。Metric 口径、冻结 snapshot、reduced SpanGraph、support anchor、role-local view、structural seed、seeded one-call Macro、frozen relation regression、early-exit、NEG 与 ASYNC 均可执行。Live role-local view 的 `gold_in_view` 从 V11 base top16 的 `0.2195` 提升到 `0.5610`；live structural seed recall 从 `0` 恢复到平均 `0.3400`。
>
> **当前 blocker**：support-first view 和 seed 仍未达到 full snapshot `0.6585` exact recall 上限；structural seed precision 仅 `0.0448`；当前 conflict pruning 均会误删必要 gold。Macro shadow 在严格 `empty_acts_require_no_act_reason` gate 下失败，后续请求还出现 timeout / circuit breaker。Frozen relation contract 在 batch 1、batch 4 forward 与 reverse order 下均保持三次冷调用 `3/3` stable-and-correct。
>
> **准入结论**：所有 V12 报告均为 `diagnostic_only=true`、`can_unblock_v8_phase=false`。V8 live Phase 0 gate 保持不变；held-out 与 DSPy 继续冻结。

## 1. 实现范围

新增：

```text
src/vet_agent/input_preprocessing/v12_contracts.py
src/vet_agent/input_preprocessing/v12_graph.py
src/vet_agent/input_preprocessing/v12_anchor.py
src/vet_agent/input_preprocessing/v12_views.py
src/vet_agent/input_preprocessing/v12_conflict.py
src/vet_agent/input_preprocessing/v12_seeds.py
src/vet_agent/input_preprocessing/v12_macro.py
src/vet_agent/input_preprocessing/v12_experiments.py
scripts/integration/run-input-preprocessing-v12-remote-runner.sh
tests/test_input_preprocessing_v12.py
```

支持 suite：

```text
quick
metric
graph
anchor
conflict
view
seeds
macro
relation
early
negative
async
rep
all
```

支持实验：

```text
METRIC-ALIGN
GRAPH-REDUCE
ANCHOR-TOPO
ANCHOR-NMS
ROLE-LOCAL-VIEW
SEED-RECOVERY
SEED-SHARED
SEED-ACTION
MACRO-FULL
MACRO-VIEW-PRUNE
REL-FROZEN-REGRESSION
EARLY-MINIMAL
EARLY-VOI
EARLY-FAILURE
REP-V12
NEG-V12
ASYNC-V12
HELD-OUT-V12
```

报告版本：

```text
v12-experiment-report-1
```

关键版本：

```text
graph schema = v12-support-graph-20260831-1
anchor eligibility = v12-anchor-eligibility-20260831-1
conflict resolution = v12-role-conflict-20260831-1
view = v12-support-first-role-view-20260831-1
seed = v12-support-first-seed-20260831-1
macro schema = v11-macro-seeded-1+support-first-view
macro prompt = v12-macro-support-first-dev-20260831-1
```

Runner 默认拒绝读取文件名包含 `held_out` 的 fixture 或 snapshot。

## 2. 核心实现边界

### 2.1 Metric 口径拆分

V12 区分：

```text
snapshot candidate record
snapshot unique boundary node
view presented slot
unique candidate sent to Macro
```

并修复 V11 `near_or_exact` 双重计数问题。V12 使用：

```text
near_overlap OR exact
```

的 union 口径。

### 2.2 Runtime 不使用 gold 字段

Graph、anchor、view 与 seed 输入不包含：

```text
expected_start
expected_end
expected_label
gold quote
claim owner
held-out 信息
```

Quick 报告中：

```text
runtime_gold_field_leak_count = 0
```

### 2.3 Graph 与 anchor

Graph 构建：

```text
同 boundary 候选合并
strict containment 进入 DAG
containment DAG 执行 transitive reduction
overlap / adjacency 独立存边
阻断跨 source block 边
```

Anchor 使用版本化字典序结构优先级：

```text
parser child
child role diversity
direct child count
boundary completeness
length
extractor agreement
score
```

不使用医学词表、症状关键词、疾病词典或 canonical 命中反推 role。

### 2.4 Candidate menu 与 Macro

每个 seed 的字段只能来自对应 role menu。无候选输出 `null`，fallback 必须携带 reason。

Macro 仍保持一次调用。`MACRO-ACT` / `MACRO-SKELETON` / `MACRO-BINDING` 是指标拆分，不是运行时多个模型任务。

V12 强制：

```text
empty acts require no_act_reason
empty claims require coverage_gap_suspected + reason
unseeded claim blocked
free quote blocked
menu violation blocked
```

## 3. 本地与远程验证

本地：

```text
ruff check V12 modules/tests: PASS
mypy V12 modules: PASS
pytest V12: 5 passed
pytest 全量: 257 passed, 43 skipped
bash -n remote runner: PASS
```

远程 `.venv-v11`：

```text
compileall V12 modules: PASS
ruff check V12 modules/tests: PASS
mypy V12 modules: PASS
pytest V8/V9/V10/V11/V12: 39 passed
```

工程修复：

1. V12 remote runner 显式传播 `INPUT_PREPROCESSING_TIMEOUT_SECONDS`；
2. `QwenClient` 在 request body 中向 LiteLLM 传播 caller timeout；
3. Macro unit failure 会将 `MACRO-FULL` 顶层状态置为 `failed`；
4. `V11MacroSemanticRawOutput` 补齐 `empty_acts_require_no_act_reason` 硬 gate。

## 4. Quick control

报告：

```text
.data/evaluations/input-preprocessing-v12/quick-v3/v12-20260831-175955-2543729.json
sha256=b624e75c426022acbd2a2000224864434b81a2cd88cf7acc442c721bec987c45
```

### 4.1 METRIC-ALIGN

```text
snapshot_candidate_record_count = 935
snapshot_unique_candidate_count = 488
fixture_field_count = 82
exact_field_recall = 0.6585365853658537
role_coverage = 0.5121951219512195
near_or_exact = 0.7926829268292683
offset_valid_rate = 1.0
text_match_rate = 1.0
runtime_gold_field_leak_count = 0
```

结论：

1. V11 冻结 snapshot 成功复现；
2. 935 条 candidate record 归并为 488 个 unique boundary node；
3. runtime snapshot 未泄漏 expected/gold 字段；
4. V12 available gold 上限仍是 `0.6585`，graph/ranking 不能创造缺失 span。

### 4.2 GRAPH-REDUCE

```text
node_count = 488
edge_count = 8232
containment_edge_count = 473
overlap_edge_count = 3321
adjacency_edge_count = 4438
direct_child_edge_count = 473
duplicate_candidate_count = 447
gold_path_retention = 1.0
graph_latency_ms = 59
```

V11 graph edge count 为 `68199`。V12 降到 `8232`，且 available gold path retention 为 `1.0`。

### 4.3 ANCHOR-TOPO

```text
gold_support_anchor_recall@1 = 0.22727272727272727
gold_support_anchor_recall@2 = 0.4090909090909091
gold_support_anchor_recall@3 = 0.5
anchor_precision = 0.45454545454545453
selected_anchor_count = 11
over_broad_anchor_rate = 0.0
anchor_without_valid_child_rate = 0.0
```

结论：

1. topological support anchor 有恢复效果；
2. top3 可覆盖一半 support fields；
3. 未默认选择整段输入；
4. anchor 仍受 snapshot 中缺失 support candidate 限制。

### 4.4 ROLE-LOCAL-VIEW

```text
gold_in_view = 0.5609756097560976
gold_in_primary = 0.5487804878048781
gold_in_fallback = 0.012195121951219512
empty_menu_count = 0
fallback_rate = 0.027645788336933045
snapshot_unique_candidate_count = 488
unique_candidates_sent_to_macro = 457
view_presented_slot_count = 2315
duplicate_presentation_count = 1858
macro_input_character_count = 7997
macro_input_token_count_available = false
```

对比 V11 live base top16：

```text
gold_in_view = 0.2195
```

V12 提升到 `0.5610`。

结论：

1. support-first graph view 显著优于 V11 全局 ranking view；
2. view 仍未接近 full snapshot 的 `0.6585` 可用 recall；
3. fallback 使用率低，没有退回全局池；
4. token usage 仍不可用，不能用估算值冒充真实 token。

### 4.5 SEED-RECOVERY

```text
mean_seed_count = 21.2
mean_seed_recall = 0.33999999999999997
mean_seed_precision = 0.044787878787878786
mean_shared_seed_recall = 0.04
mean_action_seed_recall = 0.4
mean_state_seed_recall = 0.10666666666666666
mean_claim_id_stability = 1.0
mean_coverage_gap_rate = 0.005882352352941176
mean_coverage_gap_count = 0.2
```

对比 V11：

```text
live seed recall = 0
```

V12 恢复为 `0.3400`。

结论：

1. support anchor 能恢复部分 structural seed；
2. seed ID 稳定；
3. seed precision 很低，target 选择仍产生大量错误替代 seed；
4. shared scope 恢复仍弱；
5. action seed 有部分恢复。

### 4.6 ANCHOR-NMS / conflict resolution

```text
anchor_count = 11
global_negative_gold_retention = 0.42077922077922075
```

| variant | candidate reduction | gold retention |
|---|---:|---:|
| no pruning | 0.0 | 1.0 |
| global filter_spans negative | 0.8575 | 0.4208 |
| same-role | 0.7578 | 0.4100 |
| same-anchor-role | 0.7578 | 0.4100 |
| score-margin | 0.8121 | 0.4684 |

结论：

1. 当前 conflict pruning 均会误删必要 gold；
2. global `filter_spans` 负面对照符合预期，不能作为主路径；
3. score-margin 相对较好，但 gold retention 仍只有 `0.4684`；
4. V12 不采用任何 pruning variant 作为 finalist。

### 4.7 NEG / ASYNC / early exit

NEG-V12：

```text
mutation_count = 9
gate_blocked_as_expected = 9
false_pass = 0
gate_blocked_as_expected_rate = 1.0
```

ASYNC-V12：

```text
submitted_count = 2
accepted_count = 1
queue_full_count = 1
dead_letter_count = 1
trace_completeness = 1.0
```

EARLY-FAILURE：

```text
failure_case_count = 5
downstream_call_count = 0
false_pass_count = 0
blocked_reason_correct_rate = 1.0
```

其他 early-exit control：

```text
false_early_exit = 0
safety_path_preserved_rate = 1.0
```

## 5. Seeded Macro shadow

最终严格 gate 报告：

```text
.data/evaluations/input-preprocessing-v12/macro-support-first-v5/v12-20260831-180800-2545211.json
sha256=902b004b1e497309939f15333d48cc07904aa05d05bb58d379dadc7744b0d6c0
```

结果：

```text
MACRO-FULL status = failed
macro_unit_count = 3
macro_unit_failed_count = 3
```

失败归因：

```text
macro-answer-fact:
  schema_invalid
  empty_acts_require_no_act_reason

macro-shared-scope:
  dependency_failed
  Qwen structured chat request failed / timeout

macro-action-roles:
  dependency_failed
  Qwen circuit breaker is open
```

结论：

1. `empty acts require no_act_reason` gate 生效；
2. 不能通过自动填充 reason、放宽 schema 或删除 act 字段绕过；
3. Macro 请求延迟和依赖稳定性仍是 blocker；
4. Macro golden 未达标，因此 REP-V12 不执行；
5. 不能进入 integration / adapter cold / held-out。

在启用严格 empty-act gate 前，V12 曾得到可执行的 macro response，但 acts 为空且缺少 reason，claim / statement 质量也为 0。该结果不能作为质量通过。启用硬 gate 后，以 `macro-support-first-v5` 为权威结果。

## 6. Relation frozen regression

V12 未修改 relation prompt，复用 V11 frozen few-shot contract。

### Batch 1

报告：

```text
.data/evaluations/input-preprocessing-v12/relation-frozen-batch1/v12-20260831-175138-2538795.json
sha256=fc99a6788813997cd0aa0632b2acf17d09d6aa0a186383c5b0a81318201dbe3e
```

```text
cold_run_count = 3
cache_hit_count = 0
relation_accuracy = 1.0
signature_stability = 1.0
stable_and_correct_rate = 1.0
unclear_rate = 0.0
format_error_count = 0
model_call_count = 18
p50_ms = 1147.0
p95_ms = 1337.0
```

### Batch 4 forward

报告：

```text
.data/evaluations/input-preprocessing-v12/relation-frozen-batch4-forward/v12-20260831-175210-2539197.json
sha256=a958562bebcb4585e84c0b7236acbfbf7f57360df1eda62b1fd2feaa428b0d08
```

```text
cold_run_count = 3
relation_accuracy = 1.0
signature_stability = 1.0
stable_and_correct_rate = 1.0
format_error_count = 0
model_call_count = 6
p50_ms = 1748.0
p95_ms = 2594.0
```

### Batch 4 reverse

报告：

```text
.data/evaluations/input-preprocessing-v12/relation-frozen-batch4-reverse/v12-20260831-175237-2539480.json
sha256=15fd0478ca4343b6562dbe8ae3187520ba0e66ddc8c80be33551937c5b814d0e
```

```text
cold_run_count = 3
relation_accuracy = 1.0
signature_stability = 1.0
stable_and_correct_rate = 1.0
format_error_count = 0
model_call_count = 6
p50_ms = 1668.0
p95_ms = 3199.0
```

结论：

1. relation frozen contract 在 V12 中保持 `3/3` stable-and-correct；
2. forward / reverse order 不改变结果；
3. batch 4 model call 更少，但单次延迟更高；
4. 该结果仍是 development diagnostic control；
5. 在 V12 整体 finalist 冻结前，不能宣布生产 winner。

## 7. 当前根因更新

1. **Graph / view blocker 明显缓解**
   - graph edge 从 `68199` 降到 `8232`；
   - available gold path retention 为 `1.0`；
   - `gold_in_view` 从 `0.2195` 提升到 `0.5610`。

2. **Support anchor / seed 有恢复但未达标**
   - anchor top3 support recall 为 `0.5`；
   - seed recall 从 `0` 恢复到 `0.34`；
   - seed precision 仅 `0.0448`；
   - full snapshot 可用 recall 仍为 `0.6585`。

3. **Conflict resolution 仍是 blocker**
   - 当前所有 pruning variant 均删除必要 gold；
   - no pruning 是唯一 gold-safe control；
   - 不能为了降低 candidate count 牺牲 gold retention。

4. **Macro 仍是首要质量与稳定性 blocker**
   - empty act reason gate 阻断有效；
   - qwen structured request 存在长延迟 / timeout / circuit breaker；
   - claim / statement / act 质量未证明。

5. **Relation frozen contract 稳定**
   - batch 1 / batch 4 forward / reverse 均为 `3/3`。

## 8. 下一步

1. 修复 Macro `no_act_reason` 输出契约，不自动填充 reason；
2. 归因 Qwen structured request timeout：
   - request payload size；
   - seed count；
   - schema complexity；
   - LiteLLM timeout propagation；
   - circuit breaker policy；
3. 改进 target ranking / seed precision；
4. 修复 shared scope seed；
5. redesign conflict resolution，确保 gold retention 不低于 no pruning；
6. Macro golden 达标后再执行 REP-V12；
7. held-out 与 DSPy 继续冻结。

## 9. 复现命令

Quick control：

```bash
scripts/integration/run-input-preprocessing-v12-remote-runner.sh \
  --suite quick \
  --mode quick \
  --no-cache \
  --output-dir .data/evaluations/input-preprocessing-v12/quick
```

Macro shadow：

```bash
INPUT_PREPROCESSING_V12_REMOTE_TIMEOUT_SECONDS=1800 \
INPUT_PREPROCESSING_V12_REQUEST_TIMEOUT_SECONDS=120 \
scripts/integration/run-input-preprocessing-v12-remote-runner.sh \
  --suite macro \
  --mode shadow \
  --no-cache \
  --top-k 8 \
  --target-top-k 12 \
  --max-targets-per-anchor 8 \
  --output-dir .data/evaluations/input-preprocessing-v12/macro-support-first
```

Relation batch 1：

```bash
scripts/integration/run-input-preprocessing-v12-remote-runner.sh \
  --suite relation \
  --mode cold \
  --no-cache \
  --relation-fewshot \
  --batch-size 1 \
  --relation-runs 3 \
  --output-dir .data/evaluations/input-preprocessing-v12/relation-frozen-batch1
```

Relation batch 4 forward：

```bash
scripts/integration/run-input-preprocessing-v12-remote-runner.sh \
  --suite relation \
  --mode cold \
  --no-cache \
  --relation-fewshot \
  --batch-size 4 \
  --relation-runs 3 \
  --output-dir .data/evaluations/input-preprocessing-v12/relation-frozen-batch4-forward
```

Relation batch 4 reverse：

```bash
scripts/integration/run-input-preprocessing-v12-remote-runner.sh \
  --suite relation \
  --mode cold \
  --no-cache \
  --relation-fewshot \
  --batch-size 4 \
  --relation-runs 3 \
  --relation-reverse-order \
  --output-dir .data/evaluations/input-preprocessing-v12/relation-frozen-batch4-reverse
```

## 10. 安全边界

本轮所有实现与远程命令保持：

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

所有 V12 graph / anchor / view / seed / Macro / relation 结果不得：

```text
进入 production projection
作为生产 fallback
解除 V8 live phase admission
替代 live span gate
接触 held-out
触发 DSPy 优化
```

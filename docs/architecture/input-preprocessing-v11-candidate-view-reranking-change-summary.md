<!--
=============================================================================
文件: input-preprocessing-v11-candidate-view-reranking-change-summary.md
作用: 记录第十一轮 input-preprocessing candidate snapshot、role-specific view、
      BGE reranking、structural seed、macro seeded schema、relation 冷启动、
      winner regression、early-exit 与快速验证实现和远程结果。
范围: 覆盖 V10 candidate pool 固化、runtime view/rerank/seed、candidate menu
      gate、statement verifier、relation fixed contract 冷调用和 REP/NEG/ASYNC。
说明: 本文证明 V11 修复链路可执行并记录当前质量 blocker；不解除 V8 live
      phase gate，不构成生产消费准入。
维护: 当 V11 runner、snapshot、报告、prompt、reranker 或结论变化时更新。
=============================================================================
-->

# Input preprocessing V11 candidate view / reranking 实验记录

> **结论**：V11 runner 已实现并在远程 `.venv-v11` 完成快速验证与探索性 shadow 实验。Phase 0 snapshot 成功固化 V10 candidate pool：`935` 个候选、exact field recall `0.6585`、role coverage `0.5122`，与 V10 full calibrated pool 一致。role-specific view 与 structural seed 在 ideal golden pool 下可保留 gold 并生成稳定 seed；但在 live candidate snapshot 上，base ranking 的 top-k view 只保留 `0.2195` gold，structural seed recall 为 `0`。BGE reranker 没有改善排名：top-1 gold 从 base 的 `0.1707` 降到 `0.0732`，且 rerank 耗时 `798253ms`。V11 relation fixed few-shot contract 在 batch 1、batch 4 forward 和 reverse order 下均达到三次冷调用 `3/3` 稳定且正确。Macro seeded one-call schema 在 golden pool 下可执行，但 claim skeleton / statement type 仍低；live candidate view 下 seed recall 为 `0`，macro 输出空 claims 且未给 coverage gap，被 schema gate 阻断。
>
> **准入结论**：所有 V11 报告均为 `diagnostic_only=true`、`can_unblock_v8_phase=false`。V8 live Phase 0 gate 保持不变，held-out 与 DSPy 继续冻结。

## 1. 实现范围

新增：

```text
src/vet_agent/input_preprocessing/v11_contracts.py
src/vet_agent/input_preprocessing/v11_snapshot.py
src/vet_agent/input_preprocessing/v11_views.py
src/vet_agent/input_preprocessing/v11_seeds.py
src/vet_agent/input_preprocessing/v11_macro.py
src/vet_agent/input_preprocessing/v11_experiments.py
scripts/integration/run-input-preprocessing-v11-remote-runner.sh
tests/test_input_preprocessing_v11.py
```

支持 suite：

```text
quick
snapshot
view
rank
budget
seeds
macro
relation
regression
early
negative
async
rep
all
```

支持实验：

```text
SNAP-INTEGRITY
SPAN-GRAPH
VIEW-COVERAGE
RANK-BASE
RANK-CROSS
RANK-BUDGET
SEED-SHARED
SEED-ACTION
MACRO-FULL
MACRO-VIEW-PRUNE
RANK-MACRO-LOAD
STATE-VERIFY
REL-COLD3
DOWNSTREAM-GOLD
DOWNSTREAM-LIVE
EARLY-MINIMAL
EARLY-VOI
EARLY-FAILURE
REP-MACRO
NEG-V11
ASYNC-V11
HELD-OUT-V11
```

报告版本：

```text
v11-experiment-report-1
```

关键版本：

```text
snapshot schema = v11-candidate-snapshot-1
view version = v11-role-view-20260831-1
reranker version = v11-bge-rerank-20260831-1
seed version = v11-structural-seed-20260831-1
macro schema = v11-macro-seeded-1
macro prompt = v11-macro-candidate-view-dev-20260831-2
statement schema = v11-statement-verify-1
```

Runner 默认拒绝读取文件名包含 `held_out` 的 fixture 或 snapshot。DSPy 保持冻结。

## 2. 核心边界

### 2.1 Snapshot 先行

V11 不在 ranking 实验中重复执行 GLiNER。live snapshot 显式构建一次：

```text
.data/evaluations/input-preprocessing-v11/snapshots/v10-candidates.json
sha256=ccd573276b6b4a956cc967e4d2c829109da7931107418856602eee011400f6d8
```

来源：

```text
snapshot_version=v10-small-t010-bilingual-variant-F-full-20260831-1
source_kind=v10-live-calibration
span_extractor_version=
  v10-gliner:staged:threshold-0.100:bilingual:
  f227d3cd637bd4e6757ae143935316d062393341
boundary_calibration_version=
  v10-boundary-calibration-dev-20260830-1
```

Snapshot 不包含：

```text
expected_start
expected_end
expected_label
gold quote
claim owner
held-out 信息
```

ideal control snapshot：

```text
.data/evaluations/input-preprocessing-v11/snapshots/ideal-golden.json
sha256=96d9ee14d4c284a9399dcd9b4b548308b74543917df5f7e86e3db33cc978d51d
```

### 2.2 Reranker 只读

BGE reranker 只做 query-candidate relevance scoring，禁止：

```text
生成 span
修改 offset / text
输出 quote
输出 canonical
判断医学事实
生成 claim
```

### 2.3 Macro 仍保持一次调用

V11 将输入组织为：

```text
turn act candidate menu
claim-local structural seed views
role-specific candidate menus
```

输出仍由一次 macro call 完成：

```text
turn acts
seed decisions / claims
statement types
role bindings
coverage gap
```

`MACRO-ACT` / `MACRO-SKELETON` / `MACRO-BINDING` 是指标拆分，不是运行时模型任务拆分。

### 2.4 Candidate menu 是物理边界

每个 seed 的字段菜单约束：

```text
support_span_id ∈ support menu
target_span_id ∈ target menu
relation_span_id ∈ relation menu
subject_span_id ∈ subject menu
temporal_span_id ∈ temporal menu
measurement_span_id ∈ measurement menu
```

无候选时必须输出 `null`；fallback candidate 必须携带 reason。V11 单独报告：

```text
candidate_menu_violation_count
fallback_selection_count
fallback_without_reason
legacy_role_ineligible_binding_count
```

其中 legacy V10 eligibility 用于对照；V11 blocking 语义是 menu 外引用。

## 3. 本地与远程验证

本地：

```text
ruff check V11 modules/tests: PASS
mypy V11 modules: PASS
pytest 全量: 252 passed, 43 skipped
bash -n remote runner: PASS
```

远程 `.venv-v11`：

```text
compileall V11 modules: PASS
pytest V8/V9/V10/V11: 34 passed
```

首轮执行中修复的工程问题：

1. Snapshot 允许某个 unit 的 live candidate 为空，避免把零覆盖误判为 fixture invalid；
2. Structural seed evaluator 不再因 suggested span 不在截断菜单中触发 `StopIteration`；
3. Statement verifier 的 Literal verdict 不再调用 enum `.value`；
4. Macro 输出空 claims 时必须显式 `coverage_gap_suspected=true` 和 reason；
5. Macro unit-level schema failure 不再变成整个 runner 未捕获异常；
6. V11 fallback selection 与 legacy role-ineligible 指标分离；
7. Relation cold stability 按 record 聚合三次结果，而不是只看最后一次；
8. Downstream gold regression 补充 canonical direct recall。

部分 Python 进程退出阶段仍可能出现 `multiprocess.resource_tracker` destructor warning。警告发生在检查和实验完成之后，退出码为 0；若发生在检查过程中，必须单独归因。

## 4. Quick control

Ideal quick control 报告：

```text
.data/evaluations/input-preprocessing-v11/quick/v11-20260831-143532-2429185.json
sha256=52f07da86333dc76230711892b26f9babf0505fb85377fc352cb018cee468bf4
```

最终 live-snapshot quick control：

```text
.data/evaluations/input-preprocessing-v11/quick-v3/v11-20260831-154201-2466995.json
sha256=ea02168dbc4ac7f1dab8683efd11168b81ef9e9d8c24d715333fc475e3215ea4
```

结果：

```text
SNAP-INTEGRITY: PASS
VIEW-COVERAGE: completed
DOWNSTREAM-GOLD: completed
EARLY-FAILURE: completed
NEG-V11: 8/8 expected blocked, false_pass=0
ASYNC-V11: queue full / dead letter / trace completeness 有效
HELD-OUT-V11: blocked，heldout_read_count=0
```

Ideal snapshot：

```text
candidate_count=68
exact_field_recall=1.0
offset_valid_rate=1.0
text_match_rate=1.0
role_coverage=1.0
```

Ideal view：

| view | gold in view | candidate count | empty menu |
|---|---:|---:|---:|
| global | 0.9390 | 935 | 0 |
| role primary | 1.0 | 72 | 122 |
| role + fallback | 1.0 | 660 | 0 |
| claim-local | 1.0 | 660 | 0 |

说明 view / menu / seed 的工具链在 golden pool 下可执行。

## 5. Live snapshot integrity

报告：

```text
.data/evaluations/input-preprocessing-v11/snapshot-v2/v11-20260831-143803-2430405.json
sha256=11eb804ffa028d7e184c3fcf1142f0ad58ff92f85d07e53c854de5414edbb079
```

```text
candidate_count = 935
fixture_field_count = 82
exact_field_recall = 0.6585365853658537
role_coverage = 0.5121951219512195
offset_valid_rate = 1.0
text_match_rate = 1.0
unambiguous_label_accuracy = 0.22535211267605634
```

该结果复现 V10 full calibrated pool 的 recall / role coverage，证明 snapshot 固化成功。

V11 的 `boundary_precision=0.0471` 使用：

```text
matched unique candidate / all 935 candidates
```

它与 V10部分表格的候选口径不同，因此只作为 snapshot 审计值，不替代 V8 gate。

SpanGraph：

```text
node_count = 952
edge_count = 68199
region_count = 17
graph_edge_valid_rate = 1.0
graph_latency_ms = 431
```

图为 in-memory NetworkX control，未引入图数据库或持久服务。

## 6. Live view coverage

报告：

```text
top5:
.data/evaluations/input-preprocessing-v11/view-live-base-top5/v11-20260831-145235-2438319.json
sha256=c4d45a76204c651574dcc8df4998516ce3ccd61a7909c30382de4083a5c45e8b

top16:
.data/evaluations/input-preprocessing-v11/view-live-base-top16/v11-20260831-145304-2438621.json
sha256=41518e2ad34cbda7faec09a5e740350cb966785968da3e94618f9302c477ac28
```

| top-k | view | gold in view | candidate count | empty menu |
|---:|---|---:|---:|---:|
| 5 | global | 0.1829 | 825 | 22 |
| 5 | role primary | 0.1707 | 490 | 85 |
| 5 | role + fallback | 0.1951 | 550 | 44 |
| 5 | claim-local | 0.1951 | 550 | 44 |
| 16 | global | 0.1951 | 2640 | 22 |
| 16 | role primary | 0.1951 | 1557 | 85 |
| 16 | role + fallback | 0.2195 | 1639 | 44 |
| 16 | claim-local | 0.2195 | 1639 | 44 |

结论：

1. claim-local view 能降低输入规模；
2. 但在当前 base ranking 下，大量 gold candidate 排名靠后；
3. top16 后 gold in view 也只有 `0.2195`；
4. live view 未达到 `gold_in_view >= global full pool` 的目标。

## 7. RANK-BASE / RANK-CROSS / RANK-BUDGET

报告：

```text
.data/evaluations/input-preprocessing-v11/rank-base-cross-prefilter48/v11-20260831-145200-2430919.json
sha256=d8a7f289df8eeb0b1c9d422fefbad7741860519dbc6db83b03b15dbb6e96f13f
```

固定：

```text
snapshot = v10-candidates.json
prefilter = 48
ranker base = deterministic V11 structural score
ranker cross = BAAI/bge-reranker-base
revision = 2cfc18c9415c912f9d8155881c133215df768a70
device = cpu
```

| metric | base | cross |
|---|---:|---:|
| primary gold rate | 0.5122 | 0.3902 |
| fallback gold rate | 0.1463 | 0.0244 |
| gold in top 1 | 0.1707 | 0.0732 |
| gold in top 3 | 0.1951 | 0.0976 |
| gold in top 5 | 0.1951 | 0.1341 |
| gold in top 16 | 0.2195 | 0.2073 |
| precision@1 | 0.1707 | 0.0732 |
| precision@16 | 0.2317 | 0.2195 |
| role coverage@16 | 0.1951 | 0.1829 |
| rerank wall latency | deterministic / negligible | 798253 ms |

结论：

1. BGE reranker 没有把正确候选排到 top-k；
2. cross rank 在 top-1 / top-3 / top-5 / top-16 均低于 base；
3. CPU rerank 延迟约 `13.3` 分钟，不可接受；
4. 当前 BGE reranker 不能作为 V11 finalist；
5. 主要 blocker 不是“缺少语义 reranker”，而是候选表示、role query 和 ranking 信号本身。

Budget 报告：

```text
.data/evaluations/input-preprocessing-v11/budget-live-base-top16/v11-20260831-145331-2438868.json
sha256=4292ed7388465b50e6adef49180d7e3f19563c22d1116a2616cb6f0acbfd99c5
```

```text
selected_top_k = 16
selected_menu_count = 187
selected_candidate_count = 2992
gold_in_top_16 = 0.21951219512195122
precision_at_16 = 0.23170731707317074
```

因此简单 top-k budget 不能保留足够 gold。

## 8. Structural seeds

Live report：

```text
.data/evaluations/input-preprocessing-v11/seeds-live-base-top16-v2/v11-20260831-145440-2439601.json
sha256=3d35ae9aa290d4626f7473bcc3fc59a2b00ce43038897c8a0c3f9cad3bd82085
```

```text
seed_recall = 0.0
shared_seed_recall = 0.0
action_seed_recall = 0.0
seed_precision = 0.0
shared_relation_inheritance_rate = 0.6
claim_id_stability = 1.0
```

Ideal control：

```text
seed_recall = 1.0
shared_seed_recall = 0.8
action_seed_recall = 0.6
seed_precision = 0.4831
claim_id_stability = 1.0
```

结论：

1. deterministic seed ID 和 seed evaluator 可执行；
2. ideal pool 下 seed 机制可恢复大部分 gold skeleton；
3. live pool 下 base ranking 选择的 support/target 均错，seed recall 为 0；
4. 在 ranking 修复前，不应继续 live macro integration。

## 9. Macro seeded one-call

### 9.1 Ideal golden pool

报告：

```text
.data/evaluations/input-preprocessing-v11/macro-ideal-base-top3-v2/v11-20260831-151826-2451310.json
sha256=aa09975be955132d1c63977495a21af7eb5e28bfcd56df2e78783f69de331aeb
```

```text
mean act expected = 1.3333
mean act output = 3.0
act precision = 0.3333
act recall = 0.8333

mean claim expected = 5.3333
mean claim output = 5.6667
claim precision = 0.3131
claim recall = 0.3222
statement_type_accuracy = 0.3222
binding_accuracy = 1.0
support_envelope_valid_rate = 1.0

invalid_span_reference = 0
invalid_span_binding = 0
unseeded_claim_count = 0
model_free_quote_output = 0
candidate_menu_violation_count = 2.6667
fallback_selection_count = 8.3333
fallback_without_reason = 0
```

结论：

1. V11 one-call seeded schema 可执行；
2. 稳定 seed ID 使 matched claim 的 binding accuracy 达到 `1.0`；
3. invalid reference / invalid binding / free quote 保持为 0；
4. act precision、claim skeleton、statement type 仍明显漂移；
5. candidate menu violation 未清零。

### 9.2 Statement verifier

```text
verified_claim_count = 3
verifier_model_call_count = 3
verifier_correction_accuracy = 0.6667
denies_as_reports = 0
normal_as_no_change = 0
```

该 verifier 是 report-only，不自动改写 statement type。

### 9.3 Live candidate view

报告：

```text
.data/evaluations/input-preprocessing-v11/macro-live-base-top3-answer-fact-v3/v11-20260831-152754-2458748.json
sha256=4244ade35acbc47706a234be77aa5d58a05655c3a87f01721c7d849e9f0fc02c
```

live structural seed：

```text
seed_recall = 0.0
```

Macro unit 结果：

```text
status = failed
failure_attribution = schema_adapter_failure
reason = empty_claims_require_coverage_gap
```

即模型在 live view 下输出空 acts / 空 Claims，且没有填写 coverage gap，被 strict schema 阻断。这是正确阻断，不能放宽 schema 或让模型自由生成 claim。

## 10. Relation cold stability

Batch 1：

```text
.data/evaluations/input-preprocessing-v11/relation-cold3-fewshot-batch1/v11-20260831-151919-2453421.json
sha256=28fcea8e01552bd990db79739d12bba3de31fa37ea09926420d5124b0c3ee7a1
```

```text
cold_run_count = 3
cache_hit_count = 0
relation_accuracy = 1.0
signature_stability = 1.0
stable_and_correct_rate = 1.0
unclear_rate = 0.0
format_error_count = 0
p50_ms = 1117
p95_ms = 1337
model_call_count = 18
```

Batch 4 forward：

```text
.data/evaluations/input-preprocessing-v11/relation-cold3-fewshot-batch4-forward/v11-20260831-151953-2453823.json
sha256=7fda84902f61f34df659d91c7d89d701b2ef3370ec6bb48662f72142c02f8f01
```

```text
relation_accuracy = 1.0
signature_stability = 1.0
stable_and_correct_rate = 1.0
format_error_count = 0
p50_ms = 1795
p95_ms = 2553
model_call_count = 6
```

Batch 4 reverse：

```text
.data/evaluations/input-preprocessing-v11/relation-cold3-fewshot-batch4-reverse/v11-20260831-152020-2454079.json
sha256=fe72abafd81dbdc7d27b9e86d7997d991a02f9a2b3da19189b1e1c205481d201
```

```text
relation_accuracy = 1.0
signature_stability = 1.0
stable_and_correct_rate = 1.0
format_error_count = 0
p50_ms = 1740
p95_ms = 3175
model_call_count = 6
```

结论：

1. V10 fixed few-shot relation contract 在 V11 中通过三次冷调用；
2. batch order 不再改变结果；
3. batch 4 调用次数更少，但单次延迟更高；
4. 该结果仍是 development diagnostic control，尚未接触 held-out；
5. 在 V11 版本冻结前，不能宣布生产 winner。

## 11. Winner regression

报告：

```text
.data/evaluations/input-preprocessing-v11/regression-gold-v2/v11-20260831-153218-2461355.json
sha256=a020cfb48ea503287949ef8968bbfac55a2f502eba669227854d1e3eaf8cb7fd
```

```text
CANONICAL:
  candidate_recall = 1.0
  canonical_accuracy = 1.0

TEMPORAL:
  temporal_binding_accuracy = 1.0

MEASUREMENT:
  measurement_binding_accuracy = 1.0

PARTICIPANT:
  participant_resolution_accuracy = 0.9286
```

`DOWNSTREAM-LIVE` 保持 blocked：

```text
target_span_availability = 0
relation_span_availability = 0
participant_span_availability = 0
reason = await_v11_macro_golden_gate
```

结论：gold winner 仍可消费输入；live downstream 的首要 blocker 是 macro seed / skeleton / binding。

## 12. REP / NEG / ASYNC / early exit

REP：

```text
.data/evaluations/input-preprocessing-v11/rep-macro-ideal-base-top3/v11-20260831-152224-2454443.json
sha256=f7bffa15786965a808f1d11a1a281a9b2163aa8fb37ec6c926d5a300520f19ee
```

```text
cold_run_count = 3
cache_hit_count = 0
unique_output_count = 3
raw_output_stability = 0.3333
semantic_claim_stability = 0.6667
semantic_binding_stability = 0.3333
p50_ms = 33649
p95_ms = 37058
```

Macro REP 未达到三次一致。

NEG-V11：

```text
mutation_count = 8
gate_blocked_as_expected = 8
false_pass = 0
gate_blocked_as_expected_rate = 1.0
```

覆盖：

```text
menu 外引用
模型自由 quote
新增无 seed claim
fallback 无 reason
空菜单伪造 span
target 不在 support 内
reranker 修改 offset/text
early exit 误解释上游失败
```

ASYNC-V11：

```text
submitted_count = 2
accepted_count = 1
queue_full_count = 1
dead_letter_count = 1
trace_completeness = 1.0
```

Early-exit：

```text
false_early_exit = 0
safety_path_preserved_rate = 1.0
failure_case_count = 4
downstream_call_count = 0
blocked_reason_correct_rate = 1.0
```

## 13. 当前根因更新

1. **Live candidate ranking 是首要 blocker**
   - full snapshot 有 `0.6585` exact recall；
   - base top16 view 只保留 `0.2195` gold；
   - BGE reranker 进一步降低 top-k gold 且延迟过高。

2. **Structural seed 被 ranking blocker 阻断**
   - ideal seed recall 为 `1.0`；
   - live seed recall 为 `0.0`。

3. **Macro schema / gate 有效，但语义质量未达标**
   - ideal pool 下 invalid reference / binding / free quote 为 0；
   - claim precision / statement type 仍约 `0.32`；
   - live view 输出空 claims 被 schema 阻断。

4. **Relation fixed contract 已达到 development 3/3**
   - batch 1 / batch 4 forward / reverse 均稳定正确；
   - 仍需版本冻结和确认性验证。

5. **Canonical / temporal / measurement 不是当前 blocker**
   - gold 输入下均通过；
   - participant resolver 为 `0.9286`，仍优于 live macro availability。

6. **Macro REP 仍不稳定**
   - raw / binding stability 仅 `1/3`；
   - semantic claim stability `2/3`。

## 14. 下一步

1. 停止继续扩大 BGE reranker 矩阵；
2. 回到 candidate representation / deterministic ranking：
   - 按 claim region 先选 support anchor；
   - 对 support 内候选做 role-local ranking；
   - 避免 17 regions × 11 roles 的全量 role pair；
   - 用 length、containment、parser provenance、parent score 和 boundary confidence 组合排序；
3. 修复 live structural seed：
   - 先保证 gold support anchor 进入 view；
   - 再生成 target seed；
   - seed 未命中时显式 coverage gap；
4. 修复 macro act precision 和 statement type；
5. 保留 relation frozen few-shot contract，等待整体 finalist 冻结；
6. REP 只在 macro golden 达标后重跑；
7. held-out 与 DSPy 继续冻结。

## 15. 复现命令

Quick：

```bash
scripts/integration/run-input-preprocessing-v11-remote-runner.sh \
  --suite quick \
  --mode quick \
  --no-cache \
  --output-dir .data/evaluations/input-preprocessing-v11/quick
```

构建 live snapshot：

```bash
scripts/integration/run-input-preprocessing-v11-remote-runner.sh \
  --suite snapshot \
  --mode shadow \
  --build-snapshot \
  --no-cache \
  --output-dir .data/evaluations/input-preprocessing-v11/snapshot
```

View：

```bash
scripts/integration/run-input-preprocessing-v11-remote-runner.sh \
  --suite view \
  --mode shadow \
  --rank-mode base \
  --top-k 16 \
  --no-cache \
  --output-dir .data/evaluations/input-preprocessing-v11/view-live-base-top16
```

Rank：

```bash
INPUT_PREPROCESSING_V11_REMOTE_TIMEOUT_SECONDS=1800 \
scripts/integration/run-input-preprocessing-v11-remote-runner.sh \
  --suite rank \
  --mode shadow \
  --rank-mode both \
  --prefilter 48 \
  --no-cache \
  --output-dir .data/evaluations/input-preprocessing-v11/rank-base-cross-prefilter48
```

Macro ideal control：

```bash
INPUT_PREPROCESSING_V11_REQUEST_TIMEOUT_SECONDS=180 \
scripts/integration/run-input-preprocessing-v11-remote-runner.sh \
  --suite macro \
  --mode shadow \
  --rank-mode base \
  --top-k 3 \
  --target-top-k 5 \
  --snapshot .data/evaluations/input-preprocessing-v11/snapshots/ideal-golden.json \
  --no-cache \
  --output-dir .data/evaluations/input-preprocessing-v11/macro-ideal-base-top3
```

Relation cold：

```bash
scripts/integration/run-input-preprocessing-v11-remote-runner.sh \
  --suite relation \
  --mode cold \
  --relation-fewshot \
  --batch-size 1 \
  --relation-runs 3 \
  --no-cache \
  --output-dir .data/evaluations/input-preprocessing-v11/relation-cold3-fewshot-batch1
```

## 16. 安全边界

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

所有 V11 snapshot / view / reranker / seed / macro / relation 结果不得：

```text
进入 production projection
作为生产 fallback
解除 V8 live phase admission
替代 live span gate
接触 held-out
触发 DSPy 优化
```

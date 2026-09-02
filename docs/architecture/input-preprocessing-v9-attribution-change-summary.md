<!--
=============================================================================
文件: input-preprocessing-v9-attribution-change-summary.md
作用: 记录第九轮 input-preprocessing 专项归因 runner、远程实验结果与根因结论。
范围: 覆盖 gold/evaluator integrity、GLiNER label/threshold、macro ideal-pool、
      relation/canonical/participant gold injection、calibration batch 与冷启动重复。
说明: 本文证明 V9 归因链路可执行并给出当前 blocker 归因；不解除 V8 phase gate，
      不构成生产消费准入。
维护: 当 V9 runner、fixture、报告或归因结论变化时同步更新。
=============================================================================
-->

# Input preprocessing V9 专项归因实验记录

> **结论**：第九轮专项归因 runner 已实现并在远程环境完成核心实验。当前 V8 blocker 被拆成四类：measurement / fixture 口径问题、GLiNER boundary + label 能力问题、macro prompt 输出 acts 与 claim signature 失败、V7 relation 的 batch 上下文敏感性。V6 canonical 与 participant resolver 在 gold 输入下可用，当前不是首要 blocker。
>
> **准入结论**：所有 V9 报告均为 `diagnostic_only=true`、`can_unblock_v8_phase=false`。V8 live Phase 0 gate 仍未通过，任何 V9 golden 结果都不能替代 live span 或 macro 准入。

## 1. 实现范围

新增：

```text
src/vet_agent/input_preprocessing/v9_attribution.py
src/vet_agent/input_preprocessing/v9_experiments.py
scripts/integration/run-input-preprocessing-v9-attribution.sh
docs/architecture/input-preprocessing-v9-attribution-experiment-plan.md
tests/test_input_preprocessing_v9.py
```

V9 支持：

```text
ATT-GOLD-INTEGRITY
ATT-SPAN-POOL
ATT-MACRO-IDEAL-POOL
ATT-RELATION-GOLD
ATT-CANONICAL-GOLD
ATT-PARTICIPANT-GOLD
ATT-REP-DETERMINISM
ATT-ADAPTER-COLD
```

CLI suite：

```text
interface
span-label
span-threshold
macro
downstream
relation-calibration
adapter
rep
gold
all
```

报告版本：

```text
v9-attribution-report-1
```

顶层报告显式包含：

```text
diagnostic_only=true
can_unblock_v8_phase=false
changed_variables
matrix sha256
model / prompt / schema version
cache status
safety boundary
```

runner 拒绝读取文件名包含 `held_out` 的 fixture。DSPy 仍保持冻结。

## 2. 本地与远程验证

本地：

```text
ruff check: PASS
mypy V8/V9 相关模块: PASS
pytest 全量: 288 passed, 43 skipped
```

远程：

```text
compileall V9 modules: PASS
pytest V8/V9: 20 passed
strict V8 environment smoke: PASS
```

## 3. ATT-GOLD-INTEGRITY

报告：

```text
.data/evaluations/input-preprocessing-v9-attribution/interface/v9-20260828-161448-121924.json
sha256=85ca6c82bd3904ae3d2373c096f07e125dc620906155dae3da196038627b8e0e
```

结果：

```text
required_field_count = 80
unique_boundary_count = 61
wrong_occurrence_count = 2
support_containment_violation_count = 0
conflicting_label_boundary_count = 5
label_evaluable_field_count = 69
label_evaluable_rate = 0.8625
```

### 归因

1. V8 ideal pool 的全文第一次出现定位确实会把重复 `"它"` 绑定到错误 owner occurrence；
2. 5 个 boundary 被多个 role 要求不同 expected label，其中包含：
   - evidence / support 同 boundary 期望 `state_mention` 与 `action_event`；
   - target / measurement 同 boundary 期望 `target_mention` 与 `measurement_expression`；
   - subject / recipient 同 boundary 期望 `subject_mention` 与 `recipient_mention`；
3. 因此旧 `label_accuracy=0` 不能全部解释为 GLiNER 语义失败，必须先拆分 boundary、field 与 label 口径；
4. V9 owner-scoped pool 已修正重复 mention 的诊断定位，但正式 V8 fixture 仍应升级为显式 offset 或 owner-relative locator。

## 4. ATT-SPAN-POOL

### 4.1 Label mode 对照

固定：

```text
model = gliner-community/gliner_small-v2.5
revision = f227d3cd637bd4e6757ae143935316d062393341
profile = staged
threshold = 0.3
```

报告：

```text
.data/evaluations/input-preprocessing-v9-attribution/span-label/v9-20260828-161550-122106.json
sha256=4fc4baf01732053035f1e65725d8687bd146d3489a2996d26ca8be8328eb06cb
```

| label mode | predicted | exact | precision | recall | label accuracy on exact | near-or-exact |
|---|---:|---:|---:|---:|---:|---:|
| english | 11 | 7 | 0.4545 | 0.0875 | 0.0 | 0.55 |
| bilingual | 14 | 10 | 0.5 | 0.125 | 0.2 | 0.475 |
| descriptive | 13 | 7 | 0.4615 | 0.0875 | 0.4286 | 0.4875 |

### 4.2 Bilingual threshold 对照

报告：

```text
.data/evaluations/input-preprocessing-v9-attribution/span-threshold-bilingual-v2/v9-20260828-161829-123720.json
sha256=d56add20a2d113b192de0885ee3af0c90776a488b9cdc71a3603f9965d723e87
```

| threshold | predicted | exact | precision | recall | label accuracy on exact | near-or-exact |
|---|---:|---:|---:|---:|---:|---:|
| 0.1 | 28 | 15 | 0.3571 | 0.1875 | 0.1333 | 0.675 |
| 0.2 | 19 | 12 | 0.4211 | 0.15 | 0.1667 | 0.575 |
| 0.3 | 14 | 10 | 0.5 | 0.125 | 0.2 | 0.475 |

### 归因

1. 降低 threshold 确实提升 boundary recall，但 precision 降到 `0.3571`，远低于 `0.75` gate；
2. threshold 0.1 时 near-or-exact 达到 `0.675`，说明大量预测与 gold 有重叠但边界不精确；
3. primary blocker 不只是过度过滤，还包括：
   - boundary 策略；
   - candidate ranking；
   - 中文口语 label 语义；
   - 当前模型能力；
4. descriptive label 在少量 exact match 上 label accuracy 较高，但 exact 样本少，不能证明稳定改善；
5. 不能通过 relaxed overlap 通过正式 gate。

## 5. ATT-MACRO-IDEAL-POOL

使用三个代表性 development units：

```text
macro-answer-fact
macro-shared-scope
macro-action-roles
```

同一 owner-scoped gold pool 分别使用：

```text
opaque span IDs
role-hinted span IDs
```

报告：

```text
.data/evaluations/input-preprocessing-v9-attribution/macro-pool/v9-20260828-162050-124272.json
sha256=6c7b3eae3280336ca534be32842aec2b677f673cb712dd906045e16406d61331
```

| pool | raw acts | expected acts | matched acts | raw claims | governed claims | expected claims | matched claims | invalid bindings |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| opaque | 0 | 4 | 0 | 6 | 3 | 16 | 1 | 10 |
| role-hinted | 0 | 4 | 0 | 9 | 6 | 16 | 3 | 10 |

### 归因

1. 所有 macro 调用都没有输出 discourse act，说明 act 失败不是 live span pool 缺失导致；
2. role-hinted IDs 比 opaque IDs 多匹配 2 个 claim，但整体仍只有 `3/16`，没有证据表明仅靠 ID 泄漏可以解决 macro；
3. claim 输出数量、governed claim 数量与 expected 数量差距很大，claim granularity / skeleton 输出仍是独立 blocker；
4. invalid binding 均为 10，optional span binding 和 support envelope 规则需要专项修复；
5. 本实验仍保持一次宏观调用，没有拆成 act / claim / binding 多模型任务。

## 6. Gold injection

### 6.1 Relation

未加 V7 calibration batch 的报告：

```text
.data/evaluations/input-preprocessing-v9-attribution/downstream-gold/v9-20260828-162127-125565.json
sha256=0a958bdd5f9f7baa83f047e0f2ea85a396844ddc98cf772fc3f00276da99edc5
```

结果：

```text
gold_relation_field_count = 6
gold_relation_span_missing_count = 2
relation_input_availability = 0.6667
relation_accuracy = 0.0
```

其中 4 条可输入记录全部被 V7 classifier 输出为：

```text
unclear
```

同一远程环境执行 V7 原始 `RELATION-GOLDEN` control：

```text
.data/evaluations/input-preprocessing-v9-attribution/v7-relation-control/input-preprocessing-v7-619b5932351d.json
sha256=620145ea54ba4b5ff0c64ba48794e6a95d5f3775f9fe92d1f6ec908b8e660cb4
```

结果：

```text
unit_count = 8
relation_accuracy = 1.0
```

加入 V7 calibration units 后：

```text
.data/evaluations/input-preprocessing-v9-attribution/relation-calibration/v9-20260828-162956-130672.json
sha256=c147af7abf5273e533eb5e9fde1ab04b320b58afb94d908253258bfd30ea9d9a
```

结果：

```text
calibration_relation_accuracy = 1.0
V8 relation_accuracy = 1.0
relation_input_availability = 0.6667
```

### 归因

1. V7 relation winner 本身仍可复现 `8/8`；
2. 单独发送 V8 gold relation records 时输出全 unclear，说明该 adapter 对 batch / prompt calibration 上下文敏感；
3. 加入 V7 calibration units 后 V8 gold records 全部正确，证明这不是底层模型能力完全失效，而是调用契约或 prompt 上下文问题；
4. V8 fixture 有 2 条 claim 只有 `expected_relation` 而没有 `relation_quote`，应显式视为 fixture / 契约缺口；
5. 不能把 calibration units 伪装成生产 fallback；后续应修复 V8→V7 relation adapter 的稳定调用契约或 prompt 版本。

### 6.2 Canonical

Gold target quote 注入 V6 canonical direct recall：

```text
gold_target_field_count = 2
candidate_recall = 1.0
canonical_accuracy = 1.0
no_candidate_count = 0
under_confirmation_count = 0
```

结论：在 gold target 可用时，V6 canonical direct recall 与 embedding 链路可复现，当前首要 blocker 是 macro target binding，而不是 canonical recall。

### 6.3 Participant

Gold participant mention 注入 TurnContext resolver：

```text
gold_participant_field_count = 14
participant_resolution_accuracy = 1.0
resolved_empty_count = 0
```

结论：participant resolver 可消费正确 owner-scoped mention；当前失败主要来自 macro participant span 选择 / binding，而不是 entity resolver。

## 7. ATT-REP-DETERMINISM

报告：

```text
.data/evaluations/input-preprocessing-v9-attribution/rep-macro-answer-fact/v9-20260828-162358-127000.json
sha256=a3e5814844ca2ea678272d99c43a10d2b13d6d531c203c3f06c82e800e39f939
```

结果：

```text
cold_run_count = 3
cache_hit_count = 0
raw_output_stability = 0.3333
act_signature_stability = 1.0
claim_signature_stability = 0.3333
binding_signature_stability = 0.3333
unique_raw_output_count = 3
p50_ms = 1146
p95_ms = 12363
```

### 归因

1. act signature 稳定是因为三次都输出空 acts，不代表 act 质量稳定；
2. claim 与 binding signature 三次各不相同，是不稳定的主要来源；
3. p50 与 p95 差距显著，仍需在语义修复后重复测量；
4. cache replay 仍不得计入稳定性证据。

## 8. 当前根因排序

1. **Measurement / fixture**
   - 重复 mention owner 定位错误；
   - 同 boundary 多 expected label；
   - 2 条 relation expectation 缺 relation span。
2. **Live span extractor**
   - exact recall 最高仅 `0.1875`；
   - threshold 降低后 precision 崩溃；
   - near boundary 比例高，boundary / ranking 问题明显。
3. **Macro semantic output**
   - acts 全部为空；
   - claim skeleton / granularity 不稳定；
   - optional binding 与 support envelope invalid 多。
4. **Relation adapter contract**
   - V7 winner 在原始 calibration set 上正常；
   - V8 gold records 单独发送全 unclear；
   - calibration batch 后恢复正常，需显式设计稳定调用契约。
5. **Canonical / participant**
   - gold 输入下均通过，当前不是首要 blocker。

## 9. 下一步

1. 将 development fixture 从 quote-first 升级为显式 offset / owner-relative locator；
2. 拆分 V8 正式指标：
   - boundary coverage；
   - field coverage；
   - unambiguous label accuracy；
   - role binding；
3. 继续 span boundary / model 对照，特别是 near-boundary candidate 的 deterministic expansion 策略；
4. 修复 macro prompt 的 act 与 claim skeleton 输出，不拆宏观调用；
5. 为 V8→V7 relation adapter 建立稳定 batch / prompt 契约，并补齐缺失 relation span fixture；
6. macro golden 达标后再执行 `ATT-ADAPTER-COLD`；
7. 所有 live V8 phase gate 保持不变。

## 10. 安全边界

本轮所有实现与远程命令保持：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
```

未接入：

```text
VetOrchestrator
ClinicalSafetyEvaluator
clinical safety pgvector
required_context
clinical safety OPA
```

V9 golden / gold injection 结果不得进入 production projection，不得作为生产 fallback，也不得解除 V8 phase admission。

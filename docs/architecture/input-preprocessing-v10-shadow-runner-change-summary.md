<!--
=============================================================================
文件: input-preprocessing-v10-shadow-runner-change-summary.md
作用: 记录第十轮 input-preprocessing 边界校准、宏观契约修复、relation 稳定化、
      winner 回归、early-exit 与快速验证的实现和远程结果。
范围: 覆盖 explicit-offset fixture、field/role 指标拆分、GLiNER coarse locator、
      deterministic boundary calibration、candidate budget、V10 macro one-call schema、
      fixed relation contract、canonical/participant regression、continuation gate、
      NEG/ASYNC/REP 与 held-out 防护。
说明: 本文证明 V10 探索性 shadow runner 和 quick control 可执行，并记录当前质量
      blocker；不解除 V8 live phase gate，不构成生产消费准入。
维护: 当 V10 runner、fixture、报告、模型 snapshot 或实验结论变化时同步更新。
=============================================================================
-->

# Input preprocessing V10 shadow runner 与快速验证记录

> **结论**：V10 修复轮 runner 已实现并在远程 `.venv-v10` 完成核心 quick / exploratory shadow 验证。Phase 0 测量修复通过：explicit offset、owner occurrence、field/role 拆分、relation span 完整性、interface audit、`NEG-V10` 与 `ASYNC-V10` 均通过。Boundary calibration 证明 GLiNER 可作为 coarse locator：small 模型在 threshold `0.1` 下 exact field recall 从 `0.183` 提升到 `0.659`，但未预算候选池 precision 为 `0.102`；修复后的 stratified role-aware budget 在 top-k 16 / per-turn cap 192 下保留 `0.659` recall，role coverage 提升到 `0.512`，precision 为 `0.124`，仍未达到 V8 Phase 0 gate。V10 macro 修复使 acts 不再默认为空，最新 act precision / recall 达到 `0.833 / 1.0` 且 invalid binding 为 0，但 claim statement type、skeleton 与 role-eligible binding 仍明显漂移。固定 relation few-shot contract 在 development gold control 和 reverse-order batch 4 下均达到 `1.0`，但仍需冷启动重复确认，不能宣布 winner。REP-COLD raw / claim / binding signature 达到 `0.667`，仍未达到三次一致。
>
> **准入结论**：所有 V10 报告均为 `diagnostic_only=true`、`can_unblock_v8_phase=false`。V8 live Phase 0 gate 保持不变，held-out 与 DSPy 继续冻结。

## 1. 实现范围

新增：

```text
src/vet_agent/input_preprocessing/v10_contracts.py
src/vet_agent/input_preprocessing/v10_fixture.py
src/vet_agent/input_preprocessing/v10_boundary_calibration.py
src/vet_agent/input_preprocessing/v10_macro.py
src/vet_agent/input_preprocessing/v10_relation.py
src/vet_agent/input_preprocessing/v10_experiments.py
scripts/integration/run-input-preprocessing-v10-remote-runner.sh
tests/test_input_preprocessing_v10.py
tests/fixtures/input_preprocessing/tenth_round_boundary_calibration_development.json
```

Fixture：

```text
schema_version = v10-explicit-offset-1
sha256 = bae80294219a20472e720ed44660ea9f547434b3f6b10acb5610faa280edefd2
```

Runner 支持的 suite：

```text
quick
interface
span
macro
relation
regression
early
negative
async
rep
all
```

支持的主要实验 ID：

```text
FIXTURE-OFFSET
FIELD-ROLE-SPLIT
RELATION-SPAN-COMPLETE
INTERFACE-AUDIT

SPAN-RAW
SPAN-CALIBRATE
SPAN-BUDGET
SPAN-MODEL
SPANMARKER-CHINESE

MACRO-ACT
MACRO-SKELETON
MACRO-BINDING
MACRO-FULL
MACRO-CANDIDATE-LOAD

REL-SINGLE
REL-BATCH-FIXED
REL-VERSIONED-FEWSHOT
REL-MISSING

CAN-REGRESSION
PARTICIPANT-REGRESSION
TEMPORAL-MEASUREMENT-REGRESSION

EARLY-MINIMAL
EARLY-VOI
EARLY-BUDGET
EARLY-ROUTER
EARLY-FAILURE

REP-COLD
HELD-OUT-V10
NEG-V10
ASYNC-V10
```

报告版本：

```text
v10-experiment-report-1
```

顶层固定包含：

```text
diagnostic_only=true
can_unblock_v8_phase=false
phase / suite / mode / lane
changed_variables
matrix path / sha256
model / prompt / schema / relation prompt / calibration version
cache status
safety boundary
```

Runner 默认拒绝读取文件名包含 `held_out` 的 fixture。DSPy 继续冻结。

## 2. 核心设计边界

### 2.1 Explicit offset 是唯一权威定位

V10 fixture 中每个 expected field 显式包含：

```text
claim_owner
field_role
start
end
text
expected_label_candidates
occurrence_locator
source_block_id
```

规则：

1. `start/end` 是权威定位；
2. `text` 仅用于校验；
3. 重复 mention 使用 owner-relative occurrence；
4. 不允许全文第一次出现作为默认定位；
5. omitted participant 可以显式指向 support 外的 owner occurrence；
6. 同一 boundary 可服务多个 field role，不再计为 label conflict。

### 2.2 GLiNER 只做 coarse locator

Boundary calibration pipeline：

```text
GLiNER coarse span
→ punctuation / whitespace conservative trim
→ generic tokenizer boundary alignment
→ nested / overlapping candidate generation
→ deterministic temporal / measurement candidates
→ role eligibility
→ exact offset deduplication
→ role-aware pruning
→ bounded candidate pool
```

运行时输入不包含：

```text
expected_start
expected_end
gold quote
expected label
held-out 信息
```

禁止：

```text
同义词替换
编辑距离修复
embedding 相似修复
LLM 重写 quote
医学关键词裁剪
兽医边界词典 default
全局 NMS 删除必要嵌套 span
```

### 2.3 V10 macro 仍保持一次宏观调用

V10 schema 分为：

```text
A. discourse acts
B. claim skeleton
C. optional bindings
```

硬边界：

```text
所有证据和绑定只能引用 span_id
不输出自由 quote 字符串
acts 为空时必须输出 no_act_reason
每个 claim 必须有 support 和 target
optional binding 不确定时用 null
```

`MACRO-ACT`、`MACRO-SKELETON`、`MACRO-BINDING` 是指标拆分；`MACRO-FULL` 仍是同一次宏观调用，不是运行时微任务拆分。

### 2.4 Relation adapter 使用固定版本化契约

Relation adapter 固定：

```text
serialization format
field order
batch size
missing field representation
prompt version
```

Few-shot 只能作为 frozen prompt version：

```text
v10-relation-fixed-contract-dev-20260830-1:fewshot-on
```

禁止：

```text
运行时动态 calibration
根据失败自动加 few-shot
把 calibration units 作为 fallback
使用 held-out
```

### 2.5 Continuation gate 是 report-only

每个组件记录：

```text
component
prerequisite_status
decision: execute / skip / early_exit / blocked
reason
```

Early-exit 实验只验证最短充分路径和失败传播，不接入生产，不绕过安全主路径，不把上游失败解释为用户未提供。

## 3. 本地与远程验证

本地：

```text
ruff check V10 modules/tests: PASS
mypy V10 modules: PASS
pytest tests/test_input_preprocessing_v10.py: 7 passed
pytest 全量: 245 passed, 43 skipped
```

远程 `.venv-v10`：

```text
ruff check V10 modules/tests: PASS
mypy V10 modules: PASS
pytest V8/V9/V10: 27 passed
```

远程环境修复：

```text
INPUT_PREPROCESSING_V10_GLINER_MULTI_PATH
由错误的 urchade__gliner_multi-v2.5 修正为
urchade__gliner_multi-v2.1
```

修正前已保留：

```text
<remote-repository-root>/.env.v10.local.bak-v10-20260830
```

该操作只修正本地模型路径，不改变模型权重或密钥权限。

## 3.1 工程问题修复与复测

首轮远程执行后修复了以下工程问题：

1. `MACRO-ACT` / `MACRO-SKELETON` / `MACRO-BINDING` 不再重复携带全量相同指标和全量 unit results；各自输出聚焦指标，仅 `MACRO-FULL` 保留完整 unit results。
2. V10 macro 增加 role eligibility governance：

   ```text
   role_ineligible_binding_count
   role_eligibility_violations
   ```

   该指标独立于 V8 invalid reference / containment gate，用于定位模型选择了存在但角色不匹配的 span。
3. Boundary calibration 增加真实 per-unit latency：

   ```text
   p50_ms
   p95_ms
   wall_latency_ms
   ```

4. Candidate budget 从简单 score 排序改为按 role / start 分层轮转，保留不同起始位置和边界长度替代项，避免 top-k 全部集中在最早短片段。
5. Nested coarse phrase 的子候选允许作为通用语言结构 micro-role 候选；最终仍由 V10 role governance 和 offset-backed quote governance 阻断错误绑定。
6. Relation runner 增加 `--relation-reverse-order`，可显式测试 batch order sensitivity。
7. Temporal / measurement regression 接入 V6 deterministic parser，并记录：

   ```text
   parser_status
   parser_unresolved_reason
   over_precision_count
   ```
8. V10 runner 未捕获异常时输出 `V10-RUNNER failed` 报告，而不是只留下 traceback。
9. 远程脚本增加可配置 timeout：

   ```text
   INPUT_PREPROCESSING_V10_REMOTE_TIMEOUT_SECONDS
   ```

   默认 `900` 秒，避免模型或依赖异常导致 SSH 长时间悬挂。
10. 修复 generic temporal parser 对以下常见中文起点表达的解析：

    ```text
    前天开始 -> day-2 / started_at
    ```

复测结果：

```text
本地 V10 tests: 8 passed
本地全量: 245 passed, 43 skipped
远程 V10 tests: 8 passed
远程 V8/V9/V10 tests: 28 passed
```

## 4. Quick control

报告：

```text
.data/evaluations/input-preprocessing-v10/quick/v10-20260830-150332-1653343.json
sha256=bb98a46ad3cbf446e79ec158d1ce59423bd8e320661e8edd43533f1dbbdc927a
```

### 4.1 FIXTURE-OFFSET

```text
fixture_field_count = 82
active_field_count = 82
unique_boundary_count = 63
multi_role_boundary_count = 7
offset_valid_rate = 1.0
text_match_rate = 1.0
owner_occurrence_valid_rate = 1.0
source_block_valid_rate = 1.0
migration_error_count = 0
```

### 4.2 FIELD-ROLE-SPLIT

```text
field_role_count = 82
unique_boundary_count = 63
multi_role_boundary_count = 7
unambiguous_label_field_count = 71
label_evaluable_rate = 0.8658536585
role_binding_expected_count = 76
roles_with_fields = 11
```

### 4.3 RELATION-SPAN-COMPLETE

```text
expected_relation_count = 6
relation_span_available_rate = 1.0
fixture_incomplete_relation_count = 0
```

V9 中发现的 2 条 relation expectation 缺 span 问题已在 development fixture 中补齐。

### 4.4 INTERFACE-AUDIT

```text
check_count = 4
passed_count = 4
gold_participant_field_count = 14
canonical_gold_record_count = 2
canonical_gold_recall_rate = 1.0
```

### 4.5 NEG-V10

```text
mutation_count = 10
gate_blocked_as_expected = 10
false_pass = 0
gate_blocked_as_expected_rate = 1.0
```

覆盖：

```text
offset/text mismatch
owner occurrence
candidate budget
role-ineligible binding
model free quote
missing relation classifier call
resolved participant empty
invented canonical
projection consuming blocked claim
early exit misinterpreting upstream failure
```

### 4.6 ASYNC-V10

```text
submitted_count = 2
accepted_count = 1
queue_full_count = 1
dead_letter_count = 1
trace_completeness = 1.0
main_link_latency_delta_ms = 0
main_link_error_rate_delta = 0.0
```

### 4.7 Early-exit control

```text
unit_count = 5
simple_lane_count = 1
continuation_record_count = 25
component_value_count = 12
no_value_component_count = 13
safety_path_preserved_rate = 1.0
```

`EARLY-FAILURE`：

```text
failure_case_count = 4
downstream_call_count = 0
false_pass_count = 0
blocked_reason_correct_rate = 1.0
```

## 5. SPAN-RAW / SPAN-CALIBRATE / SPAN-BUDGET

### 5.1 GLiNER small

固定：

```text
model = gliner-community/gliner_small-v2.5
revision = f227d3cd637bd4e6757ae143935316d062393341
threshold = 0.1
label mode = bilingual
tokenizer = bert-base-chinese snapshot
fixture = v10-explicit-offset-1
```

报告：

```text
.data/evaluations/input-preprocessing-v10/span-small/v10-20260830-150441-1653614.json
sha256=673b5b49a5913017a1057f06d2ae0a2b3a719f097a9476d240f10abe77c8c321
```

| Variant | candidates | exact recall | boundary precision | role coverage | unambiguous label accuracy | near-or-exact |
|---|---:|---:|---:|---:|---:|---:|
| A raw | 28 | 0.183 | 0.786 | 0.049 | 0.028 | 0.671 |
| B trim | 28 | 0.183 | 0.786 | 0.159 | 0.028 | 0.671 |
| C tokenizer | 28 | 0.183 | 0.786 | 0.159 | 0.028 | 0.671 |
| D nested | 926 | 0.585 | 0.094 | 0.293 | 0.141 | 0.671 |
| E + deterministic | 935 | 0.659 | 0.102 | 0.317 | 0.268 | 0.793 |
| F role eligibility | 935 | 0.659 | 0.102 | 0.244 | 0.268 | 0.793 |
| G budget | 96 | 0.366 | 0.365 | 0.159 | 0.169 | 0.793 |

结论：

1. near-boundary coarse 输出确实可以转成 exact-offset candidates；
2. nested generation 大幅提升 exact recall；
3. 未预算候选池产生候选爆炸并降低 precision；
4. role-aware budget 控制 candidate count 后，precision 回升但 recall 不足；
5. 当前组合仍远低于：

```text
required_field_coverage >= 0.90
precision >= 0.75
label_accuracy >= 0.75
```

### 5.2 GLiNER multilingual

固定：

```text
model = urchade/gliner_multi-v2.1
revision = 443d26d654e0324125a96bebd8e796c14ff2efe6
threshold = 0.1
label mode = bilingual
offline resize_token_embeddings=false
```

报告：

```text
.data/evaluations/input-preprocessing-v10/span-multi-t010/v10-20260830-150941-1656571.json
sha256=fcb18107406f4fe366abf921bb16963ddf762896b84da1ed1acbe58ee6a6568c
```

结果：

```text
SPAN-RAW:
  raw candidate count = 0
  boundary recall = 0.0

SPAN-CALIBRATE E/G:
  candidate count = 9
  exact fields = 10 / 82
  field coverage = 0.1219512195
  boundary precision = 0.8888888889
```

该 9 个候选主要来自 deterministic temporal / measurement expression，不是 GLiNER raw coarse span。当前 label mode / prompt / checkpoint 组合下，multilingual GLiNER 不能作为 coarse locator finalist。

### 5.3 SPANMARKER-CHINESE

```text
status = blocked
failure_attribution = middleware_not_configured
trained_model_count = 0
cross_validation_configured = 0
reason = await_explicit_offset_and_calibration_gate
```

SpanMarker 依赖和底座权重可用，但尚未训练，不因环境可用宣布 winner。

## 6. MACRO golden 修复

使用三个 representative units：

```text
macro-answer-fact
macro-shared-scope
macro-action-roles
```

报告：

```text
.data/evaluations/input-preprocessing-v10/macro-golden/v10-20260830-152150-1662191.json
sha256=d3d84a4781b8dbd49ec7b5685d27aff2ac549d28a4df56d3b955d1d05a3de302
```

整体：

```text
expected acts = 4
raw act output count = 10
act precision = 0.0
act recall = 0.0
empty act rate = 0.0
evidence span valid rate = 1.0
no_act_reason validity = 1.0

expected claims = 16
raw/governed claim output count = 16
claim precision = 0.3444444444
claim recall = 0.3444444444
statement type accuracy = 0.3444444444
support envelope valid rate = 1.0
binding accuracy = 0.1666666667

invalid span reference = 0
invalid span binding = 0
model free quote output = 0
```

分 unit：

| Unit | act precision | act recall | claim precision | claim recall | binding accuracy |
|---|---:|---:|---:|---:|---:|
| macro-answer-fact | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| macro-shared-scope | 0.0 | 0.0 | 0.7 | 0.7 | 0.0 |
| macro-action-roles | 0.0 | 0.0 | 0.3333 | 0.3333 | 0.5 |

归因：

1. V9 的“acts 全空”问题已修复：模型现在输出 acts，且 evidence span 全部有效；
2. 但 act 类型 / evidence 语义不匹配，act precision 与 recall 仍为 0；
3. claim skeleton 从 V9 representative units 的 `3/16` 提升到本轮 `约 5.5/16`，但仍未达标；
4. shared denied 拆分有改善，`macro-shared-scope` 达到 `7/10`；
5. `macro-answer-fact` 的 `reports_abnormal` / `denies` 被输出为 `reports`，statement type 漂移明显；
6. span-id-only 契约和 deterministic governance 有效，invalid reference / binding 为 0；
7. macro blocker 已从“ schema / 空输出”收敛到 act 定义、claim statement type 和 optional binding 语义。

## 7. Relation adapter

### 7.1 REL-SINGLE

报告：

```text
.data/evaluations/input-preprocessing-v10/relation-fixed/v10-20260830-152627-1666259.json
sha256=101aec31cfec424f18e01cc86baaf0c9d55deac30ddcb6c38e92969834aed9b0
```

```text
evaluable_record_count = 6
model_call_count = 6
relation_accuracy = 0.3333333333
unclear_rate = 0.0
format_error_count = 0
p95_latency_ms = 1315
```

错误主要是：

```text
expected absolute_status -> predicted no_change
expected change -> predicted no_change / unclear
```

### 7.2 REL-VERSIONED-FEWSHOT

报告：

```text
.data/evaluations/input-preprocessing-v10/relation-fixed-fewshot/v10-20260830-152706-1666665.json
sha256=8057ad0f7c1330c7f440dd79232365bf415c36149e48af09bc2c726db4ee4645
```

```text
evaluable_record_count = 6
model_call_count = 6
relation_accuracy = 0.8333333333
unclear_rate = 0.1666666667
format_error_count = 0
p95_latency_ms = 1521
```

5/6 正确。剩余错误：

```text
macro-long-input:claim-soft-stool
expected change
actual unclear
```

该 few-shot 是版本化静态 prompt，不是运行时动态 calibration fallback。

### 7.3 REL-BATCH-FIXED

报告：

```text
.data/evaluations/input-preprocessing-v10/relation-batch/v10-20260830-152749-1667106.json
sha256=6d4bce5b005f4bc5fac552ba2019785b2774debf498963cb81947d0245f5d63b
```

| Batch size | model calls | accuracy | unclear |
|---:|---:|---:|---:|
| 4 | 2 | 0.5 | 0.0 |
| 8 | 1 | 0.5 | 0.0 |

结论：

1. 固定契约消除 format error；
2. no-fewshot single/batch 对 `absolute_status` 与 `no_change` 语义仍不稳定；
3. frozen few-shot 明显改善，但 `0.833` 且存在 unclear，不能宣布 winner；
4. 下一步需要更清晰的枚举定义和重复冷调用，而不是动态 calibration。

## 8. Winner regression

报告：

```text
.data/evaluations/input-preprocessing-v10/regression-other-pet/v10-20260830-153107-1668749.json
sha256=385e39f430369b0f101763e6c7405037172550c860fa2cfe2566f199c146a4d0
```

使用：

```text
macro-other-pet
```

Macro：

```text
claim precision/recall = 0.5
invalid reference/binding = 0
support envelope valid rate = 1.0
```

CAN-REGRESSION：

```text
target_span_availability = 1.0
candidate_recall = 1.0
canonical_accuracy = 1.0
no_candidate_count = 0
under_confirmation_count = 0
```

PARTICIPANT-REGRESSION：

```text
participant_mention_recall = 1.0
role_assignment_accuracy = 1.0
entity_resolution_accuracy = 1.0
resolved_empty_count = 0
```

结论：

1. 当 macro target / participant span 可用时，V6 canonical recall 与 TurnContext resolver 可消费；
2. 当前首要 blocker 仍在 macro target / participant binding；
3. canonical / participant 不应先于 macro 修复被调整。

## 9. REP-COLD

报告：

```text
.data/evaluations/input-preprocessing-v10/rep-cold-v2/v10-20260830-153418-1670370.json
sha256=b0a60f49e9fa00a749844033e5fb3288a0583665c670be53831aee04e8067533
```

```text
cold_run_count = 3
cache_hit_count = 0
unique_output_count = 3
majority_agreement = 0.3333333333
raw_output_stability = 0.3333333333
act_signature_stability = 1.0
claim_signature_stability = 0.3333333333
binding_signature_stability = 0.6666666667
p50_ms = 21733
p95_ms = 22296
```

结论：

1. act signature 稳定不代表 act 语义正确；
2. claim skeleton 与 binding 仍不稳定；
3. REP 未达到三次一致目标；
4. cache replay 未参与。

## 10. 修复后复测报告

### 10.1 Quick control v2

报告：

```text
.data/evaluations/input-preprocessing-v10/quick-v2/v10-20260830-155926-1685397.json
sha256=dc96f5106b3c479c275e64c937c21121b8d7c951a9524588f8c209d14b732297
```

结果与首次 quick control 一致：

```text
FIXTURE-OFFSET: PASS
FIELD-ROLE-SPLIT: PASS
RELATION-SPAN-COMPLETE: PASS
INTERFACE-AUDIT: PASS
NEG-V10: 10/10 预期阻断，false_pass=0
ASYNC-V10: queue full / dead letter / trace completeness 均有效
HELD-OUT-V10: 保持 blocked，未读取 held-out
```

### 10.2 Boundary calibration v3

报告：

```text
.data/evaluations/input-preprocessing-v10/span-small-v3/v10-20260830-160159-1686529.json
sha256=3dedf9ee9f8e5b005c97a19d33b15df5510915627389902b3f4fc0a9ba5b954d
```

| Variant / budget | candidates | exact recall | precision | role coverage | label accuracy | p50 / p95 |
|---|---:|---:|---:|---:|---:|---:|
| E full pool | 935 | 0.659 | 0.102 | 0.317 | 0.268 | 452 / 1000 ms |
| F role eligibility | 935 | 0.659 | 0.102 | 0.512 | 0.268 | 421 / 476 ms |
| G top-k 8 | 344 | 0.500 | 0.148 | 0.341 | 0.211 | 422 / 460 ms |
| G top-k 12 | 489 | 0.622 | 0.143 | 0.476 | 0.254 | 442 / 467 ms |
| G top-k 16 | 621 | 0.659 | 0.124 | 0.512 | 0.268 | 431 / 480 ms |

修复后的 stratified budget 不再以牺牲近半 recall 换取 precision：top-k 16 保持 full pool 的 exact recall，并显著改善 role coverage。但 candidate count 和 precision 仍不达标，说明当前主要 blocker 已进一步收敛为：

```text
candidate ranking
role-aware precision
label correctness
候选池规模控制
```

### 10.3 Macro golden v2

报告：

```text
.data/evaluations/input-preprocessing-v10/macro-golden-v2/v10-20260830-160500-1687132.json
sha256=f2213d36a911cab8cfe7642e337799645fa23973db13a0f990877d11d99a78af
```

聚焦报告结果：

```text
MACRO-ACT
  act precision = 0.0
  act recall = 0.0
  empty act rate = 0.0
  evidence span valid rate = 1.0

MACRO-SKELETON
  claim precision = 0.3444
  claim recall = 0.3444
  statement type accuracy = 0.3444
  support envelope valid rate = 1.0
  invalid span reference = 0

MACRO-BINDING
  binding accuracy = 0.1667
  invalid span binding = 0
  role-ineligible binding = 24
```

新增 role governance 暴露出重要问题：模型虽然只引用存在的 span ID，且 quote governance 可解析，但有 24 次绑定选择了角色不匹配的 span。因此 V10 需要同时修复：

```text
semantic binding
role eligibility instruction
claim-local candidate view
```

### 10.4 Relation fixed few-shot v2

报告：

```text
.data/evaluations/input-preprocessing-v10/relation-fixed-fewshot-v2/v10-20260830-160526-1689290.json
sha256=6cc70bf41dfb02831a7f4a1a53a6c617d0e10bd9ccd0e35efe788d97de4a8f01
```

结果保持：

```text
relation accuracy = 0.8333
unclear rate = 0.1667
format error = 0
model calls = 6
```

Reverse-order batch 4 对照：

```text
.data/evaluations/input-preprocessing-v10/relation-reverse-batch-v2/v10-20260830-160548-1689589.json
sha256=8d0757a672a8ed16d0e8b897b2694add868fc0a0df369fadc1f385a4e5c153c3
```

```text
relation accuracy = 0.5
format error = 0
model calls = 2
```

说明固定序列化可保证 format，但 no-fewshot batch 对枚举语义仍敏感；frozen few-shot 仍是当前较优候选，不能宣布 winner。

### 10.5 Winner regression v2

报告：

```text
.data/evaluations/input-preprocessing-v10/regression-v2/v10-20260830-160730-1689827.json
sha256=e3c160fab96b79ac1ec36fd4fc26a23fa2a07c68d351e9376794a543cf4bd4c4
```

使用：

```text
macro-other-pet
macro-long-input
```

结果：

```text
CAN-REGRESSION
  target_span_availability = 1.0
  candidate_recall = 1.0
  canonical_accuracy = 1.0

PARTICIPANT-REGRESSION
  participant mention recall = 0.3333
  role assignment accuracy = 0.3333
  entity resolution accuracy = 0.3333
  resolved empty = 0

TEMPORAL-MEASUREMENT-REGRESSION
  temporal quote availability = 1.0
  binding accuracy = 1.0
```

该报告运行时发现 `前天开始` 被 V6 temporal parser 判为 `parser_unsupported`。随后已修复 deterministic parser，并新增回归测试：

```text
前天开始 -> day-2 / started_at
```

最新测试：

```text
tests/test_input_preprocessing_v10.py: 8 passed
```

由于 macro 输出随机性，后续 `macro-long-input` 单独复测没有匹配到 expected claim skeleton，因此该次报告的 canonical / participant / temporal availability 为 0；这本身进一步证明 macro skeleton 稳定性仍是集成前置 blocker，不能归因于 canonical 或 parser。

### 10.6 REP-COLD v3

报告：

```text
.data/evaluations/input-preprocessing-v10/rep-cold-v3/v10-20260830-161105-1692076.json
sha256=082e548d15c12cf7c9770eb89b740287932d9258039879b16d61d9f0b9b99280
```

```text
cold_run_count = 3
cache_hit_count = 0
unique_output_count = 3
majority_agreement = 0.3333
raw_output_stability = 0.3333
act_signature_stability = 0.6667
claim_signature_stability = 0.6667
binding_signature_stability = 0.3333
p50_ms = 22167
p95_ms = 23088
```

相比上一轮，claim signature stability 从 `0.333` 提升到 `0.667`，但仍未达到三次一致。

### 10.7 当前根因更新

1. **Measurement / fixture blocker 已修复**
   - explicit offset 全部有效；
   - owner occurrence 有效；
   - field / role / label 指标拆分；
   - relation span 完整。

2. **Span blocker 从 near-boundary 转为 ranking / precision / role coverage**
   - coarse near-or-exact 已较高；
   - nested exact recall 可达 `0.659`；
   - 未预算 precision 过低；
   - stratified top-k 16 可保留 `0.659` recall 并将 role coverage 提升到 `0.512`；
   - top-k 16 candidate count / precision 仍不达标；
   - 需要更好的 deterministic ranking、boundary variant selection 或 supervised refiner。

3. **Macro blocker 已从空 acts / invalid binding 转为语义与角色绑定**
   - acts 非空且 evidence 有效；
   - 最新 act precision / recall 为 `0.833 / 1.0`；
   - invalid reference / binding 为 0；
   - statement type、claim skeleton、optional binding 仍漂移；
   - role-ineligible binding 已从 24 降到 2，但尚未清零。

4. **Relation blocker 已从 batch 依赖转为枚举语义**
   - 固定契约 format error 为 0；
   - frozen few-shot 在 development v4 达到 `1.0`；
   - 仍需三次冷调用和冻结后确认，不能因单次 development control 宣布 winner。

5. **Canonical / participant 仍不是首要 blocker**
   - macro target 可用时 canonical 仍通过；
   - participant 失败主要来自 macro mention / role binding，而不是 resolver。

6. **Stability blocker 仍存在**
   - 三次冷调用 raw / claim signature 不一致。

### 10.8 第二轮工程修复复测

本轮继续修复以下工程问题：

1. Macro 输入新增 `role_candidate_index`，按字段 role 列出可用 opaque span ID；
2. Macro prompt 明确 `fact_statement`、`answer_now`、`denies`、`reports_normal`、`reports_abnormal` 的边界；
3. 明确每种 act type 在一个 unit 中最多输出一次，避免为每个 claim 重复输出 `fact_statement`；
4. Relation prompt 显式定义 `absolute_status` / `no_change` / `change` / `unclear`；
5. REP 新增忽略随机 claim_id 的 semantic claim / binding signature；
6. Temporal / measurement regression 新增不依赖随机 macro 输出的 explicit-offset deterministic parser control；
7. Span budget 新增显式 `--span-per-turn-limit`；
8. V10 runner failure report 修复 `Path.sha256` 调用错误；
9. Macro dependency failure 保留 exception cause chain；
10. 远程 V10 request timeout 默认提升到 120 秒，可用
    `INPUT_PREPROCESSING_V10_REQUEST_TIMEOUT_SECONDS` 覆盖。

验证：

```text
本地全量: 246 passed, 43 skipped
远程 V8/V9/V10: 28 passed
远程 ruff / mypy: PASS
```

#### Quick v4

报告：

```text
.data/evaluations/input-preprocessing-v10/quick-v4/v10-20260830-162809-1702487.json
sha256=2acd8adaa0a13174713d54ce123ee31abec9a5816a1918f40adff1e91b8f3855
```

结果与 Phase 0 quick control 一致，均通过；held-out 仍 blocked 且未读取。

#### Span budget cap 复测

64 / 128 hard cap 复测显示 recall 明显下降：

```text
per-turn cap 64:
  top-k 16 recall = 0.390

per-turn cap 128:
  top-k 16 recall = 0.537
```

最终恢复 conservative cap：

```text
per-turn cap 192
```

报告：

```text
.data/evaluations/input-preprocessing-v10/span-small-v6-g16/v10-20260830-170536-1723189.json
sha256=8f75b97f8f8a8e18b98fb1e187001fbfb3f6f6390d900bfcb5cb9118f2dc0111
```

top-k 16 / cap 192 结果：

```text
candidate_count = 621
exact recall = 0.6585
boundary precision = 0.1240
role coverage = 0.5122
unambiguous label accuracy = 0.2676
near-or-exact = 0.7927
```

结论：cap 192 能保留 calibrated pool recall，但 precision / label accuracy 仍未达标。更小的 hard cap 会变成新的 recall blocker。

#### Macro prompt 修复迭代

Macro v4 加入示例后过度泛化：

```text
act precision = 0.333
claim precision = 0.111
role-ineligible binding = 1
```

报告：

```text
.data/evaluations/input-preprocessing-v10/macro-golden-v4/v10-20260830-163832-1706955.json
sha256=85f5810e02f4363d769266098897deb04e66cbfb599a445b45f1f7932dbb2680
```

Macro v5 移除示例并加入语义规则后，act recall 恢复，但 claim 仍低：

```text
act precision = 0.389
act recall = 1.0
claim precision / recall = 0.111
role-ineligible binding = 6
```

报告：

```text
.data/evaluations/input-preprocessing-v10/macro-golden-v5/v10-20260830-165306-1715206.json
sha256=34b742b3f8c8d6c27fa1635d3bf6fe5e454e003534dc266ff51bdbfc2e737bc7
```

Macro v6 明确“每种 act_type 至多一次”后，当前最终结果：

```text
act output = 5
expected acts = 4
act precision = 0.8333
act recall = 1.0
empty act rate = 0
evidence span valid rate = 1.0

claim precision = 0.3444
claim recall = 0.3444
statement type accuracy = 0.3444
support envelope valid rate = 1.0

invalid span reference = 0
invalid span binding = 0
role-ineligible binding = 2
model free quote = 0
```

报告：

```text
.data/evaluations/input-preprocessing-v10/macro-golden-v6/v10-20260830-170802-1723739.json
sha256=55b1ac0c35f2afc42dba394b39da8f30d47def0b52d13e749a26e169dee6ef40
```

结论：act 输出契约显著修复；claim statement type / skeleton 仍是主要 blocker。role-ineligible binding 从 24 降到 2，但未清零。

#### Relation fixed contract v4

报告：

```text
.data/evaluations/input-preprocessing-v10/relation-fixed-fewshot-v4/v10-20260830-164908-1714147.json
sha256=a4d241a6680066d8846f71e962599eb86d1cf57148b05142e78201cda46e323f
```

Single / batch 1：

```text
relation accuracy = 1.0
unclear rate = 0.0
format error = 0
model calls = 6
```

Reverse-order batch 4：

```text
.data/evaluations/input-preprocessing-v10/relation-fewshot-reverse-v4/v10-20260830-164959-1714683.json
sha256=00b20c755e4645d3da600f6a98fcae885585fb61b567fa5815310e96808cc68e
```

结果：

```text
relation accuracy = 1.0
unclear rate = 0.0
format error = 0
model calls = 2
```

该结果仍为 development diagnostic control，尚未做三次冷调用和 held-out，不能宣布生产 winner。

#### Regression v4

报告：

```text
.data/evaluations/input-preprocessing-v10/regression-v4/v10-20260830-172035-1731467.json
sha256=668e2e70d8a386cd1e0c24e773e3b5abd8340c515226b7f988e694860b9eca8b
```

新增 explicit-offset deterministic parser control：

```text
deterministic_parser_record_count = 8
deterministic_parser_normalized_rate = 1.0
over_precision_count = 0
```

说明 `前天开始` 等 development temporal/measurement 输入在 gold offset 下可被确定性 parser 正常消费。

同一报告中 macro skeleton 未匹配 expected claims，因此 canonical / participant / macro temporal availability 为 0。这进一步确认 downstream availability 的首要 blocker 是 macro skeleton / binding，而不是 parser 或 canonical resolver。

#### REP-COLD v4

报告：

```text
.data/evaluations/input-preprocessing-v10/rep-cold-v4/v10-20260830-171255-1727176.json
sha256=96a1d664f30471120403c3c969fbe2833e9d4f9323ff355b248e0b56bb13ec54
```

```text
cold_run_count = 3
cache_hit_count = 0
unique_output_count = 2
majority_agreement = 0.6667
raw_output_stability = 0.6667
act_signature_stability = 1.0
claim_signature_stability = 0.6667
binding_signature_stability = 0.6667
semantic_claim_stability = 0.6667
semantic_binding_stability = 0.6667
p50_ms = 11382
p95_ms = 12413
```

REP 从三次全不同改善为 2/3 一致，但仍未达到确认性目标。

## 11. 下一步

1. 调整 span candidate ranking：
   - 不改变 gate；
   - 不使用医学词典；
   - 保持 exact offset；
   - 对比 length、score、role、containment、token boundary 的确定性 ranking。
2. 扩展 budget 对照：
   - per-role top-k；
   - per-claim region；
   - candidate count 与 macro binding 的 Pareto 曲线。
3. 在 `SPAN-CALIBRATE` 达标后再考虑 SpanMarker cross-validation。
4. 修复 macro prompt 的 act 定义和 statement type：
   - `reports_abnormal` 与 `reports`；
   - `denies` 与 shared scope；
   - act evidence 选择；
   - optional binding null 语义。
5. 修复 macro role-eligible binding：
   - prompt 中显式说明 `eligible_roles`；
   - 对 claim-local candidate view 限制可选 role；
   - 将 role-ineligible binding 作为 blocking 归因而不是静默放行。
6. 修复 relation 枚举定义：
   - `absolute_status`；
   - `no_change`；
   - `change`；
   - frozen few-shot 后重复冷调用。
7. macro golden 达标后再执行 adapter cold。
8. held-out 继续冻结，DSPy 不得读取。

## 12. 复现命令

Quick control：

```bash
scripts/integration/run-input-preprocessing-v10-remote-runner.sh \
  --suite quick \
  --mode quick \
  --no-cache \
  --output-dir .data/evaluations/input-preprocessing-v10/quick
```

GLiNER small calibration：

```bash
scripts/integration/run-input-preprocessing-v10-remote-runner.sh \
  --suite span \
  --mode shadow \
  --span-model small \
  --span-threshold 0.1 \
  --variant A \
  --variant B \
  --variant C \
  --variant D \
  --variant E \
  --variant F \
  --variant G \
  --budget 8 \
  --budget 12 \
  --budget 16 \
  --output-dir .data/evaluations/input-preprocessing-v10/span-small-v3
```

Macro golden：

```bash
scripts/integration/run-input-preprocessing-v10-remote-runner.sh \
  --suite macro \
  --mode shadow \
  --no-cache \
  --unit macro-answer-fact \
  --unit macro-shared-scope \
  --unit macro-action-roles \
  --candidate-mode full \
  --output-dir .data/evaluations/input-preprocessing-v10/macro-golden-v2
```

Relation fixed contract：

```bash
scripts/integration/run-input-preprocessing-v10-remote-runner.sh \
  --suite relation \
  --mode shadow \
  --no-cache \
  --batch-size 1 \
  --relation-fewshot \
  --output-dir .data/evaluations/input-preprocessing-v10/relation-fixed-fewshot-v2
```

REP cold：

```bash
scripts/integration/run-input-preprocessing-v10-remote-runner.sh \
  --suite rep \
  --mode shadow \
  --rep-unit macro-answer-fact \
  --rep-runs 3 \
  --output-dir .data/evaluations/input-preprocessing-v10/rep-cold-v3
```

## 13. 安全边界

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

所有 V10 golden / gold injection / early-exit 结果不得：

```text
进入 production projection
作为生产 fallback
解除 V8 live phase admission
替代 live span gate
接触 held-out
触发 DSPy 优化
```

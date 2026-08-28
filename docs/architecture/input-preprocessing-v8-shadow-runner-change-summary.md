<!--
=============================================================================
文件: input-preprocessing-v8-shadow-runner-change-summary.md
作用: 记录第八轮 input-preprocessing 全阶段 shadow runner、快速验证结果与准入阻断。
范围: 覆盖 Phase 0～5 实验 ID、live stage gate、held-out 防护、DSPy 防护、
      relation/canonical live 接口、winner 隔离、冷启动重复和异步失败隔离。
说明: 本文证明实验编排与安全边界可执行；所有质量指标仍未达到生产消费准入。
维护: 当 V8 runner、stage gate、fixture、实验结果或准入结论变化时同步更新。
=============================================================================
-->

# Input preprocessing V8 shadow runner 与快速验证记录

> **结论**：V8 runner 已从 Phase 0 / STRUCT 扩展到文档定义的全部实验 ID。quick ideal control 可以完成宏观语义、participant、relation、canonical、winner integration、三次冷调用重复和异步隔离的端到端编排；live shadow 已按 Phase 0 gate 阻断后续阶段。held-out 和 DSPy 均有显式防护，不会在默认命令中读取或优化。
>
> **准入结论**：当前 live GLiNER `SPAN-GOLDEN` 仍未达标，因此 Phase 1 之后所有 live 实验保持 `upstream_blocked`。quick ideal control 的宏观语义质量同样偏低，不能用工具链可用性替代质量准入。

## 1. 实现范围

### 1.1 Runner 与报告

`src/vet_agent/input_preprocessing/v8_experiments.py` 现支持：

```text
SPAN-GOLDEN
SPAN-POOL-COVERAGE
NEG-V8
STRUCT-BASE
STRUCT-INSTRUCTOR
STRUCT-BAML
MACRO-INTENT
MACRO-CLAIM
MACRO-BINDING
PARTICIPANT-RESOLVE
RELATION-LIVE
CAN-LIVE
WINNER-INTEGRATION
DSPY-OPT
REP-V8
HELD-OUT-V8
ASYNC-V8
```

新增报告版本：

```text
v8-experiment-report-2
```

报告统一记录：

```text
mode / phase
matrix path / sha256
span_pool / stage_admission
prompt version / schema version
model
vocabulary version
cache status
experiment metrics
execution audit
safety boundary
```

当前 `token_count` 与 `cost` 依赖底层客户端 usage 回传；现有 V8 adapter 尚未取得该字段，因此报告中显式记录 `token_count_available=false`、`cost_available=false`，不用估算值冒充真实成本。

### 1.2 Span pool 与阶段准入

新增：

```text
--span-pool ideal
--span-pool live
```

默认值：

```text
quick  -> ideal
shadow -> live
```

`ideal` 只能作为本地契约 / 工具链 control，不代表质量准入。`live` 在进入 Phase 1 之后会先执行：

```text
required_field_coverage >= 0.90
precision >= 0.75
label_accuracy >= 0.75
```

同时需要显式：

```text
--confirm-previous-phases
```

否则 Phase 1+ 报告为 `blocked/upstream_blocked`，不调用宏观模型，也不用宽松规则补 span。

### 1.3 Phase 2 宏观语义

一次宏观调用复用于：

```text
MACRO-INTENT
MACRO-CLAIM
MACRO-BINDING
PARTICIPANT-RESOLVE
```

指标包括：

```text
act precision / recall
fact/question confusion
evidence span valid rate
claim precision / recall
statement type accuracy
target binding accuracy
support envelope valid rate
binding accuracy
participant mention recall
participant resolution accuracy
resolved-empty violation
invented entity
cross claim assignment
invalid span reference / binding
free quote output
```

所有 quote 仍由 span offset 反查生成；模型输出自由 quote 字段仍由 strict schema 直接阻断。

### 1.4 Phase 3 Winner live

`RELATION-LIVE`：

- 只消费 governed claim 反查出的 live `relation_quote`；
- shadow 模式调用 V7 relation classifier；
- quick 模式仅作为 ideal plumbing control；
- 记录 relation input availability、accuracy、combined ready rate。

`CAN-LIVE`：

- shadow 模式使用 V6 canonical direct recall 与 `text-embedding-v4`；
- quick 模式使用显式 `v8-ideal-fixture-control`，只验证管线，不评估质量；
- 记录 candidate recall、canonical accuracy、under-confirmation、false confirmation、no candidate。

`WINNER-INTEGRATION`：

- 汇总 span governance、V7 relation、V6 canonical recall 和确定性 temporal / measurement parser；
- 按 claim 输出 `projected / review / blocked`；
- 单 claim 失败不污染其他 claim；
- blocked claim 不进入 projection；
- 报告 `projection_consuming_blocked_count`。

### 1.5 Phase 4 / Phase 5

`DSPY-OPT`：

```text
默认 blocked
```

必须显式 `--allow-dspy`；当前即使安装 DSPy，也没有配置 train/dev optimizer 与冻结产物流程，因此不会假称完成优化。

`REP-V8`：

- 同一 ideal control 单元执行三次冷调用；
- 禁止 cache replay；
- 报告 unique output、majority agreement、semantic signature stability、p50 / p95 latency。

`HELD-OUT-V8`：

必须同时满足：

```text
--phase confirmatory
--allow-held-out
held-out fixture path
live span pool
--confirm-previous-phases
```

默认读取 held-out fixture 会直接失败。DSPy 优化仍不得读取 held-out。

`ASYNC-V8`：

- 本地 file-backed bounded queue control；
- 验证 queue full、dead letter、trace completeness；
- 主链路延迟 / 错误率差值记录为 0，仅证明实验路径没有接入主链路。

## 2. 快速验证结果

### 2.1 本地测试

```text
ruff check: PASS
mypy v8_experiments.py: PASS
pytest tests/test_input_preprocessing_v8.py: 12 passed
远程同组测试: 12 passed
远程 strict environment smoke: PASS
```

### 2.2 Live Phase 0

报告：

```text
.data/evaluations/input-preprocessing-v8-phase0/v8-20260828-140247-48480.json
sha256=87df51c71a282d0c878c1be9f4b997342442fbdabe7e661020dc99f5b3d62c4a
```

`SPAN-GOLDEN`：

```text
precision=1.0
recall=0.075
f1=0.1395
label_accuracy=0.0
required_field_coverage=0.075
predicted_span_count=4
```

`NEG-V8`：

```text
mutation_count=7
gate_blocked_as_expected=7
false_pass=0
model_free_quote_output=0
```

结论：hard gate 有效，但 live span 质量仍显著未达标。

### 2.3 Live stage gate

报告：

```text
.data/evaluations/input-preprocessing-v8-live-gate/v8-20260828-135702-45079.json
sha256=4e5b1022e13dd8330f40996afa79b98c6de88610572ed941ec47caf9da0758ff
```

`MACRO-INTENT` 被阻断：

```text
status=blocked
failure_attribution=upstream_blocked
phase0_required_field_coverage=0.075
phase0_recall=0.075
phase0_label_accuracy=0.0
```

该阻断发生在宏观模型调用之前，符合“前一阶段未达标不得进入后阶段”的纪律。

### 2.4 Quick ideal control

报告：

```text
.data/evaluations/input-preprocessing-v8/v8-20260828-135539-43924.json
sha256=2e33c7040e15a62819ebf14f206d77317096771b7b60cbd4f1d50971142560cf
```

关键结果：

```text
STRUCT-BASE completed
MACRO-INTENT blocked
MACRO-CLAIM blocked
MACRO-BINDING blocked
PARTICIPANT-RESOLVE blocked
RELATION-LIVE blocked
CAN-LIVE completed
WINNER-INTEGRATION completed
DSPY-OPT blocked
ASYNC-V8 completed
```

主要指标：

```text
MACRO-INTENT
  act_precision=0.0
  act_recall=0.0
  evidence_span_valid_rate=0.0
  invalid_span_binding_count=12
  invalid_span_reference_count=1

MACRO-CLAIM
  claim_precision=0.133333
  claim_recall=0.116667
  statement_type_accuracy=0.116667
  target_binding_accuracy=0.116667

MACRO-BINDING / PARTICIPANT-RESOLVE
  binding_accuracy=0.3
  participant_mention_recall=0.0
  participant_resolution_accuracy=0.0
  support_envelope_valid_rate=0.333333

RELATION-LIVE
  relation_input_availability=0.0
  relation_accuracy=0.0
  combined_ready_rate=0.0

CAN-LIVE ideal control
  candidate_recall=0.0
  canonical_accuracy=0.0

WINNER-INTEGRATION
  claim_count=22
  blocked_count=21
  review_count=1
  projected_count=0
  projection_consuming_blocked_count=0

ASYNC-V8
  queue_full_count=1
  dead_letter_count=1
  trace_completeness=1.0
```

解释：quick ideal 只证明 pipeline、治理和隔离机制可执行。宏观输出质量、relation span 可用性和 canonical target 可用性均未达标，不能作为 winner 或 integration 结论。

### 2.5 STRUCT 对照

报告：

```text
.data/evaluations/input-preprocessing-v8-struct/v8-20260828-140154-47793.json
sha256=480c980a833606e63dabeebd8d90b9ef2bdc31a9e2c1c5957952e5b9dfbd4f62
```

```text
STRUCT-INSTRUCTOR
  schema_valid=1
  invalid_span_reference_count=0
  invalid_span_binding_count=3
  projection_ready_count=3
  wall_latency_ms=14706

STRUCT-BAML
  schema_valid=1
  invalid_span_reference_count=0
  invalid_span_binding_count=0
  governed_claim_count=4
  projection_ready_count=4
  wall_latency_ms=27343
```

BAML 这次未出现 invalid binding，但延迟更高；Instructor 出现 3 个 invalid binding。该单次 quick control 不能推出 adapter winner，仍需同一 live span pool、同一指标和重复冷运行比较。

### 2.6 冷启动重复

报告：

```text
.data/evaluations/input-preprocessing-v8-rep/v8-20260828-140044-47182.json
sha256=95160d5bdd51fa814f459f3065fa3d45231a5e32bb768ed0b369de57176140e8
```

```text
run_count=3
cold_run_count=3
cache_hit_count=0
unique_output_count=2
majority_agreement=0.6666666666666666
semantic_signature_stability=0.6666666666666666
p50_ms=13030
p95_ms=13092
```

结论：重复稳定性未达到“三次一致”的确认性目标。

## 3. 复现命令

### 3.1 Strict 环境冒烟

```bash
scripts/integration/verify-input-preprocessing-v8-environment.sh --strict-v8-gates
```

### 3.2 Live Phase 0

```bash
scripts/integration/run-input-preprocessing-v8-remote-runner.sh \
  --mode shadow \
  --span-pool live \
  --experiment SPAN-GOLDEN \
  --experiment SPAN-POOL-COVERAGE \
  --experiment NEG-V8 \
  --output-dir .data/evaluations/input-preprocessing-v8-phase0
```

### 3.3 Quick ideal 全链路 control

```bash
scripts/integration/run-input-preprocessing-v8-remote-runner.sh \
  --mode quick \
  --span-pool ideal \
  --no-cache \
  --adapter base \
  --macro-adapter base \
  --experiment STRUCT-BASE \
  --experiment MACRO-INTENT \
  --experiment MACRO-CLAIM \
  --experiment MACRO-BINDING \
  --experiment PARTICIPANT-RESOLVE \
  --experiment RELATION-LIVE \
  --experiment CAN-LIVE \
  --experiment WINNER-INTEGRATION \
  --experiment ASYNC-V8 \
  --experiment DSPY-OPT
```

### 3.4 三次冷调用

```bash
scripts/integration/run-input-preprocessing-v8-remote-runner.sh \
  --mode quick \
  --span-pool ideal \
  --no-cache \
  --experiment REP-V8
```

### 3.5 Live 阶段阻断验证

```bash
scripts/integration/run-input-preprocessing-v8-remote-runner.sh \
  --mode shadow \
  --span-pool live \
  --experiment SPAN-POOL-COVERAGE \
  --experiment MACRO-INTENT \
  --output-dir .data/evaluations/input-preprocessing-v8-live-gate
```

## 4. 下一步

1. 只在 development fixture 上继续 GLiNER 标签组、threshold、候选模型与边界策略对照；
2. SPAN-GOLDEN 达标后，使用同一 live span pool 重复 STRUCT-BASE / INSTRUCTOR / BAML；
3. macro 输出需要优先修复 evidence act、claim semantic signature 和 optional span binding；
4. relation span 缺失不能由模型自由 quote 或关键词补齐，只能显式 `review_required`；
5. REP-V8 至少需要三次冷调用 signature 一致；
6. 所有 live Phase 1+ 实验必须显式确认前一阶段报告已通过；
7. held-out 仍保持冻结，DSPy 不得读取。

## 5. 安全边界

本轮所有命令保持：

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

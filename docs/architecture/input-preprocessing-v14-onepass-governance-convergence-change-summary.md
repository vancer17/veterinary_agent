<!--
=============================================================================
文件: input-preprocessing-v14-onepass-governance-convergence-change-summary.md
作用: 记录第十四轮 one-pass LLM-first Structured Claim 治理收敛实验的工程
      实现、quick control、远程 shadow 迭代与观察到的现象。
范围: 覆盖 fixed-field intent、claim inventory、claim-local alignment、
      participant resolver、temporal/measurement verifier、constrained canonical
      selector、minimal lane 观测、NEG/ASYNC 与 held-out 防护。
说明: 本文只总结实验现象和报告数据，不宣布 winner，不解除 V8 live gate，
      不把 prompt 迭代现象直接外推为生产架构结论。
维护: 当 V14 报告、契约、prompt、生成参数或远程复测结果变化时同步更新。
=============================================================================
-->

# Input preprocessing V14 one-pass governance convergence 实验现象记录

> **文档状态**：V14 探索性实现与远程迭代记录；未达到生产消费准入。
>
> **记录原则**：本文只描述已执行实验和报告数据；不臆造因果结论，不宣布
> one-pass、multi-agent、prompt skill 或任何 middleware 为 winner。

## 1. 实现范围

本轮新增：

```text
src/vet_agent/input_preprocessing/v14_contracts.py
src/vet_agent/input_preprocessing/v14_intent.py
src/vet_agent/input_preprocessing/v14_generation_options.py
src/vet_agent/input_preprocessing/v14_prompt_skills.py
src/vet_agent/input_preprocessing/v14_alignment.py
src/vet_agent/input_preprocessing/v14_participant_resolver.py
src/vet_agent/input_preprocessing/v14_canonical_selector.py
src/vet_agent/input_preprocessing/v14_governance.py
src/vet_agent/input_preprocessing/v14_experiments.py

tests/test_input_preprocessing_v14.py
scripts/integration/deploy-input-preprocessing-v14-remote.sh
scripts/integration/run-input-preprocessing-v14-remote-runner.sh
```

同时为 `QwenClient` 增加了 structured response metadata 通道，可返回：

```text
output
usage
finish_reason
response_id
provider model snapshot
```

V14 runner 支持：

```text
EXEC-OBS
INTENT-SPLIT
GEN-OPTION
SKILL-INVENTORY
SKILL-SHARED
SKILL-NULL
SKILL-PARTICIPANT
ALIGN-LOCAL
PARTICIPANT-V14
TEMPORAL-V14
MEASUREMENT-V14
CAN-SELECT-V14
MINIMAL-LANE
REP-V14
NEG-V14
ASYNC-V14
HELD-OUT-V14
```

报告版本：

```text
v14-experiment-report-1
```

核心契约：

```text
intent schema = v14-fixed-field-intent-1
claim schema = v14-onepass-inventory-claim-1
prompt skill = v14-claim-skills-20260901-1
alignment = v14-claim-local-aligner-20260901-1
participant resolver = v14-turncontext-candidate-only-1
canonical selector = v14-dual-query-constrained-selector-1
```

## 2. 本地与远程工程验证

最新本地代码验证：

```text
ruff check V14 modules/tests: PASS
mypy V14 modules + QwenClient: PASS
pytest tests/test_input_preprocessing_v14.py: 8 passed
quick control: PASS
```

本地 quick control：

```text
.tmp/v14-quick-final-local/v14-20260901-200303-10489.json
sha256=4fdb79e754a74a9f5db1cb98a68818a47ff004ef07d15561a3d3871df52c7b36
```

远程部署过程中多次执行：

```text
compileall V14 modules: PASS
ruff check V14 modules/tests: PASS
mypy V14 modules: PASS
pytest V14: 8 passed
```

需要注意：最后一轮本地 prompt / validator 微调后，本地验证已通过，但对应代码
没有再执行新的远程真实 shadow。因此下文远程报告对应的是当时的部署版本，不
应与最新本地代码版本混为同一个实验版本。

## 3. Quick control

远程 quick control 报告：

```text
.data/evaluations/input-preprocessing-v14/quick/
v14-20260901-183645-3464093.json

sha256=
3c21b5833fbdc325e4d06128f3c4a6dd76cceef2f93f85b39aba42c972780200
```

结果：

```text
NEG-V14:
  mutation_count = 13
  gate_blocked_as_expected = 13
  false_pass = 0
  gate_blocked_as_expected_rate = 1.0

INTENT-SPLIT ideal:
  act precision / recall = 1.0 / 1.0
  evidence alignment = 1.0
  fact_statement_duplicate_count = 0

SKILL-INVENTORY ideal:
  claim precision / recall = 1.0 / 1.0
  claim output = 22 / 22
  unmatched inventory = 0

ALIGN-LOCAL ideal:
  field alignment = 1.0
  false alignment = 0
  wrong occurrence = 0
  outside parent = 0

PARTICIPANT-V14 ideal:
  participant mention recall = 1.0
  participant resolution accuracy = 1.0
  resolved-empty violation = 0

TEMPORAL-V14 ideal:
  parser normalized rate = 1.0
  parser conflict rate = 0

MEASUREMENT-V14 ideal:
  parser normalized rate = 1.0
  parser conflict rate = 0

CAN-SELECT-V14 ideal:
  candidate recall = 1.0
  canonical accuracy = 1.0
  false confirmation = 0
  invented canonical = 0

ASYNC-V14:
  queue full / dead letter / trace completeness 均有效

HELD-OUT-V14:
  blocked
  heldout_read_count = 0
```

解释边界：quick control 使用 ideal / contract control，只证明契约与治理链路
可执行，不代表 qwen-plus 真实 shadow 质量。

## 4. Fixed-field intent 观察到的现象

V14 将 acts array 改为 fixed-field schema：

```text
answer_now
wants_triage
correction
clarification_request
fact_statement_present
question_present
report_context_present
```

所有真实远程运行中均观察到：

```text
fact_statement_duplicate_count = 0
```

最新完整 representative 运行中的 intent 数据：

```text
act precision = 0.8333333333333334
act recall = 1.0
evidence alignment rate = 0.6666666666666666
intent_claim_consistency_rate = 1.0
```

现象：

1. fixed-field schema 使 `fact_statement` 无法在数组中重复输出；
2. intent recall 保持为 1.0；
3. precision 和 evidence phrase 选择仍有漂移；
4. 部分 false positive 信号仍会被识别出来。

本文不由此推断 fixed-field intent 已达到 finalist 标准。

## 5. One-pass claim shadow 迭代现象

本轮多次调整 claim prompt 中关于 claim inventory、target、statement type、
shared scope、participant、temporal/measurement 的说明。不同部署版本的远程
结果如下。

### 5.1 minimal-p0-shadow-v2

报告：

```text
.data/evaluations/input-preprocessing-v14/minimal-p0-shadow-v2/
v14-20260901-184556-3469499.json

sha256=
ad8a083caaaabcb4e815e44721ed38a2e21802e44e21df8061c6a4f7611d0f58
```

核心数据：

```text
unit_count = 4
dependency_failure_count = 0
model_call_count = 8

INTENT:
  act precision / recall = 0.7917 / 1.0
  evidence alignment = 0.125

CLAIM:
  claim output = 20 / 20
  claim precision / recall = 0.0 / 0.0
  blocked count = 12

ALIGN:
  field alignment = 0.4889
  false alignment = 0.4444

PARTICIPANT:
  mention recall = 0.25
  resolution accuracy = 0.25

TEMPORAL:
  parser normalized rate = 1.0
  parser conflict rate = 1.0
```

该轮报告出现重复 report 项，原因是 CLI 默认 `p0` 与显式
`--generation-option p0` 叠加，导致同一 option 执行两次。该问题随后修正。

### 5.2 minimal-p0-shadow-v3

报告：

```text
.data/evaluations/input-preprocessing-v14/minimal-p0-shadow-v3/
v14-20260901-185113-3476694.json

sha256=
1961ffcd0ecd1403cffc18cfacfd2af7114b382b60aefa46b672866ec4e8df51
```

核心数据：

```text
dependency_failure_count = 1

completed units 的 claim:
  claim expected = 10
  claim output = 10
  claim precision / recall = 0.0 / 0.0

ALIGN:
  field alignment = 0.4783
  false alignment = 0.4565

PARTICIPANT:
  mention recall = 0.3333
  resolution accuracy = 0.3333
```

失败 unit 为 `macro-shared-scope`，失败原因是：

```text
claim_inventory_ordinal_must_be_unique
```

### 5.3 minimal-p0-shadow-v4

报告：

```text
.data/evaluations/input-preprocessing-v14/minimal-p0-shadow-v4/
v14-20260901-191423-3499317.json

sha256=
8b0f12f646c0ed6a3154a2f5aeac3d5848a1824b8acd2e615a005d5c07af687f
```

核心数据：

```text
dependency_failure_count = 1

completed units 的 claim:
  claim expected = 10
  claim output = 10
  claim precision / recall = 0.7 / 0.7
  statement type accuracy = 0.7
  blocked count = 1

ALIGN:
  field alignment = 0.4355
  false alignment = 0.5645

PARTICIPANT:
  mention recall = 0.0
  resolution accuracy = 0.0
```

失败 unit 仍为 `macro-shared-scope`，失败原因仍为 inventory / claim ordinal
schema invalid。

### 5.4 minimal-p0-production 命名报告

报告：

```text
.data/evaluations/input-preprocessing-v14/minimal-p0-production/
v14-20260901-193810-3522216.json

sha256=
acd0f8a0f18fa48fde48548ddf2df80402831a35594d4bd995eea9fb01d868d1
```

核心数据：

```text
unit_count = 4
dependency_failure_count = 1
model_call_count = 7
token_count_available_rate = 0.875
total_token_count = 6030

INTENT:
  act precision / recall = 0.8333 / 1.0
  evidence alignment = 0.6667
  fact_statement_duplicate_count = 0

completed units 的 claim:
  claim expected = 10
  claim output = 10
  claim precision / recall = 1.0 / 1.0
  statement type accuracy = 1.0
  polarity accuracy = 1.0
  blocked count = 0

ALIGN:
  field alignment = 0.5926
  false alignment = 0.4074

PARTICIPANT:
  mention recall = 0.0
  resolution accuracy = 0.0
  invented entity = 0
  resolved-empty violation = 0

TEMPORAL:
  parser normalized rate = 1.0
  parser conflict rate = 0

MEASUREMENT:
  parser normalized rate = 1.0
  parser conflict rate = 0

CANONICAL:
  record count = 1
  candidate recall = 0
  canonical accuracy = 0
  false confirmation = 0
  invented canonical = 0
```

unit 级状态：

```text
macro-answer-fact: completed
macro-shared-scope: failed
macro-action-roles: completed
macro-long-input: completed
```

其中 `macro-shared-scope` 的失败原因为：

```text
claim_inventory_mismatch
```

其余 3 个 unit 的 claim 匹配均达到：

```text
claim precision / recall = 1.0 / 1.0
```

现象说明：

1. prompt 调整后，非 shared-scope 的 3 个 completed units 在该次运行中输出
   与 expected claims 完全匹配；
2. shared-scope unit 仍发生 inventory / claims 数量或对应关系 schema failure；
3. participant 字段仍大面积缺失，导致 participant 指标为 0；
4. field false alignment 仍为 0.4074；
5. temporal / measurement parser verifier 在该轮可正常消费已绑定 phrase。

### 5.5 执行口径 caveat

该报告虽然目录名为 `minimal-p0-production`，但报告中显示：

```text
attempt_policy = raw_max_attempts_1
```

这是当前 report 标签逻辑的问题：CLI 传入 `--attempt-policy production` 后，
报告标签仍按基础 generation option 的 `max_attempts=1` 判断，不能可靠区分
raw measurement 与 production candidate mode。

此外，claim lane 抛出异常时，当前 run result 未保留失败 claim 调用的完整
metadata，因此 top-level `model_call_count` 主要由成功 intent / claim 调用构
成，可能低估失败 lane 的实际模型调用次数。

后续报告必须先修复这两个执行观测问题，再比较 raw / production mode。

## 6. Shared scope 专项现象

### 6.1 shared-scope-shadow：prompt 修改前

在一次 shared scope 专项运行中，模型将并列结构合并为 3 条粗 claim：

```text
没有呕吐、干呕、反流、流涎或舔唇
→ 1 条 claim，target = 呕吐、干呕、反流、流涎或舔唇

精神、食欲和饮水都正常
→ 1 条 claim，target = 精神、食欲和饮水

没有血便和黑便
→ 1 条 claim，target = 血便和黑便
```

该现象对应：

```text
claim output = 3 / expected 10
```

### 6.2 shared-scope-shadow-v2：显式拆分提示后

报告：

```text
.data/evaluations/input-preprocessing-v14/shared-scope-shadow-v2/
v14-20260901-192115-3506918.json

sha256=
7af76c0e33e642ecef1dfdd6242a9e33d99ab74892fb496d3d29a6e503930cc0
```

结果：

```text
claim output = 10 / 10
claim precision / recall = 1.0 / 1.0
statement type accuracy = 1.0
polarity accuracy = 1.0
blocked count = 7
projection ready count = 3

field alignment = 0.6667
false alignment = 0.1
ambiguous rate = 0.2333
```

blocked 的 7 条 claim 主要来自：

```text
relation phrase = 没有 / 都正常
→ fuzzy_ambiguous
```

现象：

1. 显式并列拆分提示后，该次运行将 10 个 target 全部拆出；
2. claim skeleton 与 statement type 在该次运行中正确；
3. 多个 shared relation 绑定在同一 evidence envelope 内时，relation phrase
   出现 `fuzzy_ambiguous`；
4. 因此 10 条 claim 中仅 3 条 projection ready。

### 6.3 后续 shared-scope 重复运行

后续三次专项运行均失败：

```text
shared-scope-shadow-v3
  sha256=ede542e4fed8297e161980c46c1e083e4160235c1ea3e2387148d55d60bfa1c4
  failure = claim_inventory_ordinal_must_be_unique

shared-scope-shadow-v4
  sha256=ea78180ce96a76cbd83becf700185ead2156ac785774da180d3b93d9d01a7d36
  failure = claim_inventory_ordinal_must_be_unique

shared-scope-shadow-v5
  sha256=5e167fde5b7438ba06315c3a74d35bec72900f15807f023677c2bff1844b4cee
  failure = claim_inventory_mismatch
```

现象：

1. 同一 shared-scope 样本在一次运行中可输出 10/10 claims；
2. 后续运行多次在 inventory ordinal 或 inventory/claims 一一对应关系上失败；
3. claim 语义与 schema 纪律未同时稳定；
4. 尚未完成同版本三次冷调用 REP，不能给出稳定性结论。

## 7. Claim-local alignment 现象

Ideal control：

```text
field alignment = 1.0
false alignment = 0
wrong occurrence = 0
outside parent = 0
```

真实 shadow 中：

```text
minimal-p0-shadow-v2:
  field alignment = 0.4889
  false alignment = 0.4444

minimal-p0-shadow-v3:
  field alignment = 0.4783
  false alignment = 0.4565

minimal-p0-shadow-v4:
  field alignment = 0.4355
  false alignment = 0.5645

minimal-p0-production:
  field alignment = 0.5926
  false alignment = 0.4074
```

观察到的主要现象：

1. model phrase 多数能落回原文；
2. 但字段缺失、错误角色、错误 claim region 和 shared relation ambiguity 仍会
   造成 false alignment；
3. participant 字段缺失是 field 指标的重要来源；
4. shared relation 在重复 occurrence 上容易进入 `fuzzy_ambiguous`；
5. claim-local scope 已实现，但尚未使真实 shadow field false alignment 达到
   V14 探索目标。

## 8. Participant resolver 现象

Resolver 契约：

```text
只从 TurnContext candidates 解析
resolved 不能为空
ambiguous 必须有多个候选
不得默认 current_pet
不得发明 entity
```

真实运行：

```text
minimal-p0-shadow-v2:
  participant mention / resolution = 0.25 / 0.25

minimal-p0-shadow-v3:
  participant mention / resolution = 0.3333 / 0.3333

minimal-p0-shadow-v4:
  participant mention / resolution = 0 / 0

minimal-p0-production:
  participant mention / resolution = 0 / 0
```

同时：

```text
invented_entity_rate = 0
resolved_empty_violation = 0
```

unit 级输出显示，action claims 中常见现象是：

```text
object / temporal 已输出
action_agent_phrase 缺失
action_recipient_phrase 缺失
```

state claim 中有时输出：

```text
experiencer_phrase = 它
```

现象说明：resolver 的安全边界可执行，但模型侧 participant phrase 输出覆盖率
不稳定。本文不判断这是 prompt 能力、schema 负载还是任务拆分问题。

## 9. Temporal / measurement verifier 现象

最新完整 representative 报告：

```text
TEMPORAL-V14:
  parser normalized rate = 1.0
  parser conflict rate = 0
  model proposed review rate = 0

MEASUREMENT-V14:
  parser normalized rate = 1.0
  parser conflict rate = 0
  model proposed review rate = 0
```

早一轮 minimal shadow 中：

```text
parser normalized rate = 1.0
parser conflict rate = 1.0
parser conflict review rate = 1.0
```

现象：

1. temporal / measurement phrase 一旦被模型输出并成功 claim-local align，
   deterministic parser 可以消费；
2. parser conflict 会被显式 review；
3. 未观察到 model proposal 被伪装成 verified；
4. 该结果只覆盖 development representative units，不代表通用时间 / 度量覆盖。

## 10. Canonical selector 现象

Ideal control：

```text
candidate recall = 1.0
canonical accuracy = 1.0
false confirmation = 0
invented canonical = 0
```

最新真实 representative 报告中，仅有 1 条 expected canonical record：

```text
expected = soft_stool
candidate recall = false
canonical accuracy = false
status = not_found
false confirmation = false
invented canonical = 0
```

现象：

1. constrained selector 未产生假确认；
2. 该轮 descriptor / target dual query 未召回期望 candidate；
3. record 数量太少，不能评价 canonical selector 质量；
4. 需要在包含更多 expected canonical 的 targeted set 上复测。

## 11. Minimal lane 观测现象

最新完整 representative 报告：

```text
intent p50 = 4265.75 ms
intent p95 = 5043 ms

claim p50 = 12490.25 ms
claim p95 = 18247 ms

total p50 = 37184 ms
total p95 = 85190 ms

model_call_count = 7
prompt_token_count = 3429
completion_token_count = 2601
total_token_count = 6030
token_count_available_rate = 0.875
cost_available_rate = 0
```

现象：

1. usage metadata 在多数成功 structured 调用中可取得；
2. cost 仍不可得；
3. 失败 claim lane 的调用审计不完整；
4. 当前 p95 是该 runner 的完整实验编排耗时，不是独立 production candidate
   path 的最终延迟；
5. 由于 shared-scope unit 失败，不能将该结果视为完整 minimal lane 成本结论。

## 12. NEG / ASYNC / held-out

NEG-V14：

```text
mutation_count = 13
gate_blocked_as_expected = 13
false_pass = 0
gate_blocked_as_expected_rate = 1.0
gate_reason_correct_rate = 1.0
```

覆盖：

```text
fact_statement duplicate
model free canonical_id
model free entity_id
fuzzy not found direct pass
fuzzy ambiguous direct pass
model proposed as verified
parser conflict without review
participant resolved + null
canonical confirmed without candidates
projection consumes blocked claim
claim evidence phrase empty
true intent without evidence phrase
retry result as single attempt
```

ASYNC-V14：

```text
submitted_count = 2
accepted_count = 1
queue_full_count = 1
dead_letter_count = 1
trace_completeness = 1.0
```

Held-out：

```text
status = blocked
heldout_read_count = 0
dspy_used = false
```

## 13. 未执行或未完成的实验

以下实验尚未完成，不能给出质量结论：

```text
GEN-OPTION P0/P1/P2/P3 同版本对照
完整 representative units 的 REP-V14 三次冷调用
production candidate mode 与 raw measurement mode 的可靠对照
更多 expected canonical records 上的 CAN-SELECT-V14 复测
最新本地 prompt / validator 版本的远程真实 shadow
Adapter Cold
API async shadow design
fresh held-out
```

## 14. 当前观察到的未解现象

按报告现象列出，不做因果归因：

1. **shared-scope 输出不稳定**
   - 一次运行可 10/10 拆分；
   - 后续多次 inventory ordinal / claims 对应 schema failure；
   - shared relation phrase 多次 `fuzzy_ambiguous`。

2. **单体 one-pass schema 负载高**
   - 模型需同时输出 inventory、claims、statement semantics、participant、
     temporal、measurement、relation、canonical descriptor；
   - 不同字段出现交替缺失。

3. **participant 输出不稳定**
   - resolver 未发明 entity，也未出现 resolved-empty；
   - 但 action agent / recipient phrase 覆盖率多次为 0 或接近 0。

4. **field false alignment 未达标**
   - 最新真实运行为 0.4074；
   - ideal control 为 0。

5. **intent evidence 选择仍漂移**
   - duplicate 已消除；
   - recall 为 1.0；
   - precision 和 evidence phrase 对齐仍未稳定。

6. **执行观测仍有报告口径问题**
   - raw / production attempt 标签不可靠；
   - 失败 claim lane metadata 未完整保留；
   - cost unavailable；
   - 部分早期报告存在重复 option 输出。

## 15. 关于后续方案讨论的记录边界

在本轮实验之后，讨论过两类后续方向：

```text
1. 停止继续以单体 one-pass prompt 补丁作为主要收敛手段；
2. 研究 contract-first / deterministic orchestration / 受限语义协作或
   multi-agent 方案。
```

本轮没有执行任何新的 multi-agent 实验，也没有产生对应报告。因此本文仅记录
单体 one-pass prompt 迭代现象，不把上述方向写成实验结论。

后续若立项，应先定义新的实验计划和权威报告，再判断其相对 V14 的质量、成本、
延迟和稳定性。

## 16. 安全边界

本轮所有实现与远程命令保持：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
held_out_read = false
dspy_used = false
gliner_called_on_main_path = false
```

未接入：

```text
VetOrchestrator
ClinicalSafetyEvaluator
clinical safety pgvector
required_context
clinical safety OPA
```

所有 V14 结果均为：

```text
diagnostic_only = true
can_unblock_v8_phase = false
```

不得：

```text
进入 production projection
作为生产 fallback
解除 V8 live phase admission
替代 live span gate
接触 held-out
触发 DSPy
```

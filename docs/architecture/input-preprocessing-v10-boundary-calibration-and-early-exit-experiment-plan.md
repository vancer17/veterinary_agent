<!--
=============================================================================
文件: input-preprocessing-v10-boundary-calibration-and-early-exit-experiment-plan.md
作用: 定义 V10 测量修复、span boundary calibration、宏观语义契约修复、
      relation adapter 稳定化、gold winner 回归、早退机制和确认性实验矩阵。
范围: 覆盖 explicit offset fixture、GLiNER 粗定位、通用 tokenizer 边界校准、
      bounded candidate pool、macro act / skeleton / binding、relation adapter、
      canonical / participant regression、early exit、冷启动重复和 held-out 防护。
说明: V10 是 V9 根因后的修复与收敛轮次，不接入生产、不写业务状态、不解除
      V8 live phase gate。
维护: 当 V10 矩阵、指标、fixture、runner 或准入结论变化时同步更新。
=============================================================================
-->

# Input preprocessing V10 边界校准与早退实验计划

> **定位**：V10 是 V9 专项归因后的修复轮次，不是新的运行时架构，也不是生产消费准入。
>
> **核心纪律**：
> 1. 先修 measurement，再比较模型；
> 2. GLiNER 只做 coarse locator；
> 3. 候选池必须 role-eligible、去重、有预算；
> 4. macro 在 golden pool 下先修复 act / skeleton / binding；
> 5. relation adapter 必须版本化，禁止动态 calibration fallback；
> 6. 所有输入不得默认走完整深链路，必须验证早退与最短充分路径；
> 7. held-out 与 DSPy 默认冻结。

> 环境基线与基础冒烟见：
> [input-preprocessing-v10-environment-smoke-change-summary.md](input-preprocessing-v10-environment-smoke-change-summary.md)。
> 环境可用不改变本计划的阶段顺序，也不解除 V8 live phase admission。

## 1. V10 问题分解

V9 将 V8 blocker 拆成四类：

| V9 根因 | V10 问题 | 对应实验 |
|---|---|---|
| Measurement / fixture | quote-first、重复 mention owner、同 boundary 多角色、relation span 缺失是否污染指标 | Phase 0 |
| Span extractor | near-boundary 命中如何转成 exact-offset 候选，且不造成候选过载 | Phase 1 |
| Macro semantic output | acts 为空、claim skeleton 不稳定、optional binding 失败是否可分层修复 | Phase 2 |
| Relation adapter | V8 gold records 为何依赖 calibration batch，如何建立稳定调用契约 | Phase 3 |
| Canonical / participant | gold winner 是否能消费 macro 输出 | Phase 4 |
| 架构厚度 | 哪些组件没有继续执行价值，哪些输入可走最短路径 | Phase 5 |
| 稳定性 | finalist 是否冷启动稳定并泛化 | Phase 6 |

## 2. V10 runner 要求

新增或升级 runner 应支持：

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

顶层报告固定包含：

```text
report_version = v10-experiment-report-1
diagnostic_only = true
can_unblock_v8_phase = false
phase
lane: deterministic / golden / live / regression / early-exit
changed_variables
matrix path / sha256
model / prompt / schema / policy / gate version
span extractor / tokenizer / calibration version
candidate budget
cache status
cold run status
execution audit
safety boundary
```

默认拒绝读取文件名包含 `held_out` 的 fixture。DSPy 仍保持冻结。

## 3. Phase 0：measurement 与接口审计

### 3.1 FIXTURE-OFFSET

#### 目标

把 development fixture 从 quote-first 升级为显式 offset：

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

#### 要求

1. `start/end` 是唯一权威定位；
2. `text` 仅作为校验值；
3. 重复 mention 使用 owner-relative locator；
4. 不允许“全文第一次出现”作为默认定位；
5. fixture 迁移必须保留旧版本 digest，便于历史报告对照。

#### 指标

```text
fixture_field_count
offset_valid_rate
text_match_rate
owner_occurrence_valid_rate
source_block_valid_rate
migration_error_count
```

#### 退出

```text
offset_valid_rate = 100%
text_match_rate = 100%
owner_occurrence_valid_rate = 100%
```

### 3.2 FIELD-ROLE-SPLIT

#### 目标

拆分以下指标，不再使用单一全局 label accuracy：

```text
boundary coverage
field coverage
unambiguous label accuracy
role binding accuracy
selected span precision
```

同一 boundary 可以服务多个 field role。fixture 必须显式保留：

```text
support
target
relation
subject / participant
temporal
measurement
intent evidence
```

#### 指标

```text
field_role_count
unique_boundary_count
multi_role_boundary_count
unambiguous_label_field_count
label_evaluable_rate
role_binding_expected_count
```

#### 退出

```text
所有 expected field 都有 role；
同 boundary 多角色不再计为 label conflict；
不可评估字段显式标记 fixture_incomplete。
```

### 3.3 RELATION-SPAN-COMPLETE

#### 目标

补齐 expected relation 对应的 relation span。

#### 要求

```text
有 expected_relation 的 claim 必须有 relation span；
无法补齐的样本标记 fixture_incomplete；
fixture_incomplete 不进入 relation accuracy。
```

#### 指标

```text
expected_relation_count
relation_span_available_rate
fixture_incomplete_relation_count
```

### 3.4 INTERFACE-AUDIT

#### 目标

验证 V9 winner 与 V8/V10 接口没有实现矛盾。

#### 确定性对照

```text
V7 relation golden control
V9 relation gold records
V6 canonical golden target control
V8/V10 target span → quote → canonical recall
V9 participant gold mention → resolver
```

#### 判断

```text
原 golden control 失败：
  环境或依赖漂移。

原 golden control 成功、V10 span 注入失败：
  adapter / offset / fixture 转换错误。

两者均成功：
  可进入组件归因。
```

#### 退出

```text
interface audit 全部通过；
gold winner 可复现；
无 adapter contract contradiction。
```

## 4. Phase 1：span boundary calibration

### 4.1 SPAN-RAW

#### 目标

保留当前 GLiNER raw 输出作为 baseline。

#### 输出

```text
coarse span
label
score
raw offset
```

#### 指标

```text
coarse overlap recall
exact boundary recall
boundary precision
field coverage
false candidate rate
candidate count
latency
```

### 4.2 SPAN-CALIBRATE

#### 目标

将 near-boundary 粗块转为 exact-offset 候选池，而不是要求 GLiNER 一步输出最终 quote。

#### 管线

```text
GLiNER coarse span
→ generic tokenizer boundary alignment
→ nested / overlapping candidate generation
→ deterministic temporal / measurement candidates
→ punctuation / whitespace conservative trim
→ role eligibility
→ exact offset deduplication
→ role-aware overlap pruning
→ bounded candidate pool
```

#### 允许

```text
标点和空白保守 trim；
对齐 tokenizer boundary；
生成嵌套候选；
生成重叠但角色不同的候选；
确定性时间 / 度量 / 频率候选；
多 extractor provenance merge。
```

#### 禁止

```text
同义词替换；
编辑距离修复；
embedding 相似修复；
LLM 重写 quote；
医学关键词裁剪；
兽医边界词典 default；
全局 NMS 删除必要嵌套 span。
```

#### 对照变体

| Variant | 内容 |
|---|---|
| A | raw GLiNER |
| B | + punctuation trim |
| C | + tokenizer alignment |
| D | + nested candidate |
| E | + deterministic parser |
| F | + role eligibility |
| G | + role-aware pruning + budget |

#### 指标

```text
exact boundary recall
boundary precision
field coverage
per-role recall:
  target
  relation
  temporal
  measurement
  participant
  intent
candidate count
duplicate candidate rate
false candidate rate
latency
```

#### 退出

```text
calibrated live span pool 达到 V8 Phase 0 gate；
候选池规模受控；
exact boundary 不通过时不得用 overlap 放行。
```

### 4.3 SPAN-BUDGET

#### 目标

防止候选池过载导致 Macro LLM 注意力发散。

#### 变量

```text
per-role top-k
per-claim-region limit
per-source-block limit
per-turn total limit
rerank strategy
```

#### 候选池治理

```text
exact offset 去重；
extractor provenance merge；
同角色重叠裁剪；
保留跨角色嵌套；
保留 ambiguity alternatives；
超限进入 complexity review。
```

#### 指标

```text
candidate_count_per_role
candidate_count_per_claim
candidate_count_per_turn
macro_input_token_count
invalid_span_binding_rate
cross_claim_assignment_rate
claim_recall_delta
latency
```

#### 退出

```text
候选覆盖足够；
macro binding 不因候选数量下降；
无未解释候选爆炸。
```

### 4.4 SPAN-MODEL

#### 目标

比较 coarse locator 选型。

#### 对照

```text
当前 gliner-community/gliner_small-v2.5
urchade/gliner_multi-v2-1
其他多语言 GLiNER 权重
```

所有模型使用：

```text
同一 fixture；
同一 golden offset；
同一 label mode 集合；
同一 calibration pipeline；
同一 candidate budget；
同一 gate。
```

#### 退出

选出 coarse locator finalist，或证明现有 GLiNER 路线仍不达标。

### 4.5 SPANMARKER-CHINESE

#### 目标

评估 supervised boundary refinement。

#### 候选底座

```text
bert-base-chinese
chinese-roberta-wwm-ext
MacBERT / 中文 BERT 变体
```

#### 数据边界

```text
仅使用 development explicit-offset fixture；
5-fold 或 leave-one-sample-out；
held-out 完全冻结；
记录 fold variance 和 train/dev gap。
```

#### 指标

同 SPAN-CALIBRATE，另加：

```text
training sample count
fold variance
inference latency
model size
```

#### 退出

证明其作为 coarse locator 或 boundary refiner 的潜力；不得因小样本过拟合直接宣布 winner。

## 5. Phase 2：macro golden 修复

### 5.1 MACRO-ACT

#### 目标

修复 acts 全部为空的问题。

#### 输入

```text
raw input
TurnContext
owner-scoped golden candidate pool
role-eligible intent evidence candidates
```

#### 输出

```text
answer_now
wants_triage
correction
clarification_request
fact_statement_present
question_present
report_context_present
```

每个 true act 输出：

```text
evidence_span_id
confidence
```

acts 为空时必须输出：

```text
no_act_reason
```

#### 指标

```text
act precision / recall
fact / question / answer_now confusion
evidence span valid rate
empty act rate
no_act_reason validity
```

#### 退出

```text
acts 不再默认为空；
并行 act 可同时为 true；
true act evidence span 100% 有效。
```

### 5.2 MACRO-SKELETON

#### 目标

先修复 claim skeleton，不引入 optional binding 干扰。

#### 输出

```text
claim_id
support_span_id / support_anchor_span_ids
target_span_id
statement_type
coarse_type
confidence
```

#### 指标

```text
claim skeleton precision / recall
claim count accuracy
statement type accuracy
support envelope valid rate
target span accuracy
```

#### 退出

```text
claim skeleton 稳定；
claim granularity 可解释；
support envelope 不吞并无关 claim。
```

### 5.3 MACRO-BINDING

#### 目标

在 golden skeleton 上单独验证 optional binding。

#### 绑定

```text
relation_span_id
subject_span_id
participant_span_ids
temporal_span_id
measurement_span_id
```

#### 指标

```text
binding accuracy
invalid span reference
invalid span binding
cross claim assignment
optional field availability
```

#### 退出

```text
binding 失败可归因到具体 span role；
不得因 optional 缺失破坏 skeleton。
```

### 5.4 MACRO-FULL

#### 目标

组合 acts、skeleton 和 binding，保持一次宏观调用。

#### 输出结构

```text
Section A: discourse acts
Section B: claim skeleton
Section C: optional bindings
```

#### 指标

```text
act metrics
skeleton metrics
binding metrics
combined ready rate
invalid reference / binding
latency
cost
```

#### 退出

```text
full 不低于 act-only / skeleton-only 的可解释组合基线；
acts 与 claims 不互相污染。
```

### 5.5 MACRO-CANDIDATE-LOAD

#### 目标

验证候选池规模对宏观模型的影响。

#### 变量

```text
raw large pool
role-filtered pool
budgeted pool
claim-local pool
reranked pool
```

#### 指标

```text
candidate count
macro input token count
act accuracy
skeleton accuracy
binding accuracy
invalid span binding
latency
```

#### 退出

确定候选预算和 claim-local view 的最低可行配置。

## 6. Phase 3：relation adapter 稳定化

### 6.1 REL-SINGLE

#### 目标

验证 V8/V10 gold relation records 逐条输入是否稳定。

#### 输入

```text
support_quote
target_quote
relation_quote
```

#### 指标

```text
relation accuracy
unclear rate
format error rate
latency
```

### 6.2 REL-BATCH-FIXED

#### 目标

建立固定 batch 契约，消除 V9 的 batch context sensitivity。

#### 固定变量

```text
input serialization format
field order
batch size
missing field representation
prompt version
```

#### 指标

```text
batch size 1 / 4 / 8 accuracy
format sensitivity
batch order sensitivity
latency
```

### 6.3 REL-VERSIONED-FEWSHOT

#### 目标

如果确实需要 few-shot，则将其作为版本化 prompt 的一部分。

#### 硬边界

```text
few-shot 来自 train/dev；
不得使用 held-out；
不得运行时动态 calibration；
不得作为 fallback。
```

### 6.4 REL-MISSING

#### 目标

验证缺 relation span 时的显式失败语义。

#### 要求

```text
relation_span_missing
→ relation_input_not_evaluable
→ review_required
```

不得：

```text
用关键词补 relation；
把缺失判为 unclear；
把缺失解释为用户未提供。
```

### Phase 3 退出

```text
V8/V10 gold relation records 不依赖动态 calibration；
development 3 次冷调用稳定；
missing span 显式 not evaluable。
```

## 7. Phase 4：gold winner 回归

### 7.1 CAN-REGRESSION

#### 流程

```text
macro target_span_id
→ code resolves target_quote
→ V6 canonical direct recall
→ candidate / selected canonical
```

#### 指标

```text
target span availability
candidate recall
canonical accuracy
under-confirmation
false confirmation
selected candidate validity
```

#### 归因

```text
target span missing：
  macro target binding blocker。

target span 可用但 recall 失败：
  canonical adapter / vocabulary blocker。
```

### 7.2 PARTICIPANT-REGRESSION

#### 流程

```text
macro participant_span_ids
→ code resolves mention
→ TurnContext resolver
```

#### 硬边界

```text
invented entity = 0
resolved + empty entity = 0
cross claim assignment = 0
```

#### 指标

```text
participant mention recall
role assignment accuracy
entity resolution accuracy
ambiguous detection rate
```

### 7.3 Temporal / measurement regression

验证 parser 输入可用性和绑定正确性：

```text
temporal_quote availability
measurement_quote availability
relation binding accuracy
unresolved reason distribution
over-precision rate
```

## 8. Phase 5：早退与最短充分路径

### 8.1 Continuation Gate

每个组件执行前必须记录：

```text
component
input state
downstream consumers
potential decision impact
prerequisite status
budget status
execute / skip / early_exit / blocked
reason
```

### 8.2 EARLY-MINIMAL

#### 目标

验证简单输入是否无需完整深链路。

#### 对照

```text
A. 完整深链路
B. 现有生产语义路径
C. 简单薄声明路径
D. early exit 路径
```

#### 指标

```text
slot update quality
answer_now respected rate
unknown slot reduction
urgent / blocked regression
model call count
latency
cost
review rate
```

### 8.3 EARLY-VOI

#### 目标

验证组件是否具备继续执行价值。

#### 方法

逐个关闭：

```text
GLiNER
boundary calibration
macro
relation
canonical
participant
temporal
measurement
aggregate decomposition
```

比较下游决策是否变化。

#### 判断

关闭后以下均不变时，该组件对该类输入无执行价值：

```text
问诊状态；
回答策略；
安全结论；
用户响应。
```

### 8.4 EARLY-BUDGET

对照模型调用预算：

```text
0 call
1 call
2 calls
3 calls
unbounded research path
```

输出质量 / 延迟 / 成本 Pareto 曲线。

### 8.5 EARLY-ROUTER

验证 lane 路由：

```text
simple
standard
deep
review
```

指标：

```text
route accuracy
false simple rate
false deep rate
safety miss rate
latency saving
cost saving
```

### 8.6 EARLY-FAILURE

验证上游失败是否阻断下游：

```text
span failed → no macro；
quote failed → no projection；
relation span missing → no classifier；
canonical no candidate → no confirmed。
```

指标：

```text
downstream call count
failure propagation
blocked reason correctness
false pass rate
```

### Phase 5 退出

```text
质量不回退；
安全边界保留；
model call / latency / cost 显著下降；
早退 reason 可审计。
```

## 9. Phase 6：重复与确认性实验

### 9.1 REP-COLD

只对 finalist 执行：

```text
calibrated span pool
macro winner
relation adapter winner
early-exit lane
```

要求：

```text
cold_run_count >= 3；
cache_hit_count = 0；
unique output signature stable；
invalid span reference = 0；
invalid binding = 0。
```

同时报告：

```text
stable-and-correct rate
stable-but-wrong rate
unstable rate
p50 / p95 latency
token usage
cost
```

### 9.2 HELD-OUT-V10

#### 进入条件

```text
development winner 冻结；
model / prompt / schema / fixture / gate / extractor / tokenizer / budget 全部冻结。
```

#### 指标

同 development，并报告：

```text
development vs held-out delta
```

DSPy 仍不得读取 held-out。

## 10. NEG-V10

必须阻断：

```text
无效 offset；
span text 与原文不一致；
重复 mention owner 错位；
target 不在 support 内；
relation span 缺失仍调用 classifier；
候选池超预算；
role-ineligible span 被绑定；
模型自由输出 quote；
resolved participant 为空；
发明 entity / canonical；
canonical selected 不在候选；
projection 消费 blocked claim；
early exit 绕过安全主路径；
early exit 将上游失败解释为用户未提供。
```

指标：

```text
gate_blocked_as_expected_rate
false_pass_rate
gate_reason_correct_rate
```

## 11. ASYNC-V10

继续验证：

```text
bounded queue；
queue full；
worker claim / complete / fail；
dead letter；
trace completeness；
失败隔离；
不写业务状态；
不触发 evaluator / OPA。
```

主链路延迟 / 错误率差值为 0 只能说明实验路径未接入主链路，不得解释为生产无影响。

## 12. 复现与执行顺序

### 推荐顺序

```text
1. FIXTURE-OFFSET
2. FIELD-ROLE-SPLIT
3. RELATION-SPAN-COMPLETE
4. INTERFACE-AUDIT
5. SPAN-RAW
6. SPAN-CALIBRATE
7. SPAN-BUDGET
8. MACRO-ACT
9. MACRO-SKELETON
10. MACRO-BINDING
11. MACRO-FULL
12. REL-SINGLE / REL-BATCH-FIXED
13. CAN-REGRESSION / PARTICIPANT-REGRESSION
14. EARLY-MINIMAL / EARLY-VOI / EARLY-BUDGET
15. 有限 live retest
16. REP-COLD
17. HELD-OUT-V10
```

### 禁止顺序

```text
fixture 未修复先比较模型；
span 未达标执行 macro live；
macro 未达标执行 adapter winner；
relation adapter 未稳定执行 integration；
early exit 未验证接入生产；
held-out 未冻结版本执行确认。
```

## 13. 安全边界

V10 全程保持：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
```

不得接入：

```text
VetOrchestrator
ClinicalSafetyEvaluator
clinical safety pgvector
required_context
clinical safety OPA
```

所有 golden / gold injection / early-exit 结果均不得进入 production projection，不得作为生产 fallback，也不得解除 V8 live phase admission。

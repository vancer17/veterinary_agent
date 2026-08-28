<!--
=============================================================================
文件: input-preprocessing-v3-third-round-shadow-experiment-change-summary.md
作用: 记录输入前置预处理 V3 第三轮快速验证与 shadow 实验的实现、结果和准入结论。
范围: 覆盖 Stage 1 任务拆分、derived count、item-keyed verifier、确定性参与者继承、
      constrained canonical linking、粗粒度类型兼容矩阵、质量门禁、development /
      held-out fixture、持久化实验队列和远程真实模型 shadow。
说明: 本文只沉淀工程结论；不改变生产问诊、临床安全召回、required_context 或 OPA 裁决。
维护: 当 V3 契约、实验矩阵、远程报告结论或迁移准入条件变化时同步更新。
=============================================================================
-->

# 输入前置预处理 V3 第三轮 Shadow 实验变更总结

> **文档状态**：第三轮实验实现与远程观察已完成；结果未达到生产消费准入
>
> **结论**：V3 的结构性修复方向有效，但真实模型仍不能稳定履行 Stage 1 raw schema。derived count、独立 Turn Intent Analyzer、item-keyed verifier、确定性参与者继承和 selected candidate 约束在 ideal control 与部分 golden Stage 1 诊断中有效；development set 远程 exploratory shadow 只有 3 / 12 实验通过。不能进入生产消费，也不能接入 `VetOrchestrator`。

## 1. 实验目标

第三轮实验验证 V3 是否修复第二轮 V2 暴露的问题：

1. count 漂移是否来自模型重复维护可推导字段；
2. Stage 1 是否需要拆分成 intent、scope segmentation、participant binding；
3. Stage 2 是否必须按 expected item 逐项验证；
4. participants 是否应由确定性代码继承，而 Stage 2 只输出 verification 状态；
5. canonical 是否必须通过 `selected_candidate_id` 引用候选，而不能由模型自由生成 ID；
6. 粗粒度 semantic class / canonical type / participant role / entity type 是否足以阻断事件与状态错配；
7. 负例是否仍能全部 blocking；
8. 异步 shadow 是否具备持久化有界队列、死信和失败隔离语义；
9. clinical safety 是否继续严格 report-only。

本轮仍是探索性 development set 实验。held-out set 仅在本地 ideal control 中验证契约，未参与远程真实模型确认性准入。

## 2. 实现范围

### 2.1 V3 契约

新增：

1. `V3TurnIntentRaw`;
2. `V3ScopeSegmentationRawOutput`;
3. `V3ParticipantBindingRawOutput`;
4. `V3Stage1Output`;
5. `V3CandidateSet`;
6. `V3ItemVerificationRaw`;
7. `V3VerifiedEvidence`;
8. `V3InputAnalysisResult`;
9. `V3QualityGateResult`。

关键约束：

1. raw Stage 1 schema 不包含 `profile` 或 `expected_evidence_count`;
2. atomic claim 必须携带 `initial_assertion`;
3. shared assertion scope 必须携带 `scope_assertion` 和非空 `items`;
4. `expected_evidence_count` 和 `profile_expected_fact_count` 由代码派生；
5. Stage 2 不输出 canonical ID、新 item、新 segment、subject 或 participants;
6. Stage 2 只输出 `selected_candidate_id`;
7. final `canonical_id` 由代码根据 selected candidate 反查；
8. participants 由 Stage 1 确定性继承到 final evidence;
9. Stage 2 只输出 participant verification 状态；
10. 无候选时不得 confirmed，必须进入 not_found / unmapped + review。

### 2.2 Stage 1 拆分

V3 将 V2 的单个 Stage 1 拆为：

```text
Turn Intent Analyzer
→ Scope Segmentation Analyzer
→ Participant Binding Analyzer
→ deterministic Stage 1 assembler
```

独立 Turn Intent Analyzer 只输出：

```text
answer_now
wants_triage
correction
confidence
rationale
```

它不输出医学事实、segment 或 canonical。

Scope Segmentation Analyzer 只输出：

```text
atomic claim / shared assertion scope
source_text
analysis_text
discourse_role
initial_assertion / scope_assertion
scope items
```

Participant Binding Analyzer 只输出每个 expected item 的：

```text
subject
action_agent
action_recipient
experiencer
action_object
subject candidates
resolution status / method
```

### 2.3 Stage 2 item verifier

Stage 2 改为 item-keyed verifier：

```text
一个 expected item
→ candidate set
→ 一次结构化验证调用
→ final evidence
```

Stage 2 输入不包含完整用户原文，只包含当前 item 的：

```text
source / analysis text
initial assertion
subject
inherited participants
candidate set
```

Stage 2 输出：

```text
assertion_verification
mapping_status
selected_candidate_id
participant_verification
temporal verification
measurement verification
confidence / rationale
```

Stage 2 不能：

1. 新增 item;
2. 合并 item;
3. 漏掉 item;
4. 替换 participants;
5. 自由生成 canonical ID;
6. 把 intervention / feeding / medication 映射成普通状态概念。

### 2.4 Constrained canonical linking

新增候选引用链路：

```text
candidate retriever
→ candidates[]
→ selected_candidate_id
→ code resolves canonical_id
```

候选来自当前版本化 canonical vocabulary 的 alias embedding。候选召回使用：

```text
candidate limit = 8
minimum cosine score = 0.72
vocabulary = input-preprocessing-dev-v1
recall version = input-preprocessing-dev-v1:top-8:min-0.72
```

该阈值只是本轮 exploratory 参数，不是生产承诺。

粗粒度兼容约束包括：

```text
semantic class
canonical type
participant role
entity type
allowed subject types
```

未新增疾病、症状或医学分支枚举。

### 2.5 质量门禁

新增或强化：

1. `v3_turn_context`;
2. `v3_stage1_contract`;
3. `v3_entity_subject_role`;
4. `v3_expected_evidence_coverage`;
5. `v3_participant_inheritance`;
6. `v3_assertion_verification`;
7. `v3_canonical_registry`;
8. `v3_type_compatibility`;
9. `v3_unmapped_review`;
10. `v3_suspicious_empty`。

Gate 仍只校验结构化契约、证据锚点、主体、事件角色和候选引用，不扫描原文做医学判断。

### 2.6 异步 shadow 实验队列

新增 `FileAsyncShadowQueueV3`，用于验证：

1. 有界队列；
2. 显式 queue full；
3. 持久化 snapshot；
4. worker claim / complete / fail；
5. 死信记录；
6. trace 持久化；
7. 不写业务状态；
8. 不触发临床安全 evaluator。

该队列是实验实现，不是生产 worker，也未接入 `VetOrchestrator`。

## 3. Fixture 与实验矩阵

新增：

```text
tests/fixtures/input_preprocessing/third_round_shadow_matrix.json
tests/fixtures/input_preprocessing/third_round_held_out_matrix.json
```

development set 覆盖：

1. 并列 denied / normal;
2. 用户动作；
3. 医疗提供者动作；
4. 其他宠物症状；
5. 当前宠物 denied;
6. hypothetical;
7. unmapped mention;
8. answer-now。

held-out set 覆盖同类现象但不同表达：

1. 并列 denied / normal 顺序变化；
2. 狗粮场景；
3. 护士动作；
4. 另一只狗症状；
5. 不同 answer-now 表达；
6. 不同 unmapped mention。

实验矩阵包含：

| 实验 | 目标 |
|---|---|
| S1-COUNT | 验证 derived count 是否消除模型重复维护字段 |
| S1-SCOPE | 验证 atomic / shared scope 声明 |
| S1-ROLE | 验证主体和事件角色初绑定 |
| S1-INTENT | 验证控制意图独立识别 |
| S2-ITEM | 验证 Stage 2 逐 item 覆盖 |
| S2-PARTICIPANT | 验证参与者确定性继承 |
| CAN-LINK | 验证 selected candidate 引用能否消除假确认 |
| CAN-TYPE | 验证粗粒度类型兼容矩阵 |
| REP | 验证重复稳定性 |
| NEG | 验证负例全部阻断 |
| ASYNC | 验证持久化有界队列和死信 |
| CS | 验证临床安全 report-only 边界 |

## 4. 本地 Ideal Control 结果

命令：

```bash
uv run python -m vet_agent.input_preprocessing.v3_experiments \
  --mode ideal \
  --repeat-override 1 \
  --output-dir .data/evaluations/input-preprocessing-v3-local-final
```

结果：

```text
experiment_count = 12
passed = 12
failed = 0
```

本地 ideal control 证明：

1. V3 契约可以表达 shared assertion scope 和 event participants;
2. derived count 可以稳定组装；
3. golden Stage 1 / Stage 2 可以通过全部 gate;
4. 8 个确定性负例全部 `gate_blocked_as_expected`;
5. selected candidate 引用可以阻止无候选 confirmed;
6. answer-now 分支可以独立复现；
7. held-out ideal control 在 3 次重复下 signature 稳定；
8. 异步队列 overflow / complete / dead letter 语义可执行；
9. clinical safety 保持 `downstream_evaluation=not_implemented`。

Ideal control 不是生产结论，也不能作为 fallback。

本地全量测试结果：

```text
240 passed
43 skipped
```

V3 相关 scoped mypy 检查通过。

## 5. 远程真实模型 Shadow 结果

### 5.1 执行方式

执行方式：

1. 通过 SSH 隧道访问远程 LiteLLM;
2. 使用 `qwen-plus`;
3. 每个 case 使用独立 analyzer / Qwen client;
4. 共享版本化 candidate retriever；
5. development set;
6. exploratory phase;
7. `repeat_override=1`;
8. clinical baseline 仅做结构化对比；
9. 不进入 evaluator / pgvector / required_context / OPA。

最终报告：

```text
.data/evaluations/input-preprocessing-v3-experiments-remote-isolated-final-2/input-preprocessing-v3-1744a6fd8789.json
```

报告时间：

```text
2026-08-25T14:18:12.583502+08:00
```

SHA-256：

```text
161d387522fe07d92eb9eaf3cf7d5ac819a624ff6f0dc8acce05766983f1c1cc
```

固定版本：

```text
model = qwen-plus
prompt_version = v3-dev-20260825-5
schema_version = v3
vocabulary_version = input-preprocessing-dev-v1
candidate_recall_version = input-preprocessing-dev-v1:top-8:min-0.72
fixture_version = 1f1b7c15adf85eeb6ed7d7b6b9099c448e03d9bd0cc1bf087e6bc52073f8c393
gate_version = v3-gates-20260825-2
analyzer isolation = per-case-fresh-qwen-client-shared-candidate-retriever
```

### 5.2 总览

```text
experiment_count = 12
passed = 3
failed = 9
```

| 实验 | 结果 | 主要观察 |
|---|---:|---|
| S1-COUNT | failed | Stage 1 scope raw schema 非法，未进入 derived count 评估 |
| S1-SCOPE | failed | 真实模型仍未稳定输出合法 atomic / shared scope |
| S1-ROLE | failed | Stage 1 raw schema 非法 |
| S1-INTENT | passed | `answer_now` 独立识别且未混入 fact segment |
| S2-ITEM | failed | item coverage = 100%，但 assertion verification 和 semantic exact match 未达标 |
| S2-PARTICIPANT | failed | participant retention = 100%，但 Stage 2 semantic mismatch |
| CAN-LINK | failed | 上游 Stage 1 schema 失败，专用 V 样本未进入 linker |
| CAN-TYPE | failed | Stage 2 semantic mismatch |
| REP | failed | D/E/V 均未获得可比较的稳定真实输出 |
| NEG | passed | 8 / 8 负例全部按预期 blocking |
| ASYNC | passed | 队列溢出、worker 死信、trace 和失败隔离有效 |
| CS | failed | 上游失败，不能得到可消费投影；evaluator / OPA 未调用 |

### 5.3 Stage 1 schema 遵循率

D/E/V 的真实 Stage 1 均出现 raw schema 非法：

```text
atomic_claim_requires_initial_assertion
shared_scope_requires_assertion_and_items
```

主要表现：

1. 模型输出 atomic claim 但省略 `initial_assertion`;
2. 模型声明 shared scope 但缺少 scope assertion 或 items;
3. 并列表达仍可能被拆散；
4. schema 在组装前被阻断，未产生不可审计空结果。

结论：

> 仅把 count/profile 改为代码派生不足以修复 Stage 1。当前 raw schema 仍要求同一次结构化输出同时完成 segmentation、scope 声明和 assertion 初分类，真实模型遵循率不足。

下一轮需要继续拆分 Stage 1：

```text
segmentation / shared scope organizer
→ per-item assertion classifier
→ participant binding
```

### 5.4 独立 Turn Intent Analyzer

S1-INTENT 通过：

```text
answer_now_recognition = passed
intent_fact_separation_rate = 1.0
latency = 3441 ms
```

该能力仍是后续最合适的低风险渐进消费点，但本轮尚未接入 API shadow worker 和生产编排，不能直接灰度。

### 5.5 Stage 2 item verifier

golden Stage 1 诊断中：

```text
item_coverage = 1.0
unexpected_item = 0
item_merge = 0
participant_retention = 1.0
```

这证明 item-keyed verifier 能修复 V2 的漏项、合并和 participants 丢失问题。

但 semantic exact match 未达标：

1. D 样本 10 个 item 中 9 个 confirmed，1 个 `黑便` 被输出为 unmapped;
2. E 样本中 `另一只猫也在呕吐` 被输出为 unmapped，而期望为 confirmed vomiting;
3. D 样本 1 个 item 的 assertion verification 为 unresolved;
4. Stage 2 的 canonical 选择仍保守，存在 under-confirmation;
5. assertion verification 与 canonical mapping 的独立性仍不足。

结论：

> item-keyed verifier 的结构边界有效，但 verifier 的 semantic 质量未达到准入。

### 5.6 Constrained canonical linking

专用 CAN-LINK 样本因 Stage 1 schema 失败，未进入 linker，因此本轮不能宣称 CAN-LINK 生产假设已证明。

但从 S2 golden Stage 1 诊断可观察：

```text
confirmed_without_candidates = 0
invented_canonical = 0
unmapped_review_rate = 1.0
```

同时，D 样本 10 个 item 中 9 个均从候选引用正确 confirmed。

结论：

> selected candidate 引用显著降低了假确认风险，但真实 Stage 1 失败和 Stage 2 under-confirmation 使该链路仍未达标。

### 5.7 类型兼容矩阵

S2-PARTICIPANT 与 CAN-TYPE 的结构指标显示：

```text
participant_retention = 1.0
subject_wrong_binding_count = 0
```

但 CAN-TYPE 实验 overall failed，原因是 Stage 2 semantic mismatch，而不是 gate 未能阻断粗粒度类型错配。

结论：

> 粗粒度类型矩阵方向仍可保留；失败不应通过扩张医学枚举解决。

### 5.8 负例与 Fail-Fast

8 个确定性负例全部通过：

```text
gate_blocked_as_expected_rate = 1.0
false_pass_rate = 0
```

覆盖：

1. selected candidate 不在候选集；
2. confirmed 无候选；
3. 发明 canonical;
4. expected item 缺失；
5. Stage 2 item 合并；
6. participants 被替换；
7. assertion 未验证；
8. unmapped 缺 review。

结论：

> V3 gate 是有效架构边界，不是报告附属品。

### 5.9 异步 shadow

ASYNC 通过：

```text
queue full 显式拒绝
worker complete 显式持久化
worker timeout 进入 dead letter
dead_letter_count = 1
trace_incomplete_count = 0
business_state_written = false
clinical_safety_evaluator_called = false
```

结论：

> 持久化实验队列具备 report-only worker 语义，但仍不是生产 API shadow worker。

### 5.10 Clinical safety

CS failed 是上游 Stage 1 schema 失败后的正确阻断，不是临床安全回归。

报告保持：

```text
downstream_evaluation = not_implemented
evaluator_called = false
opa_called = false
```

结论：

> 临床安全继续禁止接入 evaluator、pgvector、required_context 和 OPA。

## 6. 架构判断

### 6.1 已证明

1. raw schema 去除 derived count 是可行的契约方向；
2. Turn Intent Analyzer 可以独立稳定识别 `answer_now`;
3. item-keyed verifier 可以做到 100% expected item coverage;
4. 确定性 participant inheritance 可以做到 100% retention;
5. selected candidate 引用可以阻断无候选 confirmed;
6. 粗粒度类型矩阵不会退化成医学枚举；
7. 8 类负例可以全部 blocking;
8. 持久化有界实验队列可以显式处理 overflow / dead letter;
9. clinical safety report-only 边界可执行。

### 6.2 未证明

1. 真实模型 D baseline 3/3 exact match;
2. 真实模型 E baseline 3/3 exact match;
3. Stage 1 raw schema 稳定；
4. Stage 2 assertion verification 稳定；
5. Stage 2 canonical 选择达到 semantic exact match;
6. CAN-LINK 专用 V 样本可进入并退出；
7. 每样本 3 次真实输出 signature 完全一致；
8. held-out 真实模型确认性实验通过；
9. API shadow 可接入 `VetOrchestrator`;
10. clinical safety adapter 可评估接入。

## 7. 准入结论

当前结果不允许：

1. 生产消费问诊事实；
2. 生产消费 clinical safety projection;
3. 接入 clinical safety evaluator;
4. 接入 `VetOrchestrator`;
5. 将 file-backed experiment queue 用于生产；
6. 将 golden Stage 1 作为 fallback;
7. 直接灰度 `answer_now`;
8. 修改回答充分性策略掩盖上游失败；
9. 放宽 Stage 1 schema、expected coverage、participant inheritance 或 candidate audit。

允许继续：

1. report-only shadow;
2. deterministic gate 回归；
3. Stage 1 继续拆分；
4. Stage 2 assertion / canonical verifier 分离；
5. held-out set 保留到 development set 通过后再执行；
6. 生产级异步 worker 设计。

## 8. 下一轮修复优先级

### 8.1 Stage 1 继续拆分

将 scope organizer 与 assertion classifier 分离：

```text
Scope Organizer:
  source span
  atomic / shared kind
  items
  discourse role

Assertion Classifier:
  per item initial assertion
```

不要继续用一个更大 prompt 同时承担两者。

### 8.2 Stage 2 拆分 assertion 与 canonical

当前 canonical 未命中会影响 assertion verification。下一轮应拆成：

```text
Item Assertion Verifier
→ Candidate Linker / Selector
→ Participant Compatibility Verifier
```

明确：

```text
canonical not found 不等于 assertion 未验证
```

### 8.3 修复 under-confirmation

重点样本：

```text
黑便 → bloody_stool
另一只猫呕吐 → other_pet vomiting，但不得进入当前宠物投影
```

修复方式必须继续通过：

1. candidate audit;
2. subject compatibility;
3. selected candidate;
4. gate;
5. review。

不得加入症状关键词规则。

### 8.4 真实模型重复稳定性

在 development set 达到单次通过后，才执行：

```text
每样本至少 3 次
signature 完全一致
```

held-out set 不得参与探索期调参。

### 8.5 生产级异步 shadow

在 Stage 1 / Stage 2 / canonical linking 达标后，再设计：

1. PostgreSQL 有界队列；
2. 幂等 snapshot;
3. worker lease;
4. retry / dead letter;
5. 采样、限流、熔断；
6. trace 持久化；
7. `VetOrchestrator` 只采样入队。

## 9. 复现命令

本地 ideal control：

```bash
uv run python -m vet_agent.input_preprocessing.v3_experiments \
  --mode ideal
```

远程 exploratory shadow：

```bash
INPUT_PREPROCESSING_LITELLM_API_KEY=<key> \
INPUT_PREPROCESSING_WITH_CLINICAL_BASELINE=true \
scripts/integration/run-input-preprocessing-v3-experiment-smoke.sh \
  --repeat-override 1 \
  --output-dir .data/evaluations/input-preprocessing-v3-experiments-remote-isolated
```

确认性实验必须显式使用 held-out fixture：

```bash
uv run python -m vet_agent.input_preprocessing.v3_experiments \
  --matrix tests/fixtures/input_preprocessing/third_round_held_out_matrix.json \
  --mode shadow \
  --phase confirmatory
```

真实模型实验依赖远程 LiteLLM。依赖不可用时必须失败，不得回退关键词、宽松 JSON 或本地规则。

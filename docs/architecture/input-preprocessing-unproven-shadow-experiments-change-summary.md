<!--
=============================================================================
文件: docs/architecture/input-preprocessing-unproven-shadow-experiments-change-summary.md
作用: 记录“尚未证明”风险的专项 shadow 实验矩阵、执行方式、当前真实模型结论和边界。
范围: 覆盖并列否定、主体歧义、非法 canonical、重复稳定性、问诊决策分支、
      临床安全结构对比、canonical 词表治理和异步 API shadow 队列骨架。
说明: 所有实验仍为 shadow / report-only，不写业务状态，不影响响应或临床安全主路径。
维护: 当实验矩阵、契约字段、质量门禁或远程服务验证结果调整时同步更新本文。
=============================================================================
-->

# 输入前置预处理未证明风险 Shadow 实验变更总结

> **文档状态**：实验框架已实现；D/E 已完成远程真实模型初跑，仍未达到生产消费条件
>
> **架构结论入口**：[agent-input-preprocessing-shadow-experiment-architecture-guidance.md](/home/vancer17/veterinary_agent/docs/architecture/agent-input-preprocessing-shadow-experiment-architecture-guidance.md)

## 1. 实验目标

上一阶段 ideal injection 已证明：

```text
当结构化事实正确非空时，
现有 ConsultationStateService / OPA 可以回答、收敛并避免重复追问。
```

本阶段继续验证尚未证明的部分：

1. 真实模型能否覆盖共享否定范围；
2. 多宠物主体和 possible 语义是否稳定；
3. `not_found` 与非法 canonical 是否稳定 Fail-Fast；
4. 重复运行是否漂移可解释；
5. `answer_now` 与问诊事实的渐进消费是否有行为价值；
6. 临床安全新旧结构化输出差异是否可观察；
7. canonical 词表 alias 召回与冲突是否可治理；
8. API shadow 是否能以异步、有界、失败隔离方式执行。

## 2. 新增实验矩阵

新增：

```text
tests/fixtures/input_preprocessing/unproven_shadow_matrix.json
```

包含实验：

| 实验 | 目标 |
|---|---|
| `D_parallel_negation_baseline` | 使用模型 Stage 1，验证共享否定全量覆盖 |
| `D_parallel_negation_golden_stage1` | 固定正确 Stage 1，隔离验证 Stage 2 拆分能力 |
| `E_subject_binding_and_possible` | 验证主体绑定、用户行为、历史与假设表达 |
| `N_invalid_canonical_fail_fast` | 验证 `not_found`、词表外概念和 unmapped mention 阻断 |
| `R_repeat_stability` | 验证重复运行输出稳定性与漂移指标 |
| `B_consultation_decision_branches` | 对比 empty baseline、answer-now-only、full projection |
| `CS_clinical_safety_structural_compare` | 只做临床安全结构化输出对比 |
| `V_canonical_alias_recall_and_collisions` | 审计 alias 召回与冲突 |

## 3. 契约与门禁增强

### 3.1 Segment evidence 覆盖契约

`SegmentModel` 新增：

```text
expected_evidence_count
```

用于表达一个 segment 可派生多个 evidence，例如：

```text
没有呕吐、干呕、反流、流涎或舔唇
→ expected_evidence_count = 5
```

新增：

```text
segment_evidence_coverage gate
```

当 Stage 2 实际 evidence 数量与 Stage 1 声明数量不一致时阻断。

### 3.2 SubjectBinding 增强

新增：

```text
resolution_status
subject_candidates
```

多宠物歧义应表达：

```text
subject_reference = subject_ambiguous
subject_type = unknown
resolution_method = subject_ambiguous
resolution_status = ambiguous
subject_candidates = [current_pet, other_cat]
```

Gate 会校验：

1. subject candidate 必须来自 TurnContext；
2. ambiguous 必须有候选；
3. missing/ambiguous 状态不能伪装成 resolved；
4. 不得默认当前宠物。

### 3.3 UnmappedMention

新增：

```text
UnmappedMention
```

用于区分：

```text
用户未提供事实
模型漏抽
模型识别到表达但 canonical 不存在
```

`not_found` 是状态，不允许作为 `canonical_id`。显式 unmapped mention 会进入：

```text
suspicious_empty / canonical_not_found
review_required
```

不会进入领域投影。

## 4. 决策分支实验

每个可模拟回合现在输出三条内存分支：

```text
baseline_empty_success
answer_now_only
full projection
```

这些分支：

1. 不写 `consultation_states`；
2. 不影响 API 响应；
3. 不共享状态；
4. 只输出 decision 对比。

C 样本可验证：

```text
baseline_empty_success → ask
answer_now_only        → answer
full projection        → answer
```

从而单独证明 `answer_now` 的消费价值。

## 5. 临床安全结构对比

新增：

```python
compare_clinical_safety_structures(...)
```

只比较：

```text
baseline present count
new present count
baseline denied count
new denied count
coverage gap
```

不执行医学判断，不接入：

```text
ClinicalSafetyEvaluator
pgvector
required_context
clinical safety OPA
```

报告保持：

```text
downstream_evaluation = not_implemented
```

## 6. Canonical 词表治理

新增：

```text
audit_vocabulary_static(...)
audit_vocabulary(...)
```

静态审计：

1. term 数；
2. alias 数；
3. duplicate alias；
4. review required。

真实 embedding 审计：

1. alias top-k 召回；
2. alias 冲突；
3. recall hit rate；
4. review queue 触发。

最近邻仍只作为治理审计，不直接写事实。

## 7. 异步 API shadow 骨架

新增：

```text
AsyncShadowTurnSnapshot
InMemoryAsyncShadowQueue
```

设计边界：

1. 队列有界；
2. 溢出直接返回 false；
3. 不阻塞主响应；
4. 不在请求线程内执行数十秒模型链路；
5. 失败隔离到 worker；
6. 尚未接入真实 orchestrator。

这是 API metadata shadow 的前置工程骨架，不是生产启用。

## 8. 运行方式

### Ideal 控制组

```bash
uv run python -m vet_agent.input_preprocessing.experiments \
  --mode ideal
```

当前结果：

```text
8/8 experiments passed
```

### 远程真实服务实验

新增：

```text
scripts/integration/run-input-preprocessing-experiment-smoke.sh
```

运行单个实验：

```bash
INPUT_PREPROCESSING_LITELLM_API_KEY=<key> \
INPUT_PREPROCESSING_EXPERIMENTS=D_parallel_negation_golden_stage1 \
scripts/integration/run-input-preprocessing-experiment-smoke.sh
```

可使用：

```text
--repeat-override 1
```

降低首次真实服务验证成本。

## 9. 当前真实模型结果

### 9.1 D baseline

报告：

```text
.data/evaluations/input-preprocessing-experiments-remote/input-preprocessing-experiments-633635bec6b1.json
```

结果：

```text
3 次运行：
  2 次 semantic exact match
  1 次模型将全部主体输出为 subject_ambiguous 且缺少必要字段

precision：
  2 次为 1.0
  1 次为 0.0

stability：
  unique_output_count = 2
  majority_agreement = 2/3
```

结论：

> 当前模型 Stage 1 输出仍不稳定；一次漂移被 evidence contract gate 阻断，未进入投影。

### 9.2 D golden Stage 1

报告：

```text
.data/evaluations/input-preprocessing-experiments-remote/input-preprocessing-experiments-3f737ae1b965.json
```

结果：

```text
3/3 passed
semantic exact match = 3/3
precision = 1.0
recall = 1.0
denied_as_present = 0
normal_as_denied = 0
gate failed = 0
unique_output_count = 1
```

结论：

> 当 Stage 1 提供正确单事实 / expected evidence count 视图时，Stage 2 能稳定拆出全部并列否定。当前 D 的主要瓶颈在 Stage 1 segmentation，而不是 Stage 2 verifier。

### 9.3 E 主体与 possible

报告：

```text
.data/evaluations/input-preprocessing-experiments-remote/input-preprocessing-e23b1fc8dd85.json
```

单次运行结果：

```text
E1 单宠物省略主语：passed
E2 另一只猫明确指代：passed
E3 多宠物歧义：failed / gate blocked
E4 用户动作：failed，被绑定成 current_pet
E5 historical / hypothetical：passed
```

E3 问题：

```text
发明 multi_pet_context
vomiting 输出 unknown
subject_ambiguous 缺少 status / candidates
```

这些均被 evidence contract gate 阻断。

E4 问题：

```text
我前天开始给它换新猫粮
动作主体应绑定 user
模型误绑定 current_pet
```

结论：

> E1/E2/E5 已稳定；多宠物歧义与用户动作主体仍是真实模型薄弱点，需要继续强化 Stage 1 主体视图和 schema 示例。

### 9.4 E golden Stage 1

在补充 Stage 1 subject binding 契约并将该绑定合并回 Stage 2 的可选主体字段后，重新运行：

```text
INPUT_PREPROCESSING_EXPERIMENTS=E_subject_binding_golden_stage1
--repeat-override 1
```

报告：

```text
.data/evaluations/input-preprocessing-experiments-remote/input-preprocessing-experiments-4e97f1f86ae4.json
```

结果：

```text
5/5 passed
semantic exact match = 5/5
gate failed = 0
denied_as_present = 0
normal_as_denied = 0
```

结论：

> 当 Stage 1 提供正确主体绑定时，Stage 2 可以保留 user、other_pet、subject_ambiguous、possible、historical 和 hypothetical 语义。E 的主要瓶颈同样集中在 Stage 1 主体视图。

## 10. 尚未进入下一阶段的原因

1. D baseline 只有 2/3 稳定；
2. E3 / E4 仍存在主体漂移；
3. 远程实验尚未完成矩阵全量 3 次运行；
4. API shadow 尚未接入真实 orchestrator；
5. 临床安全仍为结构对比，不能进入 evaluator；
6. canonical 词表仍是评估版；
7. 尚未建立人工 review 结果回流。

## 11. 后续动作

1. 优先强化 Stage 1 主体绑定输出；
2. Stage 1 中显式拆分共享否定并列项；
3. 将 E3/E4 失败样本固化为 prompt/schema 对照组；
4. 继续用 golden Stage 1 验证 Stage 2 回归；
5. 对 D/E 执行完整 3 次重复运行；
6. 将 `subject_ambiguous` 和 unmapped mention 送入 review queue；
7. API shadow 仅做异步 worker 试验，不同步阻塞用户请求；
8. 临床安全继续 report-only。

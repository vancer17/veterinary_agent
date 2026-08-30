<!--
=============================================================================
文件: input-preprocessing-v2-second-round-shadow-experiment-change-summary.md
作用: 记录输入前置预处理 v2 第二轮快速验证与 shadow 实验的实现、结果和准入结论。
范围: 覆盖 v2 实验契约、D/E/N/V/R/B/CS/AS 实验矩阵、同步质量门禁、远程真实模型
      shadow、异步旁路模拟和下一轮修复边界。
说明: 本文只沉淀工程结论，不改变生产问诊、临床安全召回、required_context 或 OPA 裁决。
维护: 当 v2 契约、实验矩阵、远程报告结论或迁移准入条件变化时同步更新。
=============================================================================
-->

# 输入前置预处理 V2 第二轮 Shadow 实验变更总结

> **文档状态**：第二轮实验实现与远程观察已完成；结果未达到生产消费准入
>
> **结论**：v2 契约和质量门禁方向有效，但 Stage 1 稳定性、Stage 2 verifier 遵循度和 canonical 受约束链接仍不合格。不能进入生产消费，也不能接入 `VetOrchestrator` 主链路。
>
> **文档职责**：本文是第二轮实验过程、样本、矩阵、模型表现、报告证据和复现方式的权威记录。长期架构方案只引用本文沉淀后的稳定结论和迁移准入条件。

## 1. 实验目标

第二轮实验验证 v2 架构是否修复第一轮暴露的缺陷：

1. Stage 1 能输出 `AtomicClaimSegment` / `SharedAssertionScopeSegment`；
2. `expected_evidence_count` 能逐项约束 Stage 2；
3. `EntityReference`、`SubjectBinding`、`ParticipantRole` 能区分用户、当前宠物、其他宠物和医疗提供者；
4. `not_found`、发明 canonical、发明实体、主体错配和漏项会被 blocking gate 阻断；
5. unmapped mention 进入 review，而不是被解释为用户未提供；
6. `answer_now` 仍可作为首个低风险渐进消费点；
7. 临床安全投影保持 report-only，不进入 evaluator / pgvector / required_context / OPA；
8. API shadow 具备有界队列和失败隔离，不影响主响应。

## 2. 实现范围

### 2.1 契约

新增：

1. `V2AtomicClaimSegment`
2. `V2SharedAssertionScopeSegment`
3. `V2EntityBinding`
4. `V2ParticipantBinding`
5. `V2VerifiedEvidence`
6. `V2CanonicalMappingStatus`
7. `V2QualityGateResult`
8. `V2InputAnalysisResult`

关键约束：

1. shared scope 的 `expected_evidence_count` 必须等于 item 数；
2. atomic claim 的 `initial_assertion` 必须被 Stage 2 验证；
3. only `confirmed` mapping 可以携带 `canonical_id`；
4. confirmed mapping 必须保留候选，且 canonical ID 必须出现在候选中；
5. not_found / unmapped / ambiguous / new concept request 必须进入 review；
6. Stage 2 的主体和参与者不能偏离 Stage 1 item 绑定；
7. 主体引用必须来自 TurnContext，ambiguous 必须携带至少两个可信候选；
8. 无法回指原文或 expected item 的结果不得通过 gate。

### 2.2 运行器

新增 `V2ArchitectureValidationRunner`，支持：

```text
ideal control
shadow
golden Stage 1
deterministic negative mutations
repeat stability
behavior branch
clinical safety report-only compare
async shadow isolation
```

实验输出包含：

1. Stage 1 / Stage 2 完整 trace；
2. 每阶段延迟和尝试次数；
3. expected / actual semantic signature；
4. gate 结果和失败原因；
5. Pydantic schema 错误摘要；
6. canonical、subject、participant、assertion 指标；
7. clinical safety report-only 边界；
8. async queue 溢出、worker 状态和失败隔离状态。

### 2.3 质量门禁

本轮新增或强化：

1. `v2_turn_context`
2. `v2_stage1_contract`
3. `v2_entity_subject_role`
4. `v2_stage2_contract`
5. `v2_expected_evidence_coverage`
6. `v2_canonical_registry`
7. `v2_assertion_consistency`
8. `v2_suspicious_empty`

这些 gate 只校验结构化契约、证据锚点、主体和事件角色，不扫描原文做医学判断，不使用关键词规则。

## 3. 实验矩阵

矩阵文件：

```text
tests/fixtures/input_preprocessing/second_round_shadow_matrix.json
```

该 fixture 是第二轮样本、golden Stage 1、expected evidence、negative mutation 和实验退出语义的工程权威。本文只描述样本设计口径，避免在文档中复制并维护第二份样本全文。

样本设计覆盖：

1. 低风险软便 / 换粮场景的原始表达与规范化表达；
2. `answer_now` 控制意图；
3. 多轮逐项补充与共享断言范围；
4. 用户动作、当前宠物、其他宠物、医疗提供者和多宠物歧义；
5. historical / hypothetical / uncertain 表达；
6. `not_found`、发明 canonical、发明实体、主体错配和 expected item 缺失负例；
7. 人工构造的 unmapped mention；
8. 异步 shadow 队列溢出与 worker 失败隔离。

覆盖：

| 实验 | 目标 |
|---|---|
| D2 baseline | 真实 Stage 1 能否稳定输出共享断言范围 |
| D2 golden Stage 1 | 给定正确 Stage 1 后，Stage 2 能否逐项验证 |
| E2 baseline | 真实 Stage 1 能否稳定绑定主体和事件角色 |
| E2 golden Stage 1 | 给定正确 Stage 1 后，Stage 2 能否保留参与者 |
| N2 negative contract | 发明 ID、角色错配、漏项和 assertion 替换是否阻断 |
| V2 unmapped review | 词表缺失表达是否进入 review |
| R2 repeat stability | 重复输出是否稳定 |
| B2 answer-now | `answer_now` 分支是否有效 |
| CS2 clinical compare | 临床安全是否仅做结构化对比 |
| AS2 async shadow | 队列和 worker 是否失败隔离 |

## 4. 本地 Ideal Control 结果

Ideal control 中 10 / 10 实验通过，包括 6 个确定性负例全部被预期阻断。

这说明：

1. v2 契约可以表达共享断言、事件参与者和 unresolved 状态；
2. golden Stage 1 / Stage 2 能通过结构化 gate；
3. 负例阻断逻辑可进入普通 CI；
4. `answer_now` 分支在理想输入下可复现；
5. 临床安全和异步 shadow 均保持 report-only / 不写业务状态。

Ideal control 不是生产结论，也不能作为 fallback。

## 5. 远程真实模型 Shadow 结果

### 5.1 报告索引

最终隔离版报告：

```text
.data/evaluations/input-preprocessing-v2-experiments-remote-isolated/input-preprocessing-v2-73e8b82b0563.json
```

报告时间：

```text
2026-08-25T11:12:28.854603+08:00
```

SHA-256：

```text
4e402ac5ebe0f80ee2e1c462a9d0b7966b3a374c58b15bee696bb0e2df88399d
```

模型：

```text
qwen-plus
```

执行方式：

1. 通过 SSH 隧道连接远程 LiteLLM；
2. 每个 case 使用独立 analyzer / Qwen client，避免前序实验熔断污染后续实验；
3. `repeat_override=1`；
4. clinical baseline 仅做结构化对比，不进入 evaluator 或 OPA。

### 5.2 总览

```text
experiment_count = 10
passed = 3
failed = 7
```

| 实验 | 结果 | Gate blocked | 中位耗时 | 结论 |
|---|---:|---:|---:|---|
| D2 baseline | failed | 1 | 73289 ms | Stage 1 未稳定输出 shared scope |
| D2 golden Stage 1 | failed | 1 | 91021 ms | Stage 2 mapping 审计不完整 |
| E2 baseline | failed | 1 | 86316 ms | Stage 1 主体 / 参与者漂移 |
| E2 golden Stage 1 | failed | 1 | 35613 ms | Stage 2 丢失参与者并错配 canonical |
| N2 negative contract | passed | 6 | 0 ms | 负例全部按预期阻断 |
| V2 unmapped review | failed | 1 | 28413 ms | 未映射表达被错误 confirmed |
| R2 repeat stability | failed | 2 | 40760 ms | Stage 1 schema / 输出不稳定 |
| B2 answer-now | passed | 0 | 5893 ms | `answer_now` 可识别并改变分支 |
| CS2 clinical compare | failed | 1 | 74207 ms | 新链路被 gate 阻断，不能对比消费 |
| AS2 async shadow | passed | 0 | 6224 ms | 队列溢出和 worker 失败显式隔离 |

### 5.3 D：共享断言范围

#### baseline

Stage 1 倾向把并列表达拆成 atomic claim，而没有稳定声明 shared assertion scope：

```text
没有呕吐、干呕、反流、流涎或舔唇
```

被拆散后，Stage 2 出现：

1. assertion 漂移，例如 denied 变成 `not_applicable`；
2. 多个 item 被合并成一个 evidence；
3. 黑便等 item 缺失；
4. confirmed mapping 缺少候选审计。

Gate 阻断：

```text
v2_canonical_registry
v2_assertion_consistency
```

结论：

> Stage 1 的 discriminated union 契约正确，但真实模型尚未稳定履行该契约。

#### golden Stage 1

给定正确 shared scope 后，Stage 2 能逐项输出主要 canonical，但 confirmed 结果没有保留候选列表：

```text
confirmed_candidates_missing
```

这证明 Stage 2 仍把 canonical mapping 当成自由选择，而不是受候选和审计约束的 verifier。

### 5.4 E：主体与事件角色

#### baseline

Stage 1 暴露：

1. action agent / recipient / experiencer 参与者缺失或不完整；
2. 相邻句子被合并；
3. 断言被 schema 外语义污染；
4. `answer_now` 等 intent 与事实表达混杂。

Stage 2 进一步放大漂移，例如把干预动作映射到普通状态 canonical。

#### golden Stage 1

即使 Stage 1 正确提供：

```text
action_agent
action_recipient
action_object
experiencer
```

Stage 2 仍输出空 participants，并把：

```text
医生给它开了药
主人昨天喂了罐头
```

映射到不合适的普通问诊 canonical。

Gate 正确阻断：

```text
stage2_participants_not_verified
canonical_subject_type_mismatch
confirmed_candidates_missing
```

结论：

> EventFrame / ParticipantRole 不是过度设计；没有该契约，用户动作、宠物行为和其他宠物症状会继续混淆。

### 5.5 V：unmapped mention

输入包含明显未映射表达：

```text
ZZZ表现
```

真实链路将其 confirmed 到已有 canonical，且缺少候选审计。

Gate 阻断：

```text
confirmed_candidates_missing
```

结论：

> 当前 constrained linker / verifier 不满足“词表缺失进入 review”的要求。不能把词表缺口伪装成已确认事实，也不能解释为用户未提供。

### 5.6 N：负例与 Fail-Fast

以下 6 类负例全部按预期阻断：

1. `not_found` 被当作 canonical ID；
2. 发明词表外 canonical；
3. Stage 2 参与者角色错配；
4. ambiguous subject 缺少候选；
5. expected item 缺失；
6. Stage 2 替换 Stage 1 assertion。

结论：

> 质量门禁是有效架构边界，而不是报告附属品。

### 5.7 B：answer-now

`answer_now` 实验通过：

```text
baseline_empty → ask
answer_now_only → answer
```

该能力仍是最合适的第一个渐进消费点，但本轮只证明 report-only 分支有效，尚未接入生产编排。

### 5.8 CS：临床安全对比

clinical safety 严格遵守：

```text
downstream_evaluation = not_implemented
evaluator_called = false
opa_called = false
```

由于新链路 Stage 1 / gate 被 blocking，不能得到可消费的当前宠物投影。既有 clinical baseline 可观察到 denied 覆盖，但新链路 denied 投影为 0，因此不能进入 evaluator。

结论：

> report-only 边界有效；临床安全继续禁止接入。

### 5.9 AS：异步 shadow

实验验证：

1. 第一个 snapshot 被接受；
2. 第二个 snapshot 因有界队列满被显式拒绝；
3. enqueue latency 为 0 ms；
4. worker 独立执行；
5. worker 失败不写业务状态；
6. 不触发临床安全 evaluator。

结论：

> 异步旁路方向可行，但当前仍是 in-memory 实验队列，不是生产 worker。

## 6. 架构判断

### 6.1 已证明

1. v2 discriminated segment contract 能表达并列断言；
2. expected item coverage gate 能发现漏项；
3. Entity / Subject / Role gate 能发现主体和角色漂移；
4. Canonical Registry gate 能阻断发明 ID、缺少候选和 unresolved 带 ID；
5. assertion consistency gate 能阻断 Stage 2 改写断言；
6. `answer_now` 是低风险渐进消费点；
7. clinical safety report-only 边界可执行；
8. 异步 shadow 的队列与失败隔离语义可验证。

### 6.2 未证明

1. 真实 Stage 1 D baseline 3/3 exact match；
2. 真实 Stage 1 E baseline 3/3 exact match；
3. Stage 2 能稳定继承 participants 和 initial assertion；
4. Stage 2 能稳定输出候选审计；
5. unmapped mention 会稳定进入 review；
6. 重复输出稳定；
7. API shadow 可接入 `VetOrchestrator`；
8. clinical safety adapter 可评估接入。

## 7. 准入结论

当前结果不允许：

1. 生产消费问诊事实；
2. 生产消费 clinical safety projection；
3. 接入 clinical safety evaluator；
4. 将 in-memory async queue 用于生产；
5. 将 golden Stage 1 作为 fallback；
6. 修改回答充分性策略掩盖上游失败。

允许继续：

1. report-only shadow；
2. deterministic gate 回归；
3. Stage 1 / Stage 2 契约迭代；
4. `answer_now` 的独立灰度设计；
5. 异步 API shadow worker 的正式设计。

## 8. 下一轮修复优先级

1. **Stage 1 schema 遵循率**
   - 修复 `profile_expected_fact_count_mismatch`；
   - 保持 profile 计数、segment kind 和 expected item 数一致；
   - 对 schema 输出漂移做同契约有限重试。

2. **Shared assertion scope**
   - 确保并列 denied / normal / abnormal 逐项输出；
   - 不允许只保留第一个 item 或整段合并。

3. **Stage 2 verifier 约束**
   - 必须继承 Stage 1 participants；
   - 必须验证 atomic `initial_assertion`；
   - confirmed mapping 必须携带候选列表；
   - 不允许把 intervention / feeding / medication 映射到普通状态概念。

4. **Constrained canonical linking**
   - confirmed 必须来自候选；
   - 未命中必须输出 unmapped / not_found；
   - unmapped 必须 review_required；
   - 禁止最近邻或模型自由发明事实。

5. **重复稳定性**
   - D / E / V 每样本至少 3 次；
   - 输出 signature 完全一致后才评估 Stage 1 准入。

6. **异步 API shadow**
   - Stage 1 / Stage 2 达标后再接 `VetOrchestrator`；
   - 使用持久化有界队列 / worker；
   - 主请求只采样和入队，不等待模型。

## 9. 复现命令

本地 ideal control：

```bash
uv run python -m vet_agent.input_preprocessing.v2_experiments \
  --mode ideal
```

远程真实模型 shadow：

```bash
INPUT_PREPROCESSING_LITELLM_API_KEY=<key> \
INPUT_PREPROCESSING_WITH_CLINICAL_BASELINE=true \
scripts/integration/run-input-preprocessing-v2-experiment-smoke.sh \
  --repeat-override 1 \
  --output-dir .data/evaluations/input-preprocessing-v2-experiments-remote-isolated
```

真实模型实验依赖远程 LiteLLM，不得在依赖不可用时退回关键词、宽松 JSON 或本地规则。

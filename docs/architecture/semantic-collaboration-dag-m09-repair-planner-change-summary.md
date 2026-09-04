<!--
=============================================================================
文件: semantic-collaboration-dag-m09-repair-planner-change-summary.md
作用: 总结受限语义协作 DAG M09 Repair Planner 的生产实现边界与后续对接契约。
范围: 覆盖 M08 Review Bundle 消费边界、通用修复 lane、clarification / human review
      路由、修复预算、inventory 修复优先级、stale 记录、计划验证与 M10 / M11 / M04
      后续职责。
说明: 本文只记录稳定生产契约、实现状态、有意 TODO 和验收结论；不展开软件包内部
      类图、函数实现、M10 prompt、typed patch 细节、数据库 DDL 或 Temporal workflow
      内部实现。
维护: 当修复 lane、不可修复边界、预算策略、stale 语义、artifact 绑定或 M10 / M11
      对接契约调整时，必须同步更新本文和生产架构基线。
=============================================================================
-->

# 受限语义协作 DAG M09 Repair Planner 变更总结

> **文档状态**：M09 生产工程边界已实现；待 M11 Artifact Store 与 M04 任务执行器组合联调

## 1. 当前状态

| 项目 | 状态 |
|---|---|
| M08 Review Bundle 消费边界 | 已实现 |
| Coverage 通用修复路由 | 已实现 |
| Faithfulness 通用修复路由 | 已实现 |
| clarification gap 路由 | 已实现 |
| human review 路由 | 已实现 |
| disagreement 保留 | 已实现 |
| repair budget | 已实现 |
| inventory repair 优先级 | 已实现 |
| stale / suppressed 记录 | 已实现 |
| deterministic plan verifier | 已实现 |
| M10 Repair SKILL / typed patch | 已在后续 M10 阶段实现 |
| M11 base artifact snapshot | TODO 显式空壳 |
| M11 Artifact Store / stale marker | 未实现 |
| M04 SemanticTaskExecutor 组合 | 未接入 |
| 真实 LiteLLM / Temporal 联调 | 未执行 |

## 2. 生产结论

M09 是确定性 Repair Planner，不是修复执行器。

它的职责是把 M08 的结构化审查结果转换为受限修复计划：

```text
M08 Review Bundle
→ deterministic repair planning
→ accepted RepairPlan
→ M10 Repair SKILL / typed patch proposal
```

M09 不按每个 Review 维度实现专门 Python 修复分支，也不创建细粒度医学或语义修复状态机。
生产只保留两个通用修复 lane：

```text
claim_inventory_repair
claim_proposition_repair
```

该设计的目的是：

1. Coverage 是 turn 级问题，需要以完整 Claim Inventory 为修复目标。
2. Faithfulness 是 claim 级问题，一次只应指向一条 proposition。
3. 已知问题的具体修复措辞由 M10 在受限上下文中生成 typed patch proposal。
4. M09 只负责路由、预算、目标身份、gap 保留和 stale 治理。
5. 避免在 Python 中按症状、疾病或单个措辞维度提前实现医学修复规则。

## 3. 职责边界

### 3.1 M09 负责

```text
消费 M08 coverage true dimensions
消费 per-claim faithfulness true dimensions
消费 clarification gaps
消费 claim index / claim digest / proposition 身份
创建通用修复任务
控制修复深度
控制单目标维度预算
控制任务数量预算
透传 clarification gap
路由 human review / disagreement / review failed
执行 inventory repair 优先级
记录 stale review 与 suppressed proposition repair
确定性验证 RepairPlan 与 M08 结果一致
```

### 3.2 M09 不负责

```text
不调用 LLM
不读取原始用户文本做语义或医学判断
不生成 corrected claims
不生成 corrected proposition
不生成 patch operations
不应用 patch
不提交 artifact
不分配 artifact version
不修改 claims
不做 evidence binding
不做诊断、风险或治疗判断
不调用问诊状态
不调用临床安全 evaluator / required_context / OPA
不写长期记忆
```

## 4. 对外契约

### 4.1 输入

M09 只消费已通过 M08 确定性验证和结果派生的 Review Bundle：

```text
coverage matrix
coverage derived dimensions
per-claim faithfulness matrix
per-claim derived dimensions
claim index / claim digest / proposition
clarification gaps
bounded missing_claim_candidates
source proposal digest
turn snapshot digest
review bundle digest
```

M09 不得消费：

```text
模型原始自由文本
review verdict / reason / confidence
corrected proposition
生成器 prompt 或调用 metadata
下游领域状态
未验证同伴任务输出
```

### 4.2 输出

M09 输出不可变 RepairPlan，至少表达：

```text
plan identity
business route
repair tasks
active clarification gaps
stale clarification gaps
human review reasons
stale review references
suppressed proposition repair targets
repair policy
source proposal digest
review bundle digest
turn snapshot digest
```

每个 repair task 至少表达：

```text
repair task identity
repair lane
target Repair SKILL identity
source proposal digest
review bundle digest
turn snapshot digest
review dimensions
claim target identity          # proposition repair 必填
repair hints                  # 仅 inventory repair 可携带
dependency review identities
```

RepairPlan 不包含：

```text
corrected proposition
patch operation
free-form reason
confidence
medical decision
```

## 5. 路由规则

### 5.1 Coverage 已知问题

除 `未分类覆盖问题` 外，Coverage 已知维度统一进入：

```text
claim_inventory_repair
```

包括：

```text
存在漏抽显式事实
存在多事实合并
存在重复claim
存在原文不支持的claim
存在非自包含proposition
存在shared scope拆分错误
```

一次 Coverage Review 只创建一个 inventory repair task，不按维度拆分多个任务。

`missing_claim_candidates` 只能作为 repair hint 传给 M10，不能被 M09 追加为权威
claim，也不能被标记为已验证 proposition。

### 5.2 Faithfulness 已知漂移 / 越权

已知模型漂移、模型越权和可由授权上下文补全的自包含性问题统一进入：

```text
claim_proposition_repair
```

包括：

```text
主体或指代范围改变
否定方向改变
否定范围改变
正常状态误写为否认
事实类型改变
时间范围改变
频率或数量改变
程度或强度改变
确定性改变
因果关系改变
医学推断或建议添加
命题不自包含
```

每条 claim 最多创建一个 proposition repair task，任务可携带该 claim 的全部 true
repair dimensions。

`医学推断或建议添加` 只能触发删除或还原式修复。M10 不得评估医学内容是否正确，也不得
生成新的诊断、风险、就医或治疗建议。

### 5.3 来源绑定缺失

以下维度不进入 Repair：

```text
指代对象不明
时间基准不明
否定范围不明
比较基线不明
```

它们只透传为 clarification gap。

若同一条 claim 同时存在可修复漂移和来源绑定缺失，M09 输出：

```text
repair_then_clarification_required
```

后续 M10 只能修复已知漂移，必须保留 gap；不得猜测指代对象、时间基准、否定范围或
比较基线。

### 5.4 不可自动修复

以下问题保持 human review：

```text
未分类覆盖问题
未分类语义改变
Coverage / Faithfulness disagreement
修复预算超限
```

Review failed 不生成修复任务。

Disagreement 不得默认 Coverage 正确，也不得默认 Faithfulness 正确；除非未来显式新增
adjudicator 契约，否则不能进入自动修复。

## 6. inventory repair 优先级

当 Coverage inventory repair 存在时：

```text
只创建 claim_inventory_repair task
抑制全部 claim_proposition_repair candidates
既有 Faithfulness review references 进入 stale
被抑制的 proposition repair targets 被显式记录
相关 clarification gaps 进入 stale
```

原因：

```text
inventory patch 可能新增、删除、拆分或替换 claim
旧 claim index / digest 可能失效
旧 Faithfulness 结果不能迁移到新 claim
对即将失效的 claim 做 proposition repair 是无效成本
旧 clarification gap 可能绑定到即将删除或重写的 claim
```

正确后续流程：

```text
M10 typed patch
→ M07 structural verifier
→ M08 re-review
→ 重新生成 gaps / repair plan
```

修复后的结果不能直接视为 verified，也不能把旧 claim 的审查结论迁移到新 claim。

## 7. 预算与确定性

当前生产预算：

```text
repair_depth = 1
max_repair_dimensions_per_target = 2
max_claim_proposition_repair_tasks = 2
max_total_repair_tasks = 2
```

硬性边界：

```text
不允许 repair of repair
同一 proposition 最多一次修复
预算超限进入 human_review_required
不得为绕过预算合并多个 claim
不得自由全局重写
```

Plan 与 task identity 由以下信息确定性派生：

```text
run id
source task id
source proposal digest
review bundle digest
turn snapshot digest
repair lane
claim index / claim digest
canonical review dimensions
repair policy
```

同一 M08 Review Bundle 在同一策略下多次规划必须得到相同 plan identity 与 task identity。

M09 的 deterministic plan verifier 通过同策略复算验证：

```text
身份一致
review bundle 可规划
lane 与目标负载匹配
dimension routing 合法
gap routing 合法
预算未超限
plan payload 与复算结果一致
```

只有 accepted RepairPlan 才能进入 M10。

## 8. M10 对接边界

M10 只能消费 accepted M09 RepairPlan。

M10 负责：

```text
读取修复任务的受限上下文
生成 typed patch proposal
声明 base artifact version
限定 patch path 与 operation 类型
通过 deterministic patch verifier
通过 deterministic patch applier
保留 repair lineage
```

M10 不得：

```text
直接消费 M08 matrix 自行扩大修复范围
直接修改 M09 RepairPlan
输出自由 JSON Patch
整轮自由重写
修复未申报维度
猜测来源绑定缺失
评估医学推断是否正确
把 patch proposal 标记为 verified artifact
```

## 9. M11 对接与有意 TODO

### 9.1 base artifact snapshot

M10 生成可提交 patch 前，必须获得 M11 提供的权威 base snapshot：

```text
claims
artifact_reference
base_version
repair_depth
```

当前 M11 未实现，M10 保留显式 TODO 空壳：

```text
RepairTargetSnapshotResolver
TODORepairTargetSnapshotResolver
```

TODO resolver 始终 Fail Fast，不伪造 claims、artifact reference 或 base version。

### 9.2 Artifact Store 与 stale marker

M11 负责：

```text
append-only artifact
artifact version
repair lineage
幂等提交
review bundle artifact 化
clarification gap artifact 化
stale marker
downstream stale propagation
```

M09 只输出计划层 stale 信息，不直接写数据库，也不成为 stale 状态权威。

## 10. M04 对接 TODO

M04 的生产任务执行器尚未组合 M09。

后续接入时必须保持：

```text
M06 generation
→ M07 structural verifier
→ M08 review bundle
→ M09 repair plan
→ M10 typed patch
→ M11 artifact commit
```

M04 不解释 Review 维度，不生成修复任务，不应用 patch，不判断 artifact 是否可信。

在 M04 组合完成前，不能宣称端到端 verified claim graph。

## 11. 安全与领域隔离

M09 保持：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
long_term_memory_written = false
```

禁止：

```text
按医学症状或疾病词建立修复分支
硬关键词 / 正则医学判断
来源绑定缺失进入 repair
未分类问题进入 repair
disagreement 默认任一方正确
repair of repair
无界 retry / repair / fan-out
失败转空计划
旧问诊语义抽取器 fallback
```

## 12. 有意 TODO 清单

| TODO | 当前行为 | 后续责任 | 启用条件 |
|---|---|---|---|
| M10 Repair SKILL | 不生成修复内容 | M10 | 修复 prompt、输出 schema 和 patch 契约明确 |
| M10 typed patch verifier / applier | 不应用 patch | M10 | patch path、operation、base version 和冲突校验明确 |
| M11 base artifact snapshot | TODO resolver Fail Fast | M11 | append-only artifact 与 base version 契约就绪 |
| M11 stale marker | M09 仅输出计划层 stale 信息 | M11 | artifact version / lineage / stale 状态机就绪 |
| M04 task executor 组合 | M09 未进入生产任务端口 | M04 | M06 / M07 / M08 / M09 / M10 / M11 消费时序闭合 |
| 修复后 re-review 闭环 | 契约已定义，执行链未接入 | M04 / M10 / M11 | typed patch 后可生成新 proposal 并重新进入 M07 / M08 |
| 真实 LiteLLM 冒烟 | 未执行 | 集成验证 | 显式外部模型网关环境与模型 snapshot |
| 真实 Temporal workflow 联调 | 未执行 | M04 / M15 | Temporal server、worker、task queue 与投影就绪 |

这些 TODO 是职责边界，不是允许绕过的错误。

## 13. 验收与验证结果

### 13.1 验收覆盖

```text
supported review 不生成修复任务
Coverage 已知问题统一进入 inventory repair
Faithfulness 已知漂移统一进入 proposition repair
来源绑定缺失只输出 clarification gap
repair then clarification 保留 gap
未分类覆盖问题进入 human review
未分类语义改变进入 human review
disagreement 不自动选择任一方
inventory repair 优先并生成 stale / suppressed 记录
预算超限进入 human review
RepairPlan 可确定性复算
计划身份漂移与 payload 漂移 blocked
M11 TODO 不伪造 base artifact snapshot
```

### 13.2 当前验证结果

```text
ruff check semantic_collaboration + M09 tests: PASS
mypy src/vet_agent/semantic_collaboration: PASS
pytest tests/test_semantic_collaboration_*.py: 92 passed
pytest 全量默认测试: 310 passed, 43 skipped
```

以上结果使用进程内测试替身，不代表真实 LiteLLM、Temporal、M10 或 M11 集成完成。

## 14. 文档同步要求

以下内容变化时必须同步更新本文：

```text
M08 Review 维度或 derived outcome
M09 通用修复 lane
不可自动修复边界
修复预算
inventory repair 优先级
stale / suppressed 语义
clarification gap 路由
M10 patch 契约
M11 artifact snapshot 契约
M04 任务执行组合边界
```

代码、测试或后续模块契约与本文冲突时，必须先修订生产架构基线，再同步实现；不得留下
双权威解释。

## 15. 关联材料

1. [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
2. [semantic-collaboration-dag-production-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-implementation-plan.md)
3. [semantic-collaboration-dag-m08-review-skill-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m08-review-skill-change-summary.md)
4. [semantic-collaboration-dag-m04-scheduler-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m04-scheduler-change-summary.md)
5. [semantic-collaboration-dag-m10-repair-skill-patch-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m10-repair-skill-patch-change-summary.md)

<!--
=============================================================================
文件: docs/architecture/semantic-collaboration-dag-pre-cde-closure-change-summary.md
作用: 总结问诊动态证据重构前语义预处理主链的闭合边界、实现状态与有意后置项。
范围: 覆盖 M02 当前回合快照存档、首轮来源边界、M04 clarification_required、
      M06～M13 应用服务、M10 后 re-review、服务结果信封、迁移与测试验收。
说明: 本文只表示语义业务主链闭合，不宣称 M14 可观测性、M15 VetOrchestrator
      接入、真实外部服务联调或多轮记忆读模型已完成。
维护: 当预处理服务输入输出契约、TurnSnapshot 存档表、修复 re-review 边界、
      首轮 / 多轮来源策略或 M15 接入前提调整时，应同步更新本文。
=============================================================================
-->

# 语义协作 DAG Pre-CDE 主链闭合总结

> **文档状态**：语义预处理业务与契约主链已实现；M14 显式后置，M15 仍等待
> 问诊动态证据链路完成。

## 1. 变更目标

在进入问诊动态证据重构前，提供稳定的上游服务边界：

```text
当前回合输入
→ TurnSnapshot
→ Plan IR
→ M06 Turn Intent / Claim Inventory
→ M07 structural verifier
→ M08 Review
→ M09 / M10 Repair
→ M11 Artifact
→ M08 re-review
→ M12 Claim Graph
→ M13 ConsultationProjection
→ SemanticConsultationPreprocessingResult
```

该服务只输出 M13 问诊投影与身份信封，不实现：

```text
问诊动态证据策略
Followup / Answer 决策
问诊状态写入
临床安全判断
长期记忆写入
VetOrchestrator 路由
```

## 2. 当前实现状态

| 能力 | 状态 | 说明 |
|---|---|---|
| TurnSnapshot 构建 | 已实现 | 继续使用 M02 strict digest 与预算门禁 |
| TurnSnapshot append-only 存档 | 已实现 | 新增 `semantic_turn_snapshots` 与 0024 迁移 |
| Persistent TurnSnapshot Reader | 已实现 | 按 digest 读取并复验完整快照 |
| 首轮历史来源 | 已实现 | `turn_index=0` 返回 `no_previous_turn` |
| 多轮历史来源 | TODO | 显式 Fail Fast，等待结构化追问读模型 |
| 首轮已验证历史事实 | 已实现 | `turn_index=0` 返回 `no_verified_claims` |
| 多轮已验证历史事实 | TODO | 显式 Fail Fast，等待 proposition-preserving 记忆读模型 |
| 可信宠物基础画像 | 已实现 | 只从 scope 仓储投影白名单字段 |
| M04 `clarification_required` | 已实现 | 合法 artifact 终态，可携带 partial artifact |
| M06 / M07 / M08 主链组合 | 已实现 | 由预处理服务顺序组合 |
| M09 / M10 / M11 repair | 已实现 | patch 提交后必须 re-review |
| M08 repair 后 re-review | 已实现 | 复用原始 proposal 身份，不伪造新 M06 输出 |
| M12 / M13 graph 投影 | 已实现 | verified 与 partial graph 均可输出问诊投影 |
| 服务结果信封 | 已实现 | 区分 verified / partial / blocked / failed |
| M14 trace / metrics | 后置 | 当前决策为暂不实现 |
| M15 VetOrchestrator 接入 | 未实现 | 等待 CDE-01～CDE-15 |

## 3. M02 边界

来源 Adapter 位于 `vet_agent.semantic_preprocessing` 组合包；`semantic_collaboration`
核心包仍只暴露 M02 Protocol，不反向依赖 scope 仓储或记忆领域。

### 3.1 当前回合

当前回合 `OriginalUserText` 仍由入口确定性构造，并在 TurnSnapshot 中原样保留。
快照保存到 PostgreSQL 后不可更新：

```text
同一 context digest + 同一 scope + 同一 JSON → 幂等成功
同一 context digest + 不同 scope / JSON → conflict
同一 turn_id + 不同 context digest → conflict
同一 scope + 同一 turn_index + 不同 context digest → conflict
```

读取时重新执行 strict Pydantic 校验与 digest 校验，不使用摘要或重建文本替代完整快照。

### 3.2 首轮来源

```text
FirstTurnOnlyBoundedHistoryReader
FirstTurnOnlyVerifiedPriorFactReader
```

首轮分别返回：

```text
no_previous_turn
no_verified_claims
```

非首轮直接抛出 `TurnSnapshotSourceUnavailableError`，不得把来源不可用解释为：

```text
AVAILABLE + questions=[]
NO_VERIFIED_CLAIMS
```

### 3.3 可信宠物画像

`ScopeTrustedPetContextReader` 只读取服务端 scope 仓储中激活且范围匹配的画像，并只投影：

```text
species
breed
age_text
weight_kg
sex
neutered
```

它不读取请求侧自报资料，也不把 `TrustedPetFactBundle` 的问诊事实语义混入 M02。

## 4. 服务契约

新增：

```text
SemanticConsultationPreprocessingRequest
SemanticConsultationPreprocessingResult
SemanticPreprocessingExecutionState
SemanticPreprocessingFailureCode
SemanticConsultationPreprocessingService
build_semantic_consultation_preprocessing_service
```

可消费状态：

```text
completed_verified
completed_partial_with_gaps
```

不可消费状态：

```text
blocked
failed
```

成功结果必须携带：

```text
turn_id
turn_snapshot_digest
run_id
plan_id
artifact_reference / version
graph_id / digest / terminal state
projection_id
ConsultationProjection
```

失败结果不得携带 `ConsultationProjection` 或 `projection_id`，不得被下游解释为空 facts。

## 5. 修复与 re-review

M08 输出 repair 路由时：

```text
M08 initial review
→ M11 v1
→ M09 repair plan
→ M10 patch set + preview
→ M11 v2 patch_applied
→ M08 review_claims(repaired claims)
→ M11 v3 repair_verified / clarification_required
→ M12 / M13
```

新增 `SemanticClaimReviewSource` 与 `StructuredReviewSkillRunner.review_claims`：

```text
execution = 原始 Claim Inventory PlanTask
source_proposal_digest = 原始 M06 proposal digest
claims = M10 deterministic preview claims
```

该入口不把修复后 claims 伪造成新的 M06 模型输出。修复深度仍固定为 1；re-review 后仍需 repair 时显式 `repair_exhausted`。

## 6. M04 终态修正

`DAGTaskTerminalState` 新增：

```text
clarification_required
```

该终态：

```text
必须携带 artifact reference
不是 infrastructure failure
不是 verified
不是通用 dependency success
可通过 M12 策略输出 graph_partial_with_gaps
```

对应 PostgreSQL 任务投影 check constraint 已同步扩展。

## 7. 持久化

新增迁移：

```text
alembic/versions/0024_semantic_turn_snapshots.py
```

表：

```text
semantic_turn_snapshots
```

该表只保存 TurnSnapshot canonical JSON，不保存：

```text
prompt
模型原始输出
问诊状态
临床安全结果
长期记忆
```

## 8. 验收

新增进程内端到端测试覆盖：

```text
快照幂等保存与 digest / turn_id 冲突
多轮历史与历史事实来源 Fail Fast
可信宠物画像白名单与范围失败
clarification_required artifact 终态
supported claims → graph_verified → ConsultationProjection
clarification gap → graph_partial_with_gaps
repair → patch → re-review → repair_verified
合法空图 → no_explicit_fact
M07 blocked → 无 M13 空投影
```

当前语义协作单元 / 组件测试：

```text
131 passed
```

## 9. 有意未完成

```text
1. M14 TraceRecorder / MetricCollector / RunReport
2. M15 VetOrchestrator 显式路由与回滚配置
3. Temporal 生产 SemanticTaskExecutor 接入
4. 真实 PostgreSQL migration 部署验证
5. 真实 LiteLLM + Temporal + PostgreSQL 端到端联调
6. 多轮结构化 assistant question 读模型
7. proposition-preserving verified prior fact 读模型
8. 临床安全 / 长期记忆 M13 TODO adapter 替换
```

这些未完成项不改变当前主链的业务闭合结论，但阻止宣称完整生产可观测或 M15 已接入。

## 10. 关联材料

1. [semantic-collaboration-dag-production-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-implementation-plan.md)
2. [consultation-dynamic-evidence-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-dynamic-evidence-architecture.md)
3. [consultation-dynamic-evidence-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-dynamic-evidence-implementation-plan.md)
4. [semantic-collaboration-dag-m11-auto-finalization-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m11-auto-finalization-change-summary.md)
5. [semantic-collaboration-dag-m12-claim-graph-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m12-claim-graph-change-summary.md)
6. [semantic-collaboration-dag-m13-domain-projection-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m13-domain-projection-change-summary.md)

<!--
=============================================================================
文件: semantic-collaboration-dag-m11-artifact-store-change-summary.md
作用: 总结受限语义协作 DAG M11 回合级 Artifact Store 与版本生产实现。
范围: 覆盖轻量权威粒度、SQLAlchemy 表结构、append-only 版本链、gate event、
      幂等提交、M08/M10 端口接入、派生索引、stale 治理与系统性人工审查。
说明: 本文记录生产契约与验收状态，不宣称 M04 执行器、M12 Claim Graph 或
      领域投影已经完成。
维护: 当 artifact 粒度、gate 状态、索引、幂等键或 stale 语义调整时同步更新。
=============================================================================
-->

# 受限语义协作 DAG M11 Artifact Store 变更总结

> **文档状态**：M11 生产工程边界已实现；待 M04 任务执行器组合、真实 PostgreSQL
> 迁移部署与 M12 Claim Graph 消费联调

## 1. 当前状态

| 项目 | 状态 |
|---|---|
| 回合级 `SemanticTurnArtifactPayload` | 已实现 |
| Review Bundle 内嵌 section | 已实现 |
| Claim records / digests | 已实现 |
| Repair lineage 内嵌 | 已实现 |
| append-only version 表 | 已实现 |
| gate event 表 | 已实现 |
| 幂等 commit request 表 | 已实现 |
| stale marker 表 | 已实现 |
| claim review 派生索引 | 已实现 |
| clarification gap 派生索引 | 已实现 |
| M08 ReviewArtifactStore 端口 | 已实现 |
| M10 snapshot / patch store 端口 | 已实现 |
| 系统性人工审查 gate event | 已实现 |
| Alembic 0023 迁移 | 已实现 |
| M04 生产执行器组合 | 未接入 |
| M12 Claim Graph | 未实现 |
| 真实 PostgreSQL M11 迁移部署 | 未执行 |

## 2. 轻量化权威粒度

M11 不为以下对象建立独立权威 artifact：

```text
单条 claim
单条 Faithfulness Review
单个 clarification gap
单个 repair task
```

权威对象是：

```text
semantic_turn_result artifact version
```

一个 version payload 内嵌：

```text
claims
review_bundle
repair_lineage
repair_depth
source proposal identity
TurnSnapshot digest
```

这保留了逐条可审计性，同时避免碎片化版本链和不可扩展的人工逐条审核。

## 3. 数据库表

新增迁移：

```text
alembic/versions/0023_semantic_artifact_store.py
```

表：

```text
semantic_artifact_versions
semantic_artifact_gate_events
semantic_artifact_commit_requests
semantic_artifact_stale_markers
semantic_claim_review_index
semantic_clarification_gap_index
```

全部字段均有 SQLAlchemy description / 数据库 comment。
业务层只能通过 `PostgresSemanticArtifactRepository` 访问数据库模型。

## 4. 版本链

同一回合适用稳定：

```text
artifact_id = sha256(run_id + source_task_id + turn_snapshot_digest)
```

典型链路：

```text
v1: M08 review artifact
v2: M10 patch 后 claims，repair_depth=1
v3: M08 re-review 后 review section
```

规则：

```text
版本只追加
payload 不更新
repair_depth 只允许 0/1
v2 不能再作为 M10 base snapshot
v1 保留用于审计
```

## 5. Gate event

Gate 不存储为 artifact payload 字段，而由：

```text
semantic_artifact_gate_events
```

以 append-only event 派生。

M08 supported 初始事件：

```text
semantic_review_supported
→ evidence_binding_pending
```

不会直接：

```text
verified
```

M10 patch 后：

```text
patch_applied
```

系统性人工审查记录为 gate event，但 gate 保持：

```text
evidence_binding_pending
```

人工审查不逐条修改 claim，也不批量标记 verified。

## 6. M08 / M10 端口

`PostgresSemanticArtifactStore` 同时实现：

```text
ReviewArtifactStore
RepairTargetSnapshotResolver
RepairPatchStore
```

### M08 提交

输入：

```text
SemanticReviewBundle
```

输出：

```text
semantic-artifact://<artifact_id>/<version>
```

幂等键：

```text
review:<review_bundle_digest>
```

### M10 snapshot

按以下身份读取：

```text
source_proposal_digest
review_bundle_digest
```

输出：

```text
claims
artifact_reference
base_version
repair_depth
```

### M10 patch commit

M11 不盲信 preview，提交时重新：

```text
读取 base claims
校验 base version
校验 repair_depth=0
重新应用 patch set
比较 preview
```

然后插入：

```text
v+1 artifact
patch lineage
patch_applied gate event
base stale marker
repair_pending claim index
```

幂等键：

```text
repair:<repair_plan_id>
```

## 7. 派生索引

### claim review index

用于查询与统计：

```text
artifact/version
claim index/digest/proposition
review status
repair dimensions
```

不是权威，可由 artifact payload 重建。

### clarification gap index

用于查询：

```text
gap id
claim digest
ambiguous dimension
binding type
status
```

gap 不是自动追问，也不是 verified fact。

## 8. stale 治理

当 review 或 repair 产生新版本时，旧版本写入：

```text
semantic_artifact_stale_markers
```

reason：

```text
superseded_by_review
superseded_by_repair
```

旧 payload 不修改。旧 claim 的 review 结论不迁移到新 claims。

## 9. 系统性人工审查

新增：

```text
SemanticSystemReviewRecord
```

记录：

```text
review id
reviewer role
system defect status
review dimension
summary digest
review time
```

该记录写入 gate event，但不改变 verified gate，也不修改 artifact payload。

人工审查关注系统缺陷，而非逐条用户事实。

## 10. 安全边界

M11 保持：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
long_term_memory_written = false
```

禁止：

```text
UPDATE / DELETE 权威 artifact payload
把 semantic_review_supported 写成 verified
人工批量 verified
旧 claim review 迁移到新 claim
自动 resolve clarification gap
生成用户追问
医学判断
扫描原文关键词
```

## 11. 验证结果

本地验证：

```text
ruff check semantic_collaboration + M11 tests: PASS
mypy src/vet_agent/semantic_collaboration: PASS
pytest tests/test_semantic_collaboration_*.py: 105 passed
pytest 全量默认测试: 323 passed, 84 skipped
```

新增测试覆盖：

```text
M08 review 内嵌回合 artifact
review 幂等重放
supported → evidence_binding_pending
clarification gap 派生索引
M10 base snapshot
M10 patch 后 v2
repair_depth=1
base stale marker
M08 re-review 后 v3
repair-of-repair blocked
系统性人工审查不提升 verified
```

## 12. 关联材料

1. [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
2. [semantic-collaboration-dag-production-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-implementation-plan.md)
3. [semantic-collaboration-dag-m10-repair-skill-patch-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m10-repair-skill-patch-change-summary.md)
4. [semantic-collaboration-dag-pre-m11-integration-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-pre-m11-integration-change-summary.md)

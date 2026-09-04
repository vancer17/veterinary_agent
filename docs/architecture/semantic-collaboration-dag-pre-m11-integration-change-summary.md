<!--
=============================================================================
文件: semantic-collaboration-dag-pre-m11-integration-change-summary.md
作用: 记录受限语义协作 DAG M10 后、M11 前 Pre-M11 集成测试的实现、真实外部
      服务验证结果、暴露的工程缺陷修复和当前剩余边界。
范围: 覆盖集成测试 harness、fixture、脚本、真实 LiteLLM / Temporal / PostgreSQL
      验证、语义校准矩阵、M04 scheduler-only 执行器、报告契约与生产修复。
说明: 本总结只证明 M02～M10 的 Pre-M11 组合能力；不宣称 M11 权威 artifact、
      verified claim graph 或领域投影完成。
维护: 当集成矩阵、外部依赖行为、Temporal converter 契约或 M11 实现状态变化时
      同步更新本文。
=============================================================================
-->

# 受限语义协作 DAG Pre-M11 集成测试变更总结

> **文档状态**：Pre-M11 首个可执行集成测试切片已实现并通过真实远端开发环境验证
>
> **验证边界**：M02～M10、真实 LiteLLM、真实 Temporal、真实 PostgreSQL projection；
> 不包含 M11 append-only commit、M12 Claim Graph、M13 领域投影和 M15 生产接入。

## 1. 结论

本轮按
[semantic-collaboration-dag-pre-m11-integration-test-design.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-pre-m11-integration-test-design.md)
完成当前可执行的 Pre-M11 集成测试切片，并在远端开发环境完成 semantic 模式验证。

最新结果：

```text
42 passed
0 failed
0 skipped
```

使用的真实依赖：

```text
LiteLLM model: qwen-plus
Temporal namespace: semantic-collaboration-dev
Temporal task queue: semantic-collaboration-dev
PostgreSQL: vet_agent / semantic_dag_* projection tables
```

远端开发库在本次验证前处于 Alembic `0021`，已按显式迁移流程升级到：

```text
0022_semantic_dag_scheduler
```

该操作只创建 M04 只读投影表，不修改 Temporal 内部表，也不创建 M11 artifact 表。

归档报告：

```text
.data/evaluations/semantic-collaboration-integration/
semantic-pre-m11-20260904-110337-5c610651-042.json

sha256=
9ee6dd0d01a3602c33be68a94121450e35e07079fe554b28e9ddedf91c42f717
```

报告摘要：

```text
report_version=semantic-pre-m11-integration-report-v1
execution_mode=semantic
case_count=42
passed=42
failed=0
skipped=0
```

边界说明：本结果表示当前 42 个可执行 case 全部通过，不等于集测设计文档中的全部
P0 矩阵已经关闭；剩余 P0 负例与细分工程 case 应在 M11 实现前继续补齐。

语义维度的第一公民解释见：

[semantic-collaboration-dag-pre-m11-semantic-validation-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-pre-m11-semantic-validation-summary.md)

## 2. 交付物

新增生产集成测试交付物：

```text
tests/integration/test_semantic_collaboration_pre_m11_external.py
tests/integration/semantic_collaboration/__init__.py
tests/integration/semantic_collaboration/contracts.py
tests/integration/semantic_collaboration/harness.py
tests/fixtures/semantic-collaboration/engineering-turns-v1.json
tests/fixtures/semantic-collaboration/semantic-regression-v1.json
scripts/integration/run-semantic-collaboration-pre-m11-smoke.sh
```

脚本支持：

```text
--ssh-tunnel
--semantic
--full-pre-m11
```

默认不访问外部服务；只有显式设置：

```text
RUN_SEMANTIC_COLLABORATION_EXTERNAL_TEST=true
```

才执行真实依赖测试。语义矩阵额外要求：

```text
RUN_SEMANTIC_COLLABORATION_SEMANTIC_TEST=true
```

## 3. 测试覆盖

### 3.1 工程链路

```text
ENV-001
ENG-001
ENG-011
ENG-023
ENG-032
```

覆盖：

```text
LiteLLM readiness / model list
Temporal SDK connect / namespace
PostgreSQL select / projection schema
TurnSnapshot digest
Root Plan IR
真实 M06 生成
M07 结构验证
真实 Temporal workflow / activity
任务终态
重复 workflow id
PostgreSQL projection 初始化、写入、finish、load
```

### 3.2 生成语义保真

```text
GEN-001
GEN-002
GEN-003
GEN-005
GEN-006
GEN-007
GEN-008
GEN-010
```

覆盖：

```text
normal
denied
uncertain
temporal scope
degree / intensity
shared scope
answer_now
复杂显式事实覆盖
可疑空结果拦截
```

### 3.3 M08 Review 校准

```text
REV-001
REV-002
REV-005
REV-006
REV-101
REV-102
REV-103
REV-105
REV-107
REV-109
REV-110
REV-111
```

覆盖：

```text
漏抽显式事实
多事实合并 / shared scope
非自包含 proposition
normal 写成 unknown
denied 写成 present
否定范围漂移
时间漂移
程度漂移
因果添加
医学建议添加
指代对象不明
```

### 3.4 M09 路由

```text
PLAN-001
PLAN-002
PLAN-003
PLAN-005
PLAN-006
PLAN-007
PLAN-008
PLAN-009
PLAN-010
PLAN-011
PLAN-012
```

覆盖：

```text
claim_inventory_repair
claim_proposition_repair
clarification_required
human_review_required
```

这些 case 使用固定 M08 矩阵构造输入，不依赖模型输出稳定性。

### 3.5 M10 修复

```text
FIX-001
FIX-003
```

覆盖：

```text
真实 proposition repair
真实 inventory sparse delta repair
typed patch verifier
application preview
test-only post-patch M07 / M08 probe
原 true dimension 消失
无新增 dimension
```

### 3.6 Fail Fast 与隔离

```text
NEG-010
NEG-012
NEG-013
ISO-001
```

覆盖：

```text
TODO TurnSnapshot reader
TODO M11 snapshot resolver
TODO M11 patch store
TODO SemanticTaskExecutor
生产 semantic_collaboration 包不引用问诊 / 临床安全 / 长期记忆 / 实验 runner
```

## 4. 测试-only 组件边界

### 4.1 StaticTurnSnapshotReader

按 digest 返回测试 fixture 构建的不可变 TurnSnapshot，不实现持久化读取，不进入
生产包。

### 4.2 FixedRepairTargetSnapshotResolver

返回显式构造的 M11 base snapshot 测试替身，只用于 M10 输入身份闭合：

```text
m11_store_used=false
m11_commit_performed=false
artifact_reference_is_authoritative=false
```

### 4.3 Scheduler-only SemanticTaskExecutor

返回确定性 M04 任务终态，用于验证 Temporal workflow、activity 和 projection。
该执行器不调用模型，也不宣称 M05～M11 生产执行器已组合。

### 4.4 Test-only post-patch probe

对 M10 preview claims 再次调用 M07 / M08，仅观察：

```text
原 dimension 是否消失
是否引入新 dimension
```

该 probe 不更新 artifact gate，不产生 `repair_verified`。

## 5. 真实集成暴露的问题与修复

### 5.1 DashScope 拒绝 array schema 的 `uniqueItems`

真实 LiteLLM / qwen-plus 对 `response_format.json_schema` 返回：

```text
When the schema contains the fields "uniqueItems" ...
the type should not be "array"
```

受影响 wire schema：

```text
Claim Inventory claims
Coverage Review missing_claim_candidates
```

修复：

```text
移除 wire JSON Schema 中的 uniqueItems
```

不变量保持不变：

```text
M07 继续确定性拒绝重复 claim
M08 verifier 继续确定性拒绝重复 missing claim candidate
```

因此该修复没有把重复项校验从 strict schema 转移为宽松模型输出，而是把不可传输的
JSON Schema 约束移至已有 deterministic verifier。

### 5.2 Temporal 默认 JSON converter 与 strict Pydantic tuple / enum 不兼容

真实 workflow 首次运行时暴露：

```text
validated_plan.plan.envelopes: list -> tuple
validated_plan.plan.tasks: list -> tuple
validated_plan.plan.dependencies: list -> tuple
task_policies: list -> tuple
```

原因：

```text
Temporal Python 默认 JSON plain converter 在部分场景不提供完整类型提示
strict Pydantic 模型收到 JSON list / str
```

修复：

1. 新增生产连接门面：

```text
connect_temporal_semantic_client(address, namespace)
```

该门面固定使用 Temporal `pydantic_data_converter`。

2. workflow 在记录 activity result 前执行确定性结构归一化：

```text
dict / enum string -> DAGTaskExecutionResult
```

该归一化只做 transport 结构转换，不解释业务结果、不放宽终态契约。

## 6. 报告契约

报告已包含：

```text
run_id
report_version
test_design_revision
code_revision
execution_mode
environment
case_results
safety_boundary
```

每个模型 case 记录：

```text
turn_snapshot_digest
plan_id
task_id
skill_id / skill_version
prompt_hash
proposal_digest
requested_model / response_model / response_id
finish_reason
latency_ms
prompt_tokens / completion_tokens / total_tokens
usage_available
m07_state
review_outcome
true_dimensions
repair_route / repair_lane
patch_state
preview_claim_count
post_patch_probe_state
original_dimension_resolved
new_dimension_introduced
```

安全边界全部保持：

```text
consultation_state_written=false
clinical_safety_evaluator_called=false
clinical_safety_retrieval_called=false
clinical_safety_opa_called=false
required_context_called=false
long_term_memory_written=false
mem0_called=false
old_semantic_extractor_called=false
input_preprocessing_experiment_called=false
heldout_read_count=0
dspy_used=false
m11_store_used=false
m11_commit_performed=false
artifact_reference_is_authoritative=false
```

## 7. 当前不能宣称

```text
M11 已实现
权威 artifact 已提交
repair_verified 已完成
M12 claim graph ready
问诊状态可消费
临床安全可消费
长期记忆可消费
VetOrchestrator 已接入
M14 生产观测闭环完成
```

## 8. 后续输入

本轮结果可作为 M11 实现输入。M11 至少需要解决：

```text
TurnSnapshot 持久化读取
base artifact snapshot
review artifact append-only 存储
patch append-only commit
version + 1
repair lineage
stale marker
幂等提交
artifact gate state
M10 后 M07 re-verify 编排
M10 后 M08 re-review 编排
repair_depth=1 后的显式终态
```

## 9. 剩余工作

当前 42-case 切片尚未覆盖集测设计中的全部 P0 矩阵。M11 实现前至少还需补齐：

```text
细分环境检查 case
契约组合负例
真实 LiteLLM metadata / schema failure 细分 case
Temporal blocked / timeout / cancel 细分 case
PostgreSQL dependency failure / identity conflict 细分 case
更多 M10 patch 负例
TODO 与身份错配负例
领域隔离独立 case
```

补齐后必须重新执行完整 P0 gate，并归档新的报告。

## 10. 关联材料

1. [semantic-collaboration-dag-pre-m11-integration-test-design.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-pre-m11-integration-test-design.md)
2. [semantic-collaboration-dag-pre-m11-semantic-validation-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-pre-m11-semantic-validation-summary.md)
3. [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
4. [semantic-collaboration-dag-production-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-implementation-plan.md)
5. [semantic-collaboration-dag-m04-scheduler-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m04-scheduler-change-summary.md)
6. [semantic-collaboration-dag-m10-repair-skill-patch-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m10-repair-skill-patch-change-summary.md)

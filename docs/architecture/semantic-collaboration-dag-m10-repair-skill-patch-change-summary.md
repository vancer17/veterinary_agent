<!--
=============================================================================
文件: semantic-collaboration-dag-m10-repair-skill-patch-change-summary.md
作用: 记录受限语义协作 DAG M10 Repair SKILL 与 typed patch 的生产对接基线。
范围: 覆盖 M10 的上游输入、两个通用 Repair lane、极薄模型输出、局部 selector、
      稀疏 inventory delta、系统 operation 推导、patch 验证、原子应用预览、
      M11 / M04 / M07 / M08 后续对接边界与有意 TODO。
说明: 本文只描述稳定契约、状态语义、验收边界和模块职责，不展开软件包内部
      类图、函数实现、测试替身、数据库 DDL、Temporal workflow 或 prompt 全文。
维护: 当 Repair lane、模型输出 schema、selector、operation、patch 状态、
      M11 绑定或修复后 re-review 闭环调整时，必须同步更新本文和生产架构基线。
=============================================================================
-->

# 受限语义协作 DAG M10 Repair SKILL 与 Patch 对接基线

> **文档状态**：M10 生产工程边界已实现；待 M11 Artifact Store、M04 任务执行器
> 组合与 M07 / M08 re-review 闭环联调
>
> **当前结论**：M10 可以在测试替身支撑下生成经过确定性验证的 patch set 和
> application preview；但由于 M11 权威 artifact 存储尚未实现，M10 尚不能进入
> 生产主路径，也不能宣称端到端 verified claim graph。

## 1. 当前状态

| 项目 | 状态 | 说明 |
|---|---|---|
| `claim_inventory_repair` | 已实现 | Coverage 已知问题统一进入 inventory 修复 lane |
| `claim_proposition_repair` | 已实现 | 单条 claim 的已知漂移 / 越权统一进入 proposition 修复 lane |
| proposition 极薄输出 | 已实现 | 模型只输出一个修复 proposition |
| inventory 稀疏 delta | 已实现 | 模型只输出局部修改和新增 claim，不输出完整 claims |
| local selector `c0/c1/...` | 已实现 | 任务内目标选择符由系统生成和解析 |
| `addresses_dimensions` 模型输出 | 已禁止 | repair dimensions 只作为输入先验和系统信封，不由模型自证 |
| deterministic operation 推导 | 已实现 | operation 类型与插入位置由系统根据 delta 推导 |
| patch 验证 | 已实现 | 校验身份、目标、预算、digest、最终 claims 形态 |
| patch set 验证 | 已实现 | 多个 proposition patch 共享同一 base version 并原子应用 |
| application preview | 已实现 | 生成 next version 与应用后 claims 的确定性预览 |
| M11 base snapshot 读取 | TODO | 显式 Fail Fast，不伪造 claims / version / artifact |
| M11 append-only patch 提交 | TODO | 显式 Fail Fast，不生成伪 artifact reference |
| M04 任务执行器组合 | 未接入 | M10 尚未进入 SemanticTaskExecutor 生产链路 |
| M07 re-verify | 未编排 | patch 后新 claims 仍需重新结构验证 |
| M08 re-review | 未编排 | patch 是否语义有效必须由独立 review 判断 |
| 真实 LiteLLM Repair 调用 | Pre-M11 集成已通过 | 已验证 proposition / inventory 两条 lane 与 patch preview |
| M10 接入真实 Temporal 生产执行器 | 未执行 | 当前仅 M04 scheduler-only workflow 独立验证 |

## 2. 架构位置

M10 位于 M09 与 M11 之间：

```text
M06 Claim Inventory
→ M07 structural verifier
→ M08 Coverage / Faithfulness Review
→ M09 Repair Planner
→ M10 Repair SKILL / typed patch
→ M11 append-only Artifact Store
→ M07 structural re-verify
→ M08 semantic re-review
→ M12 Claim Graph
```

M10 的输入是：

```text
accepted M09 SemanticRepairPlan
M11 base artifact snapshot
TurnSnapshot 受限上下文
精确模型策略
```

M10 的输出是：

```text
verified patch set
deterministic application preview
patch_ready / blocked 工程状态
```

M10 不输出：

```text
权威 artifact
verified claim graph
问诊状态
临床安全信号
长期记忆
用户追问
```

## 3. 模块职责

### 3.1 Repair LLM 负责

```text
理解 current_turn 与授权上下文
理解 M09 声明的 repair dimensions
对单条 proposition 提出修复后的 proposition
对 claim inventory 提出稀疏局部修改 / 新增 proposal
```

### 3.2 系统负责

```text
解析 local selector
推导 operation 类型
确定新增 claim 插入位置
附加 target claim index / digest
附加 artifact reference / base version
附加 patch id / task identity
校验 M09 / M11 / TurnSnapshot 身份
校验 operation 预算与最终 claims 形态
校验 patch set 冲突
生成 application preview
```

### 3.3 M11 负责

```text
提供权威 base artifact snapshot
提交 append-only 新版本
维护 version / lineage / stale / 幂等
更新 artifact gate state
```

### 3.4 M07 / M08 负责

```text
M07:
  对修复后的 claim inventory 重新执行结构验证

M08:
  对修复后的 claims 重新执行 Coverage / Faithfulness Review
```

M10 不自行判断修复后的结果已经 verified。

## 4. 两个通用 Repair lane

M10 不按每个 review dimension 建立细粒度 Python 修复分支，也不注册一组
症状级或医学级修复 SKILL。

生产只保留两个上下文粒度：

```text
claim_inventory_repair
claim_proposition_repair
```

### 4.1 `claim_inventory_repair`

适用 M09 Coverage 已知问题：

```text
存在漏抽显式事实
存在多事实合并
存在重复claim
存在原文不支持的claim
存在非自包含proposition
存在shared scope拆分错误
```

该 lane 可以看到：

```text
current_turn
必要的有界授权上下文
系统编号的 claim candidates
repair_dimensions
非权威 missing claim hints
```

它不能看到：

```text
生成器 prompt / reason / confidence / metadata
Reviewer prompt / metadata
其他未授权上下文
问诊状态
临床安全状态
长期记忆
```

### 4.2 `claim_proposition_repair`

适用 M09 Faithfulness 已知漂移 / 越权：

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

该 lane 一次只看到一条目标 proposition。

来源绑定缺失不由该 lane 消解：

```text
指代对象不明
时间基准不明
否定范围不明
比较基线不明
```

这些仍由 M09 保留 clarification gap。

## 5. 模型输出契约

### 5.1 Proposition repair 输出

模型只输出：

```json
{
  "proposition": "英短精神状态正常"
}
```

禁止输出：

```text
target
operation
addresses_dimensions
claims / corrected_claims
reason
confidence
verdict
evidence
claim_digest
claim_id
patch_id
repair_task_id
run_id / task_id
artifact_reference
base_version
```

系统根据 M09 task 已携带的目标身份，将其包装为唯一：

```text
replace_claim operation
```

### 5.2 Inventory repair 输出

模型只输出稀疏 delta：

```json
{
  "modified_claims": [
    {
      "target": "c0",
      "propositions": [
        "英短进食正常",
        "英短饮水正常"
      ]
    }
  ],
  "added_claims": [
    "英短没有血便"
  ]
}
```

核心约定：

```text
omission = keep
```

即：

```text
未出现在 modified_claims 中的 cX
由系统从 M11 base artifact 原样保留
```

模型不需要、也不允许复述完整 claim inventory。

### 5.3 `repair_dimensions` 的边界

`repair_dimensions` 来自：

```text
M09 SemanticRepairTask.review_dimensions
```

它有两个用途：

```text
1. 作为 Repair prompt 的输入语义先验
2. 作为系统 patch 信封的审计字段
```

它不是模型输出，也不表示模型自证修复成功。

模型 patch 是否真正覆盖这些维度，只能由后续 M08 re-review 判断。

## 6. local selector

Prompt 中的 claim candidates 形态：

```text
c0: 英短进食和饮水都正常
c1: 英短没有呕吐
c2: 英短大便偏软
```

`c0 / c1 / c2` 是当前修复任务内的临时 selector，不是：

```text
全局 claim id
claim digest
artifact id
run id
task id
```

系统解析 selector 后附加：

```text
target_claim_index
base_claim_digest
```

当 base inventory 为空时：

```text
<claim_candidates>
none
</claim_candidates>
```

模型仍可通过 `added_claims` 修复 suspicious empty。

这样可以避免：

```text
模型逐字复述 claim
模糊 phrase matching
模型发明全局身份
输出完整 claims 数组
```

## 7. 系统确定性 operation 推导

operation 类型不由模型输出。

系统根据稀疏 delta 推导：

```text
target + propositions=[]
→ remove_claim

target + propositions=[1 条]
→ replace_claim

target + propositions=[2 条以上]
→ replace_claim_with_claims

added_claims
→ add_claim
```

新增 claim 的插入位置由系统固定策略决定：

```text
追加到当前 claim inventory 末尾
```

模型不输出：

```text
operation
after_claim_index
claim_del
claim_add
claim_update
```

这避免了 action boolean 与 propositions 数量之间的自相矛盾，也避免模型把
注意力从语义修复转移到结构分类。

## 8. 系统 patch 信封

系统生成的 patch proposal 包含：

```text
patch_id
repair_plan_id
repair_task_id
repair_skill_id / repair_skill_version
run_id
turn_snapshot_digest
source_proposal_digest
review_bundle_digest
artifact_reference
base_version
repair_dimensions
compiled operations
model_output_digest
model_metadata
```

权威来源：

```text
patch_id / task identity
  系统

operations
  deterministic compiler

repair_dimensions
  M09

base_version / artifact_reference / base claims
  M11 snapshot

model_output_digest / model_metadata
  M05 structured gateway
```

模型不参与工程身份构造。

## 9. 确定性验证边界

### 9.1 单 patch 验证

系统验证：

```text
patch id 可复算
repair task 属于 accepted M09 plan
M11 snapshot 身份一致
repair_depth = 0
lane 与 operation 形态匹配
target selector 可解析
target claim index 存在
base claim digest 匹配
operation 数量未超预算
proposition 符合单行 / 非空 / 长度契约
最终 claims 数量不超过 8
最终 claims 无重复
patch 不产生 no-op 结果
```

不验证：

```text
医学正确性
语义忠实性
是否真正修复 M08 维度
```

这些属于 M08 re-review。

### 9.2 Patch set 验证

当 M09 plan 包含两个 proposition repair tasks 时：

```text
两个 Repair LLM 调用可分别执行
两个 patch 必须绑定同一 base artifact / base version
patch set 必须覆盖全部 M09 repair tasks
repair task 不得重复
目标 claim 不得重叠
最终 claims 必须一次性通过应用预览
```

Patch set 统一应用，避免第二个 patch 因第一个版本提交而发生 base version conflict。

### 9.3 Application preview

M10 可以生成：

```text
PatchApplicationPreview
```

包含：

```text
base artifact reference
base version
next version = base version + 1
应用后的 claims
```

它只是确定性预览，不是权威 artifact。

M10 不写数据库，不提交 M11，不调用 M08，不标记 verified。

## 10. 有意 TODO

### 10.1 M11 base artifact snapshot

M10 需要 M11 提供：

```text
claims
artifact_reference
base_version
repair_depth
source_proposal_digest
review_bundle_digest
turn_snapshot_digest
```

该 snapshot 是 M10 读取 base claims 和绑定版本的唯一权威来源。

当前显式 TODO 行为：

```text
Fail Fast
```

不允许：

```text
从 M08 bundle 重建 base claims
用内存状态伪装 M11 snapshot
伪造 artifact_reference
伪造 base_version
```

### 10.2 M11 append-only patch store

M11 需要接收：

```text
verified patch set
application preview
```

并负责：

```text
append-only commit
new version
repair lineage
stale marker
幂等提交
artifact gate state
```

当前显式 TODO 行为：

```text
Fail Fast
```

不允许：

```text
返回伪 new artifact reference
在内存中模拟权威提交
绕过 lineage / stale / version
```

### 10.3 M04 SemanticTaskExecutor 组合

后续 M04 需要组合：

```text
M06 generation
→ M07 structural verifier
→ M08 review
→ M09 repair planning
→ M10 patch proposal / preview
→ M11 artifact commit
→ M07 / M08 re-verify / re-review
```

M04 负责：

```text
任务编排
终态映射
依赖失败传播
artifact reference 消费
```

M04 不负责：

```text
解析 repair dimensions
编译 patch
验证 patch
判断医学或语义修复效果
```

### 10.4 M07 / M08 re-review 闭环

M10 输出 `patch_ready` 后，后续必须执行：

```text
M11 append-only commit
→ M07 structural re-verify
→ M08 Coverage / Faithfulness re-review
→ M11 更新 artifact gate state
```

如果 re-review 后仍存在问题，由于：

```text
repair_depth = 1
不允许 repair of repair
```

应进入：

```text
repair_failed
repair_exhausted
human_review_required
```

不能继续自动递归修复。

### 10.5 真实外部服务联调

当前已完成：

```text
真实 LiteLLM Repair SKILL 调用
M10 patch verifier / application preview
test-only post-patch M07 / M08 probe
```

尚未执行：

```text
M10 接入真实 Temporal workflow / activity 生产执行器
M11 PostgreSQL artifact 存储联调
```

这些是后续生产闭环前置条件。

## 11. 状态语义

M10 的成功工程状态是：

```text
patch_ready
```

它表示：

```text
patch set 通过确定性验证
application preview 可提交 M11
```

它不表示：

```text
verified
repair_verified
graph_ready
clinical safe
```

M10 的 blocked 状态保留：

```text
failure code
failure message
来源身份
审计信息
```

不得转换为：

```text
空 patch
原 claims 原样通过
旧问诊语义抽取器 fallback
自由重写
```

## 12. 安全与领域隔离

M10 保持：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
long_term_memory_written = false
```

禁止：

```text
完整 claims 重写
自由 JSON Patch
模型输出 operation / after_claim_index / addresses_dimensions
模型输出 base_version / artifact_reference / patch_id
模型复述未修改 claim
补造 TurnSnapshot 中不存在的事实
自动消解来源绑定缺失
评估医学推断是否正确
生成新的诊断、风险、就医或治疗建议
修复未分类问题
修复 disagreement
repair of repair
旧问诊抽取器 fallback
```

## 13. 对接验收清单

后续 M11 / M04 接入前，应以下列边界验收：

```text
M11 snapshot 身份与 M09 plan 一致
M11 repair_depth = 0
patch 提交后 version + 1
append-only 提交可追溯
重复提交幂等
上游 patch 后下游 stale 标记有效
M10 patch_ready 不被写成 verified
M11 commit 后可触发 M07
M07 accepted 后可触发 M08 re-review
M08 re-review 结果进入 M11 gate state
repair_depth = 1 被持续执行
```

## 14. 当前工程验证结果

本地已通过：

```text
ruff check semantic_collaboration + M10 tests
mypy src/vet_agent/semantic_collaboration
pytest tests/test_semantic_collaboration_*.py
pytest 全量默认测试
```

当前结果：

```text
semantic_collaboration tests: 103 passed
全量默认测试: 321 passed, 43 skipped
```

覆盖：

```text
Repair 配置闭合
proposition 输出拒绝自证字段
inventory 稀疏 delta 拒绝完整 claims
proposition runner 生成 replace patch
inventory runner 生成 split / add operations
两个 proposition patch 原子应用
snapshot 身份漂移 blocked
重复 patch target blocked
M11 TODO Fail Fast
空 inventory 可新增首条 claim
no-op proposition 替换 blocked
```

以上验证使用进程内测试替身，不代表真实 LiteLLM、Temporal 或 M11 集成完成。

Pre-M11 集成补充结果：

```text
真实 LiteLLM proposition repair: PASS
真实 LiteLLM inventory sparse delta repair: PASS
patch verifier / application preview: PASS
test-only post-patch probe: PASS
M11 commit: 保持 TODO Fail Fast
```

## 15. 关联材料

1. [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
2. [semantic-collaboration-dag-production-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-implementation-plan.md)
3. [semantic-collaboration-dag-pre-m11-integration-test-design.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-pre-m11-integration-test-design.md)
4. [semantic-collaboration-dag-pre-m11-integration-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-pre-m11-integration-change-summary.md)
5. [semantic-collaboration-dag-m09-repair-planner-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m09-repair-planner-change-summary.md)
6. [semantic-collaboration-dag-m04-scheduler-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m04-scheduler-change-summary.md)
7. [semantic-collaboration-dag-m08-review-skill-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m08-review-skill-change-summary.md)

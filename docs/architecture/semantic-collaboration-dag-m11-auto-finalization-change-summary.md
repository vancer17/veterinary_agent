<!--
=============================================================================
文件: semantic-collaboration-dag-m11-auto-finalization-change-summary.md
作用: 定义受限语义协作 DAG M11 自动终态化与移除独立 Evidence Binding 阶段的
      目标生产契约、变更原因、状态机边界、历史兼容与验收口径。
范围: 覆盖 M08 outcome 到 M11 gate 的确定性映射、append-only event 契约、
      clarification gap 的部分消费策略、M12/M13/M15 对接影响、迁移策略、
      防退化约束与测试验收。
说明: 本文记录已落地的生产契约；历史 evidence_binding_pending 仅保留
      兼容读取，新生产提交不再写入该状态。
维护: 当 M11 自动终态化规则、gate 状态、M12 消费矩阵、历史兼容策略或
      生产验收口径调整时，必须同步更新本文与生产架构基线。
=============================================================================
-->

# 受限语义协作 DAG M11 自动终态化变更基线

> **文档状态**：M11 auto-finalization 生产边界已实现；待 M04 任务执行器组合、
> 真实 PostgreSQL / Temporal 端到端联调与 M15 生产接入
>
> **决策结论**：移除独立 Evidence Binding 阶段；由 M11 在权威提交事务内执行
> 确定性结构终态化，并将可消费结果显式写入 append-only gate event。M12 只消费
> M11 gate，不提升任何状态。
>
> **文档定位**：本文面向 M11 / M12 / M13 / M15 后续实现者，说明目标契约、
> 有意移除的能力与不可越界的行为。

## 1. 核心决策

当前生产实现状态：

| 能力 | 状态 | 对接含义 |
|---|---|---|
| M11 deterministic policy | 已实现 | supported 结果按结构闭合自动终态化 |
| verified system gate event | 已实现 | actor=system，append-only，幂等随 review commit 提交 |
| repair_verified system gate event | 已实现 | patch 后 re-review supported 时自动授予 |
| clarification_required 保守保留 | 已实现 | 不伪装 verified，不自动消解 gap |
| M12 partial gate 消费矩阵 | 已实现 | clarification_required + policy 可输出 partial graph |
| 历史 evidence_binding_pending 兼容 | 已实现 | 可解析但 M12 blocked，不批量提升 |
| M04 production executor 组合 | 未实现 | M11 尚未进入 Temporal 生产任务执行链 |
| 真实 PostgreSQL / Temporal 联调 | 未执行 | 待 M15 前集成验证 |

旧链路为：

```text
M08 semantic_review_supported
→ M11 evidence_binding_pending
→ 独立 Evidence Binding / 人工证据门禁
→ verified / repair_verified
→ M12 graph
```

目标链路为：

```text
M08 outcome
→ M11 权威 artifact commit
→ M11 deterministic auto-finalization
→ verified / repair_verified / clarification_required / blocked 等显式 gate
→ M12 graph projection
→ M13 domain projection
```

关键决策：

```text
1. 不再设立独立 Evidence Binding service。
2. 不要求 claim 回指用户原文。
3. 不引入模型输出的 evidence quote / offset / reason / confidence。
4. M08 继续承担语义支持性审查。
5. M11 继续保存 TurnSnapshot / proposal / review / artifact / repair 的权威身份链。
6. M11 在 commit 事务内执行确定性结构终态化。
7. supported 且结构闭合时，由 M11 直接追加 verified event。
8. 修复后 re-review 支持且结构闭合时，由 M11 直接追加 repair_verified event。
9. clarification gap 不被自动消解；M12 只能在显式策略下分类为 partial graph。
10. M12 永远不提升 M11 gate，不写 M11 event。
```

## 2. 变更原因

### 2.1 独立 Evidence Binding 与既有链路存在重复

M08 已经基于 TurnSnapshot 与授权上下文完成：

```text
Coverage Review
Faithfulness Review
unsupported claim 判断
来源绑定缺失判断
语义漂移维度定位
```

M11 已经保存：

```text
turn_snapshot_digest
source_task_id
source_proposal_digest
review_bundle
claims / claim_digest
repair_lineage
artifact_reference
artifact_version
artifact_payload_digest
```

如果独立 Evidence Binding 只是再次校验这些身份和结构引用，则没有引入新的判断
信息，只增加一个状态、一次等待和一条生产链路。

### 2.2 Claim-to-original-text 不是必要能力

Claim 是自包含自然语言 proposition。用户原文和授权上下文保存在 TurnSnapshot。

以下路径明确不做：

```text
claim → 用户原文 quote
claim → 字符 offset
claim → 模型输出的 evidence phrase
claim → 模型自证 reason / confidence
```

原因：

```text
quote 不能证明语义正确
中文指代、省略和 shared scope 使 span-only 绑定不可靠
修复后的 proposition 可能是规范化表达而非原文逐字片段
模型自证 evidence 会重新引入幻觉路径
```

Claim 的来源链通过 M11 权威身份表达：

```text
claim
→ artifact version
→ review bundle
→ source proposal
→ TurnSnapshot
```

### 2.3 降低 M15 前置复杂度

旧方案下，M15 必须等待独立证据门禁，否则真实链路会停在：

```text
M11 evidence_binding_pending
→ M12 graph_blocked
→ M13 rejected
```

目标方案下，M15 的前置条件收敛为：

```text
M04 production task executor 组合完成
M11 auto-finalization 完成并通过验收
M12/M13 消费契约保持稳定
```

## 3. 术语重定义

### 3.1 provenance / traceability

结构化链路可以证明：

```text
这个 claim 属于哪个 artifact version
这个 artifact 绑定哪个 TurnSnapshot
这个 artifact 来自哪个 source proposal
它包含哪个 review bundle
它是否被后续版本取代
它是否经过修复
```

这是来源可追溯性，不等于语义正确性。

### 3.2 semantic support

语义支持性由 M08 判断：

```text
是否存在漏抽
是否存在原文不支持的 claim
是否存在主体 / 否定 / 时间 / 确定性漂移
是否存在医学推断添加
是否存在来源绑定缺失
```

M11 auto-finalization 不重复这些判断。

### 3.3 consumption eligibility

`verified` / `repair_verified` 表示：

```text
M08 已给出支持性 outcome
M11 结构、身份、版本、review 与 repair lineage 校验闭合
M11 auto-finalization policy 允许下游消费
该决定已写入 append-only gate event
```

它不表示：

```text
医学正确
人工逐条事实认证
claim-to-original-text anchor
长期事实稳定
临床安全结论
```

## 4. 目标状态机

| 上游状态 | M11 目标 gate | 自动终态化行为 | M12 消费 |
|---|---|---|---|
| M08 supported，非空，无 active gap | `verified` | 追加 system auto-finalization event | `graph_verified` |
| M08 supported，`no_explicit_fact` 空图 | `verified` | 空 graph 原因结构合法时追加 event | policy 允许时 `graph_verified` |
| M08 `clarification_required` | `clarification_required` | 不伪造成 verified | policy 允许时 `graph_partial_with_gaps` |
| M08 `repair_required` | `repair_required` | 不进入 graph | blocked |
| M08 `repair_then_clarification_required` | `repair_then_clarification_required` | 先修复，不直接消费 | blocked |
| M10 patch 后、未 re-review | `patch_applied` | 不自动 repair_verified | blocked |
| repair 后 re-review supported、无 gap | `repair_verified` | 追加 system auto-finalization event | `graph_verified` |
| repair 后 re-review 仍有 gap | `clarification_required` | 保留 gap，不写 repair_verified | policy 允许时 `graph_partial_with_gaps` |
| `human_review_required` | `human_review_required` | 不自动放行 | blocked |
| `disagreement` | `disagreement` | 不裁决 | `graph_disagreement` |
| `review_failed` / schema invalid / budget / timeout 等失败 | 对应显式失败态 | 不放行 | blocked |

`suspicious_empty` 不允许自动 `verified`。

若 supported artifact 缺少权威 Turn Intent section，M11 保持
`semantic_review_supported` 而不自动 `verified`；M12 必须因 Turn Intent 缺失
blocked，不得回读 M06 proposal 补齐。

## 5. M11 auto-finalization 输入校验

M11 在追加 `verified` / `repair_verified` 前至少校验：

```text
artifact payload digest 可复算
artifact 是当前 latest version
artifact 未被 stale marker 标记
review bundle 存在
review bundle 与 source proposal / TurnSnapshot 身份闭合
权威 Turn Intent section 存在
claims index 连续
claim digest 无结构重复
faithfulness review 覆盖全部 claims
review record 的 claim digest / proposition 与 artifact claims 一致
clarification gap 引用当前 claim digest
repair_depth 与 repair lineage 一致
repair_depth=1 时必须已经完成 re-review
empty graph 必须携带结构化 empty_reason
suspicious_empty 不进入 verified
```

这些检查是结构性的，不调用 LLM，不扫描用户原文，不做医学判断。

## 6. M11 gate event 契约

自动终态化不是修改 payload，也不是仅在内存中改变状态。它必须随 M11 权威提交
事务追加 gate event。

普通 artifact：

```text
from_gate = semantic_review_supported
to_gate = verified
actor_type = system
reason_code = m11_auto_verified_structural_lineage
decision_digest = sha256(...)
```

修复后 artifact：

```text
from_gate = semantic_review_supported
to_gate = repair_verified
actor_type = system
reason_code = m11_auto_repair_verified_after_re_review
decision_digest = sha256(...)
```

`decision` 至少记录：

```text
policy_version
artifact_payload_digest
review_bundle_digest
turn_snapshot_digest
claim_digests
repair_plan_id
repair_base_artifact_reference
repair_base_version
active gap ids
```

要求：

```text
decision 由系统生成，不允许模型输出
不允许下游伪造
不修改 artifact payload
与 artifact commit 使用同一幂等 request 语义
重放不得追加第二个终态化 event
event append-only
```

## 7. Clarification gap 部分消费

来源绑定缺失维度：

```text
指代对象不明
时间基准不明
否定范围不明
比较基线不明
```

不得由 M11 auto-finalization 猜测补全，也不得自动 resolve。

目标行为：

```text
M11 gate 保持 clarification_required
active gap 保留在 payload / 派生索引
M12 在 allow_partial_with_gaps=true 时输出 graph_partial_with_gaps
M13 问诊投影保留 gap
是否追问由问诊回答充分性 / followup 策略决定
```

关键区别：

```text
M12 可以把 clarification_required 分类为 graph_partial_with_gaps
M12 不能把它改写为 verified
```

## 8. M12 消费矩阵

M12 是 M11 gate 的确定性投影器，不是第二状态机。

| M11 current gate | Graph policy | M12 result |
|---|---|---|
| `verified`，无 gaps | 默认可消费 | `graph_verified` |
| `verified`，合法 empty graph | `allow_empty_graph=true` | `graph_verified` |
| `clarification_required`，active gaps | `allow_partial_with_gaps=true` | `graph_partial_with_gaps` |
| `clarification_required`，active gaps | 不允许 partial | blocked |
| `repair_verified`，无 gaps | 默认可消费 | `graph_verified` |
| repair 后仍 clarification，lineage/re-review 闭合 | `allow_partial_with_gaps=true` | `graph_partial_with_gaps` |
| `patch_applied` | 任意 | blocked，等待 re-review |
| `disagreement` | 任意 | `graph_disagreement` |
| 其他失败态 | 任意 | blocked |

M12 禁止：

```text
写 M11 gate event
把 pending 提升为 verified
把 clarification_required 提升为 verified
把 patch_applied 提升为 repair_verified
为了让下游可用而绕过 M11 状态机
```

## 9. M13 / M15 对接影响

M13 不感知 M11 auto-finalization 细节，仍只消费：

```text
M12 SemanticGraphBuildResult
```

M13 可消费状态不变：

```text
graph_verified
graph_partial_with_gaps
```

M15 的前置条件从“独立 evidence binding 完成”调整为：

```text
M11 auto-finalization 完成并通过验收
M04 production SemanticTaskExecutor 完成
M12/M13 生产消费边界就绪
```

M15 仍然不得：

```text
绕过 M12 直接读取 M11
在失败后回退旧问诊抽取器
把 partial graph 伪装为 completed
吞掉 graph failure
```

## 10. 历史兼容与迁移

历史实现可能已经产生：

```text
evidence_binding_pending gate
actor_type=evidence_binding event
```

迁移原则：

```text
1. 不删除历史 gate event。
2. 不批量把历史 pending artifact 改成 verified。
3. 保留历史 gate / actor 枚举的读取兼容。
4. 新生产提交不再写 evidence_binding_pending。
5. 历史 pending artifact 默认保持 blocked，直到重新提交或完成显式审计迁移。
6. 如未来需要迁移历史数据，必须单独立项并使用可审计迁移工具。
```

第一版不建议做历史数据迁移。

## 11. 实现切分与当前状态

目标实现切分与当前状态：

```text
1. 新增 M11 auto-finalization policy 与结构校验。已实现。
2. 调整 M08 review commit 的目标 gate event。已实现。
3. 调整 M10 patch / re-review 后的目标 gate event。已实现。
4. 调整 M12 可消费 gate 矩阵与 partial graph 分类。已实现。
5. 删除测试中手工追加 verified gate 的生产假设。已实现。
6. 增加历史 pending 兼容测试。已实现。
7. M15 前置条件与生产联调文档保持后续责任。
```

## 12. 防退化约束

后续实现不得引入：

```text
独立 Evidence Binding service
claim-to-user-text quote / offset 绑定
模型输出 evidence phrase / reason / confidence
M12 提升 M11 gate
M15 绕过 M12 消费 M11
把结构 traceability 伪造成语义正确性证明
把 verified 解释为医学正确或人工事实认证
把 clarification gap 自动 resolve
把 graph_partial_with_gaps 伪装为 graph_verified
批量提升历史 evidence_binding_pending
在 auto-finalization 中调用 LLM
在 auto-finalization 中扫描用户原文关键词
失败转空 facts
```

## 13. 验收口径

### 13.1 M11

```text
supported + Turn Intent + 结构闭合 → verified
合法 no_explicit_fact 空图 → verified
suspicious_empty → 不 verified
clarification_required → 不伪造成 verified
human_review_required / disagreement / review_failed → 不放行
patch_applied → 等待 re-review
repair + re-review supported → repair_verified
repair 后仍有 gap → 保持 clarification_required
终态化 event append-only
decision digest 稳定
幂等重放不追加第二个 event
```

### 13.2 M12

```text
verified → graph_verified
clarification_required + allow_partial → graph_partial_with_gaps
clarification_required + 不允许 partial → blocked
repair_verified → graph_verified
patch_applied → blocked
disagreement → graph_disagreement
M12 不写 gate event
M12 不生成 verified
```

### 13.3 兼容与隔离

```text
历史 evidence_binding_pending 可解析
历史 pending 不自动消费
新 artifact 不再写 evidence_binding_pending
auto-finalization 不调用 LLM
auto-finalization 不读取用户原文
问诊 / 临床安全 / 长期记忆状态均未被写入
```

## 14. 验证结果

本地质量验证：

```text
compileall src tests: PASS
ruff check semantic_collaboration + M11/M12/M13 tests: PASS
mypy src/vet_agent/semantic_collaboration: PASS
pytest tests/test_semantic_collaboration_*.py: 120 passed
pytest 全量默认测试: 338 passed, 84 skipped
```

新增与调整测试覆盖：

```text
supported + Turn Intent + 结构闭合 → verified system event
幂等 review replay 不追加第二个终态化 event
clarification_required 不伪装 verified
M10 patch 后保持 patch_applied
repair + re-review supported → repair_verified system event
系统性人工审查不批量提升 verified
M12 消费 M11 auto verified graph
M12 消费 clarification_required partial graph
历史 evidence_binding_pending 保持 blocked
缺 Turn Intent 不自动 verified
M13 不再依赖测试手工 evidence gate
```

## 15. 关联材料

1. [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
2. [semantic-collaboration-dag-production-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-implementation-plan.md)
3. [semantic-collaboration-dag-m11-artifact-store-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m11-artifact-store-change-summary.md)
4. [semantic-collaboration-dag-m12-claim-graph-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m12-claim-graph-change-summary.md)
5. [semantic-collaboration-dag-m13-domain-projection-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m13-domain-projection-change-summary.md)
6. [semantic-collaboration-dag-m06-production-boundary-revision.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m06-production-boundary-revision.md)

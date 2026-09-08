<!--
=============================================================================
文件: semantic-collaboration-dag-m13-domain-projection-change-summary.md
作用: 记录受限语义协作 DAG M13 领域投影 Adapter 的生产工程实现边界。
范围: 覆盖 M12 graph 消费契约、问诊投影、临床安全 TODO 空壳、长期记忆
      TODO 空壳、失败状态、职责边界、测试验收与后续接入前提。
说明: M13 是语义协作 DAG 与领域系统之间的确定性防腐层，不是语义抽取器、
      医学规则引擎、追问策略或领域状态写入器；本文不展开内部函数实现。
维护: 当 M13 输入输出契约、领域消费状态、TODO 责任边界或 M15 接入前提
      调整时，必须同步更新本文与生产架构基线。
=============================================================================
-->

# 受限语义协作 DAG M13 领域投影生产实现总结

> **文档状态**：M13 问诊投影 Adapter 已实现；临床安全与长期记忆 Adapter
> 保留显式 TODO 空壳，待各自领域输入契约明确后替换
>
> **文档定位**：本文面向问诊领域、临床安全领域、长期记忆领域与 M15 生产
> 接入实现者，描述当前稳定边界和有意未完成项。

## 1. 核心结论

M13 的职责是：

```text
读取 M12 SemanticGraphBuildResult
→ 按 adapter 显式契约筛选可消费 graph 终态
→ 确定性投影为领域消费契约
→ 附加来源 graph 身份与 projection id
→ 输出 projected / rejected 显式终态
```

M13 不是：

```text
语义抽取器
语义审查器
normal / denied / uncertain 关键词分类器
医学风险判断器
追问生成器
问诊状态写入器
临床安全 evaluator / OPA 调用器
长期记忆写入器
权威事实存储
```

M13 的唯一输入是：

```text
SemanticGraphBuildResult
```

M13 不读取：

```text
M11 内部 payload
M08 review matrix
M09 repair plan
M10 patch set
M06 proposal
原始用户文本
scheduler projection
```

## 2. 当前实现状态

| 能力 | 状态 | 对接含义 |
|---|---|---|
| M13 通用投影契约 | 已实现 | adapter kind、消费终态、实现状态、结果信封与失败码稳定 |
| 来源身份绑定 | 已实现 | 投影结果绑定 graph id / digest / artifact version / gate sequence |
| 确定性 projection id | 已实现 | 由 adapter 契约、graph 身份与投影负载派生 |
| 问诊投影 Adapter | 已实现 | 可消费 verified 与 partial_with_gaps |
| clarification gap 透传 | 已实现 | gap 保留结构字段，不生成追问指令 |
| 空 graph 投影 | 已实现 | no_explicit_fact 保持显式 empty reason，不转为失败 |
| 临床安全投影 Adapter | TODO 空壳 | 输入契约未定义时显式 adapter_not_implemented |
| 长期记忆候选投影 Adapter | TODO 空壳 | 候选策略未定义时显式 adapter_not_implemented |
| M13 权威持久化 | 有意未实现 | 投影是可重建结果，不是第二事实源 |
| M15 生产接入 | 未实现 | M13 尚未进入 VetOrchestrator 主路径 |

## 3. 通用投影契约

每个 M13 adapter 声明：

```text
adapter_kind
contract_version
implementation_state
accepted_terminal_states
```

契约启动校验禁止任何 adapter 声明消费：

```text
graph_blocked
graph_disagreement
```

统一投影结果信封包含：

```text
adapter_kind
adapter_contract_version
adapter_implementation_state
execution_state
source graph identity
domain projection payload
projection_id
failure_code / failure_message
```

系统保证：

```text
projected 结果必须携带 graph 来源、领域负载与 projection id
rejected 结果不得携带领域负载或 projection id
rejected 结果必须携带稳定失败码
失败不得转换为空 facts、空候选或旧链路回退
```

## 4. 问诊投影 Adapter

### 4.1 可消费状态

```text
graph_verified
graph_partial_with_gaps
```

### 4.2 输出负载

问诊投影输出：

```text
Turn Intent 布尔意图
不透明自然语言 proposition facts
claim_ref / claim_digest / review_status
active clarification gaps
empty_reason
```

### 4.3 语义边界

问诊投影必须原样透传 proposition：

```text
英短精神状态正常
英短没有呕吐
英短好像没有呕吐
```

M13 不输出：

```text
normal
denied
uncertain
unobserved
corrected
medical risk
followup_required
```

极性、确定性与观察状态的语义解释属于问诊领域自己的 candidate-only 语义
契约；不得由 M13 用关键词、正则、embedding 或医学规则推断。

`answer_now` 独立透传。它不是跳过 gap 的指令；是否带 gap 输出阶段性回答、
是否追问，由问诊回答充分性与 followup 策略决定。

### 4.4 空 graph

当 M12 在显式策略下输出合法空图时，问诊投影保留：

```text
facts = ()
empty_reason = no_explicit_fact
```

空图不会被伪装成失败，也不会被降级为空事实列表。

## 5. 临床安全 TODO Adapter

当前临床安全输入契约尚未明确，因此 M13 提供 TODO 空壳：

```text
TODOClinicalSafetyProjectionAdapter
```

调用结果固定为：

```text
execution_state = rejected
failure_code = adapter_not_implemented
```

TODO 空壳不做：

```text
临床安全 schema 猜测
urgent / blocked signal 生成
临床安全召回
required_context 评估
OPA 调用
诊断或安全动作
```

后续临床安全领域契约明确后，才允许替换为生产 adapter。

## 6. 长期记忆 TODO Adapter

当前长期记忆候选策略尚未明确，因此 M13 提供 TODO 空壳：

```text
TODOLongTermMemoryProjectionAdapter
```

未来生产 adapter 只允许消费：

```text
graph_verified
```

不得消费：

```text
graph_partial_with_gaps
graph_disagreement
graph_blocked
```

长期记忆投影只输出候选，不直接写入：

```text
长期权威事实
Mem0
任何记忆投影存储
```

### 6.4 M11 auto-finalization 的透明性

M11 从独立 Evidence Binding 改为 deterministic auto-finalization 时，M13 契约
不变。M13 不读取、不解释、不迁移 M11 gate，也不感知 `verified` /
`clarification_required` 的产生方式。

M13 只依赖 M12 的稳定终态：

```text
graph_verified
graph_partial_with_gaps
```

后续 M15 也不得以 M11 auto-finalization 为理由绕过 M12。

## 7. 失败码

M13 当前稳定失败码：

```text
graph_missing
terminal_state_not_allowed
graph_terminal_state_mismatch
gap_shape_mismatch
projection_contract_mismatch
adapter_not_implemented
```

失败行为：

```text
blocked / disagreement → terminal_state_not_allowed
graph 缺失 → graph_missing
graph 与 build result 状态不一致 → graph_terminal_state_mismatch
partial 缺 active gap 或 verified 带 gap → gap_shape_mismatch
空图缺结构化 empty reason → projection_contract_mismatch
领域契约未就绪 → adapter_not_implemented
```

## 8. 持久化与职责边界

M13 本阶段不新增数据库表，也不新增仓储方法。

原因：

```text
问诊状态由问诊领域写入
临床安全状态由既有临床安全链路写入
长期记忆由独立记忆领域裁决并写入
```

M13 投影结果可以按需由 M14 记录 trace，但 trace 不是权威事实源。若未来出现
查询压力，可以增加可重建派生索引；该索引不得承担语义判断或替代 M11/M12。

## 9. 防退化约束

后续实现不得引入：

```text
M13 直接读取 M11 / M08 / M09 / M10 / M06 / 原始用户文本
M13 调用 LLM
M13 用关键词、正则或 embedding 解释 proposition
M13 把 reported normal 映射为 denied
M13 把 unknown 解释为追问已完成
M13 把 clarification gap 转成强制追问
M13 做医学风险或诊断判断
M13 写问诊状态
M13 调用临床安全 evaluator / OPA / required_context
M13 写 Mem0 或长期事实
M13 把失败转为空 facts / 空候选
M15 绕过 M12 graph result 消费 M11
```

## 10. 验收口径

本阶段验收已覆盖：

```text
verified graph 问诊投影保留 proposition 原文与来源身份
partial graph 问诊投影保留 active gap
gap 不生成 followup 决策
answer_now / correction / clarification_request 独立透传
no_explicit_fact 空 graph 显式保留 empty reason
blocked graph 显式 rejected
adapter 契约禁止声明 blocked / disagreement
临床安全 TODO adapter 显式 adapter_not_implemented
长期记忆 TODO adapter 显式 adapter_not_implemented
rejected 结果不携带领域负载
```

## 11. 关联材料

1. [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
2. [semantic-collaboration-dag-production-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-implementation-plan.md)
3. [semantic-collaboration-dag-m12-claim-graph-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m12-claim-graph-change-summary.md)
4. [semantic-collaboration-dag-m11-artifact-store-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m11-artifact-store-change-summary.md)
5. [semantic-collaboration-dag-m11-auto-finalization-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m11-auto-finalization-change-summary.md)
6. [consultation-dynamic-evidence-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-dynamic-evidence-architecture.md)

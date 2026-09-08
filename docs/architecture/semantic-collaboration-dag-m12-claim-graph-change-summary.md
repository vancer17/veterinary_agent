<!--
=============================================================================
文件: semantic-collaboration-dag-m12-claim-graph-change-summary.md
作用: 定义受限语义协作 DAG M12 Claim Graph 与结构一致性门禁的生产对接基线。
范围: 覆盖 M12 输入输出契约、结构终态语义、M13/M15 消费边界、空图语义、
      有意 TODO、防退化约束与验收口径。
说明: M12 是 M11 回合级权威 artifact 的确定性结构投影，不是第二权威事实源；
      本文不展开软件包内部实现、私有流程、数据库结构或测试替身细节。
维护: 当 graph 输入契约、终态语义、下游消费策略、TODO 责任边界或生产接入
      前提调整时，必须同步更新本文和生产架构基线。
=============================================================================
-->

# 受限语义协作 DAG M12 Claim Graph 生产对接基线

> **文档状态**：M12 生产工程边界已实现；M13 问诊投影已实现，临床安全与
> 长期记忆仍为 TODO 空壳；待 M04 执行器组合与 M15 生产接入消费
>
> **2026-09 M11 状态机修订**：M11 auto-finalization 目标契约已决策；M12 需同步
> 调整 partial gate 消费矩阵；该消费策略已实现，详见
> [semantic-collaboration-dag-m11-auto-finalization-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m11-auto-finalization-change-summary.md)。
>
> **文档定位**：本文面向后续 M13 / M15 实现者，描述可依赖的稳定边界和有意
> 未完成项；不作为软件包内部实现说明。

## 1. 核心结论

M12 的职责是：

```text
读取 M11 当前有效回合级 artifact
→ 投影为领域无关的 scoped claim graph
→ 执行结构、身份、引用、版本、状态机和消费策略门禁
→ 输出 graph 终态
```

M12 不是：

```text
语义抽取器
语义审查器
医学知识图谱
修复器
追问生成器
领域投影器
权威事实数据库
```

M12 的“一致性”只表示：

```text
结构一致
身份一致
引用一致
版本一致
状态机一致
消费策略一致
```

不表示：

```text
语义一致
医学正确
语义无重复
语义无冲突
```

---

## 2. 当前实现状态

| 能力 | 状态 | 对接含义 |
|---|---|---|
| M11 Turn Intent section 消费 | 已实现 | M12 从 M11 权威 payload 读取 intent，不回读 M06 |
| 回合级 graph 投影 | 已实现 | M13 可消费 graph contract，而非 M11 内部 payload |
| scoped claim / gap 引用 | 已实现 | 引用绑定 artifact version，不跨版本迁移旧审查 |
| claim / gap / repair / gate 节点 | 已实现 | 下游无需理解 M08/M09/M10 内部结构 |
| artifact 当前性与 stale 校验 | 已实现 | M12 不消费旧版本 |
| 结构一致性门禁 | 已实现 | 只做结构判断，不做语义判断 |
| graph canonical digest / graph id | 已实现 | 相同输入与策略可确定性重建 |
| graph 终态 | 已实现 | verified / partial / disagreement / blocked |
| M11 auto-finalization 消费矩阵 | 已实现 | clarification_required 按策略分类为 partial graph |
| graph 权威持久化 | 有意未实现 | graph 是可重建投影，不是第二权威事实源 |
| M13 问诊投影 | 已实现 | 问诊领域消费 graph contract，不读取 M11 内部 payload |
| M13 临床安全 / 长期记忆投影 | TODO 空壳 | 对应领域契约明确前显式失败 |
| M04 执行器组合 | 未实现 | M12 尚未进入生产任务执行链 |
| M15 VetOrchestrator 接入 | 未实现 | 生产主路径尚未切换 |

---

## 3. 输入契约

M12 的唯一权威输入是：

```text
M11 current semantic_turn_result artifact
+ M11 current gate event
+ graph build policy
```

M11 artifact 必须包含：

```text
turn_intent
claims
review_bundle
repair_lineage
repair_depth
run_id
turn_snapshot_digest
source proposal identity
payload digest
```

### 3.1 Turn Intent 前提

Turn Intent 必须已经作为 M11 回合 artifact 的权威 section 保存。

M12 缺失 Turn Intent 时输出：

```text
graph_blocked
failure_code=turn_intent_missing
```

禁止：

```text
回读 M06 proposal
读取原始用户文本
读取 scheduler projection
根据 claim 反推 intent
```

### 3.2 当前版本前提

M12 只消费：

```text
当前 latest artifact version
```

且该版本不得存在 stale marker。

如果输入旧版本：

```text
graph_blocked
failure_code=artifact_stale
```

M12 不自动改用最新版本。

---

## 4. 输出契约

M12 输出一个 graph 构建结果：

```text
terminal_state
graph
failure_code
failure_message
```

### 4.1 可携带 graph 的终态

以下终态可以携带 graph：

```text
graph_verified
graph_partial_with_gaps
graph_disagreement
```

其中：

```text
graph_disagreement
```

仅用于继承上游 disagreement 或显式结构 contract mismatch。

### 4.2 不携带 graph 的终态

```text
graph_blocked
```

blocked 结果不携带可消费 graph。

下游不得把 blocked result 中的 `failure_message` 当作用户事实、追问建议或
领域结论。

---

## 5. Graph 身份与引用

M12 graph 是可确定性重建的投影。

graph 身份由以下输入派生：

```text
artifact id
artifact version
current gate sequence
graph contract version
build policy
```

graph digest 由 canonical graph payload 派生。

因此：

```text
同一 M11 artifact
+ 同一 current gate
+ 同一 graph contract
+ 同一 policy
→ 同一 graph id / digest
```

### 5.1 scoped claim 引用

Claim 引用绑定 artifact version：

```text
semantic-claim://<artifact_id>/<version>/<claim_index>/<claim_digest>
```

这意味着：

```text
v1 claim 0
v2 claim 0
v3 claim 0
```

是不同 scoped identity。

即使某个 proposition 在修复前后文本相同，也不能默认继承旧版本审查结论。

### 5.2 gap 引用

Clarification gap 引用当前版本中的 claim digest 与 claim reference。

gap 语义保持：

```text
不是 verified
不是 failure
不是 unknown fact
不是自动追问指令
```

是否追问由问诊领域决定。

---

## 6. 结构门禁范围

M12 检查：

```text
artifact reference 格式
artifact id / version
payload digest
artifact kind
artifact latest version
artifact stale marker
turn intent section
claim index 连续性
claim digest 与 proposition 一致性
claim digest / proposition 完全重复
Faithfulness review 对全部 claims 的结构覆盖
review record 与 claim digest / proposition 一致性
gap claim index / digest 引用
repair depth 与 repair lineage 一致性
repair base version 与 result version 关系
gate event sequence
current gate 状态
graph 内 scoped 引用闭合
消费策略是否允许 empty / partial graph
```

### 6.1 结构重复的唯一定义

以下视为结构重复：

```text
claim digest 相同
或 claim proposition 完全相同
```

以下不属于 M12 判断范围：

```text
英短没有呕吐
英短未呕吐
```

二者是否语义等价，必须由上游语义审查或未来独立的图级语义审查扩展判断；
M12 不得使用关键词、正则、编辑距离、embedding 或本地词典推断。

---

## 7. 不做的事项

M12 明确不做：

```text
claim 语义忠实性判断
claim 医学正确性判断
语义重复判断
语义冲突判断
intent 与 claim 的自然语言语义关系判断
gap 是否应该追问
gap 是否可通过上下文消解
修复后 proposition 语言质量判断
诊断 / 风险 / 治疗 / 用药判断
用户追问文案生成
问诊状态写入
临床安全 evaluator / OPA / required_context 调用
长期记忆写入
```

如果未来确实需要图级语义审查，必须显式立项为：

```text
上游语义审查扩展
或独立 graph semantic reviewer
```

并满足：

```text
固定布尔矩阵
独立输入输出契约
结果进入 M08 / M11 权威链路
不直接修改 graph
M12 只消费其结构化结果
```

不得把该能力合并进 M12 deterministic gate。

---

## 8. Graph 终态语义

### 8.1 `graph_verified`

成立条件：

```text
结构门禁通过
artifact 非 stale 且为当前版本
Turn Intent 存在
review / gap / repair 引用闭合
M11 current gate = verified 或 repair_verified
无 active clarification gap
消费策略允许
```

含义：

```text
结构上可消费
上游语义审查与 M11 auto-finalization 已通过
```

不含义：

```text
M12 重新验证了 claim 语义
```

对于修复后结果，还要求：

```text
repair_depth=1
repair lineage 存在
已完成 M07 re-verify
已完成 M08 re-review
current gate=repair_verified
```

### 8.2 `graph_partial_with_gaps`

成立条件：

```text
结构门禁通过
M11 current gate = clarification_required
存在 active clarification gap
repair lineage / re-review 结构闭合（针对 repaired artifact）
消费策略显式允许 partial graph
```

含义：

```text
图结构可消费
但包含显式信息缺口
```

不含义：

```text
graph_verified
gap 应该自动追问
gap 已消解
下游必须追问
```

该分类不改变 M11 gate。M12 不得把 `clarification_required` 写成或解释为
`verified`。

### 8.3 `graph_disagreement`

只来自：

```text
上游 M08 / M11 disagreement
```

或显式结构权威字段之间的 contract mismatch。

M12 不自行发现语义冲突。

该状态保留冲突证据，不自动裁决。

### 8.4 `graph_blocked`

用于结构、身份、引用、版本、状态机或消费策略失败。

典型失败包括：

```text
artifact_missing
artifact_stale
payload_digest_mismatch
turn_intent_missing
claim_index_invalid
claim_digest_mismatch
duplicate_claim
review_bundle_missing
review_coverage_incomplete
review_claim_mismatch
gap_reference_invalid
repair_lineage_invalid
repair_verification_pending
evidence_binding_pending
gate_missing
gate_sequence_invalid
empty_graph_not_allowed
partial_graph_not_allowed
structure_invalid
```

---

## 9. 空 graph 语义

`claims=[]` 不自动等于错误。

M12 只继承 M08 Coverage Review 的结构化派生结果：

### 9.1 合法空图

当上游结构化结果为：

```text
no_explicit_fact
```

且策略允许空图：

```text
graph_verified
claim_count=0
empty_reason=no_explicit_fact
```

### 9.2 可疑空图

当上游结构化结果为：

```text
suspicious_empty
```

M12 输出：

```text
graph_blocked
failure_code=suspicious_empty
```

禁止：

```text
M12 读取原始用户文本重新判断是否漏抽
```

---

## 10. M11 与 M12 的权威关系

```text
M11 = 权威回合级 artifact / gate / lineage / stale
M12 = 可重建结构投影
```

因此 M12：

```text
不写数据库
不修改 M11 payload
不生成第二权威 artifact
不追加 M11 gate event
不承担 M11 版本仲裁
```

如果未来查询或统计压力变大，可以增加：

```text
semantic_claim_graph 派生索引
```

但必须满足：

```text
可删除重建
不是权威事实源
不承担语义判断
不替代 M11 artifact
```

---

## 11. M13 消费边界

M13 只能消费：

```text
M12 graph build result
```

不得直接读取：

```text
M11 payload
M08 matrix
M09 repair plan
M10 patch set
原始用户文本
scheduler projection
```

### 11.1 问诊投影 Adapter

可消费：

```text
graph_verified
graph_partial_with_gaps
```

可使用：

```text
claim propositions
active clarification gaps
turn intent.answer_now
```

不得：

```text
把 partial graph 当作 verified graph
用关键词解释 proposition
自动把 gap 等同于追问
直接写问诊状态
```

是否追问由问诊回答充分性 / followup 策略决定。

### 11.2 临床安全投影 Adapter

只做结构化事实投影。

不得：

```text
判断疾病风险
产生 urgent / blocked
调用临床安全召回
调用 OPA
做诊断或安全动作
```

临床安全动作仍由既有临床安全领域负责。

### 11.3 长期记忆投影 Adapter

只消费：

```text
graph_verified
```

不得消费：

```text
graph_partial_with_gaps
graph_disagreement
graph_blocked
```

长期记忆投影只输出候选，不直接写入长期权威事实或 Mem0。

---

## 12. M15 生产接入边界

M15 不得绕过 M12 graph result 直接消费 M11。

生产接入前必须显式定义：

```text
同步等待 graph build
异步启动后轮询
异步回调
失败映射
超时策略
回滚配置
metadata 字段
```

禁止：

```text
graph blocked 后回退旧问诊抽取器
把 partial graph 伪装为 completed
吞掉 graph failure
把 failure_message 展示给用户
```

---

## 13. 有意 TODO

| TODO | 当前行为 | 后续责任 | 启用条件 |
|---|---|---|---|
| M11 auto-finalization | 已实现；新提交自动 verified / repair_verified | 已关闭 | 历史 pending 仅兼容读取 |
| M04 SemanticTaskExecutor 组合 | M12 未进入生产任务执行链 | M04 | M06～M11 消费时序闭合 |
| M13 临床安全投影 | TODO 空壳 | M13 | 临床安全输入契约明确 |
| M13 长期记忆候选投影 | TODO 空壳 | M13 / 记忆领域 | 长期记忆候选策略明确 |
| Graph 派生索引 | 不持久化 graph | M12 后续 / M14 | 查询或统计压力明确 |
| Graph semantic reviewer | 不实现 | 未来上游语义审查扩展 | 有系统性语义冲突样本和独立契约 |
| M15 VetOrchestrator 接入 | 未接入 | M15 | M13 与生产失败映射就绪 |
| 真实 PostgreSQL / Temporal / LiteLLM 端到端联调 | 未执行 | 集成验证 | M13 / M15 接入边界就绪 |

这些 TODO 是职责边界，不是允许绕过的错误。

---

## 14. 防退化约束

后续实现不得引入：

```text
M12 调用 LLM
M12 读取原始用户文本
M12 使用关键词 / 正则 / embedding 判断语义重复或冲突
M12 做医学判断
M12 生成用户追问
M12 写问诊状态
M12 调用临床安全 evaluator / OPA / required_context
M12 写长期记忆
M12 把 semantic_review_supported 提升为 graph_verified
M12 把 evidence_binding_pending 伪装为 verified
M12 消费 stale artifact
M12 迁移旧 claim review 到新 claim
M13 直接解析 M11 内部 payload
M15 绕过 M12 graph result
```

---

## 15. 验收口径

M12 对接 M13 / M15 前至少满足：

```text
Turn Intent 缺失 blocked
旧版本 artifact blocked
payload digest 漂移 blocked
历史 evidence_binding_pending 不产生 graph_verified
verified gate 产生 graph_verified
clarification_required + active gap 在显式策略下产生 graph_partial_with_gaps
M12 不提升或改写 M11 gate
上游 disagreement 继承为 graph_disagreement
patch 后未 re-review blocked
repair 后 re-review + repair_verified 可消费
完全相同 claim digest / proposition blocked
语义相似但文本不同的 claims 不因语义重复 blocked
suspicious_empty blocked
no_explicit_fact 在策略允许下可输出合法空图
M13 不直接读取 M11 / M08 / M09 / M10
问诊 / 临床安全 / 长期记忆状态均未被写入
```

---

## 16. 文档同步触发

以下变化必须同步更新本文：

```text
M11 turn artifact payload 调整
graph terminal state 调整
graph build policy 调整
M13 消费契约调整
M15 接入模式调整
graph 持久化策略调整
未来图级语义审查立项
```

本文与生产架构基线冲突时，以生产架构基线为准；实现不得形成第二权威解释。

## 17. 关联材料

1. [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
2. [semantic-collaboration-dag-production-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-implementation-plan.md)
3. [semantic-collaboration-dag-m11-artifact-store-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m11-artifact-store-change-summary.md)
4. [semantic-collaboration-dag-m13-domain-projection-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m13-domain-projection-change-summary.md)
5. [semantic-collaboration-dag-m11-auto-finalization-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m11-auto-finalization-change-summary.md)

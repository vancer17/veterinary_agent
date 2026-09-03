<!--
=============================================================================
文件: semantic-collaboration-dag-m08-review-skill-change-summary.md
作用: 总结受限语义协作 DAG M08 Coverage / Faithfulness Review 的生产实现边界。
范围: 覆盖 Review Skill 目录、标准化 SKILL 文档、固定布尔矩阵、M05 结构化
      模型网关、确定性验证与结果派生、clarification gap、动态审查任务边界、
      M11 artifact TODO 与 M04 / M09 / M10 / M12 后续对接契约。
说明: 本文只记录跨模块可依赖的稳定行为、状态和有意边界；不展开软件包内部
      类、函数、SQL DDL、测试替身、prompt 全文或 Temporal workflow 细节。
维护: 当 Review 矩阵字段、任务展开策略、路由优先级、clarification gap、
      artifact 对接或生产联调状态调整时，必须同步更新本文。
=============================================================================
-->

# 受限语义协作 DAG M08 Review SKILL 变更总结

> **文档状态**：M08 生产工程边界已实现；待 M04 任务执行器组合、M09 / M10
> 修复链路、M11 Artifact Store 与真实外部服务联调后关闭

## 1. 当前状态

| 项目 | 当前状态 |
|---|---|
| Coverage Review Skill 契约与标准化文档 | 已实现 |
| Faithfulness Review Skill 契约与标准化文档 | 已实现 |
| Coverage 固定布尔矩阵 | 已实现 |
| Faithfulness 固定中文布尔矩阵 | 已实现 |
| M05 StructuredLLMGateway 接入 | 已实现 |
| Review 结构与身份验证 | 已实现 |
| deterministic outcome derivation | 已实现 |
| clarification gap proposal | 已实现 |
| `no_explicit_fact` / `suspicious_empty` 区分 | 已实现 |
| Coverage / Faithfulness disagreement 保留 | 已实现 |
| M11 Review artifact 权威提交 | TODO 显式空壳 |
| M04 SemanticTaskExecutor 生产组合 | 未接入 |
| M09 Repair Planner | 未实现 |
| M10 typed patch / applier | 未实现 |
| 真实 LiteLLM 冒烟 | 未执行 |
| 真实 Temporal workflow 联调 | 未执行 |
| VetOrchestrator 生产接入 | 未接入 |

当前可以确认的是：M08 已经能够在 M07 结构验证通过后，对 Claim Inventory 执行
回合级覆盖审查和逐条 proposition 忠实性审查，并用确定性规则输出路由状态。

当前不能宣称的是：

```text
端到端 verified claim graph 已完成
M08 结果已经是权威 artifact
repair 链路已完成
真实 LiteLLM 质量已验证
真实 Temporal workflow 可恢复性已验证
生产主路径已切换
```

## 2. 职责边界

M08 位于：

```text
M06 Claim Inventory
→ M07 structural verifier
→ M08 Coverage / Faithfulness Review
→ M09 Repair Planner
→ M10 typed patch
→ M11 Artifact Store
→ M12 Claim Graph
```

M08 负责：

```text
发现漏抽、合并、多抽、重复、不支持和非自包含 claim
逐条发现主体、否定、时间、数量、程度、确定性、因果和医学越权漂移
区分模型漂移、模型越权和来源绑定缺失
输出固定布尔矩阵
输出确定性 review outcome
生成结构化 clarification gap proposal
保留 bounded missing claim hint
```

M08 不负责：

```text
生成 corrected proposition
直接修改 claims
直接追加 missing_claim_candidates
规划 repair task
应用 typed patch
提交权威 artifact
组装 claim graph
生成用户追问文案
写问诊状态
调用临床安全链路
写长期记忆
```

## 3. Skill 目录与执行家族

旧的单体 `semantic_review` 占位契约已移除，替换为两个正交生产 SKILL：

```text
claim_coverage_review
claim_faithfulness_review
```

两者均为：

```text
execution_family = structured_review
```

语义：

1. Review 矩阵由模型通过 M05 StructuredLLMGateway 生成。
2. 模型不输出业务 verdict、reason、confidence 或 corrected value。
3. Review 输出先经过 strict schema 与身份验证。
4. 业务结论由确定性规则从布尔矩阵派生。
5. `semantic_review_supported` 不是 `verified`。

## 4. 输入边界

### 4.1 Coverage Review 输入

```text
current_turn
必要的有界授权上下文
generated claims
```

Coverage Review 是 turn 级任务，一次审查整个 Claim Inventory。

### 4.2 Faithfulness Review 输入

```text
current_turn
必要的有界授权上下文
单条 claim proposition
```

Faithfulness Review 是 claim 级任务，一次只审查一条 proposition。

### 4.3 Reviewer 不可见信息

Reviewer 与 Generator 绑定同一 TurnSnapshot digest，但 Reviewer 不读取：

```text
生成器 prompt
生成器 reason
生成器 confidence
生成器模型 metadata
run_id
task_id
claim_id / claim_index
snapshot digest
其他 claim
未验证同伴任务输出
问诊状态
临床安全评估 / 召回 / required_context / OPA
长期记忆
```

工程身份只由系统附加，用于任务追踪和身份闭合，不进入模型 prompt。

## 5. 输出契约

### 5.1 Coverage Review 输出

```json
{
  "coverage_matrix": {
    "存在漏抽显式事实": false,
    "存在多事实合并": false,
    "存在重复claim": false,
    "存在原文不支持的claim": false,
    "存在非自包含proposition": false,
    "存在shared scope拆分错误": false,
    "未分类覆盖问题": false
  },
  "missing_claim_candidates": []
}
```

约束：

```text
coverage_matrix 内全部字段 required boolean
根对象与矩阵对象 additionalProperties=false
missing_claim_candidates 有界、单行、去重
missing_claim_candidates 只是 repair hint 或人工审查线索
```

禁止：

```text
直接追加 candidate 为权威 claim
把 candidate 当作 verified proposition
把 candidate 当作 M08 已修复结果
```

### 5.2 Faithfulness Review 输出

```json
{
  "faithfulness_matrix": {
    "主体或指代范围改变": false,
    "否定方向改变": false,
    "否定范围改变": false,
    "正常状态误写为否认": false,
    "事实类型改变": false,
    "时间范围改变": false,
    "频率或数量改变": false,
    "程度或强度改变": false,
    "确定性改变": false,
    "因果关系改变": false,
    "医学推断或建议添加": false,
    "命题不自包含": false,
    "指代对象不明": false,
    "时间基准不明": false,
    "否定范围不明": false,
    "比较基线不明": false,
    "未分类语义改变": false
  }
}
```

约束：

```text
faithfulness_matrix 内全部字段 required boolean
根对象与矩阵对象 additionalProperties=false
不输出 verdict / reason / confidence
不输出 corrected_proposition
不输出 evidence / assertion_state / entity_id / canonical_id
不输出诊断、风险或建议
```

## 6. 执行与任务展开边界

M08 只在 M07 结构验证通过后执行。

初始 Root Plan 仍只包含：

```text
turn_intent
claim_inventory
```

Review 任务由确定性规则在 M07 accepted Claim Inventory 后展开，不由模型选择任务，
也不修改初始 Plan Policy。

生产执行顺序：

```text
1. 读取并验证同一 TurnSnapshot digest
2. 执行 Coverage Review
3. 验证 Coverage 矩阵并派生回合级 outcome
4. 按确定性策略执行逐条 Faithfulness Review
5. 验证每条矩阵并派生 claim 级 outcome
6. 聚合成 review bundle
```

Coverage-first 策略：

```text
Coverage supported
→ 执行全部 claim 的 Faithfulness Review

Coverage 发现 claim 级问题
→ 执行 Faithfulness Review 以定位具体 claim

Coverage 只发现 inventory 级修复问题
→ 可显式跳过即将失效的 claim 级审查，并保留 skip reason

Coverage blocked / human review required
→ 不执行 Faithfulness Review，并保留稳定 skipped 状态
```

Skipped 不是审查通过。后续 M11 / M12 不得把 skipped record 当作 supported
 record 消费。

## 7. 确定性结果派生

M08 输出以下业务路由状态：

```text
semantic_review_supported
repair_required
clarification_required
repair_then_clarification_required
human_review_required
disagreement
review_failed
```

### 7.1 派生规则

```text
全部 false
→ semantic_review_supported

仅来源绑定缺失
→ clarification_required

模型漂移 / 模型越权
→ repair_required

模型漂移 / 越权 + 来源绑定缺失
→ repair_then_clarification_required

未分类维度 true
→ human_review_required

可修复 true 维度超过预算
→ human_review_required

Coverage 与 Faithfulness 冲突
→ disagreement

Review schema / 身份验证失败
→ review_failed
```

当前默认修复维度预算：

```text
max_repair_dimensions = 2
```

超过预算时禁止自动全局重写，必须进入人工审查。

### 7.2 来源绑定缺失维度

```text
指代对象不明
时间基准不明
否定范围不明
比较基线不明
```

这些不是模型可以靠猜测修复的漂移，必须进入 clarification 或
repair-then-clarification 路由。

### 7.3 医学推断或建议添加

`医学推断或建议添加` 属于模型越权类。

后续修复只能：

```text
删除模型添加的医学推断 / 风险 / 建议
或还原为用户明确表达
```

禁止：

```text
评估医学建议是否正确
在 Python 中编写医学规则
让 Repair SKILL 扩展医学内容
```

## 8. 空 Claim Inventory

M08 显式区分：

```text
no_explicit_fact
suspicious_empty
```

### 8.1 无显式事实

```text
用户当前回合没有显式事实
claims=[]
Coverage 全 false
→ no_explicit_fact
→ semantic_review_supported
```

### 8.2 可疑空结果

```text
用户当前回合存在显式事实
claims=[]
Coverage 标记存在漏抽显式事实
→ suspicious_empty
→ repair_required
```

missing claim candidates 会保留给 M09 或人工审查，但 M08 不补造、不追加、
不直接修复 claims。

## 9. Clarification gap

来源绑定缺失会生成结构化 gap proposal：

```text
subject_reference
temporal_basis
negation_scope
comparison_baseline
```

gap 至少保留：

```text
claim proposition
ambiguous dimension
required binding type
turn snapshot digest
source proposal digest
claim digest
model_overreach_repaired
```

Clarification gap 语义：

```text
不是 verified
不是 failure
不是 unknown fact
不是自动追问指令
不是用户可见文案
```

是否追问、是否阶段性回答，由问诊领域结合 answer_now、安全状态和回答充分性策略
决定。

## 10. Disagreement

以下冲突必须保留 `disagreement`，不得默认任一方正确：

```text
Coverage 认为存在原文不支持的 claim
但全部相关 Faithfulness Review 均 supported

Coverage 输出 missing claim candidates
但 Coverage 矩阵未标记存在漏抽显式事实

其他无法稳定映射到已知路由的矩阵组合
```

disagreement 时必须保留：

```text
原 claim inventory
coverage matrix
faithfulness matrices
missing hints
模型调用 metadata
review bundle digest
```

## 11. 有意 TODO

| TODO | 当前行为 | 后续责任 | 启用条件 |
|---|---|---|---|
| M09 Repair Planner | M08 只输出 true dimensions 和路由状态 | M09 | 定义 dimension → 白名单 repair task 映射和预算 |
| M10 Repair / Patch | M08 不输出 corrected proposition | M10 | 定义 typed patch、base version、patch verifier 和 applier |
| M11 Review Artifact Store | 显式 Fail Fast，不返回 artifact reference | M11 | 定义 append-only、版本、lineage、stale 和幂等提交 |
| M04 SemanticTaskExecutor 组合 | M08 Runner 未进入生产任务端口 | M04 | M06 / M07 / M08 / M11 消费时序与终态闭合 |
| Clarification gap artifact | 仅存在结构化 proposal，无权威持久化 | M11 | clarification gap 随 review bundle 进入 artifact 状态机 |
| Claim envelope allocation | M08 后仍不能分配最终 claim envelope | M08 / M09 / M12 | claim 集合经过 coverage、faithfulness 与 repair 后稳定 |
| 真实 LiteLLM 冒烟 | 未执行 | 集成验证 | 显式外部模型网关环境与模型 snapshot |
| 真实 Temporal workflow 联调 | 未执行 | M04 / M15 | Temporal server、worker、task queue 和投影就绪 |
| VetOrchestrator 接入 | 未接入 | M15 | 显式配置切换、超时、回滚和观测边界就绪 |

这些 TODO 是模块边界，不是允许绕过的错误。

## 12. 后续模块对接契约

### 12.1 M09 消费 M08

M09 可消费：

```text
coverage true dimensions
per-claim faithfulness true dimensions
repair_required route
repair_then_clarification_required route
bounded missing_claim_candidates
claim index / claim digest
```

M09 不得消费：

```text
模型原始自由文本
review verdict / reason / confidence
corrected proposition
未验证 missing candidate
```

### 12.2 M10 消费 M09

M10 只能根据 M09 的白名单任务生成局部 typed patch。

M08 不向 M10 提供 corrected value。

### 12.3 M11 消费 M08

M11 应将 review bundle 作为 append-only artifact 提交，并保留：

```text
coverage matrix
per-claim faithfulness matrix
verification state
derived outcome
clarification gaps
model metadata
source proposal digest
turn snapshot digest
claim digest
version / lineage / stale
```

在 M11 未提交前，M08 结果不能进入 M12 Claim Graph。

### 12.4 M12 消费 M11

M12 只能消费 M11 后的完整门禁 artifact。

禁止消费：

```text
M05 proposal
M07 structural accepted proposal
M08 内存 review bundle
clarification gap 伪装的 verified fact
```

### 12.5 M04 组合 M08

M04 的生产任务执行端口后续应组合：

```text
M06 generation
→ M07 verifier
→ M08 review
→ M11 artifact commit
```

M04 不解释矩阵语义，不生成 repair，不决定领域投影。

## 13. 失败与 Fail Fast 边界

以下情况必须显式失败或进入稳定终态：

```text
TurnSnapshot digest mismatch
review task 身份不一致
output schema digest 不一致
claim index / claim digest 不一致
矩阵 extra field
矩阵 missing field
非 boolean 字段
missing candidate 多行 / 重复 / 超界
模型调用失败
finish reason 异常
模型快照漂移
usage metadata 不一致
```

禁止：

```text
清洗 extra field 后放行
宽松文本 JSON 检索
失败转空矩阵
失败后默认 review supported
隐藏 fallback 模型
旧问诊语义抽取器回退
本地默认矩阵补位
```

## 14. 安全与领域隔离

M08 保持：

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_retrieval_called = false
clinical_safety_opa_called = false
required_context_called = false
long_term_memory_written = false
```

M08 不做：

```text
诊断
疾病风险判断
急诊风险判断
urgent / blocked 安全信号
就医、治疗、用药或护理建议
```

## 15. 可观测性边界

后续 M14 应为每次 Coverage / Faithfulness 调用记录：

```text
run id
source task id
source attempt
review skill id / version
turn snapshot digest
source proposal digest
claim index / claim digest
model snapshot
response id
prompt hash
schema digest
attempt number
latency
token usage
finish reason
verification state
derived outcome
true dimensions
failure code
```

不得记录：

```text
完整用户原文
完整 prompt
API key
模型原始自由文本
下游领域状态
```

建议指标：

```text
coverage_supported_rate
coverage_repair_required_rate
suspicious_empty_rate
faithfulness_supported_rate
faithfulness_repair_required_rate
clarification_required_rate
repair_then_clarification_rate
human_review_required_rate
review_failed_rate
review_disagreement_rate
review_schema_invalid_rate
review_model_failure_rate
review_p50_latency_ms
review_p95_latency_ms
claims_per_review
review_token_count
```

## 16. 验证状态

本地验证结果：

```text
ruff check semantic_collaboration + M08 tests: PASS
mypy src/vet_agent/semantic_collaboration: PASS
pytest tests/test_semantic_collaboration_*.py: 79 passed
pytest 全量默认测试: 297 passed, 43 skipped
```

覆盖：

```text
M08 配置闭合
Coverage / Faithfulness 结构化调用
Reviewer 不读取工程身份
全部 false → semantic_review_supported
模型漂移 → repair_required
来源绑定缺失 → clarification_required
漂移 + 来源绑定缺失 → repair_then_clarification_required
未分类问题 → human_review_required
suspicious empty
missing candidates 不直接追加
Coverage / Faithfulness disagreement
Coverage 内部 contradiction
extra / non-boolean schema 负例
非法 Coverage schema Fail Fast
M11 TODO Fail Fast
```

上述验证使用进程内测试替身，不等于真实 LiteLLM、Temporal 或生产主路径结论。

## 17. 文档同步触发条件

以下变化必须同步更新本文：

```text
新增或删除矩阵字段
调整 Review 输出 envelope
调整 coverage-first 或 fan-out 策略
调整 outcome 派生优先级
调整 repair dimension budget
调整 clarification gap 字段
接入 M09 / M10 / M11
接入 M04 SemanticTaskExecutor
执行真实 LiteLLM 冒烟
执行真实 Temporal workflow 联调
接入 VetOrchestrator
调整领域隔离或观测边界
```

## 18. 关联材料

1. [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
2. [semantic-collaboration-dag-production-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-implementation-plan.md)
3. [semantic-collaboration-dag-m06-generation-skill-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m06-generation-skill-change-summary.md)
4. [semantic-collaboration-dag-m04-scheduler-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m04-scheduler-change-summary.md)

<!--
=============================================================================
文件: semantic-collaboration-dag-pre-m11-semantic-validation-summary.md
作用: 以语义维度为第一公民，解释受限语义协作 DAG Pre-M11 语义测试切片的
      通过口径、真实观察结果、额外维度现象、修复效果与当前证据边界。
范围: 覆盖 M06 生成语义保真、M08 Coverage / Faithfulness 校准、M09 语义路由、
      M10 局部修复与 test-only post-patch probe 的语义结论。
说明: 本文是测试报告的人类可读语义解释，不覆盖测试设计和 JSON 报告；不宣称
      exact dimension precision、M11 verified artifact 或领域投影可用。
维护: 当语义 fixture、M08 维度、M09 路由、M10 patch 契约或新一轮语义报告变化时，
      必须同步更新本文。
=============================================================================
-->

# 受限语义协作 DAG Pre-M11 语义验证总结

> **文档状态**：Pre-M11 首个语义测试切片的独立语义结论
>
> **证据边界**：本文只解释当前 33 个语义相关 case 的一次真实执行结果；不表示
> 全量语义质量、冷启动稳定性或 M11 闭环已经完成。

## 1. 文档定位

本文是：

```text
Pre-M11 语义验证结果的人类可读解释
语义维度第一公民视角的测试结论摘要
后续语义回归、维度裁定和 M11 闭环测试的基线
```

本文不是：

```text
新的测试设计
新的 JSON 权威报告
生产架构修订
M11 / M12 / M13 完成声明
新的医学规则或关键词规则
```

权威顺序如下：

```text
1. semantic-collaboration-dag-pre-m11-integration-test-design.md
2. semantic-pre-m11-*.json 原始报告
3. 本文语义解释
```

若本文与测试设计或原始报告冲突，以测试设计和原始报告为准，并必须先修正本文。

## 2. 权威报告与执行环境

本轮解释以下报告：

```text
.data/evaluations/semantic-collaboration-integration/
semantic-pre-m11-20260904-110337-5c610651-042.json
```

报告身份：

```text
report_version=semantic-pre-m11-integration-report-v1
run_id=5c610651607f4443ab1aa7d6df98b51f
execution_mode=semantic
sha256=9ee6dd0d01a3602c33be68a94121450e35e07079fe554b28e9ddedf91c42f717
```

真实环境：

```text
LiteLLM model=qwen-plus
requested model snapshot=qwen-plus
Temporal namespace=semantic-collaboration-dev
Temporal task queue=semantic-collaboration-dev
PostgreSQL database=vet_agent
```

整份集成报告结果：

```text
total=42
passed=42
failed=0
skipped=0
```

其中语义相关 case：

```text
GEN  8 个
REV  12 个
PLAN 11 个
FIX   2 个
total=33
passed=33
failed=0
skipped=0
```

## 3. 语义测试口径

### 3.1 语义 oracle 来源

本轮语义测试没有使用：

```text
关键词命中
正则匹配
症状词分支
疾病名分支
医学资产 code
Python 医学规则状态机
```

语义结论来自：

```text
人工审核 fixture
M06 真实结构化输出
M07 deterministic structural verifier
M08 Coverage / Faithfulness 固定布尔矩阵
M08 deterministic outcome derivation
M09 deterministic repair planner
M10 deterministic patch verifier / application preview
test-only post-patch M07 / M08 probe
```

### 3.2 fixture 断言类型

当前语义 fixture 支持以下断言：

| 字段 | 语义 |
|---|---|
| `expected_dimensions` | 列出的 M08 维度都必须出现 |
| `expected_any_dimensions` | 列出的维度中至少一个出现 |
| `forbidden_dimensions` | 列出的维度不得出现 |
| `expected_intent` | Turn Intent 固定布尔字段必须匹配 |
| `minimum_claim_count` | Claim Inventory 至少包含指定数量 proposition |
| `expected_route` | M09 必须路由到指定状态 |
| `coverage_true_dimensions` | 构造固定 M08 Coverage 输入 |
| `faithfulness_true_dimensions` | 构造固定 M08 Faithfulness 输入 |

因此，当前 `REV` case 的主要证明是：

```text
目标语义问题可被发现
禁止出现的错误维度没有出现
```

不是：

```text
M08 输出维度集合与目标集合完全一致
```

### 3.3 重要口径限制

1. `expected_any_dimensions` 允许相邻语义维度命中。
2. 未列入 `forbidden_dimensions` 的额外维度不会导致失败。
3. 额外维度不能直接判定为误报，可能是：
   - 合理谨慎；
   - 维度重叠；
   - 语义相邻；
   - 真正 false positive。
4. 本轮尚未建立逐维度人工裁定表。
5. 本轮是一次完整 semantic run，不是三次冷启动重复稳定性结论。
6. M11 未实现，`patch_ready` 不等于 `repair_verified`。

## 4. 总体语义结论

### 4.1 可以确认

```text
M06 生成语义 8/8 通过
M08 目标语义维度覆盖 12/12 通过
M09 语义路由 11/11 通过
M10 局部修复 2/2 patch_ready
M10 proposition repair 后原 dimension 消失
M10 proposition repair 后未引入新 dimension
所有语义 case 均未调用问诊状态、临床安全、Mem0 或长期记忆
所有语义 case 均未使用旧问诊语义抽取器 fallback
所有语义 case 均未读取实验 held-out
```

### 4.2 不能确认

```text
M08 exact dimension precision 达标
M08 全量 recall 达标
语义输出冷启动稳定
语义 corpus 覆盖充分
M10 inventory repair 后语义效果完全闭环
M11 verified artifact 已生成
claim graph ready
问诊 / 临床安全 / 长期记忆投影可消费
```

## 5. M06 生成语义保真结果

### 5.1 汇总

```text
GEN case: 8
passed: 8
failed: 0
M07 accepted: 8/8
semantic_review_supported: 6
clarification_required: 2
repair_required: 0
human_review_required: 0
disagreement: 0
forbidden dimension violation: 0
```

模型调用审计中，进入报告的 M06 Claim Inventory proposal 显示：

```text
requested_model=qwen-plus: 8/8
response_model=qwen-plus: 8/8
finish_reason=stop: 8/8
usage_available=true: 8/8
```

### 5.2 逐 case 结果

| Case | 输入语义 | 通过断言 | 实际 M08 outcome | 实际 true dimensions | 语义结论 |
|---|---|---|---|---|---|
| GEN-001 | 精神正常 | normal 不写成 denied / unknown | `semantic_review_supported` | 无 | normal 语义保真 |
| GEN-002 | 没有呕吐 | denied 不写成 present / unknown | `clarification_required` | `时间基准不明` | 否定方向保真；时间范围需澄清 |
| GEN-003 | 好像没有呕吐 | uncertain 不强化为确定 | `clarification_required` | `时间基准不明` | 确定性保真；时间范围需澄清 |
| GEN-005 | 前天开始换粮 | 时间不漂移 | `semantic_review_supported` | 无 | temporal scope 保真 |
| GEN-006 | 大便有一点软 | 程度不夸大 | `semantic_review_supported` | 无 | 程度语义保真 |
| GEN-007 | 饭和水都正常 | shared scope 拆分且不漏抽 | `semantic_review_supported` | 无 | 两项事实均进入自包含 proposition |
| GEN-008 | 先回答，不要追问 | `answer_now=true` 且不生成医学 claim | `semantic_review_supported` | 无 | 控制意图与 claim 分离 |
| GEN-010 | 多事实复杂输入 | 不漏抽显式事实，claim 数量达到下限 | `semantic_review_supported` | 无 | 显式事实覆盖通过 |

### 5.3 clarification 现象解释

`GEN-002` 与 `GEN-003` 的否定方向和确定性断言通过，但 M08 输出：

```text
时间基准不明
```

并派生：

```text
clarification_required
```

这不是测试失败，原因是：

```text
用户说“没有呕吐”或“好像没有呕吐”，但没有说明该否定覆盖的时间范围。
```

当前系统没有猜测：

```text
一直没有呕吐
今天没有呕吐
近期没有呕吐
```

而是保留 clarification gap。

该现象说明：

```text
否定语义保真通过
系统不补造时间范围
```

但也带来后续产品问题：

```text
普通单轮否定可能被要求补充时间范围
```

后续需要在语义测试和问诊策略中区分：

```text
医学必要 clarification
体验过度的 clarification
```

## 6. M08 Review 校准结果

### 6.1 汇总

```text
REV case: 12
passed: 12
failed: 0
target dimension hit: 12/12
forbidden dimension violation: 0/12
additional observed dimensions: 9/12
```

`additional observed dimensions` 的含义是：

```text
实际输出中出现了 fixture 目标维度之外的额外维度。
```

它不等于误报；本轮尚未逐项人工裁定。

### 6.2 目标维度覆盖

| Case | 校准目标 | 实际观察维度 | 结论 |
|---|---|---|---|
| REV-001 | 漏抽“没有血便” | `存在漏抽显式事实`; `存在shared scope拆分错误` | 目标维度命中 |
| REV-002 | 多事实合并 / shared scope / 漏抽任一 | `存在漏抽显式事实` | expected-any 命中 |
| REV-005 | 非自包含 / 指代不明任一 | `指代对象不明`; `时间基准不明` | expected-any 命中 |
| REV-006 | 复杂输入空 claims 被拦截 | `存在漏抽显式事实` | 可疑空结果被识别 |
| REV-101 | normal 写 unknown 的漂移 | `事实类型改变`; `存在原文不支持的claim`; `存在漏抽显式事实`; `确定性改变` | expected-any 命中 |
| REV-102 | denied 写 present | `否定方向改变`; `存在原文不支持的claim`; `存在漏抽显式事实` | 目标维度命中 |
| REV-103 | 否定范围漂移 / shared scope / unsupported 任一 | `存在shared scope拆分错误` | expected-any 命中 |
| REV-105 | 时间漂移 | `时间范围改变`; `存在原文不支持的claim` | 目标维度命中 |
| REV-107 | 程度漂移 | `程度或强度改变`; `存在原文不支持的claim` | 目标维度命中 |
| REV-109 | 因果 / 医学推断添加 | `因果关系改变`; `医学推断或建议添加`; `存在原文不支持的claim`; `存在漏抽显式事实` | 目标维度命中 |
| REV-110 | 医学建议 / unsupported claim | `医学推断或建议添加`; `存在原文不支持的claim`; `事实类型改变`; `存在漏抽显式事实` | expected-any 命中 |
| REV-111 | 指代不明 / 非自包含任一 | `指代对象不明`; `时间基准不明` | expected-any 命中 |

### 6.3 额外维度现象

按目标维度集合统计，9 个 case 出现了额外观察维度：

```text
REV-001
REV-005
REV-101
REV-102
REV-105
REV-107
REV-109
REV-110
REV-111
```

常见额外维度包括：

```text
存在原文不支持的claim
存在漏抽显式事实
时间基准不明
确定性改变
事实类型改变
```

这些现象有三种可能解释：

1. **合理谨慎**
   - 模型发现了另一个确实存在的问题。
2. **维度重叠**
   - 同一语义漂移被多个相邻维度同时表达。
3. **误报**
   - 额外维度并不能从原文和 claim 关系推出。

当前不能直接将它们归为 false positive，必须进入后续人工裁定。

### 6.4 对当前架构的影响

额外维度目前不会破坏 M09 路由，因为：

```text
coverage 已知问题进入 inventory repair
faithfulness 已知漂移进入 proposition repair
来源绑定缺失进入 clarification
未分类问题进入 human review
```

但如果额外维度持续出现，可能带来：

```text
repair 任务偏多
clarification 过度
review outcome 更保守
下游 M11 artifact gate 更复杂
```

因此在 M11 前 recommended action 是：

```text
建立额外维度人工裁定表
区分 true positive / dimension overlap / false positive
再定义 dimension-level precision
```

## 7. M09 语义路由结果

### 7.1 汇总

```text
PLAN case: 11
passed: 11
failed: 0
repair_required: 7
clarification_required: 3
human_review_required: 1
repair lane assertion violation: 0
```

这些 case 使用固定 M08 矩阵作为输入，因此验证的是：

```text
M09 deterministic routing
```

不验证 M08 模型输出质量。

### 7.2 路由结果

| 语义输入维度 | M09 期望 route | 结果 |
|---|---|---|
| 漏抽显式事实 | `repair_required` + `claim_inventory_repair` | 通过 |
| 多事实合并 | `repair_required` + `claim_inventory_repair` | 通过 |
| shared scope 拆分错误 | `repair_required` + `claim_inventory_repair` | 通过 |
| normal / denied 单 claim 漂移 | `repair_required` + `claim_proposition_repair` | 通过 |
| 否定方向漂移 | `repair_required` + `claim_proposition_repair` | 通过 |
| 时间漂移 | `repair_required` + `claim_proposition_repair` | 通过 |
| 程度漂移 | `repair_required` + `claim_proposition_repair` | 通过 |
| 指代对象不明 | `clarification_required` | 通过 |
| 时间基准不明 | `clarification_required` | 通过 |
| 比较基线不明 | `clarification_required` | 通过 |
| 未分类语义变化 | `human_review_required` | 通过 |

### 7.3 关键语义结论

M09 当前正确保持：

```text
clarification gap 不交给 repair 猜测
human review 不被伪装成已知 repair
coverage 问题进入 turn-level inventory lane
单 claim 漂移进入 proposition lane
```

未出现：

```text
把“它也正常”自动补成宠物主体
把“和之前一样”自动补比较基线
把未分类问题强行路由到通用修复
```

### 7.4 报告观测缺口

测试断言验证了 repair lane，但当前 JSON 报告中的 `PLAN-*` case 未写入
`repair_lane` 字段，只写入了 `repair_route`。

这是测试报告投影缺口，不是 M09 行为缺口。后续应补充：

```text
PLAN case report.repair_lane
PLAN case report.repair_task_count
PLAN case report.expected_lane
```

## 8. M10 语义修复结果

### 8.1 汇总

```text
FIX case: 2
passed: 2
failed: 0
patch_ready: 2
M11 commit: 0
repair_verified: 0
```

### 8.2 FIX-001：normal 误写为 denied

输入场景：

```text
TurnSnapshot: 我家英短精神正常。
Base claim: 我家英短精神状态被否认。
```

M08 固定输入维度：

```text
正常状态误写为否认
```

M09 路由：

```text
repair_required
claim_proposition_repair
```

M10 结果：

```text
patch_state=patch_ready
preview_claim_count=1
```

test-only post-patch probe：

```text
original_dimension_resolved=true
new_dimension_introduced=false
```

语义结论：

```text
normal / denied 漂移可被局部修复
修复后原 true dimension 消失
修复后没有引入新的 M08 dimension
```

### 8.3 FIX-003：漏抽否定事实

输入场景：

```text
TurnSnapshot: 我家英短没有呕吐，也没有血便。
Base claim: 我家英短没有呕吐。
```

M08 固定输入维度：

```text
存在漏抽显式事实
```

M09 路由：

```text
repair_required
claim_inventory_repair
```

M10 结果：

```text
patch_state=patch_ready
preview_claim_count=2
```

语义结论：

```text
漏抽的否定事实可通过稀疏 add claim 补入
base claim 未被完整重写
preview claims 数量从 1 增至 2
```

当前限制：

```text
FIX-003 未执行完整 post-patch M08 probe
```

因此当前只能确认：

```text
patch 语义和 preview 形态有效
```

不能确认：

```text
修复后所有原维度均消失且无新维度
```

后续应补充 FIX-003 post-patch probe，并在报告中记录：

```text
original_dimension_resolved
new_dimension_introduced
```

## 9. 语义能力矩阵

| 语义能力 | 代表 case | 当前结果 | 证据边界 |
|---|---|---|---|
| normal 保真 | GEN-001; REV-101; FIX-001 | 通过 | 覆盖 normal / unknown / denied 漂移与修复 |
| denied 保真 | GEN-002; REV-102; FIX-003 | 通过 | 否定方向不反转，漏抽否定可补 |
| uncertain 保真 | GEN-003 | 通过 | 不强化为确定否认；触发时间 clarification |
| unknown / 未观察不补造 | GEN-004 尚未纳入本轮 | 未覆盖 | 需后续语义 corpus |
| temporal 保真 | GEN-005; REV-105; PLAN-007 | 通过 | 覆盖前天 / 今天漂移 |
| degree 保真 | GEN-006; REV-107; PLAN-008 | 通过 | 覆盖有一点软 / 严重 |
| shared scope | GEN-007; REV-002; PLAN-003 | 通过 | 饭 / 水、多事实合并可拆 |
| answer_now | GEN-008 | 通过 | intent 与 claim 分离 |
| 复杂事实覆盖 | GEN-010; REV-006 | 通过 | 多显式事实与空 claims 可识别 |
| 医学因果越权 | REV-109 | 通过 | 添加因果可发现 |
| 医学建议越权 | REV-110 | 通过 | 添加就医建议可发现 |
| 指代不明 | REV-005; REV-111; PLAN-009 | 通过 | 保留 clarification，不猜主体 |
| 时间基准不明 | GEN-002; GEN-003; PLAN-010 | 通过 | 不猜时间范围 |
| 比较基线不明 | PLAN-011 | 通过 | 不猜 baseline |
| human review | PLAN-012 | 通过 | 未分类问题不自动修复 |
| 修复后回归 | FIX-001 | 通过 | 原 dimension 消失，无新 dimension |
| inventory 修复闭环 | FIX-003 | 部分 | patch ready，但未 post-patch probe |

## 10. 语义可观测性状态

### 10.1 已观测

当前报告可定位：

```text
case_id
turn_snapshot_digest
proposal_digest
m07_state
review_outcome
true_dimensions
repair_route
repair_lane（FIX case）
patch_state
preview_claim_count
post_patch_probe_state
original_dimension_resolved
new_dimension_introduced
```

M06 Claim Inventory 调用可观测：

```text
requested_model
response_model
response_id
finish_reason
latency_ms
prompt_tokens
completion_tokens
total_tokens
usage_available
```

### 10.2 仍缺观测

当前报告尚未完整记录每一次模型调用：

```text
Turn Intent 单独调用 metadata
Coverage Review 单独调用 metadata
每条 Faithfulness Review 单独调用 metadata
Repair SKILL 单独调用 metadata
post-patch probe 各次调用 metadata
```

`PLAN-*` case 也缺少：

```text
repair_lane
repair_task_count
```

这些是测试级 report projection 缺口，不影响当前测试断言，但会影响后续语义质量归因。

M14 完整观测实现前，不应把当前 JSON 报告视为生产 trace 权威。

## 11. 风险与解释边界

### 11.1 expected-any 限制

部分 case 使用 expected-any。它适合当前阶段验证：

```text
问题可发现
路由可收敛
```

但不适合宣称：

```text
维度分类完全精确
```

后续需要 exact-dimension case。

### 11.2 额外维度未裁定

9 / 12 个 REV case 出现额外观察维度。在没有人工裁定前，不能计算可信的：

```text
dimension precision
false positive rate
confusion matrix
```

### 11.3 单次执行

本轮是一次 semantic run。当前不能证明：

```text
三次冷执行一致
同一输入输出稳定
维度不漂移
```

### 11.4 corpus 规模有限

当前 fixture 覆盖高价值语义维度，但样本规模小，不能外推为：

```text
所有普通问诊输入
所有临床安全输入
所有多轮指代场景
所有复杂否定场景
```

### 11.5 M11 未实现

当前修复终点是：

```text
patch_ready
```

不是：

```text
M11 append-only commit
M07 re-verify
M08 re-review
artifact gate update
repair_verified
```

### 11.6 领域隔离仍保持

本轮语义报告记录：

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

语义测试未滑向下游领域。

## 12. 后续语义工作

### 12.1 进入 M11 前建议完成

1. 人工裁定 9 个出现额外维度的 REV case。
2. 补充 exact-dimension 语义 case。
3. 为 FIX-003 增加 post-patch M08 probe。
4. 扩展 unknown / unobserved / corrected 语义 case。
5. 扩展多轮指代与 correction corpus。
6. 报告 `PLAN-*` 的 repair lane 和 task count。
7. 记录 Turn Intent / Coverage / Faithfulness / Repair 每次调用 metadata。
8. 建立维度级人工裁定表。

### 12.2 M11 后必须重测

M11 实现后，语义验证必须升级为：

```text
M10 patch_ready
→ M11 append-only commit
→ M07 structural re-verify
→ M08 semantic re-review
→ M11 artifact gate update
→ repair_verified / repair_failed / repair_exhausted
```

新增语义断言：

```text
原 dimension 消失
无新增 dimension
clarification gap 不被 repair 抹除
human review 不被自动修复
repair_depth=1 后不得 repair of repair
```

### 12.3 稳定性建议

在关键语义 corpus 上执行：

```text
run 1
run 2
run 3
```

分别记录：

```text
M06 proposal digest
M08 true dimensions
M09 route
M10 patch state
post-patch probe result
```

不得用后一次成功覆盖前一次失败。

## 13. 最终结论

当前 Pre-M11 语义测试切片是正常的：

```text
33/33 semantic case passed
8/8 M06 generation fidelity passed
12/12 M08 target dimension coverage passed
11/11 M09 routing passed
2/2 M10 repair cases reached patch_ready
FIX-001 post-patch probe resolved original issue without new issue
```

同时必须保留以下解释：

```text
目标维度覆盖通过，不等于 exact precision 已证明
GEN-002 / GEN-003 的时间 clarification 是重要观察
9 个 REV case 出现额外维度，待人工裁定
FIX-003 尚未完整 post-patch probe
本轮是单次 semantic run
M11 / M12 / M13 仍未闭环
```

因此准确结论是：

> 当前高价值语义能力在人工审核 fixture 和当前真实环境下可用；语义预处理层的
> 目标维度覆盖、确定性路由和局部修复已经形成可回归基线，但维度精确率、额外
> 维度归因、冷启动稳定性和 M11 后完整语义闭环仍未证明。

## 14. 关联材料

1. [semantic-collaboration-dag-pre-m11-integration-test-design.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-pre-m11-integration-test-design.md)
2. [semantic-collaboration-dag-pre-m11-integration-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-pre-m11-integration-change-summary.md)
3. [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
4. [semantic-collaboration-dag-m06-generation-skill-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m06-generation-skill-change-summary.md)
5. [semantic-collaboration-dag-m08-review-skill-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m08-review-skill-change-summary.md)
6. [semantic-collaboration-dag-m09-repair-planner-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m09-repair-planner-change-summary.md)
7. [semantic-collaboration-dag-m10-repair-skill-patch-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m10-repair-skill-patch-change-summary.md)

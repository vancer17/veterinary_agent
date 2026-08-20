<!--
=============================================================================
文件: docs/architecture/clinical-safety-required-context-change-summary.md
作用: 总结临床安全阶段 3“required_context 接入裁决”迁移后的稳定数据链、
      前提语义评估契约、受控并发策略与回归边界。
范围: 适用于回合观察事实、候选自然语言前提评估、OPA 输入投影、失败状态与
      阶段 3 回归验收。
说明: 本文档只描述跨模块稳定契约与可观察行为；不展开医学资产运营、完整症状
      标准化词表、疾病 code 治理或响应主信号排序。
维护: 当 observed_features、required_context_hash、precondition assessment、
      评估并发配置或 OPA 输入契约变化时同步维护。
=============================================================================
-->

# 临床安全前置上下文裁决变更总结

> **文档状态**：阶段 3 迁移完成后的对齐基线
>
> **适用范围**：临床安全回合事实抽取、自然语言候选前提评估、OPA 策略输入、显式回退状态
>
> **不适用范围**：资产 code 拆分、主安全信号排序、医学资产运营、完整医学词汇治理、问诊追问策略

## 1. 迁移目标

本阶段在保留资产自然语言 `required_context` 的前提下，把候选前提判断拆成三层：

```text
回合结构化事实
    ↓
自然语言前提语义蕴含评估
    ↓
OPA 通用结构化裁决
```

这样避免两条坏路径：

1. 不把 130 条静态资产的自然语言准入句子手工改造成全局医学枚举。
2. 不让 Python、OPA 或响应层通过关键词、候选分数或 `severity` 直接证明前提满足。

## 2. 回合观察事实契约

`ClinicalSafetySemanticResult` 新增 `observed_features`。每个事实包含：

| 字段 | 作用 |
|---|---|
| `feature_id` | 回合内稳定引用，例如 `f1` |
| `feature_kind` | `symptom` / `exposure` |
| `state` | `present` / `possible` / `denied` / `resolved` |
| `normalized_text` | 供前提语义评估器使用的自然语言事实 |
| `temporal_scope` | 事实时间范围 |
| `resolution_state` | 事实恢复状态 |

语义抽取提示词明确：

1. 只抽取用户本轮明确表达的症状或暴露事实。
2. 不从医学常识、资产名称或诊断推断补全。
3. 否定、假设、已缓解和远期既往必须使用状态表达。
4. `high_risk_terms` 仍只是审计短语，不是事实源。

OPA 输入只投影 `id / kind / state`，不包含 `normalized_text`。自然语言事实只在
`ClinicalSafetyPreconditionAssessor` 内部使用，避免 OPA 或其他层重新做文本匹配。

## 3. 前提语义蕴含评估

新增 `ClinicalSafetyPreconditionAssessor` 协议和 Qwen 实现。该层位于候选召回之后、
OPA 裁决之前，只回答：

> 当前回合 `observed_features` 是否明确蕴含候选 `required_context.symptoms`？

`required_context.symptoms` 的稳定组合语义是：

```text
条目级 any_of
```

即每个数组元素是一条完整的自然语言准入描述；只要当前回合事实明确蕴含其中任意一条
完整描述，评估结果可为 `satisfied`。条目内部可以自然地表达“并且”“背景下”“伴发”
等组合语义。该定义不展开症状组合、不生成额外候选，也不会把自然语言条目枚举化。

### 3.1 输入边界

模型只接收：

1. 完整 `observed_features`。
2. 去重后的 `required_context.symptoms` 自然语言子集。
3. `semantic_premise_hash` 作为 `item_id`。

不接收：

1. `candidate.score`
2. `severity`
3. `action_class`
4. `code`
5. `triage_message`
6. 原始用户全文
7. 资产全文
8. 其他候选

该边界防止“这是 urgent 资产”或“相似度很高”影响事实蕴含判断。

### 3.2 输出状态

| 状态 | 含义 | OPA 效果 |
|---|---|---|
| `satisfied` | 当前事实明确蕴含准入前提 | 候选可继续进入适用性判断 |
| `not_satisfied` | 可用事实与前提明确不一致或明确不满足 | 候选不适用，仅保留审计 |
| `unknown` | 事实不足、部分满足、低置信、超时或协议异常 | 候选不能升级，仅保留审计 |

模型输出必须引用 `evidence_ids`。以下情况会归一为 `unknown`：

1. 置信度低于门槛。
2. 引用不存在的 feature。
3. `satisfied` 引用非 present 症状事实。
4. `satisfied` 没有证据引用。
5. item 哈希缺失或重复。
6. 模型响应结构非法。

## 4. 前提哈希与去重

阶段 3 将前提哈希拆成两个职责：

| 哈希 | 覆盖范围 | 用途 |
|---|---|---|
| `required_context_hash` | 完整 `species / sex / age / symptoms` | OPA 候选版本绑定 |
| `semantic_premise_hash` | 仅 `symptoms` | 回合内复用模型语义评估结果 |

两个哈希都基于 `clinical_safety_canonical_required_context()`：

1. key 排序。
2. 值集合排序和去重。
3. 空值剔除。
4. 增加契约版本前缀。

`required_context_hash` 用于：

1. 绑定评估结果和候选版本。
2. 防止缓存、并发或批量结果错配。

OPA 同时校验：

```text
candidate.required_context_hash
==
precondition_assessments[asset_id].required_context_hash
```

哈希不一致时 Fail Closed。

`semantic_premise_hash` 用于模型输入去重。前提评估器只消费 `symptoms`，因此当多个
候选的症状前提集合相同而 species / sex / age 不同时，它们共享一次模型评估结果；
每个候选仍保留自己的完整 `required_context_hash` 供 OPA 绑定。该设计提高复用率，
但不把结构化范围判断前移到语义模型。

同一个 `semantic_premise_hash` 始终对应同一个 canonical symptoms prompt，不会因
召回候选排序或资产数组原始顺序变化而生成不同模型输入。

## 5. 受控并发与响应时间

多个候选可能声明相似或不同的自然语言前提。评估器按以下顺序减少调用量：

1. OPA `precondition_plan` 先过滤证据不足、明显不适用、被抑制、远期已缓解或不可能
   产生信号的候选；仅有症状前提但缺少 present 症状事实的候选会保留为信息缺口。
2. 只评估计划返回且声明 `required_context.symptoms` 的候选。
3. 没有可信 present 症状事实时，不调用模型，保留的候选全部 unknown。
4. 按 `semantic_premise_hash` 去重。
5. 按批次大小切分。
6. 使用信号量限制并发。
7. 设置单批超时和总截止时间。

新增配置：

| 配置 | 默认值 | 作用 |
|---|---:|---|
| `CLINICAL_SAFETY_PRECONDITION_BATCH_SIZE` | `1` | 单次模型请求最多包含多少个前提分组；真实评估确认批量上下文存在污染风险，默认单项隔离 |
| `CLINICAL_SAFETY_PRECONDITION_MAX_CONCURRENCY` | `2` | 前提评估最大并发批次 |
| `CLINICAL_SAFETY_PRECONDITION_BATCH_TIMEOUT_SECONDS` | `4` | 单批模型调用超时 |
| `CLINICAL_SAFETY_PRECONDITION_TOTAL_TIMEOUT_SECONDS` | `6` | 本轮所有前提评估总截止时间 |
| `CLINICAL_SAFETY_PRECONDITION_MIN_CONFIDENCE` | `0.65` | 前提蕴含评估最低可信置信度 |

批量上限仍为 32，最小置信度必须在 0 到 1 之间；非法配置会在评估器初始化阶段快速失败。
默认值为 1 是真实服务评估后的安全边界，不应在未重新执行隔离评估前调大。

超时、失败或缺失的条目均补为 `unknown`，不会回退关键词或按 severity 升级。
批次结果按候选原始顺序重新映射，任务完成顺序不影响 OPA 输入顺序。

## 6. OPA 输入与通用裁决

策略输入新增：

```text
semantic.observed_features
candidate.required_context_hash
precondition_assessments[asset_id]
```

每个 assessment 只包含：

1. `required_context_hash`
2. `status`
3. `evidence_ids`
4. `confidence`
5. `trusted`

OPA 对自然语言症状前提执行结构化防御：

1. 候选必须声明症状前提。
2. 必须存在该 asset 的评估。
3. 哈希必须是非空 `sha256:` 值且一致。
4. `trusted` 必须为 true。
5. `status=satisfied` 才能通过。
6. 每个 evidence id 必须存在于 `semantic.observed_features`。
7. `status=satisfied` 引用的每个事实必须是 present 症状。

`species / sex / age` 继续由 OPA 直接做结构化匹配。`matched_terms` 仍只作为审计，
不参与前提满足判断。

除最终 `decision` 外，OPA 还提供 `precondition_plan` 规则。该规则只输出需要进入
自然语言评估的 `asset_ids`，不生成医学结论；Python 不复制该过滤逻辑。

## 7. 显式状态与审计

`ClinicalSafetyFallbackState` 新增 `precondition` 状态，暴露：

1. 最终策略。
2. 是否降级。
3. 稳定原因。
4. 候选总数。
5. 自然语言前提候选数。
6. satisfied / not_satisfied / unknown 计数。
7. 是否存在可继续问诊补充的信息缺口。
8. 请求模型、模型候选链、prompt 版本、响应 schema 版本。
9. 评估总耗时（含 OPA 前提计划与语义评估）、批次数和去重分组数。

以下状态是显式降级：

1. 模型不可用。
2. 模型失败。
3. 响应非法。
4. 单批超时。
5. 总超时。
6. 协议冲突。

没有 present 事实导致的全量 unknown 是安全准入结果，不标记为依赖降级。

当 `requires_information=true` 且尚未达到追问上限时，问诊回答充分性策略会进入
`clinical_safety_precondition_unknown` 模式，将 `symptom_detail` 纳入追问槽位。
该联动只传递结构化信息缺口，不在临床安全模块内生成问诊问题。

## 8. 有意不做事项

1. 不把现有资产自然语言准入句子全部转为医学枚举。
2. 不在 Python 按 `candidate.code` 编写医学分支。
3. 不在 OPA 扫描用户原文或资产全文。
4. 不用 `matched_terms`、候选分数或 severity 证明前提满足。
5. 不提供本地硬编码前提判断回退。
6. 不在临床安全模块内实现追问、回答充分性或长期记忆。
7. 不做跨回合缓存；当前仅回合内按 `semantic_premise_hash` 去重。

## 9. 验收基线

阶段 3 完成后必须持续满足：

1. 高分 urgent 候选在无前提评估时不能升级。
2. `status=unknown` 不能升级。
3. `status=not_satisfied` 只保留审计。
4. 前提哈希错配不能升级。
5. evidence id 不存在，或 satisfied 引用了非 present 症状事实时不能升级。
6. OPA 输入不包含 `normalized_text`、用户原文或 `high_risk_terms`。
7. 前提模型输入不包含 severity、action_class、score、code 和分诊文案。
8. 多候选评估受批量与并发配置约束。
9. 评估失败和超时均显式 unknown，不启用坏回退。
10. OPA 前提计划不得把明显不适用的候选送入模型评估。
11. 同一 `semantic_premise_hash` 必须对应同一 canonical prompt。
12. 共享语义评估结果的候选必须保留各自完整 `required_context_hash` 供 OPA 校验。

## 10. 真实服务集成验证

阶段 3 提供显式开启的真实服务集成测试：

```text
tests/integration/test_clinical_safety_api_external.py
```

覆盖内容：

1. 真实 LiteLLM 语义抽取能输出可信 `observed_features`。
2. 明确否定不会被抽成 present 症状。
3. 真实 Qwen 对完整组合、any_of、部分组合、相关但不蕴含、明确否定、
   已缓解和无 present 事实场景返回符合契约的 `status`。
4. satisfied 只能引用 present 症状 evidence。
5. 前提模型 prompt 不包含 severity、action_class、score、triage_message、
   source_text 或 matched_terms。
6. 真实模型调用按 `semantic_premise_hash` 去重，同时保留完整
   `required_context_hash` 候选绑定。
7. 真实 OPA `precondition_plan` 能过滤明显不适用候选。
8. 真实 OPA `decision` 能消费 `precondition_assessments` 并输出升级信号。
9. PostgreSQL/pgvector 基线资产带 `required_context.symptoms`。
10. 完整 API satisfied 路径能产生 safety escalation。
11. 完整 API partial/unknown 路径能进入问诊追问，而不是误升级。

可通过以下脚本连接远程开发环境并执行：

```bash
scripts/integration/run-clinical-safety-api-smoke.sh
```

该脚本会：

1. 建立 PostgreSQL、LiteLLM 和 OPA 的 SSH 隧道。
2. 从远程容器配置安全注入测试所需凭据，不在终端回显密钥。
3. 默认同步本地 OPA 策略并重启远程 OPA。
4. 默认通过隧道执行 Alembic 迁移。
5. 写入带唯一前缀的临床安全、回答 RAG 和追问 RAG 最小真实基线。
6. 测试结束后按前缀清理数据。

默认测试仍跳过该集成文件；只有设置：

```text
RUN_CLINICAL_SAFETY_API_EXTERNAL_TEST=true
```

或通过上述脚本执行时才会访问外部服务。远程策略同步与数据库迁移可分别通过
`CLINICAL_SAFETY_SMOKE_SYNC_REMOTE_POLICY=false` 和
`CLINICAL_SAFETY_SMOKE_UPGRADE_REMOTE_DATABASE=false` 关闭。

当前初始真实服务验证结果：

```text
14 passed
```

该结果表示工程契约和核心语义样例可用，不等于完整医学语义质量评估完成。

### 10.1 批量隔离真实评估

批量 item 隔离评估由以下文件承载：

```text
tests/integration/test_clinical_safety_precondition_isolation_external.py
scripts/integration/run-clinical-safety-precondition-isolation-smoke.sh
```

评估维度：

1. 单 item 重复基线。
2. 批量上下文。
3. 多排列顺序。
4. 原始模型响应缺失、重复和未知 item。
5. 批量状态与单 item 共识差异。
6. evidence 是否引用到其他 item 专属事实。
7. 未知 / 部分 / 否定场景是否被放大为 trusted satisfied。
8. 同一 item 在不同顺序下的状态稳定性。

首轮完整试点使用：

```text
8 个受控用例
3 次重复
batch size = 4 / 8
3 种排列
```

Prompt v1 结果：

| 指标 | 结果 |
|---|---:|
| 结构错误 | 0 |
| 单 item 噪声 case 率 | 0% |
| 模型级污染率 | 25.00% |
| 有效污染率 | 15.97% |
| 逃逸污染率 | 0% |
| 顺序敏感率 | 31.25% |

Prompt 升级到 v2 并加入逐 item 隔离规则后复测：

| 指标 | 结果 |
|---|---:|
| 结构错误 | 0 |
| 单 item 噪声 case 率 | 0% |
| 模型级污染率 | 9.03% |
| 有效污染率 | 19.44% |
| 逃逸污染率 | 0% |
| 顺序敏感率 | 43.75% |

结论：

1. Prompt v2 可降低模型原始状态交叉，但仍不能消除顺序和置信度不稳定。
2. 未观察到 trusted satisfied 逃逸污染，说明当前 Python/OPA evidence 防御有效。
3. batch size 4 / 8 尚未达到批量隔离质量门槛。
4. 生产默认值因此收敛为 `batch_size=1`。
5. 后续如需恢复批量，必须重新执行该评估并通过有效污染率、顺序敏感率和逃逸污染率阈值。

生产默认 `batch_size=1` 复测结果：

| 指标 | 结果 |
|---|---:|
| 结构错误 | 0 |
| 单 item 噪声 case 率 | 12.50% |
| 模型级原始状态漂移率 | 4.17% |
| 批量 / 单 item 有效状态漂移率 | 1.39% |
| 逃逸污染率 | 0% |
| 顺序敏感率 | 12.50% |

单项调用本身没有其他 item 上下文，上述漂移主要来自真实模型在少数边界样例上的
非确定性，而不是跨 item 注意力污染。更重要的是，未出现 trusted satisfied 逃逸，
且有效漂移率和顺序漂移率均远低于 15% / 20% 阈值。

隔离评估脚本默认验证生产默认 `batch_size=1`。研究 4 / 8 或其他批量时，可显式设置：

```bash
CLINICAL_SAFETY_PRECONDITION_ISOLATION_BATCH_SIZES=4,8 \
scripts/integration/run-clinical-safety-precondition-isolation-smoke.sh
```

评估报告输出至：

```text
.data/evaluations/clinical-safety-precondition-isolation-*.json
```

## 11. 后续 TODO

| TODO | 当前边界 | 后续方向 |
|---|---|---|
| 跨回合评估缓存 | 仅回合内按内容哈希去重 | 如成本需要，可引入模型 / prompt / schema 版本化的受控缓存 |
| 前提语义质量评估 | Python 单测和 OPA 契约测试 | 建立医学审核 golden set 和线上 unknown 率监控 |
| 批量 item 隔离质量 | 真实评估确认 4/8 存在顺序和状态漂移，生产默认已收敛为单项调用 | 如需恢复批量，扩充医学审核样本并重新通过隔离评估阈值 |
| 全局前提评估并发治理 | 当前仅控制单回合并发，实际模型并发由 Qwen 客户端统一限流 | 根据线上限流和超时数据评估是否需要独立 bulkhead |
| `required_context.symptoms` 资产质量 | 保留自然语言，不解释不可靠条目 | 审计高频 unknown 条目后逐步治理资产文案 |
| 主信号排序与 code 拆分 | 属于阶段 4 | 由资产治理和响应投影阶段处理 |

## 12. 相关文档

1. [临床安全待迁移问题与分阶段治理方案](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-open-issues-migration-plan.md)
2. [临床安全证据充分性边界变更总结](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-evidence-boundary-change-summary.md)
3. [临床安全召回输入收紧变更总结](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-retrieval-input-change-summary.md)
4. [临床安全补齐测试基线变更总结](/home/vancer17/veterinary_agent/docs/architecture/clinical-safety-test-baseline-change-summary.md)

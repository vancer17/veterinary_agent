<!--
=============================================================================
文件: docs/architecture/clinical-safety-retrieval-input-change-summary.md
作用: 维护临床安全召回输入的稳定边界、跨模块对接契约与有意预留 TODO。
范围: 覆盖召回准入、向量正文、结构化画像过滤、候选身份、语义事实交接、
      OPA 输入边界和资产治理要求。
说明: 本文档只描述跨模块可依赖的行为边界；不描述疾病知识、关键词词表、
      软件包内部实现、数据库细节、提示词内容或测试替身实现。
维护: 当召回准入字段、向量正文边界、observed_features、required_context、
      候选/裁决原因码或资产范围契约变化时同步维护。
=============================================================================
-->

# 临床安全召回输入边界

> **文档状态**：阶段 2 完成后维护、阶段 3 事实交接边界更新后的对齐基线
>
> **适用范围**：临床安全召回输入、结构化宠物画像过滤、候选召回、语义事实交接、OPA 输入边界、资产治理对接
>
> **不适用范围**：医学资产运营、完整医学词汇治理、急诊 code 拆分、主安全信号排序、问诊追问策略、长期记忆

## 1. 稳定输入职责

临床安全强召回只消费三个正交输入：

| 输入 | 允许承担的职责 | 禁止承担的职责 |
|---|---|---|
| `query_text` | 生成 embedding，寻找语义相关候选 | 证明风险成立、决定急诊或推断资产 code |
| `ClinicalSafetyRetrievalScope` | 按物种、性别、年龄过滤明显不适用资产 | 作为本轮风险证据或症状事实 |
| `risk_evidence_state` | 决定是否具备强召回资格 | 表示疾病、诊断、处置或最终动作 |

稳定不变量：

1. 宠物画像是适用性上下文，不是本轮风险证据。
2. 用户询问分诊不等于当前存在高危事实。
3. 候选相似度不是风险等级。
4. `matched_terms` 只能审计，不是事实或前提满足证据。
5. 候选 `severity` / `action_class` 不能补足缺失的回合事实。

## 2. 强召回准入边界

召回请求由可信语义结果统一构造，外部实现不得绕过该入口拼接查询。

| 语义状态 | `query_text` | 强召回结果 |
|---|---|---|
| 语义缺失、低置信、失败或禁用 | 空 | 阻断 |
| 可信但证据 `insufficient` | 空 | 阻断 |
| 可信且证据 `sufficient`，正文非空 | 用户本轮事实正文 | 允许 |
| 可信且证据 `sufficient`，正文为空 | 空 | 阻断 |

稳定跳过原因：

| 场景 | 原因码 |
|---|---|
| 证据不足 | `risk_evidence_not_sufficient` |
| 证据未知 | `risk_evidence_unknown` |
| 证据充分但正文为空 | `empty_query` |

跳过强召回是候选准入结果，不是医学结论，也不是依赖降级。

## 3. 向量正文边界

向量正文只允许使用当前回合用户事实文本，并具备确定性长度上限。

以下信息永久禁止进入召回正文：

1. 物种、性别、年龄和宠物画像摘要。
2. 分诊、知识、预防等意图标签。
3. 症状、暴露、时间、缓解等泛化状态标签。
4. `high_risk_terms`、`negated_terms` 等审计短语。
5. 资产名称、候选 code、风险等级或处置文案。
6. `observed_features` 的自然语言正文。

`observed_features` 用于阶段 3 前提语义评估，不反向改变阶段 2 的召回正文。

## 4. 结构化范围过滤

范围值域：

| 字段 | 允许值 |
|---|---|
| `species` | `dog` / `cat` / `unknown` |
| `sex` | `male` / `female` / `unknown` |
| `age_group` | `juvenile` / `adult` / `senior` / `unknown` |

过滤语义：

| 条件 | 结果 |
|---|---|
| 资产范围为空数组 | 该维度不限制 |
| 当前值为 `unknown` | 不推断默认值；召回层不猜物种、性别或年龄 |
| 当前值在资产范围内 | 允许参与召回 |
| 当前值不在资产范围内 | 排除或判定不适用 |

`unknown` 不代表匹配成功。受限资产的最终裁决依赖对应 `required_context` Fail Closed。

## 5. 回合事实与候选交接

召回后交给前提裁决链路的稳定事实包括：

1. 本轮结构化语义状态。
2. `observed_features` 的引用标识、类别和状态。
3. 候选的完整前置上下文哈希。
4. 只覆盖症状前提的语义去重哈希。
5. 前提评估状态、置信度和 evidence 引用。

边界：

1. `observed_features.normalized_text` 只供前提语义评估使用。
2. OPA 只消费 `id / kind / state`，不接收自然语言事实正文。
3. `satisfied` 必须由当前回合 present 症状事实支撑。
4. denied、resolved、possible 或缺失事实不能支撑急性前提满足。
5. 共享语义评估结果的候选仍必须保留各自完整候选哈希。
6. 候选分数、severity、action_class、code 和 triage 文案不得进入前提事实判断。

## 6. `required_context` 对接语义

### 6.1 结构化维度

`species`、`sex`、`age` 是等值结构化前提，必须在发布契约中与资产受限范围保持一致。

### 6.2 自然语言症状前提

`required_context.symptoms` 是条目级 `any_of` 自然语言准入描述集合：

1. 每个数组元素是一条完整准入描述。
2. 当前回合事实明确蕴含任意一条完整描述时，前提可为 `satisfied`。
3. 只满足组合描述的一部分时必须 `unknown`。
4. 明确否定、已缓解或仅相关但不蕴含时不得 `satisfied`。
5. 不展开症状组合，不生成额外候选，不把条目改造为全局医学枚举。

组合语义写在自然语言条目内部，例如“多饮多尿背景下新发拒食”，而不是由数组顺序或全部条目隐式推导。

## 7. OPA 输入边界

OPA 只消费结构化摘要：

1. 语义可信状态。
2. 结构化 scope。
3. observed feature 引用。
4. 候选 required context 与哈希。
5. 前提评估状态和 evidence 引用。
6. 候选 severity、action、分数与阈值。

OPA 不接收：

1. 用户原文。
2. `observed_features.normalized_text`。
3. `high_risk_terms`、`negated_terms`。
4. 资产全文或向量 chunk 全文。
5. 前提评估模型自由文本说明。

最终动作始终由 OPA 统一裁决；前提语义评估器不输出 action、severity、message 或 signal。

## 8. 稳定可观察状态

召回状态暴露：

```text
stage
degraded
reasons
retrieval_source
vector_hit_count
candidate_count
```

稳定召回原因码族：

| 类别 | 原因码 |
|---|---|
| 准入跳过 | `risk_evidence_not_sufficient` / `risk_evidence_unknown` / `empty_query` |
| 依赖失败 | `embedding_client_unavailable` / `embedding_generation_failed:<异常类型>` / `vector_retrieval_failed:<异常类型>` |
| 向量为空 | `query_embedding_empty` / `vector_hit_count_zero` / `clinical_safety_retrieval_empty` / `vector_candidate_count_zero` |
| 数据契约异常 | `invalid_asset_reference` / `clinical_safety_asset_read_failed:<异常类型>` / `scope_filtered_candidate:<asset_id>` |
| 参数异常 | `invalid_retrieval_arguments` |

OPA 候选原因保持：

```text
clinical_safety_candidate_<原因>:<code>
```

其中 `<code>` 只是资产身份，不承载 Python 或 Rego 医学分支。

## 9. 资产与仓储对接要求

资产治理必须保证：

1. 受限 species / sex / age 声明等值 `required_context`。
2. 范围数组只使用受控枚举，空数组表示不限制。
3. `required_context.symptoms` 保持条目级 any_of 自然语言语义。
4. 运营说明、处置说明和 Agent 指令不得混入症状前提。
5. 每个急诊模式拥有可审计身份；通用大类不得替代具体模式身份。

向量仓储必须：

1. 在检索阶段消费结构化范围。
2. 只返回已发布、已审核且具备有效向量的候选。
3. 不以文本文件、资产 JSON 或关键词扫描作为生产回退。
4. 不在仓储层推断临床风险或候选前提满足性。

## 10. 集成禁止事项

1. 不绕过可信语义结果直接构造召回请求。
2. 不把画像拼入 embedding 正文。
3. 不把 `matched_terms`、候选分数或 severity 当作事实。
4. 不新增关键词、正则、资产短语或文本 JSON 检索回退。
5. 不按 `candidate.code` 编写 Python / Rego 医学分支。
6. 不把原始用户文本传给 OPA。
7. 不让前提评估器输出最终临床动作。
8. 不用可能、否定、已缓解或远期事实支撑当前急性前提。
9. 不在语义失败、模型失败或评估超时后恢复本地医学规则回退。

## 11. 有意预留 TODO

| TODO | 当前边界 | 后续对接要求 |
|---|---|---|
| 医学观察事实词汇治理 | `observed_features` 由语义抽取输出自然语言事实，未形成完整受控词汇 | 建立医学审核事实集、同义映射和版本兼容策略；不得恢复关键词推断 |
| `required_context.symptoms` 资产质量 | 保留自然语言条目级 any_of，不展开组合 | 审计高频 unknown / partial 条目，逐步治理运营说明和不可观察描述 |
| 前提语义质量 golden set | 已有真实服务集成与隔离评估样例，未形成完整医学审核集 | 扩充物种、年龄、否定、缓解、组合和近似表达样本，并绑定模型 / prompt 版本 |
| 画像弱增强 | 画像只做结构化过滤 | 如需增强，必须先定义受控事实投影和隐私边界，不得恢复文本拼接 |
| 召回排序与多样性 | 当前以相似度和结构化范围为主，未治理候选多样性 | 先定义可审计排序契约，再调整候选顺序或 TopK 策略 |
| 跨回合前提评估缓存 | 仅回合内按症状前提哈希去重 | 如引入缓存，键必须包含事实摘要、模型、prompt 和 schema 版本 |
| 资产 code 拆分 | 属于阶段 4 | 每个急诊模式应有独立稳定 code；大类只作为分组信息 |
| 主信号排序与响应投影 | 多候选审计保留，用户主信号治理未完成 | 由策略输出或统一投影层选择主信号，不得在响应层拼接全部 urgent 建议 |
| 线上质量监控 | 已有真实服务验证，未形成持续指标治理 | 监控召回率、unknown 率、误升级、模型耗时、成本和评估漂移 |

## 12. 验收基线

后续变更至少保持以下行为：

1. 证据不足、未知或语义失败时不执行强召回。
2. embedding 正文只包含用户本轮限长事实文本。
3. 物种、性别、年龄只进入结构化范围。
4. `unknown` 范围不被推断成任何具体值。
5. 明确范围失配候选不能进入最终升级。
6. OPA 输入不包含用户原文、事实自然语言正文或审计短语。
7. `required_context.symptoms` 不满足时不能 trusted satisfied。
8. 部分满足组合前提时必须 unknown。
9. denied / resolved / possible 事实不能支撑当前急性前提。
10. 候选分数、severity、action_class 和 code 不能补足前提事实。
11. 前提语义评估失败或超时时不能恢复关键词或本地医学规则回退。
12. 依赖失败通过 metadata 暴露稳定原因码。
13. 资产发布契约持续拒绝范围与 `required_context` 不一致的受限资产。

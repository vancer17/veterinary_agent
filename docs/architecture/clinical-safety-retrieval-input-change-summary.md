<!--
=============================================================================
文件: docs/architecture/clinical-safety-retrieval-input-change-summary.md
作用: 总结临床安全阶段 2“收紧召回输入”迁移后的稳定边界、对接契约与
      有意预留 TODO 项。
范围: 适用于临床安全语义抽取、候选召回输入、结构化画像过滤、OPA 输入边界、
      资产治理对接与阶段 2 回归验收。
说明: 本文档只描述跨模块稳定契约、可观察行为和明确不做事项；不承载疾病知识、
      关键词词表、资产正文、软件包内部函数实现或具体 SQL 细节。
维护: 当 query_text、ClinicalSafetyRetrievalScope、risk_evidence_state、
      召回/裁决原因码、observed_features 或资产范围契约变化时同步维护。
=============================================================================
-->

# 临床安全召回输入收紧变更总结

> **文档状态**：阶段 2 迁移与审查加固完成后的对齐基线
>
> **适用范围**：临床安全召回输入、结构化宠物画像过滤、pgvector 候选召回、OPA 输入边界、资产治理对接
>
> **不适用范围**：`observed_features` 正式特征词汇治理、完整 `required_context` 组合语义、
> 资产 code 拆分、主安全信号排序和医学资产运营

## 1. 迁移目标与输入职责

本阶段将临床安全候选召回从“单一混合字符串查询”迁移为三个正交输入：

| 输入 | 作用 | 禁止承担的职责 |
|---|---|---|
| `query_text` | 生成 embedding 并寻找语义相关候选 | 不直接决定急诊、阻断或安全 code |
| `ClinicalSafetyRetrievalScope` | 过滤不适用物种、性别和年龄范围 | 不替代本轮风险证据 |
| `risk_evidence_state` | 决定是否具备强召回资格 | 不表示疾病、诊断或最终动作 |

宠物画像是适用性上下文，不是本轮风险证据；候选相似度不是风险等级。

## 2. 稳定查询契约

召回入口统一接收 `ClinicalSafetyRetrievalRequest`，不再接收可同时承载画像和查询正文的
任意字符串。该对象只包含：

```text
query_text
scope
risk_evidence_state
```

准入矩阵：

| 语义状态 | `query_text` | 效果 |
|---|---|---|
| 缺失 / 低置信 / 失败 / 禁用 | 强制为空 | 完全阻断强召回，不采用任何画像值 |
| 可信但 `insufficient` | 强制为空 | 阻断强召回；画像仅保留为审计信息 |
| 可信且 `sufficient` 且正文非空 | 用户本轮原文（去首尾空白、限长） | 进入 embedding 与结构化过滤 |

跳过强召回的稳定原因码：

| 场景 | 原因码 |
|---|---|
| `risk_evidence_state=insufficient` | `risk_evidence_not_sufficient` |
| `risk_evidence_state=unknown` | `risk_evidence_unknown` |
| 证据充分但正文为空 | `empty_query` |

跳过强召回不是医学安全结论，也不是普通错误回退，而是显式的临床安全候选准入结果；
该状态不标记为链路降级。生产链路只允许通过“由可信语义结果构造召回请求”的统一入口
发起强召回，不得绕过证据边界直接捏造查询正文或证据状态。

## 3. 向量正文边界

当前阶段使用用户本轮原文作为 `query_text`，但只在证据充分时使用，并具备确定性长度
上限（2000 字符）；超长输入保留头部主诉，不因超长跳过召回。

以下信息永久退出向量正文：

1. 物种、性别和年龄（含年龄原文摘要）。
2. `intent_type`。
3. `symptom_state`、`exposure_state` 等泛化状态标签。
4. `temporal_scope`、`resolution_state` 等策略状态标签。
5. `high_risk_terms`。
6. 宠物上下文摘要。

语义结果只承载结构化事实与审计信息，不再提供任何“查询提示拼接”职责；召回入口也
不再接收画像文本摘要或年龄原文参数。

## 4. 结构化范围过滤语义

范围字段与受控值域：

| 字段 | 允许值 |
|---|---|
| `species` | `dog` / `cat` / `unknown` |
| `sex` | `male` / `female` / `unknown` |
| `age_group` | `juvenile` / `adult` / `senior` / `unknown` |

召回过滤、防御校验与 OPA 裁决共享同一语义矩阵，任何一层实现调整都必须保持一致：

| 条件 | 结果 |
|---|---|
| 资产范围为空数组 | 该维度不限制 |
| 当前值为 `unknown` | 不生成过滤条件，不推断默认值 |
| 当前值在资产范围内 | 允许参与召回 / 裁决 |
| 当前值不在资产范围内 | 排除或判定不适用 |

部署形态：

1. PostgreSQL/pgvector 在向量检索阶段按资产表 `species_scope`、`sex_scope`、`age_scope`
   数组执行结构化过滤。
2. 候选聚合阶段保留同一判断作为防御性校验；防御性拦截输出
   `scope_filtered_candidate:<asset_id>` 原因并标记链路降级，仓储协议违约不得被静默吞掉。
3. OPA 裁决层对已知失配值再次判定不适用（防御纵深）。

该过滤只判断资产适用性：不解析用户文本、不读取召回短语资产、不生成医学事实。

## 5. `high_risk_terms` 边界收缩

`high_risk_terms` 仍可保留在语义 metadata 中，用于审计模型抽取结果，但不再进入：

1. embedding 查询正文。
2. OPA 策略输入。
3. `required_context.symptoms` 满足判断。
4. 安全信号 `matched_terms` 投影。
5. 风险证据充分性补写。
6. 候选严重级别或安全 code 生成。

在正式 `observed_features` 契约完成前，OPA 对含有 `required_context.symptoms` 的候选
执行显式不可用处理，稳定原因码为：

```text
clinical_safety_candidate_required_context_unavailable:<code>
```

系统不使用自由中文短语猜测症状前提是否满足。

## 6. 审查加固后的稳定边界

1. **审计原因唯一性**：同一候选同时命中多个不适用条件时，OPA 必须输出唯一确定性
   原因，不得产生多输出冲突。优先级固定为：证据不可用 > 证据不足 > 语义否认 / 远期
   已缓解 > 前置上下文缺失 > 结构化范围不匹配 > 正常可观察候选。
2. **命中可解释性回退**：生产 pgvector 命中不携带短语命中词时，安全信号 `matched_terms`
   回退到资产治理域生成的 chunk 标题；不回退到用户原文、`high_risk_terms` 或资产短语扫描。
3. **范围失配与前置缺失的区分**：`unknown` 画像值对资产 scope 放行（不推断），但对
   资产声明的 `required_context` 阻断（前提必须肯定存在）。
4. **画像未知的收口责任在资产治理**：受限 `species_scope`、`sex_scope`、`age_scope`
   必须声明等值的 `required_context`，保证画像未知时受限资产在裁决层 Fail Closed；
   通用资产（空范围）不强制声明画像前置。
5. **值域双层约束**：三个范围维度的受控枚举同时由资产发布契约与数据库层约束执行；
   越界值在发布或写入阶段显式失败，不进入运行时静默失配。

## 7. 跨层职责边界

| 层 | 当前职责 |
|---|---|
| 临床安全语义抽取 | 输出可信结构化状态、受控画像值和审计短语 |
| 查询契约 | 依据证据边界整理 `query_text` 与结构化范围，执行强召回准入 |
| embedding 客户端 | 将 `query_text` 转换为向量 |
| PostgreSQL/pgvector 仓储 | 按向量相似度和结构化范围召回已发布 chunk |
| 候选聚合层 | 聚合候选并执行防御性范围校验与显式降级留痕 |
| OPA | 消费不含原始文本和审计短语的结构化输入，执行动作裁决 |

本阶段没有新增按症状、资产 code 或物种组合的 Python / Rego 医学分支。

## 8. 对接契约

### 8.1 外部 API 兼容

外部 API 请求与响应主结构保持兼容；本阶段行为变化全部通过响应 metadata 可审计，
不引入破坏性字段变更。

### 8.2 可观察状态与稳定原因码

召回状态通过 metadata 暴露 `stage`、`degraded`、`reasons`、`vector_hit_count`、
`candidate_count` 与 `retrieval_source`。集成方可以依赖的稳定原因码族：

| 类别 | 原因码 |
|---|---|
| 准入跳过 | `risk_evidence_not_sufficient` / `risk_evidence_unknown` / `empty_query` |
| 依赖不可用或失败 | `embedding_client_unavailable` / `embedding_generation_failed:<异常类型>` / `vector_retrieval_failed:<异常类型>` |
| 向量结果为空 | `query_embedding_empty` / `vector_hit_count_zero` / `clinical_safety_retrieval_empty` / `vector_candidate_count_zero` |
| 数据一致性异常 | `invalid_asset_reference` / `clinical_safety_asset_read_failed:<异常类型>` / `scope_filtered_candidate:<asset_id>` |
| 调用参数异常 | `invalid_retrieval_arguments` |

OPA 裁决原因码族保持 `clinical_safety_candidate_<原因>:<code>` 结构；`<code>` 为资产
身份，不承载 Python / Rego 医学分支语义。

### 8.3 资产治理对接要求

1. 受限范围维度必须声明等值 `required_context`（species / sex / age）。
2. 范围值只能使用受控枚举；空数组表示不限制。
3. `required_context.symptoms` 在 `observed_features` 落地前只会触发显式不可用，
   不会被短语匹配满足。

### 8.4 仓储实现对接要求

任何向量仓储实现必须在向量检索阶段消费结构化范围；未消费范围的实现会在聚合层被
防御性拦截并表现为降级状态，而不是静默放出不适用候选。

### 8.5 集成禁止事项

1. 不绕过统一入口直接构造召回请求。
2. 不把 `high_risk_terms`、`matched_terms` 或候选分数当作症状事实或风险等级。
3. 不新增关键词、正则、资产短语或文本 JSON 检索回退。
4. 不在 Python 业务层按 `candidate.code` 编写医学分支。

## 9. 有意预留 TODO

以下事项属于有意不做，后续实现必须以替代式迁移收束，不得在现有边界旁新增旁路：

| TODO | 当前边界 | 对接要求 |
|---|---|---|
| `observed_features` 正式事实集合 | 尚无受控结构化症状/暴露事实契约；`required_context.symptoms` 显式不可用 | 由语义抽取输出受治理词汇；OPA 只做集合关系判断，不扫描原文 |
| `required_context` 完整组合语义 | 仅实现单维度等值与 fail-closed；未定义 `all_of`、`any_of`、否定和未知值组合 | 先扩展资产契约与结构化事实契约，再实现通用策略匹配 |
| 命中可解释性增强 | `matched_terms` 仅回退到 chunk 标题；“为什么命中/为什么未升级”完整投影未建设 | 阶段 3 随裁决理由统一设计；不得恢复短语扫描 |
| 画像弱增强 | 画像只做结构化过滤，不做文本增强 | 除非 `observed_features` 稳定后提供受控事实投影 |
| 画像未知的运行时收紧 | `unknown` 对 scope 放行；收口依赖资产 `required_context` 声明 | 预发布回归须包含“画像未知 + 受限资产”样本；资产审核强制第 8.3 节规则 |
| 资产 code 拆分与主信号排序 | 仍存在多模式复用同一急诊 code；响应可能拼接多条建议 | 阶段 4 资产治理处理；`code` 不得成为 Python 医学分支条件 |
| 语义质量与版本治理 | 结构化字段与失败语义已稳定，医学表达覆盖未治理 | 建立医学审核样本、版本兼容策略和真实服务契约测试 |
| 召回多样性与排序策略 | 仅保证范围过滤与相似度主路径；未做候选多样性治理 | 如需调整，须先定义可审计的排序契约，再改实现 |

## 10. 验收基线

阶段 2 回归测试至少覆盖：

1. 证据充分时 embedding 正文只包含用户本轮限长文本。
2. 物种、性别和年龄只进入结构化范围对象。
3. `insufficient` 语义不调用 embedding。
4. `unknown` 语义不从原文推断召回范围。
5. 成年犬不会仅凭画像召回幼犬专属资产。
6. OPA payload 不包含 `source_text`、`high_risk_terms` 和 `negated_terms`。
7. embedding 或 PostgreSQL 不可用时不回退到关键词、文件或资产短语召回。
8. 多个不适用谓词同时命中时 OPA 输出唯一确定性审计原因。
9. 受限资产缺少等值 `required_context` 时发布契约显式失败。
10. 三层范围过滤语义一致，且不包含值域外过滤值。

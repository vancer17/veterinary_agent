<!--
=============================================================================
文件: docs/architecture/clinical-safety-open-issues-migration-plan.md
作用: 汇总临床安全链路当前仍需迁移的残余问题，并给出可分阶段执行的治理方案。
范围: 适用于临床安全语义抽取、候选召回、OPA 裁决、响应投影与相关资产治理。
说明: 本文档只描述迁移顺序、职责边界、验收标准与不变量；不展开软件包内部实现、
      Rego 细节、提示词细节或测试替身实现。
维护: 当临床安全语义字段、召回边界、候选裁决契约、资产 code 体系或响应投影方式调整时，
      应同步更新本文档。
=============================================================================
-->

# 临床安全待迁移问题与分阶段治理方案

> **文档状态**：待执行迁移方案
>
> **适用范围**：临床安全语义抽取、临床安全候选召回、临床安全裁决、响应投影、资产 code 治理
>
> **不适用范围**：问诊状态机、回答充分性、长期记忆、RAG 知识问答、基础输入安全、输出安全

## 1. 背景

当前临床安全链路的主路径已经完成语义抽取、候选召回与裁决的框架化迁移，但在真实预发布测试中，仍可观察到以下残余问题：

1. `intent_type=triage` 被过度解释为“已经存在高危事实”。
2. 召回查询把用户模糊主诉、宠物画像、年龄和分诊意图混合为一个过宽的向量查询。
3. OPA 主要依据候选 `severity`、`action_class` 和分数裁决，尚未严格验证“当前回合是否真的满足候选前提”。
4. 资产契约中的 `required_context` 已定义，但尚未进入完整裁决输入和策略匹配。
5. 多个不同急诊模式复用同一个 `EMERGENCY_RED_FLAG`，并在响应层被批量拼接展示，造成语义混乱。

本文档的目标是把上述问题拆成独立可执行的迁移阶段，避免后续修复再次滑向自定义规则状态机或硬关键词匹配。

## 2. 当前基线

当前可直接对照的实现位置如下：

- [src/vet_agent/clinical_safety/semantic_extractor.py](/home/vancer17/veterinary_agent/src/vet_agent/clinical_safety/semantic_extractor.py:202)
- [src/vet_agent/clinical_safety/evaluator.py](/home/vancer17/veterinary_agent/src/vet_agent/clinical_safety/evaluator.py:88)
- [src/vet_agent/clinical_safety/policy.py](/home/vancer17/veterinary_agent/src/vet_agent/clinical_safety/policy.py:110)
- [docker/opa/policies/clinical_safety.rego](/home/vancer17/veterinary_agent/docker/opa/policies/clinical_safety.rego:1)
- [src/vet_agent/orchestrator.py](/home/vancer17/veterinary_agent/src/vet_agent/orchestrator.py:558)
- [src/vet_agent/clinical_safety/asset_contract.py](/home/vancer17/veterinary_agent/src/vet_agent/clinical_safety/asset_contract.py:197)

当前基线可以概括为：

1. 语义抽取已具备结构化输出和显式降级状态。
2. 候选召回已切换为 pgvector 主路径。
3. OPA 已承担临床安全最终动作裁决。
4. 资产契约已定义 `required_context`。
5. 用户响应仍可能把多个 urgent 信号合并展示。

因此，本文件聚焦的是“迁移未闭合的最后几步”，而不是重做已完成的基础框架。

## 3. 待迁移问题清单

### 3.1 分诊意图与风险证据仍然耦合

**问题描述**

`intent_type=triage` 只表示用户想知道是否需要就医，不代表本轮输入已出现急诊证据。

**当前表现**

- `symptom_state=unknown`
- `exposure_state=unknown`
- `high_risk_terms=[]`
- 仍可能进入高危候选召回并触发升级

**风险**

模糊分诊请求被误升级为急诊分诊，导致系统把“提问意图”当成“临床事实”。

**目标状态**

引入独立的证据充分性表达，例如 `evidence_sufficiency` 或等价结构化字段，用于描述当前输入是否已经足以进入临床安全裁决。

---

### 3.2 召回查询过宽

**问题描述**

临床安全召回把用户主诉、宠物画像、年龄与语义提示拼成一个单一向量查询，导致语义空间被过度扩展。

**当前表现**

- 宠物画像信息被并入 embedding 主文本
- `triage`、`current`、`ongoing` 等泛化语义参与召回
- 缺少对“症状是否已明确”的前置门槛

**风险**

向量库召回到大量“场景相关但证据不成立”的泛化急诊资产。

**目标状态**

把检索输入拆成两部分：

1. 向量正文仅包含用户明确症状、暴露和时间表达。
2. 宠物画像仅作为结构化过滤或弱增强信息，而不是正文语义。

---

### 3.3 OPA 仍把候选严重级别当作当前风险

**问题描述**

当前 OPA 主要依据候选 `severity`、`action_class` 和分数判断是否升级，但没有独立核验“当前回合是否满足该候选的前提条件”。

**当前表现**

- `severity=urgent` 即倾向升级
- `action_class in {"emergency", "same_day_visit", "urgent_visit"}` 即倾向升级
- `score >= urgent_min_score` 即倾向升级

**风险**

候选资产本身是急诊类型，不等于当前用户已经满足该资产触发条件。

**目标状态**

OPA 必须同时消费：

1. 候选严重级别。
2. 结构化语义结果。
3. 候选适用性范围。
4. 当前回合是否满足候选前提。

---

### 3.4 `required_context` 尚未进入完整裁决

**问题描述**

资产契约已经定义了 `required_context`，但当前候选负载和 Rego 规则尚未把它作为核心裁决输入。

**当前表现**

- 资产定义里有前提条件
- `_candidate_payload()` 未传递 `required_context`
- `clinical_safety.rego` 未显式完成结构化满足判断

**风险**

资产中原本表达的症状组合、暴露背景和前置条件在运行时丢失，最终只剩“向量召回到一个急诊资产”。

**目标状态**

`required_context` 必须成为候选输入的一部分，并由 OPA 结合结构化语义结果进行满足性判断。

---

### 3.5 多个不同风险复用同一个 `EMERGENCY_RED_FLAG`

**问题描述**

多个不同急诊模式共用同一个 `code`，导致不同疾病、不同处置口径、不同审计定位在响应层被统一成一个宽泛红旗。

**当前表现**

- 呼吸急症、泌尿梗阻、神经急症、消化道急症都可能落到同一 `code`
- `_safety_triage_response_text()` 会把多个 urgent 信号的 message 直接拼接

**风险**

用户看到的是一串互不相关的急诊建议，无法判断本轮最主要的风险到底是哪一个。

**目标状态**

每个急诊模式拥有独立 `code`，`asset_type` 或 `category` 表示大类，`action_class` 表示处置级别，响应层只展示一个主信号，其余候选进入审计 metadata。

## 4. 分阶段迁移方案

### 阶段 0：补齐测试基线

**目标**

把当前残余问题固化为可回归测试，先让问题“可见、可定位、可验证”。

**交付物**

1. `triage` 意图但症状和暴露均未知时，不应直接升级。
2. 模糊主诉不应仅因宠物画像被召回为 urgent。
3. `required_context` 缺失时，候选不应被误认为已满足。
4. 多个 urgent 候选不应直接全部拼接到用户响应。

**验收标准**

- 新增测试能够稳定重现当前问题。
- 测试描述明确映射到本文件的四类残余问题。

---

### 阶段 1：引入证据充分性边界

**目标**

把“用户在问是否需要就医”与“当前已经具备高危证据”分离。

**建议改动点**

- [src/vet_agent/clinical_safety/semantic_extractor.py](/home/vancer17/veterinary_agent/src/vet_agent/clinical_safety/semantic_extractor.py:330)
- [src/vet_agent/clinical_safety/evaluator.py](/home/vancer17/veterinary_agent/src/vet_agent/clinical_safety/evaluator.py:97)

**执行方式**

1. 让结构化语义结果显式表达证据充分性。
2. 语义不充分时，只允许进入追问或 observe 级别路径。
3. 不把“triage 意图”直接映射为急诊风险。

**验收标准**

- `symptom_state=unknown` 且 `exposure_state=unknown` 时，不产生急诊型升级。
- 语义低置信或证据不足时，系统优先进入补充信息路径。

---

### 阶段 2：收紧召回输入

**目标**

把宠物画像从“正文语义”迁移为“结构化过滤或弱增强”。

**建议改动点**

- [src/vet_agent/clinical_safety/evaluator.py](/home/vancer17/veterinary_agent/src/vet_agent/clinical_safety/evaluator.py:134)
- [src/vet_agent/clinical_safety/retriever.py](/home/vancer17/veterinary_agent/src/vet_agent/clinical_safety/retriever.py:83)

**执行方式**

1. 向量正文优先保留用户明确症状和暴露。
2. 宠物画像仅作为范围过滤条件，不参与过宽正文拼接。
3. 当症状与暴露都未知时，召回应默认保守。

**验收标准**

- 无明确症状的 triage 请求不会因宠物画像单独召回大量急诊资产。
- 成年犬不会仅因 `species=dog` 被召回幼犬专属模式。

---

### 阶段 3：把 `required_context` 接入裁决

**目标**

让候选的前提条件真正进入 OPA 决策，而不是停留在资产文档。

**建议改动点**

- [src/vet_agent/clinical_safety/policy.py](/home/vancer17/veterinary_agent/src/vet_agent/clinical_safety/policy.py:358)
- [docker/opa/policies/clinical_safety.rego](/home/vancer17/veterinary_agent/docker/opa/policies/clinical_safety.rego:1)

**执行方式**

1. 将 `required_context` 写入候选 payload。
2. 在 OPA 中实现结构化满足判断。
3. 把 `observed_features`、语义状态和上下文适用性纳入同一裁决面。

**验收标准**

- 候选即使分数较高，只要 `required_context` 不满足，也不能升级为 urgent。
- 候选说明和裁决理由能体现“为什么命中”与“为什么未升级”。

---

### 阶段 4：拆分急诊 code 并收敛响应展示

**目标**

让每个急诊模式拥有独立身份，并避免用户看到多条互不相关的急诊建议。

**建议改动点**

- [assets/clinical_safety/vet_safety_assets.v1.json](/home/vancer17/veterinary_agent/assets/clinical_safety/vet_safety_assets.v1.json)
- [src/vet_agent/orchestrator.py](/home/vancer17/veterinary_agent/src/vet_agent/orchestrator.py:558)

**执行方式**

1. 将 `EMERGENCY_RED_FLAG` 拆分为具体、稳定、可审计的独立 `code`。
2. 保留 `asset_type`、`category` 和 `action_class` 作为大类信息。
3. 用户输出只投影一个主信号，其他候选进入 metadata 和审计记录。

**验收标准**

- 响应文本不再出现多条无关急诊建议并列输出。
- 审计 metadata 仍能保留全部候选细节。

---

### 阶段 5：回归验证与预发布观察

**目标**

在预发布环境验证迁移后的行为稳定性，避免引入新的回退路径。

**验收样例**

1. 模糊分诊请求只进入追问，不进入急诊。
2. 明确症状但范围不匹配的候选被过滤或降级。
3. `required_context` 不满足时，候选仅保留审计，不生成主升级信号。
4. 用户只看到一个主急诊信号，不再看到多条拼接建议。

## 5. 明确不做事项

为了避免系统重新退化为自定义规则状态机或硬关键词匹配，本轮迁移明确不做以下事项：

1. 不在 Python 业务层按 `candidate.code` 编写医学 if/else 分支。
2. 不用原始文本关键词直接映射急诊结论。
3. 不在 OPA 中扫描原始用户文本生成候选。
4. 不把 `EMERGENCY_RED_FLAG` 继续作为所有急诊模式的通用总标签。
5. 不把所有 urgent 候选直接拼接给用户。
6. 不把 `required_context` 继续停留在仅文档化字段。

## 6. 建议的执行顺序

推荐按以下顺序逐步推进：

1. 先补测试。
2. 再引入证据充分性边界。
3. 再收紧召回输入。
4. 再接入 `required_context`。
5. 最后拆分 `code` 并收敛用户展示。

这样可以把每一步的行为变化控制在最小范围内，便于审计、回滚和预发布验证。

## 7. 迁移完成判定

当以下条件同时满足时，可以认为本轮临床安全残余问题迁移完成：

1. `triage` 意图不会在症状和暴露未知时直接升级。
2. 宠物画像不会单独把模糊主诉放大为一批 urgent 候选。
3. OPA 会显式校验候选前提条件，而不只看严重级别和分数。
4. `required_context` 已进入候选裁决输入并产生实际效果。
5. 不同急诊模式拥有独立 `code`，用户响应只展示主信号。

## 8. 后续联动

本文件完成后，后续如需继续细化，可以再拆分为以下子文档：

1. 临床安全证据充分性字段设计。
2. 临床安全召回查询分层方案。
3. 临床安全资产 code 治理方案。
4. 临床安全 OPA 规则收敛方案。
5. 临床安全用户响应投影规范。


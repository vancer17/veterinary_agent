<!--
=============================================================================
文件: docs/architecture/consultation-dynamic-evidence-implementation-plan.md
作用: 将问诊动态证据目标架构拆成按阶段、按组件、可验收的实施清单。
范围: 覆盖阶段顺序、组件依赖、交付物、验收标准、PR 切分、TODO 阻塞、
      测试门禁、M15 接入前提与完成判定。
说明: 本文只承载工程实施顺序与状态，不重新定义架构契约；问诊动态证据
      契约以 consultation-dynamic-evidence-architecture.md 为权威，RAG 数据
      治理以 veterinary-rag-knowledge-graph-architecture.md 为权威。
维护: 当阶段顺序、组件依赖、实施状态、PR 切分、验收门禁或生产接入前提
      调整时，必须同步更新本文；架构契约变化时必须先修订架构基线。
=============================================================================
-->

# 问诊动态证据实施清单

> **文档状态**：工程实施计划 / 组件清单
>
> **架构权威**：
> [consultation-dynamic-evidence-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-dynamic-evidence-architecture.md)
>
> **RAG 数据治理权威**：
> [veterinary-rag-knowledge-graph-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/veterinary-rag-knowledge-graph-architecture.md)
>
> **当前生产状态**：旧核心槽位问诊链路仍是生产主路径。本文描述目标链路
> 的实施顺序；所有前置组件未完成前，M15 不得切换 primary。

## 1. 文档定位

本文回答：

```text
按什么顺序实现
每个组件依赖什么
每个组件交付什么
做到什么程度算完成
哪些依赖未就绪时必须 TODO / blocked
```

本文不回答：

```text
架构为什么这样设计
模型 prompt 全文
数据库表结构细节
RAG 建库实现细节
临床安全策略
长期记忆写入策略
```

若本文与问诊架构基线冲突，以架构基线为准；若架构基线与代码冲突，必须先
通过显式架构变更修订基线，不得在实现中形成第二解释。

## 2. 实施原则

```text
1. 契约先行：先冻结输入输出与失败码，再实现执行逻辑。
2. Verifier 同步交付：每个模型输出组件同步交付 strict verifier。
3. Fail Fast：失败显式 blocked，不得转空集合或回退旧链路。
4. 域边界优先：依赖未就绪时提供 TODO 空壳，不跨域实现。
5. 状态可追溯：组件状态、依赖与验收项必须可从本文定位。
6. 小 PR 交付：每个 PR 只覆盖清单中的一个或少数强关联组件。
7. 文档同步：实现状态变化必须同步本文。
```

## 3. 状态模型

组件状态固定为：

```text
not_started
contract_draft
contract_frozen
in_progress
implementation_done
test_in_progress
verification_done
blocked
deferred
completed
```

状态含义：

| 状态 | 含义 |
|---|---|
| `not_started` | 未开始 |
| `contract_draft` | 契约正在设计，不可实现业务逻辑 |
| `contract_frozen` | 输入、输出、失败码与验收标准冻结 |
| `in_progress` | 实现进行中 |
| `implementation_done` | 代码已实现，但测试 / 文档未闭合 |
| `test_in_progress` | 单元、负例或集成测试进行中 |
| `verification_done` | 组件级验收通过，尚未完成集成验收 |
| `blocked` | 依赖、契约或外部服务阻塞 |
| `deferred` | 经显式决策延后，不属于当前阶段 |
| `completed` | 实现、测试、文档和验收全部完成 |

只有同时满足以下条件才可标记 `completed`：

```text
实现完成
正例与负例测试完成
包顶层导出完成
文档状态同步完成
架构边界测试通过
无未声明依赖
```

## 4. 总体依赖图

```text
CDE-00 架构与清单冻结
→ CDE-01 TrustedPetFact 契约 / TODO Reader
→ CDE-02 ConsultationEvidenceContext
→ CDE-03 KnowledgeRetrievalPort 消费契约 / TODO Port
→ CDE-04 Evidence Proposition Extraction
→ CDE-05 Support Review
→ CDE-06 Candidate vs Canonical Duplicate Review
   与 CDE-07 Pairwise Batch Internal Duplicate Review 并发
→ CDE-08 Deterministic Canonicalization
→ CDE-09 Coverage State Review
→ CDE-10 Coverage Source Binding Review
→ CDE-11 Coverage Relation Merge
→ CDE-12 Dynamic Consultation Policy
→ CDE-13 Followup / Answer 对接
→ CDE-14 Legacy slots 兼容与迁移
→ CDE-15 端到端工程验收
→ CDE-16 M15 生产接入
```

额外约束：

```text
CDE-06 与 CDE-07 可并发，但必须基于同一个冻结 candidate snapshot。
CDE-09 与 CDE-10 必须顺序执行；CDE-10 依赖 CDE-09 的状态结果。
CDE-16 必须等待 CDE-01～CDE-15 全部 completed。
```

## 5. 阶段与组件总览

| 阶段 | 组件 | 状态 | 依赖 |
|---|---|---|---|
| Phase 0 | CDE-00 架构与清单冻结 | completed | 问诊架构、RAG 架构 |
| Phase 1 | CDE-01 TrustedPetFact 契约 / TODO Reader | not_started | 可信宠物事实领域边界 |
| Phase 1 | CDE-02 ConsultationEvidenceContext | not_started | CDE-01、M13 |
| Phase 2 | CDE-03 KnowledgeRetrievalPort 消费契约 / TODO Port | not_started | CDE-02、RAG 域端口 |
| Phase 3 | CDE-04 Evidence Proposition Extraction | not_started | CDE-03、M05 结构化模型能力 |
| Phase 4 | CDE-05 Support Review | not_started | CDE-04 |
| Phase 5 | CDE-06 Candidate vs Canonical Duplicate Review | not_started | CDE-05、canonical snapshot |
| Phase 5 | CDE-07 Pairwise Batch Internal Duplicate Review | not_started | CDE-05、冻结候选集 |
| Phase 6 | CDE-08 Deterministic Canonicalization | not_started | CDE-06、CDE-07 |
| Phase 7 | CDE-09 Coverage State Review | not_started | CDE-08、CDE-02 |
| Phase 7 | CDE-10 Coverage Source Binding Review | not_started | CDE-09 |
| Phase 7 | CDE-11 Coverage Relation Merge | not_started | CDE-09、CDE-10 |
| Phase 8 | CDE-12 Dynamic Consultation Policy | not_started | CDE-11 |
| Phase 8 | CDE-13 Followup / Answer 对接 | not_started | CDE-12 |
| Phase 9 | CDE-14 Legacy slots 兼容与迁移 | not_started | CDE-12 |
| Phase 10 | CDE-15 端到端工程验收 | not_started | CDE-01～CDE-14 |
| Phase 11 | CDE-16 M15 生产接入 | not_started | CDE-15、M15 前提 |

外部依赖状态：

| 外部依赖 | 当前状态 | 本文处理 |
|---|---|---|
| M13 ConsultationProjection | implemented | 只消费，不修改 |
| 旧核心槽位问诊链路 | production | 仅作为兼容与部署回滚路径 |
| RAG 数据治理域 | target architecture | 问诊侧只实现消费契约与 TODO Port |
| TrustedPetFact 领域 | 未实现 | CDE-01 先交付契约与 TODO Reader |
| M15 | 未实现 | CDE-16 最后接入 |

## 6. Phase 0：架构与清单冻结

### CDE-00 架构与清单冻结

**状态**：completed

**交付物**：

```text
consultation-dynamic-evidence-architecture.md
veterinary-rag-knowledge-graph-architecture.md
consultation-dynamic-evidence-implementation-plan.md
```

**验收**：

```text
问诊域与 RAG 数据治理域职责分离
M13 消费边界固定
模型输出契约归属问诊架构文档
RAG 建库职责归属 RAG 架构文档
组件依赖与状态模型固定
```

## 7. Phase 1：问诊证据上下文

### CDE-01 TrustedPetFact 契约 / TODO Reader

**状态**：not_started

**架构引用**：问诊架构 §6

**上游依赖**：

```text
宠物档案或已确认临床记录领域
长期事实治理边界
```

**交付物**：

```text
TrustedPetFactBundle 契约
TrustedPetFactReader Protocol
TODOTrustedPetFactReader 或等价显式失败空壳
可信事实 selector / digest 规则
```

**验收**：

```text
只读取已确认事实
不读取 Mem0 原始候选
不跨宠物召回
fact_text 是自包含自然语言 proposition
fact_id / digest / source / time 字段闭合
依赖未就绪时显式 trusted_pet_fact_reader_not_implemented
```

### CDE-02 ConsultationEvidenceContext

**状态**：not_started

**架构引用**：问诊架构 §5、§7

**上游依赖**：

```text
CDE-01
M13 ConsultationProjection
```

**交付物**：

```text
ConsultationSemanticProjectionConsumer
ConsultationEvidenceContext
current patient proposition selector 集合
trusted pet fact selector 集合
Turn Intent 与 clarification gap 透传
```

**验收**：

```text
不读取 M11 / M08 / M09 / M10 / M12 内部结构
不读取原始用户文本
不把 ConsultationProjection 硬转换成 SemanticExtractionResult
claim_ref 与 fact_id 不混合
current claim 与 trusted fact 显式分源
M13 rejected 结果显式失败
```

## 8. Phase 2：RAG 消费层

### CDE-03 KnowledgeRetrievalPort 消费契约 / TODO Port

**状态**：not_started

**架构引用**：问诊架构 §8；RAG 架构 §12

**上游依赖**：

```text
CDE-02
KnowledgeRetrievalPort
RAG active index release
```

**交付物**：

```text
KnowledgeRetrievalRequest 构造器
KnowledgeRetrievalResult 消费契约
scope / chunk local selector 绑定
TODOKnowledgeRetrievalService
RAG 稳定失败码映射
```

**验收**：

```text
请求携带 query_propositions，不携带原始用户文本
trusted_fact_propositions 显式分源
不访问 RAG 表、向量库、parser、cluster 内部状态或 index admin API
返回 scope / chunk 均有稳定身份
RAG 失败映射为问诊侧稳定失败码
TODO Port 显式 rag_service_unavailable
```

## 9. Phase 3：知识命题抽取

### CDE-04 Evidence Proposition Extraction

**状态**：not_started

**架构引用**：问诊架构 §9、§10

**上游依赖**：

```text
CDE-03
结构化 LLM Gateway
单 scope / 单 chunk 调用策略
```

**交付物**：

```text
轻量 TAG prompt renderer
strict output schema
evidence proposition candidate record
candidate selector 生成
来源 / scope / document 确定性绑定
```

**验收**：

```text
每次调用只处理一个 scope 和一个 chunk 或同 scope 连续小窗口
输出 evidence_propositions 字符串数组
命题是自包含自然语言陈述句
不输出问句、诊断、治疗、安全动作、追问建议
不输出 source_chunk_refs、condition id、reason、confidence
系统绑定 candidate / chunk / section / scope / document 身份
空结果与 suspicious empty 显式区分
```

## 10. Phase 4：来源支持审查

### CDE-05 Support Review

**状态**：not_started

**架构引用**：问诊架构 §11

**上游依赖**：

```text
CDE-04
source chunk / candidate scope
```

**交付物**：

```text
Support Review prompt renderer
supported_by_candidate strict schema
key closure verifier
unsupported blocking result
```

**验收**：

```text
输入不包含 canonical propositions、用户事实或问诊策略
输出 key set 等于 candidate selector set
value 严格 boolean
extra / missing key blocked
unsupported candidate 不进入 canonicalization
禁止 reason / confidence / corrected proposition
```

## 11. Phase 5：重复关系审查

### CDE-06 Candidate vs Canonical Duplicate Review

**状态**：not_started

**架构引用**：问诊架构 §12.1

**上游依赖**：

```text
CDE-05
冻结 canonical knowledge proposition snapshot
```

**交付物**：

```text
candidate / canonical selector 构造
duplicate_by_candidate strict schema
key closure verifier
canonical duplicate result record
```

**验收**：

```text
输出 keyed boolean，不输出布尔数组
key set 等于 candidate selector set
不输出重复目标 canonical id
粒度不同、方向不同、时间数量不同均输出 false
schema invalid / key mismatch blocked
```

### CDE-07 Pairwise Batch Internal Duplicate Review

**状态**：not_started

**架构引用**：问诊架构 §12.2

**上游依赖**：

```text
CDE-05
冻结 candidate snapshot
pairwise 预算策略
```

**交付物**：

```text
deterministic pair generator
equivalent_by_pair strict schema
pair closure verifier
candidate count / pair budget policy
```

**验收**：

```text
系统显式生成全部 unordered pairs
不要求模型判断“更早 candidate”
输出 key set 等于 pair selector set
value 严格 boolean
不输出 merge target 或命题文本
超预算显式 split / blocked / human review
```

## 12. Phase 6：确定性归一

### CDE-08 Deterministic Canonicalization

**状态**：not_started

**架构引用**：问诊架构 §13

**上游依赖**：

```text
CDE-06
CDE-07
```

**交付物**：

```text
exact dedupe
connected components builder
transitive consistency verifier
deterministic representative selector
canonical proposition id / digest
source / scope edge builder
raw candidate audit record
```

**验收**：

```text
不由 LLM 选择 canonical 文本
传递性冲突输出 duplicate_relation_disagreement
重复 candidate 不进入 active canonical set
raw candidate 与审查结果保留审计
canonical set 可由输入与审查结果重建
跨批结果必须经过最终合并门禁
```

## 13. Phase 7：覆盖审查

### CDE-09 Coverage State Review

**状态**：not_started

**架构引用**：问诊架构 §14.1～§14.4

**上游依赖**：

```text
CDE-02
CDE-08
clarification gaps
```

**交付物**：

```text
Coverage State prompt renderer
coverage_state_by_knowledge strict schema
coverage state enum verifier
knowledge key closure verifier
```

**验收**：

```text
输出 keyed enum，不输出 source refs
knowledge key set 闭合
normal / denied / unknown / unobserved 分离
partially_covered 不升格为完全覆盖
历史事实不覆盖当前症状
schema invalid blocked
```

### CDE-10 Coverage Source Binding Review

**状态**：not_started

**架构引用**：问诊架构 §14.5、§14.6

**上游依赖**：

```text
CDE-09
current patient fact selectors
trusted pet fact selectors
```

**交付物**：

```text
target set builder
Source Binding prompt renderer
source_refs_by_knowledge strict schema
source selector verifier
state-source requirement verifier
```

**验收**：

```text
在 Coverage State Review 后顺序执行
target set 由 state 结果确定性筛选
unknown / unobserved 不进入 source binding
source refs 只能来自输入 c* / p*
covered / partial / uncertain / mismatch 状态满足来源数量要求
禁止重新输出 coverage state
```

### CDE-11 Coverage Relation Merge

**状态**：not_started

**架构引用**：问诊架构 §14.7

**上游依赖**：

```text
CDE-09
CDE-10
```

**交付物**：

```text
coverage relation builder
coverage relation digest
state review digest 绑定
source binding review digest 绑定
```

**验收**：

```text
只合并通过 verifier 的 state 与 source binding 结果
每条需要来源的 relation 均有有效 source refs
unknown / unobserved 不携带来源
下游只消费 merged coverage relation
coverage relation 可审计、可缓存、可重建
```

## 14. Phase 8：问诊策略与输出对接

### CDE-12 Dynamic Consultation Policy

**状态**：not_started

**架构引用**：问诊架构 §15

**上游依赖**：

```text
CDE-11
Turn Intent
clarification gaps
followup limits
previous consultation state
```

**交付物**：

```text
dynamic consultation policy contract
candidate scope ranking input
answer_now 处理
gap impact 处理
answer / ask / reject decision
```

**验收**：

```text
不解析自然语言 proposition
不机械选择缺失最少候选病症
gap 不直接等于强制追问
answer_now 不抹除不确定性
不产生 urgent / blocked
不调用临床安全 evaluator / OPA / required_context
```

### CDE-13 Followup / Answer 对接

**状态**：not_started

**架构引用**：问诊架构 §16

**上游依赖**：

```text
CDE-12
既有回答生成与追问生成链路
```

**交付物**：

```text
followup generation adapter
answer scope builder
uncertainty expression projection
```

**验收**：

```text
只有策略输出 ask 后才生成追问
知识抽取层不生成问句
阶段性回答明确当前信息边界
不生成诊断、治疗方案或安全动作
既有 Answer RAG / Followup RAG 边界不被绕过
```

## 15. Phase 9：旧槽位兼容与迁移

### CDE-14 Legacy slots 兼容与迁移

**状态**：not_started

**架构引用**：问诊架构 §18

**上游依赖**：

```text
CDE-12
旧 ConsultationStateService
旧 prompt / response 兼容层
```

**交付物**：

```text
legacy slot compatibility projection
旧状态读取兼容
新旧策略显式切换配置
部署级回滚配置
```

**验收**：

```text
旧 slots 不再作为动态策略权威
仅明确等价时从 coverage relation 派生旧槽位
不强行合并进食量与饮水量粒度
新旧链路不形成双权威
部署级可回滚
```

## 16. Phase 10：端到端工程验收

### CDE-15 端到端工程验收

**状态**：not_started

**架构引用**：问诊架构 §26

**上游依赖**：

```text
CDE-01～CDE-14
```

**交付物**：

```text
契约测试
组件集成测试
领域隔离测试
端到端确定性替身测试
失败路径测试
文档状态审计
```

**验收**：

```text
全部组件 verification_done
全部模型输出 verifier 通过 key closure / strict schema 测试
全部失败路径显式 blocked
旧链路未在 primary 失败路径自动调用
consultation_state_written 仅发生在问诊领域自身
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
long_term_memory_written = false
input_preprocessing_experiment_called = false
```

## 17. Phase 11：M15 生产接入

### CDE-16 M15 生产接入

**状态**：not_started

**架构引用**：问诊架构 §23

**上游依赖**：

```text
CDE-15
M15 配置、失败映射与回滚策略
```

**交付物**：

```text
VetOrchestrator 显式路由
primary / disabled 配置
失败 metadata 信封
部署级回滚配置
生产排障说明
```

**验收**：

```text
CDE-01～CDE-15 全部 completed
primary 失败显式暴露
primary 失败不自动调用旧问诊抽取器
disabled 不启动动态证据链路
问诊状态只由问诊领域写入
生产请求可定位 retrieval / proposition / coverage / decision 身份
```

## 18. TODO 与阻塞管理

依赖未就绪时：

```text
实现 TODO Port / TODO Reader
显式抛出稳定失败码
补齐负例测试
在本文标记 blocked
记录阻塞依赖与恢复条件
```

禁止：

```text
跨域实现依赖
用旧问诊抽取器兜底
用关键词规则兜底
返回空集合伪装成功
删除失败记录
```

阻塞记录格式：

```text
组件 ID
阻塞依赖
阻塞原因
当前显式失败码
恢复条件
责任域
```

## 19. PR 切分

| PR | 覆盖组件 | 主要交付 |
|---|---|---|
| PR 00 | CDE-00 | 架构与实施清单 |
| PR 01 | CDE-01 | TrustedPetFact 契约与 TODO Reader |
| PR 02 | CDE-02 | ConsultationEvidenceContext |
| PR 03 | CDE-03 | KnowledgeRetrievalPort 消费契约与 TODO Port |
| PR 04 | CDE-04 | Evidence Proposition Extraction |
| PR 05 | CDE-05 | Support Review |
| PR 06 | CDE-06 | Candidate vs Canonical Duplicate Review |
| PR 07 | CDE-07 | Pairwise Batch Internal Duplicate Review |
| PR 08 | CDE-08 | Deterministic Canonicalization |
| PR 09 | CDE-09 | Coverage State Review |
| PR 10 | CDE-10 | Coverage Source Binding Review |
| PR 11 | CDE-11 | Coverage Relation Merge |
| PR 12 | CDE-12 | Dynamic Consultation Policy |
| PR 13 | CDE-13 | Followup / Answer 对接 |
| PR 14 | CDE-14 | Legacy slots 兼容与迁移 |
| PR 15 | CDE-15 | 端到端工程验收 |
| PR 16 | CDE-16 | M15 生产接入 |

每个 PR 必须包含：

```text
契约引用
实现
strict verifier
正例测试
负例测试
失败状态
包顶层导出
本文状态更新
```

## 20. 横切工程规范

所有实现 PR 必须满足：

```text
src-layout 包结构
公开 API 通过包 __init__.py 暴露
函数与闭包具备严格类型提示
函数与枚举具备中文 ReST 风格签名注释
文件顶部具备职责注释块
避免悬空配置与悬空工具方法
Fail Fast，不隐藏失败
```

涉及数据库时必须满足：

```text
SQLAlchemy 建模
字段级 description
仓储 Protocol 显式继承
业务层不得直接操作表模型
跨包不得直接引用内部实现
```

## 21. 测试门禁

| 测试类型 | 覆盖范围 |
|---|---|
| 契约测试 | 所有输入 / 输出模型、strict schema、failure enum |
| 单元测试 | deterministic builder、verifier、merge、selector mapping |
| prompt 测试 | TAG 渲染、变量闭合、不泄漏任务 ID |
| 负例测试 | extra field、missing key、非 boolean、key mismatch、来源不存在 |
| 组件集成测试 | CDE 相邻组件的数据流 |
| 领域隔离测试 | 不访问 RAG / M11 / M12 / 临床安全 / 长期记忆内部实现 |
| 端到端替身测试 | M13 + TrustedFact + RAG Port + Evidence Pipeline + Policy |
| 生产接入测试 | 配置路由、失败映射、无 fallback |

最低通过条件：

```text
compileall
ruff check 相关包与测试
mypy 相关包
pytest 单元 / 组件 / 端到端替身测试
```

真实外部服务联调必须显式标记 integration，不作为默认单测依赖。

## 22. 完成判定

问诊动态证据重构完成时必须满足：

```text
1. CDE-01～CDE-16 全部 completed
2. M13 输入边界闭合
3. TrustedPetFact 与 current claim 分源
4. 问诊侧只消费 KnowledgeRetrievalPort
5. Evidence Proposition 输出为固定薄 JSON
6. Support / Duplicate / Coverage verifier 全部生效
7. Canonicalization 与 Coverage Merge 均由确定性代码完成
8. Dynamic Policy 不解析自然语言原文
9. Followup 生成不决定策略
10. 临床安全与长期记忆边界未被侵入
11. 旧 slots 退出主路径且可部署回滚
12. M15 primary 失败不回退旧链路
13. 本文组件状态与代码、测试、文档一致
```

## 23. 文档同步触发

以下变化必须同步更新本文：

```text
组件新增、删除或合并
组件依赖顺序调整
组件状态变化
PR 切分调整
验收标准调整
TODO / blocked 状态变化
M15 接入前提变化
```

以下变化必须先修订架构基线，再同步本文：

```text
M13 消费契约调整
TrustedPetFactBundle 调整
KnowledgeRetrievalPort 调整
模型输出 JSON 调整
Coverage 状态机调整
动态问诊策略调整
旧槽位迁移策略调整
```

## 24. 关联材料

1. [consultation-dynamic-evidence-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-dynamic-evidence-architecture.md)
2. [veterinary-rag-knowledge-graph-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/veterinary-rag-knowledge-graph-architecture.md)
3. [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
4. [semantic-collaboration-dag-production-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-implementation-plan.md)
5. [consultation-semantic-extraction-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-semantic-extraction-change-summary.md)
6. [consultation-state-answerability-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-state-answerability-change-summary.md)

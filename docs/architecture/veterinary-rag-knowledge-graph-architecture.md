<!--
=============================================================================
文件: docs/architecture/veterinary-rag-knowledge-graph-architecture.md
作用: 定义兽医医疗 PDF 语料的 RAG 知识图谱数据治理域目标架构。
范围: 覆盖 PDF 来源治理、结构化解析、section tree、chunking、质量门禁、
      scope cluster、embedding / 检索索引、版本发布、增量重建、检索服务端口、
      来源追溯、缓存失效、权限隔离、观测治理与跨域消费边界。
说明: 本文属于 RAG 数据治理域架构基线，不实现问诊策略、临床安全裁决、
      长期记忆写入或任何医学动作；PDF source chunk 是知识权威，图谱与索引
      均为可版本化、可重建的派生资产。
维护: 当 PDF 来源契约、结构解析契约、section / chunk / scope schema、索引
      版本策略、检索端口或跨域消费边界调整时，必须同步更新本文。
=============================================================================
-->

# 兽医 RAG 知识图谱架构基线

> **文档状态**：目标架构基线，尚未实施
>
> **领域定位**：RAG 数据治理域
>
> **下游消费方**：
> [consultation-dynamic-evidence-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-dynamic-evidence-architecture.md)
> 定义问诊动态证据域如何消费本文输出的 scope / chunk 契约。
>
> **问诊侧实施清单**：
> [consultation-dynamic-evidence-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-dynamic-evidence-implementation-plan.md)
> 定义问诊域如何分阶段接入 `KnowledgeRetrievalPort`。
>
> **当前生产状态**：既有 Answer RAG / Followup RAG / RAG miss governance
> 链路继续存在；本文描述的是后续证据知识图谱独立治理目标，不自动替换既有
> RAG 服务。

## 1. 文档定位

本文定义兽医医疗 PDF 语料如何从原始文档变成：

```text
可追溯
可版本化
可重建
可质量分级
可按知识 scope 检索
```

的知识图谱。

核心对象是：

```text
Source Document
Section Tree
Source Chunk
Scope Cluster
Retrieval Index
Knowledge Retrieval Service
```

本文不定义：

```text
问诊证据命题抽取
问诊 coverage review
回答充分性策略
临床安全裁决
长期记忆写入
Answer RAG / Followup RAG 的业务策略
```

这些职责由对应领域文档定义。

## 2. 目标与非目标

### 2.1 目标

1. 让每个 RAG chunk 可追溯到：

```text
source document
document version
section path
page range
chunk digest
```

2. 利用 PDF 天然结构建立知识上下文，而不是只做无结构 top-k chunk 检索。
3. 以 scope cluster 提供候选知识分组，避免跨病症 chunk 混合调用。
4. 让索引、聚类和 embedding 结果可版本化、可灰度、可回滚。
5. 为问诊动态证据域提供稳定 `KnowledgeRetrievalPort`。
6. 将 parse quality、scope confidence 和索引健康状态显式暴露给调用方。
7. 保持 PDF source chunk 作为知识权威，图谱和索引均为派生资产。

### 2.2 非目标

本文不做：

```text
医学诊断
治疗方案生成
urgent / blocked 安全信号
临床安全 evaluator / OPA 调用
固定病症槽位维护
人工维护全量 required_evidence 索引
问诊命题等价判断
长期记忆写入
Mem0 消费或写入
```

## 3. 领域边界

| 能力 | 权威方 |
|---|---|
| PDF 来源注册与版本 | RAG 数据治理域 |
| PDF 结构解析 | RAG 数据治理域 |
| section tree | RAG 数据治理域 |
| chunking 与 parse quality | RAG 数据治理域 |
| scope cluster | RAG 数据治理域 |
| embedding 与检索索引 | RAG 数据治理域 |
| 索引发布 / 重建 / 回滚 | RAG 数据治理域 |
| 检索服务实现 | RAG 数据治理域 |
| 检索 query 语义构造 | 消费域，例如问诊动态证据域 |
| scope / chunk 消费策略 | 消费域 |
| 知识命题抽取与审查 | 问诊动态证据域 |
| 临床安全裁决 | 临床安全域 |
| 回答生成 | 既有回答链路 |

RAG 服务不得理解：

```text
M11 payload
M12 graph
M13 ConsultationProjection
患者原始文本
问诊 coverage state
临床安全状态
```

消费域不得访问：

```text
RAG 数据库表
向量库内部集合
embedding model 内部对象
PDF parser
scope clustering 内部状态
index admin API
```

## 4. 总体管道

### 4.1 离线 / 准离线数据治理管道

```text
PDF Source Registry
→ Source Validation
→ PDF Structural Parser
   ├─ document metadata
   ├─ outline / bookmark / heading extraction
   ├─ page mapping
   └─ section tree
→ Chunking & Quality Gate
→ Knowledge Source Index
→ Embedding Builder
→ Scope Cluster Builder
→ Retrieval Index Builder
→ Index Release
→ Observability & Maintenance
```

### 4.2 运行时检索管道

```text
Consumer Domain
→ KnowledgeRetrievalPort
→ Knowledge Retrieval Service
   ├─ scope retrieval
   └─ chunk retrieval within scope
→ KnowledgeRetrievalResult
→ Consumer Domain Review Pipeline
```

问诊动态证据域是当前主要设计消费方，但端口契约不得绑定问诊内部对象。

## 5. Source Document 契约

PDF 来源资产至少包含：

```text
document_id
document_title
document_version
file_digest
source_uri
source_type
trust_tier
language
published_date
license_metadata
enabled
created_at
updated_at
ingestion_version
```

### 5.1 来源治理要求

必须显式回答：

```text
谁可以新增 PDF
什么来源被视为可信
PDF 更新如何生成新 document_version
文档禁用后索引如何失效
相同内容重复上传如何去重
外部来源凭证如何保护
版权与引用要求如何保留
```

### 5.2 来源可信级别

`trust_tier` 可以区分：

```text
veterinary_guideline
clinical_reference
internal_approved_material
external_reference
unreviewed
```

具体枚举在实施前必须冻结。未审查或低可信来源不得默认进入生产检索范围。

## 6. PDF 结构解析

### 6.1 解析输入

```text
document_id
document_version
PDF binary / object reference
file_digest
parser_version
```

### 6.2 解析输出

```text
document parse record
outline / bookmarks
heading candidates
section tree
page mapping
raw text blocks
table candidates
figure captions
parse quality metrics
parser warnings
```

### 6.3 结构信号

解析器可以使用：

```text
PDF outline / bookmarks
目录页
标题字体与字号
章节编号
页眉页脚
标题位置
段落缩进
表格标题
图片说明
```

不得使用医学关键词规则决定：

```text
病症归属
诊断结论
急诊风险
安全动作
```

### 6.4 解析质量

至少定义：

```text
parse_good
parse_degraded
table_split
heading_missing
ocr_uncertain
page_binding_failed
section_scope_missing
parse_failed
```

质量退化 chunk 不得静默进入生产检索。若允许受限使用，必须：

```text
显式配置
保留 parse quality
返回给消费方
可审计
可禁用
```

## 7. Section Tree 契约

每个 section 包含：

```text
section_id
document_id
document_version
section_path
heading_text
heading_level
parent_section_id
page_start
page_end
position
section_digest
parse_quality
```

示例：

```text
《兽医临床鉴别诊断手册》
→ 消化系统症状
→ 呕吐
→ 急性呕吐的鉴别诊断
```

### 7.1 Section 权威关系

```text
PDF source document 是权威
section tree 是结构化派生投影
```

section tree 可随 parser version 重建。

### 7.2 章节身份稳定性

当文档版本或解析策略变化时，section identity 应优先基于稳定材料派生：

```text
document_id
document_version
section path
heading text digest
page range
parser version
```

不应使用数据库自增 ID 作为跨版本稳定身份。

## 8. Source Chunk 契约

每个 chunk 包含：

```text
chunk_id
document_id
document_version
section_id
section_path
chunk_index
chunk_text
chunk_digest
page_start
page_end
char_start / char_end 可选
parse_quality
contains_table
contains_figure_caption
embedding_ref
enabled
```

### 8.1 Chunking 原则

1. 优先不跨越 section 边界。
2. 必须保留页码绑定。
3. 必须保留上下文标题或 section path。
4. 表格不得被机械切断；无法完整解析时显式标记。
5. chunk 大小策略必须版本化。
6. chunk digest 由 canonical chunk 内容派生。

### 8.2 Chunk 权威关系

```text
PDF source document 是权威
source chunk 是可重建切分
embedding 是派生表示
```

任何下游知识命题都必须绑定 `chunk_id` 与 `chunk_digest`。

## 9. Scope Cluster 契约

Scope cluster 用于提供自然知识分组，例如：

```text
呕吐
急性呕吐
呕吐的鉴别诊断
犬猫呕吐评估
```

每个 scope cluster 包含：

```text
scope_cluster_id
scope_text
scope_version
scope_cluster_digest
cluster_method_version
member_section_ids
member_chunk_ids
embedding_centroid_ref
membership_strength
document_count
enabled
```

### 9.1 聚类信号

可以使用：

```text
section heading embedding
目录结构
父章节路径
页面连续性
chunk embedding 与 heading embedding 一致性
跨文档相似标题
```

不得使用：

```text
医学关键词规则
诊断词典
急诊规则
安全动作规则
```

### 9.2 Scope cluster 定位

Scope cluster 是：

```text
候选知识检索上下文
可版本化派生索引
可重建缓存
```

Scope cluster 不是：

```text
权威诊断实体
医学结论
urgent / blocked 来源
问诊策略
长期事实
```

### 9.3 `scope_confidence` 边界

如果实现包含 `scope_confidence`，它只能用于：

```text
检索排序
补充召回
人工复核候选
降低调用优先级
```

不得用于：

```text
判定医学事实
判定 proposition 等价
产生 urgent / blocked
替代临床安全裁决
```

### 9.4 聚类一致性

同一 index release 内必须可追溯：

```text
scope → section → chunk → document
```

如果 chunk 同时属于多个 scope，必须保留多个 membership，并明确主 scope
只由检索策略决定，不由聚类结果隐式覆盖。

## 10. Embedding 与检索索引

### 10.1 Embedding 契约

```text
embedding_id
embedding_model
embedding_version
source_type: section_heading / chunk_text / scope_centroid
source_id
vector_ref
dimension
created_at
```

### 10.2 检索索引类型

可组合：

```text
vector index
lexical index
section path index
document metadata filter
scope membership index
hybrid retrieval
reranking
```

具体实现可以演进，但必须版本化。

### 10.3 Index Release 契约

```text
index_release_id
index_version
corpus_digest
embedding_model_version
parser_version
cluster_method_version
chunking_policy_version
retrieval_policy_version
created_at
state
```

状态：

```text
building
canary
active
deprecated
failed
rolled_back
```

### 10.4 发布原则

1. 不直接修改 active index。
2. 新索引先 building，再 canary，再 active。
3. active 切换必须原子化。
4. 旧索引保留可回滚窗口。
5. 每次发布记录 corpus diff。
6. 发布前后执行检索回归测试。

## 11. 增量更新与重建

### 11.1 文档新增

```text
register document
parse
chunk
embed
cluster
build candidate index
release
```

### 11.2 文档更新

必须比较：

```text
file_digest
document_version
section tree diff
chunk digest diff
```

文档更新不得直接污染 active release。

### 11.3 文档禁用 / 删除

禁用文档后：

```text
active index 可保留到下一个 release
检索服务必须立即按 enabled filter 排除
后续 release 移除相关 chunks
历史 retrieval result 保留审计
```

### 11.4 Embedding 模型升级

必须生成新 index release，并执行：

```text
新旧检索对比
召回回归
scope 稳定性检查
人工抽样
灰度切换
```

不得原地覆盖向量。

## 12. KnowledgeRetrievalPort

### 12.1 端口原则

RAG 数据治理域通过稳定端口向消费域提供检索能力：

```text
retrieve_scopes(request)
retrieve_chunks(scope)
get_chunk(chunk_id)
```

端口输入不得包含：

```text
用户原始文本
M11 / M12 / M13 内部对象
消费域内部状态
```

端口输出不得包含：

```text
诊断结论
治疗方案
urgent / blocked
临床动作
```

### 12.2 KnowledgeRetrievalRequest

```text
request_id
query_propositions
trusted_fact_propositions 可选
top_scopes
top_chunks_per_scope
allowed_document_policy
allowed_trust_tiers
allowed_languages
retrieval_policy_version
```

`query_propositions` 由消费域构造，RAG 服务只把它们当作检索查询文本。

### 12.3 KnowledgeRetrievalResult

```text
retrieval_id
request_id
index_release_id
index_version
terminal_state
failure_code
scopes
chunks
retrieval_metadata
```

### 12.4 Scope record

```text
scope_cluster_id
scope_text
scope_version
scope_cluster_digest
membership_strength
related_section_ids
document_ids
```

### 12.5 Chunk record

```text
chunk_id
chunk_text
chunk_digest
document_id
document_version
section_id
section_path
page_start
page_end
parse_quality
scope_cluster_id
```

## 13. 检索策略边界

推荐默认策略：

```text
scope-first retrieval
→ chunk retrieval within scope
```

### 13.1 消费域约束

问诊动态证据域负责确保：

```text
每次证据命题抽取只绑定一个 scope
每次调用只处理一个 chunk 或同 scope 连续小窗口
不同病症 scope 不混入同一次抽取
```

这不是 RAG 服务内部判断，而是消费域调用策略。

### 13.2 RAG 服务可以做的排序

```text
scope ranking
chunk ranking
hybrid score
metadata filtering
reranking
```

### 13.3 RAG 服务禁止的推断

```text
疾病诊断
急诊风险
安全动作
问诊必要性
命题等价
医学事实覆盖
```

## 14. 来源追溯与 Citation

每个检索 chunk 必须可还原：

```text
document_title
document_version
section_path
page_start / page_end
chunk_digest
source_uri 可选
license / citation metadata
```

下游展示引用时不得只显示抽象 `chunk_id`。

## 15. 缓存与失效

可缓存：

```text
scope retrieval result
chunk retrieval result
chunk record
embedding result
scope cluster membership
```

缓存 key 必须包含：

```text
query digest
retrieval policy version
index release id
allowed document policy digest
```

失效条件：

```text
index release 切换
document enabled 变化
document version 更新
retrieval policy 更新
embedding model 更新
cluster method 更新
```

缓存不是权威事实源。

## 16. 权限与数据隔离

必须支持：

```text
调用方身份
授权 scope
tenant / workspace 过滤
document trust tier 过滤
语言过滤
审计访问
敏感来源凭证隔离
```

RAG 检索不得因 query 相似而跨租户返回知识资产。

## 17. 观测治理

至少记录：

```text
request_id
retrieval_id
consumer_domain
index_release_id
retrieval policy version
scope count
chunk count
latency
failure code
parse quality distribution
document trust tier distribution
cache hit state
```

核心指标：

```text
PDF 解析成功率
section 识别成功率
page binding 成功率
chunk 质量分布
索引构建成功率
索引发布延迟
reindex 成功率
检索失败率
检索延迟
scope 聚类稳定性
跨版本检索回归差异
```

## 18. 失败状态

RAG 域内部失败码示例：

```text
source_document_unavailable
pdf_parse_failed
section_tree_invalid
page_binding_failed
chunking_failed
embedding_service_failed
embedding_dimension_mismatch
scope_cluster_failed
index_build_failed
index_release_failed
index_unavailable
reindex_in_progress
retrieval_policy_invalid
retrieval_failed
unauthorized_document_access
```

对问诊动态证据域暴露的稳定失败码由消费边界映射，例如：

```text
rag_service_unavailable
rag_retrieval_failed
knowledge_source_unavailable
no_authoritative_match
scope_metadata_missing
source_chunk_missing
```

## 19. 与既有 RAG 链路的关系

当前已有：

```text
answer_rag
followup_rag
rag_miss_governance
```

本文定义的证据知识图谱不自动替换它们。

关系：

```text
Evidence Knowledge RAG:
  用于问诊动态证据命题与知识 scope 检索

Answer RAG:
  用于阶段性回答证据召回

Followup RAG:
  用于追问问题生成证据召回
```

它们可以共享：

```text
document source registry
chunk store
vector index
scope index
citation metadata
```

但对外契约和业务策略必须分离。

## 20. 数据持久化边界

未来实现数据库层时必须满足：

```text
SQLAlchemy 建模
每个字段具备 description
仓储 Protocol 显式继承
业务层不得直接操作表模型
跨包 API 通过包 __init__.py 暴露
```

权威关系：

```text
PDF source document / chunk text
> section tree
> scope cluster
> embedding index
> retrieval cache
```

## 21. 防退化约束

后续实现不得引入：

```text
无结构 top-k chunk 作为唯一检索路径
PDF chunk 无 section / page 绑定
parse quality 被静默忽略
scope cluster 被当作诊断实体
embedding 阈值判定医学事实
关键词规则决定病症归属
RAG 服务输出 urgent / blocked
RAG 服务调用临床安全 evaluator / OPA
RAG 服务读取 M11 / M12 / M13 内部对象
消费域直接访问 RAG 表或向量库
原地覆盖 active index
无版本 embedding 升级
删除失败检索记录
失败转空 chunk 列表
```

## 22. 实施顺序

```text
PR 01 RAG 架构契约与数据模型设计
PR 02 PDF source registry 与文件治理
PR 03 PDF structural parser 与 section tree
PR 04 chunking 与 parse quality gate
PR 05 knowledge source index
PR 06 embedding builder 与向量索引
PR 07 scope cluster builder
PR 08 retrieval index release / rollback
PR 09 KnowledgeRetrievalPort
PR 10 混合检索与重排策略
PR 11 增量更新与重建
PR 12 权限、观测与运维文档
PR 13 与问诊动态证据域集成
```

## 23. 验收测试

至少覆盖：

```text
document 注册可追溯 file_digest 与版本
PDF 解析失败显式 blocked
section 均绑定 document version
chunk 均绑定 section / page / digest
parse quality 降级不被静默放行
表格跨页可显式标记
scope cluster 可重建且版本化
scope confidence 不用于医学判断
embedding model / index release 版本绑定
index release 可回滚
document disable 后检索立即排除
embedding 升级不原地覆盖
KnowledgeRetrievalPort 不接收原始用户文本
KnowledgeRetrievalPort 不返回诊断 / urgent / blocked
检索结果可还原 citation
消费域无法访问 RAG 内部表 / 向量库
缓存 key 绑定 index release
RAG 失败不返回空列表
```

## 24. 文档同步触发

以下变化必须同步更新本文：

```text
PDF 来源契约调整
section / chunk schema 调整
parse quality 状态调整
scope cluster 算法或契约调整
embedding model 策略调整
index release 策略调整
KnowledgeRetrievalPort 调整
权限隔离策略调整
与 Answer RAG / Followup RAG 的共享边界调整
```

## 25. 关联材料

1. [consultation-dynamic-evidence-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-dynamic-evidence-architecture.md)
2. [answer-rag-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/answer-rag-change-summary.md)
3. [followup-rag-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/followup-rag-change-summary.md)
4. [rag-miss-governance-change-summary.md](/home/vancer17/veterinary_agent/docs/architecture/rag-miss-governance-change-summary.md)
5. [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
6. [consultation-dynamic-evidence-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/consultation-dynamic-evidence-implementation-plan.md)

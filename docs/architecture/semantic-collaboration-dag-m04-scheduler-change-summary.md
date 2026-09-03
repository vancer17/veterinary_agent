<!--
=============================================================================
文件: semantic-collaboration-dag-m04-scheduler-change-summary.md
作用: 作为受限语义协作 DAG M04 Temporal-first 调度器的实现边界与对接基线。
范围: 覆盖 runtime 职责划分、任务执行端口、失败与重试语义、超时与取消、
      PostgreSQL 投影边界、有意 TODO、后续模块接入方式和验收口径。
说明: 本文面向 M05～M15 实现与运维对接，只描述稳定契约和架构边界，
      不展开软件包内部实现、SQL DDL、workflow 内部循环或测试替身细节。
维护: 当 Temporal 边界、任务终态、执行端口、投影语义或生产接入前提调整时，
      必须同步更新本文。
=============================================================================
-->

# 受限语义协作 DAG M04 Temporal-first 调度器对接基线

> **文档状态**：M04 生产工程边界已实现；待 Temporal Docker 编排、真实
> workflow 联调与 VetOrchestrator 接入后关闭
>
> **边界修订说明**：M04 Temporal-first 边界不变。最新 M06 生产契约已收敛为
> Turn Intent + 自然语言 Claim Proposition Inventory，任务执行门禁需同步纳入
> M08 Coverage / Faithfulness Review。

## 1. 当前状态

| 项目 | 当前状态 |
|---|---|
| M04 调度契约 | 已实现 |
| 确定性语义图推进 | 已实现 |
| Temporal workflow / activity 边界 | 已实现 |
| PostgreSQL 只读投影 | 已实现 |
| Temporal Server Docker 编排 | 未实现 |
| 真实 Temporal workflow 联调 | 未执行 |
| M05 StructuredLLMGateway | 已实现；尚未接入任务执行器 |
| M07 Deterministic Verifier | 未实现 |
| M08 Coverage / Faithfulness Review | 未实现 |
| M11 Artifact Store | 未实现 |
| VetOrchestrator 生产切换 | 未接入 |

当前可以确认的是：M04 已经按 Temporal-first 生产架构完成工程边界，不再包含
自有数据库任务队列、租约、attempt 状态机或 worker 恢复协议。

当前不能宣称的是：语义协作 DAG 已经可以端到端生成 verified claim graph。
原因在于 M05 尚未接入 M04 任务执行器，且 M06～M11 的生成 SKILL、
verifier 与 artifact 链路尚未完成。

## 2. 架构结论

M04 固定采用：

```text
自有确定性语义图内核
+ Temporal durable execution runtime
+ PostgreSQL 只读投影
```

核心判断：

> 自有代码负责“任务在语义图中何时可推进、结果意味着什么”；
> Temporal 负责“任务如何被可靠执行、重试、恢复和审计”；
> PostgreSQL 只负责“把已发生的业务终态投影为可查询状态”。

### 2.1 职责矩阵

| 职责 | 权威方 |
|---|---|
| Plan IR 依赖关系 | M03 |
| Skill 失败与语义重试策略 | M01 SkillCatalog |
| DAG frontier 计算 | M04 自有语义图内核 |
| 任务业务终态解释 | M04 契约 + M07 verifier + M08 review outcome |
| 任务队列 / activity 分发 | Temporal |
| worker 崩溃恢复 | Temporal |
| 基础设施重试 | Temporal RetryPolicy |
| 语义可重试失败的下一轮 attempt | Temporal RetryPolicy |
| workflow / activity 超时 | Temporal |
| 执行历史 | Temporal event history |
| artifact 权威 | M11 Artifact Store |
| artifact 引用绑定 | M04 / M11 契约 |
| 查询与审计投影 | PostgreSQL projection |
| claim graph | M12 |
| 问诊 / 临床安全 / 长期记忆投影 | M13 |
| VetOrchestrator 接入 | M15 |

### 2.2 已移除的自研执行职责

M04 明确不再包含：

```text
DeterministicDAGScheduler
数据库任务队列
数据库 worker 租约
数据库 ready / running 调度状态
数据库 attempt 状态机
自研 worker 恢复协议
Python 内自研任务投递循环
```

后续不得为了“方便排查”或“快速恢复”重新引入这些路径。

## 3. Temporal runtime 边界

### 3.1 Temporal 负责

```text
activity 队列
activity 分发
worker 崩溃后的任务重放
基础设施异常 retry
语义可重试失败的下一轮 attempt
workflow / activity timeout
workflow event history
workflow replay
```

### 3.2 Temporal 不负责

```text
解释医学语义
判断任务是否 verified
判断 artifact 是否可信
决定领域投影
调用问诊状态
调用临床安全链路
写长期记忆
发明 Plan IR 任务
```

### 3.3 稳定 workflow 契约

生产 workflow 名称：

```text
semantic-collaboration-dag.v2
```

workflow 输入只包含：

```text
run_id
workflow_id
validated_plan
policy
task_policies
```

稳定身份要求：

1. `run_id` 由权威 Plan IR 身份派生；
2. 同一 Plan IR 不允许产生多个权威 workflow；
3. workflow 输入绑定 TurnSnapshot digest、SkillCatalog digest 与 PlanPolicy digest；
4. workflow 不携带原始用户文本大 payload；
5. workflow 不携带未验证同伴任务输出；
6. workflow 不携带下游领域状态。

### 3.4 Temporal 环境接入前提

后续 Docker 编排必须显式提供：

```text
Temporal Server
Temporal 持久化数据库
Temporal namespace
task queue
worker 服务
网络与健康检查
观测与日志配置
```

应用侧启动 workflow 时必须显式提供：

```text
Temporal client
task queue
SkillRegistry
ValidatedPlan
DAGExecutionPolicy
```

worker 侧必须显式提供：

```text
Temporal client
task queue
SemanticDAGProjectionRepository
SemanticTaskExecutor
```

禁止：

```text
Temporal 不可用时自动降级为数据库扫表
Temporal 不可用时自动降级为进程内调度器
单次请求内回退旧问诊语义抽取器
把 Temporal 状态伪装成旧调度器状态
```

## 4. 任务执行端口

`SemanticTaskExecutor` 是 M04 与 M05～M11 之间的唯一任务执行边界。

后续应由以下能力组合实现：

```text
M05 StructuredLLMGateway
+ M07 Deterministic Verifier
+ M08 Review Outcome
+ M11 Artifact commit
= SemanticTaskExecutor
```

### 4.1 输入契约

任务执行端口收到：

```text
run_id
attempt_number
task
turn_snapshot_digest
dependency_artifacts
```

语义：

1. `task` 是权威 `PlanTask`，不能被实现侧改写；
2. `turn_snapshot_digest` 是上下文版本绑定；
3. `dependency_artifacts` 只包含直接上游成功任务的已验证 artifact 引用；
4. 执行器不能读取未验证同伴输出；
5. 执行器不能读取问诊状态、临床安全状态或长期记忆；
6. 执行器不能自行发明任务或依赖。

### 4.2 输出契约

任务执行端口必须返回显式业务结果：

```text
task_id
terminal_state
artifact_reference
failure_code
failure_message
```

约束：

```text
verified / repair_verified 必须携带 artifact_reference
失败终态不得携带 artifact_reference
失败终态必须携带 failure_code 和 failure_message
not_applicable 不携带 artifact，也不携带 failure
```

允许的业务终态：

```text
verified
repair_verified
not_applicable
blocked
disagreement
repair_exhausted
repair_failed
dependency_failed
review_failed
context_budget_exceeded
timeout
```

### 4.3 当前 TODO 空壳

当前存在显式 TODO 执行器：

```text
TODOSemanticTaskExecutor
```

行为：

```text
始终 Fail Fast
不调用模型
不生成伪 verified
不返回空 facts
不做旧链路 fallback
```

该空壳是有意保留的 M05～M11 接入占位，不是待绕过的错误。

## 5. 失败与重试语义

### 5.1 基础设施失败

示例：

```text
网络瞬断
模型网关 5xx
activity worker 崩溃
不可归因于语义结果的执行异常
```

处理方：

```text
Temporal RetryPolicy
```

以下错误不允许自动重试：

```text
NotImplementedError
SemanticTaskExecutionError
DAGProjectionRepositoryError
```

### 5.2 语义可重试失败

权威来源：

```text
SkillCatalog FailurePolicy.retryable_on
SkillCatalog FailurePolicy.max_attempts
```

处理方式：

```text
1. activity 收到语义失败结果
2. 判断该 failure code 是否声明为 retryable
3. 若仍在 SkillCatalog 语义重试预算内，抛出稳定 ApplicationError
4. Temporal RetryPolicy 执行下一轮 attempt
5. 达到预算后返回原显式业务终态
```

这保证：

```text
是否语义可重试：由 SkillCatalog 决定
下一次 attempt 调度：由 Temporal 决定
attempt 历史：由 Temporal event history 记录
数据库不保存 attempt 调度状态
```

### 5.3 语义不可重试失败

示例：

```text
schema_invalid
forbidden_output
ownership_violation
verifier_failed
canonical 无候选
repair budget exhausted
```

处理原则：

```text
不自动 retry
不掩盖失败
进入显式任务终态
按后续 M08～M10 架构接入 review / repair / blocked 路径
```

禁止：

```text
把 schema invalid 当作网络抖动重试
把 forbidden field 清洗后重试
把 verifier failed 伪装为 no_explicit_fact
把失败转换为空 facts
```

## 6. 超时与取消

### 6.1 超时

| 层级 | 权威 |
|---|---|
| 单任务执行超时 | Temporal activity timeout |
| 整轮 DAG 超时 | Temporal workflow run timeout |
| 超时后的业务解释 | M04 显式 `timeout` 终态 |

要求：

```text
超时不能表现为悬空任务
超时不能转换为 unknown
超时不能被默认模板掩盖
```

### 6.2 取消

取消通过 Temporal workflow signal 进入。

收敛规则：

```text
未终态任务写入显式 blocked 终态
run 投影写入 canceled
已 verified artifact 不因取消被撤销
下游不得继续消费未完成任务结果
```

禁止：

```text
数据库直接扫描并取消任务
业务层绕过 workflow 修改任务终态
取消后继续投影半成品 claim graph
```

## 7. PostgreSQL 投影边界

### 7.1 投影用途

PostgreSQL 仅用于：

```text
API 查询
审计摘要
工程排障
任务终态展示
artifact reference 索引
run / task / plan / workflow 身份关联
```

### 7.2 投影数据边界

run 投影可保存：

```text
run_id
workflow_id
plan_id
turn_id
snapshot_digest
skill_catalog_digest
plan_policy_digest
status
policy
task_policies
created_at / updated_at / finished_at
```

task 投影可保存：

```text
task_id
skill_id
skill_version
target_envelope_id
terminal_state
artifact_reference
failure_code
failure_message
```

### 7.3 投影禁止字段

投影表不得出现：

```text
worker_id
lease_until
runtime_state
ready / running 调度状态
attempt_count
max_attempts
```

原因：

```text
这些是执行状态，不是业务投影
权威在 Temporal
```

### 7.4 仓储访问边界

业务层只能通过：

```text
SemanticDAGProjectionRepository
```

访问投影。

允许能力：

```text
initialize_run
load_run
record_task_result
record_dependency_failure
finish_run
```

禁止：

```text
直接操作 SQLAlchemy 表模型
基于投影表实现任务扫描器
基于投影表实现 worker 调度
基于投影表实现租约或恢复
```

## 8. 有意预留 TODO

这些 TODO 是架构边界，不是随手遗漏。

| TODO | 责任模块 | M04 当前边界 | 后续接入方式 |
|---|---|---|---|
| Temporal Docker 编排 | 运维 / 部署 | 不提供服务编排 | 提供 Server、持久化、namespace、task queue、worker 与健康检查 |
| StructuredLLMGateway | M05 | M04 不直接调用模型 | 消费 M05 已实现的模型调用边界，在执行器内返回未验证 proposal |
| Deterministic Verifier | M07 | 不解释任务输出 | 校验 schema、所有权、evidence 与 forbidden output |
| Artifact Store | M11 | 只传递 artifact reference | 提供 append-only artifact、版本、lineage 与 stale |
| Review SKILL | M08 | 不做 review | 按 review 契约诊断任务或 artifact |
| Repair Planner | M09 | 不规划修复 | 只根据白名单 failure code 生成修复任务 |
| Repair / Patch | M10 | 不修改输出 | 只输出和验证 typed patch |
| Claim Graph | M12 | 不组装 graph | 只消费 verified artifact |
| 领域投影 Adapter | M13 | 不写领域状态 | 问诊、临床安全、长期记忆分别通过 adapter 消费 graph |
| VetOrchestrator 接入 | M15 | 不进入主请求链路 | 显式启动 workflow 并等待或查询结果 |

## 9. 后续模块对接方式

### 9.1 M05 对接

M05 已实现：

```text
结构化模型调用
strict response schema
模型 snapshot
usage / finish reason
调用失败显式化
```

M04 任务执行器后续消费的是：

```text
SemanticModelProposal
attempt metadata
```

该结果仍必须交给 M07 verifier 和 M08 review，不能直接生成 verified artifact
或任务成功终态。

验收：

```text
extra field 不清洗
schema invalid 不伪装成功
模型失败不转空结果
调用审计可追踪 workflow / task / attempt
```

### 9.2 M07 对接

M07 应实现：

```text
schema 校验
字段所有权校验
evidence binding 校验
forbidden output 阻断
claim binding 校验
```

验收：

```text
未验证输出不能返回 verified
forbidden field blocked
ownership violation blocked
evidence mismatch blocked
```

### 9.3 M11 对接

M11 应实现：

```text
append-only artifact
artifact version
base version
repair lineage
stale 标记
幂等提交
```

验收：

```text
verified 结果必须携带有效 artifact reference
重复 activity 重放不会重复提交权威 artifact
上游修复后下游标记 stale
```

### 9.4 M08～M10 对接

Review / Repair / Patch 不得由 M04 代实现。

验收：

```text
review 只诊断，不改 artifact
repair 只生成白名单 typed patch
patch 必须带 base version
repair of repair 被预算阻断
repair exhausted 是显式终态
```

### 9.5 M12～M13 对接

Claim Graph 与领域投影只能消费 verified artifact。

验收：

```text
proposal 不进入 graph
blocked artifact 不进入 graph
未实现 adapter 显式 Fail Fast
问诊 / 临床安全 / 长期记忆职责不越界
```

### 9.6 M15 对接

VetOrchestrator 接入时必须显式选择：

```text
同步等待 workflow
异步启动后轮询
异步启动后回调
```

无论哪种模式：

```text
不得隐式 fallback
不得吞掉 workflow failure
不得把 partial graph 伪装完成
必须在 metadata 暴露 run / workflow 身份
```

## 10. 防退化清单

后续 code review 必须阻断以下方向：

```text
恢复 DeterministicDAGScheduler
实现数据库任务队列
实现数据库租约
实现数据库 attempt 状态机
实现自研 worker 恢复协议
根据投影表扫描 ready task
在 Python 中调度医学语义分支
在调度层调用问诊状态
在调度层调用临床安全 evaluator / OPA / required_context
在调度层写长期记忆
用关键词 / 正则补抽事实
用宽松 JSON 修复模型输出
把失败转空 facts
回退旧问诊语义抽取器
```

## 11. 联调验收清单

### 11.1 Temporal 环境验收

```text
workflow 可启动
activity 可调度
worker 重启后 workflow 可恢复
retry history 可查询
timeout 可映射为 timeout 终态
cancel signal 可收敛 run
event history 可关联 task 与 artifact
```

### 11.2 投影验收

```text
同一 plan 重复初始化幂等
terminal run 的全部 task 均终态
依赖失败写入 dependency_failed
投影无 worker / lease / attempt 字段
投影不可用于调度
```

### 11.3 领域隔离验收

```text
consultation_state_written = false
clinical_safety_evaluator_called = false
clinical_safety_opa_called = false
required_context_called = false
long_term_memory_written = false
```

该断言限制 M04 与其 activity；后续领域自身消费 M13 投影时，由对应领域链路
负责自己的测试与策略。

## 12. 关联材料

1. [semantic-collaboration-dag-production-architecture.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-architecture.md)
2. [semantic-collaboration-dag-production-implementation-plan.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-production-implementation-plan.md)
3. [semantic-collaboration-dag-m06-production-boundary-revision.md](/home/vancer17/veterinary_agent/docs/architecture/semantic-collaboration-dag-m06-production-boundary-revision.md)

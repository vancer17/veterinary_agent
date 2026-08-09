<!--
文件：tmp/流式 meta 缺失.md
作用：归档兽医 Agent 流式终态事件缺失 metadata 的问题、接口契约、联调边界与验收要求。
说明：本文基于 2026-08-09 的现行代码审查结果编写，用于兽医 Agent、后端 BFF、App 前端、PM 与 QA 联调。
-->

---
id: vet-agent-stream-terminal-metadata-ticket
version: 1.0.0
owner: 兽医 Agent 开发线
last_updated: 2026-08-09
audience: 兽医 Agent 开发线、后端 BFF、App 前端线、PM、QA
status: fix-ticket
source: src/vet_agent/orchestrator.py; src/vet_agent/ingress_adapter.py; tests/test_vet_agent_api.py
scope: 流式 turn.completed 终态事件与同步 AgentTurnResponse 的 metadata 契约一致性
---

# 兽医 Agent 修复工单：流式终态 metadata 缺失

## 1. 工单背景

兽医 Agent 的同步响应已经在外部转换结果中携带 `metadata`。该对象承载问诊阶段、缺失槽位、追问计划、多任务拆分、临床安全回退状态及审计路径等前端状态所需信息。

当前流式链路会先完成完整回合计算，再按事件输出推理展示、分段文本和终态事件。因此，在发送 `turn.completed` 时，完整 `metadata` 已经存在于最终 `AgentTurnResponse` 中。当前问题不是元数据尚未生成，而是终态事件序列化时遗漏了该字段。

## 2. 问题描述

### 2.1 同步与流式契约不一致

同步外部响应由 `VetAgentIngressOrchestrator._to_external_turn()` 构造，已包含：

```jsonc
{
  "metadata": {
    // 与本轮问诊、追问、安全裁决和任务路由相关的状态快照。
  }
}
```

但两个流式出口的 `turn.completed` 都未包含该对象：

| 流式出口 | 当前终态事件字段 | 问题 |
|---|---|---|
| `VetOrchestrator.stream_turn()` | `id`、`status` | 缺少 `request_id`、`trace_id`、`metadata` |
| `VetAgentIngressOrchestrator.stream_turn()` | `id`、`status`、`request_id`、`trace_id` | 缺少 `metadata` |

这导致 App 前端在同步模式可获得的状态，在流式模式下无法获得同一来源的数据。

### 2.2 业务影响

- `requires_followup` 状态下，前端无法取得 `missing_slots`、`consultation_state`、`answerability` 与 `followup_question_plan`，无法稳定呈现追问进度与待补信息。
- 多任务路径下，前端无法取得 `task_count`、`tasks` 与任务状态快照，无法建立多任务视图。
- 临床安全路径下，前端无法取得 `clinical_safety_semantic` 与 `clinical_safety_resolution`，无法区分正常裁决和显式回退状态。
- 前端若自行推导医疗槽位、追问内容或安全状态，会产生超出 Agent 事实来源的风险。

## 3. 代码证据

### 3.1 同步响应已包含 metadata

`src/vet_agent/ingress_adapter.py` 中的 `_to_external_turn()` 已将内部响应的 `metadata` 写入外部响应。

### 3.2 原始 SSE 终态遗漏 metadata

`src/vet_agent/orchestrator.py` 中 `stream_turn()` 的 `turn.completed` 当前仅输出：

```jsonc
{
  "id": "turn_xxx",
  "status": "completed"
}
```

### 3.3 外部流式终态遗漏 metadata

`src/vet_agent/ingress_adapter.py` 中 `stream_turn()` 的 `turn.completed` 当前输出 `id`、`status`、`request_id` 与 `trace_id`，但未输出 `external["metadata"]`。

### 3.4 回归测试存在缺口

`tests/test_vet_agent_api.py` 已验证流式链路能发送 `reasoning_display.*` 与 `segment.delta` 事件，但尚未解析并断言 `turn.completed.metadata`。

## 4. 修复要求

### 4.1 终态事件必须携带 metadata

两个流式出口的 `turn.completed` 必须携带完整的 `metadata` 对象。

推荐直接在终态事件中增加 `metadata`，不新增独立 `turn.metadata` 事件。终态事件表示本轮不可再变化的最终快照，前端不需要维护额外事件的合并顺序。

```jsonc
{
  "event": "turn.completed",
  "id": "turn_xxx",
  "status": "requires_followup",
  "request_id": "req_xxx",
  "trace_id": "trace_xxx",
  "metadata": {
    // 必须与同一回合的同步响应 metadata 语义一致。
    // 直接转发同步响应已对外暴露的最终状态，不在流式层二次推导。
    "consultation_phase": "collecting_info",
    "missing_slots": ["appetite"],
    "followup_question_plan": {
      // questions 是本轮实际展示的追问子集，不等同于全部 missing_slots。
      "strategy": "rag_llm_question_planner",
      "fallback_reason": null,
      "questions": [
        {
          "slot": "appetite",
          "question": "今天的进食量与平时相比有什么变化？",
          "priority": 10
        }
      ]
    }
  }
}
```

### 4.2 metadata 必须保持同步响应语义

流式 `turn.completed.metadata` 必须直接使用同一回合最终 `AgentTurnResponse.metadata`，不得由流式层重新拼装、裁剪业务字段或根据文本二次推导。

允许事件层保留既有的协议封装差异，但以下字段语义必须一致：

- `id`
- `status`
- `request_id`
- `trace_id`
- `metadata`

### 4.3 metadata 必须始终为对象

即使本轮没有扩展状态，`turn.completed.metadata` 也必须输出空对象 `{}`，不得省略、输出 `null` 或输出非对象值。

### 4.4 不扩大流式敏感信息边界

本次修复仅补齐同步响应已对外暴露的 `metadata`，不得因此在终态事件中新增以下内容：

- `evidence`
- `safety_signals`
- 原始提示词
- 内部推理链
- 会话密钥、身份凭据或后端连接信息

若 BFF 存在独立的事件过滤策略，应由 BFF 按既有安全边界处理；本工单不对本仓库之外的 BFF 实现作事实假设。

## 5. 前端消费契约

### 5.1 requires_followup

前端应以 `metadata.followup_question_plan.questions` 中的 `question` 作为面向用户的追问文本，以 `slot` 作为稳定标识。

`metadata.missing_slots` 表示当前尚未满足的完整槽位集合；受单轮最大追问数和优先级限制，`questions` 可以是其子集。前端不得假设两者数量相等。

### 5.2 多任务

`metadata.tasks` 仅在 `vet_result.route = "multi_task_consultation"` 且任务拆分实际执行时出现。普通单任务问诊不得依赖该字段。

前端应以 `tasks` 是否为非空数组决定是否展示多任务视图，而不是根据 `task_router_strategy` 推测任务数量。

### 5.3 可点选选项

当前 `RagFollowupPlan` 仅定义文本问题、槽位、优先级、依据和回退原因，未定义 `choices` 或 `options` 契约。

本工单不要求生成医疗选项。前端应按纯文本追问处理，不得自行生成带有医疗判断含义的选项。

### 5.4 followup_rounds

`consultation_state.followup_rounds` 表示当前会话中已经发出的连续追问轮次数；当本轮输出追问时，该值已经包含当前轮。前端不得将其解释为“用户已经完成回答的轮次数”。

## 6. 验收要求

| 编号 | 验收项 | 预期结果 |
|---|---|---|
| A1 | 原始 SSE 终态字段 | `VetOrchestrator.stream_turn()` 的 `turn.completed.data` 包含 `id`、`status`、`request_id`、`trace_id`、`metadata` |
| A2 | 外部流式终态字段 | `VetAgentIngressOrchestrator.stream_turn()` 的 `turn.completed` 包含 `id`、`status`、`request_id`、`trace_id`、`metadata` |
| A3 | 同步/流式一致性 | 同一回合的流式终态 `metadata` 与同步外部响应 `metadata` 语义一致 |
| A4 | 追问状态 | `requires_followup` 流式终态可读取 `consultation_phase`、`missing_slots`、`consultation_state`、`answerability`、`followup_question_plan` |
| A5 | 多任务状态 | 多任务流式终态可读取 `task_count`、`tasks`、`consultation_states` |
| A6 | 安全回退状态 | 临床安全相关回合的流式终态可读取 `clinical_safety_semantic` 与 `clinical_safety_resolution` |
| A7 | 空对象约束 | 无扩展状态时 `metadata` 为 `{}`，不是缺失字段或 `null` |
| A8 | 回归测试 | 测试解析 `turn.completed` 并断言 metadata 存在、为对象且包含状态相关字段 |

## 7. 风险等级

| 风险项 | 等级 | 说明 |
|---|---|---|
| 流式追问状态不可见 | P0 | 阻断前端在流式模式下建立问诊进度与追问交互 |
| 多任务状态不可见 | P1 | 多任务结果无法被前端按结构化数据展示 |
| 临床安全回退状态不可见 | P1 | 前端和联调人员无法审计安全链路是否处于降级状态 |
| 同步/流式契约分叉 | P1 | 不同接入模式产生不同业务行为，增加回归与排障成本 |

## 8. 非目标范围

本工单不覆盖以下内容：

- 将当前“先完整执行、再分段输出”的实现改造成模型逐 token 生成。
- 新增 `turn.metadata` 独立事件。
- 设计或生成医疗追问选项。
- 修改 BFF 的事件过滤实现。
- 在流式终态中新增 `evidence`、`safety_signals` 或内部推理信息。

## Changelog

- 1.0.0（2026-08-09）：基于现行 `VetOrchestrator` 与 `VetAgentIngressOrchestrator` 流式实现重写工单，明确终态 metadata 契约、前端消费边界与验收要求。

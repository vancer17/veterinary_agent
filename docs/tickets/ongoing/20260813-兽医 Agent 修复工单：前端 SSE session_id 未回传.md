<!--
=============================================================================
文件: docs/tickets/ongoing/20260813-兽医 Agent 修复工单：前端 SSE session_id 未回传.md
作用: 归档前端经 BFF 调用兽医 Agent 时，流式响应中的 session_id 未被保存或回传导致多轮问诊上下文丢失的问题。
范围: 适用于 App 前端、后端 BFF、兽医 Agent 多轮问诊联调、临时效果验收和后续回归测试。
说明: 本文只描述检查项、修复要求、接口契约与验收标准，不包含具体业务代码实现。
维护: 当前端 SSE 消费方式、BFF 转发契约、Agent 会话字段或多轮问诊状态契约调整时，应同步更新本文档。
=============================================================================
-->

---
id: vet-agent-frontend-sse-session-id-ticket
version: 1.0.0
owner: App 前端线、后端 BFF、兽医 Agent 开发线
last_updated: 2026-08-13
audience: App 前端线、后端 BFF、兽医 Agent 开发线、PM、QA
status: fix-ticket
source: 前端经 BFF 调用兽医 Agent 的多轮快乐路径联调记录
scope: 前端 SSE 会话事件消费、BFF 多轮请求契约、Agent 会话上下文连续性
---

# 兽医 Agent 修复工单：前端 SSE session_id 未回传

## 1. 工单背景

当前临时效果验收中，前端通过主服务 BFF 调用兽医 Agent，而不是直接访问 Agent 公网入口。

调用入口为：

```text
POST /pets/{petId}/vet/turns
```

前端默认或主要使用流式响应模式，即请求体中 `stream=true` 或未显式关闭流式响应。BFF 在该模式下会先向前端发送一个独立 SSE 会话事件，用于告知本轮对应的 Agent 会话编号。

示例：

```text
event: session
data: {"session_id":"sess_xxx"}
```

后续轮次必须将该 `session_id` 放回请求体。否则，BFF 会为新请求创建新会话，Agent 无法读取上一轮主诉、追问槽位和问诊状态，导致多轮路径无法按预期收束。

## 2. 问题描述

### 2.1 主要现象

使用已验证的多轮快乐路径进行前端验收时，可能出现以下现象：

- 第一轮 Agent 正常追问。
- 第二轮用户已经补充精神、吃喝、排便或活动状态后，Agent 仍继续追问基础信息。
- 第二轮看起来像被当成一条新的问诊请求处理，而不是上一轮追问的回答。
- 同一提示词在直接调用 Agent 或受控 BFF 测试中可以完成，但在前端链路中无法完成。

### 2.2 典型复现路径

第一轮：

```text
我家猫早上吐了两次黄水，我有点担心。
```

预期结果：

```text
status=requires_followup
```

第二轮：

```text
精神还行，会自己喝水；下午主动吃了猫粮，比平时少三成左右。便便是下午拉的，成形，没有发黑，也没看到有血，没看到黏糊糊的膜；到现在八小时没再吐。
```

若第二轮携带第一轮返回的 `session_id`，预期结果为：

```text
status=completed
```

若第二轮未携带 `session_id`，BFF 会新建会话，Agent 可能继续追问起病时间、主诉背景、食欲量化信息等内容。

## 3. 根因判断

### 3.1 会话编号只在 SSE session 事件中出现

流式链路中，前端不能只消费文本分片事件。前端必须额外处理 `event: session`。

当前事件序列的关键结构为：

```text
event: session
data: {"session_id":"sess_xxx"}

event: turn.started
data: {...}

event: reasoning_display.started
data: {...}

event: segment.delta
data: {...}

event: turn.completed
data: {...}
```

其中 `session` 事件不是展示文本，但它是后续多轮请求的关键状态来源。

### 3.2 第二轮缺少 session_id 时会被视为新会话

当前 BFF 的多轮请求语义是：

```jsonc
{
  "input": [
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "用户本轮输入"
        }
      ]
    }
  ],
  "stream": true,
  "session_id": "sess_xxx"
}
```

第一轮可以没有 `session_id`。第二轮及后续轮次必须携带同一会话的 `session_id`。

如果前端没有携带该字段，BFF 无法判断当前输入是上一轮追问的回答，只能按新会话处理。

### 3.3 petId 与 session_id 需要保持一致

前端请求路径中的 `petId` 与请求体中的 `session_id` 必须对应同一只宠物和同一条问诊会话。

以下情况均应视为异常：

- 使用猫的 `petId` 测试“柯基跛行”提示词。
- 切换宠物后继续复用旧 `session_id`。
- 切换会话后仍复用上一条会话的 `session_id`。
- 页面刷新或重新进入问诊页后丢失当前会话编号，但 UI 仍展示上一轮对话内容。

## 4. 影响范围

| 影响项 | 说明 |
|---|---|
| 多轮问诊收敛 | Agent 丢失上一轮主诉后，会重新收集基础信息 |
| 快乐路径验收 | 已验证可完成的多轮示例在前端链路中可能失败 |
| 用户体验 | 用户认为已经回答过的问题会被再次追问 |
| 临床上下文完整性 | 主诉、时间线、槽位状态和追问计划无法连续累积 |
| 问题排查成本 | 直接调用 Agent 正常，前端链路异常，容易误判为模型能力问题 |

## 5. 前端检查项

### 5.1 SSE 事件解析

前端应确认 SSE 客户端是否支持读取自定义事件名。

必须处理以下事件：

| 事件名 | 是否展示 | 用途 |
|---|---|---|
| `session` | 否 | 保存当前 Agent 会话编号 |
| `turn.started` | 可选 | 标记本轮开始 |
| `reasoning_display.*` | 可选 | 展示推理摘要或进度 |
| `segment.*` | 是 | 展示正文分片 |
| `turn.completed` | 是 | 标记本轮结束并更新终态 |

不得只监听默认 `message` 事件后忽略自定义事件。

### 5.2 session_id 保存位置

前端应将 `session_id` 作为问诊会话状态保存，而不是作为单条消息的临时字段。

建议状态边界：

| 状态字段 | 说明 |
|---|---|
| `petId` | 当前问诊所属宠物 |
| `vetSessionId` | 当前 Agent 会话编号 |
| `conversationId` | 前端本地会话或页面会话编号，如已有 |
| `lastTurnStatus` | 最近一轮 Agent 状态 |

当 `petId` 变化时，应清空旧的 `vetSessionId`，避免跨宠物复用。

### 5.3 第二轮请求体检查

第一轮请求可以不携带 `session_id`：

```jsonc
{
  "input": [
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "我家猫早上吐了两次黄水，我有点担心。"
        }
      ]
    }
  ],
  "stream": true
}
```

第二轮请求必须携带第一轮返回的 `session_id`：

```jsonc
{
  "input": [
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "type": "input_text",
          "text": "精神还行，会自己喝水；下午主动吃了猫粮，比平时少三成左右。便便是下午拉的，成形，没有发黑，也没看到有血，没看到黏糊糊的膜；到现在八小时没再吐。"
        }
      ]
    }
  ],
  "stream": true,
  "session_id": "sess_xxx"
}
```

### 5.4 页面生命周期检查

前端应检查以下页面行为：

- 首轮 SSE 返回 `session` 事件后，状态是否立即写入当前问诊上下文。
- 用户点击发送第二轮时，请求体是否包含当前 `vetSessionId`。
- 页面刷新后，如历史对话仍保留，是否能恢复对应 `vetSessionId`。
- 用户切换宠物后，旧 `vetSessionId` 是否被清空。
- 用户重新发起新问诊时，是否明确创建新会话而不是复用旧会话。
- 请求失败或 SSE 中断后，是否错误清空仍有效的 `vetSessionId`。

## 6. 后端 BFF 检查项

### 6.1 流式响应首事件

BFF 应确保流式响应开始后稳定输出 `session` 事件。

验收示例：

```text
event: session
data: {"session_id":"sess_xxx"}
```

### 6.2 请求转发

BFF 应确认当前端请求体包含 `session_id` 时，转发给 Agent 的请求仍保留该字段。

检查点：

- 不得在 BFF DTO 转换时丢弃 `session_id`。
- 不得只在同步模式转发 `session_id`，而在流式模式遗漏。
- 不得将前端会话编号、业务会话编号和 Agent `session_id` 混用。

### 6.3 审计日志

建议 BFF 在开发或联调环境记录以下脱敏字段：

```jsonc
{
  "pet_id": "362918043241680896",
  "stream": true,
  "has_session_id": true,
  "session_id_prefix": "sess_3632...",
  "input_text_length": 42
}
```

日志中不得记录用户 token、完整鉴权头、密钥或其他敏感参数。

## 7. 修复要求

### 7.1 前端必须消费 session 事件

前端 SSE 消费逻辑必须识别 `event: session`，解析其中的 `session_id`，并写入当前问诊上下文。

修复后，第一轮收到 `session` 事件时，应立即得到：

```text
vetSessionId=sess_xxx
```

### 7.2 后续轮次必须回传 session_id

当当前问诊上下文存在 `vetSessionId` 时，后续所有 `/pets/{petId}/vet/turns` 请求必须携带：

```jsonc
{
  "session_id": "sess_xxx"
}
```

### 7.3 切换宠物必须重置会话

当前端 `petId` 变化时，必须清空旧 `vetSessionId`。

不得出现以下状态：

```text
petId=cat_pet_id
vetSessionId=dog_session_id
```

### 7.4 新问诊必须显式开新会话

如果用户点击“新问诊”或前端进入新的问诊上下文，应清空旧 `vetSessionId`，让第一轮请求重新创建 Agent 会话。

### 7.5 同步与流式模式行为一致

若前端或调试工具使用 `stream=false`，应从同步响应的 `data.session_id` 保存会话编号。

若使用 `stream=true`，应从 SSE `session` 事件保存会话编号。

两种模式在第二轮请求中都应回传同一字段：

```text
session_id
```

## 8. 验收要求

| 编号 | 验收项 | 预期结果 |
|---|---|---|
| A1 | 第一轮流式请求 | 前端收到并解析 `event: session` |
| A2 | 会话状态保存 | 第一轮后前端状态中存在 `vetSessionId` |
| A3 | 第二轮请求体 | 请求体包含第一轮返回的 `session_id` |
| A4 | BFF 转发 | BFF 转发到 Agent 的请求仍包含同一 `session_id` |
| A5 | 多轮猫呕吐路径 | 第二轮补齐信息后返回 `status=completed` |
| A6 | 第二轮缺失 session_id 负例 | Agent 创建新会话，测试应能识别该问题 |
| A7 | 切换宠物 | 切换 `petId` 后旧 `vetSessionId` 被清空 |
| A8 | 新问诊 | 新问诊不复用旧 `session_id` |
| A9 | 页面恢复 | 若前端展示历史问诊，应能恢复对应 `vetSessionId` 或明确只读展示 |
| A10 | 日志审计 | 联调日志可判断本轮是否携带 `session_id`，且不泄露敏感信息 |

## 9. 回归测试建议

### 9.1 前端单元测试

覆盖 SSE 解析器：

- 输入包含 `event: session` 的 SSE 片段。
- 断言解析结果包含 `session_id`。
- 断言该事件不会被当成普通文本消息展示。

### 9.2 前端集成测试

模拟两轮请求：

1. 第一轮 mock BFF 返回 `event: session`。
2. 用户继续发送第二轮。
3. 断言第二轮请求体包含相同 `session_id`。

### 9.3 BFF 联调测试

使用真实 BFF 入口发起两轮请求：

```text
POST /pets/{petId}/vet/turns
```

断言：

- 第一轮返回 `session_id`。
- 第二轮携带相同 `session_id`。
- Agent 不创建新会话。
- 多轮快乐路径可完成。

### 9.4 负例测试

故意省略第二轮 `session_id`，应观察到：

- BFF 或 Agent 创建新的 `session_id`。
- 第二轮不会被标记为上一轮追问回答。
- 测试用例明确失败或输出诊断信息。

## 10. 临时验收提示词

建议优先使用猫呕吐路径进行联调，因为该路径与当前远程测试宠物档案一致。

第一轮：

```text
我家猫早上吐了两次黄水，我有点担心。
```

第二轮：

```text
精神还行，会自己喝水；下午主动吃了猫粮，比平时少三成左右。便便是下午拉的，成形，没有发黑，也没看到有血，没看到黏糊糊的膜；到现在八小时没再吐。
```

注意事项：

- 第二轮必须携带第一轮 `session_id`。
- 不建议在当前临时验收中使用“没有血便”这类组合表达，避免触发既有红旗否定误判。
- 测试“柯基跛行”路径时，应使用狗的 `petId`，不得使用猫的 `petId`。

## 11. 非目标范围

本工单不覆盖以下内容：

- 修复红旗否定表达误判问题。
- 调整 Agent 问诊槽位收敛策略。
- 修改 BFF 身份、宠物资料和会话范围校验规则。
- 新增 Agent 公网入口。
- 调整模型提示词、RAG 召回或临床安全策略。
- 设计前端完整问诊历史存储方案。

上述事项如需处理，应拆分独立工单。

## 12. 风险等级

| 风险项 | 等级 | 说明 |
|---|---|---|
| 前端未保存 `session_id` | P0 | 直接导致多轮问诊上下文丢失 |
| 前端未回传 `session_id` | P0 | 第二轮被视为新会话，问诊漏斗无法稳定收束 |
| 跨宠物复用 `session_id` | P1 | 可能造成宠物资料与用户主诉冲突 |
| SSE 自定义事件未被处理 | P1 | 流式模式下会话状态不可见 |
| 日志缺少会话携带状态 | P2 | 增加前后端联调排查成本 |

## Changelog

- 1.0.0（2026-08-13）：新增前端 SSE `session_id` 未回传问题的检查与修复工单，明确前端消费契约、BFF 检查项、验收要求和回归测试建议。

---
id: vet-agent-backend-bff-contract
version: 0.2.0
owner: backend-owner(petintelli-backend, session df0c5194)
last_updated: 2026-07-17
status: active(宪法+三待拍全拍板;端口 18081 已给;待 Agent 部署即联调)
audience: 兽医 Agent 线 / App 前端线 / PM;cc 守卫线、DB、Infra
related: external_api.md(agent 线对外 API v1) / [[backend-ownership-charter]] / 情绪翻译接入契约(同款中转模式)
---

# 兽医 Agent 后端接入契约(后端 BFF)

## 0. 已拍宪法(PM 2026-07-17,适用所有微服务)
**前端 → 后端 → 后端调微服务 → 后端决定返回。前端永不直连任何微服务;后端承担全部鉴权与适配负担,微服务保持简单。**
- Agent 线的 external_api.md 明言"user_id/pet_id 由**可信上游**传入,不做 JWT/属主校验"——**该可信上游 = 本后端**。Agent 线无需改动其 API。

## 1. 拓扑
```
App(JWT) ──POST /pets/{petId}/vet/turns──▶ 后端 BFF
                                             │ ① 验 JWT → 拿 user_id
                                             │ ② require_owned_pet(petId, user_id) → 属主校验(关 IDOR)
                                             │ ③ 从 DB 补 pet_info(species/breed/age/weight)——不信客户端自报
                                             │ ④ per-user 限流(成本闸)
                                             │ ⑤ 构造 AgentTurnRequest,注入可信 vet_context
                                             ▼
                              Agent POST /agent/turns(内网,App 够不到)
                                             │ 同步 JSON / SSE 事件流
                                             ▼
                              后端透传/整形 ──▶ App(同步 JSON 或 SSE)
```

## 2. 后端对 App 的端点(新增,纯我 lane)
| 端点 | 说明 |
|---|---|
| `POST /pets/{petId}/vet/turns` | 一轮兽医对话;`stream` 字段决定同步 JSON 还是 SSE。App 只传 `input`/`attachments`/`stream`/`idempotency_key`——**不传 user_id/session_id/pet_id/model/vet_context**(后端注入) |
| `GET /pets/{petId}/vet/sessions/{sessionId}`(可选) | 历史(若前端要);归 ConversationStore,后端代理 |

**后端注入的可信 vet_context**(客户端无权覆盖):
- `user_id` ← JWT subject;`pet_id` ← 路径 + 属主校验;`pet_info` ← DB(`master_pet`/绑定关系)。
- `session_id` ← **后端发放并管理(PM 拍板)**:后端在"新对话"发放、续聊复用 → 传 Agent;Agent 的 `ConversationStore` 按它存对话内容、`PetSessionPolicy` 校验"一 session 一宠"。后端=发放方,Agent=存储+校验方;App 不感知 session 机制。

## 3. 后端 → Agent 调用(后端去适配 external_api.md 现状)
- **传输 = HTTP(Agent 线 2026-07-17 明确要求,与本契约一致)**。同步请求/响应 + `stream=true` SSE。**注意:HTTP 直连无 Redis 解耦退路(与情绪翻译不同),故承重墙①(网络可达)升为不可谈判硬门槛——连不上=功能不存在。** 同步耦合的失败处理见 §5(上游超时 + 503/504 透传)。
- 调 `POST /agent/turns`(不用 `/openai/v1/responses` 兼容口);
- 透传 `request_id`/`trace_id`(接我 OTel traceparent 链:后端把 W3C traceparent 映射到 agent 的 X-Trace-ID / trace_id);
- `metadata` 只带无害 client 信息;**绝不**透传客户端的 metadata 里任何控制字段(external_api §3.2/§5.3 也禁,双保险)。

## 4. SSE 透传(stream=true)——PM 拍板:默认 SSE 直透 + 同步兜底
- **默认 SSE 直透**(医疗长回复,流式体验;Agent API 本为 SSE 设计,含 reasoning_display 渐显);**保留 `stream=false` 同步兜底**(SSE 不稳环境降级,Agent 两种都支持,后端零成本兼容)。
- 后端用 httpx 流式读 agent SSE → FastAPI StreamingResponse 逐事件写回 App,**不缓冲整轮再发**(和 agent §4.6 一致);
- ⚠️**部署硬注意**:网关(nginx)对 `/pets/{petId}/vet/turns` 路由**必须 `proxy_buffering off`**,否则 nginx 攒住 SSE 不吐 → 流式失效退化成"最后一次性到达"。(infra action)
- 忠实转发 `turn.*`/`segment.*`/`reasoning_display.*`/`heartbeat`,**不改写/不总结/不裁剪**(reasoning_display 已由 agent 下游确认可展示,后端只搬运);
- App 断连 → 后端关闭到 agent 的上游连接 + 记 `CLIENT_CANCELLED`,不回滚已发内容。

## 5. 安全(后端职责)
- JWT 必验(external_api 假设的"可信"由此兑现);属主校验用既有 `require_owned_pet`;
- **访问日志纪律照抄 agent §9**:可记 request_id/trace_id/user_id/pet_id/path/status/duration;**禁记**完整医疗输入、完整回复、reasoning 原文——医疗正文不落我普通日志;
- 错误信封对齐 agent §3.3(code/message/request_id/trace_id/details),后端把上游 503/504 如实透传(SERVICE_UNAVAILABLE/ORCHESTRATOR_TIMEOUT)。

## 6. 附件(后端中转上传,同 VGS/情绪翻译)
- App **不直传 agent**;走后端上传端点 → 后端存 OSS → 生成 `storage_ref` → 填进 AgentTurnRequest 的 `attachments[]`;
- **承重墙(§8-2)**:agent 下游(LabOcrService 等)要能读该 `storage_ref` 指向的 OSS——桶/RAM 权限归 infra,和情绪翻译同款,别重复踩;
- 后端只做元信息+大小+MIME 白名单校验,不做医学类型判定(agent §4.3 一致)。

## 7. 限流与幂等(PM 拍板:6/分 + 40/天,per-user)
- **per-user 限流**:用既有 Lua fail-closed 限流器(payment 线沉淀):**6 轮/分钟/用户**(防脚本爆刷,真人 3-4/分,留余量)+ **40 轮/天/用户**(成本闸,一次问诊 10-20 轮,够两场);触发 → 429 RATE_LIMITED;两值设 **env 可调**(拿真实遥测再调,不改代码);
- 带附件轮(OCR/图像)更贵,v1 不细分,将来可对"带附件轮"单设更严子限;(可选)加全局 kill-switch 上限作成本熔断;
- 幂等:透传 `idempotency_key`(无则用 request_id);整轮幂等判定归 agent 编排层,后端不自行重试整轮(agent §4.8 一致)。

## 8. 承重墙(待 Agent/Infra)——三待拍已全部拍板(见 §2/§4/§7)
- **承重墙①(端口已给:18081;剩一个绑定细节)**:Agent 服务 HTTP 端口 = **18081**。后端从容器内走 docker 网桥 `172.17.0.1:18081` 连。**硬要求:18081 必须绑 `0.0.0.0`**(如 device-management `0.0.0.0:18080`),**不能只绑 `127.0.0.1`**(loopback-only 则容器连不到,端口给了也白给);或让 Agent 容器与后端共享 docker 网络、按容器名连。**Agent 一部署,后端立即实测 `172.17.0.1:18081` 可达性**,通即联调。**禁用 audio-model 式独立网+loopback**(情绪翻译连不上的根因)。
- **承重墙②**:附件 OSS 桶/RAM(agent 下游读 storage_ref;infra,和情绪翻译同款,可能已具备)。
- ~~待拍③/④/⑤~~ **已拍板**:③ session_id=**后端发放**(§2)/④ 限流=**6/分+40/天 per-user**(§7)/⑤ 交互=**SSE 直透+同步兜底**(§4)。

## 9. 各线 action
- **Agent 线**:① **18081 绑 `0.0.0.0`**(或与后端共享 docker 网),部署后告我确认 → 我实测联调;② 确认 storage_ref 期望格式(OSS key?预签名 URL?);③ 确认 session_id 由后端发放、你方 ConversationStore 按它存+PetSessionPolicy 校验(§2);④ 保持 `/agent/turns` HTTP + SSE 现状即可,无需改造。
- **App 前端线**:① 改为调后端 `POST /pets/{petId}/vet/turns`(不再直连 agent)② 只传 input/attachments/stream/idempotency_key(**不传** user_id/session_id/pet_id/vet_context)③ 附件走后端上传端点 ④ 默认 SSE、必要时 stream=false 降级。
- **Infra**:① 网关对 vet 路由 `proxy_buffering off`(§4)② 附件 OSS 桶/RAM(承重墙②)。
- **PM**:三待拍已拍板,无剩余。

## Changelog
- 0.2.0 (2026-07-17):三待拍全拍板并写入正文——session=后端发放(§2)/限流=6分+40天 per-user env 可调(§7)/交互=SSE 直透+同步兜底(§4);端口=18081(承重墙①收窄为"绑 0.0.0.0"一个细节);新增 SSE 部署硬注意(nginx proxy_buffering off);各线 action 刷新。待 Agent 部署即联调,后端 BFF 纯我 lane。
- 0.1.0-draft (2026-07-17):首版。宪法"前端→后端→微服务"落到兽医 Agent;后端 BFF 端点+vet_context 注入(关 IDOR)+SSE 透传+附件中转+限流;两承重墙(网络可达/附件桶)+三待拍(session/限流数值/交互模式)。

# RESTful OpenAPI (Swagger) 接口设计规范文档

## 1. 核心设计原则

为确保 API 契约的严谨性、向前兼容性及机器可读性，所有基于 OpenAPI (OAS 3.0+) 规范的接口设计必须遵循以下基础原则：

1. **资源导向 (Resource-Oriented)：** URI 仅代表资源实体（名词），动作由 HTTP Method（动词）表达。
2. **严谨的数据类型 (Strict Typing)：** Schema 定义必须具备明确的类型界定。基于强类型校验的后端架构（如借助 Pydantic 等结构化类型工具），OpenAPI 规范不仅是文档，更是数据校验的物理边界。
3. **向前兼容 (Forward Compatibility)：** 禁止对已发布接口进行破坏性变更（如删除字段、修改字段类型、增加必填入参），如有必要，需通过版本迭代推进。
4. **去语境化 (Context-Free)：** 接口定义应具备独立自明性，`summary` 和 `description` 必须清晰界定入参限制与业务语义。

---

## 2. 基础路径与版本管控 (Base Path & Versioning)

### 2.1 路径结构

标准的基础路径格式如下：
`/{网关前缀 (可选)}/{服务上下文}/{API版本}/{资源集}`

* **服务上下文：** 标识归属的微服务或中台组件。
* **版本控制：** 采用 URI 路径版本控制，仅保留大版本号（如 `v1`, `v2`）。小版本迭代必须向后兼容，不体现在 URI 中。

**规范示例：**

* `[https://api.domain.com/aigc-core/v1/agents](https://api.domain.com/aigc-core/v1/agents)`
* `[https://api.domain.com/tenant-center/v2/workspaces](https://api.domain.com/tenant-center/v2/workspaces)`

---

## 3. 资源路径设计 (Resource URI Design)

### 3.1 命名规范

* **名词复数：** 资源路径始终使用复数名词。
* **命名风格：** 统一使用 `kebab-case`（短横线分隔），禁用驼峰或下划线。
* **层级嵌套：** 资源嵌套层级不应超过三层。若关系过于复杂，应通过查询参数（Query Parameters）进行过滤。

**规范示例：**

* 正例：`GET /v1/agents/{agent_id}/configurations`
* 反例：`GET /v1/agent/{agent_id}/getConfigurations` （混入动词，且为单数）
* 反例：`GET /v1/users/{user_id}/orders/{order_id}/items/{item_id}` （层级过深）

### 3.2 动作抽象 (Actions)

当某些业务行为难以映射为标准 CRUD 时，允许在资源后附加特定动作，动作前需增加 `:` 或 `/` 隔离，并使用 POST 方法。

* 正例：`POST /v1/agents/{agent_id}/:deploy`

---

## 4. HTTP 动词与状态码映射

### 4.1 动词约束

| 方法 | 核心职责 | 幂等性 | 备注 |
| --- | --- | --- | --- |
| **GET** | 获取资源或资源列表 | 是 | 严禁在 GET 请求中携带 Request Body。 |
| **POST** | 创建新资源或执行特定操作 | 否 | 创建成功需返回 `201 Created` 及资源标识。 |
| **PUT** | 全量替换资源 | 是 | 需提供资源的完整表现层，未提供的字段应被置空。 |
| **PATCH** | 局部更新资源 | 否 | 仅更新报文中提供的字段。 |
| **DELETE** | 删除资源 | 是 | 软删除或物理删除，对客户端透明。 |

### 4.2 核心状态码 (Status Codes)

接口响应必须使用标准的 HTTP 状态码，禁止全部返回 `200` 然后在报文体中定义自定义错误。

* **200 OK：** 请求成功。
* **201 Created：** 资源创建成功。
* **204 No Content：** 请求成功执行，但无数据返回（常用于 DELETE 或 PUT）。
* **400 Bad Request：** 客户端输入错误（包含 Schema 校验失败）。
* **401 Unauthorized：** 身份认证失败或未提供凭证。
* **403 Forbidden：** 认证通过，但无权访问该资源或无权操作该租户的数据。
* **404 Not Found：** 目标资源不存在。
* **429 Too Many Requests：** 触发接口限流。
* **500 Internal Server Error：** 服务端内部异常。

---

## 5. 企业级架构扩展参数规范

鉴于企业级多租户及复杂业务系统的诉求，公共参数及上下文隔离需遵循严格标准。

### 5.1 身份与多租户隔离 (Authentication & Multi-Tenancy)

必须在 OpenAPI 的 `securitySchemes` 和 `parameters` 中明确定义隔离机制。

* `Authorization`: 承载 OAuth2 令牌或 JWT，用于身份认证。
* `X-Tenant-ID`: 对于多租户平台，跨租户操作边界需通过头部标明资源归属的租户标识。

### 5.2 列表分页规范 (Pagination)

对于返回集合的 GET 接口，必须引入分页参数。

* `page` (Query, Integer): 当前页码，默认 `1`。
* `size` (Query, Integer): 每页数量，默认 `20`，最大限制须在 Schema 中明确定义（如 `maximum: 100`）。

返回体标准结构：

```yaml
type: object
properties:
  total:
    type: integer
    description: "记录总数"
  items:
    type: array
    items:
      $ref: '#/components/schemas/TargetResource'

```

---

## 6. 异常报文结构 (Error Schema)

当 HTTP 状态码 >= 400 时，响应报文必须遵循统一的异常结构（推荐参考 RFC 7807 Problem Details 规范的简化版），以便上游系统统一拦截处理。

```yaml
components:
  schemas:
    ErrorResponse:
      type: object
      required:
        - code
        - message
      properties:
        code:
          type: string
          description: "内部业务错误码，如 'AGENT_NOT_FOUND'"
        message:
          type: string
          description: "对研发友好的错误原因描述"
        details:
          type: array
          description: "详细错误信息，如字段级别的结构校验失败明细"
          items:
            type: object
            properties:
              field:
                type: string
              reason:
                type: string

```

---

## 7. OpenAPI 文件编写红线 (Compliance Checklist)

在产出最终的 YAML/JSON 规范文件时，必须通过以下合规检查：

1. **Operation ID 唯一性：** 每个 API 端点的 `operationId` 必须全局唯一，且遵循 `<verb><Resource>` 格式（例：`listAgents`, `createWorkspace`），这是自动生成高质量 Client SDK 的基石。
2. **Schema 必须内聚：** 所有复杂的请求和响应结构必须定义在 `components/schemas` 下，并在路径中使用 `$ref` 引用。严禁在 `paths` 节点内直接内联编写复杂的 `properties`。
3. **约束显式化：** 所有字符串必须提供 `maxLength` 或 `enum` 限制；数值必须提供 `minimum/maximum`；必填项必须在 `required` 数组中声明。
4. **安全定义前置：** 全局 `security` 策略必须在文档根节点声明，非公开接口需通过 OpenAPI 语法屏蔽或明确标注不需要 Token 的白名单（如 `{}`）。
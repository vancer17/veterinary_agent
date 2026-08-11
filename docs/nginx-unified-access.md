# Nginx 统一路径访问配置说明

## 概述

本文档说明如何通过统一的 80 端口和路径前缀访问 Veterinary Agent 的所有服务，无需配置多个域名。

---

## 访问路径规划

| 服务 | 访问路径 | 上游端口 | 说明 |
|------|---------|---------|------|
| **Mem0 Dashboard** | `http://47.97.19.58/` | 3001 | 主入口，记忆可视化管理界面 |
| **LiteLLM** | `http://47.97.19.58/litellm/` | 4000 | 模型网关服务与 Admin UI |
| **Mem0 API** | `http://47.97.19.58/mem0/` | 8001 | 记忆管理 REST API |
| **Mem0 API (Dashboard)** | `http://47.97.19.58/api/mem0/` | 8001 | Dashboard 专用，已配置 CORS |

---

## 配置文件变更

### 1. 新增统一配置文件

**文件路径**: `docker/nginx/vhost/vet-agent.conf`

**关键特性**:
- 统一使用 80 端口监听所有请求
- `server_name _` 匹配所有域名与 IP 地址
- 通过 `location` 路径前缀路由到不同服务
- 保留 Dashboard 访问 Mem0 API 的 CORS 配置

### 2. 旧配置文件（可删除）

以下三个独立域名配置文件已被统一配置替代：
- `docker/nginx/vhost/litellm.vet-agent.conf`
- `docker/nginx/vhost/mem0.vet-agent.conf`
- `docker/nginx/vhost/mem0-dashboard.vet-agent.conf`

**建议操作**: 将旧配置文件重命名为 `.bak` 后缀备份，或直接删除。

---

## 部署步骤

### 步骤 1: 同步配置文件到服务器

```bash
# 方法一：使用 rsync 同步整个 nginx 目录
rsync -avz --exclude='*.bak' \
  docker/nginx/vhost/vet-agent.conf \
  devlop@47.97.19.58:/www/server/panel/vhost/nginx/

# 方法二：使用 scp 单独上传
scp -i ~/.ssh/AlibabaCloudLinux -P 22 \
  docker/nginx/vhost/vet-agent.conf \
  devlop@47.97.19.58:/tmp/ && \
ssh -i ~/.ssh/AlibabaCloudLinux devlop@47.97.19.58 -p 22 \
  "sudo mv /tmp/vet-agent.conf /www/server/panel/vhost/nginx/"
```

### 步骤 2: 备份并禁用旧配置文件

```bash
ssh -i ~/.ssh/AlibabaCloudLinux devlop@47.97.19.58 -p 22 << 'EOF'
cd /www/server/panel/vhost/nginx/
sudo mv litellm.vet-agent.conf litellm.vet-agent.conf.bak
sudo mv mem0.vet-agent.conf mem0.vet-agent.conf.bak
sudo mv mem0-dashboard.vet-agent.conf mem0-dashboard.vet-agent.conf.bak
EOF
```

### 步骤 3: 验证 Nginx 配置语法

```bash
ssh -i ~/.ssh/AlibabaCloudLinux devlop@47.97.19.58 -p 22 \
  "sudo /www/server/nginx/sbin/nginx -t"
```

**预期输出**:
```
nginx: the configuration file /www/server/nginx/conf/nginx.conf syntax is ok
nginx: configuration file /www/server/nginx/conf/nginx.conf test is successful
```

### 步骤 4: 重载 Nginx 配置

```bash
ssh -i ~/.ssh/AlibabaCloudLinux devlop@47.97.19.58 -p 22 \
  "sudo /www/server/nginx/sbin/nginx -s reload"
```

---

## 功能验证

### 1. 验证 Mem0 Dashboard（主入口）

```bash
# 测试首页访问
curl -I http://47.97.19.58/

# 预期返回 200 或 302
```

**浏览器访问**: `http://47.97.19.58/`

### 2. 验证 LiteLLM 服务

```bash
# 测试健康检查
curl http://47.97.19.58/litellm/health

# 测试模型列表（需要认证）
curl http://47.97.19.58/litellm/v1/models \
  -H "Authorization: Bearer sk-your-master-key"
```

**浏览器访问 Admin UI**: `http://47.97.19.58/litellm/`

### 3. 验证 Mem0 API

```bash
# 测试 API 文档
curl -I http://47.97.19.58/mem0/docs

# 测试健康检查
curl http://47.97.19.58/mem0/health

# 测试记忆列表（需要认证）
curl http://47.97.19.58/mem0/memories \
  -H "Authorization: Bearer your-mem0-token"
```

**浏览器访问 API 文档**: `http://47.97.19.58/mem0/docs`

### 4. 验证跨域配置（Dashboard 访问 Mem0 API）

```bash
# 测试 CORS 响应头
curl -I -H "Origin: http://47.97.19.58" \
  http://47.97.19.58/api/mem0/memories

# 预期包含以下响应头：
# Access-Control-Allow-Origin: http://47.97.19.58
# Access-Control-Allow-Credentials: true

# 测试 OPTIONS 预检请求
curl -X OPTIONS -I \
  -H "Origin: http://47.97.19.58" \
  -H "Access-Control-Request-Method: POST" \
  http://47.97.19.58/api/mem0/memories

# 预期返回 HTTP 204
```

---

## 路径重写说明

### LiteLLM 路径重写

```nginx
location /litellm/ {
    proxy_pass http://127.0.0.1:4000/;
}
```

**示例**:
- 请求: `http://47.97.19.58/litellm/v1/models`
- 转发: `http://127.0.0.1:4000/v1/models`

### Mem0 路径重写

```nginx
location /mem0/ {
    proxy_pass http://127.0.0.1:8001/;
}
```

**示例**:
- 请求: `http://47.97.19.58/mem0/docs`
- 转发: `http://127.0.0.1:8001/docs`

### Dashboard 访问 Mem0 API 路径重写

```nginx
location /api/mem0/ {
    proxy_pass http://127.0.0.1:8001/;
}
```

**示例**:
- 请求: `http://47.97.19.58/api/mem0/memories`
- 转发: `http://127.0.0.1:8001/memories`

---

## 注意事项

### 1. 路径前缀要求

所有服务访问时**必须带尾随斜杠** `/`，否则会出现路径错误：

✅ **正确**: `http://47.97.19.58/litellm/`  
❌ **错误**: `http://47.97.19.58/litellm` (会被重定向)

### 2. Dashboard 配置文件

`docker/mem0-dashboard/application.yml` 中的 `public_api_url` 已正确配置为 `/api/mem0`，无需修改：

```yaml
dashboard:
  public_api_url: /api/mem0      # 浏览器访问 Mem0 的同源路径
  internal_api_url: http://mem0:8000  # 服务端通过容器网络访问
```

### 3. 客户端 SDK 配置

如果使用 OpenAI SDK 或 LiteLLM SDK，需要修改 `base_url`：

```python
# LiteLLM SDK
from openai import OpenAI

client = OpenAI(
    base_url="http://47.97.19.58/litellm/v1",
    api_key="sk-your-litellm-key"
)

# Mem0 SDK
import mem0

mem0_client = mem0.MemoryClient(
    api_url="http://47.97.19.58/mem0",
    api_key="your-mem0-api-key"
)
```

### 4. 日志文件位置

统一配置后的日志文件：
- 访问日志: `/www/wwwlogs/vet-agent-access.log`
- 错误日志: `/www/wwwlogs/vet-agent-error.log`

旧配置的日志文件（可归档）：
- `/www/wwwlogs/litellm-access.log`
- `/www/wwwlogs/mem0-access.log`
- `/www/wwwlogs/mem0-dashboard-access.log`

---

## 故障排查

### 问题 1: 404 Not Found

**原因**: 路径前缀不匹配或缺少尾随斜杠。

**解决**:
```bash
# 检查 Nginx 配置是否正确加载
sudo /www/server/nginx/sbin/nginx -T | grep -A 5 "location /litellm/"

# 验证上游服务是否正常
curl http://127.0.0.1:4000/health
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:3001/api/health
```

### 问题 2: CORS 错误

**原因**: `/api/mem0/` 路径的 CORS 配置未生效。

**解决**:
```bash
# 检查 CORS 响应头
curl -I -H "Origin: http://47.97.19.58" \
  http://47.97.19.58/api/mem0/memories | grep -i access-control

# 如果没有响应头，检查 Nginx 配置
sudo /www/server/nginx/sbin/nginx -T | grep -A 20 "location /api/mem0/"
```

### 问题 3: 502 Bad Gateway

**原因**: 上游服务未启动或端口映射错误。

**解决**:
```bash
# 检查容器运行状态
docker ps | grep -E "litellm|mem0|mem0-dashboard"

# 检查端口监听
ss -tlnp | grep -E "4000|8001|3001"

# 查看容器日志
docker logs litellm
docker logs mem0
docker logs mem0-dashboard
```

---

## 安全加固建议

### 1. 添加 IP 白名单（可选）

```nginx
# 在 server 块顶部添加
geo $allowed_ip {
    default 0;
    你的办公室IP/32 1;
    你的家庭IP/32 1;
}

server {
    if ($allowed_ip = 0) {
        return 403;
    }
    # ... 其他配置
}
```

### 2. 启用基本认证（可选）

```bash
# 生成密码文件
sudo htpasswd -c /www/server/nginx/.htpasswd admin

# 在需要保护的 location 中添加
location /litellm/ {
    auth_basic "Restricted Access";
    auth_basic_user_file /www/server/nginx/.htpasswd;
    proxy_pass http://127.0.0.1:4000/;
}
```

### 3. 限流配置（推荐）

```nginx
# 在 http 块中添加
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/s;

# 在 location 中应用
location /litellm/ {
    limit_req zone=api_limit burst=20 nodelay;
    proxy_pass http://127.0.0.1:4000/;
}
```

---

## 后续升级到 HTTPS

当需要启用 HTTPS 时，推荐使用 Let's Encrypt 免费证书：

```bash
# 1. 安装 certbot
sudo yum install certbot python3-certbot-nginx

# 2. 配置域名（需要先配置 DNS 解析）
sudo certbot --nginx -d your-domain.com

# 3. 自动续期
sudo crontab -e
0 0 1 * * /usr/bin/certbot renew --quiet
```

---

## 总结

✅ **已完成的配置**:
1. 创建统一 Nginx 配置文件 `vet-agent.conf`
2. 通过路径前缀路由三个核心服务
3. 保留 Dashboard 访问 Mem0 API 的 CORS 配置
4. 统一日志文件便于管理

✅ **访问方式**:
- Dashboard 主入口: `http://47.97.19.58/`
- LiteLLM 服务: `http://47.97.19.58/litellm/`
- Mem0 API: `http://47.97.19.58/mem0/`

📌 **下一步操作**:
1. 部署新配置到服务器
2. 禁用旧的域名配置文件
3. 验证所有服务访问正常
4. 更新客户端 SDK 的 `base_url` 配置

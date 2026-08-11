# Veterinary Agent Nginx 配置

## 概述

本目录包含 Veterinary Agent 项目的 Nginx 统一入口配置，通过路径前缀路由到不同的后端服务。

## 架构设计

**统一访问入口**：`http://vet-agent.local`（需配置 hosts 解析到服务器 IP）

**服务路由规则**：

| 访问路径 | 后端服务 | 用途 |
|---------|---------|------|
| `/` | Mem0 Dashboard (端口 3001) | 主界面入口 |
| `/litellm/*` | LiteLLM Proxy (端口 4000) | 模型代理服务 |
| `/mem0/*` | Mem0 API (端口 8001) | 记忆管理 API |
| `/api/mem0/*` | Mem0 API (端口 8001) | Dashboard 专用（CORS 已配置） |

## 快速开始

### 1. 本地添加 hosts 解析

```bash
# Linux/macOS
echo "47.97.19.58 vet-agent.local" | sudo tee -a /etc/hosts

# Windows (以管理员身份编辑 C:\Windows\System32\drivers\etc\hosts)
47.97.19.58 vet-agent.local
```

### 2. 访问服务

```bash
# Mem0 Dashboard（主入口）
http://vet-agent.local/

# LiteLLM Admin UI
http://vet-agent.local/litellm/ui

# Mem0 API 文档
http://vet-agent.local/mem0/docs

# LiteLLM API（需要 Authorization Header）
curl http://vet-agent.local/litellm/v1/models \
  -H "Authorization: Bearer sk-your-key"
```

## 配置文件结构

```
docker/nginx/
├── README.md                          # 本文档
├── vhost/
│   └── vet-agent.conf                 # 统一入口配置
└── mime.types                         # MIME 类型定义（可选）
```

## 部署到服务器

### 方式一：手动部署（推荐）

```bash
# 1. 上传配置文件
scp docker/nginx/vhost/vet-agent.conf \
  user@47.97.19.58:/tmp/

# 2. SSH 登录服务器
ssh user@47.97.19.58

# 3. 覆盖配置文件
sudo mv /tmp/vet-agent.conf /www/server/panel/vhost/nginx/

# 4. 验证配置语法
sudo /www/server/nginx/sbin/nginx -t

# 5. 重载 Nginx
sudo /www/server/nginx/sbin/nginx -s reload
```

### 方式二：使用部署脚本

```bash
# 在本地执行
cd /home/vancer17/veterinary_agent
./docker/nginx/deploy.sh
```

## 配置详解

### LiteLLM 路由配置

```nginx
location /litellm/ {
    proxy_pass http://127.0.0.1:4000/;
    proxy_buffering off;           # 关闭缓冲支持 SSE 流
    proxy_cache off;               # 关闭缓存
    proxy_read_timeout 300s;       # 模型推理耗时较长
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";  # WebSocket 支持
}
```

**特性**：
- ✅ SSE 流式响应支持
- ✅ WebSocket 连接支持
- ✅ 健康检查与指标端点独立配置
- ✅ 300 秒超时适配模型推理

### Mem0 路由配置

```nginx
location /mem0/ {
    proxy_pass http://127.0.0.1:8001/;
    proxy_read_timeout 120s;
}

location /api/mem0/ {
    proxy_pass http://127.0.0.1:8001/;
    
    # CORS 配置（Dashboard 专用）
    add_header Access-Control-Allow-Origin http://vet-agent.local always;
    add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, PATCH, OPTIONS" always;
    add_header Access-Control-Allow-Headers "Content-Type, Authorization, X-Requested-With" always;
    add_header Access-Control-Allow-Credentials true always;
    add_header Access-Control-Max-Age 3600 always;
    
    # OPTIONS 预检请求处理
    if ($request_method = 'OPTIONS') {
        return 204;
    }
}
```

**特性**：
- ✅ 两个独立入口：`/mem0/` 与 `/api/mem0/`
- ✅ `/api/mem0/` 启用完整 CORS 配置支持 Dashboard 跨域请求
- ✅ OPTIONS 预检请求快速响应

### Mem0 Dashboard 路由配置

```nginx
location / {
    proxy_pass http://127.0.0.1:3001;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";  # WebSocket 支持（HMR）
}
```

**特性**：
- ✅ Next.js 热更新支持
- ✅ 作为默认入口（访问根路径直接进入 Dashboard）

## 验证部署

```bash
# 1. 验证 DNS 解析
ping vet-agent.local

# 2. 验证 HTTP 响应
curl -I http://vet-agent.local/

# 3. 验证各服务路径
curl -I http://vet-agent.local/litellm/health
curl -I http://vet-agent.local/mem0/docs
curl -I http://vet-agent.local/api/mem0/docs

# 4. 验证 LiteLLM API（需要有效的 API Key）
curl http://vet-agent.local/litellm/v1/models \
  -H "Authorization: Bearer sk-your-key"
```

## 常见问题

### 1. 访问返回 404

**原因**：hosts 解析未生效或 Nginx 配置未加载

**解决**：
```bash
# 检查 hosts 文件
cat /etc/hosts | grep vet-agent.local

# 检查 Nginx 配置
sudo /www/server/nginx/sbin/nginx -T | grep vet-agent.local

# 重载 Nginx
sudo /www/server/nginx/sbin/nginx -s reload
```

### 2. LiteLLM 返回 401 Unauthorized

**原因**：正常行为，LiteLLM 的 API 端点需要认证

**解决**：
- Admin UI 访问：`http://vet-agent.local/litellm/ui`（需要 `LITELLM_MASTER_KEY`）
- API 调用：需要先通过 `/key/generate` 创建 virtual key

### 3. Mem0 Dashboard 无法连接 API

**原因**：Dashboard 的 `public_api_url` 配置不正确

**解决**：
```bash
# 检查 Mem0 Dashboard 配置
cat docker/mem0-dashboard/.env | grep public_api_url
# 应该显示：public_api_url=/api/mem0
```

### 4. 服务器上已存在其他站点监听 80 端口

**原因**：Nginx 配置中多个 `server_name` 冲突

**解决**：
- 当前配置使用 `server_name vet-agent.local` 避免与 IP 直接访问冲突
- 如需更改域名，修改 [vhost/vet-agent.conf](vhost/vet-agent.conf) 中的 `server_name` 指令

## 生产环境建议

### 1. 启用 HTTPS（强烈推荐）

```bash
# 使用 Let's Encrypt 免费证书
certbot --nginx -d vet-agent.yourdomain.com
```

### 2. 配置真实域名

将 `vet-agent.local` 替换为你的域名：

```nginx
server {
    listen 80;
    server_name vet-agent.yourdomain.com;
    # ...
}
```

并在 DNS 解析商处添加 A 记录：
```
vet-agent.yourdomain.com    A    47.97.19.58
```

### 3. IP 白名单（可选）

限制访问来源：

```nginx
location /litellm/ {
    allow 1.2.3.4;        # 办公室 IP
    allow 5.6.7.8/24;     # VPN 网段
    deny all;
    
    proxy_pass http://127.0.0.1:4000/;
    # ...
}
```

### 4. 访问日志分离

```nginx
server {
    access_log /www/wwwlogs/vet-agent-access.log;
    error_log /www/wwwlogs/vet-agent-error.log;
    # ...
}
```

## 端口映射总览

| 服务 | 容器端口 | 宿主机端口 | Nginx 路径 |
|------|---------|-----------|-----------|
| Mem0 Dashboard | 3000 | 3001 | `/` |
| LiteLLM Proxy | 4000 | 4000 | `/litellm/*` |
| Mem0 API | 8000 | 8001 | `/mem0/*`, `/api/mem0/*` |
| PostgreSQL | 5432 | 5432 | （内部服务，不对外） |
| Nginx | 80 | 80 | 统一入口 |

## 技术栈

- **Nginx**: 1.22+ (宝塔面板默认版本)
- **LiteLLM**: 最新版
- **Mem0**: 最新版
- **Mem0 Dashboard**: Next.js 应用

## 维护日志

- `2026-08-11`: 初始化统一路径路由配置，移除多域名方案
- 后续更新请在此处补充

## 联系方式

如有问题，请联系项目维护者或查看项目文档。

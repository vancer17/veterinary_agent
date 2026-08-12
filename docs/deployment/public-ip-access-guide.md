# 公网 IPv4 访问指南

## 📋 服务器信息

**服务器 IP**：`47.97.19.58`  
**监听端口**：`80` (HTTP)  
**Nginx 状态**：✅ 运行中

---

## 🌐 服务访问地址

### Mem0 Dashboard（记忆管理界面）
```
http://47.97.19.58/
```
- **功能**：可视化记忆管理、用户管理、记忆查询
- **首次访问**：会重定向到 `/setup` 进行初始化配置

### LiteLLM Admin UI（模型网关管理）
```
http://47.97.19.58/litellm/ui
```
- **功能**：模型配置、API Key 管理、用量监控
- **认证**：需要 Master Key 或 Virtual Key

### LiteLLM API（模型调用接口）
```
http://47.97.19.58/litellm/v1/chat/completions
http://47.97.19.58/litellm/v1/embeddings
```
- **功能**：OpenAI 兼容的模型调用接口
- **认证**：需要在 Authorization 头中提供 API Key

### Mem0 API 文档（记忆服务 API）
```
http://47.97.19.58/mem0/docs
```
- **功能**：交互式 API 文档（Swagger UI）
- **OpenAPI 规范**：`http://47.97.19.58/mem0/openapi.json`

### Mem0 API（Dashboard 专用，支持 CORS）
```
http://47.97.19.58/api/mem0/*
```
- **功能**：Dashboard 前端调用的 Mem0 后端接口
- **特性**：已配置 CORS，支持跨域请求

---

## ✅ 服务验证结果

| 服务 | 访问地址 | 状态 | 响应 |
|------|---------|------|------|
| **Mem0 Dashboard** | `http://47.97.19.58/` | ✅ 正常 | 307 重定向到 /setup |
| **LiteLLM Health** | `http://47.97.19.58/litellm/health` | ✅ 正常 | 401 需要认证 |
| **Mem0 API Docs** | `http://47.97.19.58/mem0/docs` | ✅ 正常 | 200 返回文档页面 |

---

## 🔧 配置详情

### Nginx 配置文件
- **主配置**：`/www/server/panel/vhost/nginx/vet-agent.conf`
- **监听端口**：80
- **Server Name**：`47.97.19.58`

### 路径路由规则
```
/                   → Mem0 Dashboard (端口 3001)
/litellm/*          → LiteLLM Proxy (端口 4000)
/mem0/*             → Mem0 API (端口 8001)
/api/mem0/*         → Mem0 API with CORS (端口 8001)
```

### 已解决的冲突
- ✅ 从 `www.zczxpet.com.conf` 中移除了 IP 地址 `47.97.19.58`
- ✅ 备份文件：`www.zczxpet.com.conf.bak-20260811-223001`
- ✅ 杀死了旧的 Nginx 进程并重新启动服务

---

## 🔒 安全建议

### 1. API Key 管理
- **LiteLLM Master Key**：仅用于管理操作，不要暴露给客户端
- **Virtual Key**：为每个应用创建独立的 Virtual Key，设置合理的预算限制

### 2. 防火墙配置
当前仅开放 80 端口，其他服务端口（3001, 4000, 8001）仅在服务器内部访问：
```bash
# 查看当前开放端口
sudo firewall-cmd --list-ports
```

### 3. HTTPS 配置（推荐）
生产环境建议配置 SSL 证书：
```bash
# 使用 Let's Encrypt 免费证书
# 1. 配置域名 DNS A 记录指向 47.97.19.58
# 2. 使用 certbot 自动申请证书
sudo certbot --nginx -d yourdomain.com
```

---

## 🛠️ 故障排查

### 问题 1：无法访问服务
```bash
# SSH 登录服务器
ssh devlop@47.97.19.58 -i ~/.ssh/AlibabaCloudLinux

# 检查 Nginx 状态
sudo systemctl status nginx

# 如果服务未运行，启动服务
sudo systemctl start nginx

# 重新加载配置
sudo nginx -s reload
```

### 问题 2：配置冲突
```bash
# 测试 Nginx 配置
sudo nginx -t

# 查看错误日志
sudo tail -f /www/wwwlogs/vet-agent-error.log
```

### 问题 3：端口被占用
```bash
# 查看 80 端口占用情况
sudo netstat -tlnp | grep :80

# 或使用 ss
sudo ss -tlnp | grep :80
```

---

## 📝 维护记录

### 2026-08-11
- ✅ 配置 IP 地址访问：`47.97.19.58`
- ✅ 解决 Nginx 配置冲突（移除 `www.zczxpet.com.conf` 中的 IP）
- ✅ 重启 Nginx 服务
- ✅ 验证所有服务访问正常

---

## 📞 联系信息

如有问题，请参考：
- **项目文档**：`/home/vancer17/veterinary_agent/docs/`
- **架构设计**：`docs/architecture/agent-middleware-migration-plan.md`
- **Docker 配置**：`docker/compose.yml`

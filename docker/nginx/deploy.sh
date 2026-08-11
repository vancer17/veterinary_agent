# =============================================================================
# 文件: docker/nginx/deploy.sh
# 作用: 部署 Nginx 配置文件到远程服务器的自动化脚本。
# 范围: 备份旧配置、上传新配置、验证语法并重载 Nginx 服务。
# 说明: 本脚本支持开发与生产环境；执行前需确保 SSH 密钥与目标服务器访问权限。
# =============================================================================

#!/usr/bin/env bash

set -euo pipefail

# =============================================================================
# 配置变量定义
# =============================================================================

# 远程服务器连接信息
REMOTE_HOST="47.97.19.58"
REMOTE_USER="devlop"
REMOTE_PORT="22"
SSH_KEY="/home/vancer17/.ssh/AlibabaCloudLinux"

# 远程服务器 Nginx 配置路径（宝塔面板默认路径）
REMOTE_NGINX_VHOST_DIR="/www/server/panel/vhost/nginx"
REMOTE_NGINX_CONF_DIR="/www/server/nginx/conf"

# 本地配置文件路径
LOCAL_VHOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/vhost" && pwd)"

# 备份目录命名规则：使用时间戳确保唯一性
BACKUP_TIMESTAMP=$(date +%Y%m%d-%H%M%S)
REMOTE_BACKUP_DIR="${REMOTE_NGINX_VHOST_DIR}/backup-${BACKUP_TIMESTAMP}"

# =============================================================================
# 日志与错误处理函数
# =============================================================================

log_info() {
    echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') - $*"
}

log_error() {
    echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $*" >&2
}

log_success() {
    echo "[SUCCESS] $(date '+%Y-%m-%d %H:%M:%S') - $*"
}

# =============================================================================
# SSH 远程命令执行函数
# =============================================================================

ssh_exec() {
    ssh -i "${SSH_KEY}" -p "${REMOTE_PORT}" "${REMOTE_USER}@${REMOTE_HOST}" "$@"
}

ssh_exec_sudo() {
    ssh -i "${SSH_KEY}" -p "${REMOTE_PORT}" "${REMOTE_USER}@${REMOTE_HOST}" "sudo $*"
}

# =============================================================================
# 主流程函数
# =============================================================================

# 检查本地配置文件是否存在
check_local_files() {
    log_info "检查本地配置文件..."

    if [[ ! -d "${LOCAL_VHOST_DIR}" ]]; then
        log_error "本地配置目录不存在: ${LOCAL_VHOST_DIR}"
        exit 1
    fi

    local conf_count
    conf_count=$(find "${LOCAL_VHOST_DIR}" -name "*.conf" -type f | wc -l)

    if [[ ${conf_count} -eq 0 ]]; then
        log_error "本地配置目录中未找到 .conf 文件"
        exit 1
    fi

    log_success "找到 ${conf_count} 个配置文件"
}

# 备份远程服务器现有配置
backup_remote_configs() {
    log_info "备份远程服务器现有配置到 ${REMOTE_BACKUP_DIR}..."

    ssh_exec_sudo "mkdir -p ${REMOTE_BACKUP_DIR}"

    # 备份所有 vet-agent 相关配置文件
    ssh_exec_sudo "find ${REMOTE_NGINX_VHOST_DIR} -maxdepth 1 -name '*vet-agent*.conf' -type f -exec cp {} ${REMOTE_BACKUP_DIR}/ \; 2>/dev/null || true"

    local backup_count
    backup_count=$(ssh_exec_sudo "ls -1 ${REMOTE_BACKUP_DIR}/*.conf 2>/dev/null | wc -l" || echo "0")

    if [[ ${backup_count} -gt 0 ]]; then
        log_success "已备份 ${backup_count} 个配置文件"
    else
        log_info "未找到需要备份的配置文件（首次部署）"
    fi
}

# 上传新配置文件到远程服务器
upload_new_configs() {
    log_info "上传新配置文件到远程服务器..."

    local uploaded=0

    for conf_file in "${LOCAL_VHOST_DIR}"/*.conf; do
        if [[ -f "${conf_file}" ]]; then
            local filename
            filename=$(basename "${conf_file}")

            log_info "上传 ${filename}..."

            # 先上传到临时目录（devlop 用户有写权限）
            scp -i "${SSH_KEY}" -P "${REMOTE_PORT}" "${conf_file}" \
                "${REMOTE_USER}@${REMOTE_HOST}:/tmp/${filename}"

            # 再通过 sudo 移动到目标目录并设置权限
            ssh_exec_sudo "mv /tmp/${filename} ${REMOTE_NGINX_VHOST_DIR}/${filename}"
            ssh_exec_sudo "chown root:root ${REMOTE_NGINX_VHOST_DIR}/${filename}"
            ssh_exec_sudo "chmod 644 ${REMOTE_NGINX_VHOST_DIR}/${filename}"

            ((uploaded++))
        fi
    done

    log_success "已上传 ${uploaded} 个配置文件"
}

# 验证 Nginx 配置语法
validate_nginx_config() {
    log_info "验证 Nginx 配置语法..."

    if ssh_exec_sudo "nginx -t"; then
        log_success "Nginx 配置语法验证通过"
        return 0
    else
        log_error "Nginx 配置语法验证失败"
        log_error "正在回滚配置..."
        rollback_configs
        exit 1
    fi
}

# 回滚配置文件
rollback_configs() {
    log_info "回滚到备份配置..."

    ssh_exec_sudo "rm -f ${REMOTE_NGINX_VHOST_DIR}/*vet-agent*.conf"
    ssh_exec_sudo "cp ${REMOTE_BACKUP_DIR}/*.conf ${REMOTE_NGINX_VHOST_DIR}/ 2>/dev/null || true"

    log_success "配置已回滚"
}

# 重载 Nginx 服务
reload_nginx() {
    log_info "重载 Nginx 服务..."

    if ssh_exec_sudo "systemctl reload nginx"; then
        log_success "Nginx 服务重载成功"
    elif ssh_exec_sudo "systemctl start nginx"; then
        log_success "Nginx 服务启动成功"
    else
        log_error "Nginx 服务重载失败"
        exit 1
    fi
}

# 显示部署后的状态信息
show_deployment_status() {
    log_info "部署状态信息："
    echo ""
    echo "已部署的配置文件："
    ssh_exec_sudo "ls -lh ${REMOTE_NGINX_VHOST_DIR}/*vet-agent*.conf"
    echo ""
    echo "Nginx 服务状态："
    ssh_exec_sudo "systemctl status nginx --no-pager -l | head -20"
    echo ""
    echo "访问地址（需在客户端 /etc/hosts 中配置域名解析）："
    echo "  - LiteLLM:        http://litellm.vet-agent.local"
    echo "  - Mem0 API:       http://mem0.vet-agent.local"
    echo "  - Mem0 Dashboard: http://mem0-dashboard.vet-agent.local"
    echo ""
    echo "备份目录: ${REMOTE_BACKUP_DIR}"
}

# =============================================================================
# 脚本入口
# =============================================================================

main() {
    log_info "开始部署 Nginx 配置..."
    echo ""

    check_local_files
    backup_remote_configs
    upload_new_configs
    validate_nginx_config
    reload_nginx
    show_deployment_status

    echo ""
    log_success "Nginx 配置部署完成"
}

main "$@"

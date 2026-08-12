# =============================================================================
# 文件: docker/opa/policies/system_log.rego
# 作用: 定义 OPA decision log 的基础脱敏与丢弃策略。
# 范围: 仅覆盖常见密钥、令牌和鉴权头字段，不处理具体业务数据治理策略。
# 说明: 后续业务策略输入稳定后，应按临床与隐私分级补充更精确的日志脱敏规则。
# =============================================================================

package system.log

import rego.v1

# 当前默认不丢弃决策日志，保证策略裁决可审计。
default drop := false

# 常见敏感输入字段路径。不存在的路径会被 OPA 忽略，不会影响正常日志生成。
sensitive_input_pointers := {
	"/input/api_key",
	"/input/password",
	"/input/token",
	"/input/access_token",
	"/input/refresh_token",
	"/input/authorization",
	"/input/headers/authorization",
	"/input/headers/x-api-key",
	"/input/openai_api_key",
	"/input/litellm_api_key",
	"/input/mem0_api_key",
}

# OPA decision log 插件会在写出日志前删除 mask 返回的 JSON Pointer。
mask contains pointer if {
	some pointer in sensitive_input_pointers
}

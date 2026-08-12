# =============================================================================
# 文件: docker/opa/policies/bootstrap.rego
# 作用: 提供 OPA 服务初始可用性与调用契约烟测策略。
# 范围: 仅允许 healthcheck 类输入通过，不承载身份、宠物、临床安全、记忆写入等业务策略。
# 说明: 后续正式业务策略应通过独立 package 或远程 bundle 接入，并替换 application.yml 的默认裁决路径。
# =============================================================================

package vet_agent.bootstrap

import rego.v1

# 默认拒绝，避免 OPA 尚未接入正式策略时产生宽松授权语义。
default allow := false

# healthcheck 输入仅用于验证 Data API 调用链路。
allow if {
	input.action == "healthcheck"
}

# 对外返回结构化裁决，保持后续业务策略接入时的响应形态稳定。
decision := {
	"allow": allow,
	"reason": reason,
}

# 默认原因明确表示当前尚未安装业务策略。
default reason := "OPA bootstrap policy default deny; business policies are not installed yet."

# healthcheck 通过原因用于冒烟测试断言。
reason := "OPA bootstrap policy allows healthcheck smoke requests." if {
	input.action == "healthcheck"
}

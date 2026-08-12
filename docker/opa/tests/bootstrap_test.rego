# =============================================================================
# 文件: docker/opa/tests/bootstrap_test.rego
# 作用: 验证 OPA bootstrap policy 与基础 decision log 脱敏策略。
# 范围: 仅覆盖容器初始策略可用性，不验证后续业务 Rego 策略。
# 说明: 该文件由 CI 镜像构建检查执行，不挂载到生产 OPA 策略目录。
# =============================================================================

package vet_agent.bootstrap_test

import data.system.log
import data.vet_agent.bootstrap
import rego.v1

# healthcheck 输入应允许通过，供 Data API 冒烟测试使用。
test_healthcheck_allowed if {
	bootstrap.allow with input as {"action": "healthcheck"}
}

# 未知动作必须保持默认拒绝，避免默认策略产生宽松授权。
test_unknown_action_denied if {
	not bootstrap.allow with input as {"action": "unknown"}
}

# 结构化裁决需要包含 allow 与 reason 字段，便于调用方稳定解析。
test_decision_contains_reason if {
	decision := bootstrap.decision with input as {"action": "healthcheck"}
	decision.allow == true
	decision.reason == "OPA bootstrap policy allows healthcheck smoke requests."
}

# 决策日志脱敏策略必须覆盖 Authorization 请求头路径。
test_decision_log_mask_contains_authorization_pointer if {
	"/input/headers/authorization" in log.mask
}

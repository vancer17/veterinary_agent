# =============================================================================
# 文件: docker/opa/policies/input_safety.rego
# 作用: 裁决兽医 Agent 基础输入安全候选动作。
# 范围: 仅消费应用侧提交的结构化候选，不扫描用户自然语言文本，不承载临床医学推理。
# 说明: 提示注入、输入完整性、未开放能力等候选由应用和检测器生成；OPA 只负责最终动作、原因和信号。
# =============================================================================

package vet_agent.input_safety

import rego.v1

# 默认放行空候选；应用侧仍会在启用 INPUT_SAFETY_POLICY_ALWAYS_CALL 时获得可审计裁决。
default action := "allow"

default allow := false

# 被标记为 blocked 的结构化候选一律阻断。
action := "block" if {
	some candidate in input.candidates
	candidate.severity == "blocked"
}

# 被标记为 urgent 的结构化候选在无阻断候选时升级。
action := "escalate" if {
	not blocked_candidate
	some candidate in input.candidates
	candidate.severity == "urgent"
}

# 仅 caution/info 候选进入观测。
action := "observe" if {
	count(input.candidates) > 0
	not blocked_candidate
	not urgent_candidate
}

blocked_candidate if {
	some candidate in input.candidates
	candidate.severity == "blocked"
}

urgent_candidate if {
	some candidate in input.candidates
	candidate.severity == "urgent"
}

# 对外结构化裁决结果。
decision := {
	"action": action,
	"allow": allow,
	"message": message,
	"reasons": reasons,
	"signals": signals,
}

allow if {
	action == "allow"
}

allow if {
	action == "observe"
}

allow if {
	action == "escalate"
}

message := "基础输入安全策略允许本轮继续进入 Agent 主链路。" if {
	action == "allow"
}

message := "基础输入安全策略记录候选并允许继续执行。" if {
	action == "observe"
}

message := "基础输入安全策略识别到需要优先处理的输入风险。" if {
	action == "escalate"
}

message := "当前输入未通过基础输入安全策略裁决。" if {
	action == "block"
}

reasons := [candidate.message | some candidate in input.candidates]

signals := [signal |
	some candidate in input.candidates
	severity := signal_severity(candidate)
	signal := {
		"code": candidate.code,
		"severity": severity,
		"message": candidate.message,
		"matched_terms": object.get(candidate, "matched_terms", []),
	}
]

signal_severity(candidate) := "blocked" if {
	action == "block"
	candidate.severity == "blocked"
}

signal_severity(candidate) := "urgent" if {
	action == "escalate"
	candidate.severity == "urgent"
}

signal_severity(candidate) := candidate.severity if {
	action != "block"
	action != "escalate"
}

signal_severity(candidate) := "caution" if {
	action == "block"
	candidate.severity != "blocked"
}

signal_severity(candidate) := "caution" if {
	action == "escalate"
	candidate.severity != "urgent"
}

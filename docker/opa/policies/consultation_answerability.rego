# =============================================================================
# 文件: docker/opa/policies/consultation_answerability.rego
# 作用: 裁决兽医 Agent 问诊状态是否足以进入阶段性回答。
# 范围: 仅消费应用侧提交的结构化状态、证据画像和用户意图，不扫描用户自然语言文本。
# 说明: 应用侧负责状态合并和追问文案生成；本策略只输出 answer 或 ask 的准入结果，
#       避免在策略层退化为关键词匹配或自定义规则状态机。
# =============================================================================

package vet_agent.consultation_state

import rego.v1

default allow := false

# 最低上下文仅用于防止没有主诉或物种时直接生成临床判断。
minimum_context if {
	object.get(input.evidence_profile, "minimum_context", false) == true
}

answer_now_requested if {
	object.get(input.intent, "answer_now", false) == true
}

slot_complete if {
	count(object.get(input, "unresolved_slots", [])) == 0
}

clinical_safety_precondition_unknown if {
	object.get(input.evidence_profile, "clinical_safety_precondition_unknown", false) == true
}

sufficient_semantic_evidence if {
	object.get(input.state, "followup_rounds", 0) >= 1
	object.get(input.evidence_profile, "known_category_count", 0) >= object.get(input.limits, "min_known_categories", 2)
}

followup_limit_reached if {
	object.get(input.state, "followup_rounds", 0) >= object.get(input.limits, "max_followup_rounds", 2)
}

allow if {
	minimum_context
	answer_now_requested
}

allow if {
	minimum_context
	slot_complete
	not clinical_safety_precondition_unknown
}

allow if {
	minimum_context
	sufficient_semantic_evidence
	not clinical_safety_precondition_unknown
}

allow if {
	minimum_context
	followup_limit_reached
}

action := "answer" if {
	allow
}

action := "ask" if {
	not allow
}

mode := "user_requested_answer_now" if {
	allow
	answer_now_requested
}

mode := "slot_complete" if {
	allow
	not answer_now_requested
	slot_complete
}

mode := "sufficient_semantic_evidence" if {
	allow
	not answer_now_requested
	not slot_complete
	sufficient_semantic_evidence
}

mode := "clinical_safety_precondition_unknown" if {
	not allow
	minimum_context
	clinical_safety_precondition_unknown
}

mode := "max_followup_rounds_reached" if {
	allow
	not answer_now_requested
	not slot_complete
	not sufficient_semantic_evidence
	followup_limit_reached
}

mode := "needs_high_value_evidence" if {
	not allow
	minimum_context
	not clinical_safety_precondition_unknown
}

mode := "needs_minimum_context" if {
	not allow
	not minimum_context
}

answer_scope := "preliminary" if {
	allow
}

answer_scope := "insufficient" if {
	not allow
}

reason := "用户明确要求根据现有信息先给阶段性判断。" if {
	mode == "user_requested_answer_now"
}

reason := "结构化证据已经足以支撑阶段性回答。" if {
	mode == "slot_complete"
}

reason := "已获得足够的结构化证据覆盖。" if {
	mode == "sufficient_semantic_evidence"
}

reason := "已达到连续追问轮数上限。" if {
	mode == "max_followup_rounds_reached"
}

reason := "仍缺少会明显影响分诊建议的高价值信息。" if {
	mode == "needs_high_value_evidence"
}

reason := "仍缺少进入阶段性回答所需的最低问诊上下文。" if {
	mode == "needs_minimum_context"
}

reason := "临床安全前提仍缺少关键症状或背景信息。" if {
	mode == "clinical_safety_precondition_unknown"
}

policy_reason := "consultation_answerability_user_requested_answer_now" if {
	mode == "user_requested_answer_now"
}

policy_reason := "consultation_answerability_slot_complete" if {
	mode == "slot_complete"
}

policy_reason := "consultation_answerability_semantic_evidence_sufficient" if {
	mode == "sufficient_semantic_evidence"
}

policy_reason := "consultation_answerability_followup_limit_reached" if {
	mode == "max_followup_rounds_reached"
}

policy_reason := "consultation_answerability_more_evidence_needed" if {
	mode == "needs_high_value_evidence"
}

policy_reason := "consultation_answerability_clinical_safety_precondition_unknown" if {
	mode == "clinical_safety_precondition_unknown"
}

policy_reason := "consultation_answerability_minimum_context_missing" if {
	mode == "needs_minimum_context"
}

reasons := [policy_reason]

unresolved_slots := [slot |
	some index
	slot := object.get(input, "unresolved_slots", [])[index]
	trim(slot, " ") != ""
]

candidate_slots := slots if {
	count(object.get(input, "advisory_slots", [])) > 0
	slots := [slot |
		some index
		slot := object.get(input, "advisory_slots", [])[index]
		trim(slot, " ") != ""
	]
}

candidate_slots := unresolved_slots if {
	count(object.get(input, "advisory_slots", [])) == 0
}

blocking_slots := [] if {
	allow
}

blocking_slots := array.slice(candidate_slots, 0, object.get(input.limits, "max_questions", 3)) if {
	not allow
}

message := "问诊回答充分性策略允许进入阶段性回答。" if {
	allow
}

message := "问诊回答充分性策略要求继续追问。" if {
	not allow
}

decision := {
	"action": action,
	"allow": allow,
	"message": message,
	"mode": mode,
	"answer_scope": answer_scope,
	"blocking_slots": blocking_slots,
	"unresolved_slots": unresolved_slots,
	"reason": reason,
	"reasons": reasons,
}

# =============================================================================
# 文件: docker/opa/policies/clinical_safety.rego
# 作用: 裁决兽医 Agent 临床安全候选的最终动作与安全信号。
# 范围: 仅消费应用侧提交的结构化语义、pgvector 候选、召回状态和阈值，不扫描用户原始文本。
# 说明: 医学语义抽取与候选召回在应用侧完成；本策略只做动作矩阵裁决，避免退化为关键词或临床推理状态机。
# =============================================================================

package vet_agent.clinical_safety

import rego.v1

# 默认无候选时放行；候选由应用侧 pgvector 召回产生，OPA 不生成新的临床安全候选。
default action := "allow"

default allow := false

# 可信语义明确否认暴露，且该候选属于毒物、人用药、植物毒物或化学毒物时，不生成升级信号。
suppressed_candidate(candidate) if {
	semantic_trusted
	candidate.asset_type in {"toxin", "human_drug", "plant_toxin", "chemical_toxin"}
	input.semantic.exposure_state == "denied"
	not input.semantic.intent_type in {"knowledge", "prevention"}
}

# 可信语义明确表示远期既往且事件已结束时，非仍在发生的急性症状不再升级。
temporally_resolved_candidate(candidate) if {
	semantic_trusted
	input.semantic.temporal_scope == "remote_past"
	input.semantic.resolution_state == "resolved"
	not candidate.asset_type in {"toxin", "human_drug", "plant_toxin", "chemical_toxin"}
}

# 物种、性别和年龄只作为结构化上下文边界过滤，不在策略层组合推理疾病。
context_mismatch(candidate) if {
	semantic_trusted
	count(object.get(candidate, "species_scope", [])) > 0
	input.semantic.species != "unknown"
	not input.semantic.species in candidate.species_scope
}

context_mismatch(candidate) if {
	semantic_trusted
	count(object.get(candidate, "sex_scope", [])) > 0
	input.semantic.sex != "unknown"
	not input.semantic.sex in candidate.sex_scope
}

context_mismatch(candidate) if {
	semantic_trusted
	"senior" in object.get(candidate, "age_scope", [])
	not input.semantic.age_group in {"senior", "unknown"}
}

# 正向风险证据只来自结构化语义字段；triage 意图本身不构成急诊事实。
positive_risk_evidence if {
	semantic_trusted
	input.semantic.exposure_state in {"confirmed", "possible"}
}

positive_risk_evidence if {
	semantic_trusted
	input.semantic.symptom_state == "present"
}

positive_risk_evidence if {
	semantic_trusted
	count(object.get(input.semantic, "high_risk_terms", [])) > 0
}

insufficient_evidence_candidate(candidate) if {
	candidate.code != ""
	semantic_trusted
	not positive_risk_evidence
}

# required_context 表示资产进入裁决的前置结构化事实；缺失事实不能被高分召回补足。
required_context_mismatch(candidate) if {
	semantic_trusted
	required := object.get(candidate, "required_context", {})
	values := object.get(required, "species", [])
	count(values) > 0
	input.semantic.species == "unknown"
}

required_context_mismatch(candidate) if {
	semantic_trusted
	required := object.get(candidate, "required_context", {})
	values := object.get(required, "species", [])
	count(values) > 0
	input.semantic.species != "unknown"
	not input.semantic.species in values
}

required_context_mismatch(candidate) if {
	semantic_trusted
	required := object.get(candidate, "required_context", {})
	values := object.get(required, "sex", [])
	count(values) > 0
	input.semantic.sex == "unknown"
}

required_context_mismatch(candidate) if {
	semantic_trusted
	required := object.get(candidate, "required_context", {})
	values := object.get(required, "sex", [])
	count(values) > 0
	input.semantic.sex != "unknown"
	not input.semantic.sex in values
}

required_context_mismatch(candidate) if {
	semantic_trusted
	required := object.get(candidate, "required_context", {})
	values := object.get(required, "age", [])
	count(values) > 0
	input.semantic.age_group == "unknown"
}

required_context_mismatch(candidate) if {
	semantic_trusted
	required := object.get(candidate, "required_context", {})
	values := object.get(required, "age", [])
	count(values) > 0
	input.semantic.age_group != "unknown"
	not input.semantic.age_group in values
}

required_context_mismatch(candidate) if {
	semantic_trusted
	required := object.get(candidate, "required_context", {})
	values := object.get(required, "symptoms", [])
	count(values) > 0
	not required_symptom_context_satisfied(values)
}

required_symptom_context_satisfied(values) if {
	some term in object.get(input.semantic, "high_risk_terms", [])
	term in values
}

applicable_candidate(candidate) if {
	some item in input.candidates
	candidate = item
	not suppressed_candidate(candidate)
	not temporally_resolved_candidate(candidate)
	not context_mismatch(candidate)
	not insufficient_evidence_candidate(candidate)
	not required_context_mismatch(candidate)
}

urgent_candidate(candidate) if {
	applicable_candidate(candidate)
	candidate.severity == "blocked"
}

urgent_candidate(candidate) if {
	applicable_candidate(candidate)
	candidate.severity == "urgent"
}

urgent_candidate(candidate) if {
	applicable_candidate(candidate)
	candidate.action_class in {"emergency", "same_day_visit", "urgent_visit"}
}

urgent_candidate(candidate) if {
	applicable_candidate(candidate)
	candidate.score >= object.get(input.thresholds, "urgent_min_score", 0.75)
}

observable_candidate(candidate) if {
	applicable_candidate(candidate)
	candidate.score >= object.get(input.thresholds, "signal_min_score", 0.65)
}

observable_candidate(candidate) if {
	applicable_candidate(candidate)
	urgent_candidate(candidate)
}

action := "block" if {
	some candidate in input.candidates
	urgent_candidate(candidate)
	candidate.severity == "blocked"
}

action := "escalate" if {
	not blocked_candidate
	some candidate in input.candidates
	urgent_candidate(candidate)
}

action := "observe" if {
	not blocked_candidate
	not escalated_candidate
	some candidate in input.candidates
	observable_candidate(candidate)
}

blocked_candidate if {
	some candidate in input.candidates
	urgent_candidate(candidate)
	candidate.severity == "blocked"
}

escalated_candidate if {
	some candidate in input.candidates
	urgent_candidate(candidate)
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

# 对外结构化裁决结果；调用方必须严格校验该结构。
decision := {
	"action": action,
	"allow": allow,
	"message": message,
	"reasons": reasons,
	"signals": signals,
}

message := "临床安全策略未识别到需要中断主链路的风险。" if {
	action == "allow"
}

message := "临床安全策略记录候选并允许继续问诊。" if {
	action == "observe"
}

message := "临床安全策略识别到需要优先线下处理的风险。" if {
	action == "escalate"
}

message := "临床安全策略阻断本轮继续生成普通医疗回答。" if {
	action == "block"
}

reasons := [reason |
	some candidate in input.candidates
	reason := candidate_reason(candidate)
]

candidate_reason(candidate) := sprintf("clinical_safety_candidate:%s:%s", [candidate.code, candidate.action_class]) if {
	applicable_candidate(candidate)
	observable_candidate(candidate)
}

candidate_reason(candidate) := sprintf("clinical_safety_candidate_suppressed:%s", [candidate.code]) if {
	suppressed_candidate(candidate)
}

candidate_reason(candidate) := sprintf("clinical_safety_candidate_context_mismatch:%s", [candidate.code]) if {
	context_mismatch(candidate)
}

candidate_reason(candidate) := sprintf("clinical_safety_candidate_insufficient_evidence:%s", [candidate.code]) if {
	insufficient_evidence_candidate(candidate)
}

candidate_reason(candidate) := sprintf("clinical_safety_candidate_required_context_mismatch:%s", [candidate.code]) if {
	required_context_mismatch(candidate)
}

candidate_reason(candidate) := sprintf("clinical_safety_candidate_temporally_resolved:%s", [candidate.code]) if {
	temporally_resolved_candidate(candidate)
}

signals := [signal |
	some candidate in input.candidates
	observable_candidate(candidate)
	severity := signal_severity(candidate)
	signal := {
		"code": candidate.code,
		"severity": severity,
		"message": candidate.message,
		"matched_terms": matched_terms(candidate),
	}
]

signal_severity(candidate) := "blocked" if {
	action == "block"
	candidate.severity == "blocked"
}

signal_severity(candidate) := "urgent" if {
	action == "escalate"
	urgent_candidate(candidate)
}

signal_severity(candidate) := candidate.severity if {
	action == "observe"
}

signal_severity(candidate) := "caution" if {
	action == "block"
	candidate.severity != "blocked"
}

matched_terms(candidate) := terms if {
	terms := object.get(candidate, "matched_terms", [])
	not semantic_trusted
}

matched_terms(candidate) := terms if {
	semantic_trusted
	terms := array.concat(
		object.get(candidate, "matched_terms", []),
		object.get(input.semantic, "high_risk_terms", []),
	)
}

semantic_trusted if {
	object.get(input.semantic, "trusted", false) == true
}

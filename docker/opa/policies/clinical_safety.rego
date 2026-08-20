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
	count(object.get(candidate, "age_scope", [])) > 0
	input.semantic.age_group != "unknown"
	not input.semantic.age_group in candidate.age_scope
}

# 证据充分性边界由语义抽取层统一输出；OPA 不再重新拼装症状、暴露和意图字段。
semantic_evidence_sufficient if {
	semantic_trusted
	object.get(input.semantic, "risk_evidence_state", "unknown") == "sufficient"
}

semantic_evidence_insufficient if {
	semantic_trusted
	object.get(input.semantic, "risk_evidence_state", "unknown") == "insufficient"
}

semantic_evidence_unavailable if {
	not semantic_trusted
}

semantic_evidence_unavailable if {
	semantic_trusted
	object.get(input.semantic, "risk_evidence_state", "unknown") == "unknown"
}

insufficient_evidence_candidate(candidate) if {
	candidate.code != ""
	semantic_evidence_insufficient
}

unavailable_evidence_candidate(candidate) if {
	candidate.code != ""
	semantic_evidence_unavailable
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

# 自然语言症状前提由应用侧语义评估器转换为结构化状态；OPA 只校验绑定和证据引用。
symptom_context_required(candidate) if {
	required := object.get(candidate, "required_context", {})
	values := object.get(required, "symptoms", [])
	count(values) > 0
}

present_observed_feature(feature_id) if {
	some feature in object.get(input.semantic, "observed_features", [])
	feature.id == feature_id
	feature.kind == "symptom"
	feature.state == "present"
}

observed_symptom_feature(feature_id) if {
	some feature in object.get(input.semantic, "observed_features", [])
	feature.id == feature_id
	feature.kind == "symptom"
}

candidate_precondition_assessment(candidate) := assessment if {
	assessments := object.get(input, "precondition_assessments", {})
	assessment := object.get(assessments, candidate.asset_id, {})
	count(assessment) > 0
}

valid_precondition_evidence(assessment) if {
	evidence_ids := object.get(assessment, "evidence_ids", [])
	count(evidence_ids) > 0
	every evidence_id in evidence_ids {
		observed_symptom_feature(evidence_id)
	}
}

present_precondition_evidence(assessment) if {
	evidence_ids := object.get(assessment, "evidence_ids", [])
	every evidence_id in evidence_ids {
		present_observed_feature(evidence_id)
	}
}

definitive_precondition_assessment(candidate) if {
	symptom_context_required(candidate)
	assessment := candidate_precondition_assessment(candidate)
	candidate_hash := object.get(candidate, "required_context_hash", "")
	assessment_hash := object.get(assessment, "required_context_hash", "")
	count(candidate_hash) > 0
	count(assessment_hash) > 0
	regex.match(`^sha256:[0-9a-f]{64}$`, candidate_hash)
	regex.match(`^sha256:[0-9a-f]{64}$`, assessment_hash)
	candidate_hash == assessment_hash
	object.get(assessment, "trusted", false) == true
	valid_precondition_evidence(assessment)
}

required_context_mismatch(candidate) if {
	semantic_trusted
	symptom_context_required(candidate)
	assessment := candidate_precondition_assessment(candidate)
	object.get(assessment, "status", "unknown") == "not_satisfied"
	definitive_precondition_assessment(candidate)
}

# 评估缺失、unknown、低置信、哈希错配、证据非法或事实不足时均 Fail Closed。
required_context_unavailable(candidate) if {
	semantic_trusted
	symptom_context_required(candidate)
	not satisfied_precondition_context(candidate)
}

satisfied_precondition_context(candidate) if {
	semantic_trusted
	definitive_precondition_assessment(candidate)
	assessment := candidate_precondition_assessment(candidate)
	present_precondition_evidence(assessment)
	object.get(assessment, "status", "unknown") == "satisfied"
}

potentially_signal_candidate(candidate) if {
	candidate.severity in {"blocked", "urgent"}
}

potentially_signal_candidate(candidate) if {
	candidate.action_class in {"emergency", "same_day_visit", "urgent_visit"}
}

potentially_signal_candidate(candidate) if {
	candidate.score >= object.get(input.thresholds, "signal_min_score", 0.65)
}

precondition_plan_candidate(candidate) if {
	some item in input.candidates
	candidate = item
	semantic_evidence_sufficient
	symptom_context_required(candidate)
	potentially_signal_candidate(candidate)
	not suppressed_candidate(candidate)
	not temporally_resolved_candidate(candidate)
	not context_mismatch(candidate)
	not required_context_mismatch(candidate)
}

precondition_plan := {
	"asset_ids": [candidate.asset_id |
		some candidate in input.candidates
		precondition_plan_candidate(candidate)
	],
}

applicable_candidate(candidate) if {
	some item in input.candidates
	candidate = item
	semantic_evidence_sufficient
	not suppressed_candidate(candidate)
	not temporally_resolved_candidate(candidate)
	not context_mismatch(candidate)
	not insufficient_evidence_candidate(candidate)
	not unavailable_evidence_candidate(candidate)
	not required_context_mismatch(candidate)
	not required_context_unavailable(candidate)
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
	"primary_signal": primary_signal,
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

# 候选审计原因按固定优先级输出唯一值；多个不适用谓词同时命中时不得产生多输出冲突。
# 优先级：证据不可用 > 证据不足 > 语义否认/远期已缓解 > 前置上下文缺失 > 结构化范围不匹配 > 正常可观察候选。
candidate_reason(candidate) := sprintf("clinical_safety_candidate_evidence_unavailable:%s", [candidate.code]) if {
	unavailable_evidence_candidate(candidate)
} else := sprintf("clinical_safety_candidate_insufficient_evidence:%s", [candidate.code]) if {
	insufficient_evidence_candidate(candidate)
} else := sprintf("clinical_safety_candidate_suppressed:%s", [candidate.code]) if {
	suppressed_candidate(candidate)
} else := sprintf("clinical_safety_candidate_temporally_resolved:%s", [candidate.code]) if {
	temporally_resolved_candidate(candidate)
} else := sprintf("clinical_safety_candidate_required_context_mismatch:%s", [candidate.code]) if {
	required_context_mismatch(candidate)
} else := sprintf("clinical_safety_candidate_required_context_unavailable:%s", [candidate.code]) if {
	required_context_unavailable(candidate)
} else := sprintf("clinical_safety_candidate_context_mismatch:%s", [candidate.code]) if {
	context_mismatch(candidate)
} else := sprintf("clinical_safety_candidate:%s:%s", [candidate.code, candidate.action_class]) if {
	applicable_candidate(candidate)
	observable_candidate(candidate)
}

signals := [signal |
	some candidate in input.candidates
	observable_candidate(candidate)
	severity := signal_severity(candidate)
	signal := {
		"asset_id": candidate.asset_id,
		"code": candidate.code,
		"canonical_name": candidate.canonical_name,
		"severity": severity,
		"message": candidate.message,
		"matched_terms": matched_terms(candidate),
	}
]

# 主信号只从有效信号对应的候选中选择；排序不读取用户原文、不匹配关键词、不判断具体疾病编码。
default primary_signal := null

primary_signal := signal if {
	action in {"escalate", "block"}
	candidate := primary_candidate
	severity := signal_severity(candidate)
	signal := {
		"asset_id": candidate.asset_id,
		"code": candidate.code,
		"canonical_name": candidate.canonical_name,
		"severity": severity,
		"message": candidate.message,
		"matched_terms": matched_terms(candidate),
	}
}

primary_candidate := ordered_candidates[0][3]

ordered_candidates := sort([ordering |
	some candidate in input.candidates
	primary_eligible_candidate(candidate)
	ordering := [primary_severity_rank(candidate), primary_action_rank(candidate), candidate.asset_id, candidate]
])

primary_eligible_candidate(candidate) if {
	action == "block"
	urgent_candidate(candidate)
	candidate.severity == "blocked"
}

primary_eligible_candidate(candidate) if {
	action == "escalate"
	urgent_candidate(candidate)
	candidate.severity != "blocked"
}

primary_severity_rank(candidate) := 0 if {
	candidate.severity == "blocked"
}

primary_severity_rank(candidate) := 1 if {
	candidate.severity != "blocked"
}

primary_action_rank(candidate) := 0 if {
	candidate.action_class == "emergency"
}

primary_action_rank(candidate) := 1 if {
	candidate.action_class == "same_day_visit"
}

primary_action_rank(candidate) := 2 if {
	candidate.action_class == "urgent_visit"
}

default primary_action_rank(_) := 3

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

# 命中词只来自向量候选自身；语义抽取的 high_risk_terms 不进入 OPA 或用户信号投影。
matched_terms(candidate) := terms if {
	terms := object.get(candidate, "matched_terms", [])
}

semantic_trusted if {
	object.get(input.semantic, "trusted", false) == true
}

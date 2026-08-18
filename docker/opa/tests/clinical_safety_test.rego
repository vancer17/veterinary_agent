# =============================================================================
# 文件: docker/opa/tests/clinical_safety_test.rego
# 作用: 验证临床安全 OPA 策略只基于结构化候选与可信语义裁决动作。
# 范围: 覆盖空候选放行、急诊候选升级、可信否认暴露抑制、证据不可用不升级和上下文过滤。
# 说明: 测试输入不包含用户原始文本，确保策略不依赖关键词、正则或文本扫描路径。
# =============================================================================

package vet_agent.clinical_safety_test

import data.vet_agent.clinical_safety
import rego.v1

base_input := {
	"context": {
		"request_id": "req_opa_unit",
		"trace_id": "tr_opa_unit",
		"user_id": "user_opa_unit",
		"pet_id": "pet_opa_unit",
		"session_id": "session_opa_unit",
	},
	"semantic": {
		"trusted": true,
		"stage": "llm",
		"strategy": "litellm_response_format",
		"confidence": 0.94,
		"species": "cat",
		"sex": "unknown",
		"age_group": "adult",
		"exposure_state": "confirmed",
		"symptom_state": "present",
		"temporal_state": "current",
		"temporal_scope": "ongoing",
		"resolution_state": "ongoing",
		"intent_type": "toxicity",
		"risk_evidence_state": "sufficient",
		"high_risk_terms": ["泰诺"],
		"negated_terms": [],
	},
	"retrieval": {
		"stage": "vector",
		"degraded": false,
		"candidate_count": 1,
		"retrieval_source": "clinical_safety_pgvector",
	},
	"thresholds": {
		"signal_min_score": 0.65,
		"urgent_min_score": 0.75,
	},
	"candidates": [],
}

toxic_candidate := {
	"asset_id": "safety_human_drug_001",
	"code": "TOXIC_SUBSTANCE",
	"asset_type": "human_drug",
	"canonical_name": "对乙酰氨基酚",
	"species_scope": ["cat", "dog"],
	"sex_scope": [],
	"age_scope": [],
	"severity": "urgent",
	"action_class": "emergency",
	"score": 0.91,
	"score_type": "cosine_similarity",
	"retrieval_source": "clinical_safety_pgvector",
	"message": "疑似误食人用药时应立即联系线下急诊兽医。",
	"matched_terms": ["对乙酰氨基酚"],
	"decision_hints": {},
}

test_empty_candidates_allowed if {
	decision := clinical_safety.decision with input as base_input

	decision.action == "allow"
	decision.allow == true
	count(decision.signals) == 0
}

test_emergency_candidate_escalates if {
	decision := clinical_safety.decision with input as object.union(base_input, {
		"candidates": [toxic_candidate],
	})

	decision.action == "escalate"
	decision.allow == true
	decision.signals[0].code == "TOXIC_SUBSTANCE"
	decision.signals[0].severity == "urgent"
	"泰诺" in decision.signals[0].matched_terms
}

test_triage_without_positive_evidence_does_not_escalate if {
	triage_semantic := object.union(base_input.semantic, {
		"exposure_state": "unknown",
		"symptom_state": "unknown",
		"intent_type": "triage",
		"risk_evidence_state": "insufficient",
		"high_risk_terms": [],
	})
	decision := clinical_safety.decision with input as object.union(base_input, {
		"semantic": triage_semantic,
		"candidates": [toxic_candidate],
	})

	decision.action == "allow"
	decision.allow == true
	count(decision.signals) == 0
	"clinical_safety_candidate_insufficient_evidence:TOXIC_SUBSTANCE" in decision.reasons
}

test_trusted_denied_exposure_suppresses_toxic_candidate if {
	denied_semantic := object.union(base_input.semantic, {
		"exposure_state": "denied",
		"intent_type": "other",
	})
	decision := clinical_safety.decision with input as object.union(base_input, {
		"semantic": denied_semantic,
		"candidates": [toxic_candidate],
	})

	decision.action == "allow"
	decision.allow == true
	count(decision.signals) == 0
	decision.reasons[0] == "clinical_safety_candidate_suppressed:TOXIC_SUBSTANCE"
}

test_untrusted_semantic_does_not_escalate_toxic_candidate if {
	untrusted_semantic := object.union(base_input.semantic, {
		"trusted": false,
		"stage": "llm_low_confidence",
		"risk_evidence_state": "unknown",
		"exposure_state": "denied",
		"intent_type": "other",
		"high_risk_terms": [],
	})
	decision := clinical_safety.decision with input as object.union(base_input, {
		"semantic": untrusted_semantic,
		"candidates": [toxic_candidate],
	})

	decision.action == "allow"
	decision.allow == true
	count(decision.signals) == 0
	"clinical_safety_candidate_evidence_unavailable:TOXIC_SUBSTANCE" in decision.reasons
}

test_trusted_unknown_evidence_does_not_escalate_toxic_candidate if {
	unknown_evidence_semantic := object.union(base_input.semantic, {
		"risk_evidence_state": "unknown",
	})
	decision := clinical_safety.decision with input as object.union(base_input, {
		"semantic": unknown_evidence_semantic,
		"candidates": [toxic_candidate],
	})

	decision.action == "allow"
	decision.allow == true
	count(decision.signals) == 0
	"clinical_safety_candidate_evidence_unavailable:TOXIC_SUBSTANCE" in decision.reasons
}

test_species_context_mismatch_filters_candidate if {
	dog_semantic := object.union(base_input.semantic, {"species": "dog"})
	cat_only_candidate := object.union(toxic_candidate, {"species_scope": ["cat"]})
	decision := clinical_safety.decision with input as object.union(base_input, {
		"semantic": dog_semantic,
		"candidates": [cat_only_candidate],
	})

	decision.action == "allow"
	decision.allow == true
	count(decision.signals) == 0
	decision.reasons[0] == "clinical_safety_candidate_context_mismatch:TOXIC_SUBSTANCE"
}

test_required_context_mismatch_filters_candidate if {
	contextual_candidate := object.union(toxic_candidate, {
		"code": "CYANOSIS_RISK_PATTERN",
		"asset_type": "emergency_red_flag",
		"required_context": {
			"species": ["cat", "dog"],
			"symptoms": ["呼吸困难"],
		},
	})
	semantic_without_symptom := object.union(base_input.semantic, {
		"exposure_state": "unknown",
		"symptom_state": "present",
		"high_risk_terms": ["呕吐"],
	})
	decision := clinical_safety.decision with input as object.union(base_input, {
		"semantic": semantic_without_symptom,
		"candidates": [contextual_candidate],
	})

	decision.action == "allow"
	decision.allow == true
	count(decision.signals) == 0
	decision.reasons[0] == "clinical_safety_candidate_required_context_mismatch:CYANOSIS_RISK_PATTERN"
}

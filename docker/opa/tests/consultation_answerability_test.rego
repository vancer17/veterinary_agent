# =============================================================================
# 文件: docker/opa/tests/consultation_answerability_test.rego
# 作用: 验证问诊回答充分性 OPA 策略的结构化裁决行为。
# 范围: 覆盖最低上下文、用户要求先答、证据充分和继续追问四类主分支。
# 说明: 该测试只依赖结构化输入字段，不验证任何用户原始文本关键词扫描行为。
# =============================================================================

package vet_agent.consultation_state_test

import data.vet_agent.consultation_state
import rego.v1

test_answer_now_allowed_when_minimum_context_present if {
	decision := consultation_state.decision with input as {
		"context": {
			"request_id": "r1",
			"trace_id": "t1",
			"user_id": "u1",
			"pet_id": "p1",
			"session_id": "s1",
		},
		"state": {
			"domain": "general",
			"phase": "collecting_info",
			"followup_rounds": 0,
			"asked_question_count": 0,
			"has_chief_complaint": true,
			"has_species": true,
		},
		"intent": {
			"answer_now": true,
			"wants_triage": false,
			"correction": false,
			"raw_intent": "先给阶段性判断",
		},
		"limits": {
			"max_followup_rounds": 2,
			"min_known_categories": 2,
			"max_questions": 3,
		},
		"evidence_profile": {
			"minimum_context": true,
			"known_category_count": 1,
			"known_categories": ["patient_identity"],
		},
		"unresolved_slots": ["onset"],
		"advisory_slots": ["onset"],
	}

	decision.allow == true
	decision.action == "answer"
	decision.mode == "user_requested_answer_now"
}

test_semantic_evidence_allows_without_full_slot_completion if {
	decision := consultation_state.decision with input as {
		"context": {
			"request_id": "r2",
			"trace_id": "t2",
			"user_id": "u1",
			"pet_id": "p1",
			"session_id": "s1",
		},
		"state": {
			"domain": "general",
			"phase": "collecting_info",
			"followup_rounds": 1,
			"asked_question_count": 1,
			"has_chief_complaint": true,
			"has_species": true,
		},
		"intent": {
			"answer_now": false,
			"wants_triage": false,
			"correction": false,
			"raw_intent": "",
		},
		"limits": {
			"max_followup_rounds": 2,
			"min_known_categories": 2,
			"max_questions": 3,
		},
		"evidence_profile": {
			"minimum_context": true,
			"known_category_count": 2,
			"known_categories": ["patient_identity", "time_course"],
		},
		"unresolved_slots": ["onset", "appetite"],
		"advisory_slots": ["onset", "appetite"],
	}

	decision.allow == true
	decision.action == "answer"
	decision.mode == "sufficient_semantic_evidence"
}

test_minimum_context_missing_stays_in_followup if {
	decision := consultation_state.decision with input as {
		"context": {
			"request_id": "r3",
			"trace_id": "t3",
			"user_id": "u1",
			"pet_id": "p1",
			"session_id": "s1",
		},
		"state": {
			"domain": "general",
			"phase": "collecting_info",
			"followup_rounds": 0,
			"asked_question_count": 0,
			"has_chief_complaint": false,
			"has_species": false,
		},
		"intent": {
			"answer_now": false,
			"wants_triage": false,
			"correction": false,
			"raw_intent": "",
		},
		"limits": {
			"max_followup_rounds": 2,
			"min_known_categories": 2,
			"max_questions": 3,
		},
		"evidence_profile": {
			"minimum_context": false,
			"known_category_count": 0,
			"known_categories": [],
		},
		"unresolved_slots": ["species", "onset"],
		"advisory_slots": ["species", "onset"],
	}

	decision.allow == false
	decision.action == "ask"
	decision.mode == "needs_minimum_context"
	decision.blocking_slots == ["species", "onset"]
}

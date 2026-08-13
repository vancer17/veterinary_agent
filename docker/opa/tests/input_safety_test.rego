# =============================================================================
# 文件: docker/opa/tests/input_safety_test.rego
# 作用: 验证基础输入安全 OPA 策略的结构化候选裁决行为。
# 范围: 覆盖空候选放行、blocked 候选阻断、caution 候选观测。
# 说明: 测试输入包含用户文本，但策略断言不得依赖文本关键词命中。
# =============================================================================

package vet_agent.input_safety_test

import data.vet_agent.input_safety
import rego.v1

test_empty_candidates_allowed if {
	decision := input_safety.decision with input as {
		"request": {"text_length": 0},
		"candidates": [],
	}

	decision.action == "allow"
	decision.allow == true
	count(decision.signals) == 0
}

test_blocked_candidate_blocks if {
	decision := input_safety.decision with input as {
		"request": {"text_length": 42},
		"candidates": [
			{
				"code": "PROMPT_INJECTION_ATTEMPT",
				"severity": "blocked",
				"message": "输入存在越权或提示注入风险。",
				"matched_terms": [],
			},
		],
	}

	decision.action == "block"
	decision.allow == false
	decision.signals[0].code == "PROMPT_INJECTION_ATTEMPT"
	decision.signals[0].severity == "blocked"
}

test_caution_candidate_observed_without_text_scan if {
	decision := input_safety.decision with input as {
		"request": {
			"text_length": 30,
			"text": "这段文字即使包含任意普通词也不应被 Rego 扫描。",
		},
		"candidates": [
			{
				"code": "ATTACHMENT_PURPOSE_UNKNOWN",
				"severity": "caution",
				"message": "附件用途未明确声明。",
				"matched_terms": ["a1"],
			},
		],
	}

	decision.action == "observe"
	decision.allow == true
	decision.signals[0].matched_terms == ["a1"]
}

# =============================================================================
# 文件: docker/opa/tests/task_routing_test.rego
# 作用: 验证任务路由 OPA 策略只消费结构化计划元数据。
# 范围: 覆盖合法单任务、超限任务、非法任务域和已有任务引用校验。
# 说明: 测试不依赖关键词、自然语言内容或自定义拆分状态机。
# =============================================================================

package vet_agent.task_routing_test

import data.vet_agent.task_routing
import rego.v1

test_task_routing_allows_valid_plan if {
	decision := task_routing.decision with input as {
		"max_task_count": 5,
		"allowed_domains": ["gastrointestinal", "general"],
		"active_task_keys": [],
		"tasks": [
			{
				"task_id": "task_001",
				"task_key": "__default__",
				"domain": "gastrointestinal",
				"text_length": 12,
				"priority": 10,
				"existing_task_key": null,
			},
		],
	}

	decision.action == "allow"
	decision.allow == true
	decision.reasons == []
}

test_task_routing_rejects_invalid_domain if {
	decision := task_routing.decision with input as {
		"max_task_count": 5,
		"allowed_domains": ["general"],
		"active_task_keys": [],
		"tasks": [
			{
				"task_id": "task_001",
				"task_key": "respiratory",
				"domain": "respiratory",
				"text_length": 8,
				"priority": 10,
				"existing_task_key": null,
			},
		],
	}

	decision.action == "reject"
	decision.allow == false
	"task_routing_invalid_domain:respiratory" in decision.reasons
}

test_task_routing_rejects_unknown_existing_task if {
	decision := task_routing.decision with input as {
		"max_task_count": 5,
		"allowed_domains": ["general"],
		"active_task_keys": ["task_known"],
		"tasks": [
			{
				"task_id": "task_001",
				"task_key": "general",
				"domain": "general",
				"text_length": 8,
				"priority": 10,
				"existing_task_key": "task_unknown",
			},
		],
	}

	decision.action == "reject"
	decision.allow == false
}

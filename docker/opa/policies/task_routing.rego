# =============================================================================
# 文件: docker/opa/policies/task_routing.rego
# 作用: 对结构化任务路由候选执行最终策略准入。
# 范围: 只校验任务数量、任务域、任务键和已有任务引用，不扫描用户自然语言。
# 说明: LiteLLM response_format 与应用侧 Pydantic 已完成结构解析；本策略不负责
#       拆分、修正或回退，只对不可变 TaskExecutionPlan 的生成提供 allow/reject。
# =============================================================================

package vet_agent.task_routing

import rego.v1

default allow := false

policy_reason contains "task_routing_no_tasks" if {
	count(input.tasks) == 0
}

policy_reason contains "task_routing_too_many_tasks" if {
	count(input.tasks) > input.max_task_count
}

policy_reason contains sprintf("task_routing_invalid_domain:%s", [task.domain]) if {
	some task in input.tasks
	not task.domain in input.allowed_domains
}

policy_reason contains "task_routing_empty_task_key" if {
	some task in input.tasks
	trim(task.task_key, " ") == ""
}

policy_reason contains "task_routing_empty_text" if {
	some task in input.tasks
	task.text_length <= 0
}

policy_reason contains "task_routing_duplicate_task_key" if {
	count({task.task_key | some task in input.tasks}) != count(input.tasks)
}

policy_reason contains sprintf("task_routing_unknown_existing_task:%s", [task.existing_task_key]) if {
	some task in input.tasks
	task.existing_task_key != null
	not task.existing_task_key in input.active_task_keys
}

allow if {
	count(policy_reason) == 0
}

action := "allow" if {
	allow
}

action := "reject" if {
	not allow
}

message := "任务路由策略允许生成本轮任务执行计划。" if {
	allow
}

message := "任务路由策略拒绝本轮任务执行计划。" if {
	not allow
}

reasons := [reason | some reason in policy_reason]

decision := {
	"action": action,
	"allow": allow,
	"message": message,
	"reasons": reasons,
}

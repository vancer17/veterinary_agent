from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from vet_agent.config import Settings
from vet_agent.repositories.rules import RuleRepository
from vet_agent.runtime.qwen import QwenClient


DOMAIN_TITLES = {
    "gastrointestinal": "消化道问题",
    "respiratory": "呼吸问题",
    "mobility": "疼痛/活动问题",
    "behavior": "行为问题",
    "feeding": "喂养问题",
    "general": "一般健康问题",
}


@dataclass(frozen=True)
class SplitTask:
    task_id: str
    text: str
    domain: str
    title: str
    priority: int = 100
    reason: str = ""

    @property
    def state_key(self) -> str:
        return self.domain


@dataclass(frozen=True)
class TaskSplitDecision:
    tasks: list[SplitTask]
    strategy: str
    fallback_reason: str | None = None


class TaskRouterItem(BaseModel):
    domain: str = Field(min_length=1)
    title: str | None = Field(default=None)
    text: str = Field(min_length=1)
    priority: int = Field(default=100, ge=1, le=100)
    reason: str = Field(default="")


class TaskRouterOutput(BaseModel):
    tasks: list[TaskRouterItem] = Field(default_factory=list, min_length=1, max_length=5)


class RuleTaskSplitter:
    """Splits one user turn into domain-level tasks using consultation rules."""

    def __init__(self, rule_repository: RuleRepository) -> None:
        self.rule_repository = rule_repository

    def split(self, user_text: str) -> list[SplitTask]:
        text = self._normalize(user_text)
        if not text:
            return []

        clauses = self._clauses(text)
        grouped: dict[str, list[str]] = {}
        domain_order: list[str] = []
        for clause in clauses:
            domains = self._matched_domains(clause)
            if not domains:
                domains = ["general"]
            for domain in domains:
                grouped.setdefault(domain, [])
                if domain not in domain_order:
                    domain_order.append(domain)
                grouped[domain].append(clause)

        meaningful_domains = [domain for domain in domain_order if domain != "general"]
        if len(meaningful_domains) <= 1:
            domain = meaningful_domains[0] if meaningful_domains else self._primary_domain(text)
            return [self._task(1, text, domain)]

        tasks: list[SplitTask] = []
        for index, domain in enumerate(meaningful_domains, start=1):
            task_text = "，".join(self._unique(grouped.get(domain, [])))
            tasks.append(self._task(index, task_text or text, domain))
        return tasks

    def _matched_domains(self, text: str) -> list[str]:
        rules = self.rule_repository.consultation_rules()
        domains: list[str] = []
        for domain_rule in sorted(rules.domains.values(), key=lambda item: item.priority):
            if domain_rule.domain == "general":
                continue
            if any(keyword and keyword in text for keyword in domain_rule.classifier_keywords):
                domains.append(domain_rule.domain)
        return domains

    def _primary_domain(self, text: str) -> str:
        matches = self._matched_domains(text)
        return matches[0] if matches else "general"

    def _task(self, index: int, text: str, domain: str) -> SplitTask:
        return SplitTask(
            task_id=f"task_{index:03d}",
            text=text.strip(),
            domain=domain,
            title=DOMAIN_TITLES.get(domain, "咨询问题"),
            reason="规则关键词命中",
        )

    def _clauses(self, text: str) -> list[str]:
        normalized = re.sub(r"\s+", " ", text)
        normalized = re.sub(r"(另外|还有|顺便|同时|再问一下|再问|然后|还有一个问题)", "。\\1", normalized)
        parts = re.split(r"[。！？!?；;\n]+", normalized)
        clauses: list[str] = []
        for part in parts:
            for clause in re.split(r"，另外|，还有|，顺便|，同时|，然后|,另外|,还有|,顺便|,同时|,然后", part):
                clause = clause.strip(" ，,。；;")
                if clause:
                    clauses.append(clause)
        return clauses or [text]

    def _normalize(self, text: str) -> str:
        return text.strip()

    def _unique(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result


class TaskSplitterAgent:
    """LLM TaskRouter sub-agent with deterministic rule fallback."""

    def __init__(
        self,
        rule_repository: RuleRepository,
        qwen: QwenClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.rule_repository = rule_repository
        self.qwen = qwen
        self.settings = settings
        self.rule_splitter = RuleTaskSplitter(rule_repository)

    async def split(
        self,
        user_text: str,
        *,
        model: str | None = None,
        pet_context_summary: str | None = None,
    ) -> TaskSplitDecision:
        if not self._llm_enabled():
            return self._fallback(user_text, "llm_task_router_unavailable")

        try:
            raw = await self.qwen.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是兽医多 Agent 系统中的 TaskRouterAgent。"
                            "只负责把用户单轮输入拆成任务，不做诊断、不回答医学建议。"
                            "必须只输出 JSON，不要输出 Markdown。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": self._prompt(user_text, pet_context_summary),
                    },
                ],
                model=model,
                temperature=0.0,
            )
            tasks = self._parse_llm_tasks(raw, user_text)
            if not tasks:
                return self._fallback(user_text, "llm_returned_no_valid_tasks")
            return TaskSplitDecision(tasks=tasks, strategy="llm_task_router")
        except Exception as exc:
            return self._fallback(user_text, f"{type(exc).__name__}")

    def _fallback(self, user_text: str, reason: str) -> TaskSplitDecision:
        return TaskSplitDecision(
            tasks=self.rule_splitter.split(user_text),
            strategy="rule_fallback",
            fallback_reason=reason,
        )

    def _llm_enabled(self) -> bool:
        if self.qwen is None or not self.qwen.available:
            return False
        if self.settings is not None and not self.settings.enable_llm_task_splitter:
            return False
        return True

    def _prompt(self, user_text: str, pet_context_summary: str | None) -> str:
        return (
            "请把用户这一轮话拆成 1 到 5 个任务。相同 domain 的内容应合并成一个任务；"
            "不同 domain 的内容应拆开。不要编造用户没说过的信息。\n\n"
            f"允许的 domain:\n{self._domain_catalog_json()}\n\n"
            "输出 JSON schema:\n"
            '{"tasks":[{"domain":"gastrointestinal","title":"消化道问题","text":"原文相关片段","priority":10,"reason":"为什么拆成这个任务"}]}\n\n'
            "字段要求:\n"
            "- domain 必须来自允许列表。\n"
            "- title 使用面向用户的短标题。\n"
            "- text 只能摘取或压缩用户原文，不要加入诊断建议。\n"
            "- priority: 急迫或核心主诉用较小数字；普通问题用较大数字。\n"
            "- 如果用户只有一个任务，也返回一个 tasks 元素。\n\n"
            f"系统已知宠物上下文摘要:\n{pet_context_summary or '暂无'}\n\n"
            f"用户输入:\n{user_text}"
        )

    def _domain_catalog_json(self) -> str:
        rules = self.rule_repository.consultation_rules()
        domains: list[dict[str, Any]] = []
        for domain_rule in sorted(rules.domains.values(), key=lambda item: item.priority):
            domains.append(
                {
                    "domain": domain_rule.domain,
                    "title": DOMAIN_TITLES.get(domain_rule.domain, domain_rule.domain),
                    "classifier_keywords": domain_rule.classifier_keywords[:12],
                    "required_slots": domain_rule.required_slots,
                }
            )
        return json.dumps(domains, ensure_ascii=False)

    def _parse_llm_tasks(self, raw: str, user_text: str) -> list[SplitTask]:
        payload = self._extract_json(raw)
        parsed = TaskRouterOutput.model_validate(payload)
        allowed_domains = set(self.rule_repository.consultation_rules().domains)
        tasks: list[SplitTask] = []
        seen_domains: set[str] = set()
        for item in sorted(parsed.tasks, key=lambda task: task.priority):
            domain = item.domain.strip()
            if domain not in allowed_domains:
                domain = "general"
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            text = item.text.strip() or user_text
            tasks.append(
                SplitTask(
                    task_id=f"task_{len(tasks) + 1:03d}",
                    text=text[:500],
                    domain=domain,
                    title=(item.title or DOMAIN_TITLES.get(domain) or domain)[:40],
                    priority=item.priority,
                    reason=item.reason[:160],
                )
            )
        return tasks

    def _extract_json(self, raw: str) -> dict[str, Any]:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.S)
            if not match:
                raise
            data = json.loads(match.group(0))
        if not isinstance(data, dict):
            raise ValueError("Task router output must be a JSON object")
        return data

from __future__ import annotations

from src.vet_agent.services.context import PetContext


class QuestionPlanner:
    def plan(self, user_text: str, pet_context: PetContext, max_questions: int = 3) -> list[str]:
        text = user_text.lower()
        questions: list[str] = []
        if not any(term in text for term in ("多久", "今天", "昨天", "小时", "天", "刚刚")):
            questions.append("这个情况从什么时候开始的？是突然发生还是逐渐出现？")
        if not any(term in text for term in ("精神", "食欲", "吃", "喝水")):
            questions.append("现在精神、食欲和饮水和平时比有明显变化吗？")
        if not any(term in text for term in ("吐", "拉", "咳", "喘", "疼", "尿", "便")):
            questions.append("有没有呕吐、腹泻、咳喘、疼痛、排尿或排便异常？")

        if pet_context.profile.get("weight_kg") in (None, "", "未知"):
            questions.append("系统里没有可靠体重记录，最近一次体重大约是多少？")
        return questions[:max_questions]

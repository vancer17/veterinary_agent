"""
文件：src/vet_agent/agents/question_planner.py
作用：提供多 Agent 协作中的任务拆分、安全、问诊、记忆抽取与回答生成能力。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from vet_agent.services import PetContext


class QuestionPlanner:
    def plan(self, user_text: str, pet_context: PetContext, max_questions: int = 3) -> list[str]:
        """执行 plan 业务逻辑。

        :param user_text: 用户输入文本。
        :param pet_context: 宠物上下文。
        :param max_questions: 最多追问数量。
        :return: 返回函数执行结果。
        """
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

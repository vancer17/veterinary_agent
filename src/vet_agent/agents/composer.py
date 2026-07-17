"""
文件：src/vet_agent/agents/composer.py
作用：提供多 Agent 协作中的任务拆分、安全、问诊、记忆抽取与回答生成能力。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from vet_agent import Evidence
from vet_agent.repositories import KnowledgeHit
from vet_agent.runtime import QwenClient
from vet_agent.services import PetContext

from .question_planner import QuestionPlanner
from .safety import SafetyAgent


class ResponseComposer:
    def __init__(self, qwen: QwenClient, safety: SafetyAgent, planner: QuestionPlanner) -> None:
        """初始化当前对象。

        :param qwen: 参数 qwen。
        :param safety: 参数 safety。
        :param planner: 参数 planner。
        :return: 无返回值。
        """
        self.qwen = qwen
        self.safety = safety
        self.planner = planner

    async def compose(
        self,
        *,
        user_text: str,
        pet_context: PetContext,
        memory: dict,
        knowledge_hits: list[KnowledgeHit],
        model: str,
        max_followup_questions: int,
        consultation_context: str | None = None,
        allow_followup: bool = True,
    ) -> tuple[str, list[Evidence]]:
        """根据上下文生成面向用户的回答。

        :param user_text: 用户输入文本。
        :param pet_context: 宠物上下文。
        :param memory: 参数 memory。
        :param knowledge_hits: 参数 knowledge_hits。
        :param model: 模型名称。
        :param max_followup_questions: 参数 max_followup_questions。
        :param consultation_context: 参数 consultation_context。
        :param allow_followup: 参数 allow_followup。
        :return: 返回函数执行结果。
        """
        questions = self.planner.plan(user_text, pet_context, max_followup_questions) if allow_followup else []
        knowledge_text = "\n".join(f"- {hit.title}: {hit.summary}" for hit in knowledge_hits)
        memory_text = memory.get("pet", {}).get("last_summary") or "暂无可用历史记忆"
        consultation_text = consultation_context or "尚未形成结构化问诊状态"
        mode_instruction = (
            "结构化问诊状态已足够。请给出阶段性最终建议，不要继续追问，除非出现急症兜底提醒。"
            if not allow_followup
            else f"信息可能仍不完整。每轮最多问 {max_followup_questions} 个关键问题。"
        )
        prompt = f"""
你是面向宠物主人的兽医 AI 助手。必须遵守:
1. 不能替代线下兽医诊断。
2. 涉及用药只能给方向，不能给具体剂量数字；必须提示按药品使用说明书或遵从兽医指导。
3. 不确定就说不确定，不能编造检查结果。
4. 使用大白话，给出依据，优先利用系统已知宠物数据，不重复追问已知品种、年龄、体重等。
5. {mode_instruction}

系统已知宠物数据:
{pet_context.summary()}

结构化问诊状态:
{consultation_text}

历史记忆:
{memory_text}

知识库摘要:
{knowledge_text}

用户输入:
{user_text}

请按以下结构回答:
- 分诊/紧急度
- 可能方向与依据
- 现在可以做什么
- 线下兽医兜底
"""
        try:
            raw = await self.qwen.chat(
                [
                    {"role": "system", "content": "你是严格遵守安全规则的宠物健康多 Agent 编排中的回复生成 Agent。"},
                    {"role": "user", "content": prompt},
                ],
                model=model,
            )
        except Exception:
            raw = self._fallback_reply()
        if questions and "还需要确认" not in raw:
            raw = f"{raw}\n\n还需要确认的问题:\n" + "\n".join(f"{index + 1}. {q}" for index, q in enumerate(questions))
        sanitized, _ = self.safety.sanitize_output(raw)
        return sanitized, pet_context.evidence

    def _fallback_reply(self) -> str:
        """执行 _fallback_reply 内部辅助逻辑。

        :return: 返回函数执行结果。
        """
        return (
            "分诊/紧急度: 当前模型服务暂时不可用，我先按保守安全原则给出通用分诊建议。\n"
            "可能方向与依据: 仅凭线上信息不能确诊，需要结合精神、食欲、呕吐腹泻、呼吸、疼痛和既往病史判断。\n"
            "现在可以做什么: 记录症状开始时间、频率、精神食欲和排泄变化，保持饮水，避免自行喂人药或不确定药物。\n"
            "线下兽医兜底: 如果出现呼吸困难、持续呕吐/腹泻、血便、无法站立、明显疼痛、误食毒物或症状加重，请尽快线下就诊。"
        )

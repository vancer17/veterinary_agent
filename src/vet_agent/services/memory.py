"""
文件：src/vet_agent/services/memory.py
作用：承载业务服务、记忆、报告解析、权限与治理逻辑。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from vet_agent import TrustedIdentity
from vet_agent.stores import JsonDocumentStore


class MemoryService:
    def __init__(self, store: JsonDocumentStore) -> None:
        """初始化当前对象。

        :param store: 参数 store。
        :return: 无返回值。
        """
        self.store = store

    async def read(self, identity: TrustedIdentity) -> dict[str, Any]:
        """读取指定范围内的持久化数据。

        :param identity: 可信身份信息。
        :return: 返回函数执行结果。
        """
        data = self.store.load()
        pet_memory = dict(data.get("pets", {}).get(identity.pet_id, {}))
        facts = pet_memory.get("facts")
        if isinstance(facts, dict):
            pet_memory["facts"] = list(facts.values())
        return {
            "owner": data.get("owners", {}).get(identity.user_id, {}),
            "pet": pet_memory,
            "session": data.get("sessions", {}).get(identity.session_id, {}),
        }

    async def remember_turn(
        self,
        identity: TrustedIdentity,
        *,
        user_text: str,
        summary: str,
        medical: bool,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """执行 remember_turn 业务逻辑。

        :param identity: 可信身份信息。
        :param user_text: 用户输入文本。
        :param summary: 参数 summary。
        :param medical: 是否属于医疗咨询回合。
        :param metadata: 附加元数据。
        :return: 返回函数执行结果。
        """
        data = self.store.load()
        data.setdefault("owners", {}).setdefault(identity.user_id, {})
        pet_memory = data.setdefault("pets", {}).setdefault(identity.pet_id, {"turns": []})
        session_memory = data.setdefault("sessions", {}).setdefault(identity.session_id, {"turns": []})
        item = {
            "at": datetime.now(UTC).isoformat(),
            "user_text": user_text[:500],
            "summary": summary[:1000],
            "medical": medical,
            "metadata": metadata or {},
        }
        pet_memory.setdefault("turns", []).append(item)
        session_memory.setdefault("turns", []).append(item)
        pet_memory["last_summary"] = summary[:1000]
        session_memory["last_summary"] = summary[:1000]
        self.store.save(data)

    async def read_consultation_state(self, identity: TrustedIdentity) -> dict[str, Any]:
        """执行 read_consultation_state 业务逻辑。

        :param identity: 可信身份信息。
        :return: 返回函数执行结果。
        """
        data = self.store.load()
        return (
            data.get("sessions", {})
            .get(identity.session_id, {})
            .get("consultation_state", {})
        )

    async def save_consultation_state(
        self,
        identity: TrustedIdentity,
        state: dict[str, Any],
    ) -> None:
        """执行 save_consultation_state 业务逻辑。

        :param identity: 可信身份信息。
        :param state: 参数 state。
        :return: 返回函数执行结果。
        """
        data = self.store.load()
        data.setdefault("owners", {}).setdefault(identity.user_id, {})
        data.setdefault("pets", {}).setdefault(identity.pet_id, {"turns": []})
        session_memory = data.setdefault("sessions", {}).setdefault(identity.session_id, {"turns": []})
        session_memory["consultation_state"] = state
        data["pets"][identity.pet_id]["consultation_state"] = state
        self.store.save(data)

    async def read_task_consultation_states(self, identity: TrustedIdentity) -> dict[str, Any]:
        """执行 read_task_consultation_states 业务逻辑。

        :param identity: 可信身份信息。
        :return: 返回函数执行结果。
        """
        data = self.store.load()
        return (
            data.get("sessions", {})
            .get(identity.session_id, {})
            .get("task_consultation_states", {})
        )

    async def save_task_consultation_states(
        self,
        identity: TrustedIdentity,
        states: dict[str, Any],
    ) -> None:
        """替换当前会话仍未完成的多任务问诊状态。

        :param identity: 可信身份信息。
        :param states: 未完成任务的活跃问诊状态集合。
        :return: 返回函数执行结果。
        """
        data = self.store.load()
        data.setdefault("owners", {}).setdefault(identity.user_id, {})
        data.setdefault("pets", {}).setdefault(identity.pet_id, {"turns": []})
        session_memory = data.setdefault("sessions", {}).setdefault(identity.session_id, {"turns": []})
        session_memory["task_consultation_states"] = states
        data["pets"][identity.pet_id]["task_consultation_states"] = states
        self.store.save(data)

    async def clear_default_consultation_state(self, identity: TrustedIdentity) -> None:
        """清理当前会话的默认活跃问诊状态。

        :param identity: 可信身份信息。
        :return: 返回函数执行结果。
        """
        data = self.store.load()
        data.get("sessions", {}).get(identity.session_id, {}).pop("consultation_state", None)
        data.get("pets", {}).get(identity.pet_id, {}).pop("consultation_state", None)
        self.store.save(data)

    async def clear_consultation_state(self, identity: TrustedIdentity) -> None:
        """清理当前会话所有活跃问诊状态。

        :param identity: 可信身份信息。
        :return: 返回函数执行结果。
        """
        data = self.store.load()
        data.get("sessions", {}).get(identity.session_id, {}).pop("consultation_state", None)
        data.get("sessions", {}).get(identity.session_id, {}).pop("task_consultation_states", None)
        data.get("pets", {}).get(identity.pet_id, {}).pop("consultation_state", None)
        data.get("pets", {}).get(identity.pet_id, {}).pop("task_consultation_states", None)
        self.store.save(data)

    async def delete_pet_memory(self, pet_id: str, user_id: str | None = None) -> None:
        """执行 delete_pet_memory 业务逻辑。

        :param pet_id: 参数 pet_id。
        :param user_id: 参数 user_id。
        :return: 返回函数执行结果。
        """
        data = self.store.load()
        data.get("pets", {}).pop(pet_id, None)
        self.store.save(data)

    async def upsert_pet_fact(
        self,
        identity: TrustedIdentity,
        *,
        fact_type: str,
        fact_key: str,
        fact_value: str,
        confidence: float = 1.0,
        source_turn_id: str | None = None,
        source_text: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """执行 upsert_pet_fact 业务逻辑。

        :param identity: 可信身份信息。
        :param fact_type: 事实类型。
        :param fact_key: 事实键名。
        :param fact_value: 事实内容。
        :param confidence: 置信度。
        :param source_turn_id: 参数 source_turn_id。
        :param source_text: 事实来源文本。
        :param metadata: 附加元数据。
        :return: 返回函数执行结果。
        """
        data = self.store.load()
        pet_memory = data.setdefault("pets", {}).setdefault(identity.pet_id, {"turns": []})
        facts = pet_memory.setdefault("facts", {})
        key = f"{fact_type}:{fact_key}"
        facts[key] = {
            "fact_type": fact_type,
            "fact_key": fact_key,
            "fact_value": fact_value,
            "confidence": confidence,
            "source_turn_id": source_turn_id,
            "source_text": source_text,
            "metadata": metadata or {},
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self.store.save(data)

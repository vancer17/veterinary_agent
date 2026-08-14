"""
文件：src/vet_agent/services/memory.py
作用：承载业务服务、记忆、报告解析、权限与治理逻辑。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from vet_agent import TrustedIdentity
from vet_agent.repositories import ConsultationStateRepository, JsonConsultationStateRepository
from vet_agent.stores import JsonDocumentStore


class MemoryService:
    """基于 JSON 文档的记忆业务服务，供显式测试或嵌入场景使用。

    :return: 无返回值。
    """

    def __init__(
        self,
        store: JsonDocumentStore,
        consultation_state_repository: ConsultationStateRepository | None = None,
    ) -> None:
        """初始化 JSON 记忆业务服务及问诊状态仓储。

        :param store: JSON 记忆文档存储。
        :param consultation_state_repository: 活跃问诊状态仓储；
            未提供时使用当前文档存储构造测试实现。
        :return: 无返回值。
        """
        self.store = store
        self.consultation_state_repository = (
            consultation_state_repository
            or JsonConsultationStateRepository(store)
        )

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
        """通过问诊状态仓储读取默认活跃问诊状态。

        :param identity: 当前可信用户、宠物与会话范围。
        :return: 返回默认活跃问诊状态。
        """
        return self.consultation_state_repository.read_default(identity)

    async def save_consultation_state(
        self,
        identity: TrustedIdentity,
        state: dict[str, Any],
    ) -> None:
        """通过问诊状态仓储保存默认活跃问诊状态。

        :param identity: 当前可信用户、宠物与会话范围。
        :param state: 待保存的结构化问诊状态。
        :return: 无返回值。
        """
        self.consultation_state_repository.save_default(identity, state)

    async def read_task_consultation_states(self, identity: TrustedIdentity) -> dict[str, Any]:
        """通过问诊状态仓储读取多任务活跃问诊状态。

        :param identity: 当前可信用户、宠物与会话范围。
        :return: 返回任务键到问诊状态的映射。
        """
        return self.consultation_state_repository.read_tasks(identity)

    async def save_task_consultation_states(
        self,
        identity: TrustedIdentity,
        states: dict[str, Any],
        *,
        clear_default_state: bool = False,
    ) -> None:
        """通过问诊状态仓储替换多任务活跃问诊状态。

        :param identity: 当前可信用户、宠物与会话范围。
        :param states: 未完成任务的活跃问诊状态集合。
        :param clear_default_state: 是否在同一次写入中清理默认问诊状态；
            仅用于 __default__ 迁移到具体任务键的场景。
        :return: 无返回值。
        """
        self.consultation_state_repository.replace_tasks(
            identity,
            states,
            clear_default=clear_default_state,
        )

    async def clear_default_consultation_state(self, identity: TrustedIdentity) -> None:
        """通过问诊状态仓储清理默认活跃问诊状态。

        :param identity: 当前可信用户、宠物与会话范围。
        :return: 无返回值。
        """
        self.consultation_state_repository.clear_default(identity)

    async def clear_consultation_state(self, identity: TrustedIdentity) -> None:
        """通过问诊状态仓储清理全部活跃问诊状态。

        :param identity: 当前可信用户、宠物与会话范围。
        :return: 无返回值。
        """
        self.consultation_state_repository.clear_all(identity)

    async def delete_pet_memory(self, pet_id: str, user_id: str | None = None) -> None:
        """执行 delete_pet_memory 业务逻辑。

        :param pet_id: 参数 pet_id。
        :param user_id: 参数 user_id。
        :return: 返回函数执行结果。
        """
        data = self.store.load()
        data.get("pets", {}).pop(pet_id, None)
        self.store.save(data)
        self.consultation_state_repository.delete_for_pet(pet_id, user_id)

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

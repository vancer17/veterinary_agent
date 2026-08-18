"""
文件：src/vet_agent/services/postgres_memory.py
作用：承载业务服务、记忆、报告解析、权限与治理逻辑。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from typing import Any

from vet_agent import TrustedIdentity
from vet_agent.memory import MemoryReadService
from vet_agent.repositories import (
    ConsultationStateRepository,
    MemoryWriteRepository,
    PostgresConsultationStateRepository,
    PostgresMemoryWriteRepository,
)

from .semantic_memory import DisabledSemanticMemory, SemanticMemoryWriter


class PostgresMemoryService:
    def __init__(
        self,
        database_url: str,
        memory_read_service: MemoryReadService,
        semantic_memory: SemanticMemoryWriter | None = None,
        consultation_state_repository: ConsultationStateRepository | None = None,
        memory_write_repository: MemoryWriteRepository | None = None,
    ) -> None:
        """初始化 PostgreSQL 记忆业务服务及其数据仓储。

        :param database_url: 数据库连接地址。
        :param memory_read_service: 结构化记忆读取服务，用于兼容旧管理接口读取。
        :param semantic_memory: 语义记忆投影写入客户端。
        :param consultation_state_repository: 活跃问诊状态仓储；未提供时按数据库地址构造 PostgreSQL 实现。
        :param memory_write_repository: 结构化记忆写入仓储；未提供时按数据库地址构造 PostgreSQL 实现。
        :return: 无返回值。
        """
        self.semantic_memory = semantic_memory or DisabledSemanticMemory()
        self.memory_read_service = memory_read_service
        self.consultation_state_repository = (
            consultation_state_repository
            or PostgresConsultationStateRepository(database_url)
        )
        self.memory_write_repository = (
            memory_write_repository
            or PostgresMemoryWriteRepository(database_url)
        )

    async def read(self, identity: TrustedIdentity) -> dict[str, Any]:
        """读取指定范围内的持久化数据。

        :param identity: 可信身份信息。
        :return: 返回函数执行结果。
        """
        bundle = await self.memory_read_service.read_snapshot(identity)
        return bundle.to_legacy_dict()

    async def remember_turn(
        self,
        identity: TrustedIdentity,
        *,
        user_text: str,
        summary: str,
        medical: bool,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """编排当前回合的结构化记忆写入与语义记忆投影。

        :param identity: 当前可信用户、宠物与会话范围。
        :param user_text: 用户输入文本。
        :param summary: Agent 回合摘要或响应摘要。
        :param medical: 是否属于医疗咨询回合。
        :param metadata: 回合附加审计元数据。
        :return: 无返回值。
        """
        write_metadata = metadata or {}
        self.memory_write_repository.remember_turn(
            identity,
            user_text=user_text,
            summary=summary,
            medical=medical,
            metadata=write_metadata,
        )
        await self._semantic_add_turn(identity, user_text=user_text, summary=summary, metadata=write_metadata)

    async def read_consultation_state(self, identity: TrustedIdentity) -> dict[str, Any]:
        """通过问诊状态仓储读取默认活跃问诊状态。

        :param identity: 当前可信用户、宠物与会话范围。
        :return: 返回默认活跃问诊状态。
        """
        return self.consultation_state_repository.read_default(identity)

    async def save_consultation_state(self, identity: TrustedIdentity, state: dict[str, Any]) -> None:
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
        :param clear_default_state: 是否同时清理默认问诊状态；用于默认任务迁移到具体任务键的场景。
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
        """编排结构化记忆、活跃问诊状态和语义投影的宠物范围删除。

        :param pet_id: 待删除的宠物标识。
        :param user_id: 可选的用户范围限制。
        :return: 无返回值。
        """
        self.memory_write_repository.delete_for_pet(pet_id, user_id)
        self.consultation_state_repository.delete_for_pet(pet_id, user_id)
        await self._semantic_delete_pet(pet_id, user_id=user_id)

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
        """通过结构化记忆写入仓储保存宠物长期事实。

        :param identity: 当前可信用户与宠物范围。
        :param fact_type: 事实类型。
        :param fact_key: 事实键名。
        :param fact_value: 事实内容。
        :param confidence: 置信度。
        :param source_turn_id: 参数 source_turn_id。
        :param source_text: 事实来源文本。
        :param metadata: 附加元数据。
        :return: 无返回值。
        """
        self.memory_write_repository.upsert_pet_fact(
            identity,
            fact_type=fact_type,
            fact_key=fact_key,
            fact_value=fact_value,
            confidence=confidence,
            source_turn_id=source_turn_id,
            source_text=source_text,
            metadata=metadata or {"source": "manual_correction"},
        )

    async def _semantic_add_turn(
        self,
        identity: TrustedIdentity,
        *,
        user_text: str,
        summary: str,
        metadata: dict[str, Any],
    ) -> None:
        """执行 _semantic_add_turn 内部辅助逻辑。

        :param identity: 可信身份信息。
        :param user_text: 用户输入文本。
        :param summary: 参数 summary。
        :param metadata: 附加元数据。
        :return: 返回函数执行结果。
        """
        try:
            await self.semantic_memory.add_turn(identity, user_text=user_text, summary=summary, metadata=metadata)
        except Exception:
            return None

    async def _semantic_delete_pet(self, pet_id: str, *, user_id: str | None = None) -> None:
        """执行 _semantic_delete_pet 内部辅助逻辑。

        :param pet_id: 参数 pet_id。
        :param user_id: 参数 user_id。
        :return: 返回函数执行结果。
        """
        try:
            await self.semantic_memory.delete_pet(pet_id, user_id=user_id)
        except Exception:
            return None

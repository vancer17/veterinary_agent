"""
文件：src/vet_agent/input_safety/repository.py
作用：提供基础输入安全候选定义的仓储协议与 PostgreSQL 实现。
范围：仅允许本文件直接访问 input_safety_candidate_definitions 数据表；业务层通过 InputSafetyRepository 协议访问。
说明：候选定义用于描述结构化检测器输出的策略语义，不实现文本关键词扫描或硬编码状态机。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from vet_agent.db import InputSafetyCandidateDefinitionModel, make_session_factory
from vet_agent.input_safety.models import InputSafetyCandidateCategory


@dataclass(frozen=True)
class InputSafetyCandidateDefinition:
    """表示基础输入安全候选定义。

    :param code: 候选编码。
    :param category: 候选类别。
    :param default_severity: 候选默认严重级别。
    :param message: 候选默认说明。
    :param detector: 候选来源检测器标识。
    :param enabled: 候选定义是否启用。
    :param version: 候选定义版本。
    :param metadata: 附加审计信息。
    :return: 无返回值。
    """

    code: str
    category: InputSafetyCandidateCategory
    default_severity: str
    message: str
    detector: str
    enabled: bool = True
    version: str = "v1"
    metadata: dict[str, Any] = field(default_factory=dict)


class InputSafetyRepository(Protocol):
    """定义基础输入安全候选定义仓储协议。

    :return: 无返回值。
    """

    def definitions(self) -> tuple[InputSafetyCandidateDefinition, ...]:
        """读取已启用的基础输入安全候选定义。

        :return: 返回候选定义元组。
        """
        ...

    def definition_by_code(self, code: str) -> InputSafetyCandidateDefinition | None:
        """按候选编码读取基础输入安全候选定义。

        :param code: 候选编码。
        :return: 存在时返回候选定义，否则返回 None。
        """
        ...

    def is_ready(self) -> bool:
        """检查候选定义仓储是否可用。

        :return: 仓储可访问时返回 True。
        """
        ...


class PostgresInputSafetyRepository(InputSafetyRepository):
    """基于 PostgreSQL 的基础输入安全候选定义仓储。

    :return: 无返回值。
    """

    def __init__(self, database_url: str) -> None:
        """初始化 PostgreSQL 输入安全候选仓储。

        :param database_url: 数据库连接地址。
        :return: 无返回值。
        """
        self.session_factory = make_session_factory(database_url)

    def definitions(self) -> tuple[InputSafetyCandidateDefinition, ...]:
        """读取已启用的基础输入安全候选定义。

        :return: 返回候选定义元组。
        """
        with self.session_factory() as session:
            rows = session.scalars(
                select(InputSafetyCandidateDefinitionModel)
                .where(InputSafetyCandidateDefinitionModel.enabled.is_(True))
                .order_by(InputSafetyCandidateDefinitionModel.priority, InputSafetyCandidateDefinitionModel.code)
            ).all()
        return tuple(_definition_from_row(row) for row in rows)

    def definition_by_code(self, code: str) -> InputSafetyCandidateDefinition | None:
        """按候选编码读取基础输入安全候选定义。

        :param code: 候选编码。
        :return: 存在时返回候选定义，否则返回 None。
        """
        normalized = code.strip()
        if not normalized:
            return None
        with self.session_factory() as session:
            row = session.scalar(
                select(InputSafetyCandidateDefinitionModel).where(
                    InputSafetyCandidateDefinitionModel.code == normalized,
                    InputSafetyCandidateDefinitionModel.enabled.is_(True),
                )
            )
        return _definition_from_row(row) if row is not None else None

    def is_ready(self) -> bool:
        """检查候选定义仓储是否可用。

        :return: 数据表可访问且存在启用定义时返回 True。
        """
        try:
            with self.session_factory() as session:
                count = session.scalar(
                    select(func.count())
                    .select_from(InputSafetyCandidateDefinitionModel)
                    .where(InputSafetyCandidateDefinitionModel.enabled.is_(True))
                )
            return int(count or 0) > 0
        except SQLAlchemyError:
            return False


class StaticInputSafetyRepository(InputSafetyRepository):
    """基于内存定义的输入安全候选仓储。

    说明：该实现仅供测试或受控嵌入场景显式注入使用，生产容器默认不会构造该仓储。

    :return: 无返回值。
    """

    def __init__(self, definitions: tuple[InputSafetyCandidateDefinition, ...] = ()) -> None:
        """初始化内存输入安全候选仓储。

        :param definitions: 预置候选定义。
        :return: 无返回值。
        """
        self._definitions = definitions

    def definitions(self) -> tuple[InputSafetyCandidateDefinition, ...]:
        """读取已启用的基础输入安全候选定义。

        :return: 返回候选定义元组。
        """
        return tuple(definition for definition in self._definitions if definition.enabled)

    def definition_by_code(self, code: str) -> InputSafetyCandidateDefinition | None:
        """按候选编码读取基础输入安全候选定义。

        :param code: 候选编码。
        :return: 存在时返回候选定义，否则返回 None。
        """
        normalized = code.strip()
        for definition in self.definitions():
            if definition.code == normalized:
                return definition
        return None

    def is_ready(self) -> bool:
        """检查内存候选定义仓储是否可用。

        :return: 始终返回 True。
        """
        return True


def _definition_from_row(row: InputSafetyCandidateDefinitionModel) -> InputSafetyCandidateDefinition:
    """将数据库行转换为输入安全候选定义。

    :param row: 输入安全候选定义数据表行。
    :return: 返回仓储层候选定义。
    """
    return InputSafetyCandidateDefinition(
        code=row.code,
        category=InputSafetyCandidateCategory(row.category),
        default_severity=row.default_severity,
        message=row.message,
        detector=row.detector,
        enabled=row.enabled,
        version=row.version,
        metadata=row.metadata_json or {},
    )

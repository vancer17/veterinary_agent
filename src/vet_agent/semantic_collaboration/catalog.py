"""
=============================================================================
文件：src/vet_agent/semantic_collaboration/catalog.py
作用：实现受限语义协作 DAG 的 M01 SkillCatalog 与全局契约校验。
范围：覆盖 SKILL 注册、身份解析、字段所有权矩阵、修复映射闭合、目录冻结
      与稳定契约摘要。
说明：本文件是进程内 immutable 生产目录，不访问数据库、不动态扫描插件、
      不解析 Markdown 作为运行时契约，也不提供失败回退。
=============================================================================
"""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from .contracts import (
    FieldOwnershipPath,
    RepairMapping,
    SkillSpec,
    SkillTaskKind,
)
from .errors import SkillCatalogError


def _ownership_record_sort_key(record: SkillOwnershipRecord) -> str:
    """读取所有权记录的排序键。

    :param record: 字段所有权矩阵记录。
    :return: 返回规范化字段路径字符串。
    """
    return record.path.path


def _repair_mapping_record_sort_key(
    record: SkillRepairMappingRecord,
) -> tuple[str, str, str]:
    """读取修复映射记录的排序键。

    :param record: 目录级修复映射记录。
    :return: 返回来源标识、来源版本与失败码组成的排序键。
    """
    return (
        record.source_skill_id,
        record.source_skill_version,
        record.mapping.failure_code.value,
    )


class SkillOwnershipRecord(BaseModel):
    """表示字段所有权矩阵中的单条权威记录。

    :param skill_id: 拥有字段的 SKILL 标识。
    :param skill_version: 拥有字段的 SKILL 版本。
    :param path: 被拥有的规范化输出字段路径。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str = Field(description="拥有该字段的 SKILL 标识。")
    skill_version: str = Field(description="拥有该字段的 SKILL 版本。")
    path: FieldOwnershipPath = Field(description="被拥有的输出字段路径。")


class SkillOwnershipMatrix(BaseModel):
    """表示全目录字段所有权与冲突检测结果。

    :param records: 按路径排序的所有权记录集合。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    records: tuple[SkillOwnershipRecord, ...] = Field(
        description="当前目录的完整字段所有权矩阵。",
    )

    def records_for_path(
        self, path: FieldOwnershipPath
    ) -> tuple[SkillOwnershipRecord, ...]:
        """读取指定路径的直接所有权记录。

        :param path: 规范化输出字段路径。
        :return: 返回与该路径完全匹配的所有权记录。
        """
        return tuple(record for record in self.records if record.path == path)


class SkillRepairMappingRecord(BaseModel):
    """表示目录中可被 Repair Planner 消费的修复映射记录。

    :param source_skill_id: 发生失败的 SKILL 标识。
    :param source_skill_version: 发生失败的 SKILL 版本。
    :param mapping: 白名单修复映射契约。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_skill_id: str = Field(description="发生失败的 SKILL 标识。")
    source_skill_version: str = Field(description="发生失败的 SKILL 版本。")
    mapping: RepairMapping = Field(description="失败码到修复任务的映射。")


class SkillRegistry:
    """表示面向 Plan Validator 和调度层的只读 SKILL 解析门面。

    :param catalog: 已冻结的生产 SkillCatalog。
    :return: 无返回值。
    """

    def __init__(self, catalog: SkillCatalog) -> None:
        """初始化只读 SKILL 注册门面。

        :param catalog: 已冻结的生产 SkillCatalog。
        :return: 无返回值。
        :raises SkillCatalogError: 目录未冻结时抛出。
        """
        if not catalog.frozen:
            raise SkillCatalogError("skill registry requires a frozen catalog")
        self._catalog = catalog

    def get(
        self,
        skill_id: str,
        skill_version: str | None = None,
    ) -> SkillSpec | None:
        """按身份读取已注册 SkillSpec。

        :param skill_id: SKILL 稳定标识。
        :param skill_version: 可选精确版本。
        :return: 找到时返回 SkillSpec，否则返回 None。
        """
        return self._catalog.get(skill_id, skill_version)

    def require(
        self,
        skill_id: str,
        skill_version: str | None = None,
    ) -> SkillSpec:
        """按身份强制解析已注册 SkillSpec。

        :param skill_id: SKILL 稳定标识。
        :param skill_version: 可选精确版本。
        :return: 返回对应的权威 SkillSpec。
        :raises SkillCatalogError: SKILL 缺失或版本不唯一时抛出。
        """
        return self._catalog.require(skill_id, skill_version)

    def specs_for_task(self, task_kind_value: str) -> tuple[SkillSpec, ...]:
        """按稳定任务类型枚举 SKILL。

        :param task_kind_value: 任务类型枚举值。
        :return: 返回匹配任务类型的 SkillSpec 元组。
        """
        return self._catalog.specs_for_task(task_kind_value)

    def ownership_matrix(self) -> SkillOwnershipMatrix:
        """读取目录字段所有权矩阵。

        :return: 返回不可变字段所有权矩阵。
        """
        return self._catalog.ownership_matrix()

    def repair_mappings(self) -> tuple[SkillRepairMappingRecord, ...]:
        """读取目录修复映射目录。

        :return: 返回按来源身份与失败码排序的修复映射元组。
        """
        return self._catalog.repair_mappings()

    def contract_digest(self) -> str:
        """读取目录稳定契约摘要。

        :return: 返回 SkillCatalog 的 SHA-256 契约摘要。
        """
        return self._catalog.contract_digest()


class SkillCatalog:
    """表示生产启动期构建后冻结的权威 SKILL 目录。

    :param initial_specs: 初始 SKILL 契约集合。
    :return: 无返回值。
    """

    def __init__(self, initial_specs: Iterable[SkillSpec] = ()) -> None:
        """初始化可构建的 SKILL 目录。

        :param initial_specs: 初始 SKILL 契约集合。
        :return: 无返回值。
        """
        self._specs: dict[tuple[str, str], SkillSpec] = {}
        self._ownership: dict[FieldOwnershipPath, SkillSpec] = {}
        self._frozen = False
        for spec in initial_specs:
            self.register(spec)

    @property
    def frozen(self) -> bool:
        """读取目录是否已冻结。

        :return: 目录冻结时返回 True。
        """
        return self._frozen

    def register(self, spec: SkillSpec) -> None:
        """注册一个 SkillSpec 并立即执行局部一致性校验。

        :param spec: 待注册的权威 SKILL 契约。
        :return: 无返回值。
        :raises SkillCatalogError: 目录冻结、身份重复或字段所有权冲突时抛出。
        """
        if self._frozen:
            raise SkillCatalogError("skill catalog is frozen")
        identity = spec.identity()
        if identity in self._specs:
            raise SkillCatalogError("duplicate skill identity")
        for owned_path in spec.owns:
            for existing_path, current in self._ownership.items():
                if existing_path.conflicts_with(owned_path):
                    raise SkillCatalogError(
                        "field ownership conflict: "
                        f"{current.skill_id}@{current.skill_version} and "
                        f"{spec.skill_id}@{spec.skill_version} conflict on "
                        f"{owned_path.path}"
                    )
        self._specs[identity] = spec
        for owned_path in spec.owns:
            self._ownership[owned_path] = spec

    def validate(self) -> None:
        """执行目录级闭合校验并暴露未闭合修复映射。

        :return: 无返回值。
        :raises SkillCatalogError: 修复目标缺失、身份重复或所有权冲突时抛出。
        """
        identities = list(self._specs)
        if len(identities) != len(set(identities)):
            raise SkillCatalogError("duplicate skill identity")
        matrix = self.ownership_matrix()
        paths = [record.path for record in matrix.records]
        if len(paths) != len(set(paths)):
            raise SkillCatalogError("duplicate field ownership")
        for spec in self._specs.values():
            for mapping in spec.repair_mappings:
                target_identity = (
                    mapping.repair_skill_id,
                    mapping.repair_skill_version,
                )
                target = self._specs.get(target_identity)
                if target is None:
                    raise SkillCatalogError(
                        f"repair target is not registered: {target_identity[0]}@{target_identity[1]}"
                    )
                if target.task_kind != SkillTaskKind.REPAIR:
                    raise SkillCatalogError(
                        "repair mapping target is not a repair skill"
                    )

    def freeze(self) -> Self:
        """校验并冻结目录，禁止运行期动态注册。

        :return: 返回当前已冻结目录，便于组合根链式构建。
        :raises SkillCatalogError: 目录闭合校验失败时抛出。
        """
        self.validate()
        self._frozen = True
        return self

    def get(
        self,
        skill_id: str,
        skill_version: str | None = None,
    ) -> SkillSpec | None:
        """按身份读取已注册 SkillSpec。

        :param skill_id: SKILL 稳定标识。
        :param skill_version: 可选精确版本；缺省时要求当前目录只有唯一版本。
        :return: 找到时返回 SkillSpec，否则返回 None。
        """
        if skill_version is not None:
            return self._specs.get((skill_id, skill_version))
        matches = tuple(
            spec
            for (current_id, _), spec in self._specs.items()
            if current_id == skill_id
        )
        if len(matches) == 1:
            return matches[0]
        return None

    def require(
        self,
        skill_id: str,
        skill_version: str | None = None,
    ) -> SkillSpec:
        """按身份强制解析已注册 SkillSpec。

        :param skill_id: SKILL 稳定标识。
        :param skill_version: 可选精确版本；缺省时要求当前目录只有唯一版本。
        :return: 返回对应的权威 SkillSpec。
        :raises SkillCatalogError: SKILL 缺失或版本不唯一时抛出。
        """
        spec = self.get(skill_id, skill_version)
        if spec is None:
            raise SkillCatalogError(
                f"skill is not registered or version is ambiguous: {skill_id}"
            )
        return spec

    def list_specs(self) -> tuple[SkillSpec, ...]:
        """列出目录中的全部 SkillSpec。

        :return: 返回按身份排序后的 SkillSpec 元组。
        """
        return tuple(self._specs[key] for key in sorted(self._specs))

    def specs_for_task(self, task_kind_value: str) -> tuple[SkillSpec, ...]:
        """按任务类型枚举可用 SKILL。

        :param task_kind_value: 稳定任务类型枚举值。
        :return: 返回匹配任务类型的 SkillSpec 元组。
        """
        return tuple(
            spec
            for spec in self.list_specs()
            if spec.task_kind.value == task_kind_value
        )

    def ownership_matrix(self) -> SkillOwnershipMatrix:
        """构建当前目录的字段所有权矩阵。

        :return: 返回按路径排序的不可变所有权矩阵。
        """
        records = tuple(
            SkillOwnershipRecord(
                skill_id=spec.skill_id,
                skill_version=spec.skill_version,
                path=path,
            )
            for spec in self.list_specs()
            for path in spec.owns
        )
        return SkillOwnershipMatrix(
            records=tuple(sorted(records, key=_ownership_record_sort_key))
        )

    def registry(self) -> SkillRegistry:
        """从已冻结目录创建只读注册门面。

        :return: 返回供后续 Plan Validator 消费的 SkillRegistry。
        :raises SkillCatalogError: 目录未冻结时抛出。
        """
        return SkillRegistry(self)

    def repair_mappings(self) -> tuple[SkillRepairMappingRecord, ...]:
        """构建目录级修复映射记录。

        :return: 返回按来源身份与失败码排序的修复映射元组。
        """
        records = tuple(
            SkillRepairMappingRecord(
                source_skill_id=spec.skill_id,
                source_skill_version=spec.skill_version,
                mapping=mapping,
            )
            for spec in self.list_specs()
            for mapping in spec.repair_mappings
        )
        return tuple(
            sorted(
                records,
                key=_repair_mapping_record_sort_key,
            )
        )

    def contract_digest(self) -> str:
        """计算整个 SkillCatalog 的稳定契约摘要。

        :return: 返回目录内全部 SkillSpec 的 SHA-256 摘要。
        """
        canonical = "\n".join(spec.canonical_json() for spec in self.list_specs())
        return sha256(canonical.encode("utf-8")).hexdigest()

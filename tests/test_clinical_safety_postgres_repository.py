"""
=============================================================================
文件：tests/test_clinical_safety_postgres_repository.py
作用：验证 PostgreSQL 临床安全仓储的结构化范围过滤 SQL 与业务层语义矩阵一致。
范围：仅覆盖 SQL 条件生成契约，不依赖真实数据库连接，不验证向量排序或发布数据。
说明：该测试锁定“空范围不限制、受控值包含匹配、unknown 不生成过滤条件”的三条
      召回过滤不变量，防止 SQL 实现与 matches_asset 防御判断发生语义漂移。
=============================================================================
"""

from __future__ import annotations

from sqlalchemy import literal, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import ColumnElement

from vet_agent.clinical_safety import (
    ClinicalSafetyRetrievalScope,
    PostgresClinicalSafetyRepository,
)


def _compile_scope_filters(scope: ClinicalSafetyRetrievalScope) -> str:
    """编译结构化范围过滤条件为 PostgreSQL 方言 SQL。

    :param scope: 临床安全召回使用的结构化适用范围。
    :return: 返回字面量绑定后的 SQL 文本；无过滤条件时返回占位查询文本。
    """
    repository = PostgresClinicalSafetyRepository(
        "postgresql://vet_agent:vet_agent@127.0.0.1:1/vet_agent"
    )
    filters: tuple[ColumnElement[bool], ...] = repository._scope_filters(scope)
    statement = select(*filters) if filters else select(literal(1))
    compiled = statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    return " ".join(str(compiled).split())


def test_unknown_scope_generates_no_sql_filters() -> None:
    """验证 unknown 范围不生成过滤条件。

    :return: 无返回值；断言通过表示画像未知时 SQL 层不推断默认物种、性别或年龄。
    """
    assert _compile_scope_filters(ClinicalSafetyRetrievalScope()) == "SELECT 1 AS anon_1"


def test_known_scope_filters_use_empty_or_contains_semantics() -> None:
    """验证已知范围使用“空数组不限制或包含当前值”的 SQL 语义。

    :return: 无返回值；断言通过表示 SQL 过滤与业务层 matches_asset 判断保持同一矩阵。
    """
    compiled_sql = _compile_scope_filters(
        ClinicalSafetyRetrievalScope(species="dog", sex="female", age_group="adult")
    )

    assert "cardinality(clinical_safety_assets.species_scope) = 0" in compiled_sql
    assert "clinical_safety_assets.species_scope AS TEXT[]) @> ARRAY['dog']" in compiled_sql
    assert "cardinality(clinical_safety_assets.sex_scope) = 0" in compiled_sql
    assert "clinical_safety_assets.sex_scope AS TEXT[]) @> ARRAY['female']" in compiled_sql
    assert "cardinality(clinical_safety_assets.age_scope) = 0" in compiled_sql
    assert "clinical_safety_assets.age_scope AS TEXT[]) @> ARRAY['adult']" in compiled_sql

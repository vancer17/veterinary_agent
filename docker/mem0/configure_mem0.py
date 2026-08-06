"""
文件：docker/mem0/configure_mem0.py
作用：在 Mem0 REST Server 启动前写入项目所需的运行时配置覆盖。
范围：当前仅维护 pgvector embedding 维度配置，不创建业务表，不导入业务数据。
说明：Mem0 上游服务暂未通过环境变量暴露 pgvector 维度，本脚本在 Alembic 后写入 settings 表。
"""

from __future__ import annotations

import json
import os
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL


def deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """递归合并配置字典。

    :param base: 原始配置字典。
    :param updates: 需要覆盖或补充的配置字典。
    :return: 合并后的新配置字典。
    """
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def main() -> None:
    """执行 Mem0 启动前配置写入流程。

    :return: 无返回值。
    """
    dims = int(os.getenv("MEM0_EMBEDDING_MODEL_DIMS", "1024"))
    database = os.getenv("APP_DB_NAME", "mem0_app")

    url = URL.create(
        "postgresql+psycopg",
        username=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=database,
    )
    engine = create_engine(url, pool_pre_ping=True)

    update = {"vector_store": {"config": {"embedding_model_dims": dims}}}
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT value FROM settings WHERE key = 'config_overrides'")
        ).scalar_one_or_none()
        overrides = json.loads(existing) if existing else {}
        overrides = deep_merge(overrides, update)
        conn.execute(
            text(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES ('config_overrides', :value, now())
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    updated_at = now()
                """
            ),
            {"value": json.dumps(overrides, ensure_ascii=False)},
        )

    print(f"Mem0 pgvector embedding dimensions configured: {dims}")


if __name__ == "__main__":
    main()

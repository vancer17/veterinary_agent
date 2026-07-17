"""
文件：docker/mem0/configure_mem0.py
作用：提供容器启动前的中间件辅助配置脚本。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


# File: docker/mem0/configure_mem0.py
# Purpose: Inject runtime Mem0 server overrides before the REST API starts.
# Notes: The official Mem0 server currently does not expose pgvector dimensions
#        through environment variables, so this script persists the override in
#        Mem0's own settings table after its Alembic migrations have run.

from __future__ import annotations

import json
import os
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL


def deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """执行 deep_merge 业务逻辑。

    :param base: 基础数据。
    :param updates: 参数 updates。
    :return: 返回函数执行结果。
    """
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def main() -> None:
    """执行命令行入口逻辑。

    :return: 返回函数执行结果。
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

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
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def main() -> None:
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

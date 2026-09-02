"""Content-addressed run cache for V7 attribution experiments."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class V7RunUnitKey:
    """Every version and input that can change a model result."""

    experiment_id: str
    model: str
    prompt_version: str
    schema_version: str
    input_digest: str
    turn_context_digest: str
    adapter: str = "unspecified"

    def digest(self) -> str:
        payload = (
            f"{self.experiment_id}\n{self.model}\n{self.prompt_version}\n"
            f"{self.schema_version}\n{self.input_digest}\n"
            f"{self.turn_context_digest}\n{self.adapter}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class V7RunCache:
    """A small durable JSON cache used by the single V7 runner process."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._entries: dict[str, dict[str, Any]] | None = None
        self.hit_count = 0
        self.miss_count = 0

    def get(self, key: V7RunUnitKey, *, response_model_name: str) -> Any | None:
        entry = self._load().get(key.digest())
        if entry is None or entry.get("response_model") != response_model_name:
            self.miss_count += 1
            return None
        self.hit_count += 1
        return entry["output"]

    def put(
        self,
        key: V7RunUnitKey,
        *,
        response_model_name: str,
        output: Any,
        attempt_count: int,
    ) -> None:
        entries = self._load()
        entries[key.digest()] = {
            "experiment_id": key.experiment_id,
            "model": key.model,
            "prompt_version": key.prompt_version,
            "schema_version": key.schema_version,
            "input_digest": key.input_digest,
            "turn_context_digest": key.turn_context_digest,
            "adapter": key.adapter,
            "response_model": response_model_name,
            "output": output,
            "attempt_count": attempt_count,
            "cached_at": datetime.now().astimezone().isoformat(),
        }
        self._write(entries)

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._entries is not None:
            return self._entries
        if self.path is None or not self.path.exists():
            self._entries = {}
            return self._entries
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise TypeError("cache_root_must_be_object")
            self._entries = raw
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"invalid_v7_run_cache:{self.path}") from exc
        return self._entries

    def _write(self, entries: dict[str, dict[str, Any]]) -> None:
        self._entries = entries
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.path)


def digest_value(value: Any) -> str:
    """Digest a deterministic JSON-serializable run payload."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"unsupported_cache_value:{type(value).__name__}")

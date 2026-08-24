"""Versioned canonical vocabulary loading for shadow candidate recall."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .contracts import CanonicalTerm


class CanonicalVocabulary(BaseModel):
    """A versioned canonical catalog used only for candidate recall."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=120)
    terms: list[CanonicalTerm] = Field(min_length=1)

    @classmethod
    def load(cls, path: Path) -> CanonicalVocabulary:
        """Load and validate a vocabulary document.

        :raises ValueError: When the document is absent or violates the contract.
        """

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls.model_validate(raw)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"invalid canonical vocabulary: {path}") from exc

    def term_map(self) -> dict[str, CanonicalTerm]:
        """Return terms indexed by canonical ID."""

        return {term.canonical_id: term for term in self.terms}

    def to_prompt_payload(self) -> list[dict[str, Any]]:
        """Return a compact prompt payload without embedding vectors."""

        return [
            {
                "canonical_id": term.canonical_id,
                "canonical_type": term.canonical_type,
                "allowed_subject_types": term.allowed_subject_types,
                "surface_forms": term.aliases[:12],
            }
            for term in self.terms
        ]

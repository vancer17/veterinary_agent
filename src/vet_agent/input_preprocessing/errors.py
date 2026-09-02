"""Explicit Fail-Fast errors for the input-preprocessing shadow path."""

from __future__ import annotations


class InputPreprocessingError(RuntimeError):
    """Base error for the preprocessing shadow pipeline."""


class InputPreprocessingDependencyError(InputPreprocessingError):
    """Raised when LiteLLM or embedding dependencies are unavailable."""


class InputPreprocessingContractError(InputPreprocessingError):
    """Raised when structured model output fails the stable contract."""


class InputPreprocessingQualityGateError(InputPreprocessingError):
    """Raised when a blocking quality gate rejects shadow output."""


class InputPreprocessingNotImplementedError(InputPreprocessingError):
    """Raised when a declared domain adapter is intentionally unimplemented."""

"""Generation-option controls for V14 one-pass convergence experiments."""

from __future__ import annotations

from .v14_contracts import V14GenerationOptions


def generation_options(option_id: str) -> V14GenerationOptions:
    """Return one of the narrowly scoped V14 generation variants."""

    normalized = option_id.lower()
    options = {
        "p0": V14GenerationOptions(option_id="p0", temperature=0.0),
        "p1": V14GenerationOptions(
            option_id="p1",
            temperature=0.0,
            seed=14,
        ),
        "p2": V14GenerationOptions(
            option_id="p2",
            temperature=0.0,
            top_p=1.0,
        ),
        "p3": V14GenerationOptions(
            option_id="p3",
            temperature=0.2,
            top_p=1.0,
        ),
    }
    if normalized not in options:
        raise ValueError(f"unsupported_v14_generation_option:{option_id}")
    return options[normalized]

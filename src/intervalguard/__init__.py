"""Staleness-awareness for tool calls in LLM agent loops."""

from intervalguard.core import (
    Event,
    StaleReadError,
    check_dependencies,
    clear_registry,
    is_stale,
    registry,
    relation,
    tracked,
)

__all__ = [
    "Event",
    "StaleReadError",
    "check_dependencies",
    "clear_registry",
    "is_stale",
    "registry",
    "relation",
    "tracked",
]

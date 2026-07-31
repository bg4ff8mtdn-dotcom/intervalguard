"""Staleness-awareness for tool calls in LLM agent loops."""

from intervalguard.core import (
    Event,
    StaleReadError,
    UnknownDependencyError,
    check_dependencies,
    clear_registry,
    is_stale,
    last_event,
    registry,
    relation,
    tracked,
)

__all__ = [
    "Event",
    "StaleReadError",
    "UnknownDependencyError",
    "check_dependencies",
    "clear_registry",
    "is_stale",
    "last_event",
    "registry",
    "relation",
    "tracked",
]

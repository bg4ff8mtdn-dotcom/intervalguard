from __future__ import annotations

import contextvars
import functools
import uuid
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Event:
    name: str
    start_time: datetime
    end_time: datetime
    validity_window_seconds: int
    depends_on: list[str] = field(default_factory=list)
    kind: str = "read"  # "read" or "write"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


class StaleReadError(RuntimeError):
    pass


class UnknownDependencyError(LookupError):
    """A declared dependency id could not be found in the registry."""


registry: list[Event] = []

# Each call gets its own view of "the event I just produced", so concurrent
# callers of the same tracked function cannot clobber each other.
_last_event: contextvars.ContextVar[Event | None] = contextvars.ContextVar(
    "intervalguard_last_event", default=None
)


def last_event() -> Event | None:
    """The Event produced by the most recent tracked call in this context."""
    return _last_event.get()


def clear_registry() -> None:
    registry.clear()


def relation(a: Event, b: Event) -> str:
    if a.end_time < b.start_time:
        return "before"
    if a.end_time == b.start_time:
        return "meets"
    if a.start_time >= b.end_time:
        return "after"
    if a.start_time >= b.start_time and a.end_time <= b.end_time:
        return "during"
    return "overlaps"


def is_stale(event: Event, now: datetime) -> bool:
    return now - event.end_time > timedelta(seconds=event.validity_window_seconds)


def _supersedes(later: Event, dependency: Event) -> bool:
    """True when `later` wrote the resource after `dependency` observed it.

    Only writes can invalidate a prior read. Another read of the same resource
    changes nothing about it, so concurrent pollers must not invalidate each
    other.
    """
    if later.kind != "write":
        return False
    if later.id == dependency.id or later.name != dependency.name:
        return False
    if later.end_time <= dependency.end_time:
        return False
    return relation(dependency, later) in ("before", "meets", "overlaps", "during")


def _dependency_issues(
    event: Event, all_events: list[Event], now: datetime | None = None
) -> list[tuple[Event, str]]:
    now = now or _now()
    by_id = {e.id: e for e in all_events}
    issues: list[tuple[Event, str]] = []

    for dep_id in event.depends_on:
        dep = by_id.get(dep_id)
        if dep is None:
            raise UnknownDependencyError(
                f"event '{event.name}' (id={event.id}) declares dependency "
                f"id={dep_id}, which is not in the registry: it cannot be "
                f"verified, so this call must not be treated as checked"
            )

        superseding = next(
            (e for e in all_events if _supersedes(e, dep)),
            None,
        )
        if superseding is not None:
            issues.append(
                (
                    dep,
                    f"superseded by '{superseding.name}' (id={superseding.id}) "
                    f"at {_iso(superseding.end_time)}",
                )
            )
        elif is_stale(dep, now):
            age = int((now - dep.end_time).total_seconds())
            issues.append(
                (
                    dep,
                    f"read at {_iso(dep.end_time)} has aged {age}s, past its "
                    f"{dep.validity_window_seconds}s validity window",
                )
            )

    return issues


def check_dependencies(
    event: Event, all_events: list[Event], now: datetime | None = None
) -> list[str]:
    return [dep.name for dep, _ in _dependency_issues(event, all_events, now)]


class _Unspecified:
    """Sentinel: distinguishes 'no writes= given' from an explicit writes=False."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<unspecified>"


UNSPECIFIED = _Unspecified()


def tracked(
    name: str, validity_window_seconds: int, writes: bool | _Unspecified = UNSPECIFIED
) -> Callable:
    """Wrap a tool call so its reads are registered and its dependencies verified.

    The wrapper accepts a `depends_on` keyword listing prior event IDs this call
    relies on; it is consumed by intervalguard and not passed to the function.

    Pass `writes=True` for calls that modify the resource. Only writes can
    supersede an earlier read of the same `name`. Omitting `writes` entirely is
    treated as a read, but warns on every call: a forgotten `writes=True` on a
    real write is invisible otherwise.

    The Event produced by a call is available to that caller via `last_event()`,
    which is context-local rather than stored on the wrapper.
    """

    unspecified = isinstance(writes, _Unspecified)
    is_write = False if unspecified else bool(writes)

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, depends_on: list[str] | None = None, **kwargs):
            if unspecified:
                warnings.warn(
                    f"tracked call {func.__name__!r} (name={name!r}) did not "
                    f"specify writes=; it is being treated as a read. Pass "
                    f"writes=True if it modifies the resource, or writes=False "
                    f"to state that it does not.",
                    stacklevel=2,
                )
            start = _now()
            result = func(*args, **kwargs)
            event = Event(
                name=name,
                start_time=start,
                end_time=_now(),
                validity_window_seconds=validity_window_seconds,
                depends_on=list(depends_on or []),
                kind="write" if is_write else "read",
            )
            registry.append(event)
            _last_event.set(event)

            issues = _dependency_issues(event, registry, now=event.end_time)
            if issues:
                dep, reason = issues[0]
                raise StaleReadError(
                    f"event '{dep.name}' (id={dep.id}) is stale: {reason}"
                )
            return result

        return wrapper

    return decorator

# intervalguard

An agent fetches a fact, does three more things, and then acts on that fact — but the fact changed while the agent was busy. Nothing errors, nothing looks wrong, and the agent confidently commits to a stale belief. `intervalguard` makes that failure loud: it tracks *when* each tool call read the world, and raises `StaleReadError` the moment a decision depends on a read that time or a later write has invalidated.

A TTL alone will not catch this. A read can be well inside its validity window and still be wrong, because something else wrote to the same resource in the meantime. `intervalguard` checks both.

## The failure mode

Delayed verification destabilizing multi-agent belief: a "corrector" agent fetches ground truth, but its fetch is slow and its correction lands later. By then the ground truth has moved, so the correction actively pushes the group toward the wrong consensus — and it does so with the authority of having "checked."

The runnable illustration is [`demo/debate.py`](demo/debate.py): two debaters, one corrector, and an evidence store that flips mid-flight. The same scenario runs twice — once bare, once instrumented.

## Install

```bash
pip install -e ".[dev]"
```

Python 3.12+. Standard library only; `pytest` is the sole dev dependency.

## Usage

```python
from intervalguard import tracked, StaleReadError

@tracked(name="evidence:reactor_2_status", validity_window_seconds=30)
def fetch_status():
    return evidence_store["reactor_2_status"]

@tracked(name="evidence:reactor_2_status", validity_window_seconds=30)
def write_status(value):
    evidence_store["reactor_2_status"] = value

@tracked(name="debate:consensus", validity_window_seconds=30)
def apply_correction(observed):
    return f"consensus based on {observed}"

status = fetch_status()
read_event = fetch_status.last_event

write_status("OFFLINE")          # someone else changes the world

try:
    apply_correction(status, depends_on=[read_event.id])
except StaleReadError as exc:
    print(exc)
    # event 'evidence:reactor_2_status' (id=4dcecf80) is stale:
    # superseded by 'evidence:reactor_2_status' (id=190c1b88) at 2026-07-30T18:44:41Z
```

`depends_on` is consumed by the decorator and never passed to your function. Each wrapped function exposes `.last_event` so the next call can declare what it read from.

## What it checks

A dependency is reported stale when either:

1. **Expiry** — `now - read.end_time` exceeds the read's `validity_window_seconds`.
2. **Supersession** — a later event with the same `name` ended after the read did, meaning the underlying resource was written while the result was in hand. This fires even when the read is still inside its window.

## API

- `Event` — `id`, `name`, `start_time`, `end_time`, `validity_window_seconds`, `depends_on`.
- `relation(a, b) -> str` — one of `before`, `meets`, `overlaps`, `during`, `after`, from the two intervals alone.
- `is_stale(event, now) -> bool`
- `check_dependencies(event, all_events, now=None) -> list[str]` — names of dependencies that expired or were superseded.
- `tracked(name, validity_window_seconds)` — decorator; registers an `Event` per call and raises `StaleReadError` before returning if any declared dependency is stale.
- `registry` / `clear_registry()` — the in-memory event log.

## Run the demo

```bash
python demo/debate.py
```

Run 1 reaches a wrong consensus. Run 2 raises `StaleReadError` before the wrong correction is applied, naming the read that went stale and when.

## Tests

```bash
pytest
```

## Scope

v1, built for a demo and an open-source release. In-memory registry, single process, five interval relations rather than the full Allen set. No hosted service, dashboard, or framework integrations.

## License

MIT

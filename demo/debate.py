"""Two debaters, one corrector, and a fact that changes mid-flight.

Run:  python demo/debate.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from intervalguard import (  # noqa: E402
    StaleReadError,
    clear_registry,
    last_event,
    tracked,
)

RESOURCE = "evidence:reactor_2_status"
FETCH_LATENCY = 0.8

EVIDENCE_STORE: dict[str, str] = {}

_t0 = 0.0


def clock() -> str:
    return f"[t+{time.monotonic() - _t0:4.1f}s]"


def say(speaker: str, text: str) -> None:
    print(f"{clock()} {speaker:<22} {text}")


def banner(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def reset_world() -> None:
    global _t0
    EVIDENCE_STORE.clear()
    EVIDENCE_STORE["reactor_2_status"] = "ONLINE"
    clear_registry()
    _t0 = time.monotonic()


# --- the three agents -------------------------------------------------------


def debater_alpha() -> str:
    say("Debater Alpha", "Claim: 'Reactor 2 is online, so we should route load to it.'")
    return "route load to Reactor 2"


def debater_beta() -> str:
    say("Debater Beta", "Counter: 'I'm not confident Reactor 2 is up. Verify it.'")
    return "verify before routing"


def slow_fetch() -> str:
    """The corrector's evidence lookup. Deliberately slow, like a real tool call."""
    time.sleep(FETCH_LATENCY)
    return EVIDENCE_STORE["reactor_2_status"]


def operator_updates_reactor(new_status: str) -> str:
    """An external write to the same resource the corrector is reading."""
    time.sleep(0.2)
    EVIDENCE_STORE["reactor_2_status"] = new_status
    say("Ops system", f"WRITE {RESOURCE} -> {new_status}")
    return new_status


def decide_correction(observed_status: str) -> str:
    if observed_status == "ONLINE":
        return "Reactor 2 is ONLINE -- overruling Beta. Consensus: route load to Reactor 2."
    return "Reactor 2 is OFFLINE -- Beta was right. Consensus: hold load."


# --- run 1: no staleness awareness -----------------------------------------


def run_uninstrumented() -> None:
    banner("RUN 1 -- WITHOUT intervalguard")
    reset_world()

    debater_alpha()
    debater_beta()

    say("Corrector", f"fetching {RESOURCE} ...")
    cached_status = slow_fetch()
    say("Corrector", f"got '{cached_status}', caching it for the correction step")

    operator_updates_reactor("OFFLINE")
    time.sleep(0.3)

    say("Corrector", "applying correction from cached evidence")
    correction = decide_correction(cached_status)
    say("Corrector", correction)

    truth = EVIDENCE_STORE["reactor_2_status"]
    print()
    print(f"  Ground truth at decision time : {truth}")
    print(f"  Evidence the corrector used   : {cached_status}")
    print("  RESULT: WRONG CONSENSUS. The debate was 'corrected' with a fact that")
    print("          stopped being true while the fetch result sat in cache.")


# --- run 2: same scenario, instrumented -------------------------------------


def run_instrumented() -> None:
    banner("RUN 2 -- WITH intervalguard")
    reset_world()

    fetch_status = tracked(name=RESOURCE, validity_window_seconds=30, writes=False)(
        slow_fetch
    )
    write_status = tracked(name=RESOURCE, validity_window_seconds=30, writes=True)(
        operator_updates_reactor
    )
    apply_correction = tracked(
        name="debate:consensus", validity_window_seconds=30, writes=False
    )(decide_correction)

    debater_alpha()
    debater_beta()

    say("Corrector", f"fetching {RESOURCE} ...")
    cached_status = fetch_status()
    read_event = last_event()
    say(
        "Corrector",
        f"got '{cached_status}' (tracked as event id={read_event.id}, 30s window)",
    )

    write_status("OFFLINE")
    time.sleep(0.3)

    say("Corrector", "applying correction from cached evidence")
    try:
        correction = apply_correction(cached_status, depends_on=[read_event.id])
        say("Corrector", correction)
        print("\n  RESULT: correction applied (no staleness detected).")
    except StaleReadError as exc:
        print()
        print(f"  StaleReadError: {exc}")
        print()
        print("  RESULT: the wrong correction was NEVER applied. The read was still")
        print("          inside its 30s validity window, so a plain TTL would have")
        print("          let it through -- intervalguard caught it because the")
        print("          underlying resource was written after the read happened.")


if __name__ == "__main__":
    run_uninstrumented()
    run_instrumented()
    print()

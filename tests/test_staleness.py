import threading
import time
import warnings
from datetime import datetime, timedelta, timezone

import pytest

from intervalguard import (
    Event,
    StaleReadError,
    UnknownDependencyError,
    check_dependencies,
    clear_registry,
    is_stale,
    last_event,
    registry,
    tracked,
)

BASE = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)


def event(
    start_s, end_s, name="price", window=30, depends_on=None, id=None, kind="read"
) -> Event:
    kwargs = {} if id is None else {"id": id}
    return Event(
        name=name,
        start_time=BASE + timedelta(seconds=start_s),
        end_time=BASE + timedelta(seconds=end_s),
        validity_window_seconds=window,
        depends_on=depends_on or [],
        kind=kind,
        **kwargs,
    )


def test_is_stale_just_inside_window():
    e = event(0, 10, window=30)
    assert is_stale(e, BASE + timedelta(seconds=39, microseconds=999999)) is False


def test_is_stale_exactly_at_window_boundary():
    e = event(0, 10, window=30)
    assert is_stale(e, BASE + timedelta(seconds=40)) is False


def test_is_stale_just_past_window():
    e = event(0, 10, window=30)
    assert is_stale(e, BASE + timedelta(seconds=40, microseconds=1)) is True


def test_check_dependencies_plain_expiry():
    read = event(0, 10, window=30, id="read1")
    consumer = event(100, 110, name="decide", depends_on=["read1"])
    stale = check_dependencies(consumer, [read, consumer], now=BASE + timedelta(seconds=110))
    assert stale == ["price"]


def test_check_dependencies_fresh_dependency_is_clean():
    read = event(0, 10, window=30, id="read1")
    consumer = event(20, 25, name="decide", depends_on=["read1"])
    assert check_dependencies(consumer, [read, consumer], now=BASE + timedelta(seconds=25)) == []


def test_check_dependencies_superseded_while_still_inside_window():
    read = event(0, 10, window=300, id="read1")
    write = event(12, 14, id="write1", kind="write")
    consumer = event(20, 22, name="decide", depends_on=["read1"])
    stale = check_dependencies(
        consumer, [read, write, consumer], now=BASE + timedelta(seconds=22)
    )
    assert stale == ["price"]


def test_check_dependencies_ignores_writes_to_other_resources():
    read = event(0, 10, window=300, id="read1")
    other = event(12, 14, name="volume", id="write1", kind="write")
    consumer = event(20, 22, name="decide", depends_on=["read1"])
    assert check_dependencies(consumer, [read, other, consumer], now=BASE + timedelta(seconds=22)) == []


def test_check_dependencies_ignores_earlier_writes():
    write = event(-20, -10, id="write1", kind="write")
    read = event(0, 10, window=300, id="read1")
    consumer = event(20, 22, name="decide", depends_on=["read1"])
    assert check_dependencies(consumer, [write, read, consumer], now=BASE + timedelta(seconds=22)) == []


def test_tracked_raises_on_superseded_dependency():
    clear_registry()

    read = tracked("price", 300, writes=False)(lambda: 100)
    write = tracked("price", 300, writes=True)(lambda: time.sleep(0.05) or 101)
    decide = tracked("decide", 300, writes=False)(lambda v: v * 2)

    read()
    read_event = last_event()
    write()

    with pytest.raises(StaleReadError) as exc:
        decide(100, depends_on=[read_event.id])

    message = str(exc.value)
    assert read_event.id in message
    assert "superseded" in message


def test_tracked_returns_result_when_dependencies_are_fresh():
    clear_registry()

    read = tracked("price", 300, writes=False)(lambda: 100)
    decide = tracked("decide", 300, writes=False)(lambda v: v * 2)

    read()
    assert decide(100, depends_on=[last_event().id]) == 200


# --- regression: a later read must never invalidate an earlier read ---------


def test_later_read_of_same_resource_does_not_supersede_earlier_read():
    first_read = event(0, 10, window=300, id="read1")
    second_read = event(12, 14, id="read2")  # another poller, no write
    consumer = event(20, 22, name="decide", depends_on=["read1"])
    assert (
        check_dependencies(
            consumer, [first_read, second_read, consumer], now=BASE + timedelta(seconds=22)
        )
        == []
    )


def test_two_concurrent_pollers_do_not_invalidate_each_others_reads():
    clear_registry()

    poll = tracked("price", 300, writes=False)(lambda: time.sleep(0.05) or 100)
    decide = tracked("decide", 300, writes=False)(lambda v: v * 2)

    poll()
    agent_a_read = last_event()
    poll()  # a second agent polls the same resource; nothing was written

    assert decide(100, depends_on=[agent_a_read.id]) == 200


# --- regression: an unresolvable dependency id must fail loudly -------------


def test_unknown_dependency_id_raises_instead_of_being_silently_skipped():
    read = event(0, 10, id="read1")
    consumer = event(20, 22, name="decide", depends_on=["mistyped-id"])

    with pytest.raises(UnknownDependencyError) as exc:
        check_dependencies(consumer, [read, consumer], now=BASE + timedelta(seconds=22))

    assert "mistyped-id" in str(exc.value)


def test_tracked_raises_unknown_dependency_when_dependency_was_evicted():
    clear_registry()

    read = tracked("price", 300, writes=False)(lambda: 100)
    decide = tracked("decide", 300, writes=False)(lambda v: v * 2)

    read()
    read_event = last_event()
    clear_registry()  # the dependency is no longer resolvable

    with pytest.raises(UnknownDependencyError) as exc:
        decide(100, depends_on=[read_event.id])

    assert read_event.id in str(exc.value)


# --- regression: concurrent callers must not share one last-event slot ------


def test_concurrent_callers_do_not_overwrite_each_others_last_event():
    clear_registry()

    poll = tracked("price", 300, writes=False)(lambda tag: time.sleep(0.1) or tag)
    seen: dict[str, object] = {}

    def worker(tag: str, delay: float) -> None:
        time.sleep(delay)
        poll(tag)
        time.sleep(0.2)  # let the other thread's call land before reading back
        seen[tag] = last_event()

    threads = [
        threading.Thread(target=worker, args=("a", 0.0)),
        threading.Thread(target=worker, args=("b", 0.05)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert seen["a"].id != seen["b"].id
    assert seen["a"].end_time < seen["b"].end_time
    assert {seen["a"].id, seen["b"].id} <= {e.id for e in registry}


# --- regression: an unannotated call must not look like a declared read -----


def test_omitting_writes_warns_that_read_vs_write_is_unstated():
    clear_registry()

    ambiguous = tracked("price", 300)(lambda: 100)

    with pytest.warns(UserWarning, match="did not specify writes="):
        ambiguous()

    assert registry[-1].kind == "read"  # behavior unchanged: still a read


def test_explicit_writes_false_does_not_warn():
    clear_registry()

    read = tracked("price", 300, writes=False)(lambda: 100)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        read()

    assert caught == []
    assert registry[-1].kind == "read"


def test_explicit_writes_true_does_not_warn_and_marks_a_write():
    clear_registry()

    write = tracked("price", 300, writes=True)(lambda: 101)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        write()

    assert caught == []
    assert registry[-1].kind == "write"

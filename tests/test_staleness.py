import time
from datetime import datetime, timedelta, timezone

import pytest

from intervalguard import (
    Event,
    StaleReadError,
    check_dependencies,
    clear_registry,
    is_stale,
    tracked,
)

BASE = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)


def event(start_s, end_s, name="price", window=30, depends_on=None, id=None) -> Event:
    kwargs = {} if id is None else {"id": id}
    return Event(
        name=name,
        start_time=BASE + timedelta(seconds=start_s),
        end_time=BASE + timedelta(seconds=end_s),
        validity_window_seconds=window,
        depends_on=depends_on or [],
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
    write = event(12, 14, id="write1")
    consumer = event(20, 22, name="decide", depends_on=["read1"])
    stale = check_dependencies(
        consumer, [read, write, consumer], now=BASE + timedelta(seconds=22)
    )
    assert stale == ["price"]


def test_check_dependencies_ignores_writes_to_other_resources():
    read = event(0, 10, window=300, id="read1")
    other = event(12, 14, name="volume", id="write1")
    consumer = event(20, 22, name="decide", depends_on=["read1"])
    assert check_dependencies(consumer, [read, other, consumer], now=BASE + timedelta(seconds=22)) == []


def test_check_dependencies_ignores_earlier_writes():
    write = event(-20, -10, id="write1")
    read = event(0, 10, window=300, id="read1")
    consumer = event(20, 22, name="decide", depends_on=["read1"])
    assert check_dependencies(consumer, [write, read, consumer], now=BASE + timedelta(seconds=22)) == []


def test_tracked_raises_on_superseded_dependency():
    clear_registry()

    read = tracked("price", 300)(lambda: 100)
    write = tracked("price", 300)(lambda: time.sleep(0.05) or 101)
    decide = tracked("decide", 300)(lambda v: v * 2)

    read()
    read_event = read.last_event
    write()

    with pytest.raises(StaleReadError) as exc:
        decide(100, depends_on=[read_event.id])

    message = str(exc.value)
    assert read_event.id in message
    assert "superseded" in message


def test_tracked_returns_result_when_dependencies_are_fresh():
    clear_registry()

    read = tracked("price", 300)(lambda: 100)
    decide = tracked("decide", 300)(lambda v: v * 2)

    read()
    assert decide(100, depends_on=[read.last_event.id]) == 200

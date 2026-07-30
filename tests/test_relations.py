from datetime import datetime, timedelta, timezone

from intervalguard import Event, relation

BASE = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)


def event(start_s: int, end_s: int, name: str = "r") -> Event:
    return Event(
        name=name,
        start_time=BASE + timedelta(seconds=start_s),
        end_time=BASE + timedelta(seconds=end_s),
        validity_window_seconds=60,
    )


def test_before():
    assert relation(event(0, 10), event(20, 30)) == "before"


def test_meets():
    assert relation(event(0, 10), event(10, 20)) == "meets"


def test_overlaps():
    assert relation(event(0, 20), event(10, 30)) == "overlaps"


def test_during():
    assert relation(event(10, 20), event(0, 30)) == "during"


def test_after():
    assert relation(event(20, 30), event(0, 10)) == "after"


def test_identical_intervals_are_during():
    assert relation(event(0, 10), event(0, 10)) == "during"

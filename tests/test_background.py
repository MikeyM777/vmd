"""Slow questions, asked off the thread that draws the window.

Every wait in this file is bounded independently of the thing under test, so a
reader that never runs fails the test rather than hanging the suite.
"""

from __future__ import annotations

import threading
import time

from vmd.background import BackgroundValue

# The ceiling on any wedge these tests set up. A regression that puts the read
# back on the caller's thread costs this much and then fails, rather than
# stopping the suite.
WEDGE_CEILING = 5.0
PATIENCE = 10.0


def until(predicate, timeout: float = PATIENCE) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


class Wedge:
    """A read that does not return until the test says so."""

    def __init__(self, answer=True) -> None:
        self.answer = answer
        self.entered = threading.Event()
        self.released = threading.Event()
        self.reads = 0

    def __call__(self):
        self.reads += 1
        self.entered.set()
        self.released.wait(WEDGE_CEILING)
        return self.answer


def test_the_caller_never_waits_for_the_read() -> None:
    wedge = Wedge()
    value = BackgroundValue(wedge, stale_after=0.0, name="a wedged reading")
    try:
        started = time.monotonic()
        reading = value.get()
        elapsed = time.monotonic() - started
    finally:
        wedge.released.set()
        value.close()
    assert elapsed < 0.5, f"the caller waited {elapsed:.2f} s"
    assert reading.value is None
    assert reading.known is False, "nothing has been read, and it must say so"


def test_a_reading_that_has_never_succeeded_is_not_a_reading() -> None:
    wedge = Wedge()
    value = BackgroundValue(wedge, stale_after=0.0, name="a wedged reading")
    try:
        assert value.get().known is False
        assert value.get().age is None
    finally:
        wedge.released.set()
        value.close()


def test_the_answer_arrives_and_carries_its_age() -> None:
    value = BackgroundValue(lambda: 42, stale_after=60.0, name="an answer")
    try:
        value.get()
        assert until(lambda: value.get().value == 42)
        reading = value.get()
        assert reading.known is True
        assert reading.age is not None and reading.age >= 0.0
        assert reading.older_than(60.0) is False
    finally:
        value.close()


def test_only_one_read_is_ever_in_flight() -> None:
    """A slow answer must not become a pile of threads, one per heartbeat."""
    wedge = Wedge()
    value = BackgroundValue(wedge, stale_after=0.0, name="a wedged reading")
    try:
        for _ in range(20):
            value.get()
        assert wedge.entered.wait(PATIENCE)
        assert wedge.reads == 1, f"{wedge.reads} readers were started at once"
    finally:
        wedge.released.set()
        value.close()


def test_a_read_slower_than_the_staleness_window_is_still_believed() -> None:
    """Stamped when it finished, not when it started.

    A radio that is not answering costs both login timeouts before it says so -
    longer than the window. Stamped at the start, the reading would be expired
    the moment it was written and the next caller would start another one, which
    is how a slow answer becomes a permanent one.
    """
    slow = Wedge(answer="the radio is not answering")
    value = BackgroundValue(slow, stale_after=0.05, name="a slow reading")
    try:
        value.get()
        assert slow.entered.wait(PATIENCE)
        time.sleep(0.2)  # longer than the window, while the read is still out
        slow.released.set()
        assert until(lambda: value.get().value == "the radio is not answering")
        assert value.get().older_than(0.05) is False, "the answer was stale on arrival"
    finally:
        slow.released.set()
        value.close()


def test_a_read_that_raises_is_not_an_answer_and_does_not_end_the_reader() -> None:
    calls = {"n": 0}

    def sometimes():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("the process list could not be read")
        return "second time lucky"

    value = BackgroundValue(sometimes, stale_after=0.0, name="a flaky reading")
    try:
        value.get()
        assert until(lambda: value.get().value == "second time lucky")
    finally:
        value.close()


def test_closing_does_not_wait_for_a_read_that_never_returns() -> None:
    wedge = Wedge()
    value = BackgroundValue(wedge, stale_after=0.0, name="a wedged reading")
    try:
        value.get()
        assert wedge.entered.wait(PATIENCE)
        started = time.monotonic()
        closed = value.close(timeout=0.5)
        elapsed = time.monotonic() - started
    finally:
        wedge.released.set()
    assert closed is False, "it cannot have finished; it must say so rather than pretend"
    assert elapsed < 2.0, f"closing waited {elapsed:.2f} s on a wedged read"


def test_a_closed_reading_starts_nothing_new() -> None:
    wedge = Wedge()
    value = BackgroundValue(wedge, stale_after=0.0, name="a wedged reading")
    value.close()
    value.get()
    assert not wedge.entered.wait(0.2), "a closed reading started another read"


def test_a_seed_counts_as_something_already_seen() -> None:
    """The console checks a PID once, synchronously, as it adopts a child.
    That is a reading, not nothing."""
    wedge = Wedge()
    value = BackgroundValue(wedge, stale_after=60.0, name="a seeded reading", seed=True)
    try:
        reading = value.get()
        assert reading.value is True
        assert reading.known is True
        assert not wedge.entered.wait(0.2), "a fresh seed must not need a read"
    finally:
        wedge.released.set()
        value.close()


def test_invalidating_forgets_what_was_read() -> None:
    value = BackgroundValue(lambda: 7, stale_after=60.0, name="an answer")
    try:
        value.get()
        assert until(lambda: value.get().value == 7)
        value.invalidate()
        assert value.get().known is False
    finally:
        value.close()

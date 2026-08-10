"""The video pane contract, exercised through the fake.

The real pane needs a display and a stream; everything that consumes video is
tested against the fake instead, which is why the fake is production code rather
than a test fixture.
"""

from __future__ import annotations

from vmd.desktop.video import FakeVideoPane


def test_a_new_pane_is_stopped() -> None:
    assert FakeVideoPane().state == "stopped"


def test_showing_a_url_connects_then_plays() -> None:
    pane = FakeVideoPane()
    pane.show("rtsp://127.0.0.1:8554/thermal")
    assert pane.state == "connecting"
    assert pane.url == "rtsp://127.0.0.1:8554/thermal"

    pane.pretend_playing()
    assert pane.state == "playing"


def test_stopping_forgets_the_stream() -> None:
    pane = FakeVideoPane()
    pane.show("rtsp://x/y")
    pane.pretend_playing()
    pane.stop()
    assert pane.state == "stopped"
    assert pane.url is None


def test_a_pane_that_goes_quiet_is_late_and_nothing_else_happens() -> None:
    """The rule of this rewrite: the pane reports; it does not intervene."""
    pane = FakeVideoPane()
    pane.show("rtsp://x/y")
    pane.pretend_playing()
    pane.pretend_late()
    assert pane.state == "late"
    assert pane.url == "rtsp://x/y", "a late stream must not be torn down"
    assert pane.restarts == 0


def test_only_a_failure_counts_as_a_failure() -> None:
    pane = FakeVideoPane()
    pane.show("rtsp://x/y")
    pane.pretend_failed()
    assert pane.state == "failed"


def test_showing_a_new_url_replaces_the_old_one() -> None:
    pane = FakeVideoPane()
    pane.show("rtsp://x/one")
    pane.pretend_playing()
    pane.show("rtsp://x/two")
    assert pane.url == "rtsp://x/two"
    assert pane.state == "connecting"
    assert pane.restarts == 1

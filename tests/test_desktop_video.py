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


# ------------------------------------------------------------- the transport
#
# Playback had no pause. Re-watching the same ten seconds is the single most
# common thing anyone does with security footage, and it cost a fresh click on a
# day bar where one pixel is over a minute.
#
# Everything here is a request passed to the player and nothing else. The pane
# still watches and does not intervene: there is no timer, nothing retries, and
# nothing here restarts a stream.


def test_a_new_pane_is_not_paused_and_runs_at_normal_speed() -> None:
    pane = FakeVideoPane()
    assert pane.paused is False
    assert pane.rate == 1.0


def test_pausing_and_letting_it_run_again() -> None:
    pane = FakeVideoPane()
    pane.show("file:///a.mp4")
    pane.set_paused(True)
    assert pane.paused is True
    pane.set_paused(False)
    assert pane.paused is False


def test_the_speed_can_be_changed() -> None:
    pane = FakeVideoPane()
    pane.show("file:///a.mp4")
    pane.set_rate(4.0)
    assert pane.rate == 4.0


def test_a_speed_of_zero_or_less_is_refused() -> None:
    """Zero is not slow motion, it is a player that never advances again, and
    a negative rate is not something libVLC plays."""
    pane = FakeVideoPane()
    pane.set_rate(0.0)
    assert pane.rate > 0.0
    pane.set_rate(-2.0)
    assert pane.rate > 0.0


def test_showing_something_new_starts_it_running_again() -> None:
    """Paused, then taken to a movement: the operator pressed a button that
    means "show me that", and being handed a still frame of it is the console
    obeying the letter of a request nobody made."""
    pane = FakeVideoPane()
    pane.show("file:///a.mp4")
    pane.set_paused(True)
    pane.show("file:///b.mp4")
    assert pane.paused is False


def test_the_speed_survives_being_taken_to_another_file() -> None:
    """A speed is a way of watching, not a property of one file: an operator
    working through a night at 8x does not want it reset by every seek."""
    pane = FakeVideoPane()
    pane.show("file:///a.mp4")
    pane.set_rate(8.0)
    pane.show("file:///b.mp4")
    assert pane.rate == 8.0


def test_a_stopped_pane_knows_no_position() -> None:
    assert FakeVideoPane().position_seconds() is None


def test_the_position_is_where_it_was_asked_to_start() -> None:
    pane = FakeVideoPane()
    pane.show("file:///a.mp4", at_seconds=42.0)
    assert pane.position_seconds() == 42.0


def test_seeking_moves_the_position_without_reopening_the_file() -> None:
    pane = FakeVideoPane()
    pane.show("file:///a.mp4", at_seconds=10.0)
    assert pane.seek_seconds(30.0) is True
    assert pane.position_seconds() == 30.0
    assert pane.restarts == 0


def test_nothing_can_be_sought_in_a_pane_showing_nothing() -> None:
    pane = FakeVideoPane()
    assert pane.seek_seconds(30.0) is False


def test_a_seek_never_goes_before_the_first_frame() -> None:
    pane = FakeVideoPane()
    pane.show("file:///a.mp4", at_seconds=5.0)
    pane.seek_seconds(-20.0)
    assert pane.position_seconds() == 0.0


# ------------------------------------------------------- how far behind it runs
#
# "When I open the VMD app compared to the FLIR browser GUI our VMD is much
# later than the FLIR GUI. It's unacceptable." What follows is every claim
# `vlc_options` makes, written down so that a future edit that reaches for the
# caching figure alone has to notice the other three.


def test_the_delay_reaches_both_of_libvlcs_caching_options() -> None:
    """The RTSP module reads the LIVE figure for a stream it calls live, and the
    network one for everything else. Setting only one is how a console ends up
    applying a number to the path that is not asking about it."""
    from vmd.desktop.video import vlc_options

    options = vlc_options(120)
    assert "--network-caching=120" in options
    assert "--live-caching=120" in options


def test_the_clock_allowance_is_only_taken_away_at_the_fastest_step() -> None:
    """"The VMD is totally stuck while the FLIR GUI is working perfectly."

    These two options are the only ones here that change what libVLC does with a
    frame rather than how many it keeps, and they are the two that can stop a
    picture outright: with no allowance at all, a stream whose timestamps wander
    has every frame arrive at a time libVLC thinks is wrong and discards them.

    Worth having, and most of the delay - but a picture 300 ms behind beats a
    picture that is not there, so they belong at the step whose name says it is
    the extreme one and not at the one the console starts on.
    """
    from vmd.desktop.video import DEFAULT_DELAY_MS, TIGHT_CLOCK_AT_OR_BELOW_MS, vlc_options

    ordinary = vlc_options(DEFAULT_DELAY_MS)
    assert "--clock-jitter=0" not in ordinary
    assert "--clock-synchro=0" not in ordinary

    fastest = vlc_options(TIGHT_CLOCK_AT_OR_BELOW_MS)
    assert "--clock-jitter=0" in fastest
    assert "--clock-synchro=0" in fastest


def test_the_step_the_console_starts_on_is_one_of_the_safe_ones() -> None:
    """The default has to be a setting that simply works. Everything clever is
    one step away and is asked for by name."""
    from vmd.desktop.video import DEFAULT_DELAY_MS, TIGHT_CLOCK_AT_OR_BELOW_MS

    assert DEFAULT_DELAY_MS > TIGHT_CLOCK_AT_OR_BELOW_MS


def test_a_negative_delay_is_not_passed_to_libvlc() -> None:
    """The model refuses one, so this is about a pane built directly - a spike
    tool, a test. libVLC's own range starts at 0 and it is not this file's job
    to find out what it does with less."""
    from vmd.desktop.video import vlc_options

    assert "--network-caching=0" in vlc_options(-1)


def test_the_default_delay_is_the_one_the_settings_model_has() -> None:
    """Two defaults for one idea is two answers within a month, and the operator
    would be holding both. A pane built directly behaves like the console."""
    from vmd.desktop.video import DEFAULT_DELAY_MS
    from vmd.settings import Settings

    assert Settings().live_delay_ms == DEFAULT_DELAY_MS


def test_nothing_asks_libvlc_to_turn_the_picture() -> None:
    """The switch that did is gone, and so is the filter it set. A transform
    filter left on a pane nobody can switch off is a picture upside down with
    no control on the form that explains it."""
    from vmd.desktop.video import vlc_options

    plain = vlc_options(120)
    assert not [option for option in plain if "transform" in option], plain

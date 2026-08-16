"""The loop that keeps the camera's bitrate inside what the link is carrying.

Everything here is driven by a clock the test holds and an executor that runs
the work on the calling thread, so nothing in this file waits on anything. That
is not a convenience: a control loop tested against real time is a test that can
hang, and this suite's rule is that a test may fail and may not hang.
"""

from __future__ import annotations

import logging

import pytest

from vmd.ptz.autobitrate import (
    AIRTIME_CALM_PERCENT,
    BUSY_FOR_SECONDS,
    CALM_FOR_SECONDS,
    DOWN_FACTOR,
    MIN_SECONDS_BETWEEN_DOWN,
    MIN_SECONDS_BETWEEN_UP,
    SIGNAL_FALLING_DB,
    UP_FACTOR,
    BitrateLoop,
)
from vmd.radio.panel import AIRTIME_BUSY_PERCENT
from vmd.settings import BitrateSettings, Settings

BUSY = AIRTIME_BUSY_PERCENT + 20.0
CALM = AIRTIME_CALM_PERCENT - 10.0

# The heartbeat the console actually runs at. The loop is fed once per beat.
BEAT = 2.0


class Clock:
    """Time the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


class Camera:
    """Whatever the loop asks the camera for, remembered rather than done."""

    def __init__(self, result: dict | None = None) -> None:
        self.asked: list[int] = []
        self.result = result if result is not None else {"ok": True, "changed": ["enc0"]}

    def __call__(self, kbps: int) -> dict:
        self.asked.append(kbps)
        return dict(self.result)


def settings(mode: str = "auto", floor: int = 1000, ceiling: int = 5000) -> Settings:
    return Settings(
        bitrate=BitrateSettings(mode=mode, floor_kbps=floor, ceiling_kbps=ceiling)
    )


def loop(clock: Clock, camera: Camera, **kwargs) -> BitrateLoop:
    return BitrateLoop(
        settings=settings(**kwargs),
        apply=camera,
        clock=clock,
        # On the calling thread: a test may not wait on a worker.
        executor=lambda work: work(),
    )


def link(airtime: float, signal: float = -66.0, age: float = 1.0, **extra) -> dict:
    """A radio reading of the shape `RadioService.status` hands out."""
    reading = {
        "connected": True,
        "reason": "",
        "airtime_percent": airtime,
        "signal_dbm": signal,
        "age_seconds": age,
    }
    reading.update(extra)
    return reading


def feed(control: BitrateLoop, clock: Clock, reading: dict, seconds: float) -> None:
    """Hand the loop one reading per heartbeat for `seconds` of link time."""
    beats = int(seconds / BEAT) + 1
    for _ in range(beats):
        control.poll(dict(reading))
        clock.tick(BEAT)


# --- no reading, no action ---------------------------------------------------


def test_a_radio_that_was_never_set_up_leaves_the_camera_alone() -> None:
    """Guessing at a link you cannot see is worse than leaving the camera alone.

    This is the state every console is in before the radio is configured, and
    the loop must be able to sit in it for months without touching anything.
    """
    clock, camera = Clock(), Camera()
    control = loop(clock, camera)

    feed(control, clock, {"connected": False, "reason": "the radio is not set up"}, 600)

    assert camera.asked == []
    state = control.state()
    assert state.running is False
    assert "radio" in state.reason.lower()


def test_a_radio_that_reports_no_airtime_is_not_guessed_at() -> None:
    """Some airOS builds have no `polling.use`. That is not "the link is quiet"."""
    clock, camera = Clock(), Camera()
    control = loop(clock, camera)

    feed(control, clock, link(airtime=None), 600)

    assert camera.asked == []
    assert control.state().running is False


def test_a_reading_too_old_to_believe_is_not_a_reading() -> None:
    """The radio answers from a cache and the cache carries its age. A link that
    was quiet a minute ago is not evidence about the link now."""
    clock, camera = Clock(), Camera()
    control = loop(clock, camera)

    feed(control, clock, link(BUSY, age=120.0), 600)

    assert camera.asked == []
    assert control.state().running is False
    assert "old" in control.state().reason.lower()


def test_the_loop_says_so_when_it_is_not_running(caplog) -> None:
    """The operator has no terminal. A loop that is silently doing nothing and a
    loop that is working are the same thing on screen unless it says."""
    clock, camera = Clock(), Camera()
    control = loop(clock, camera)

    with caplog.at_level(logging.INFO):
        feed(control, clock, {"connected": False, "reason": "cannot reach 192.0.2.9"}, 60)

    said = " ".join(record.getMessage() for record in caplog.records)
    assert "192.0.2.9" in said
    # Said once, not once every two seconds for the life of the console.
    assert said.count("192.0.2.9") == 1


# --- sustained readings, never spikes ----------------------------------------


def test_one_busy_reading_does_not_move_anything() -> None:
    """A pan, a keyframe or a passing vehicle moves the airtime for a second."""
    clock, camera = Clock(), Camera()
    control = loop(clock, camera)

    feed(control, clock, link(CALM), 300)
    control.poll(link(BUSY))
    clock.tick(BEAT)
    feed(control, clock, link(CALM), 60)

    assert camera.asked == []


def test_two_readings_either_side_of_a_gap_are_not_a_held_reading() -> None:
    """The console's heartbeat stalls - a save restarts three child processes,
    a `tasklist` blocks on a drive that has gone - and when it comes back the
    two readings on either side of the gap span the whole window on their own.
    That is not ten seconds of a busy link; it is one reading, twice, with
    nothing known in between, and acting on it would be acting on a spike after
    all.
    """
    clock, camera = Clock(), Camera()
    control = loop(clock, camera)

    control.poll(link(BUSY))
    clock.tick(BUSY_FOR_SECONDS)
    control.poll(link(BUSY))

    assert camera.asked == []


def test_a_link_busy_for_long_enough_turns_the_picture_down() -> None:
    clock, camera = Clock(), Camera()
    control = loop(clock, camera, ceiling=5000)

    feed(control, clock, link(BUSY), BUSY_FOR_SECONDS + BEAT)

    assert camera.asked == [int(5000 * DOWN_FACTOR)]


def test_a_link_that_calms_down_briefly_is_not_a_reason_to_spend_more() -> None:
    """Coming down costs the picture once; going up too eagerly costs it
    repeatedly, so the calm has to hold for far longer than the trouble did."""
    clock, camera = Clock(), Camera()
    control = loop(clock, camera)

    feed(control, clock, link(BUSY), BUSY_FOR_SECONDS + BEAT)
    assert len(camera.asked) == 1
    clock.tick(MIN_SECONDS_BETWEEN_UP)

    feed(control, clock, link(CALM), BUSY_FOR_SECONDS + BEAT)

    assert len(camera.asked) == 1, "the down window is not long enough to go back up"


def test_going_up_takes_far_longer_than_coming_down() -> None:
    clock, camera = Clock(), Camera()
    control = loop(clock, camera, ceiling=5000)

    feed(control, clock, link(BUSY), BUSY_FOR_SECONDS + BEAT)
    lowered = camera.asked[-1]
    clock.tick(MIN_SECONDS_BETWEEN_UP)

    feed(control, clock, link(CALM), CALM_FOR_SECONDS + BEAT)

    assert camera.asked[-1] == int(lowered * UP_FACTOR)
    assert CALM_FOR_SECONDS > BUSY_FOR_SECONDS


# --- the floor is a floor ----------------------------------------------------


def test_the_camera_is_never_asked_for_less_than_the_floor() -> None:
    clock, camera = Clock(), Camera()
    control = loop(clock, camera, floor=1000, ceiling=1200)

    for _ in range(10):
        feed(control, clock, link(BUSY), BUSY_FOR_SECONDS + BEAT)
        clock.tick(MIN_SECONDS_BETWEEN_DOWN)

    assert camera.asked, "the loop should have come down at least once"
    assert min(camera.asked) == 1000


def test_a_link_that_cannot_carry_the_floor_is_reported_as_such(caplog) -> None:
    """The state that means "this link cannot do this job". It is a real answer
    and the operator has to be given it rather than a picture quietly ruined."""
    clock, camera = Clock(), Camera()
    control = loop(clock, camera, floor=1000, ceiling=1200)

    with caplog.at_level(logging.WARNING):
        for _ in range(6):
            feed(control, clock, link(BUSY), BUSY_FOR_SECONDS + BEAT)
            clock.tick(MIN_SECONDS_BETWEEN_DOWN)

    assert control.state().below_floor is True
    said = " ".join(record.getMessage() for record in caplog.records).lower()
    assert "1000" in said
    assert "link" in said
    # Once. This condition can last for days and it must not fill the log.
    assert said.count("1000 kb/s") == 1


def test_the_camera_is_never_asked_for_more_than_the_ceiling() -> None:
    clock, camera = Clock(), Camera()
    control = loop(clock, camera, floor=1000, ceiling=5000)

    for _ in range(10):
        feed(control, clock, link(CALM), CALM_FOR_SECONDS + BEAT)
        clock.tick(MIN_SECONDS_BETWEEN_UP)

    assert all(kbps <= 5000 for kbps in camera.asked), camera.asked


# --- changes are rare --------------------------------------------------------


def test_a_second_change_is_refused_until_the_link_has_had_time_to_settle() -> None:
    """Writing an encoder configuration interrupts the stream: go2rtc has to
    reconnect and the operator sees a blip."""
    clock, camera = Clock(), Camera()
    control = loop(clock, camera)

    feed(control, clock, link(BUSY), BUSY_FOR_SECONDS + BEAT)
    assert len(camera.asked) == 1

    # Busy again straight away, and for far longer than the window.
    feed(control, clock, link(BUSY), MIN_SECONDS_BETWEEN_DOWN - BEAT * 3)

    assert len(camera.asked) == 1


def test_coming_down_is_allowed_again_sooner_than_going_up() -> None:
    assert MIN_SECONDS_BETWEEN_DOWN < MIN_SECONDS_BETWEEN_UP


def test_asking_for_what_the_camera_is_already_at_is_not_a_change() -> None:
    """At the ceiling with a quiet link there is nothing to do, and doing it
    anyway would blip the picture every few minutes for ever."""
    clock, camera = Clock(), Camera()
    control = loop(clock, camera, ceiling=5000)

    for _ in range(5):
        feed(control, clock, link(CALM), CALM_FOR_SECONDS + BEAT)
        clock.tick(MIN_SECONDS_BETWEEN_UP)

    assert camera.asked == []


# --- the camera may refuse ---------------------------------------------------


def test_a_change_the_camera_did_not_keep_is_counted_rather_than_believed() -> None:
    """This camera answers 200 with a SOAP fault, and that mistake has been made
    twice in this project. What landed is read back, not assumed."""
    clock, camera = Clock(), Camera({"ok": True, "changed": ["enc0"], "refused": ["enc0"]})
    control = loop(clock, camera)

    feed(control, clock, link(BUSY), BUSY_FOR_SECONDS + BEAT)

    assert control.state().refused == 1
    assert control.state().changes == 0


def test_a_bitrate_the_camera_did_not_keep_does_not_move_where_the_loop_thinks_it_is() -> None:
    """The other half of the same sentence, and the half that was not true.

    The loop said "what it reports now is what it is doing; the request was not
    believed" - and had already recorded the request as the camera's new
    setting two lines above. So the next busy window took 70% of a bitrate the
    camera never had, and the one after that 70% of that, walking the loop's
    idea of the picture down to the floor while the camera sat at 5000 kb/s the
    whole time. Then a calm link climbed back from a number that was fiction.

    The rule is the one the failed-write case already follows: the loop tracks
    what it has COMMANDED and the camera has KEPT, never what it has asked for.
    """
    clock, camera = Clock(), Camera({"ok": True, "changed": ["enc0"], "refused": ["enc0"]})
    control = loop(clock, camera, ceiling=5000)

    feed(control, clock, link(BUSY), BUSY_FOR_SECONDS + BEAT)
    clock.tick(MIN_SECONDS_BETWEEN_DOWN)
    feed(control, clock, link(BUSY), BUSY_FOR_SECONDS + BEAT)

    assert camera.asked == [int(5000 * DOWN_FACTOR)] * 2, (
        "a change the camera did not keep must not move the loop's idea of it"
    )
    assert control.state().target_kbps == 5000


def test_a_write_that_failed_is_tried_again_rather_than_assumed_to_have_worked() -> None:
    clock, camera = Clock(), Camera({"ok": False, "error": "the camera refused the command"})
    control = loop(clock, camera, ceiling=5000)

    feed(control, clock, link(BUSY), BUSY_FOR_SECONDS + BEAT)
    clock.tick(MIN_SECONDS_BETWEEN_DOWN)
    feed(control, clock, link(BUSY), BUSY_FOR_SECONDS + BEAT)

    assert camera.asked == [int(5000 * DOWN_FACTOR)] * 2, (
        "a write that did not happen must not move the loop's idea of the camera"
    )


# --- the signal is a second opinion, never the first -------------------------


def test_a_signal_falling_away_stops_the_loop_spending_more() -> None:
    """He is testing at -66 dBm with the antennas close together. At 15 km the
    radio's own expectation is -80, the modulation is lower, and the same
    bitrate costs far more airtime - so a link whose signal is on its way down
    is not a link to spend the calm on.
    """
    clock, camera = Clock(), Camera()
    control = loop(clock, camera)

    feed(control, clock, link(BUSY), BUSY_FOR_SECONDS + BEAT)
    assert len(camera.asked) == 1
    clock.tick(MIN_SECONDS_BETWEEN_UP)

    signal = -70.0
    for _ in range(int(CALM_FOR_SECONDS / BEAT) + 2):
        control.poll(link(CALM, signal=signal))
        signal -= (SIGNAL_FALLING_DB + 4.0) / (CALM_FOR_SECONDS / BEAT)
        clock.tick(BEAT)

    assert len(camera.asked) == 1, "the calm was real but the link was going away"


def test_a_weak_but_steady_signal_is_not_a_reason_to_stay_low() -> None:
    """At the real range the signal simply IS weak - the radio expects -80 - so
    a rule written against an absolute number would pin the picture at the floor
    for ever on the link this console is actually for."""
    clock, camera = Clock(), Camera()
    control = loop(clock, camera)

    feed(control, clock, link(BUSY, signal=-82.0), BUSY_FOR_SECONDS + BEAT)
    assert len(camera.asked) == 1
    clock.tick(MIN_SECONDS_BETWEEN_UP)

    feed(control, clock, link(CALM, signal=-82.0), CALM_FOR_SECONDS + BEAT)

    assert len(camera.asked) == 2


# --- what it says ------------------------------------------------------------


def test_every_change_says_what_it_did_and_why(caplog) -> None:
    clock, camera = Clock(), Camera()
    control = loop(clock, camera, ceiling=5000)

    with caplog.at_level(logging.INFO):
        feed(control, clock, link(BUSY), BUSY_FOR_SECONDS + BEAT)

    said = [
        record.getMessage()
        for record in caplog.records
        if str(int(5000 * DOWN_FACTOR)) in record.getMessage()
    ]
    assert said, [record.getMessage() for record in caplog.records]
    sentence = said[0]
    assert "kb/s" in sentence
    assert str(int(BUSY)) in sentence, "it has to say what the link was doing"
    banned = ("yolo", "cnn", "classifier", "inference", "model", "sensor")
    assert not any(word in sentence.lower() for word in banned), sentence


def test_it_never_asks_the_camera_to_change_anything_but_the_bitrate() -> None:
    """4K at 4 Mb/s is still 4K. A resolution change is far more disruptive for
    less benefit, so this loop does not have the vocabulary to ask for one."""
    clock, camera = Clock(), Camera()
    control = loop(clock, camera)

    feed(control, clock, link(BUSY), BUSY_FOR_SECONDS + BEAT)

    assert camera.asked and all(isinstance(kbps, int) for kbps in camera.asked)


# --- the switch --------------------------------------------------------------


def test_setting_the_picture_by_hand_stops_the_loop_dead() -> None:
    clock, camera = Clock(), Camera()
    control = loop(clock, camera, mode="manual")

    feed(control, clock, link(BUSY), 600)

    assert camera.asked == []
    assert control.state().running is False


def test_the_switch_takes_effect_without_restarting_the_console() -> None:
    clock, camera = Clock(), Camera()
    control = loop(clock, camera, mode="manual")
    feed(control, clock, link(BUSY), 600)
    assert camera.asked == []

    control.apply_settings(settings(mode="auto"))
    feed(control, clock, link(BUSY), BUSY_FOR_SECONDS + BEAT)

    assert camera.asked


# --- the arithmetic the thresholds rest on -----------------------------------


def test_a_step_up_cannot_land_the_link_back_in_trouble() -> None:
    """The gap between "quiet enough to spend more" and "busy enough to cut" has
    to be wider than one step moves the airtime, or the loop oscillates: up,
    busy, down, quiet, up, for ever, blipping the picture each time.
    """
    assert AIRTIME_CALM_PERCENT * UP_FACTOR < AIRTIME_BUSY_PERCENT


def test_it_retreats_faster_than_it_advances() -> None:
    assert DOWN_FACTOR < 1.0 < UP_FACTOR
    assert (1.0 - DOWN_FACTOR) > (UP_FACTOR - 1.0)


@pytest.mark.parametrize("airtime", [AIRTIME_CALM_PERCENT, AIRTIME_BUSY_PERCENT])
def test_the_thresholds_themselves_are_a_hold(airtime: float) -> None:
    """Exactly on the line is not over it. Anything else makes a link sitting on
    a threshold flap between two states with the reading unchanged."""
    clock, camera = Clock(), Camera()
    control = loop(clock, camera)

    feed(control, clock, link(airtime), max(BUSY_FOR_SECONDS, CALM_FOR_SECONDS) + BEAT)

    assert camera.asked == []


# ------------------------------------------------- two cameras on one radio link
#
# "The FLIR sends 2.5 Mbps and multiply it by 2 because there are 2 cameras."
# The ceiling on the Settings tab is how much of the LINK the video may use, and
# there are two consoles holding it now. Each reads the same airtime - airtime
# is a property of the medium, so it already counts the other camera - so two
# consoles each spending the whole ceiling is twice the link, and both of them
# then find it full and turn their own camera down for ever.


def sharing(clock: Clock, camera: Camera, cameras: int, **kwargs) -> BitrateLoop:
    return BitrateLoop(
        settings=settings(**kwargs),
        apply=camera,
        clock=clock,
        executor=lambda work: work(),
        share=lambda _settings: cameras,
    )


def test_one_camera_may_use_the_whole_ceiling() -> None:
    """Which is what this loop has always assumed, and is still true of a
    single-camera installation."""
    control = sharing(Clock(), Camera(), cameras=1, ceiling=5000)
    assert control.my_ceiling() == 5000


def test_two_cameras_get_half_the_ceiling_each() -> None:
    control = sharing(Clock(), Camera(), cameras=2, ceiling=5000)
    assert control.my_ceiling() == 2500


def test_a_share_is_never_taken_below_the_floor() -> None:
    """A ceiling divided until it is under the floor is two instructions that
    cannot both be obeyed, and the floor is the one that means "less than this
    is not worth showing"."""
    control = sharing(Clock(), Camera(), cameras=10, floor=1000, ceiling=5000)
    assert control.my_ceiling() == 1000


def test_the_loop_starts_at_its_share_and_not_at_the_whole_link() -> None:
    """The seed is what the loop believes the camera is already set to. Seeded
    at the whole ceiling, the very first thing a second console would do on a
    quiet link is try to raise past its share."""
    control = sharing(Clock(), Camera(), cameras=2, ceiling=5000)
    assert control.state().target_kbps == 2500


def test_a_quiet_link_never_raises_a_shared_camera_past_its_share() -> None:
    """The one that costs the link if it regresses: a quiet link is exactly when
    both consoles decide there is room, and both spending the whole ceiling is
    what fills it."""
    clock, camera = Clock(), Camera()
    control = sharing(clock, camera, cameras=2, ceiling=5000, floor=1000)
    # Down to the floor first, the only way this loop moves: a busy link. From
    # there it has the whole of a quiet day to climb back through its share.
    for _ in range(20):
        feed(control, clock, link(BUSY), BUSY_FOR_SECONDS + BEAT)
    assert control.state().target_kbps == 1000, "it never came down to start with"

    for _ in range(40):
        feed(control, clock, link(CALM), CALM_FOR_SECONDS + BEAT)

    assert control.state().target_kbps is not None
    assert control.state().target_kbps <= 2500, (
        f"a shared camera climbed to {control.state().target_kbps} kb/s of a "
        f"5000 kb/s link that two cameras are on"
    )


def test_a_second_camera_appearing_is_noticed_at_the_next_save() -> None:
    """A console is not restarted because somebody set the other camera up next
    door, so the share is read again rather than held from start-up."""
    clock, camera = Clock(), Camera()
    cameras = [1]
    control = BitrateLoop(
        settings=settings(ceiling=5000),
        apply=camera,
        clock=clock,
        executor=lambda work: work(),
        share=lambda _settings: cameras[0],
    )
    assert control.my_ceiling() == 5000

    cameras[0] = 2
    control.apply_settings(settings(ceiling=5000))
    assert control.my_ceiling() == 2500
    assert control.state().target_kbps == 2500, "it is still aiming at the whole link"


def test_a_share_that_cannot_be_worked_out_costs_nothing(caplog) -> None:
    """Reading the folder next door is a filesystem call on a machine whose disk
    is one of the things this console exists to report on. It must not be able
    to stop the loop."""

    def angry(_settings):
        raise OSError("the drive went away")

    control = BitrateLoop(
        settings=settings(ceiling=5000),
        apply=Camera(),
        clock=Clock(),
        executor=lambda work: work(),
        share=angry,
    )
    with caplog.at_level("ERROR"):
        assert control.my_ceiling() == 5000
    assert caplog.records, "it fell back to the whole link and said nothing"

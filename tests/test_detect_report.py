"""What the detector threw away, and why, in words.

"The VMD is shit... it's marking steady and static things... no movement."

Something was wrong and nothing on this console could say what. The counters
that answer it have been written to detection.json since the day they were
added, every few seconds, and nothing has ever read them.

The four shapes this fault can take call for four completely different fixes and
are indistinguishable from outside the machine. These tests are one per shape.
"""

from __future__ import annotations

from vmd.detect.report import lines


def state(**over) -> dict:
    base = {
        "stream": "thermal",
        "frames": 90_000,
        "blobs": 0,
        "rejected": {"ignore_mask": 0, "horizon": 0, "too_small": 0, "too_large": 0},
        "suppressed": 0,
        "events": 0,
    }
    base.update(over)
    return base


def said(**over) -> str:
    return " ".join(lines(state(**over)))


def test_everything_that_moves_becoming_an_alarm_is_named() -> None:
    """The shape he reported. Blobs are found, almost nothing is thrown away,
    and almost all of it becomes an event - so a treeline is an alarm all day."""
    words = said(blobs=52_000, events=48_000, rejected={"too_small": 800})
    assert "NEARLY EVERYTHING IS BEING REPORTED" in words
    assert "sensitivity" in words, "it names the fault and not the fix"


def test_every_blob_being_thrown_away_is_named_with_the_rule_that_did_it() -> None:
    """The shape that silently deletes real people, which is the dangerous one:
    nothing on the screen looks wrong and the perimeter is unwatched."""
    words = said(
        blobs=52_000,
        events=3,
        rejected={"too_small": 51_000, "ignore_mask": 100, "horizon": 40},
    )
    assert "NEARLY EVERYTHING IS BEING THROWN AWAY" in words
    assert "smaller than the size setting allows" in words, (
        "it must name which rule, because each one is a different fix"
    )


def test_a_detector_that_sees_nothing_at_all_says_so() -> None:
    """Not a settings problem, and saying "turn the sensitivity up" here would
    send somebody down the wrong road for a day."""
    words = said(blobs=2, events=0)
    assert "NOTHING IS BEING SEEN" in words
    assert "not the detection settings" in words


def test_a_camera_that_will_not_stay_still_is_named_as_the_camera() -> None:
    """Whole frames discarded because too much of the picture moved at once is
    the camera moving, and no amount of tuning the scene rules fixes a mast."""
    words = said(blobs=9_000, events=300, suppressed=40_000, rejected={"too_small": 2_000})
    assert "the camera moving" in words
    assert "mast" in words


def test_a_healthy_detector_is_not_lectured_at() -> None:
    """Some rejection is a rule doing its job. A diagnostic that has an opinion
    about every reading is one nobody reads."""
    words = said(blobs=4_000, events=40, rejected={"too_small": 1_200, "horizon": 300})
    assert "NEARLY EVERYTHING" not in words
    assert "NOTHING IS BEING SEEN" not in words
    assert "4,000" in words, "the counts are said whatever the verdict"


def test_the_counts_are_always_said_even_when_there_is_no_verdict() -> None:
    """The numbers are the evidence. A reading nobody can classify is still the
    thing to send to somebody who can."""
    words = said(blobs=100, events=10, rejected={"too_small": 30})
    assert "looked at 90,000 frames" in words
    assert "found 100" in words
    assert "reported 10" in words


def test_no_frames_at_all_is_its_own_sentence() -> None:
    assert "no frames have reached the detector" in said(frames=0)


def test_a_state_from_an_older_detector_costs_a_sentence_and_not_the_report() -> None:
    """This reads a file written by another process, which may be older than
    this code. A missing key must not take the whole report down."""
    assert lines({}) == ["no frames have reached the detector at all"]
    assert lines({"frames": 10, "blobs": "not a number"}), "it gave up on a bad value"

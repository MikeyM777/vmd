"""Association across frames, and the confirmation rule as a state machine.

None of this touches an image. The confirmation rule is the wind rule and it is
pure arithmetic over box centres and frame indices.
"""

from vmd.detect.motion import Box
from vmd.detect.tracking import Track, Tracker, confirmed


def walk(x: int, y: int = 100, size: int = 20) -> Box:
    return Box(x, y, size, size)


def test_a_track_records_what_it_saw():
    track = Track(id=1)
    track.observe(walk(10), 3)
    track.observe(walk(40), 4)
    assert track.first_frame == 3
    assert track.last_frame == 4
    assert track.box == walk(40)
    assert len(track.boxes) == 2


def test_travelled_is_the_distance_from_first_to_last_centre():
    track = Track(id=1)
    track.observe(Box(0, 0, 10, 10), 0)
    track.observe(Box(30, 40, 10, 10), 1)
    assert track.travelled == 50.0  # 3-4-5 triangle


def test_travel_does_not_accumulate_from_jitter():
    """A blob shivering in place has travelled nothing, however long it shivers."""
    track = Track(id=1)
    for index in range(40):
        track.observe(Box(100 + (index % 2), 100, 10, 10), index)
    assert track.travelled <= 1.5


def test_blobs_near_each_other_across_frames_are_one_track():
    tracker = Tracker()
    tracker.update([walk(10)], 0)
    tracker.update([walk(20)], 1)
    tracks = tracker.update([walk(30)], 2)
    assert len(tracks) == 1
    assert len(tracks[0].boxes) == 3


def test_a_blob_that_jumps_too_far_starts_a_new_track():
    tracker = Tracker(max_jump_px=30)
    tracker.update([walk(10)], 0)
    tracks = tracker.update([walk(300)], 1)
    assert len(tracks) == 2
    assert {t.id for t in tracks} == {1, 2}


def test_two_blobs_become_two_tracks_and_stay_apart():
    tracker = Tracker()
    tracker.update([walk(10), walk(200)], 0)
    tracks = tracker.update([walk(20), walk(210)], 1)
    assert len(tracks) == 2
    assert all(len(t.boxes) == 2 for t in tracks)


def test_a_track_that_reappears_after_a_long_gap_is_a_new_track():
    """Twenty frames of nothing is not the same dog coming back. It is news."""
    tracker = Tracker(max_gap_frames=3)
    first = tracker.update([walk(10)], 0)[0]
    for index in range(1, 20):
        tracker.update([], index)
    tracks = tracker.update([walk(10)], 20)
    assert len(tracks) == 1
    assert tracks[0].id != first.id


def test_a_track_survives_a_short_gap():
    tracker = Tracker(max_gap_frames=3)
    first = tracker.update([walk(10)], 0)[0]
    tracker.update([], 1)
    tracker.update([], 2)
    tracks = tracker.update([walk(30)], 3)
    assert len(tracks) == 1
    assert tracks[0].id == first.id


def _travelling_track(seen_frames: list[int]) -> Track:
    """A track seen on the given frames, moving 20 px each time it is seen."""
    track = Track(id=1)
    for position, frame in enumerate(seen_frames):
        track.observe(walk(10 + position * 20), frame)
    return track


def test_confirmation_is_n_of_m():
    """Two of the last five is not enough; three of the last five is."""
    two_of_five = _travelling_track([0, 4])
    assert confirmed(two_of_five, need=3, window=5, min_travel_px=12) is False

    three_of_five = _travelling_track([0, 2, 4])
    assert confirmed(three_of_five, need=3, window=5, min_travel_px=12) is True


def test_frames_outside_the_window_do_not_count():
    stale = _travelling_track([0, 1, 2, 10])
    assert confirmed(stale, need=3, window=5, min_travel_px=12) is False


def test_a_track_that_never_travels_is_never_confirmed():
    """The wind rule: foliage flickers in place for as long as you like."""
    track = Track(id=1)
    for index in range(60):
        track.observe(Box(100, 100, 12, 12), index)
    assert confirmed(track, need=3, window=5, min_travel_px=12) is False


def test_a_track_that_travels_far_enough_is_confirmed():
    track = Track(id=1)
    for index in range(5):
        track.observe(walk(10 + index * 10), index)
    assert confirmed(track, need=3, window=5, min_travel_px=12) is True

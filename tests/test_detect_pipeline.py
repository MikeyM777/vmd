"""The whole pipeline on synthetic frames: blobs, filters, tracking, confirmation.

Still no camera, no file, no model. A rectangle drawn with numpy stands in for a
person at 700 m, which is roughly what a person at 700 m looks like anyway.
"""

import time

import numpy as np

from vmd.detect.filters import is_global_motion
from vmd.detect.motion import MotionFinder
from vmd.detect.pipeline import DetectionConfig, DetectionPipeline

from tests.test_detect_motion import (
    BACKGROUND_GREY,
    FRAME_H,
    FRAME_W,
    MOVER_GREY,
    draw_rect,
    grey_frame,
)

# How many empty frames the subtractor gets before anything happens - eight
# seconds at 25 fps.
#
# It has to be this long to be honest. MOG2's automatic learning rate is
# 1/(2 x frames seen) until it falls to 1/history, so a model warmed for a
# handful of frames adapts at 1/12 per frame and absorbs *anything* repetitive
# within two frames. Warmed for six frames, the flickering-rectangle test below
# passes because the subtractor swallowed the flicker, which proves nothing
# about the confirmation rule that is supposed to reject it. Warmed properly,
# the flicker keeps producing blobs and the travel rule has to do the work -
# which is what happens on a real stream that has been up for a while.
WARMUP_FRAMES = 200


def feed_empty(pipeline: DetectionPipeline, count: int, start: int = 0) -> int:
    """Feed `count` empty frames. Returns the next frame index."""
    for index in range(start, start + count):
        assert pipeline.feed(grey_frame(), index) == []
    return start + count


def test_a_moving_rectangle_is_detected_and_confirmed():
    pipeline = DetectionPipeline()
    index = feed_empty(pipeline, WARMUP_FRAMES)

    detections = []
    last_x = 0
    for step in range(15):
        last_x = 30 + step * 10
        frame = grey_frame()
        draw_rect(frame, last_x, 110, 24, 24)
        detections.extend(pipeline.feed(frame, index))
        index += 1

    assert len(detections) == 1
    detection = detections[0]
    assert detection.track.travelled > 12
    # The reported box is where the rectangle was when it was confirmed, not
    # where it ended up - so only check it is somewhere along the walk.
    cx, _cy = detection.box.centre
    assert 30 <= cx <= last_x + 24
    assert abs(detection.box.centre[1] - 122) < 15


def test_a_rectangle_flickering_in_place_is_never_confirmed():
    """The wind rule, and the single most important test in this file.

    Foliage moving in wind produces blobs that appear, vanish and reappear in
    the same place. It is movement, and it is not news. A person or a dog
    travels; a branch does not.
    """
    pipeline = DetectionPipeline()
    index = feed_empty(pipeline, WARMUP_FRAMES)

    detections = []
    for step in range(40):
        frame = grey_frame()
        if step % 2 == 0:
            draw_rect(frame, 150, 110, 24, 24)
        detections.extend(pipeline.feed(frame, index))
        index += 1

    assert detections == []


def test_a_thirteen_pixel_walker_is_found_on_a_tall_frame():
    """The frame getting taller must not make the detector blinder.

    Every other test in this file draws on a 240-line frame, where a 24-pixel
    rectangle is a tenth of the picture. The visible camera delivers 1080 lines
    and this app re-encodes the stream over ONVIF while it is running, so the
    frame height is not a constant - and the person the system exists to see is
    13 px whatever the sensor is. Measured on the owner's own 1080x1920
    footage, reading the minimum height as a fraction of the frame rejected
    four of eight walk-throughs on every frame of them.
    """
    width, height = 1080, 1920
    pipeline = DetectionPipeline()
    index = 0
    for index in range(WARMUP_FRAMES):
        assert pipeline.feed(grey_frame(width, height), index) == []
    index += 1

    detections = []
    for step in range(20):
        frame = grey_frame(width, height)
        draw_rect(frame, 300 + step * 6, 900, 6, 13)  # a person at 700 m
        detections.extend(pipeline.feed(frame, index))
        index += 1

    assert len(detections) == 1
    assert detections[0].box.h < 30


def noise_frame(seed: int = 7, width: int = FRAME_W, height: int = FRAME_H) -> np.ndarray:
    """A textured scene. A flat grey frame cannot show a pan: shifting it changes nothing."""
    generator = np.random.default_rng(seed)
    return generator.integers(40, 210, size=(height, width), dtype=np.uint8)


def test_a_whole_frame_shift_produces_nothing():
    """A pan, or wind shaking the mast. Every blob in the frame is discarded."""
    scene = noise_frame()

    finder = MotionFinder()
    pipeline = DetectionPipeline()
    for index in range(WARMUP_FRAMES):
        finder.blobs(scene)
        assert pipeline.feed(scene, index) == []

    fired = False
    detections = []
    for step in range(12):
        shifted = np.roll(scene, (step + 1) * 13, axis=1)
        fired = fired or is_global_motion(finder.blobs(shifted), (FRAME_W, FRAME_H))
        detections.extend(pipeline.feed(shifted, WARMUP_FRAMES + step))

    assert fired is True
    assert detections == []
    # And the pipeline threw the frames away for that reason, rather than
    # happening to find nothing in them.
    assert pipeline.frames_suppressed > 0


def test_a_blob_in_the_ignore_mask_is_dropped_and_one_outside_it_is_not():
    mask = np.zeros((FRAME_H, FRAME_W), dtype=np.uint8)
    mask[:, :FRAME_W // 2] = 1  # the left half is painted out

    def walk_across(left_half: bool) -> list:
        pipeline = DetectionPipeline(DetectionConfig(ignore_mask=mask))
        index = feed_empty(pipeline, WARMUP_FRAMES)
        found = []
        for step in range(15):
            x = (10 if left_half else 170) + step * 8
            frame = grey_frame()
            draw_rect(frame, x, 110, 24, 24)
            found.extend(pipeline.feed(frame, index))
            index += 1
        return found

    assert walk_across(left_half=True) == []
    assert len(walk_across(left_half=False)) == 1


def test_a_blob_above_the_horizon_is_dropped_and_the_same_blob_below_it_is_kept():
    """The bird rule, end to end."""

    def fly(y: int, horizon_y: int) -> list:
        pipeline = DetectionPipeline(DetectionConfig(horizon_y=horizon_y))
        index = feed_empty(pipeline, WARMUP_FRAMES)
        found = []
        for step in range(15):
            frame = grey_frame()
            draw_rect(frame, 30 + step * 10, y, 24, 24)
            found.extend(pipeline.feed(frame, index))
            index += 1
        return found

    assert fly(y=20, horizon_y=120) == []  # a bird, well above the skyline
    assert len(fly(y=160, horizon_y=120)) == 1  # the same thing on the ground


def test_a_horizon_below_the_bottom_of_the_frame_is_refused_rather_than_obeyed(caplog):
    """A horizon lower than the frame is tall rejects every blob in it.

    It is typed into a spin box that accepts 0 to 100000 and knows nothing
    about how tall this stream is - and the operator types it against whatever
    resolution the console was showing, which is not necessarily this one. Obey
    it and the stream is a hundred percent blind, for ever, in silence. The
    design says as much itself: "a wrong horizon silently deletes real
    detections".
    """
    pipeline = DetectionPipeline(DetectionConfig(horizon_y=FRAME_H + 50))
    index = feed_empty(pipeline, WARMUP_FRAMES)

    detections = []
    with caplog.at_level("WARNING", logger="vmd.detect.pipeline"):
        for step in range(15):
            frame = grey_frame()
            draw_rect(frame, 30 + step * 10, 160, 24, 24)
            detections.extend(pipeline.feed(frame, index))
            index += 1

    assert len(detections) == 1, "a horizon that cannot be right must not be obeyed"
    said = " ".join(record.getMessage() for record in caplog.records)
    assert "horizon" in said.lower(), f"it was ignored without saying so: {said!r}"
    assert str(FRAME_H) in said, "the operator is not told what the frame height actually is"


def test_a_horizon_inside_the_frame_is_obeyed_and_says_nothing(caplog):
    pipeline = DetectionPipeline(DetectionConfig(horizon_y=120))
    index = feed_empty(pipeline, WARMUP_FRAMES)
    with caplog.at_level("WARNING", logger="vmd.detect.pipeline"):
        for step in range(15):
            frame = grey_frame()
            draw_rect(frame, 30 + step * 10, 20, 24, 24)  # a bird, above the line
            pipeline.feed(frame, index)
            index += 1
    assert caplog.records == []
    assert pipeline.rejected["horizon"] > 0


def test_the_pipeline_counts_what_each_rule_threw_away():
    """Every rule here deletes real detections when it is wrong, and every one
    of them does it silently. A count is the least the operator can be given:
    a rule that has rejected everything it has ever seen is a rule that is
    wrong, and it is visible as a number without anyone having to guess."""
    mask = np.zeros((FRAME_H, FRAME_W), dtype=np.uint8)
    mask[:, : FRAME_W // 2] = 1
    pipeline = DetectionPipeline(DetectionConfig(ignore_mask=mask))
    index = feed_empty(pipeline, WARMUP_FRAMES)

    for step in range(15):
        frame = grey_frame()
        draw_rect(frame, 10 + step * 4, 110, 24, 24)  # inside the painted half
        pipeline.feed(frame, index)
        index += 1

    assert pipeline.blobs_seen > 0
    assert pipeline.rejected["ignore_mask"] > 0
    assert pipeline.rejected["ignore_mask"] <= pipeline.blobs_seen


def test_a_confirmed_track_is_reported_once():
    """The alarm fires when the track becomes real, not on every frame after."""
    pipeline = DetectionPipeline()
    index = feed_empty(pipeline, WARMUP_FRAMES)

    detections = []
    for step in range(30):
        frame = grey_frame()
        draw_rect(frame, 10 + step * 8, 110, 24, 24)
        detections.extend(pipeline.feed(frame, index))
        index += 1

    assert len(detections) == 1
    assert detections[0].frame_index < index - 1  # confirmed early, then silent


def test_sensitivity_is_one_control():
    """Low, normal, high - not five sliders.

    High sensitivity finds a small faint mover that low sensitivity refuses,
    and low sensitivity still finds an obvious one. That is the whole contract.
    """

    def hunt(sensitivity: str, size: int, brightness: int) -> list:
        pipeline = DetectionPipeline(DetectionConfig(sensitivity=sensitivity))
        index = feed_empty(pipeline, WARMUP_FRAMES)
        found = []
        for step in range(20):
            frame = grey_frame()
            draw_rect(frame, 30 + step * 8, 110, size, size, brightness)
            found.extend(pipeline.feed(frame, index))
            index += 1
        return found

    faint_and_small = dict(size=6, brightness=BACKGROUND_GREY + 30)
    assert hunt("low", **faint_and_small) == []
    assert len(hunt("high", **faint_and_small)) == 1

    # ... and low is not simply deaf: an obvious mover still gets through.
    assert len(hunt("low", size=40, brightness=MOVER_GREY)) == 1


def test_the_pipeline_keeps_up_with_the_camera():
    """100 frames at the thermal sensor's resolution, under 20 ms each.

    The deployment laptop is weaker than the machine this runs on, so this is a
    floor rather than a measurement of the real thing.
    """
    width, height = 640, 512
    pipeline = DetectionPipeline()
    frames = []
    for step in range(100):
        frame = np.full((height, width), BACKGROUND_GREY, dtype=np.uint8)
        draw_rect(frame, 20 + step * 5, 200, 30, 30)
        frames.append(frame)

    started = time.perf_counter()
    for index, frame in enumerate(frames):
        pipeline.feed(frame, index)
    elapsed_ms = (time.perf_counter() - started) * 1000.0 / len(frames)

    print(f"\npipeline: {elapsed_ms:.2f} ms per frame at {width}x{height}")
    assert elapsed_ms < 20.0

"""The rejection rules, tested as the geometry they are - no images involved."""

import numpy as np

from vmd.detect.filters import (
    above_horizon,
    implausible_size,
    in_ignore_mask,
    is_global_motion,
)
from vmd.detect.motion import Box

FRAME_W, FRAME_H = 320, 240


def test_a_box_inside_the_ignore_mask_is_dropped_and_one_outside_is_not():
    mask = np.zeros((FRAME_H, FRAME_W), dtype=np.uint8)
    mask[100:150, 100:150] = 1  # the operator painted this swaying tree out

    inside = Box(110, 110, 20, 20)  # centre (120, 120) - inside the paint
    outside = Box(200, 110, 20, 20)  # centre (210, 120) - outside it
    assert in_ignore_mask(inside, mask) is True
    assert in_ignore_mask(outside, mask) is False


def test_a_box_straddling_the_mask_edge_is_judged_by_its_centre():
    mask = np.zeros((FRAME_H, FRAME_W), dtype=np.uint8)
    mask[:, :160] = 1  # columns 0..159 painted out
    assert in_ignore_mask(Box(140, 10, 20, 20), mask) is True  # centre x=150, masked
    assert in_ignore_mask(Box(155, 10, 20, 20), mask) is False  # centre x=165, clear


def test_no_mask_means_nothing_is_ignored():
    assert in_ignore_mask(Box(10, 10, 5, 5), None) is False


def test_a_box_off_the_frame_is_not_ignored():
    mask = np.ones((FRAME_H, FRAME_W), dtype=np.uint8)
    assert in_ignore_mask(Box(-50, -50, 10, 10), mask) is False


def test_a_blob_above_the_horizon_is_dropped_and_the_same_blob_below_it_is_kept():
    """The bird rule. Above the horizon line nothing is ground traffic."""
    horizon = 120
    bird = Box(50, 40, 10, 10)  # bottom at 50, entirely above the line
    walker = Box(50, 150, 10, 10)  # bottom at 160, on the ground
    assert above_horizon(bird, horizon) is True
    assert above_horizon(walker, horizon) is False


def test_a_blob_straddling_the_horizon_is_kept():
    # Its feet are below the line, so it may well be standing on the ground.
    assert above_horizon(Box(50, 110, 10, 30), 120) is False


def test_no_horizon_set_means_nothing_is_above_it():
    assert above_horizon(Box(50, 10, 10, 10), None) is False


def test_implausible_sizes_are_rejected_and_plausible_ones_are_not():
    # A person at 700 m is a handful of pixels; a blob covering half the frame
    # is the sun coming out, not an intruder.
    assert implausible_size(Box(0, 0, 4, 2), FRAME_H, 0.02, 0.5) is True  # too small
    assert implausible_size(Box(0, 0, 300, 200), FRAME_H, 0.02, 0.5) is True  # too large
    assert implausible_size(Box(0, 0, 10, 20), FRAME_H, 0.02, 0.5) is False


def test_global_motion_fires_when_most_of_the_frame_moves():
    """The camera itself moved: PTZ, or wind shaking the mast."""
    size = (FRAME_W, FRAME_H)
    whole_frame = [Box(0, 0, FRAME_W, FRAME_H)]
    assert is_global_motion(whole_frame, size) is True

    # Half the frame in two separate pieces - still the camera, not a person.
    halves = [Box(0, 0, FRAME_W, FRAME_H // 4), Box(0, 180, FRAME_W, FRAME_H // 4)]
    assert is_global_motion(halves, size) is True

    small = [Box(0, 0, 20, 20), Box(100, 100, 20, 20)]
    assert is_global_motion(small, size) is False


def test_global_motion_on_no_boxes_is_false():
    assert is_global_motion([], (FRAME_W, FRAME_H)) is False

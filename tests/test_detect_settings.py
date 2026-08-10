"""Detection settings, and the promise that yesterday's file still loads."""

import json

import numpy as np
import pytest

from vmd.detect.config import config_from_settings, mask_from_regions
from vmd.settings import (
    DetectionSettings,
    IgnoreRegion,
    Settings,
    SettingsError,
    StreamSettings,
    load_settings,
    save_settings,
)

# A settings.json written by the console *before* detection existed, captured
# verbatim. Every detection field has to have a default, or upgrading the
# software would leave the operator with a console that will not start.
SETTINGS_BEFORE_DETECTION = """{
  "video_mode": "auto",
  "video_buffer_ms": 500,
  "camera": {
    "host": "10.0.0.2",
    "username": "",
    "password": "",
    "streams": [
      {
        "name": "thermal",
        "url": "rtsp://10.0.0.2/thermal",
        "enabled": true,
        "reader": "auto"
      },
      {
        "name": "visible",
        "url": "rtsp://10.0.0.2/visible",
        "enabled": true,
        "reader": "auto"
      }
    ]
  },
  "radio": {
    "host": "",
    "username": "",
    "password": "",
    "enabled": false
  },
  "storage": {
    "root": "recordings",
    "budget_gb": 100.0,
    "budget_enabled": true,
    "retention_days": null,
    "warn_at_fraction": 0.9,
    "segment_seconds": 300
  },
  "bitrate": {
    "mode": "auto",
    "floor_kbps": 1000,
    "ceiling_kbps": 5000,
    "manual_kbps": 3000
  },
  "target_distance_m": 700.0
}"""


def test_a_settings_file_written_before_detection_existed_still_loads(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(SETTINGS_BEFORE_DETECTION, encoding="utf-8")

    settings = load_settings(path)

    assert settings.camera.host == "10.0.0.2"
    assert [s.name for s in settings.camera.streams] == ["thermal", "visible"]
    # Every detection field answered from a default, not from the file.
    assert settings.detection.enabled is True
    assert settings.detection.classify is False
    assert settings.detection.min_travel_px is None
    for stream in settings.camera.streams:
        assert stream.detect is False
        assert stream.sensitivity == "normal"
        assert stream.ignore_regions == []
        assert stream.horizon_y is None


def test_detection_defaults_to_off_per_stream():
    """Opt-in, because a detector pointed at a treeline before anyone has
    painted an ignore mask alarms all day and teaches the operator to ignore
    it."""
    assert StreamSettings(name="thermal", url="rtsp://x").detect is False


def test_detection_settings_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    settings = Settings()
    settings.camera.streams = [
        StreamSettings(
            name="thermal",
            url="rtsp://x/thermal",
            detect=True,
            sensitivity="high",
            horizon_y=140,
            ignore_regions=[IgnoreRegion(x=10, y=20, w=30, h=40)],
        )
    ]
    settings.detection = DetectionSettings(classify=True, min_travel_px=25.0)
    save_settings(settings, path)

    loaded = load_settings(path)
    stream = loaded.camera.streams[0]
    assert stream.detect is True
    assert stream.sensitivity == "high"
    assert stream.horizon_y == 140
    assert [(r.x, r.y, r.w, r.h) for r in stream.ignore_regions] == [(10, 20, 30, 40)]
    assert loaded.detection.classify is True
    assert loaded.detection.min_travel_px == 25.0


def test_an_unknown_sensitivity_is_refused(tmp_path):
    path = tmp_path / "settings.json"
    payload = json.loads(SETTINGS_BEFORE_DETECTION)
    payload["camera"]["streams"][0]["sensitivity"] = "paranoid"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SettingsError):
        load_settings(path)


def test_a_negative_horizon_is_refused():
    with pytest.raises(ValueError):
        StreamSettings(name="thermal", url="rtsp://x", horizon_y=-5)


def test_an_empty_ignore_region_is_refused():
    """A zero-width region ignores nothing and is a painting mistake."""
    with pytest.raises(ValueError):
        IgnoreRegion(x=0, y=0, w=0, h=10)


def test_a_negative_minimum_travel_is_refused():
    with pytest.raises(ValueError):
        DetectionSettings(min_travel_px=-1.0)


# --------------------------------------------------------------------------
# Settings -> DetectionConfig
# --------------------------------------------------------------------------


def test_config_carries_the_stream_s_choices():
    stream = StreamSettings(
        name="thermal", url="rtsp://x", detect=True, sensitivity="low", horizon_y=90
    )
    config = config_from_settings(stream, DetectionSettings())
    assert config.sensitivity == "low"
    assert config.horizon_y == 90
    assert config.tuning.min_travel_px == 24  # the "low" preset, untouched


def test_the_minimum_travel_override_reaches_the_tuning():
    stream = StreamSettings(name="thermal", url="rtsp://x", sensitivity="normal")
    config = config_from_settings(stream, DetectionSettings(min_travel_px=40.0))
    assert config.tuning.min_travel_px == 40.0
    # Everything else still comes from the preset it overrode.
    assert config.tuning.min_area == 40


def test_no_override_leaves_the_preset_alone():
    stream = StreamSettings(name="thermal", url="rtsp://x", sensitivity="high")
    config = config_from_settings(stream, DetectionSettings(min_travel_px=None))
    assert config.tuning.min_travel_px == 8


def test_the_ignore_mask_is_not_built_until_the_frame_size_is_known():
    """Nothing knows how big a frame is until one arrives, so the config leaves
    the mask empty and the runner paints it on the first frame."""
    stream = StreamSettings(
        name="thermal", url="rtsp://x", ignore_regions=[IgnoreRegion(x=0, y=0, w=5, h=5)]
    )
    assert config_from_settings(stream, DetectionSettings()).ignore_mask is None


def test_mask_from_regions_marks_only_the_painted_rectangle():
    mask = mask_from_regions([(2, 3, 4, 5)], width=20, height=20)
    assert mask is not None
    assert mask.shape == (20, 20)
    assert mask[3, 2] != 0
    assert mask[7, 5] != 0  # last row/column inside the rectangle
    assert mask[8, 6] == 0  # just outside it
    assert mask[0, 0] == 0
    assert np.count_nonzero(mask) == 4 * 5


def test_mask_from_regions_is_none_when_nothing_is_painted():
    assert mask_from_regions([], width=10, height=10) is None


def test_mask_from_regions_clips_a_region_that_hangs_off_the_frame():
    """The operator painted at one resolution; the stream arrived at another."""
    mask = mask_from_regions([(8, 8, 100, 100)], width=10, height=10)
    assert mask is not None
    assert mask.shape == (10, 10)
    assert np.count_nonzero(mask) == 4


def test_mask_from_regions_ignores_a_region_entirely_outside_the_frame():
    assert mask_from_regions([(50, 50, 10, 10)], width=10, height=10) is None


@pytest.mark.parametrize("region", [(50, 0, 10, 5), (0, 50, 5, 10)])
def test_a_region_off_one_edge_alone_still_paints_nothing(region):
    """Off to the right and off the bottom are separate clips, and a mask that
    covers nothing must read as no mask rather than as an empty one."""
    assert mask_from_regions([region], width=10, height=10) is None

"""Operator settings: what the user configures, stored as JSON on disk."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class SettingsError(Exception):
    """Raised when a settings file exists but cannot be read or is invalid."""


class Model(BaseModel):
    """Base for every settings model.

    `allow_inf_nan=False` is the whole point of it. JSON permits 1e400, which
    pydantic accepts as float("inf"), which serialises back out as null, which
    then fails to load - a value that validates on the way in and bricks the
    console on the way out. Refusing it at the door is the only fix that keeps
    "saved" and "loadable" the same thing.
    """

    model_config = ConfigDict(allow_inf_nan=False)


class IgnoreRegion(Model):
    """A rectangle of the frame where movement is not news.

    The only reliable answer to one specific swaying tree, a road, or a flag.
    Rectangles rather than a painted bitmap because a bitmap does not belong in
    a settings file the operator may have to read, and because a rectangle
    survives the stream changing resolution: it is clipped, not corrupted.
    """

    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0

    @field_validator("x", "y")
    @classmethod
    def _not_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("an ignore region must start inside the frame")
        return value

    @field_validator("w", "h")
    @classmethod
    def _has_area(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("an ignore region must have a width and a height")
        return value

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)


class StreamSettings(Model):
    name: str
    url: str
    enabled: bool = True

    # --- detection, per stream ---------------------------------------------
    #
    # Off by default, and deliberately so. A detector aimed at a treeline
    # before anyone has painted an ignore mask or set the horizon alarms all
    # day, and an operator who has learned to ignore the alarm strip is worse
    # off than one who has none. Thermal and visible also fail differently, so
    # this is a per-stream choice rather than one switch.
    detect: bool = False

    # One control, three positions, mapping to blob area and confirmation
    # counts. See vmd/detect/pipeline.py: the alternative is five sliders and
    # five ways to make the detector useless.
    sensitivity: Literal["low", "normal", "high"] = "normal"

    ignore_regions: list[IgnoreRegion] = Field(default_factory=list)

    # Is this the thermal head? Asked, rather than guessed.
    #
    # Nothing else in this file says which stream is which sensor, and the
    # user's camera names its streams `ch1` and `ch2` - so guessing from the
    # name would be wrong on the only camera this system has. The operator
    # knows which head is which, and this is the one place the software can be
    # told. It exists because of one consequence: the classifier is off by
    # default on the thermal, where a 13-pixel blob is not a photograph.
    #
    # Defaulting to False means an unmarked stream is treated as visible, so
    # the mistake an operator can make by not answering is a few wasted
    # milliseconds and an occasional junk label - never a missing event.
    thermal: bool = False

    # Run the classifier on this stream. None means "follow the sensor": off
    # for thermal, on for anything else. True or False is the operator
    # overruling that, which is allowed - the conclusion drawn from `thermal`
    # is a default, not a rule. Whichever way this lands, a confirmed track is
    # still an event: the classifier has no veto.
    classify: bool | None = None

    # Where the ground stops in this view, in frame pixels from the top. The
    # bird rule needs it. None disables the rule, which is the right default:
    # a wrong horizon silently deletes real detections.
    horizon_y: int | None = None

    @field_validator("horizon_y")
    @classmethod
    def _horizon_inside_the_frame(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("horizon_y must be 0 or more, or null to disable the rule")
        return value
    # Which client reads this stream from the camera.
    #
    #   auto   - the streaming server's own RTSP client. Lowest overhead.
    #   ffmpeg - ffmpeg reads it instead. The same demuxer VLC is built on, and
    #            it tolerates a stream that stutters where a stricter client
    #            gives up. Costs a process per stream and a little more delay.
    #
    # Offered per stream because they do not fail together: a thermal head and a
    # 4K head on one camera behave nothing alike on a link under pressure.
    reader: Literal["auto", "ffmpeg"] = "auto"


class CameraSettings(Model):
    host: str = ""
    username: str = ""
    password: str = ""
    streams: list[StreamSettings] = Field(default_factory=list)


class RadioSettings(Model):
    """Ubiquiti airOS radio. Optional: bitrate control falls back to video statistics."""

    host: str = ""
    username: str = ""
    password: str = ""
    enabled: bool = False


class StorageSettings(Model):
    root: Path = Path("recordings")
    budget_gb: float = 100.0
    budget_enabled: bool = True
    retention_days: int | None = None  # None disables the age rule
    warn_at_fraction: float = 0.9
    segment_seconds: int = 300

    @field_validator("budget_gb")
    @classmethod
    def _budget_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("budget_gb must be greater than 0")
        return value

    @field_validator("retention_days")
    @classmethod
    def _days_positive(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("retention_days must be greater than 0, or null to disable")
        return value

    @property
    def budget_bytes(self) -> int:
        return int(self.budget_gb * 1024**3)


class DetectionSettings(Model):
    """What detection is, everywhere. Per-stream choices live on StreamSettings.

    `enabled` is the master switch for the detector process. Turning it off
    stops detection and nothing else: recording is a separate process and shares
    nothing with this but the local stream.
    """

    enabled: bool = True

    # The master switch for the classifier. Off by default: at 700 m a person
    # is about 13 pixels, and a model trained on photographs has nothing useful
    # to say about that - and the weights are an optional install. With it on,
    # each stream decides for itself (StreamSettings.classify, defaulting to
    # off for the thermal). It never gates an event either way: an unnamed
    # track is still an event.
    classify: bool = False

    # How far a track must travel before it is believed, in pixels. None means
    # "whatever the sensitivity preset says", which is the honest default: the
    # presets were measured, and an operator who overrides this is overriding a
    # measurement.
    min_travel_px: float | None = None

    @field_validator("min_travel_px")
    @classmethod
    def _travel_not_negative(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("min_travel_px must be 0 or more, or null to follow the preset")
        return value


class BitrateSettings(Model):
    mode: Literal["auto", "manual"] = "auto"
    floor_kbps: int = 1000
    ceiling_kbps: int = 5000
    manual_kbps: int = 3000


class Settings(Model):
    # How the live picture reaches the browser.
    #
    #   webrtc - lowest delay, smallest tolerance for a link that stutters
    #   mp4    - buffers, so it survives a burst at the cost of a second or two
    #   auto   - webrtc, falling back to mp4 when it cannot deliver
    #
    # It is a setting rather than a decision because the right answer depends on
    # a link nobody can measure from here: on a clean link webrtc is plainly
    # better, and on a link that saturates when the camera pans, mp4 is.
    video_mode: Literal["auto", "webrtc", "mp4"] = "auto"

    # How much jitter the live picture absorbs before it stutters, in
    # milliseconds.
    #
    # WebRTC defaults to as little as it can get away with, which is right for a
    # conversation and wrong for this: a keyframe every second arrives as a
    # burst, and with nothing to absorb it the picture hitches once a second on
    # a link under pressure. Half a second of buffer costs half a second of
    # delay and removes the hitch - which is the trade VLC makes by default, and
    # why VLC looks smooth here while this did not.
    video_buffer_ms: int = 500

    @field_validator("video_buffer_ms")
    @classmethod
    def _buffer_sane(cls, value: int) -> int:
        if not 0 <= value <= 5000:
            raise ValueError("video_buffer_ms must be between 0 and 5000")
        return value

    camera: CameraSettings = Field(default_factory=CameraSettings)
    radio: RadioSettings = Field(default_factory=RadioSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    bitrate: BitrateSettings = Field(default_factory=BitrateSettings)
    detection: DetectionSettings = Field(default_factory=DetectionSettings)
    target_distance_m: float = 700.0


# Saves are serialised in-process. Two threads writing the same file is the
# common case here: the console has one settings form but many request threads.
_SAVE_LOCK = threading.Lock()


def load_settings(path: str | Path) -> Settings:
    """Load settings. A missing file means first run and yields defaults."""
    path = Path(path)
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SettingsError(f"settings file could not be read: {path}: {exc}") from exc
    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:
        raise SettingsError(f"invalid settings in {path}:\n{exc}") from exc


def save_settings(settings: Settings, path: str | Path) -> None:
    """Write settings so that the file on disk is never half-written.

    A plain write truncates first, so a crash, a power cut or a second writer
    mid-save leaves an empty or spliced file - and the operator loses the camera
    address and password with no warning. Writing a temporary file, flushing it
    to the platter and renaming it means the destination is only ever the old
    complete file or the new complete file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = settings.model_dump_json(indent=2)

    with _SAVE_LOCK:
        # Same directory as the destination: os.replace is only atomic within a
        # filesystem, and the temp directory may be on another one.
        handle, temp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise


def detect_free_bytes(path: str | Path) -> int | None:
    """Free space on the drive holding `path`, or None if it cannot be determined."""
    try:
        return shutil.disk_usage(str(path)).free
    except OSError:
        return None

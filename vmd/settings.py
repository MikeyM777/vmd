"""Operator settings: what the user configures, stored as JSON on disk."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


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

    @field_validator("name")
    @classmethod
    def _has_a_name(cls, value: str) -> str:
        """A stream name is an identifier, not a caption.

        It is the go2rtc stream id, the folder segments are filed under, the
        value events are attributed to and the one thing `wall_view`
        remembers. A stream with no name is one nothing downstream can address,
        and every one of those consumers would carry on without a word.

        The settings window builds a row before the operator has typed into it;
        it does that with `model_construct`, because a row on screen is not a
        setting yet. It becomes one at Save, which is where this fires and
        where the window already has a sentence for it.
        """
        if not value.strip():
            raise ValueError(
                "every stream needs a name - it is how the recording, the events "
                "and the live picture are told apart"
            )
        return value

    @field_validator("url")
    @classmethod
    def _only_a_camera_or_a_file(cls, value: str) -> str:
        """Refuse anything that is not an RTSP address or a file on this machine.

        This box is typed into by hand and its contents are handed straight to
        go2rtc's source parser, which is far larger than "a camera": the bundled
        binary also understands `exec:` (run this command line), and `ring:`,
        `wyze:`, `tapo:`, `nest:`, `hass:`, `ngrok:` and `http(s):`, several of
        which call out to a vendor's cloud. One pasted line would turn an
        air-gapped console into a cloud client or an arbitrary-command runner,
        and nothing downstream would object because passing an unknown scheme
        through untouched is exactly what the URL builder is supposed to do.

        A bare path is allowed - the recorder genuinely supports reading a local
        file, and that is how it is tested without a camera. A Windows path is a
        bare path too, even though `C:\\...` parses as a one-letter scheme.
        """
        text = value.strip()
        if not text:
            return value
        scheme = urlsplit(text).scheme.lower()
        if scheme in ("", "rtsp", "rtsps") or len(scheme) == 1:
            return value
        raise ValueError(
            f"a stream address must start with rtsp:// or rtsps:// (this one starts "
            f"with {scheme}:). Enter the camera's RTSP address."
        )

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

    @field_validator("streams")
    @classmethod
    def _names_are_distinct(cls, value: list[StreamSettings]) -> list[StreamSettings]:
        """Two streams cannot share a name, because nothing downstream could tell.

        go2rtc serves one stream under a name, the recorder files segments
        under it, the detector attributes events to it and the Live tab picks a
        view by it. Two streams called ch1 means the second camera's footage
        and the second camera's events are filed as the first camera's - so the
        operator watches a perimeter that is not the one on the screen, and
        every part of the system agrees with him.
        """
        seen: set[str] = set()
        for stream in value:
            key = stream.name.strip().casefold()
            if not key:
                # A row the operator has not filled in yet. Refused on the way
                # to being saved, by the window, where it can be pointed at.
                continue
            if key in seen:
                raise ValueError(
                    f"two streams are both called {stream.name!r}. The name is how "
                    f"the recordings, the events and the live picture are told "
                    f"apart, so each one needs its own."
                )
            seen.add(key)
        return value


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

    @field_validator("warn_at_fraction")
    @classmethod
    def _fraction_can_actually_fire(cls, value: float) -> float:
        """A fraction above 1 is the disk warning switched off in disguise.

        The rule is `used >= warn_at_fraction * budget`, and the retention sweep
        keeps `used` under the budget - so anything above 1 is a threshold that
        can never be reached. The one thing that tells the operator the disk is
        filling would be gone, and the settings file it went missing in would
        look entirely ordinary. Zero is refused at the other end for the mirror
        reason: a warning that is always on is one nobody reads.
        """
        if not 0 < value <= 1:
            raise ValueError(
                "warn_at_fraction must be more than 0 and at most 1 - it is the "
                "share of the budget at which the disk warning appears, so "
                "anything above 1 is a warning that can never appear"
            )
        return value

    @field_validator("segment_seconds")
    @classmethod
    def _segments_have_a_length(cls, value: int) -> int:
        """It is handed to ffmpeg as -segment_time, where 0 is not a length.

        Recording is one process away from the console and reports only that it
        is running, so a segment length that ffmpeg cannot act on is a day of
        files nobody looks at until somebody needs the footage.
        """
        if value <= 0:
            raise ValueError("segment_seconds must be greater than 0")
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

    @field_validator("floor_kbps", "ceiling_kbps", "manual_kbps")
    @classmethod
    def _a_bitrate_is_a_rate(cls, value: int) -> int:
        """Zero is not a low bitrate; it is the camera told to send nothing.

        `fit_to_link` caps every encoder on the camera to fit inside the
        ceiling, and the camera keeps what it is told. A zero here is a live
        picture that never comes back, applied to the camera rather than to
        this file.
        """
        if value <= 0:
            raise ValueError("a bitrate must be greater than 0 kb/s")
        return value

    @model_validator(mode="after")
    def _the_range_is_a_range(self) -> "BitrateSettings":
        if self.floor_kbps > self.ceiling_kbps:
            raise ValueError(
                f"the bitrate floor ({self.floor_kbps} kb/s) is above the ceiling "
                f"({self.ceiling_kbps} kb/s), which is two instructions that cannot "
                f"both be obeyed"
            )
        return self


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

    # Which of the camera's views the Live tab is showing: the name of one
    # stream, or empty for all of them side by side.
    #
    # A new field and not `video_mode`, which is a different question wearing a
    # similar name - `video_mode` is how the picture is carried (webrtc, mp4),
    # a transport left over from the browser console, and overloading it would
    # make one word mean two things in a file the operator can be asked to read
    # over the phone.
    #
    # A stream NAME and not a number, because a camera calls its views whatever
    # it likes and this file outlives any particular order they were listed in:
    # saved as position 1, a stream added above it silently changes which view
    # the console comes back to. A name that no longer exists falls back to
    # showing everything, which is the state that cannot hide anything.
    wall_view: str = ""

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
        return _with_absolute_root(Settings(), path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SettingsError(f"settings file could not be read: {path}: {exc}") from exc
    try:
        settings = Settings.model_validate(raw)
    except ValidationError as exc:
        raise SettingsError(f"invalid settings in {path}:\n{exc}") from exc
    return _with_absolute_root(settings, path)


def _with_absolute_root(settings: Settings, settings_path: Path) -> Settings:
    """Anchor a relative recording folder to the settings file, not the shell.

    `root` defaults to the relative "recordings", and three separate processes
    resolve it: the console, the recorder it starts, and anything run by hand.
    Both launchers pin the working directory, so today they agree - but a
    console started any other way would fill a second recordings tree somewhere
    else on the disk, and the operator would have no way to find the footage
    that went into it. Resolving against the file that named it means every
    process reaches the same folder however it was started.
    """
    root = Path(settings.storage.root)
    if not root.is_absolute():
        settings.storage.root = (settings_path.parent / root).resolve()
    return settings


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

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


class StreamSettings(Model):
    name: str
    url: str
    enabled: bool = True


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


class BitrateSettings(Model):
    mode: Literal["auto", "manual"] = "auto"
    floor_kbps: int = 1000
    ceiling_kbps: int = 5000
    manual_kbps: int = 3000


class Settings(Model):
    camera: CameraSettings = Field(default_factory=CameraSettings)
    radio: RadioSettings = Field(default_factory=RadioSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    bitrate: BitrateSettings = Field(default_factory=BitrateSettings)
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

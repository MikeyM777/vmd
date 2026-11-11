"""camera.restarts: the rules for restarting the camera by itself."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from vmd.settings import CameraSettings, RestartRule, Settings

SAVED = dt.datetime(2026, 11, 11, 13, 17)


def test_an_old_file_without_restarts_restarts_nothing() -> None:
    camera = CameraSettings.model_validate({"host": "10.0.0.2"})
    assert camera.restarts == []


def test_the_three_kinds_of_rule_survive_a_save_and_load() -> None:
    rules = [
        RestartRule(kind="every_hours", hours=2, since=SAVED),
        RestartRule(kind="daily", at="16:00", since=SAVED),
        RestartRule(kind="every_days", days=2, at="19:00", since=SAVED),
    ]
    camera = CameraSettings(host="10.0.0.2", restarts=rules)
    again = Settings.model_validate_json(Settings(camera=camera).model_dump_json())
    assert again.camera.restarts == rules


def test_a_time_is_written_back_as_hh_mm() -> None:
    assert RestartRule(kind="daily", at="9:05", since=SAVED).at == "09:05"


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "every_hours"},
        {"kind": "every_hours", "hours": 0},
        {"kind": "every_hours", "hours": 169},
        {"kind": "daily"},
        {"kind": "daily", "at": "25:00"},
        {"kind": "daily", "at": "16:60"},
        {"kind": "daily", "at": "four"},
        {"kind": "every_days", "at": "19:00"},
        {"kind": "every_days", "days": 0, "at": "19:00"},
        {"kind": "every_days", "days": 2},
    ],
)
def test_a_rule_that_cannot_happen_is_refused(fields: dict) -> None:
    with pytest.raises(ValidationError):
        RestartRule(since=SAVED, **fields)

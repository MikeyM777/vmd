# Camera Restart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `⟲ Restart camera` button on the Live tab and a list of scheduled restart rules in Settings, both sending ONVIF `SystemReboot` to this console's own camera.

**Architecture:** One new ONVIF call (`OnvifPtz.reboot`) carried through the existing `PtzService` / `PtzCommands` path on its own lane. A pure module (`restart_schedule.py`) decides when a rule is due; a tiny disk module (`restart_record.py`) remembers the last restart in `last_restart.json` beside `settings.json`. The window's 2-second heartbeat asks the schedule and fires; the Live tab only owns the button. The recorder's supervisor reads the record's age so a restart VMD asked for is not reported as flapping.

**Tech Stack:** Python 3.11, PySide6, pydantic v2, pytest + pytest-qt. Spec: `docs/superpowers/specs/2026-11-11-camera-restart-design.md`.

**Run tests with** (never plain `python`, never plain `uv run` - see `ORIENTATION.md`):

```
.\bin\uv.exe run --offline --frozen --no-sync pytest <path> -q
```

**Branch:** `feat/camera-restart` (already created). Do **not** bump `VERSION`, build a kit or push - the owner releases.

---

## File map

| File | What changes |
|---|---|
| `vmd/settings.py` | New `RestartRule` model; `CameraSettings.restarts: list[RestartRule]` |
| `vmd/ptz/restart_schedule.py` | **New.** Pure: `next_slot`, `due`, `next_restart`, `describe`, `next_in_words` |
| `vmd/ptz/restart_record.py` | **New.** `path_beside`, `read`, `write`, `restarted_within` |
| `vmd/ptz/onvif.py` | `OnvifPtz.reboot()` |
| `vmd/ptz/service.py` | `IN_WORDS["reboot"]`, `PtzService.reboot()`, `PtzCommands.reboot()` + `_send` branch |
| `vmd/supervisor.py` | Optional `excused` callable: short-lived deaths not counted while it is true |
| `vmd/record_main.py` | Pass `excused=` to the recorder's `Supervisor` |
| `vmd/desktop/live.py` | Button, `restart_asked` signal, `confirm_restart` injection, `send_restart()` |
| `vmd/desktop/window.py` | `restart_camera()`, `_tick_restarts()` on the heartbeat, rules refreshed on save |
| `vmd/desktop/restart_box.py` | **New.** The Settings editor box |
| `vmd/desktop/settings_tab.py` | Embed `RestartBox`; load / save / validate it |
| `tests/test_restart_schedule.py`, `tests/test_restart_record.py`, `tests/test_desktop_restart_box.py` | **New** tests |
| `tests/test_settings.py`, `tests/test_ptz.py`, `tests/test_ptz_commands.py`, `tests/test_supervisor.py`, `tests/test_desktop_live.py`, `tests/test_desktop_window.py`, `tests/test_desktop_settings_tab.py` | Added tests |

---

### Task 1: `RestartRule` in the settings model

**Files:**
- Modify: `vmd/settings.py` (imports at top; new class just above `class CameraSettings`, ~line 262; new field in `CameraSettings` after `ptz_speed`, ~line 274)
- Test: `tests/test_settings.py` (append)

- [ ] **Step 1: Write the failing tests** - append to `tests/test_settings.py`:

```python
# --- camera restarts ---------------------------------------------------------

import datetime as _dt

import pydantic
import pytest

from vmd.settings import CameraSettings, RestartRule, Settings

SAVED = _dt.datetime(2026, 11, 11, 13, 17)


def test_an_old_file_without_restarts_restarts_nothing() -> None:
    camera = CameraSettings.model_validate({"host": "10.0.0.2"})
    assert camera.restarts == []


def test_the_three_kinds_of_rule_are_accepted() -> None:
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
        {"kind": "every_hours"},                      # no number
        {"kind": "every_hours", "hours": 0},          # under an hour
        {"kind": "every_hours", "hours": 169},        # over a week
        {"kind": "daily"},                            # no time
        {"kind": "daily", "at": "25:00"},
        {"kind": "daily", "at": "16:60"},
        {"kind": "daily", "at": "four"},
        {"kind": "every_days", "at": "19:00"},        # no number of days
        {"kind": "every_days", "days": 0, "at": "19:00"},
        {"kind": "every_days", "days": 2},            # no time
    ],
)
def test_a_rule_that_cannot_happen_is_refused(fields: dict) -> None:
    with pytest.raises(pydantic.ValidationError):
        RestartRule(since=SAVED, **fields)
```

- [ ] **Step 2: Run to see them fail**

Run: `.\bin\uv.exe run --offline --frozen --no-sync pytest tests/test_settings.py -q -k "restart or rule or hh_mm"`
Expected: FAIL - `ImportError: cannot import name 'RestartRule'`

- [ ] **Step 3: Implement.** In `vmd/settings.py` add `import datetime` and `import re` to the imports (alphabetical, after `from __future__ import annotations` block's stdlib imports). Add above `class CameraSettings`:

```python
# "HH:MM" or "H:MM", 24-hour. Written back as HH:MM so the file always reads
# the same way whatever was typed.
_CLOCK = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


class RestartRule(Model):
    """One time the camera is restarted by itself. See
    docs/superpowers/specs/2026-11-11-camera-restart-design.md.

    `since` is when the operator saved the rule. "Every 2 hours" counts from
    it, and "every 2 days at 19:00" starts on the first 19:00 after it.
    """

    kind: Literal["every_hours", "daily", "every_days"]
    hours: int | None = None
    days: int | None = None
    at: str | None = None
    since: datetime.datetime

    @field_validator("at")
    @classmethod
    def _is_a_clock_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        match = _CLOCK.match(value)
        if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
            raise ValueError(
                f"{value!r} is not a time of day - write it like 16:00"
            )
        return f"{int(match.group(1)):02d}:{match.group(2)}"

    @model_validator(mode="after")
    def _has_what_its_kind_needs(self) -> "RestartRule":
        if self.kind == "every_hours":
            if self.hours is None or not 1 <= self.hours <= 168:
                raise ValueError(
                    "a restart every so many hours needs a number from 1 to 168"
                )
        if self.kind in ("daily", "every_days") and self.at is None:
            raise ValueError("a restart at a time of day needs the time, like 16:00")
        if self.kind == "every_days":
            if self.days is None or not 1 <= self.days <= 365:
                raise ValueError(
                    "a restart every so many days needs a number from 1 to 365"
                )
        return self
```

In `CameraSettings`, after the `ptz_speed` line:

```python
    # When the camera restarts by itself. Empty - the default, and what every
    # settings.json written before this existed reads as - restarts nothing.
    restarts: list[RestartRule] = Field(default_factory=list)
```

- [ ] **Step 4: Run to see them pass**

Run: `.\bin\uv.exe run --offline --frozen --no-sync pytest tests/test_settings.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```
git add vmd/settings.py tests/test_settings.py
git commit -m "settings: RestartRule and camera.restarts"
```

---

### Task 2: `restart_schedule.py` - when is a restart due

**Files:**
- Create: `vmd/ptz/restart_schedule.py`
- Test: `tests/test_restart_schedule.py`

- [ ] **Step 1: Write the failing tests** - create `tests/test_restart_schedule.py`:

```python
"""When the camera restarts by itself. Pure arithmetic on wall-clock times."""

from __future__ import annotations

from datetime import datetime, timedelta

from vmd.ptz.restart_schedule import describe, due, next_in_words, next_restart, next_slot
from vmd.settings import RestartRule

SAVED = datetime(2026, 11, 11, 13, 17)


def every_hours(n: int, since: datetime = SAVED) -> RestartRule:
    return RestartRule(kind="every_hours", hours=n, since=since)


def daily(at: str, since: datetime = SAVED) -> RestartRule:
    return RestartRule(kind="daily", at=at, since=since)


def every_days(n: int, at: str, since: datetime = SAVED) -> RestartRule:
    return RestartRule(kind="every_days", days=n, at=at, since=since)


# -- next_slot ----------------------------------------------------------------

def test_every_two_hours_counts_from_the_moment_it_was_saved() -> None:
    rule = every_hours(2)
    assert next_slot(rule, SAVED) == datetime(2026, 11, 11, 15, 17)
    assert next_slot(rule, datetime(2026, 11, 11, 15, 17)) == datetime(2026, 11, 11, 17, 17)
    assert next_slot(rule, datetime(2026, 11, 11, 23, 30)) == datetime(2026, 11, 12, 1, 17)


def test_daily_is_today_if_still_ahead_else_tomorrow() -> None:
    rule = daily("16:00")
    assert next_slot(rule, SAVED) == datetime(2026, 11, 11, 16, 0)
    assert next_slot(rule, datetime(2026, 11, 11, 16, 0)) == datetime(2026, 11, 12, 16, 0)


def test_daily_never_fires_before_it_was_saved() -> None:
    rule = daily("09:00")
    assert next_slot(rule, datetime(2026, 11, 10, 8, 0)) == datetime(2026, 11, 12, 9, 0)


def test_every_two_days_starts_on_the_first_time_after_saving() -> None:
    rule = every_days(2, "19:00")
    assert next_slot(rule, SAVED) == datetime(2026, 11, 11, 19, 0)
    assert next_slot(rule, datetime(2026, 11, 11, 19, 0)) == datetime(2026, 11, 13, 19, 0)
    assert next_slot(rule, datetime(2026, 11, 12, 20, 0)) == datetime(2026, 11, 13, 19, 0)


def test_every_two_days_saved_after_the_time_starts_tomorrow() -> None:
    rule = every_days(2, "10:00")
    assert next_slot(rule, SAVED) == datetime(2026, 11, 12, 10, 0)


# -- due ----------------------------------------------------------------------

def test_a_rule_is_due_on_the_tick_that_crosses_it() -> None:
    rule = daily("16:00")
    assert due([rule], datetime(2026, 11, 11, 15, 59, 59), None) is None
    assert due([rule], datetime(2026, 11, 11, 16, 0, 1), None) is rule


def test_a_missed_slot_is_skipped_not_caught_up() -> None:
    """Console opened at 17:40 after a 16:00 it missed: no restart now."""
    assert due([daily("16:00")], datetime(2026, 11, 11, 17, 40), None) is None


def test_a_restart_just_done_is_not_done_again() -> None:
    rule = daily("16:00")
    just = datetime(2026, 11, 11, 16, 0, 1)
    assert due([rule], datetime(2026, 11, 11, 16, 0, 3), last_restart=just) is None


def test_two_rules_within_ten_minutes_give_one_restart() -> None:
    hourly = every_hours(1, since=datetime(2026, 11, 11, 15, 5))  # 16:05
    at_four = daily("16:00")
    first = due([hourly, at_four], datetime(2026, 11, 11, 16, 0, 1), None)
    assert first is at_four
    later = datetime(2026, 11, 11, 16, 5, 1)
    assert due([hourly, at_four], later, last_restart=datetime(2026, 11, 11, 16, 0, 1)) is None


def test_a_clock_put_back_does_not_restart_twice() -> None:
    """The repeated hour at the end of summer time: last restart looks later than now."""
    rule = daily("01:30")
    assert due([rule], datetime(2026, 10, 25, 1, 30, 1), last_restart=datetime(2026, 10, 25, 1, 30, 5)) is None


def test_no_rules_nothing_due() -> None:
    assert due([], datetime(2026, 11, 11, 16, 0), None) is None


# -- what the operator reads --------------------------------------------------

def test_next_restart_is_the_earliest_of_all_rules() -> None:
    rules = [every_days(2, "19:00"), daily("16:00"), every_hours(2)]
    when, rule = next_restart(rules, datetime(2026, 11, 11, 14, 0))
    assert when == datetime(2026, 11, 11, 15, 17)
    assert rule is rules[2]
    assert next_restart([], datetime(2026, 11, 11, 14, 0)) is None


def test_each_rule_has_a_sentence() -> None:
    assert describe(every_hours(2)) == "every 2 hours"
    assert describe(every_hours(1)) == "every hour"
    assert describe(daily("16:00")) == "every day at 16:00"
    assert describe(every_days(2, "19:00")) == "every 2 days at 19:00"


def test_the_next_restart_in_words() -> None:
    now = datetime(2026, 11, 11, 12, 48)
    assert next_in_words(datetime(2026, 11, 11, 16, 0), now) == "today 16:00 (in 3 h 12 min)"
    assert next_in_words(datetime(2026, 11, 12, 9, 0), now) == "tomorrow 09:00 (in 20 h 12 min)"
    assert next_in_words(datetime(2026, 11, 13, 19, 0), now) == "13.11 19:00 (in 2 days)"
    assert next_in_words(now + timedelta(seconds=30), now) == "today 12:48 (in 1 min)"
```

- [ ] **Step 2: Run to see them fail**

Run: `.\bin\uv.exe run --offline --frozen --no-sync pytest tests/test_restart_schedule.py -q`
Expected: FAIL - `ModuleNotFoundError: No module named 'vmd.ptz.restart_schedule'`

- [ ] **Step 3: Implement** - create `vmd/ptz/restart_schedule.py`:

```python
"""When the camera restarts by itself. Pure: no Qt, no network, no disk.

Every time here is naive local wall-clock time, the same thing the operator
reads off the clock on the wall. No `zoneinfo`: the offline PC may have no tz
database. What that costs is written down in the spec and is accepted - on the
two DST nights an `every_hours` rule is an hour off once, a time that does not
exist that night is skipped, and one that happens twice fires once.

See docs/superpowers/specs/2026-11-11-camera-restart-design.md.
"""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta

from vmd.settings import RestartRule

# How late a tick may notice a slot and still act on it. The heartbeat is two
# seconds; a slot older than this was missed (the console was closed, the PC
# was off) and is skipped rather than caught up - a console starting after a
# power cut must not restart a camera that has only just come up.
GRACE = timedelta(seconds=60)

# Nothing restarts within this long of the last restart. The camera takes about
# two minutes to come back; restarting it again while it boots achieves nothing.
# It is also what merges two rules that land together, and what stops the
# repeated hour at the end of summer time firing a rule twice.
MIN_GAP = timedelta(minutes=10)


def _clock(at: str) -> time:
    hours, minutes = at.split(":")
    return time(int(hours), int(minutes))


def next_slot(rule: RestartRule, after: datetime) -> datetime:
    """The first restart time of this rule strictly later than `after`."""
    if rule.kind == "every_hours":
        step = timedelta(hours=rule.hours)
        if after < rule.since:
            return rule.since + step
        k = math.floor((after - rule.since) / step) + 1
        return rule.since + k * step

    at = _clock(rule.at)
    if rule.kind == "daily":
        start = max(after, rule.since)
        candidate = datetime.combine(start.date(), at)
        if candidate <= start:
            candidate += timedelta(days=1)
        return candidate

    # every_days: the first `at` at or after `since`, then every N days.
    first = datetime.combine(rule.since.date(), at)
    if first < rule.since:
        first += timedelta(days=1)
    if after < first:
        return first
    step = timedelta(days=rule.days)
    k = math.floor((after - first) / step) + 1
    return first + k * step


def due(
    rules: list[RestartRule], now: datetime, last_restart: datetime | None
) -> RestartRule | None:
    """The rule whose time has just come, or None.

    A slot is due when it lies in (now - GRACE, now] and nothing restarted the
    camera within MIN_GAP of now - in either direction, so a clock put back
    does not restart it again either.
    """
    if last_restart is not None and abs(now - last_restart) < MIN_GAP:
        return None
    for rule in rules:
        if next_slot(rule, now - GRACE) <= now:
            return rule
    return None


def next_restart(
    rules: list[RestartRule], now: datetime
) -> tuple[datetime, RestartRule] | None:
    """The next restart of all the rules, for the "Next restart" line."""
    best: tuple[datetime, RestartRule] | None = None
    for rule in rules:
        when = next_slot(rule, now)
        if best is None or when < best[0]:
            best = (when, rule)
    return best


def describe(rule: RestartRule) -> str:
    """The rule in the words the Logs tab and the Settings tab use."""
    if rule.kind == "every_hours":
        return "every hour" if rule.hours == 1 else f"every {rule.hours} hours"
    if rule.kind == "daily":
        return f"every day at {rule.at}"
    return f"every {rule.days} days at {rule.at}"


def next_in_words(when: datetime, now: datetime) -> str:
    """'today 16:00 (in 3 h 12 min)', 'tomorrow 09:00 (...)', '13.11 19:00 (in 2 days)'."""
    if when.date() == now.date():
        day = "today"
    elif when.date() == (now + timedelta(days=1)).date():
        day = "tomorrow"
    else:
        day = when.strftime("%d.%m")
    left = max(timedelta(0), when - now)
    minutes = max(1, math.ceil(left.total_seconds() / 60))
    if minutes >= 48 * 60:
        span = f"in {round(minutes / (24 * 60))} days"
    elif minutes >= 60:
        span = f"in {minutes // 60} h {minutes % 60} min"
    else:
        span = f"in {minutes} min"
    return f"{day} {when:%H:%M} ({span})"
```

- [ ] **Step 4: Run to see them pass**

Run: `.\bin\uv.exe run --offline --frozen --no-sync pytest tests/test_restart_schedule.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```
git add vmd/ptz/restart_schedule.py tests/test_restart_schedule.py
git commit -m "ptz: restart_schedule - when a scheduled camera restart is due"
```

---

### Task 3: `restart_record.py` - the last restart, on disk

**Files:**
- Create: `vmd/ptz/restart_record.py`
- Test: `tests/test_restart_record.py`

- [ ] **Step 1: Write the failing tests** - create `tests/test_restart_record.py`:

```python
"""last_restart.json: when this console last restarted its camera."""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

from vmd.ptz.restart_record import FILENAME, path_beside, read, restarted_within, write


def test_it_lives_beside_the_settings(tmp_path: Path) -> None:
    assert path_beside(tmp_path / "settings.json") == tmp_path / FILENAME


def test_what_is_written_is_what_is_read(tmp_path: Path) -> None:
    path = tmp_path / FILENAME
    when = datetime(2026, 11, 11, 16, 0, 1)
    write(path, when, "scheduled: every day at 16:00")
    assert read(path) == when


def test_no_file_is_no_restart(tmp_path: Path) -> None:
    assert read(tmp_path / FILENAME) is None
    assert restarted_within(tmp_path / FILENAME, 300) is False


def test_a_broken_file_is_no_restart_not_a_crash(tmp_path: Path) -> None:
    path = tmp_path / FILENAME
    path.write_text("{not json", encoding="utf-8")
    assert read(path) is None


def test_restarted_within_goes_by_the_age_of_the_file(tmp_path: Path) -> None:
    path = tmp_path / FILENAME
    write(path, datetime(2026, 11, 11, 16, 0), "by hand")
    assert restarted_within(path, 300) is True
    old = time.time() - 600
    os.utime(path, (old, old))
    assert restarted_within(path, 300) is False
```

- [ ] **Step 2: Run to see them fail**

Run: `.\bin\uv.exe run --offline --frozen --no-sync pytest tests/test_restart_record.py -q`
Expected: FAIL - `ModuleNotFoundError`

- [ ] **Step 3: Implement** - create `vmd/ptz/restart_record.py`:

```python
"""When this console last restarted its camera, kept beside settings.json.

Read by two processes: the console, so a console reopened in the same minute
does not restart the camera again, and the recorder, so the stream deaths a
restart causes are not reported as a recorder that keeps failing. The recorder
only needs the file's age, which is why `restarted_within` goes by mtime.

Nothing here raises. A missing or broken file means "no restart known".
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

FILENAME = "last_restart.json"


def path_beside(settings_path: str | Path) -> Path:
    return Path(settings_path).parent / FILENAME


def read(path: Path) -> datetime | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return datetime.fromisoformat(payload["at"])
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - a broken file is no restart, never a crash
        logger.warning("could not read %s; treating it as no restart", path)
        return None


def write(path: Path, when: datetime, why: str) -> None:
    """Atomically: a half-written file read by the recorder must not exist."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps({"at": when.isoformat(timespec="seconds"), "why": why})
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".last_restart.")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 - the restart itself must still be sent
        logger.exception("could not write %s", path)


def restarted_within(path: Path, seconds: float) -> bool:
    try:
        return time.time() - path.stat().st_mtime < seconds
    except OSError:
        return False
```

- [ ] **Step 4: Run to see them pass**

Run: `.\bin\uv.exe run --offline --frozen --no-sync pytest tests/test_restart_record.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```
git add vmd/ptz/restart_record.py tests/test_restart_record.py
git commit -m "ptz: restart_record - last_restart.json beside the settings"
```

---

### Task 4: `OnvifPtz.reboot()` - ONVIF SystemReboot

**Files:**
- Modify: `vmd/ptz/onvif.py` (new method after `set_home`, ~line 874)
- Test: `tests/test_ptz.py` (`FakeCamera.do_POST` command list ~line 100; new tests after `test_set_home_is_sent_as_a_set_home_command` ~line 202)

- [ ] **Step 1: Write the failing tests.** In `tests/test_ptz.py`, in `FakeCamera.do_POST`, add `"SystemReboot"` to the tuple of recognised commands:

```python
                (name for name in ("ContinuousMove", "GotoHomePosition", "SetHomePosition", "Stop", "SystemReboot") if f"<{name} " in body),
```

Then after `test_set_home_is_sent_as_a_set_home_command` add:

```python
def test_reboot_is_sent_to_the_device_service(camera: tuple[str, int]) -> None:
    connected(camera).reboot()
    path, body = next(
        (p, b) for p, b in reversed(FakeCamera.requests) if "SystemReboot" in b
    )
    assert path == "/onvif/device_service"
    assert '<SystemReboot xmlns="http://www.onvif.org/ver10/device/wsdl"' in body


def test_a_camera_that_refuses_to_reboot_says_why(camera: tuple[str, int]) -> None:
    ptz = connected(camera)
    FakeCamera.command_body = FAULT
    with pytest.raises(PtzError, match="Sender not Authorized"):
        ptz.reboot()
```

- [ ] **Step 2: Run to see them fail**

Run: `.\bin\uv.exe run --offline --frozen --no-sync pytest tests/test_ptz.py -q -k reboot`
Expected: FAIL - `AttributeError: 'OnvifPtz' object has no attribute 'reboot'`

- [ ] **Step 3: Implement** - in `vmd/ptz/onvif.py`, after `set_home`:

```python
    def reboot(self) -> None:
        """Restart the camera. ONVIF Device SystemReboot.

        In the Device core profile, so expected on the FLIR - but its datasheet
        lists only FLIR's own APIs, and this cannot be proven from the dev
        machine. A camera that refuses answers with a fault, which `_post`
        surfaces as it does for every other command. The camera drops off the
        network right after answering; nothing here waits for it to come back.
        """
        self._post(
            "/onvif/device_service",
            f'<SystemReboot xmlns="{DEVICE}"/>',
            expect="SystemRebootResponse",
        )
```

- [ ] **Step 4: Run to see them pass**

Run: `.\bin\uv.exe run --offline --frozen --no-sync pytest tests/test_ptz.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```
git add vmd/ptz/onvif.py tests/test_ptz.py
git commit -m "ptz: OnvifPtz.reboot - ONVIF SystemReboot"
```

---

### Task 5: `reboot` through `PtzService` and `PtzCommands`

**Files:**
- Modify: `vmd/ptz/service.py` (`IN_WORDS` ~line 42; `PtzService.set_home` ~line 297; `PtzCommands.set_home` ~line 579; `PtzCommands._send` ~line 716)
- Test: `tests/test_ptz.py` (service), `tests/test_ptz_commands.py` (lane)

- [ ] **Step 1: Look at `tests/test_ptz_commands.py`** to reuse its fake and its wait helper (it builds `PtzCommands(fake)` and waits with `wait_until_idle`). Then write the failing tests.

Append to `tests/test_ptz.py`:

```python
def test_the_service_reboots_and_forgets_it_was_connected(camera: tuple[str, int]) -> None:
    host, port = camera
    settings = Settings(camera=CameraSettings(host=host, username=USER, password=PASSWORD))
    service = PtzService(settings)
    service.camera.port = port
    service.status()
    assert service.reboot() == {"ok": True}
    assert service._connected is False
    assert any("SystemReboot" in body for _path, body in FakeCamera.requests)


def test_a_reboot_that_gets_no_answer_is_reported_as_sent() -> None:
    """The camera may already be going down; a timeout is not a failure."""
    settings = Settings(camera=CameraSettings(host="127.0.0.1", username=USER, password=PASSWORD))
    service = PtzService(settings)
    service.camera.port = 9  # discard port: nothing listens, the connection is refused

    def refuse() -> None:
        raise PtzError("cannot reach http://127.0.0.1:9/onvif/device_service after 8 s: refused")

    service.camera.reboot = refuse
    result = service.reboot()
    assert result["ok"] is True
    assert "did not answer" in result["note"]


def test_reboot_has_words_for_the_logs() -> None:
    assert in_words("reboot") == IN_WORDS["reboot"] != UNKNOWN_COMMAND
```

Append to `tests/test_ptz_commands.py` (use the file's existing fake class if it records calls; otherwise this one):

```python
class RecordingPtz:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def move(self, pan, tilt, zoom):
        self.calls.append("move")
        return {"ok": True}

    def stop(self):
        self.calls.append("stop")
        return {"ok": True}

    def reboot(self):
        self.calls.append("reboot")
        return {"ok": True}


def test_a_restart_is_not_thrown_away_by_the_next_arrow_key() -> None:
    """Steering is last-wins. A reboot in that lane would be lost to the next
    arrow press, so it has a lane of its own."""
    ptz = RecordingPtz()
    commands = PtzCommands(ptz)
    try:
        commands.reboot()
        commands.move(0.5, 0.0, 0.0)
        commands.stop()
        assert commands.wait_until_idle(5.0)
        assert "reboot" in ptz.calls
        assert ptz.calls.count("reboot") == 1
    finally:
        commands.close()
```

(Add `from vmd.ptz.service import PtzCommands` to the imports if the file does not already have it.)

- [ ] **Step 2: Run to see them fail**

Run: `.\bin\uv.exe run --offline --frozen --no-sync pytest tests/test_ptz.py tests/test_ptz_commands.py -q -k "reboot or restart"`
Expected: FAIL - `KeyError: 'reboot'` / `AttributeError: ... 'reboot'`

- [ ] **Step 3: Implement** in `vmd/ptz/service.py`.

Add to `IN_WORDS`:

```python
    "reboot": "the camera would not restart",
```

Add near the other module constants (after `UNANSWERED_AFTER`):

```python
# What the console says when a restart was sent and the camera went quiet. It
# may well be restarting already - it drops off the network as it does - so
# this is not reported as a failure, and the restart is not sent again.
RESTART_UNANSWERED = "restart sent; the camera did not answer (it may already be restarting)"
```

In `PtzService`, after `set_home`:

```python
    def reboot(self) -> dict:
        def work() -> None:
            self.camera.reboot()
            # The camera is going away. The next status() has to connect again
            # rather than trust a session to a camera that has rebooted.
            self._connected = False

        result = self._do("reboot", work)
        if result.get("ok") is False and "cannot reach" in result.get("error", ""):
            with self._lock:
                self._connected = False
            return {"ok": True, "note": RESTART_UNANSWERED}
        return result
```

In `PtzCommands`, after `set_home`:

```python
    def reboot(self) -> None:
        # Its own lane: steering is last-wins, and an arrow key pressed just
        # after Restart would otherwise silently discard the restart.
        self.submit(("reboot",), lane="restart")
```

In `PtzCommands._send`, after the `set_home` branch:

```python
            if kind == "reboot":
                return self._ptz.reboot()
```

- [ ] **Step 4: Run to see them pass**

Run: `.\bin\uv.exe run --offline --frozen --no-sync pytest tests/test_ptz.py tests/test_ptz_commands.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```
git add vmd/ptz/service.py tests/test_ptz.py tests/test_ptz_commands.py
git commit -m "ptz: reboot through PtzService and its own PtzCommands lane"
```

---

### Task 6: the recorder does not call a restart "flapping"

**Files:**
- Modify: `vmd/supervisor.py` (`Supervisor.__init__` ~line 54; the short-lived count in `tick` ~line 143)
- Modify: `vmd/record_main.py` (the `Supervisor(` call ~line 772; imports)
- Test: `tests/test_supervisor.py` (append)

- [ ] **Step 1: Write the failing test** - append to `tests/test_supervisor.py`:

```python
def test_deaths_while_excused_are_not_counted_as_flapping():
    """A camera restart VMD asked for kills every stream for two minutes. Those
    deaths are expected and must not become a 'keeps failing' warning."""
    service = FakeService(alive=False)
    clock = FakeClock()
    excused = {"now": True}
    supervisor = Supervisor(
        [Managed(name="thermal", service=service)],
        clock=clock,
        restart_delay=2.0,
        excused=lambda: excused["now"],
    )
    for _ in range(6):
        supervisor.tick()          # starts it
        clock.advance(3.0)
        service.running = False    # dies at once, as ffmpeg does with no camera
        supervisor.tick()
        clock.advance(3.0)
    assert supervisor.short_lived["thermal"] == 0
    assert supervisor.flapping("thermal") is False

    excused["now"] = False
    for _ in range(3):
        supervisor.tick()
        clock.advance(3.0)
        service.running = False
        supervisor.tick()
        clock.advance(3.0)
    assert supervisor.flapping("thermal") is True


def test_an_excuse_that_raises_is_no_excuse():
    service = FakeService(alive=False)
    clock = FakeClock()

    def broken() -> bool:
        raise OSError("disk gone")

    supervisor = Supervisor(
        [Managed(name="thermal", service=service)], clock=clock, excused=broken
    )
    supervisor.tick()
    clock.advance(3.0)
    service.running = False
    supervisor.tick()
    assert supervisor.short_lived["thermal"] == 1
```

- [ ] **Step 2: Run to see it fail**

Run: `.\bin\uv.exe run --offline --frozen --no-sync pytest tests/test_supervisor.py -q -k excuse`
Expected: FAIL - `TypeError: ... unexpected keyword argument 'excused'`

- [ ] **Step 3: Implement.** In `Supervisor.__init__` add a parameter after `stable_after`:

```python
        excused: Callable[[], bool] | None = None,
```

and store it (with the other attributes):

```python
        # True while deaths are expected - the camera was just restarted on
        # purpose. Starts that do not stick then are not counted as flapping.
        self._excused = excused
```

Add a method next to `flapping`:

```python
    def _excused_now(self) -> bool:
        if self._excused is None:
            return False
        try:
            return bool(self._excused())
        except Exception:  # noqa: BLE001 - an excuse that cannot be read is none
            logger.exception("could not ask whether deaths are excused")
            return False
```

In `tick`, change the short-lived condition from

```python
            if not held and started_at is not None and now - started_at < self._stable_after:
```

to

```python
            if (
                not held
                and started_at is not None
                and now - started_at < self._stable_after
                and not self._excused_now()
            ):
```

In `vmd/record_main.py` add the import `from vmd.ptz import restart_record` and a constant near the other module constants:

```python
# How long after a camera restart its stream deaths are expected. The camera is
# back in about two minutes; five leaves room for a slow radio link.
RESTART_EXCUSE_SECONDS = 300.0
```

and change the recorder's supervisor (~line 772) to:

```python
        self.supervisor = Supervisor(
            [Managed(name=r.stream, service=r) for r in self.recorders],
            excused=self._camera_was_just_restarted,
        )
```

with a method on the same class:

```python
    def _camera_was_just_restarted(self) -> bool:
        """The console wrote last_restart.json beside our settings: the camera
        is restarting on purpose, and its streams dying is expected."""
        if self.settings_path is None:
            return False
        return restart_record.restarted_within(
            restart_record.path_beside(self.settings_path), RESTART_EXCUSE_SECONDS
        )
```

- [ ] **Step 4: Run to see them pass**

Run: `.\bin\uv.exe run --offline --frozen --no-sync pytest tests/test_supervisor.py tests/test_record_main.py -q`
Expected: all PASS (if `tests/test_record_main.py` does not exist, run only the supervisor tests plus `pytest tests -q -k record`)

- [ ] **Step 5: Commit**

```
git add vmd/supervisor.py vmd/record_main.py tests/test_supervisor.py
git commit -m "supervisor: a restart VMD asked for is not flapping"
```

---

### Task 7: the Live tab button

**Files:**
- Modify: `vmd/desktop/live.py` (constants ~line 356; signals ~line 676; `__init__` params ~line 689; buttons ~line 988; `chooser_row` ~line 1018; methods after `set_home` ~line 3016)
- Test: `tests/test_desktop_live.py`

- [ ] **Step 1: Write the failing tests.** In `tests/test_desktop_live.py` add to `FakePtz`:

```python
    def reboot(self) -> dict:
        self.commands.append(("reboot",))
        return {"ok": True}
```

and append:

```python
def test_restart_is_sent_once(qtbot) -> None:
    tab, ptz, _ = build(qtbot, "thermal")
    tab.send_restart("by hand")
    assert sent(tab, ptz) == [("reboot",)]
    assert tab._moving.text() == "camera restarting"


def test_the_restart_button_asks_first_and_cancel_sends_nothing(qtbot) -> None:
    tab, ptz, _ = build(qtbot, "thermal")
    asked: list[bool] = []
    tab.restart_asked.connect(lambda: asked.append(True))
    tab._confirm_restart = lambda: False
    tab._restart_button.click()
    assert asked == []
    tab._confirm_restart = lambda: True
    tab._restart_button.click()
    assert asked == [True]
    assert sent(tab, ptz) == []  # the window sends it, not the button


def test_the_restart_button_is_rightmost_and_takes_no_focus(qtbot) -> None:
    tab, _ptz, _ = build(qtbot, "thermal")
    assert tab._restart_button.focusPolicy() == Qt.FocusPolicy.NoFocus
    row = tab._restart_button.parentWidget().layout()
    for index in range(row.count()):
        if row.itemAt(index).widget() is tab._restart_button:
            assert index == row.count() - 1
            break
    else:
        raise AssertionError("the restart button is not on the row above the pictures")
```

Note: the third test assumes the chooser row is the button's parent's layout. If `parentWidget().layout()` is the whole tab's layout, locate the row through `tab._fullscreen_button`'s containing `QHBoxLayout` instead and assert `indexOf(restart) == indexOf(fullscreen) + 1` and is the last item.

- [ ] **Step 2: Run to see them fail**

Run: `.\bin\uv.exe run --offline --frozen --no-sync pytest tests/test_desktop_live.py -q -k restart`
Expected: FAIL - `AttributeError: 'LiveTab' object has no attribute 'send_restart'`

- [ ] **Step 3: Implement** in `vmd/desktop/live.py`.

Constants beside `SET_HOME_WORDS`:

```python
RESTART_WORDS = "⟲ Restart camera"
RESTART_QUESTION = "Restart the camera?\n\nThere is no picture for about 2 minutes."
```

Signal beside `camera_asked`:

```python
    #: The operator pressed Restart and said yes. The window sends it - it is
    #: the thing that owns last_restart.json and the schedule - and this tab
    #: owns only the button. Same seam as `settings_asked`.
    restart_asked = Signal()
```

`__init__` gets a new keyword parameter after `executor`:

```python
        confirm_restart: Callable[[], bool] | None = None,
```

and, right after `self._commands = PtzCommands(ptz)`:

```python
        # Asked before a restart goes out. Injected so a test can answer it
        # without a modal dialog that would block the suite.
        self._confirm_restart = confirm_restart or self._ask_before_restart
```

After the `_set_home_button` block (~line 988):

```python
        # Restart the camera. Rightmost on the row, after the gear and
        # Fullscreen, as the owner asked - this row survives stream-only, so the
        # button is always there. NoFocus like every control on this tab.
        self._restart_button = QPushButton(RESTART_WORDS)
        self._restart_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._restart_button.setToolTip(
            "Restart the camera. It asks first. No picture for about 2 minutes."
        )
        self._restart_button.clicked.connect(self._restart_clicked)
```

In the `chooser_row` block, after `chooser_row.addWidget(self._fullscreen_button)`:

```python
        chooser_row.addWidget(self._restart_button)
```

Methods after `set_home`:

```python
    def _ask_before_restart(self) -> bool:
        answer = QMessageBox.question(
            self,
            "Restart the camera",
            RESTART_QUESTION,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _restart_clicked(self) -> None:
        if self._confirm_restart():
            self.restart_asked.emit()

    def send_restart(self, why: str) -> None:
        """Tell the camera to restart. Called by the window, for the button
        and for the schedule alike; `why` is for the window's log line."""
        self._moving.setText("camera restarting")
        self._commands.reboot()
        self._show_camera_note()
```

Add `QMessageBox` to the `PySide6.QtWidgets` import at the top of the file.

- [ ] **Step 4: Run to see them pass**

Run: `.\bin\uv.exe run --offline --frozen --no-sync pytest tests/test_desktop_live.py tests/test_desktop_live_fullscreen.py -q`
Expected: all PASS (including the existing "nothing on the live tab can take the arrow keys" test)

- [ ] **Step 5: Commit**

```
git add vmd/desktop/live.py tests/test_desktop_live.py
git commit -m "live: Restart camera button, rightmost, asks first"
```

---

### Task 8: the window sends restarts - by hand and on schedule

**Files:**
- Modify: `vmd/desktop/window.py` (imports; `__init__` after `settings = load_settings(...)` ~line 900 and signal wiring ~line 1030; `heartbeat` ~line 1361; `settings_saved` ~line 1699)
- Test: `tests/test_desktop_window.py`

- [ ] **Step 1: Write the failing tests** - append to `tests/test_desktop_window.py` (it already has `build(qtbot, tmp_path, ...)`; check whether its default `ptz` fake records commands - if not, pass one that does):

```python
from datetime import datetime

from vmd.ptz import restart_record
from vmd.settings import RestartRule, load_settings, save_settings


class RebootPtz:
    def __init__(self) -> None:
        self.commands: list[tuple] = []

    def status(self) -> dict:
        return {"available": True, "reason": "ready"}

    def reboot(self) -> dict:
        self.commands.append(("reboot",))
        return {"ok": True}

    def move(self, *a):
        return {"ok": True}

    def stop(self):
        return {"ok": True}


def test_the_button_restarts_the_camera_and_writes_it_down(qtbot, tmp_path) -> None:
    ptz = RebootPtz()
    window, _ = build(qtbot, tmp_path, ptz=ptz)
    window.live.restart_asked.emit()
    assert window.live.wait_for_camera(5.0)
    assert ptz.commands == [("reboot",)]
    assert restart_record.read(tmp_path / restart_record.FILENAME) is not None


def test_the_heartbeat_restarts_on_schedule_and_only_once(qtbot, tmp_path) -> None:
    ptz = RebootPtz()
    window, _ = build(qtbot, tmp_path, ptz=ptz)
    window._restart_rules = [
        RestartRule(kind="daily", at="16:00", since=datetime(2026, 11, 11, 9, 0))
    ]
    window._now = lambda: datetime(2026, 11, 11, 16, 0, 1)
    window.heartbeat()
    window._now = lambda: datetime(2026, 11, 11, 16, 0, 3)
    window.heartbeat()
    assert window.live.wait_for_camera(5.0)
    assert ptz.commands == [("reboot",)]


def test_a_saved_schedule_reaches_the_heartbeat(qtbot, tmp_path) -> None:
    window, path = build(qtbot, tmp_path)
    settings = load_settings(path)
    settings.camera.restarts = [
        RestartRule(kind="every_hours", hours=2, since=datetime(2026, 11, 11, 9, 0))
    ]
    window.settings_saved(settings)
    assert [r.hours for r in window._restart_rules] == [2]
```

(If `build` returns something other than `(window, path)`, adapt the unpacking to what it returns - read it at ~line 217 first.)

- [ ] **Step 2: Run to see them fail**

Run: `.\bin\uv.exe run --offline --frozen --no-sync pytest tests/test_desktop_window.py -q -k "restart or schedule"`
Expected: FAIL - `AttributeError: ... '_restart_rules'` / the reboot never sent

- [ ] **Step 3: Implement** in `vmd/desktop/window.py`.

Imports:

```python
from datetime import datetime

from vmd.ptz import restart_record, restart_schedule
```

In `__init__`, right after the `settings = load_settings(...)` try/except (~line 900):

```python
        # The camera's restart schedule, asked on every heartbeat. Kept here
        # and not in the Live tab: the window owns settings.json, the file
        # beside it and the heartbeat. `_now` is wall-clock local time and is
        # replaced by tests.
        self._restart_rules = list(settings.camera.restarts)
        self._restart_record = restart_record.path_beside(self._settings_path)
        self._now = datetime.now
```

In the signal wiring block, after the `camera_asked` connection (~line 1029):

```python
        asked_restart = getattr(self.live, "restart_asked", None)
        if asked_restart is not None:
            asked_restart.connect(lambda: self.restart_camera("by hand"))
```

New methods (place after `heartbeat`):

```python
    def restart_camera(self, why: str) -> None:
        """Restart this console's camera - the one in settings.camera.host.

        Written down first, so that a console reopened in the same minute does
        not do it again and the recorder knows its streams are about to die.
        """
        logger.warning("restarting the camera (%s)", why)
        restart_record.write(self._restart_record, self._now(), why)
        send = getattr(self.live, "send_restart", None)
        if send is not None:
            send(why)

    def _tick_restarts(self) -> None:
        rule = restart_schedule.due(
            self._restart_rules, self._now(), restart_record.read(self._restart_record)
        )
        if rule is not None:
            self.restart_camera(f"scheduled: {restart_schedule.describe(rule)}")
```

In `heartbeat`, as the last step:

```python
        try:
            self._tick_restarts()
        except Exception:  # noqa: BLE001 - the heartbeat goes on
            logger.exception("the camera restart schedule could not be checked")
```

In `settings_saved`, as the first statement after the docstring:

```python
        try:
            self._restart_rules = list(settings.camera.restarts)
        except Exception:  # noqa: BLE001 - a save must not throw back into Save
            logger.exception("the restart schedule could not be read from the save")
```

- [ ] **Step 4: Run to see them pass**

Run: `.\bin\uv.exe run --offline --frozen --no-sync pytest tests/test_desktop_window.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```
git add vmd/desktop/window.py tests/test_desktop_window.py
git commit -m "window: restart the camera by hand and on schedule"
```

---

### Task 9: the schedule editor in Settings

**Files:**
- Create: `vmd/desktop/restart_box.py`
- Modify: `vmd/desktop/settings_tab.py` (after `layout.addWidget(camera_box)` ~line 1395; `load` after `self.ptz_speed = ...` ~line 2725; `settings_from_form` camera update ~line 2992; `_problem` first lines ~line 3036)
- Test: `tests/test_desktop_restart_box.py`, `tests/test_desktop_settings_tab.py`

- [ ] **Step 1: Write the failing tests.** Create `tests/test_desktop_restart_box.py`:

```python
"""The 'Restart the camera on a schedule' box on the Settings tab."""

from __future__ import annotations

from datetime import datetime

from vmd.desktop.restart_box import RestartBox
from vmd.settings import RestartRule

NOW = datetime(2026, 11, 11, 12, 48)
SAVED = datetime(2026, 11, 1, 9, 0)


def box(qtbot, rules=()) -> RestartBox:
    widget = RestartBox()
    qtbot.addWidget(widget)
    widget.load(list(rules), NOW)
    return widget


def test_empty_says_there_is_nothing_scheduled(qtbot) -> None:
    widget = box(qtbot)
    assert widget.values(NOW) == []
    assert widget.next_line() == "No scheduled restarts"


def test_the_three_example_rules_round_trip(qtbot) -> None:
    widget = box(qtbot)
    widget.add_row(kind="every_hours", hours=2)
    widget.add_row(kind="daily", at="16:00")
    widget.add_row(kind="every_days", days=2, at="19:00")
    values = widget.values(NOW)
    assert [v["kind"] for v in values] == ["every_hours", "daily", "every_days"]
    assert values[0]["hours"] == 2 and values[0]["since"] == NOW
    assert values[1]["at"] == "16:00"
    assert values[2]["days"] == 2 and values[2]["at"] == "19:00"
    assert widget.next_line().startswith("Next restart: today 14:48")


def test_an_unchanged_rule_keeps_when_it_was_saved(qtbot) -> None:
    widget = box(qtbot, [RestartRule(kind="every_hours", hours=2, since=SAVED)])
    assert widget.values(NOW)[0]["since"] == SAVED


def test_a_changed_rule_counts_from_now(qtbot) -> None:
    widget = box(qtbot, [RestartRule(kind="every_hours", hours=2, since=SAVED)])
    widget.rows[0].number.setValue(3)
    assert widget.values(NOW)[0]["since"] == NOW


def test_the_cross_removes_a_rule(qtbot) -> None:
    widget = box(qtbot, [RestartRule(kind="daily", at="16:00", since=SAVED)])
    widget.rows[0].remove.click()
    assert widget.values(NOW) == []


def test_a_time_that_is_not_a_time_is_a_problem(qtbot) -> None:
    widget = box(qtbot)
    widget.add_row(kind="daily", at="25:00")
    assert "25:00" in widget.problem()
```

Append to `tests/test_desktop_settings_tab.py`:

```python
def test_a_restart_schedule_is_saved_with_the_camera(qtbot, tmp_path: Path) -> None:
    tab, path = build(qtbot, tmp_path)
    tab.camera_host = "192.168.1.250"
    tab.set_streams([("thermal", "rtsp://192.168.1.250:554/ch2", True, "auto")])
    tab.restart_box.add_row(kind="daily", at="16:00")
    assert tab.save() is True
    stored = load_settings(path)
    assert [(r.kind, r.at) for r in stored.camera.restarts] == [("daily", "16:00")]


def test_a_bad_restart_time_is_refused_on_save(qtbot, tmp_path: Path) -> None:
    tab, _path = build(qtbot, tmp_path)
    tab.camera_host = "192.168.1.250"
    tab.set_streams([("thermal", "rtsp://192.168.1.250:554/ch2", True, "auto")])
    tab.restart_box.add_row(kind="daily", at="25:00")
    assert tab.save() is False
```

- [ ] **Step 2: Run to see them fail**

Run: `.\bin\uv.exe run --offline --frozen --no-sync pytest tests/test_desktop_restart_box.py tests/test_desktop_settings_tab.py -q -k "restart or Restart or box"`
Expected: FAIL - `ModuleNotFoundError: No module named 'vmd.desktop.restart_box'`

- [ ] **Step 3: Implement** - create `vmd/desktop/restart_box.py`:

```python
"""'Restart the camera on a schedule': the rules, one row each, on Settings.

Thin: what a rule means and when it fires is `vmd/ptz/restart_schedule.py`.
This box only shows rows, reads them back, and says when the next one is.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from vmd.ptz.restart_schedule import next_in_words, next_restart
from vmd.settings import RestartRule

# (what the dropdown says, the rule kind). The number box and the time box are
# shown or hidden to match.
KINDS: list[tuple[str, str]] = [
    ("Every … hours", "every_hours"),
    ("Every day at", "daily"),
    ("Every … days at", "every_days"),
]
NOTHING = "No scheduled restarts"


class RestartRow(QWidget):
    def __init__(self, kind: str, hours: int | None, days: int | None,
                 at: str | None, since: datetime | None) -> None:
        super().__init__()
        self.kind = QComboBox()
        for label, value in KINDS:
            self.kind.addItem(label, value)
        self.number = QSpinBox()
        self.number.setRange(1, 365)
        self.at = QLineEdit()
        self.at.setPlaceholderText("16:00")
        self.at.setMaximumWidth(70)
        self.remove = QPushButton("✕")
        self.remove.setToolTip("Take this restart off the list")
        line = QHBoxLayout(self)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(self.kind)
        line.addWidget(self.number)
        line.addWidget(self.at)
        line.addStretch(1)
        line.addWidget(self.remove)

        self.kind.setCurrentIndex([v for _l, v in KINDS].index(kind))
        self.number.setValue(hours if kind == "every_hours" else (days or 2))
        if kind == "every_hours" and hours is None:
            self.number.setValue(2)
        self.at.setText(at or "")
        self.kind.currentIndexChanged.connect(self._show_what_this_kind_needs)
        self._show_what_this_kind_needs()
        # What it was when loaded. Unchanged, it keeps the moment it was saved;
        # changed, it counts from the next Save.
        self._loaded = self._shape() if since is not None else None
        self._since = since

    def _show_what_this_kind_needs(self) -> None:
        kind = self.kind.currentData()
        self.number.setVisible(kind in ("every_hours", "every_days"))
        self.at.setVisible(kind in ("daily", "every_days"))

    def _shape(self) -> dict:
        kind = self.kind.currentData()
        return {
            "kind": kind,
            "hours": self.number.value() if kind == "every_hours" else None,
            "days": self.number.value() if kind == "every_days" else None,
            "at": self.at.text().strip() if kind in ("daily", "every_days") else None,
        }

    def value(self, now: datetime) -> dict:
        shape = self._shape()
        since = self._since if shape == self._loaded else now
        return {**shape, "since": since}


class RestartBox(QGroupBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Restart the camera on a schedule", parent)
        self.rows: list[RestartRow] = []
        self._now = datetime.now()
        outer = QVBoxLayout(self)
        self._list = QVBoxLayout()
        outer.addLayout(self._list)
        add = QPushButton("+ Add a restart time")
        add.clicked.connect(lambda: self.add_row())
        outer.addWidget(add)
        self._next = QLabel(NOTHING)
        outer.addWidget(self._next)

    def load(self, rules: list[RestartRule], now: datetime) -> None:
        for row in list(self.rows):
            self._drop(row)
        self._now = now
        for rule in rules:
            self.add_row(rule.kind, rule.hours, rule.days, rule.at, rule.since)
        self._refresh_next()

    def add_row(self, kind: str = "every_hours", hours: int | None = None,
                days: int | None = None, at: str | None = None,
                since: datetime | None = None) -> RestartRow:
        row = RestartRow(kind, hours, days, at, since)
        row.remove.clicked.connect(lambda _=False, r=row: self._drop(r))
        row.kind.currentIndexChanged.connect(self._refresh_next)
        row.number.valueChanged.connect(self._refresh_next)
        row.at.textChanged.connect(self._refresh_next)
        self.rows.append(row)
        self._list.addWidget(row)
        self._refresh_next()
        return row

    def _drop(self, row: RestartRow) -> None:
        self.rows.remove(row)
        self._list.removeWidget(row)
        row.deleteLater()
        self._refresh_next()

    def values(self, now: datetime) -> list[dict]:
        return [row.value(now) for row in self.rows]

    def problem(self) -> str:
        """The first row the model would refuse, in its own words, or ''."""
        for value in self.values(self._now):
            try:
                RestartRule.model_validate(value)
            except Exception as exc:  # noqa: BLE001 - pydantic's error, said plainly
                shown = value.get("at") or value.get("hours") or value.get("days")
                return f"Restart time {shown}: {exc.errors()[0]['msg'] if hasattr(exc, 'errors') else exc}"
        return ""

    def next_line(self) -> str:
        return self._next.text()

    def _refresh_next(self, *_args) -> None:
        rules = []
        for value in self.values(self._now):
            try:
                rules.append(RestartRule.model_validate(value))
            except Exception:  # noqa: BLE001 - a half-typed row is not a rule yet
                continue
        found = next_restart(rules, self._now)
        self._next.setText(
            NOTHING if found is None else f"Next restart: {next_in_words(found[0], self._now)}"
        )
```

In `vmd/desktop/settings_tab.py`:

- imports: `from datetime import datetime` and `from vmd.desktop.restart_box import RestartBox`.
- after `layout.addWidget(camera_box)` (~line 1395):

```python
        # When the camera restarts by itself. Right under the camera, because
        # it is about that camera and nothing else on this page.
        self.restart_box = RestartBox()
        layout.addWidget(self.restart_box)
```

- in `load`, after `self.ptz_speed = settings.camera.ptz_speed`:

```python
        self.restart_box.load(list(settings.camera.restarts), datetime.now())
```

- in `settings_from_form`, inside `payload["camera"].update(...)`, after `ptz_speed=self.ptz_speed,`:

```python
            restarts=self.restart_box.values(datetime.now()),
```

- at the top of `_problem`, before the streams loop:

```python
        restart_problem = self.restart_box.problem()
        if restart_problem:
            return restart_problem
```

- [ ] **Step 4: Run to see them pass**

Run: `.\bin\uv.exe run --offline --frozen --no-sync pytest tests/test_desktop_restart_box.py tests/test_desktop_settings_tab.py -q`
Expected: all PASS. The `next_line` test expects 14:48 because `every_hours 2` saved at `NOW` = 12:48.

- [ ] **Step 5: Commit**

```
git add vmd/desktop/restart_box.py vmd/desktop/settings_tab.py tests/test_desktop_restart_box.py tests/test_desktop_settings_tab.py
git commit -m "settings: Restart the camera on a schedule box"
```

---

### Task 10: whole suite, then look at it

- [ ] **Step 1: Full suite (unit)**

Run: `.\bin\uv.exe run --offline --frozen --no-sync pytest -q -m "not integration"`
Expected: all PASS. Fix anything this branch broke before going on - in particular any test that counts the buttons on the Live tab's top row or asserts the `CameraSettings` field list.

- [ ] **Step 2: Self-test**

Run: `.\bin\uv.exe run --offline --frozen --no-sync python -m vmd.selftest`
Expected: exit code 0.

- [ ] **Step 3: Open the console and look** (no camera here - the press will report "cannot reach"/"did not answer", which is the expected sentence): start `VMD.bat`, check the button is rightmost on the top row, press it, Cancel sends nothing, Restart shows `camera restarting`; Settings shows the new box under Camera, `+ Add` / `✕` work, "Next restart" updates while typing, Save round-trips. Screenshot both tabs for the owner.

- [ ] **Step 4: Commit any fixes** with a message saying what was fixed. Do not bump `VERSION`, do not push - the owner releases, and the on-site proof (pressing Restart on each PC and photographing the screen) comes with that release.

---

## Amendments (2026-11-11, after the owner tried the prototypes)

Prototypes: `.superpowers/brainstorm/41545-1794423488/content/schedule-try-it-v2.html`, `live-countdown.html`.

**A. Task 9 is replaced: no typing anywhere.** The Settings box lists each rule as a sentence with a red **Delete**, and **+ Add a restart time** opens `RestartDialog`:
step 1 three big choices (Every few hours / Once a day / Every few days); step 2 chips 1,2,3,4,6,8,12 hours or 2,3,4,5,7 days, labelled "🔁 It keeps repeating, forever, until you delete it."; step 3 a grid of 24 hour buttons + minutes :00 :15 :30 :45. Before Add it shows the sentence ("Restart every 5 days at 19:00 - again and again, forever") and the next four times joined by arrows, "… and so on". A rule cannot be edited, only deleted and added again, so its `since` never needs reconciling. No time can be invalid, so `RestartBox.problem()` is gone. Dialog and box expose `choose_*`/`rule()`/`add_rule()` and an injectable `ask_for_rule` so tests never open a modal.

**B. New Task 11: the warning before a scheduled restart.** A red frame in the Live tab's top row, between the view buttons and Set Home (never over a picture; hidden = zero width; survives stream-only): `⚠ Camera restarts in 9:59 (every day at 16:00)`, from 10 minutes before. It flashes for 4 s at 10:00, 5:00, 1:00 and 0:30 left, and continuously in the last 30 s. While a restart is in progress (2 minutes after it is sent, by hand or by schedule) it reads `⟲ Camera restarting`. No skip button (owner: none). Pure helpers in `restart_schedule`: `upcoming(rules, now, last_restart, within)`, `flashing(seconds_left)`, `clock_words(seconds_left)`, `upcoming_slots(rule, now, n)`, `day_words(when, now)`. The window tells the tab the due time on each heartbeat (`LiveTab.set_restart_due(when, words)`); the tab redraws the clock on its own 250 ms timer so the seconds do not jump by two.

**Language:** English, like the rest of the console.

# View schedule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Live tab switches by itself to the night view (thermal) and back to the day view (visible) at two set times a day.

**Architecture:** A `ViewSchedule` setting plus a pure timing module (done), a heartbeat step in the window that chooses the view through the Live tab's existing view-button path, and a Settings box that edits the schedule and saves it on every change. Spec: `docs/superpowers/specs/2026-11-19-view-schedule-design.md`.

**Tech Stack:** Python 3.12, PySide6, pydantic v2, pytest + pytest-qt. Tests run with
`bin\uv.exe run --offline --frozen --no-sync pytest -p no:cacheprovider <files>` from `C:\dev\VMD`
(never plain `uv run` - it reaches for the network).

---

## Task 1: Setting - DONE (commit 5f207ba)

`vmd/settings.py`: `ViewSchedule(enabled, day_view, day_from="06:00", night_view, night_from="18:00")`,
times validated/normalised, equal times refused. `Settings.view_schedule`.

## Task 2: Pure module - DONE (commit 5f207ba)

`vmd/desktop/view_schedule.py`:
- `problem(sched, streams) -> str | None` - why an enabled schedule cannot run (None if off or fine)
- `active(sched, streams) -> bool`
- `current(sched, now) -> str`
- `crossed(sched, before, now) -> str | None` - switch in (before, now], latest wins, clock back -> None
- `next_switch(sched, now) -> (datetime, str)`
- `describe_switch(sched, view) -> "thermal from 18:00"`
- `next_in_words` re-exported from `vmd.ptz.restart_schedule`
Tests: `tests/test_view_schedule.py`.

## Task 3: Live tab + window tick

**Files:**
- Modify: `vmd/desktop/live.py` (LiveTab: add `show_view`, `view_names`)
- Modify: `vmd/desktop/window.py` (start-up choice, `_tick_view_schedule`, `_view_schedule_saved`, follow `settings_saved`)
- Test: `tests/test_desktop_window.py` (append cases), `tests/test_desktop_live.py` (append)

- [ ] **Step 1: LiveTab methods (live.py, near `_view_chosen` ~line 2794).**
```python
    def view_names(self) -> list[str]:
        """The streams the view buttons offer, without "all of them"."""
        return [name for name in self.views.views() if name]

    def show_view(self, name: str) -> bool:
        """Show one view as if its button were pressed. False if there is no
        such view or it is already showing - nothing happens then."""
        if name not in self.view_names() or name == self.views.chosen_view():
            return False
        self.views.choose(name)  # announce=True: view_changed -> wall_view saved
        return True
```
- [ ] **Step 2: Window state and start-up (window.py `__init__`, next to `self._restart_rules`).**
```python
        self._view_schedule = settings.view_schedule
        self._view_tick_at = self._now()
```
In `build_live`, before `tab.apply(settings)`:
```python
            names = [s.name for s in settings.camera.streams]
            if view_schedule.active(settings.view_schedule, names):
                settings.wall_view = view_schedule.current(settings.view_schedule, self._now())
```
(Only the in-memory copy - the Live tab starts on the right view without decoding the wrong one first.) Import: `from vmd.desktop import view_schedule`.
- [ ] **Step 3: Heartbeat step**, guarded like `_tick_restarts` in `heartbeat()`:
```python
        try:
            self._tick_view_schedule()
        except Exception:  # noqa: BLE001 - the heartbeat goes on
            logger.exception("the view schedule could not be checked")
```
```python
    def _tick_view_schedule(self) -> None:
        """Switch the Live tab's view when a day or night start time passes.
        Only on a crossing, so a view chosen by hand holds until the next one."""
        now = self._now()
        before, self._view_tick_at = self._view_tick_at, now
        names_of = getattr(self.live, "view_names", None)
        show = getattr(self.live, "show_view", None)
        if names_of is None or show is None:
            return
        if not view_schedule.active(self._view_schedule, names_of()):
            return
        view = view_schedule.crossed(self._view_schedule, before, now)
        if view is not None and show(view):
            logger.info("scheduled view switch: %s",
                        view_schedule.describe_switch(self._view_schedule, view))
```
- [ ] **Step 4: Saved from Settings.** Connect like `restarts_saved` (~line 976):
```python
        view_schedule_saved = getattr(self.settings_tab, "view_schedule_saved", None)
        if view_schedule_saved is not None:
            view_schedule_saved.connect(self._view_schedule_saved)
```
```python
    def _view_schedule_saved(self, sched) -> None:
        """A schedule just switched on or changed: show what it says now, at
        once - turning it on at 20:00 shows the night view now, not at 06:00."""
        self._view_schedule = sched
        self._view_tick_at = self._now()
        try:
            if view_schedule.active(sched, self.live.view_names()):
                if self.live.show_view(view_schedule.current(sched, self._view_tick_at)):
                    logger.info("view schedule on: showing %s", view_schedule.current(sched, self._view_tick_at))
        except Exception:  # noqa: BLE001 - saving must never fail loudly
            logger.exception("the view schedule could not be applied")
```
In `settings_saved(self, settings)`: `self._view_schedule = settings.view_schedule` (do NOT re-apply - a Save must not undo a hand choice).
- [ ] **Step 5: Tests** (append to `tests/test_desktop_window.py`, using its `build(qtbot, tmp_path, ...)` helper and a settings file with TWO streams named `thermal` (thermal=True) and `visible`; look at how existing tests there write streams). Cases:
  1. start-up at 20:00 with schedule on (patch `datetime.now` path: set the schedule + streams in settings.json before `build`, and make `window._now`-independent by asserting `current` result with a monkeypatched `vmd.desktop.window.datetime` OR by constructing then calling `_view_schedule_saved` - pick whichever the file's existing patterns support) -> `window.live.views.chosen_view() == "thermal"`.
  2. `_view_tick_at = 17:59:59`, `_now = 18:00:01`, `heartbeat()` -> thermal shown, and `load_settings(...).wall_view == "thermal"`.
  3. hand press: at 19:00 `window.live.views.choose("visible")`, heartbeat at 19:00:02 -> still visible; tick across 06:00 next day -> visible stays (it is the day view), tick across 18:00 -> thermal.
  4. schedule off -> heartbeat across 18:00 changes nothing.
  5. `settings_tab.view_schedule_saved.emit(sched)` at 20:00 -> thermal shown immediately (skip if Task 4 not merged yet: call `window._view_schedule_saved(sched)` directly).
  6. LiveTab: `show_view("nope")` is False; `show_view` of the chosen view is False.
- [ ] **Step 6:** run `tests/test_desktop_window.py tests/test_desktop_live.py tests/test_view_schedule.py`; all green.
- [ ] **Step 7: Commit** only these files: `git add vmd/desktop/live.py vmd/desktop/window.py tests/test_desktop_window.py tests/test_desktop_live.py && git commit -m "view schedule: the window switches the Live tab's view at day and night start"`.

## Task 4: Settings box

**Files:**
- Create: `vmd/desktop/view_schedule_box.py`
- Modify: `vmd/desktop/settings_tab.py` (build box under `restart_box` ~line 1408, `view_schedule_saved = Signal(object)` next to `restarts_saved` ~line 1173, `_save_view_schedule` next to `_save_restarts` ~line 2824, fill it in `load()`)
- Test: `tests/test_desktop_view_schedule_box.py`, append to `tests/test_desktop_settings_tab.py`

- [ ] **Step 1: The box.** `ViewScheduleBox(QGroupBox)` titled "Switch the view on a schedule". Read `vmd/desktop/restart_box.py` first and copy its look, spacing, notes and NoFocus policy; import helpers from it rather than duplicating where possible. Do NOT change restart_box behaviour.
  - `changed = Signal()`
  - `load(sched: ViewSchedule, streams: list[tuple[str, bool]], now: Callable[[], datetime] = datetime.now)` - streams are `(name, thermal)`. Empty `day_view`/`night_view` are filled in the UI: night = first thermal stream, day = first non-thermal; fallback first/second stream. Loading emits nothing.
  - Widgets: `QCheckBox("Switch the view by itself")`; day row `[QComboBox of stream names] from [QPushButton "06:00"]`; night row the same; a `QLabel` next line.
  - Time button opens `TimeDialog` (in this file): tap-only, hour buttons 00-23, quarter buttons :00 :15 :30 :45 plus a 0-59 minute `QSpinBox`, OK/Cancel - same style as `RestartDialog`'s hour/minute rows. `TimeDialog(initial: str)`, `.value() -> str "HH:MM"`, testable without exec (methods `choose_hour(int)`, `choose_minute(int)`).
  - Picking a time equal to the other period's time is refused in the dialog (OK disabled + note "day and night cannot start at the same time").
  - `schedule() -> ViewSchedule` from the widgets.
  - Any user change -> refresh line -> `changed.emit()`.
  - Line text: off -> "Off"; `view_schedule.problem(...)` -> that reason; else `"Next switch: " + next_in_words(when, now) + f" -> {view}"`. Refresh the line every 30 s with a QTimer so the countdown moves (like RestartBox does - check how it refreshes).
  - Fewer than two streams: every control disabled, line "needs two views - add a second stream".
- [ ] **Step 2: Settings tab wiring.**
```python
    view_schedule_saved = Signal(object)
```
```python
        self.view_schedule_box = ViewScheduleBox()
        self.view_schedule_box.changed.connect(self._save_view_schedule)
        layout.addWidget(self.view_schedule_box)
```
```python
    def _save_view_schedule(self) -> None:
        """Write only the view schedule into settings.json, now - like the
        restart times, without Save."""
        sched = self.view_schedule_box.schedule()
        try:
            settings = load_settings(self.settings_path)
            settings.view_schedule = sched
            save_settings(settings, self.settings_path)
        except (OSError, SettingsError) as exc:
            logger.exception("the view schedule could not be saved")
            self._set_message(f"The view schedule could not be saved: {exc}")
            return
        self._loaded.view_schedule = sched
        self._set_message("View schedule saved.")
        self.view_schedule_saved.emit(sched)
```
  In `load()`, where `restart_box.load(...)` is called: `self.view_schedule_box.load(settings.view_schedule, [(s.name, s.thermal) for s in settings.camera.streams])`.
  Make sure the main Save (`settings_from_form` / `save`) keeps `view_schedule` from `_loaded` and does not reset it - add a test.
- [ ] **Step 3: Tests** - `tests/test_desktop_view_schedule_box.py`: defaults from thermal flags; one stream -> disabled + reason; tick on -> `changed` emitted and `schedule().enabled`; TimeDialog value / equal-time refusal; next line text at a fixed `now` ("Next switch: today 18:00 (in 6 h 0 min) -> thermal" for 12:00 - check exact `next_in_words` output). Settings tab: toggling the box writes `view_schedule` to disk and emits `view_schedule_saved`; a normal Save afterwards keeps it.
- [ ] **Step 4:** run `tests/test_desktop_view_schedule_box.py tests/test_desktop_settings_tab.py tests/test_desktop_restart_box.py tests/test_desktop_camera_tools.py`; all green.
- [ ] **Step 5: Commit** only these files: `git add vmd/desktop/view_schedule_box.py vmd/desktop/settings_tab.py tests/test_desktop_view_schedule_box.py tests/test_desktop_settings_tab.py && git commit -m "view schedule: Settings box - day and night view and start times, saved at once"`.

## Task 5: Verify

- [ ] Full suite: `bin\uv.exe run --offline --frozen --no-sync pytest -p no:cacheprovider -m "not integration"`. Known flaky: `test_desktop_camera_tools` can time out under full-suite load (passes alone) - rerun alone before calling it a failure.
- [ ] Independent review of the diff against the spec.
- [ ] Offscreen screenshot of the Settings box (`QT_QPA_PLATFORM=offscreen`, `widget.grab().save(...)`) to eyeball the layout.

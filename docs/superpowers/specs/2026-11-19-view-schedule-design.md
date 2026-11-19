# View schedule: thermal at night, visible by day

Date: 2026-11-19. Status: approved in brainstorming, not built.

## Why

The console watches one view at a time - thermal at night, visible by day (see
`Settings.stream_only`). Today the operator presses the view button on the Live
tab by hand, twice a day. He wants the switch to happen by itself at set times,
the way the camera restart already does (`docs/superpowers/specs/2026-11-11-camera-restart-design.md`).

## Decisions taken

| Question | Decision |
|---|---|
| What switches | **The console view only** - exactly what pressing a view button on the Live tab does (`LiveTab.views`, saved as `Settings.wall_view`). Nothing is sent to the camera. Recording is untouched. |
| Rule shape | **One day/night pair**: "<day view> from HH:MM", "<night view> from HH:MM", every day. Not a rule list. |
| A press by hand | **Holds until the next switch time**, then the schedule takes over again. No pause button, no forcing back. |
| On console start | **Shows what the schedule says now** (opened at 20:00 with night from 18:00 -> night view), overriding the saved `wall_view`. |
| Warning on the Live tab | **None.** A view switch is not disruptive; no countdown. |
| Per console | Yes. Each console has its own `settings.json`, so each has its own schedule. |

## Components

1. **`vmd/settings.py` - `ViewSchedule` and `Settings.view_schedule`.**
   ```
   class ViewSchedule(Model):
       enabled: bool = False
       day_view: str = ""      # a stream name
       day_from: str = "06:00"
       night_view: str = ""    # a stream name
       night_from: str = "18:00"
   ```
   - `day_from` / `night_from` validated like `RestartRule.at` (HH:MM, 00-23,
     00-59, normalised to two digits). Reuse the same `_CLOCK` regex.
   - `day_from == night_from` is rejected (no length to either period).
   - `Settings.view_schedule: ViewSchedule = Field(default_factory=ViewSchedule)`.
     Top level, not under `camera`: it is about the console's screen. Every
     existing `settings.json` loads unchanged and switches nothing.
   - Empty `day_view` / `night_view` means "not picked yet". Defaults are filled
     by the editor (component 4), not by the model: night = the first stream
     with `thermal=True`, day = the first stream with `thermal=False`.

2. **`vmd/desktop/view_schedule.py` - new, pure.** No Qt, no disk, no network.
   Naive local wall-clock time, same as `vmd/ptz/restart_schedule.py`.
   - `current(sched, now) -> str` - the view that should be on at `now`.
     Day period is `[day_from, night_from)`, night is the rest; either may
     cross midnight (night 18:00 -> 06:00, or an odd day 20:00 -> 04:00).
   - `crossed(sched, before, now) -> str | None` - if a switch time lies in
     `(before, now]`, the view that switch turns on (the latest one if several),
     otherwise None. The window calls this with the previous tick's time.
   - `next_switch(sched, now) -> tuple[datetime, str]` - the next switch strictly
     after `now`, and to which view. For the "Next switch" line.
   - `describe_switch(sched, view) -> str` - "thermal from 18:00", for Logs.
   - `next_in_words` is reused from `vmd/ptz/restart_schedule.py`, not copied.
   - All functions assume `sched.enabled` and both views set; callers check
     `active(sched, streams) -> bool` first: enabled, both views non-empty,
     different, and both present in the configured stream names.

3. **`vmd/desktop/window.py` - the tick.**
   - At start-up, after the Live tab has applied settings: if
     `active(...)`, choose `current(sched, now)` on the Live tab. This goes
     through the same path as a button press, so `wall_view` is saved as usual.
   - On every heartbeat, a new guarded step (`_tick_view_schedule`), beside
     `_tick_restarts`: `crossed(sched, self._view_tick_at, now)`; if it returns a
     view, choose it on the Live tab and log
     `scheduled view switch: thermal from 18:00`. Then `self._view_tick_at = now`.
     The first tick's `before` is the start-up time, so start-up and the tick
     never both fire.
   - A clock put back (`now < before`) switches nothing and just resets
     `before`.
   - Choosing the view that is already showing does nothing (no log, no save).
   - A press by hand needs no code: the schedule only acts when a switch time
     passes.
   - The Live tab gets one small public method if it has none:
     `show_view(name)` -> `self.views.choose(name)` (announce=True, so the
     existing `view_changed` -> `wall_view` save path runs).
   - Schedule changes from Settings arrive through a new `view_schedule_saved`
     signal on the Settings tab (same pattern as `restarts_saved`). On arrival
     the window stores the new schedule and, if `active`, shows `current(...)`
     straight away - turning it on at 20:00 shows thermal now, not at 06:00.
   - The schedule also follows `settings_saved` (a stream renamed or removed on
     the Settings tab is noticed).

4. **`vmd/desktop/view_schedule_box.py` - new, the editor.** A `QGroupBox`
   "Switch the view on a schedule", placed on the Settings tab directly under the
   restart box.
   - A tick: "Switch the view by itself".
   - Two rows: `[view dropdown] from [HH:MM button]` for day and for night. The
     time button opens a small tap-only dialog with hour and minute choices, in
     the same style as `RestartDialog` (`vmd/desktop/restart_box.py`) - quarter
     hour buttons plus a 0-59 minute field. Reuse what can be imported from
     `restart_box.py`; do not modify the restart feature's behaviour.
   - A line: "Next switch: today 18:00 -> thermal (in 3 h 12 min)", or "Off"
     when not active, or the reason it cannot run ("needs two views - add a
     second stream", "day and night are the same view").
   - Saved the moment anything changes - no Save button - through
     `SettingsTab._save_view_schedule`, which writes only `view_schedule` into
     the file (read-modify-write, like `_save_restarts`) and emits
     `view_schedule_saved`.
   - Fewer than two streams: the whole box disabled, with the reason.
   - All controls `NoFocus` where the existing boxes do so.

## Edge cases

- A view named in the schedule that no longer exists: `active` is False, the box
  says which name is missing, Logs gets one line (not one per heartbeat).
- `stream_only` mode, fullscreen: unaffected, the view buttons still exist.
- The "All views" choice ("") is never offered in the schedule dropdowns.
- DST: same accepted cost as the restart schedule - a skipped wall time on the
  spring night means the switch happens on the first tick after it (the period
  changed), since `crossed` compares wall times.
- Console closed across a switch time: start-up shows `current(...)`, so nothing
  is missed.

## Testing

- `tests/test_view_schedule.py` - pure module: `current` across both periods,
  night across midnight, day across midnight, exact boundary instants,
  `crossed` with a 2 s step, a long gap (hours) crossing several switches ->
  latest wins, clock put back, `next_switch` today/tomorrow, `active` rules.
- `tests/test_settings_view_schedule.py` - defaults, old files load,
  time validation and normalisation, equal times rejected, round trip.
- `tests/test_desktop_view_schedule_box.py` - defaults picked from `thermal`
  flags, edits emit and save, disabled with one stream, next-switch line.
- `tests/test_desktop_window.py` (added cases) - fake clock: start-up shows the
  current view, a heartbeat across 18:00 switches and logs, a hand press holds
  until the next switch, schedule saved from Settings applies at once.
- Whole suite stays green: `bin\uv.exe run --offline --frozen --no-sync pytest`.

## Not in scope

- Sending anything to the camera (sensor switching, palettes).
- Sunrise/sunset times.
- More than two switches a day, per-weekday rules.

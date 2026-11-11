# Camera restart: a button and a schedule

Date: 2026-11-11. Status: approved in brainstorming, not built.

## Why

The operator wants to restart the camera from the console, and to have it
restart by itself at times set in advance (every 2 hours, every day at 16:00,
every 2 days at 19:00). Today the only way is the FLIR's own browser GUI.

## The setup this is for

- Camera: Teledyne FLIR Elara DX (thermal + visible on one head), steered over
  ONVIF by `vmd/ptz/onvif.py`.
- **Two PCs, two cameras, one camera per PC.** Every console restarts only its
  own camera (`settings.camera.host`). There is no "restart the other one".
- VMD is now a **watching product only**. Movement alarms play no part in this
  feature: a scheduled restart happens at its time, whatever is on screen.

## Decisions taken

| Question | Decision |
|---|---|
| Where is the button | Live tab, top row, **rightmost** - after the ⚙ button. That row survives stream-only mode, so the button is always reachable. |
| What a press does | A confirmation dialog: *"Restart the camera? There is no picture for about 2 minutes."* - **Restart** / **Cancel**. |
| How the schedule is set | A **list of rules**, any number at once, `+ Add a restart time`, `✕` per rule. |
| Rule kinds | `every N hours`, `every day at HH:MM`, `every N days at HH:MM`. |
| "Every N hours" counts from | **The moment the rule was saved** (saved 13:17, every 2 h → 15:17, 17:17, ...). |
| "Every N days at HH:MM" counts from | The first HH:MM at or after the save moment (saved today 13:00, every 2 days at 19:00 → today 19:00, then every 2 days). |
| Who runs the schedule | **The console** (the VMD window). It autostarts at logon and is always open on this product. No new scheduled task, so the ordinary copy-only USB update (APPLY-UPDATE) is enough to deploy it. |

## Components

1. **`vmd/ptz/onvif.py` - `OnvifPtz.reboot()`.** Posts
   `<SystemReboot xmlns="http://www.onvif.org/ver10/device/wsdl"/>` to
   `/onvif/device_service` through the existing `_post` (same auth fallbacks,
   same `TIMEOUT`), expecting `SystemRebootResponse`. A fault raises `PtzError`
   with the camera's own reason.
2. **`vmd/ptz/service.py` - `PtzService.reboot()` and `PtzCommands.reboot()`.**
   Same shape as `home()`: goes through `_do`, runs on the commands thread, the
   GUI never waits on the network.
3. **`vmd/desktop/live.py` - the button.** `⟲ Restart camera`, `NoFocus` like
   every control on the Live tab (the arrow keys must keep steering). Press →
   confirmation dialog → `restart_asked` signal → the window writes
   `last_restart.json` and calls `LiveTab.send_restart()`, which queues
   `reboot` and sets the steering line to *"camera restarting"*.
4. **`vmd/settings.py` - `CameraSettings.restarts: list[RestartRule]`.**
   `RestartRule` has `kind: Literal["every_hours", "daily", "every_days"]`,
   `hours: int | None`, `days: int | None`, `at: str | None` ("HH:MM"),
   `since: datetime` (when it was saved). Defaults to `[]`, so every existing
   `settings.json` keeps working and restarts nothing.
5. **`vmd/ptz/restart_schedule.py` - new, pure.** No Qt, no network, no disk.
   `next_restart(rules, now, last_restart) -> (when, rule) | None` and
   `due(rules, now, last_restart) -> rule | None`. Everything about time lives
   here, so all of it is unit-tested without a window.
6. **`vmd/desktop/settings_tab.py` - the editor.** A box *"Restart the camera on
   a schedule"*: one row per rule (kind dropdown + number + HH:MM field + ✕),
   `+ Add a restart time`, and a line *"Next restart: today 16:00 (in 3 h 12
   min)"* or *"No scheduled restarts"*.

## Timing

- **The tick.** The Live tab's existing 2-second heartbeat calls
  `restart_schedule.due(...)`. When a rule is due, it sends `reboot` (no dialog -
  it was scheduled) and logs `scheduled restart (<rule in words>)`.
- **Slots.**
  - `every_hours N`: `since + k·N h`, k ≥ 1.
  - `daily HH:MM`: every day at HH:MM local time.
  - `every_days N at HH:MM`: the first HH:MM at or after `since`, then every N
    days after that one.
- **Due** means: a slot lies in `(last_restart, now]` and `now - slot < 60 s`.
- **Missed slots are skipped, never caught up.** Console opened at 17:40 after a
  missed 16:00 → no restart now; wait for the next slot. Otherwise every start
  after a power cut would restart a camera that has only just come up.
- **No double restart.** `last_restart` (UTC timestamp) is written to
  `last_restart.json` beside the console's `settings.json` the moment a restart
  is sent, by hand or by schedule. A console reopened in the same minute reads
  it and does not fire again.
- **Rules that coincide.** A slot less than 3 minutes from `last_restart` is
  skipped (the camera takes ~2 minutes to come up; restarting it while it boots
  achieves nothing). So "every 2 h" and "daily 16:00" landing together give one
  restart.
- **Local time, DST.** Every rule uses naive wall-clock local time (no
  `zoneinfo`: the offline PC may have no tz database). Accepted consequences: on
  the two DST days an `every_hours` rule is an hour off once, a wall time that
  does not exist that night (02:00-03:00 spring) is skipped, and one that
  happens twice fires once (the 3-minute gap blocks the repeat).
- **The command lane.** `reboot` goes into its own `PtzCommands` lane
  (`"restart"`), never the steering lane - a steering lane is last-wins, and an
  arrow key pressed right after Restart would otherwise silently discard it.

## After a restart

- go2rtc, the panes and the recorder lose the camera and reconnect by
  themselves, as they already do when the radio link drops.
- For 5 minutes after a restart was sent, stream deaths are **expected**: they
  are not counted toward the supervisor's `FLAPPING_AFTER`, so no "keeps
  failing" warning appears for a restart VMD asked for. Only the **recorder's**
  supervisor needs this (its ffmpegs are what die when the camera goes away;
  the console's children - go2rtc, recorder, detector - stay up). It reads the
  age of `last_restart.json` beside the same `settings.json`.
- The steering line under "Steering" reads `camera restarting` after a restart
  is sent, until the next steering command replaces it.

## Errors

- **Fault from the camera** (e.g. not supported): camera note *"the camera
  refused the restart: <reason>"*, a warning in Logs. A failed scheduled restart
  is not retried; the next slot is.
- **No answer (timeout):** the camera may already be going down. Note *"restart
  sent, the camera did not answer"* - not reported as a failure, not re-sent.
  `last_restart` is still written.
- **Invalid rule:** `25:00`, `0` hours or days, or under 1 hour, is refused on
  Save with a sentence pointing at the row, like every other settings field.

## Testing

- `tests/ptz/test_restart_schedule.py`: every-2-hours from the save moment,
  daily 16:00, every 2 days at 19:00, crossing midnight, DST start and end,
  missed slot skipped, two rules within 3 minutes → one restart, reopened in
  the same minute → no second restart, no rules → `None`.
- `tests/ptz/test_onvif_reboot.py`: the exact SOAP body and path against a fake
  camera; a SOAP fault surfaces as `PtzError` with the camera's reason.
- `tests/settings`: an old `settings.json` without `restarts` loads; invalid
  rules are refused.
- Live tab: the button exists, is rightmost in the top row, takes no focus,
  Cancel sends nothing, Restart sends exactly one `reboot`.
- **On site:** after the USB update, press Restart on each PC and photograph the
  screen. This is the only proof that this FLIR accepts ONVIF `SystemReboot`.

## Risk

The FLIR datasheet lists only FLIR's own APIs, not ONVIF - yet PTZ over ONVIF
works on the unit. `SystemReboot` is in the ONVIF Device core profile, so it is
expected to work, but it cannot be checked from the dev machine. If the camera
refuses it, the follow-up is FLIR's own reboot over CGI - **not in this scope**.

## Not in scope

Restarting the other PC's camera; catching up missed restarts; anything tied to
movement alarms; FLIR CGI.

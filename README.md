# VMD

A video motion detection console for a single multi-spectral PTZ camera watching a
distant perimeter. It shows live video, records continuously, raises an alarm when
something moves, and lets an operator look back through what was recorded.

The deployment it is built for: one FLIR-class thermal + visible PTZ head roughly
700 m from the area of interest, reaching the laptop over a Ubiquiti point-to-point
link more than 15 km long, at around 5 Mb/s. The laptop has no internet and no wifi:
the system is entirely offline, nothing is published anywhere, and nothing but this
one laptop ever sees the video.

## What it deliberately does not do

It does not care *what* moved. A person, a dog, a vehicle — all are worth knowing
about. What it must not do is cry wolf at wind in trees, rain, or birds. The
classifier labels movement and raises confidence; it never gates the alarm.

## Status

Working:

- **Recording core** — segmented continuous recording, a SQLite segment index,
  budget- and age-based retention, stall detection and restart, and a supervisor
  that keeps the whole thing alive across link drops, clock steps and full disks.
  Retention deletes the oldest footage rather than ever stopping the recorder.
- **The console** — a desktop application: live video rendered by VLC, camera
  steering, playback of what was recorded, settings and logs. It starts the
  streaming server and the recorder as child processes and restarts them if they
  stop; closing the window does not stop recording.

Being built now: the detection service — a detector process supervised like the
recorder, an event store for what moved, and the alarm strip that lists it in the
console. The `spike/` directory holds the throwaway tools that established how
detection should work — motion-gated crop detection, a ground-truth labeller, a
scorer, a miss classifier, a per-machine benchmark, and a camera prober for
commissioning day.

## Layout

| Path | What it is |
|---|---|
| `vmd/` | The application: settings, recording, storage, supervisor |
| `tests/` | Test suite (`uv run pytest`) |
| `scripts/` | Installers: `install.ps1`, the offline pair, and the autostart tasks |
| `vmd/desktop/` | The console: the desktop window, its tabs, and the VLC video panes |
| `mockup/` | Early visual explorations, kept for reference |
| `spike/` | Experiments and field tools. Throwaway by intent, kept for their findings |
| `docs/superpowers/` | Design specs and implementation plans |
| `PRODUCT.md`, `DESIGN.md` | Who this is for, and the visual system it is built in |

## Install

**New to this? Read [INSTALL.md](INSTALL.md) instead** — the same thing written
out click by click, with what every screen should say and what to do when it says
something else.

### Windows — double-click

Download the project, then double-click **`install.bat`**. That is the whole
procedure. It installs anything missing, builds the environment, and opens the
console when it finishes:

| | Where it lands | |
|---|---|---|
| **uv** | system-wide, and copied to `bin\uv.exe` | fetches Python itself and every Python library |
| **VLC** | system-wide | draws the live picture inside the console window |
| **Python** | `bin\python\` | inside the project, so the folder can be copied to an offline machine and still run |
| **ffmpeg** | `bin\ffmpeg.exe` | records the video. `bin\` goes on PATH, which is how the recorder finds it by name |
| **go2rtc** | `bin\go2rtc.exe` | takes the camera's RTSP once and re-serves it locally to the console |
| **yolo11n.pt** | the project root | the detector's weights. Not in git, and never downloaded at runtime — this machine has no internet |
| **VMD.exe** | the project root | built at the end — one file you double-click to start the console |

It also creates two scheduled tasks so the recorder and the console come back by
themselves after a restart — see
[Starting by itself](#starting-by-itself) below.

Anything already on the machine is left alone, so running it again is quick. The
first run downloads ffmpeg and the detector stack and takes several minutes.

If it stops at step 1, Windows is missing `winget` — install *App Installer*
from the Microsoft Store and run it again.

### Windows — by hand

```powershell
winget install --id astral-sh.uv -e
winget install --id Gyan.FFmpeg -e
winget install --id VideoLAN.VLC -e
git clone https://github.com/noamsolomon123/vmd.git
cd vmd
uv sync --extra detect
```

### macOS

```bash
brew install uv ffmpeg go2rtc
brew install --cask vlc
git clone https://github.com/noamsolomon123/vmd.git
cd vmd
uv sync --extra detect
```

### Linux (Debian / Ubuntu)

```bash
sudo apt update && sudo apt install -y ffmpeg git vlc
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/noamsolomon123/vmd.git
cd vmd
uv sync --extra detect
```

go2rtc is a single binary — download the one for your platform from
<https://github.com/AlexxIT/go2rtc/releases/latest> and drop it in `bin/`.

The detector's weights are not in git. Fetch them into the project root, which
is where `vmd/detect/classify.py` resolves them to:

```bash
curl -L -o yolo11n.pt https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt
```

Without that file, movement is still detected and recorded — it is simply never
named, and nothing is downloaded to fix it, because the machine this runs on has
no internet.

`uv sync` creates the virtual environment and installs every Python dependency at
the exact versions in `uv.lock`. You do not need to create a venv, activate
anything, or run `pip` — `uv run` uses the right environment automatically.

### Check it worked

```bash
uv run --offline --frozen --no-sync python -c "import cv2, pydantic, ultralytics; print('python deps ok')"
ffmpeg -version
uv run --offline --frozen --no-sync pytest
```

All three should succeed. `--offline --frozen --no-sync` is how the launchers
run too: a plain `uv run` re-checks the lock and syncs, and a sync on the
deployment laptop is a hang with no way out. On a connected machine you can drop
them.

If `ffmpeg` is not found, the recorder cannot record — fix that before anything
else. The console draws its live video with libVLC, so VLC must be installed
too, and on Windows it must be the 64-bit build: a 32-bit VLC cannot be loaded
by 64-bit Python, and the video pane will say so.

### Starting by itself

`install.bat` registers two logon tasks: **VMD Recorder**, which starts the
recording service directly, and **VMD Console** 45 seconds later. Two tasks
rather than one because recording is the product and the console is only the
window onto it — a console that fails to open must not be able to stop the disk
filling. The recorder writes `recorder.pid`, which is what makes the console
adopt it rather than start a second one.

It also stops the laptop sleeping, hibernating, or suspending on lid close.

A logon task still waits for somebody to sign in. `scripts\autostart-on.bat`,
run as administrator, offers automatic Windows sign-in as an explicit opt-in and
says what it costs first. `scripts\autostart-off.bat` removes all of it.

### Offline machines

The console runs on a machine with no internet and no wifi at all, so install on
a connected machine first and carry it over — with the two scripts that exist
for it, not by copying the folder in Explorer:

```
install.bat                    on the connected machine
scripts\offline-kit.bat        on the connected machine - checks and copies to USB
scripts\offline-install.bat    on the offline machine
```

Copying by hand does not work. `.venv` records the absolute path of the
interpreter it was built against, uv keeps interpreters under `%APPDATA%`, and
uv itself is not in the folder at all — so the copy stops with `No Python at
'…\python.exe'`, or with `uv is not installed`. `install.bat` avoids that by
putting the interpreter in `bin\python\` and uv in `bin\uv.exe`;
`offline-kit.bat` verifies it really did, adds the VLC installer, and copies;
`offline-install.bat` repairs the two recorded absolute paths if the folder
landed somewhere new, puts `bin\` on PATH, installs VLC, and proves the
environment runs without touching the network.

Match the operating system and CPU architecture between the two machines, or the
environment will not run.

## Running

**Double-click `VMD.exe`.** It opens the console window. `VMD.bat` does the same
thing without the executable.

```bash
uv run python -m vmd.desktop     # the console, same as VMD.exe
uv run python -m vmd.record_main # recording service
uv run pytest                    # test suite
```

Everything the operator configures is in the console's **Settings** tab: the
camera's IP address, username and password, the RTSP stream addresses, the radio's
address and credentials, and the storage budget. Press **Save**. Nobody hand-edits
a configuration file: the console writes `settings.json` beside the program so the
values survive a restart, and the recording service reads that same file.
Passwords are shown as typed rather than masked — deliberately: the laptop is
offline and single-purpose, and a password that cannot be read back is the harder
failure to recover from when the camera refuses the connection.

Nothing is preset. Field of view is unknown until commissioning and is a setting, not a
guess.

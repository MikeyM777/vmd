# VMD

A video motion detection console for a single multi-spectral PTZ camera watching a
distant perimeter. It shows live video, records continuously, raises an alarm when
something moves, and lets an operator look back through what was recorded.

The deployment it is built for: one FLIR-class thermal + visible PTZ head roughly
700 m from the area of interest, reaching the laptop over a Ubiquiti point-to-point
link more than 15 km long, at around 5 Mb/s. The laptop has no internet. The console
binds to `127.0.0.1` and shares nothing.

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
- **The console** — a local web server on `127.0.0.1` serving the interface:
  live, playback and settings, with edge-of-frame and keyboard steering. Camera
  address, credentials, stream addresses and storage budget are typed into the
  Settings tab and saved from there. Start it by double-clicking `VMD.exe`.

Not built yet: the live streaming layer, and the detection service itself. The
`spike/` directory holds the throwaway tools that established how detection should
work — motion-gated crop detection, a ground-truth labeller, a scorer, a miss
classifier, a per-machine benchmark, and a camera prober for commissioning day.

## Layout

| Path | What it is |
|---|---|
| `vmd/` | The application: settings, recording, storage, supervisor |
| `tests/` | Test suite (`uv run pytest`) |
| `vmd/webui/` | The console: local web server and the page it serves |
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

| | |
|---|---|
| **uv** | fetches Python itself and every Python library |
| **ffmpeg** | records the video |
| **go2rtc** | serves the live stream to the browser, in place of VLC |
| **VMD.exe** | built at the end — one file you double-click to start the console |

Anything already on the machine is left alone, so running it again is quick. The
first run downloads the detector stack and takes several minutes.

If it stops at step 1, Windows is missing `winget` — install *App Installer*
from the Microsoft Store and run it again.

### Windows — by hand

```powershell
winget install --id astral-sh.uv -e
winget install --id Gyan.FFmpeg -e
git clone https://github.com/noamsolomon123/vmd.git
cd vmd
uv sync --extra detect
```

### macOS

```bash
brew install uv ffmpeg go2rtc
git clone https://github.com/noamsolomon123/vmd.git
cd vmd
uv sync --extra detect
```

### Linux (Debian / Ubuntu)

```bash
sudo apt update && sudo apt install -y ffmpeg git
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/noamsolomon123/vmd.git
cd vmd
uv sync --extra detect
```

go2rtc is a single binary — download the one for your platform from
<https://github.com/AlexxIT/go2rtc/releases/latest> and drop it in `bin/`.

`uv sync` creates the virtual environment and installs every Python dependency at
the exact versions in `uv.lock`. You do not need to create a venv, activate
anything, or run `pip` — `uv run` uses the right environment automatically.

### Check it worked

```bash
uv run python -c "import cv2, pydantic, ultralytics; print('python deps ok')"
ffmpeg -version
uv run pytest
```

All three should succeed. If `ffmpeg` is not found, the recorder cannot record —
fix that before anything else.

### Offline machines

The console runs on a machine with no internet, so install on a connected machine
first and carry it over. Run `install.bat` on the connected machine, then copy the
whole project directory across — `.venv/` and `bin/` included — plus an `ffmpeg`
binary. Match the operating system and CPU architecture between the two machines,
or the environment will not run.

## Running

**Double-click `VMD.exe`.** It starts the console and opens it in your browser.
`VMD.bat` does the same thing without the executable.

```bash
uv run python -m vmd.webui       # the console, same as VMD.exe
uv run python -m vmd.record_main # recording service
uv run pytest                    # test suite
```

Everything the operator configures is in the console's **Settings** tab: camera
address, username, password, the RTSP stream addresses, the radio, and the storage
budget. Press **Save**. The console keeps them in `settings.json` beside the
program so they survive a restart — you never open that file yourself, and the
recording service reads the same one.

Nothing is preset. Field of view is unknown until commissioning and is a setting, not a
guess.

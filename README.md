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
- **Console mockup** (`mockup/console.html`) — the full interface in the chosen
  visual system: live, playback and settings, edge-of-frame and keyboard steering.
  Open it directly in a browser; it is self-contained and needs no server.

Not built yet: the live streaming layer, and the detection service itself. The
`spike/` directory holds the throwaway tools that established how detection should
work — motion-gated crop detection, a ground-truth labeller, a scorer, a miss
classifier, a per-machine benchmark, and a camera prober for commissioning day.

## Layout

| Path | What it is |
|---|---|
| `vmd/` | The application: settings, recording, storage, supervisor |
| `tests/` | Test suite (`uv run pytest`) |
| `mockup/` | Interface mockups, self-contained HTML |
| `spike/` | Experiments and field tools. Throwaway by intent, kept for their findings |
| `docs/superpowers/` | Design specs and implementation plans |
| `PRODUCT.md`, `DESIGN.md` | Who this is for, and the visual system it is built in |

## Install

Three things: Python, **uv** (which installs everything else), and **ffmpeg**
(which does the actual recording). Nothing else.

### Windows

```powershell
winget install --id Python.Python.3.12 -e
winget install --id astral-sh.uv -e
winget install --id Gyan.FFmpeg -e
```

Close and reopen the terminal so the new commands are on `PATH`, then:

```powershell
git clone https://github.com/noamsolomon123/vmd.git
cd vmd
uv sync
```

### macOS

```bash
brew install uv ffmpeg
git clone https://github.com/noamsolomon123/vmd.git
cd vmd
uv sync
```

### Linux (Debian / Ubuntu)

```bash
sudo apt update && sudo apt install -y ffmpeg git
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/noamsolomon123/vmd.git
cd vmd
uv sync
```

`uv sync` creates the virtual environment and installs every Python dependency at
the exact versions in `uv.lock`. You do not need to create a venv, activate
anything, or run `pip` — `uv run` uses the right environment automatically.

### Check it worked

```bash
uv run python -c "import cv2, pydantic; print('python deps ok')"
ffmpeg -version
uv run pytest
```

All three should succeed. If `ffmpeg` is not found, the recorder cannot record —
fix that before anything else.

### Offline machines

The console runs on a machine with no internet, so install on a connected machine
first and carry it over. `uv sync` on the connected machine fills `.venv/`; copy
the whole project directory, `.venv/` included, plus an `ffmpeg` binary. Match the
operating system and CPU architecture between the two machines, or the environment
will not run.

## Running

```bash
uv run pytest                    # test suite
uv run python -m vmd.record_main # recording service
```

The console mockup needs nothing at all — open `mockup/console.html` in a browser.

Camera address, credentials and storage budget are entered by the operator; nothing
is preset. Field of view is unknown until commissioning and is a setting, not a
guess.

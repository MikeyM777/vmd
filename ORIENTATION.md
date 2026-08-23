# What is in this folder

A map, for somebody who has to fix something and has never seen this before.
It says which file does what and which of the flows it belongs to. It does not
explain how to install anything — `INSTALL.md` and `docs/OFFLINE-SETUP.md` do
that, at length, for the person standing at the machine.

## What VMD is, in one paragraph

A console that watches one camera on a perimeter: it shows the live picture,
records everything to disk continuously, makes a sound when something moves,
and lets somebody look back through what was recorded. It runs on a Windows PC
with **no internet, ever**. That one fact shapes everything else here — the
program brings its own Python, its own `uv`, its own ffmpeg and go2rtc, and its
own copy of every library, because nothing can be downloaded at the far end.

**Recording is the product. The console is only the window onto it.** The
recorder is a separate process; closing the window does not stop it.

## The four flows

Everything in `scripts\` belongs to exactly one of these. When something is
broken, work out which flow it is first — it narrows sixteen files to about
three.

| Flow | Where it runs | Starts at |
|---|---|---|
| **1. Online install** | a PC with internet | `install.bat` |
| **2. Build the offline kit** | a PC with internet | `OfflineSetup.bat` |
| **3. Offline install** | the air-gapped PC | `offline-install.bat` |
| **4. Update by USB stick** | laptop + air-gapped PC | `VMD-Update-Stick.bat`, then the console's own Update button |

## Every launcher, and the script behind it

Each `.bat` at the root is a thin door: it finds PowerShell and runs one file
in `scripts\`. The `.bat` is never where the logic is.

| Double-click this | It runs | Flow | What it is for |
|---|---|---|---|
| `install.bat` | `scripts\install.ps1` | 1 | First install on a connected PC: fetches VLC, uv, ffmpeg, go2rtc, builds `.venv`, builds `VMD.exe`, sets up autostart. |
| `OfflineSetup.bat` | `scripts\offline_setup.ps1` | 2 | The whole kit in one step: runs the install, checks it is self-contained, copies it, and zips it. What comes out goes on a USB stick. |
| `offline-kit.bat` | `scripts\offline_kit.ps1` | 2 | The copying half on its own. Knows both lists: what must travel, and what must **never** leave (recordings, settings, passwords, frame grabs). |
| `offline-install.bat` | `scripts\offline_install.ps1` | 3 | Install on the air-gapped PC. Repairs the two absolute paths a copied environment cannot carry, installs VLC from the bundle, proves the environment runs without touching the network. |
| `cameras.bat` | `scripts\cameras.ps1` | 3 | One console per camera. Writes `cameras\<name>\settings.json`, a launcher, and a desktop shortcut. Run once per camera. |
| `autostart-on.bat` / `autostart-off.bat` | `scripts\autostart.ps1` | 3 | Whether the recorder and console come back by themselves after a power cut. Scheduled tasks, or Startup-folder shortcuts when Windows refuses them. |
| `VMD-Update-Stick.bat` | `scripts\update_stick.ps1 -Gui` | 4 | The laptop end of updating. A window with two buttons — get the new version, then write it to the stick — because that laptop has one USB port and its internet is a USB stick too, so the two are never plugged in at once. |
| `VMD.bat` | — | — | Opens the console. What `VMD.exe` also does. |

Not launchers, and not part of any flow: `bench.bat` and `label.bat` are
developer tools pointing at `spike\`. They are ignored by git and do not work
on a deployed machine.

## The rest of `scripts\`

| File | What it is |
|---|---|
| `_common.ps1` | Shared by every script above: printing, the step counter, finding VLC, finding the project's own Python, repairing the venv paths, reading whether autostart is really set up. Start here when a behaviour is the same in two installers. |
| `recorder_service.ps1` | What the scheduled task runs to keep the recorder going. |
| `startup_console.ps1` | What the scheduled task runs to open the console. |
| `build_exe.ps1` | Builds `VMD.exe` with PyInstaller. Needs the network. `VMD.exe` bundles no code — it only finds the project beside it and runs it, so an update changes the program without rebuilding the exe. |
| `guide_pdf.py`, `guide_shots.py`, `make_feature_page.py`, `make_alarm_sound.py` | Authoring tools. They generate the documents and the chime under `docs\`. No operator flow ever runs them. |

## The program itself, `vmd\`

| Folder | What lives there |
|---|---|
| `desktop\` | The console window: the tabs, the live wall, the settings form, the logs table, the Update panel, and the libVLC loader. |
| `storage\` | The recorder. One ffmpeg per stream, five-minute segments, and the rules that delete the oldest footage when the disk budget is reached. |
| `detect\` | Movement detection: motion, masks, tracking, the event database, the stills. Naming *what* moved is off — see below. |
| `streaming\` | go2rtc. It opens **one** link to the camera and re-serves it locally, so the panes and the recorder do not each dial the camera. |
| `ptz\` | Steering the camera: ONVIF over raw HTTP, zoom, and the loop that lowers the picture quality when the radio link gets busy. |
| `radio\` | Reading the Ubiquiti radio the camera is on. Read-only, off the GUI thread. |
| `update\` | The USB update: what is on the stick, the SHA-256 manifest, applying it, the smoke test, and putting the old version back if it fails. Standard library only, on purpose — it has to keep working while the libraries around it are being replaced. |
| `launcher.py` | What `VMD.exe` is. Finds `uv`, then runs `python -m vmd.desktop`. |
| `selftest.py` | "Does this copy work?" One answer, one exit code. Run by the updater before it lets a new version stand, and by the offline installer. |

## Things that will surprise you

- **Nothing here uses the system Python.** Not for anything. There is an
  interpreter in `bin\python\` and a `.venv` beside it, and every command goes
  through `bin\uv.exe`. Installing Python from python.org does not help and is
  not needed.
- **`uv run --offline --frozen --no-sync`** appears everywhere. A plain
  `uv run` re-checks the lock file and reaches for the network, which on the
  air-gapped machine is a hang with no way out.
- **Naming what moved is off.** `classify_enabled()` returns `False`
  unconditionally, so every event is "something moved" and the whole
  YOLO/torch path is dead code. The weights still travel in the kit for the day
  it comes back.
- **The Playback tab is off by default**, and turning it off also turns
  recording off — they are the same switch.
- **Age-based deletion is off by default.** Only the disk budget deletes
  anything.
- **`VERSION` is one integer.** "Is the stick newer?" is `8 > 7`. That is the
  whole versioning system, and it is enough.
- **`docs\review\` is a dated snapshot from the first deployment**, not current
  status. Believe the code over those documents.
- **`README.md` is deliberately empty.** Nothing should be added to it.

## Where the answers are when something is wrong

- The installers write a transcript. `offline-install.log` in the project
  folder is the whole story of an install and is what to send.
- The console's **Logs** tab is the only diagnostic surface on the air-gapped
  machine. It holds the last 500 lines and strips passwords out of them.
- `bin\logs\` holds the recorder's and the updater's own files.

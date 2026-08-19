# Putting VMD on the offline computer, step by step

The computer that runs VMD has no internet and never will. Everything it needs
has to be built on a computer that does have one, carried across on a USB
stick, and installed there.

There is one file that does the whole first half and one that does the whole
second half. **Read Part 1 and Part 2 and nothing else** — that is the normal
way, and it takes about twenty minutes of which fifteen are waiting.

Part 3 is what to do if either of those files does not work. It is the same
steps, done by hand, one at a time. Nothing in Part 3 is needed unless
something has gone wrong.

---

## What you need

- A Windows computer **with internet**. This is where the copy is built. It is
  not the computer that will run VMD.
- A USB stick with **at least 8 GB free**.
- The offline Windows computer, with **at least 20 GB free** on its C: drive.
- Both computers 64-bit. Every Windows computer sold in the last ten years is.

You do **not** need to be an administrator on the computer with internet. You
**do** need to be able to click "Yes" once on the offline computer, when it
asks permission to install VLC.

---

## Part 1 — On the computer that has internet

### 1. Get the VMD folder onto it

Either clone it with git, or download it as a ZIP from GitHub and unzip it.
Put it somewhere with a short path — `C:\VMD` is what everything here assumes.

If you downloaded a ZIP, Windows marks every file in it as "came from the
internet" and then refuses to run the scripts. Fix that once, before anything
else: right-click the ZIP → **Properties** → tick **Unblock** → **OK**, and
unzip it again.

### 2. Double-click `OfflineSetup.bat`

That is the whole of Part 1. It:

1. downloads everything VMD needs — Python, uv, ffmpeg, go2rtc, the detector's
   weights, the VLC installer — into the folder,
2. builds the Python environment and `VMD.exe`,
3. checks that what it has built will actually run on a **different** machine,
   which is the step that catches the mistake everybody makes,
4. copies it all into `VMD-offline\VMD\` on your desktop,
5. writes `START HERE.txt` beside it,
6. zips the lot into `VMD-offline.zip` on your desktop.

It prints **READY** and the name of the file when it is done. It is safe to run
again — it does not start from the beginning.

### 3. Copy `VMD-offline.zip` to the USB stick

One file, not a folder of two hundred thousand small files. That is deliberate:
a folder copy that stops halfway looks finished, passes every check on the other
machine, and fails on an import three days later.

---

## Part 2 — On the computer with no internet

### 1. Copy the zip off the USB stick and unzip it

Right-click → **Extract All**. Anywhere will do; the desktop is fine.

### 2. Copy the `VMD` folder to `C:\VMD`

The whole folder. It is a few gigabytes and takes a few minutes.

It does not have to be `C:\VMD` — but everything written down anywhere says
`C:\VMD`, and this is a machine somebody else will one day have to fix.

### 3. Open `C:\VMD` and double-click `offline-install.bat`

**Not `install.bat`.** That one downloads things, and this machine has no
internet: it will sit waiting for a connection that is not coming.

Windows asks for permission once, to install VLC. Click **Yes**.

It may ask a **second** time, near the end, to create the two scheduled tasks
that bring the system back after a restart. Some machines refuse those to an
ordinary account and some do not. Click **Yes** if it asks. Saying no is not
fatal: shortcuts in the Startup folder are used instead, and the summary says
so — they start the same two things at sign-in, but do not restart them after
a crash.

It prints three lists at the end:

| List | What to do |
|---|---|
| **INSTALLED AND WORKING** | Nothing. |
| **MISSING, BUT THE SYSTEM STILL DOES ITS JOB** | Read it. It usually means no live picture, which is worth fixing but is not urgent. |
| **BROKEN — MUST BE FIXED** | Fix these before the system is relied on. Each one says how. |

Everything it printed is also saved in `C:\VMD\bin\logs\offline-install.log`,
with passwords taken out. If something looks wrong, that file is the whole
story.

### 4. Double-click `cameras.bat`, once for each camera

It asks three questions:

- **Camera address** — `192.168.1.250`, for example.
- **What it watches** — in Hebrew. `ירושלים`. This is written above the
  pictures and on the window, so you can see at a glance which console is
  which.
- **Screen number** — 1 or 2, on a desk with a screen per camera.

It puts a shortcut on the desktop named after what that camera watches.

Run it again for the second camera.

### 5. Fill in the Settings tab, once per camera

Open a camera's shortcut, go to **Settings**, and type:

- the camera's **username** and **password**,
- the **address of each picture** the camera shows (the thermal one and the
  visible one).

Press **Save**. Recording starts as soon as that is saved.

Passwords are shown, never hidden. This machine is offline and physically
controlled, and the failure this form actually suffers is a typo nobody can
see.

### 6. Double-click `autostart-on.bat`

This is what makes the system come back by itself after a power cut. It finds
every camera set up in step 4 and makes a pair of scheduled tasks for each.

If Windows answers **Access is denied** — which it does on some machines for an
account that is not an administrator — it asks for permission and tries again,
and if that is refused it puts shortcuts in the Startup folder instead. All
three outcomes are printed. To get the scheduled tasks on a machine that
refused them, right-click `autostart-on.bat` and choose **Run as
administrator**.

Windows still needs somebody to sign in after a restart. To have it sign in by
itself as well, read the top of `scripts\autostart.ps1` first — it costs
something real — and then run `autostart-on.bat -EnableAutoLogon`.

---

## Part 3 — Doing it by hand, if the files above do not work

Every step here is one of the steps `OfflineSetup.bat` and
`offline-install.bat` do for you. Do them in order. You only need the ones
after whatever failed.

### On the computer with internet

**3.1 Install uv** (the thing that installs Python packages)

Open PowerShell and run:

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**3.2 Put a Python interpreter inside the folder**

This is the step that everything else depends on and the one that is easiest to
get wrong. The environment must be built against an interpreter that lives
**inside the VMD folder**, or it will point at nothing on the other machine.

```powershell
cd C:\VMD
uv python install 3.12 --install-dir bin\python
uv venv --python bin\python
uv sync --frozen
```

**3.3 Copy uv itself into the folder**

The offline machine has no uv and no way to get one.

```powershell
copy (Get-Command uv).Source C:\VMD\bin\uv.exe
```

**3.4 Download the four things that are not Python packages**

Save each one exactly where it says:

| What | Where to get it | Where it goes |
|---|---|---|
| ffmpeg | <https://www.gyan.dev/ffmpeg/builds/> — "release essentials" zip | `C:\VMD\bin\ffmpeg.exe` (out of the zip's `bin\` folder) |
| go2rtc | <https://github.com/AlexxIT/go2rtc/releases/latest> — `go2rtc_win64.zip` | `C:\VMD\bin\go2rtc.exe` |
| detector weights | <https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt> | `C:\VMD\yolo11n.pt` |
| VLC installer | <https://www.videolan.org/vlc/> — the **64-bit** Windows installer | `C:\VMD\bin\vendor\vlc-win64.exe` |

The VLC one has to be the 64-bit build. A 32-bit VLC installs perfectly, looks
completely normal, and the console is blind for ever: 64-bit Python cannot load
a 32-bit libVLC.

**3.5 Build `VMD.exe`** (optional)

```powershell
cd C:\VMD
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
```

If this fails, skip it. `VMD.bat` does exactly the same thing.

**3.6 Copy the folder to the USB stick**

Everything except: `recordings`, `footage`, `clips`, `.git`, `build`,
`settings.json`, `go2rtc.json`, `streaming.json`, `detection.json`, any `.db`
or `.pid` file, and any picture or video in the top folder.

None of that is needed, and most of it is either about the machine you are
standing at or about the perimeter this system watches.

Zip the folder before copying it. A folder copy that stops halfway is the
failure this whole document is trying to avoid.

### On the computer with no internet

**3.7 Copy the folder to `C:\VMD`**

**3.8 Repair the two paths a copied environment cannot carry**

The environment records, in two files, the absolute path of the machine it was
built on. If the folder landed at the same path it was built at — `C:\VMD` on
both — there is nothing to do here.

Otherwise, open these two files in Notepad and correct the paths in them:

- `C:\VMD\.venv\pyvenv.cfg` — the `home = ...` line must point at
  `C:\VMD\bin\python\...`
- `C:\VMD\.venv\Lib\site-packages\_editable_impl_vmd.pth` — must point at
  `C:\VMD`

**3.9 Put `bin\` on PATH**

```powershell
[Environment]::SetEnvironmentVariable('Path',
    "C:\VMD\bin;" + [Environment]::GetEnvironmentVariable('Path', 'User'), 'User')
```

Then sign out and back in, or restart. Windows does not tell running programs
that PATH changed.

**3.10 Install VLC**

Double-click `C:\VMD\bin\vendor\vlc-win64.exe` and click through it. Accept
every default.

**3.11 Check that it runs**

```powershell
cd C:\VMD
bin\uv.exe run --offline --frozen --no-sync python -c "import cv2, pydantic; print('ok')"
```

`ok` means the environment works. `No Python at '...'` means step 3.2 or step
3.8 is wrong — the interpreter is not inside the folder, or the path was not
corrected.

**3.12 Start it**

```powershell
cd C:\VMD
VMD.bat
```

Then Part 2 steps 4, 5 and 6 above — the cameras, the settings, and the
automatic start.

---

## If something still does not work

| What you see | What it means |
|---|---|
| `No Python at 'C:\Users\...'` | The environment was built against an interpreter outside the folder. Step 3.2, on the machine with internet, and copy it again. |
| `uv is not installed` | `bin\uv.exe` did not travel, or `bin\` is not on PATH. Steps 3.3 and 3.9. |
| The console opens but every picture is black | VLC is missing, or it is the 32-bit one. Step 3.10, with the **64-bit** installer. Recording is not affected. |
| The console opens and says "NOT recording" | ffmpeg is missing, or the camera address in Settings is wrong. The Logs tab says which. |
| Nothing starts after a restart | `autostart-on.bat`, and read the note about signing in. |
| It says "settings file could not be read" | The console opens with the standard settings anyway. Correct them in the Settings tab and press Save; that replaces the broken file. |

Everything both installers print is saved in `C:\VMD\bin\logs\`. Those files
have passwords taken out of them and are meant to be sent to somebody who can
read them.

---

## Related

- `INSTALL.md` — the full installation document, including the online case and
  what everything on this machine is for.
- `README.md` — what VMD is and how it is put together.

# Installing VMD, step by step

This is the long version, written so that nothing is assumed. If you have done
this sort of thing before, the short version in [README.md](README.md) is enough.

Every step says what to click, what you should see, and what to do if you see
something else instead.

---

## Before you start

**You need:**

- A computer running **Windows 10 or Windows 11**.
- **About 6 GB of free space** on the C: drive. Most of that is the detector
  (the AI part), which is large.
- **An internet connection**, for the installation only. The finished system is
  entirely offline: the laptop it runs on has no wifi and no internet, nothing it
  records is uploaded or shared anywhere, and nothing outside that one laptop can
  see the video — see [Installing on a machine with no
  internet](#installing-on-a-machine-with-no-internet) below.
- **The Windows password of an administrator account.** Windows will ask for
  permission once or twice while installing. If you normally use this computer
  and can install programs on it, you already have this.

**How long it takes:** 5 to 20 minutes, depending on your internet speed. Most of
that is one large download that runs on its own — you do not have to watch it.

**You do not need to install Python.** The installer brings its own. If you
already have Python, it is not touched or changed.

---

## Part 1 — Get the files onto your computer

Pick **one** of the two ways below. Way A is simpler. Way B is better if you want
to receive updates later.

### Way A — Download a ZIP (simplest)

1. Open this page in your browser:
   **https://github.com/noamsolomon123/vmd**

2. Find the green button near the top right that says **`<> Code`**. Click it.

3. A small menu opens. Click **Download ZIP** at the bottom of that menu.

4. The file **`vmd-master.zip`** downloads. It usually lands in your **Downloads**
   folder. Your browser may show it at the bottom of the window or at the top
   right — either way, it is in Downloads.

5. Open the **Downloads** folder (press `Windows key + E` to open File Explorer,
   then click **Downloads** in the left-hand list).

6. **Right-click** `vmd-master.zip` and choose **Extract All…**

7. A window appears asking where to put the files. Delete whatever is in the box
   and type exactly:

   ```
   C:\VMD
   ```

   Then click **Extract**.

   > Any folder works, but `C:\VMD` is short and has no spaces in it, which
   > avoids a whole category of problems later. Do not put it in OneDrive,
   > Desktop, or Documents if you can avoid it — OneDrive in particular syncs
   > files while the program is trying to use them.

8. When it finishes, a folder opens. **You may see one folder inside called
   `vmd-master`.** If you do, open it — the real files are in there. You are in
   the right place when you can see files named `install.bat`, `README.md` and
   `pyproject.toml` side by side.

### Way B — Use git (if you have it)

1. Press the **Windows key**, type `powershell`, and press **Enter**.

2. Type these three lines, pressing **Enter** after each:

   ```powershell
   cd C:\
   git clone https://github.com/noamsolomon123/vmd.git VMD
   cd C:\VMD
   ```

   If it says `git is not recognized`, you do not have git. Use Way A instead.

---

## Part 2 — Unblock the installer

Windows marks every file that came from the internet as untrusted, and will
refuse to run it properly until you say otherwise. This takes ten seconds.

1. In the folder, **right-click** on **`install.bat`**.

2. Click **Properties** (at the bottom of the menu).

3. Look at the **bottom** of the window that opens. If you see a line that says
   *"This file came from another computer and might be blocked…"* with a checkbox
   next to the word **Unblock**, **tick that checkbox**.

4. Click **OK**.

**If there is no Unblock checkbox, that is fine** — it means Windows did not
block the file. Close the window and continue.

---

## Part 3 — Run the installer

1. **Double-click `install.bat`.**

   > If you see `install` instead of `install.bat`, that is the same file —
   > Windows is hiding the file type. Look for the icon with the gear on it.

2. **A black window opens.** This is normal. This window is the installer talking
   to you. Do not close it.

3. **You may see a blue box saying "Windows protected your PC".** This is
   SmartScreen, and it appears for any file it has not seen before. To continue:

   - Click the small text **More info**.
   - Then click the button **Run anyway**.

4. **Windows may ask "Do you want to allow this app to make changes to your
   device?"** — click **Yes**. This is Windows installing the components, and it
   can appear more than once.

5. **Now wait and watch.** The window prints eight steps. Here is what each one is
   doing and what "good" looks like:

   | Step | What it is doing | You should see |
   |---|---|---|
   | `[1/8]` | Checking that Windows can install software | `winget is available.` |
   | `[2/8]` | Installing **uv** — the thing that brings Python | `uv is already installed.` or `uv installed.` |
   | `[3/8]` | Installing **ffmpeg** — the thing that records video | `ffmpeg is already installed.` or `ffmpeg installed.` |
   | `[4/8]` | Installing **VLC** — the thing that draws the live picture in the console | `VLC is already installed.` or `VLC installed.` |
   | `[5/8]` | Downloading **go2rtc** — it takes the camera's video once and passes it to the console | `go2rtc installed to bin\go2rtc.exe` |
   | `[6/8]` | Downloading Python and all the libraries | A long list of lines starting with `+`, then `Environment ready.` |
   | `[7/8]` | Building `VMD.exe`, the file you double-click from now on | `Built VMD.exe (6.7 MB)` |
   | `[8/8]` | Starting the console | `Installed.`, then the console window opens |

   **Step 6 is the long one.** It downloads roughly 3 GB. The screen may look
   frozen for minutes at a time. It is not frozen. Leave it alone.

6. **When it is done** you will see the word **`Installed.`** in green and the
   console itself opens — its own window, a dark screen with two video panels and
   a column of readings on the right. There is no web page and no address to
   type anywhere.

7. **Leave the black window open while the console is open.** The console runs
   from it; closing the black window closes the console. Closing the console does
   **not** stop the recording — the recorder is a separate program that keeps
   filling the disk either way.

---

## Starting it again, any time after that

**Double-click `VMD.exe`** in the `C:\VMD` folder. That is all. The console
window opens. There is no web page and no address to type.

A small black window appears beside it. That is normal — leave it open while you
use the console. Closing it closes the console.

You do not run `install.bat` again unless something is broken.

---

## Part 4 — Check that it really worked

Optional, but it takes thirty seconds and tells you for certain.

1. Open the `C:\VMD` folder in File Explorer.

2. Click once on the **address bar** at the top (the strip showing `C:\VMD`).
   The text becomes selected and editable.

3. Type `powershell` over it and press **Enter**. A blue window opens, already
   pointed at the right folder.

4. Type this and press **Enter**:

   ```powershell
   uv run pytest
   ```

   After a moment you should see a row of dots and a line ending in `passed`.
   Dots are good. Letters like `F` or `E` mean something is wrong — see the
   table below.

5. Type this and press **Enter**:

   ```powershell
   ffmpeg -version
   ```

   You should see several lines of version information. `not recognized` means
   ffmpeg did not install — see the table below.

6. Type this and press **Enter**, all on one line:

   ```powershell
   uv run python -c "import vlc; vlc.Instance(); print('vlc ok')"
   ```

   `vlc ok` on the last line means the console will be able to draw the live
   picture. VLC often prints a few lines about a `stale plugins cache` first —
   ignore those, they are harmless. No `vlc ok` at all means VLC is missing, or
   is the 32-bit one — see the table below.

---

## If something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| The black window flashes and vanishes instantly | The file was run in a way that closes on its own | Do not run it from inside the ZIP. Extract it first (Part 1, step 6) and double-click the extracted copy |
| `winget is not available on this machine.` | Windows is missing its package installer | Open the Microsoft Store, search for **App Installer**, install it, restart the computer, run `install.bat` again |
| `Could not download go2rtc` | The download was blocked or the connection dropped | Everything else still installed. Download the file yourself from https://github.com/AlexxIT/go2rtc/releases/latest — take `go2rtc_win64.zip`, open it, and drag `go2rtc.exe` into the `bin` folder inside `C:\VMD` |
| `WARNING - ffmpeg is not installed` | The recorder cannot record without it | Open PowerShell and run `winget install --id Gyan.FFmpeg -e`, then close PowerShell, reopen it, and run `install.bat` again |
| `WARNING - VLC is not installed` | The console opens but shows no live picture | Install VLC from https://www.videolan.org/vlc/ — take the **64-bit Windows installer**, click through it, then run `install.bat` again |
| A video panel says `No video here:` | The console could not find VLC, or found a 32-bit one | Install the **64-bit** VLC from https://www.videolan.org/vlc/ and start the console again. Everything except the picture keeps working meanwhile |
| `uv sync failed` | The big download was interrupted | Check your internet and run `install.bat` again. It continues from where it stopped |
| Antivirus blocks or deletes something | Some antivirus tools dislike newly downloaded `.exe` files | Allow the `C:\VMD` folder in your antivirus, then run `install.bat` again |
| `Access is denied` | The folder is protected | Move the whole `VMD` folder to `C:\VMD` and try again. Avoid `C:\Program Files` |
| The console window does not open at the end | Only the last step failed; everything else is installed | Double-click `VMD.exe` in `C:\VMD` yourself |
| You cannot see the console after starting it | Its window opened behind the others | Click its icon in the taskbar, or hold `Alt` and press `Tab` |
| `Could not build VMD.exe` | Only the convenience launcher failed | Everything works — double-click `VMD.bat` instead |
| It asks about Python or opens the Microsoft Store | Windows is offering its own Python | Close that window. You do not need it. `uv` installs the Python this project uses |

**Running `install.bat` again is always safe.** It skips whatever is already
done. If you are stuck, that is the first thing to try.

---

## What ended up on your computer

| Thing | Where | What it does |
|---|---|---|
| **uv** | Installed by Windows, system-wide | Manages Python and the libraries |
| **ffmpeg** | Installed by Windows, system-wide | Records the video to disk |
| **VLC** | Installed by Windows, system-wide | Draws the live picture inside the console window |
| **go2rtc** | `C:\VMD\bin\go2rtc.exe` | Takes the camera's video once and passes it to the console |
| **Python + libraries** | `C:\VMD\.venv\` | Everything the program itself runs on |
| **The project** | `C:\VMD\` | The code, the console, the documents |

Nothing was placed anywhere else, and nothing starts by itself when Windows
starts. The one thing that does run in the background is the recorder, and only
once you have opened the console: the console starts it, and it deliberately
keeps running after the console is closed so that closing a window never stops
the recording. Deleting the `C:\VMD` folder removes the project completely.

To remove the system-wide tools as well:

```powershell
winget uninstall --id astral-sh.uv -e
winget uninstall --id Gyan.FFmpeg -e
winget uninstall --id VideoLAN.VLC -e
```

---

## Using it after installing

Open PowerShell in `C:\VMD` (Part 4, steps 1–3), then:

| To do this | Type this |
|---|---|
| Open the console | `.\VMD.exe` — or just double-click it |
| Start recording | `uv run python -m vmd.record_main` |
| Run the tests | `uv run pytest` |
| Find out what camera is on the network | `uv run python spike\probe_camera.py 192.168.1.64 --user admin --password YOURPASSWORD` |

## Entering the camera details

There is **no file to edit**. Everything goes in the console:

1. Start the console (double-click `VMD.exe`).
2. Click the **Settings** tab at the top.
3. Fill in the camera's **Address** (its IP address on the network), **Username**
   and **Password**. The password is shown as you type it and is never hidden
   behind dots. That is on purpose: this laptop is offline and does nothing else,
   and a password you cannot read back is far more trouble than one you can.
4. Under **Streams**, put in the RTSP address of each camera stream and tick
   **record** next to the ones you want recorded. If you do not know the
   addresses, the camera prober in the table above finds them for you.
5. Under **Storage**, set the folder and how many GB the recordings may use.
6. Press **Save**. It says `Saved.` in green.

Do the same for the **Radio** — its address, username and password — under its
own heading, if the link has one.

The console writes all of this into a file called `settings.json` next to the
program, so it is still there next time. **You never open that file, and nobody
edits it by hand.** It exists so the settings survive a restart, and so the
recording service — which is a separate program — reads exactly what you typed.

If you type something impossible, Save refuses and says why, in words, next to
the button. Nothing is written until it is valid.

**One honest note about what you are looking at.** The console is real: the live
panels show the camera through VLC, and steering, playback, settings and logs all
work. Recording is real too, and it keeps running whether the console window is
open or closed. The part still being built is the detection service — the thing
that decides something moved and says so.

**Nothing here goes anywhere.** The video stays on this laptop's disk. There is no
account, no upload, no cloud, and no wifi on the machine at all.

---

## Installing on a machine with no internet

The console is meant to run on a laptop with no network of any kind. You cannot
install directly on such a machine, so you build it elsewhere and carry it over.

1. On a computer **that has internet**, follow Parts 1 to 3 above.
2. Copy the **entire `C:\VMD` folder** to a USB drive. This must include the
   hidden-looking `.venv` folder and the `bin` folder — that is where Python,
   the libraries and go2rtc actually live.
3. Put two more things on the same USB drive. They are installed system-wide and
   do **not** travel inside the `C:\VMD` folder:
   - **ffmpeg** — download it from https://www.gyan.dev/ffmpeg/builds/ (take the
     *release essentials* zip). On the offline machine, unpack it and copy
     `ffmpeg.exe` into `C:\VMD\bin\`.
   - **VLC** — download the **64-bit Windows installer** from
     https://www.videolan.org/vlc/. On the offline machine, double-click it and
     click through it. Without VLC the console opens but shows no live picture.
4. Copy the folder from the USB drive to `C:\VMD` on the offline machine.
5. Open PowerShell there and run `uv run pytest`. If the tests pass, the
   transfer worked.

**Both machines must run the same version of Windows and the same kind of
processor** (both 64-bit Intel/AMD, or both ARM). Python and the libraries are
compiled for the machine they were downloaded for, and will not run on a
different kind.

---

## macOS and Linux

There is no double-click installer for these; the system is built for the
Windows laptop. The commands are in [README.md](README.md#macos).

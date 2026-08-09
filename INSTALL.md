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
- **An internet connection**, for the installation only. The system itself runs
  with no internet at all — see [Installing on a machine with no
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

5. **Now wait and watch.** The window prints six steps. Here is what each one is
   doing and what "good" looks like:

   | Step | What it is doing | You should see |
   |---|---|---|
   | `[1/6]` | Checking that Windows can install software | `winget is available.` |
   | `[2/6]` | Installing **uv** — the thing that brings Python | `uv is already installed.` or `uv installed.` |
   | `[3/6]` | Installing **ffmpeg** — the thing that records video | `ffmpeg is already installed.` or `ffmpeg installed.` |
   | `[4/6]` | Downloading **go2rtc** — the thing that shows live video, instead of VLC | `go2rtc installed to bin\go2rtc.exe` |
   | `[5/6]` | Downloading Python and all the libraries | A long list of lines starting with `+`, then `Environment ready.` |
   | `[6/6]` | Opening the console | `Installed.` and your browser opens |

   **Step 5 is the long one.** It downloads roughly 3 GB. The screen may look
   frozen for minutes at a time. It is not frozen. Leave it alone.

6. **When it is done** you will see the word **`Installed.`** in green, and your
   web browser will open showing the console — a dark screen with two video
   panels and a column of readings on the right.

7. The black window says **`Press any key to close this window.`** Press any key.
   You are finished.

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

   After a moment you should see a row of dots and `111 passed`. Dots are good.
   Letters like `F` or `E` mean something is wrong — see the table below.

5. Type this and press **Enter**:

   ```powershell
   ffmpeg -version
   ```

   You should see several lines of version information. `not recognized` means
   ffmpeg did not install — see the table below.

---

## If something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| The black window flashes and vanishes instantly | The file was run in a way that closes on its own | Do not run it from inside the ZIP. Extract it first (Part 1, step 6) and double-click the extracted copy |
| `winget is not available on this machine.` | Windows is missing its package installer | Open the Microsoft Store, search for **App Installer**, install it, restart the computer, run `install.bat` again |
| `Could not download go2rtc` | The download was blocked or the connection dropped | Everything else still installed. Download the file yourself from https://github.com/AlexxIT/go2rtc/releases/latest — take `go2rtc_win64.zip`, open it, and drag `go2rtc.exe` into the `bin` folder inside `C:\VMD` |
| `WARNING - ffmpeg is not installed` | The recorder cannot record without it | Open PowerShell and run `winget install --id Gyan.FFmpeg -e`, then close PowerShell, reopen it, and run `install.bat` again |
| `uv sync failed` | The big download was interrupted | Check your internet and run `install.bat` again. It continues from where it stopped |
| Antivirus blocks or deletes something | Some antivirus tools dislike newly downloaded `.exe` files | Allow the `C:\VMD` folder in your antivirus, then run `install.bat` again |
| `Access is denied` | The folder is protected | Move the whole `VMD` folder to `C:\VMD` and try again. Avoid `C:\Program Files` |
| The browser does not open at the end | Only the last step failed; everything is installed | Open `C:\VMD\mockup\console.html` by double-clicking it |
| It asks about Python or opens the Microsoft Store | Windows is offering its own Python | Close that window. You do not need it. `uv` installs the Python this project uses |

**Running `install.bat` again is always safe.** It skips whatever is already
done. If you are stuck, that is the first thing to try.

---

## What ended up on your computer

| Thing | Where | What it does |
|---|---|---|
| **uv** | Installed by Windows, system-wide | Manages Python and the libraries |
| **ffmpeg** | Installed by Windows, system-wide | Records the video to disk |
| **go2rtc** | `C:\VMD\bin\go2rtc.exe` | Will serve the live video to the browser, replacing VLC |
| **Python + libraries** | `C:\VMD\.venv\` | Everything the program itself runs on |
| **The project** | `C:\VMD\` | The code, the console, the documents |

Nothing was placed anywhere else, and nothing runs at startup or in the
background. Deleting the `C:\VMD` folder removes the project completely.

To remove the two system-wide tools as well:

```powershell
winget uninstall --id astral-sh.uv -e
winget uninstall --id Gyan.FFmpeg -e
```

---

## Using it after installing

Open PowerShell in `C:\VMD` (Part 4, steps 1–3), then:

| To do this | Type this |
|---|---|
| Open the console again | `start mockup\console.html` |
| Start recording | `uv run python -m vmd.record_main` |
| Run the tests | `uv run pytest` |
| Find out what camera is on the network | `uv run python spike\probe_camera.py 192.168.1.64 --user admin --password YOURPASSWORD` |

You can also just double-click `install.bat` again — it finishes in seconds and
opens the console for you.

**One honest note about what you are looking at.** The console shows the real
interface, but the live video layer is not built yet, so the picture in it is
drawn by the page rather than streamed from a camera. Steering, layout, playback
and settings all behave for real. The recording service is real and works. The
live stream is the next thing to be built.

---

## Installing on a machine with no internet

The console is meant to run on a laptop with no network of any kind. You cannot
install directly on such a machine, so you build it elsewhere and carry it over.

1. On a computer **that has internet**, follow Parts 1 to 3 above.
2. Copy the **entire `C:\VMD` folder** to a USB drive. This must include the
   hidden-looking `.venv` folder and the `bin` folder — that is where Python,
   the libraries and go2rtc actually live.
3. On the offline machine, also install **ffmpeg**. Download it beforehand from
   https://www.gyan.dev/ffmpeg/builds/ (take the *release essentials* zip),
   unpack it, and copy `ffmpeg.exe` into `C:\VMD\bin\`.
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

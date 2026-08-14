# Installing VMD, step by step

This is the long version, written so that nothing is assumed. If you have done
this sort of thing before, the short version in [README.md](README.md) is enough.

Every step says what to click, what you should see, and what to do if you see
something else instead.

**If the machine this has to run on has no internet**, do not start here. Go to
[Installing on a machine with no internet](#installing-on-a-machine-with-no-internet)
and read it first: you install on a connected machine, prepare a copy, and
carry it over. The steps below are the first half of that.

---

## Before you start

**You need:**

- A computer running **Windows 10 or Windows 11**.
- **About 10 GB of free space** on the C: drive. The finished folder is about
  2 GB. The rest is a download cache that `uv` keeps outside the folder and
  reuses — see [What ended up on your computer](#what-ended-up-on-your-computer),
  which says exactly where it is and how to empty it.
- **An internet connection**, for the installation only. The finished system is
  entirely offline: the machine it runs on has no wifi and no internet, nothing it
  records is uploaded or shared anywhere, and nothing outside that one machine can
  see the video.
- **The Windows password of an administrator account.** Windows asks for
  permission once. If you normally use this computer and can install programs on
  it, you already have this.

**How long it takes:** 10 to 30 minutes, depending on your internet speed. Most
of that is two large downloads that run on their own — you do not have to watch
them.

**You do not need to install Python.** The installer brings its own, and keeps
it inside the project folder. If you already have Python, it is not touched or
changed.

---

## Part 1 — Get the files onto your computer

Pick **one** of the two ways below. Way A is simpler. Way B is better if you want
to receive updates later.

### Way A — Download a ZIP (simplest)

1. Open this page in your browser:
   **https://github.com/noamsolomon123/vmd**

2. Above the file list there is a button that says **`desktop-console`** with a
   branch icon. If it says something else — `main`, or `master` — click it and
   choose **desktop-console** from the list. That is the version this document
   describes.

3. Find the green button near the top right that says **`<> Code`**. Click it.

4. A small menu opens. Click **Download ZIP** at the bottom of that menu.

5. A file whose name starts with **`vmd-`** downloads — `vmd-desktop-console.zip`
   if you followed step 2. It usually lands in your **Downloads** folder. Your
   browser may show it at the bottom of the window or at the top right — either
   way, it is in Downloads.

6. Open the **Downloads** folder (press `Windows key + E` to open File Explorer,
   then click **Downloads** in the left-hand list).

7. **Right-click** the `vmd-…zip` file and choose **Extract All…**

8. A window appears asking where to put the files. Delete whatever is in the box
   and type exactly:

   ```
   C:\VMD
   ```

   Then click **Extract**.

   > Any folder works, but `C:\VMD` is short and has no spaces in it, which
   > avoids a whole category of problems later. Do not put it in OneDrive,
   > Desktop, or Documents if you can avoid it — OneDrive in particular syncs
   > files while the program is trying to use them.

9. When it finishes, a folder opens. **You may see one folder inside** with a
   name like `vmd-desktop-console`. If you do, open it — the real files are in
   there. You are in the right place when you can see files named `install.bat`,
   `README.md` and `pyproject.toml` side by side.

### Way B — Use git (if you have it)

1. Press the **Windows key**, type `powershell`, and press **Enter**.

2. Type these three lines, pressing **Enter** after each:

   ```powershell
   cd C:\
   git clone --branch desktop-console https://github.com/noamsolomon123/vmd.git VMD
   cd C:\VMD
   ```

   If it says `git is not recognized`, you do not have git. Use Way A instead.

---

## Part 2 — Unblock the installer

Windows marks every file that came from the internet as untrusted, and will
refuse to run it properly until you say otherwise. This takes ten seconds.

> **Quicker, if you have not extracted the ZIP yet:** right-click the
> `vmd-…zip` file itself, choose **Properties**, tick **Unblock** at the bottom,
> click **OK**, and *then* extract it. Doing it on the ZIP unblocks every file
> inside it at once, including the other `.bat` files in the folder that you
> will need later. If you have already extracted, do the steps below instead.

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

4. **A second black window opens and Windows asks "Do you want to allow this app
   to make changes to your device?"** — click **Yes**.

   That second window installs the two things that have to go on the computer
   itself rather than into the folder: **uv** and **VLC**. It closes by itself
   after a minute or two, and the first window carries on. Do not close either
   of them.

   > If you click **No**, nothing breaks immediately — Windows will just ask
   > again, once per item. If you click No to all of them, the installer says
   > plainly what is missing at the end.

5. **Now wait and watch.** The first window prints twelve steps. Here is what
   each one is doing and what "good" looks like:

   | Step | What it is doing | You should see |
   |---|---|---|
   | `[1/12]` | Checking that Windows can install software | `winget is available.` |
   | `[2/12]` | Installing **uv** — the thing that brings Python | `uv is already installed.` or `uv installed.` |
   | `[3/12]` | Finding or installing the **64-bit VLC** — the thing that draws the live picture | `VLC is already here:` and a folder, or `VLC is here now:` followed by `Checked: 64-bit libvlc.dll, with its plugins folder beside it.` |
   | `[4/12]` | Downloading **ffmpeg** — the thing that records video — into `bin\` | `ffmpeg installed to bin\ffmpeg.exe` |
   | `[5/12]` | Downloading **go2rtc** — it takes the camera's video once and passes it to the console | `go2rtc installed to bin\go2rtc.exe` |
   | `[6/12]` | Downloading the **detector's weights** — the file that lets it name what moved | `yolo11n.pt downloaded.` |
   | `[7/12]` | Putting a copy of **uv** inside the folder, and the folder's `bin\` on your PATH | `uv copied to bin\uv.exe` |
   | `[8/12]` | Installing **Python itself**, inside the folder | `Python installed into bin\python\` |
   | `[9/12]` | Downloading every library the program uses, then asking Python whether it can draw the live picture | A long list of lines starting with `+`, then `Environment ready.` and `Yes - Python loaded libVLC.` |
   | `[10/12]` | Building `VMD.exe`, the file you double-click from now on | `Built VMD.exe (6.7 MB)` |
   | `[11/12]` | Making the system come back by itself after a restart | `Both scheduled tasks are registered.` |
   | `[12/12]` | Adding it all up, then starting the console | The three lists described below, then the console window opens |

   **Steps 4 and 9 are the long ones.** Step 4 downloads about 170 MB and step 9
   about 1.5 GB. The screen may look frozen for minutes at a time. It is not
   frozen. Leave it alone.

   > **Step 3 does not decide anything about VLC, and says so.** It can only
   > look at the disk, and looking at the disk is not the same question as "can
   > this program use it". Step 9 asks Python directly, once Python exists, and
   > that answer is the one that counts. If step 3 says something cautious and
   > step 9 says `Yes`, everything is fine.

6. **When it is done** the installer prints three lists. Read them; they are
   the whole result, and they are deliberately not the same colour.

   | The list | What it means | What to do |
   |---|---|---|
   | **INSTALLED AND WORKING** (green) | Checked, and working | Nothing |
   | **MISSING, BUT THE SYSTEM STILL DOES ITS JOB WITHOUT IT** (yellow) | The system records, detects and can be used. Something optional is absent — most often the live picture | Read it. Fix it when convenient. It does not stop you using the system today |
   | **BROKEN — MUST BE FIXED BEFORE THE SYSTEM IS USED** (red) | Do not rely on this system yet | Do exactly what the lines underneath say, then run `install.bat` again |

   When everything went well the third list says **`Nothing. This system can be
   used.`** in green. That sentence, not the absence of yellow, is the one to
   look for.

   Then the console itself opens — its own window, a dark screen with two video
   panels and a column of readings on the right. There is no web page and no
   address to type anywhere.

   **The first time the console opens it can take fifteen seconds**, with
   nothing on the screen while you wait. That is VLC building an index of its
   own parts. The installer tries to do that for you in step 3, so usually you
   will not see it — but if you do, it is not stuck. Every start after the first
   takes about five seconds.

   > **Every line the installer printed is also saved to a file:**
   >
   > ```
   > bin\logs\install.log        (inside the VMD folder, wherever you put it)
   > ```
   >
   > and, if Windows asked for permission, `install-admin.log` beside it. The
   > installer prints the full path at the end, so you never have to work it
   > out — this document says `C:\VMD` throughout, but the folder works
   > anywhere and the path it prints is the real one.
   >
   > If anything looked wrong, send those files — they are the whole story, and
   > the installer takes any passwords out of them before writing them. You do
   > not need to describe what you saw; the file already has it.

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

4. Type this and press **Enter**, all on one line:

   ```powershell
   uv run --offline --frozen --no-sync pytest
   ```

   After a moment you should see a row of dots and a line ending in `passed`.
   Dots are good. Letters like `F` or `E` mean something is wrong — see the
   table below.

   > The three words after `run` mean "do not go to the internet for this".
   > They are how the console starts too, and they are the difference between a
   > command that works on the offline machine and one that hangs there.

5. Type this and press **Enter**:

   ```powershell
   .\bin\ffmpeg.exe -version
   ```

   You should see several lines of version information. `not recognized` or
   `cannot find the path` means ffmpeg did not install — see the table below.

6. Type this and press **Enter**, all on one line:

   ```powershell
   uv run --offline --frozen --no-sync python -c "from vmd.desktop.libvlc import prepare; print(prepare().dll)"
   ```

   A path ending in `libvlc.dll` means the console will be able to draw the live
   picture. Anything else is one sentence saying what is wrong and what to do
   about it — the same sentence the console's video pane shows.

   > **This asks the console's own loader**, which is the only thing whose
   > answer matters. The console does not let `import vlc` go looking for VLC
   > any more: it searches itself, checks that the copy it found is 64-bit and
   > has its `plugins` folder, and hands the answer over. So this command and
   > the installer's step 9 and the video pane cannot disagree with each other.
   >
   > VLC may print a long list of lines about a `stale plugins cache` first —
   > ignore those, they are harmless, and they are the reason a first start can
   > be slow.
   >
   > **Putting VLC on `PATH` does not fix anything here**, and is worth not
   > trying: since Python 3.8, Windows no longer searches `PATH` for a library's
   > own dependencies, so a VLC that is only on `PATH` still fails.

---

## Part 5 — It starts by itself now

This machine is meant to be on all the time, recording all the time. Windows
restarts anyway: an update, a power cut, somebody pressing the wrong thing. Step
11 of the installer set that up so a restart costs nothing.

**What was created.** Two entries in Windows Task Scheduler:

| Name | When | What it does |
|---|---|---|
| **VMD Recorder** | When you sign in to Windows | Starts the recording, on its own, without the console |
| **VMD Console** | 45 seconds later | Opens the console window |

They are separate on purpose. **Recording is the product; the console is only
the window onto it.** If the console ever fails to open — a broken settings
file, a VLC that will not load — the disk keeps filling anyway. The console,
when it does open, notices the recorder is already running and uses that one
rather than starting a second.

Also set, because a machine that goes to sleep records nothing:

- it never sleeps, never hibernates, and never spins its disks down
- **closing the lid does nothing**

### What a restart actually looks like

Read this before the first one, because the middle of it looks like a failure
and is not:

1. You sign in to Windows. The desktop appears. **There is no console window,
   and there will not be one for about a minute.**
2. Recording has already started, invisibly, a second or two after you signed
   in. Nothing on screen says so.
3. **About 45 seconds later the console window opens by itself.**

**Do not double-click `VMD.exe` while you are waiting.** That is the one thing
that turns a working restart into a problem: you would get a second console, and
two consoles share one settings file and one recording index. If you have already
done it, close both windows and wait — the scheduled task opens a clean one, or
double-click `VMD.exe` once after the minute has passed.

If nothing has appeared after two minutes, see
[If something goes wrong](#if-something-goes-wrong).

### Checking it without a terminal

Any time you suspect it is not working:

1. Press the **Windows key**, type `Task Scheduler`, press **Enter**.
2. In the left-hand column click **Task Scheduler Library** — the top item, not
   any of the folders under it.
3. The middle of the window lists every task on this machine, sorted by name.
   Scroll to the **V**s. You are looking for exactly two rows:

   | Name | What the **Status** column should say |
   |---|---|
   | `VMD Recorder` | **Running** — for as long as recording is happening |
   | `VMD Console` | **Running** while the console window is open; `Ready` after you close it |

   **`VMD Recorder` saying `Running` is the answer to "is it recording?"** That
   task stays alive for exactly as long as the recording does. If it says
   `Ready`, recording has stopped, and the reason is in
   `C:\VMD\bin\logs\autostart.log`.

   > If the Status column is not shown, click **View** at the top, then
   > **Refresh**. Task Scheduler does not update by itself.

4. If the two rows are not there at all, autostart was never set up or was
   removed. Double-click `autostart-on.bat` in `C:\VMD`.

There is also a plain-text record of every start: open `C:\VMD\bin\logs` and
double-click **`autostart.log`**. One line per event, most recent at the bottom.
`no settings.json yet` there means the camera details have not been entered.

**What is still missing, and it matters.** All of that happens when somebody
*signs in* to Windows. After a power cut the machine comes back on and stops at
the sign-in screen, recording nothing, until a person types the password.

To close that gap, Windows can sign itself in:

1. Open the `C:\VMD` folder.
2. **Right-click** `autostart-on.bat` and choose **Run as administrator**.
3. It explains what it costs, then asks you to type `YES` in capitals, then asks
   for the Windows password of this account.

**What it costs, plainly:** anyone who can reach the machine gets a signed-in desktop
with no password, and the password is written into the Windows registry in clear
text, where any administrator of the machine can read it. In exchange, a power
cut costs nothing at all.

For this deployment — one machine, no network of any kind, physically inside the
perimeter it is watching, doing nothing but recording — that is usually the
right trade. It is still your decision, so nothing switches it on for you.

**To turn all of this off:** open `C:\VMD` and double-click
**`autostart-off.bat`**. If you switched the automatic sign-in on, right-click it
and choose **Run as administrator** instead, so it can switch that off too and
delete the stored password. Nothing that is currently running stops, and no
recording is deleted.

**To see what is on right now:** double-click **`autostart-on.bat`**. It sets
everything up again — which is harmless, it is the same two entries — and then
prints the current state:

```
    VMD Recorder       on   (Ready)
    VMD Console        on   (Ready)
    Automatic sign-in  off  (somebody must sign in after a restart)
```

---

## If something goes wrong

**Before anything else: there is a file.** Everything the installer printed is
in `bin\logs\install.log` inside the VMD folder — `C:\VMD\bin\logs\install.log`
if you put the folder where this document says, and the installer prints the
real path at the end either way. `install-admin.log` sits beside it if Windows
asked for permission. Passwords are taken out of both before they are written.
If what you are seeing is not in the table below, send those files rather than
trying to describe what you saw.

| What you see | What it means | What to do |
|---|---|---|
| The black window flashes and vanishes instantly | The file was run in a way that closes on its own | Do not run it from inside the ZIP. Extract it first (Part 1, step 8) and double-click the extracted copy |
| `winget is not available on this account.` | Windows is missing its package installer | Only **uv** and **VLC** need it; everything else still installs. Open the Microsoft Store, search for **App Installer**, install it, then run `install.bat` again |
| `Could not download go2rtc` | The download was blocked or the connection dropped | Everything else still installed. Download the file yourself from https://github.com/AlexxIT/go2rtc/releases/latest — take `go2rtc_win64.zip`, open it, and drag `go2rtc.exe` into the `bin` folder inside `C:\VMD` |
| `ffmpeg is missing, so nothing can be recorded` (in the red list) | The recorder cannot record without it | Download the *release essentials* zip from https://www.gyan.dev/ffmpeg/builds/, open it, and drag `ffmpeg.exe` into `C:\VMD\bin\`. Then run `install.bat` again |
| `yolo11n.pt is missing` (in the yellow list) | The detector's weights did not download | Detection still works; it just cannot say *what* moved. Download it from https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt and save it into `C:\VMD` |
| `VLC is not installed, so the console shows no live picture` (in the yellow list) | Python looked for libVLC and did not find one | Install VLC from https://www.videolan.org/vlc/ — take the **64-bit Windows installer**, click through it, then run `install.bat` again. Recording is unaffected meanwhile |
| `winget installed the 32-BIT VLC` or `it is the 32-bit version` | The 32-bit VLC cannot be loaded by 64-bit Python, no matter where it is or how many times it is reinstalled | **This is the one that looks like "VLC is missing" while VLC is plainly in your Start menu, and re-running the installer will never fix it.** Uninstall VLC from *Add or remove programs*, download the **64-bit** Windows installer from https://www.videolan.org/vlc/ by hand, install that, run `install.bat` again |
| `is missing its plugins folder` | libVLC is there but the parts it plays video with are not. It would open a black picture and never say why | Install VLC again from https://www.videolan.org/vlc/, taking the **64-bit** Windows installer |
| A video panel says `No video here:` | The console could not find a VLC it can use | The rest of that sentence says which of the three it is and where it looked. Same fixes as the rows above; everything except the picture keeps working meanwhile |
| You are tempted to add VLC to `PATH` | It will not help | Since Python 3.8, Windows does not search `PATH` for a library's own dependencies, and VLC's installer does not put itself there either. The console finds VLC through the registry and the ordinary folders, and adds the folder it chose to the search path itself |
| `Still running from this folder: …` | The recorder or the console was running while the installer wanted to rebuild the environment | Restart the machine and run `install.bat` again before anything else has started. If you do not, step 9 may stop with `Access is denied` |
| The console takes fifteen seconds to appear, once | VLC is rebuilding its own index of parts | Nothing to do. Later starts take about five seconds. To fix it for good, run `install.bat` again — step 3 rebuilds that index while it has permission to |
| `uv sync failed` | The big download was interrupted | Check your internet and run `install.bat` again. It continues from where it stopped |
| `No Python at '…\python.exe'` | The folder was copied from another machine without being prepared | Do not copy the folder by hand. See [Installing on a machine with no internet](#installing-on-a-machine-with-no-internet) |
| `uv is not installed, so nothing can run yet` | There is no `uv.exe` in `C:\VMD\bin\` and none on this account's PATH | Sign out of Windows and back in, which is when Windows picks up a new PATH. If it persists, run `install.bat` again — on the offline machine, `offline-install.bat` |
| Antivirus blocks or deletes something | Some antivirus tools dislike newly downloaded `.exe` files | Allow the `C:\VMD` folder in your antivirus, then run `install.bat` again |
| `Access is denied` | The folder is protected | Move the whole `VMD` folder to `C:\VMD` and try again. Avoid `C:\Program Files` |
| The console window does not open at the end | Only the last step failed; everything else is installed | Double-click `VMD.exe` in `C:\VMD` yourself |
| You cannot see the console after starting it | Its window opened behind the others | Click its icon in the taskbar, or hold `Alt` and press `Tab` |
| `Could not build VMD.exe` | Only the convenience launcher failed | Everything works — double-click `VMD.bat` instead |
| Two console windows are open at once | One was left over from before, or `VMD.exe` was double-clicked during the 45 seconds after a restart | Close both, then double-click `VMD.exe` once. They share one settings file and one recording folder, and two of them fighting over it is worth avoiding |
| Nothing happens for a minute after a restart | Normal. The recorder starts at once and the console follows 45 seconds later | Wait. Do not double-click `VMD.exe` meanwhile — see [What a restart actually looks like](#what-a-restart-actually-looks-like) |
| The **Logs** tab repeats `a recorder is already running (pid 1234); leaving it alone`, **with a different number every time** | **Nothing is being recorded.** Each new recorder is standing down to something that is not a recorder, and being started again seconds later. Seen once, the line is normal — it is how a second recorder correctly declines. Seen over and over with a changing number, it is a fault | Sign out of Windows and back in. That stops everything and starts the two tasks cleanly. If it comes back, check `C:\VMD\bin\logs\autostart.log` and report it — this is a defect, not a setting |
| No console two minutes after a restart | The task did not run, or the console failed to open | Open Task Scheduler and look at the two `VMD` rows ([Checking it without a terminal](#checking-it-without-a-terminal)). If `VMD Recorder` says `Running`, recording is fine and only the window is missing — double-click `VMD.exe` |
| It asks about Python or opens the Microsoft Store | Windows is offering its own Python | Close that window. You do not need it. `uv` installs the Python this project uses |

**Running `install.bat` again is always safe.** It skips whatever is already
done. If you are stuck, that is the first thing to try.

---

## What ended up on your computer

| Thing | Where | What it does |
|---|---|---|
| **uv** | Installed by Windows, system-wide, **and** copied to `C:\VMD\bin\uv.exe` | Manages Python and the libraries. The copy in `bin\` is the one that travels to the offline machine |
| **VLC** | Installed by Windows, system-wide | Draws the live picture inside the console window |
| **ffmpeg** | `C:\VMD\bin\ffmpeg.exe` | Records the video to disk |
| **go2rtc** | `C:\VMD\bin\go2rtc.exe` | Takes the camera's video once and passes it to the console |
| **Python** | `C:\VMD\bin\python\` | Deliberately inside the folder, so the folder can be copied to a machine that has no internet and still run |
| **The libraries** | `C:\VMD\.venv\` | About 1.5 GB. Everything the program itself runs on |
| **The detector's weights** | `C:\VMD\yolo11n.pt` | What lets it say *person* rather than only *something moved* |
| **The project** | `C:\VMD\` | The code, the console, the documents |

Four things live **outside** that folder, and deleting `C:\VMD` does not remove
any of them:

| Thing | Where | How to remove it |
|---|---|---|
| **uv's download cache** | `%LOCALAPPDATA%\uv\cache` — on the machine this was written on it had grown to **21.7 GB** (it is shared with any other project that uses uv) | `uv cache clean` |
| **The PATH entry** | `C:\VMD\bin` added to your account's PATH | `autostart-off.bat` does not do this. Remove it in Windows Settings → *Edit environment variables for your account* |
| **The two scheduled tasks** | Windows Task Scheduler | Double-click `autostart-off.bat` |
| **uv and VLC themselves** | Installed system-wide | See below |

### Removing it completely

Do these in order:

```powershell
# 1. stop things starting by themselves (run as administrator if you switched
#    the automatic Windows sign-in on)
C:\VMD\autostart-off.bat

# 2. empty the download cache - this is the big one
uv cache clean

# 3. remove the system-wide tools
winget uninstall --id astral-sh.uv -e
winget uninstall --id VideoLAN.VLC -e
```

Then delete the `C:\VMD` folder, and remove `C:\VMD\bin` from your PATH in
Windows Settings → *Edit environment variables for your account*.

---

## Using it after installing

Open PowerShell in `C:\VMD` (Part 4, steps 1–3), then:

| To do this | Type this |
|---|---|
| Open the console | `.\VMD.exe` — or just double-click it |
| Start recording | `uv run --offline --frozen --no-sync python -m vmd.record_main` |
| Run the tests | `uv run --offline --frozen --no-sync pytest` |
| Find out what camera is on the network | see below — it needs the camera password, so it is two lines |

> On a machine that has internet you can leave out `--offline --frozen
> --no-sync`. On the machine that does not, leaving them out is how you get a
> command that hangs with no way out.

### Asking the camera what it is

This one needs the camera's password. **Type it in two lines, like this**, and
not as one:

```powershell
$pw = Read-Host "Camera password"
uv run --offline --frozen --no-sync python spike\probe_camera.py 192.168.1.64 --user admin --password $pw
```

The first line asks for the password and keeps it in `$pw`. The second line
passes `$pw` along — the four characters `$pw`, not the password itself.

**Why it matters, and it does.** PowerShell writes every command you type into a
file, so that pressing the up-arrow tomorrow brings back what you typed today.
The file is

```
%APPDATA%\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt
```

It is plain text, it is never cleared, and `%APPDATA%` is the one folder Windows
copies between machines when an account is managed by a company or a domain. A
password typed straight into the command would sit in that file in the clear,
forever, on a machine whose entire point is that nothing leaves it. What you type
at the `Camera password` prompt is *not* written there — only commands are.

Close the window when you are done, or type `Remove-Variable pw`. Either way the
password is gone from memory; neither of them is the part that matters.

## Entering the camera details

There is **no file to edit**. Everything goes in the console:

1. Start the console (double-click `VMD.exe`).
2. Click the **Settings** tab at the top.
3. Fill in the camera's **Address** (its IP address on the network), **Username**
   and **Password**. The password is shown as you type it and is never hidden
   behind dots. That is on purpose: this machine is offline and does nothing else,
   and a password you cannot read back is far more trouble than one you can.
4. Under **Streams**, press **Add a stream** for each camera view and put in its
   name and its RTSP address. The two views sit side by side as cards. There is
   no tick saying whether to use one: **every view on this list is used** — it
   is shown in the Live tab, it is recorded, and it is watched for movement if
   you ask for that. To stop using a view, press **Remove** on its card. If you
   do not know the addresses, the camera prober in the table above finds them
   for you.
5. Under **Storage**, set the folder, then press **Scan this PC**. It looks at
   the drive that folder is on — how big it is, how much is free, how much VMD
   is already using — and fills in a budget and an age rule that fit, with a
   report you can read line by line. Both are suggestions and both stay yours to
   change; the slider beside the budget says what the number means in days you
   can look back. Nothing is written until you press Save.
6. Press **Save**. It says `Saved.` in green.

Do the same for the **Radio** — its address, username and password — under its
own heading, if the link has one. In that same box is
**Turn the picture down by itself when the link gets busy**, which is **on**.
With a radio and a camera both configured, the console watches how busy the link
is and asks the camera for a smaller picture when it is struggling, then a
better one again once it has been quiet for a while. It never goes below the
lowest picture you allow, it never changes the resolution, and every change it
makes is written into the **Logs** tab. Each change makes the picture blip for a
moment. Untick it to leave the camera exactly as it is set; it stops within a
couple of seconds and nothing needs restarting.

If you lower the budget below what is already on the disk, Save does not write
straight away: it tells you roughly how much of the oldest footage that will
delete and asks you to press Save again. This is the only irreversible thing on
the page.

The console writes all of this into a file called `settings.json` next to the
program, so it is still there next time. **You never open that file, and nobody
edits it by hand.** It exists so the settings survive a restart, and so the
recording service — which is a separate program — reads exactly what you typed.

If you type something impossible, Save refuses and says why, in words, next to
the button. Nothing is written until it is valid.

> **Nothing records until this is filled in.** There is no camera to record
> before it, so the recorder that starts at sign-in looks at the missing
> settings file, writes one line saying so into `bin\logs\autostart.log`, and
> stops. Once you have pressed Save, the console starts recording immediately,
> and every restart after that starts it on its own.

**One honest note about what you are looking at.** All four parts are real and
running: the live panels show the camera through VLC; steering, zoom, playback,
settings and logs all work; recording keeps going whether the console window is
open or closed; and the detector is a separate program the console starts and
looks after, off per camera view until you switch it on. What none of it has met
is your camera and your radio — see [docs/FIRST-MORNING.md](docs/FIRST-MORNING.md)
for the list of things only the real hardware can answer.

**Nothing here goes anywhere.** The video stays on this machine's disk. There is no
account, no upload, no cloud, and no wifi on the machine at all. Nothing this
program runs reaches the network except the camera and the radio on the other end
of the radio link.

---

## Installing on a machine with no internet

> **The short way is one file at each end.** On the computer that has internet,
> double-click **`OfflineSetup.bat`**: it does everything in Stage 1 below and
> ends with a single zip file on your desktop. On the offline computer, unzip
> it, copy the `VMD` folder to `C:\VMD`, and double-click
> **`offline-install.bat`**.
>
> **`docs/OFFLINE-SETUP.md`** is that written out in full, including how to do
> every step by hand if either file does not work. Read that one first. The
> rest of this section is the same ground in more detail, and is what
> `OfflineSetup.bat` is doing while you wait.

The console is meant to run on a machine with no network of any kind. You cannot
install directly on such a machine, so you build it on one that has internet,
prepare the copy, and carry it over.

**You cannot do this by copying the folder in File Explorer.** A folder copied
by hand does not work, and it fails in a way that looks like a bug rather than a
mistake: the copy carries `.venv`, but `.venv` only contains a note saying where
Python is, and Python was in `C:\Users\<your name>\AppData\...` on the machine
you copied *from*. On the offline machine that folder does not exist, and every
launcher stops with `No Python at '…\python.exe'`. `uv` does not travel by hand
either, and nothing starts without it. The two scripts below exist so that none
of that can happen.

### Stage 1 — on the machine that has internet

1. Follow **Parts 1 to 3** above. Let the installer finish.

2. Plug in a USB drive with **at least 8 GB** free.

3. Open the `C:\VMD` folder — the same folder `install.bat` is in.

4. **Double-click `offline-kit.bat`.**

   It fetches the VLC installer — the one thing that cannot live inside the
   folder — then checks, item by item, that everything else the other machine
   needs is actually inside it:

   ```
   [2/3] Checking that everything the other machine needs is inside this folder
         the Python environment (.venv)
         uv, in bin\uv.exe
         ffmpeg, in bin\ffmpeg.exe
         go2rtc, in bin\go2rtc.exe
         the detector's weights (yolo11n.pt)
         VMD.exe
         the VLC installer
         the project's own Python (bin\python\), and .venv is built against it
   ```

   Anything it cannot find, it prints as `MISSING` and tells you what to do.
   **If it says the environment was built against an interpreter outside this
   folder**, delete the `.venv` folder inside `C:\VMD` and run `install.bat`
   again — that rebuilds it correctly, and takes a few minutes.

5. It then asks for the **drive letter** of the USB drive — type `E`, or
   whichever it is, and press Enter. It copies the whole folder across. This is
   about 2 GB and takes a few minutes.

   **It deliberately leaves things behind**, and all of it is either private to
   this machine or rebuilt automatically on the other one:

   - your **recordings** and the index of them
   - **`settings.json`** and **`go2rtc.json`** — both hold the camera's address
     and password. Those are typed on the machine that will use them, in the
     Settings tab, like everything else
   - **frame grabs and saved camera web pages** left in the folder from
     commissioning — the next still saved there is of the perimeter this system
     watches, and it has no business on a USB stick
   - the scratch a run leaves beside the settings, all of which the other
     machine writes fresh

   To see exactly what would travel before it does, without copying anything,
   there is `offline-kit.bat -ListOnly`.

### Stage 2 — on the machine with no internet

1. Plug in the USB drive.

2. Copy the **`VMD`** folder from the USB drive to **`C:\VMD`**. (Drag it onto
   the C: drive in File Explorer. Any folder works — the installer corrects
   itself — but `C:\VMD` is what the rest of this document assumes.)

3. Open the `C:\VMD` folder.

4. **Double-click `offline-install.bat`.** Not `install.bat` — that one needs an
   internet connection and this one does not.

5. It prints seven steps. Windows asks for permission once, when it installs
   VLC; click **Yes**. It ends with the same three lists `install.bat` prints —
   green for what works, yellow for what is missing but optional, red for what
   must be fixed first. When the red list says **`Nothing. This system can be
   used.`** the console opens.

   Everything it printed is saved to `C:\VMD\bin\logs\offline-install.log`. That
   is the file to copy onto the USB drive and send if anything looked wrong —
   this machine has no other way to tell anyone what it saw.

6. **Double-click `cameras.bat`, once for each camera.** This machine watches
   two streets with two cameras, and each gets its own console: its own
   settings, its own recordings, its own window on its own screen. It asks
   three questions — the camera's address, what it watches (in Hebrew), and
   which screen it belongs on — and puts a shortcut on the desktop named after
   what that camera watches.

   Skip this for a single camera. The plain `settings.json` beside `VMD.exe`
   works exactly as it always has.

7. Fill in the camera details in the **Settings** tab and press **Save**, as
   described above — once per camera, on that camera's own console. Recording
   starts as soon as you do.

8. Restart the machine once and check that the console comes back on its own.
   That is the whole point of the exercise, and it is worth seeing it happen
   before you walk away from the machine. Read
   [Part 5 — It starts by itself](#part-5--it-starts-by-itself) about signing in
   automatically, because until you set that up, coming back on its own still
   means somebody types the Windows password first.

**Both machines must run the same version of Windows and the same kind of
processor** (both 64-bit Intel/AMD, or both ARM). Python and the libraries are
compiled for the machine they were downloaded for, and will not run on a
different kind.

**What has not been tested end to end.** The mechanism above was proved on one
machine — a project-local Python, a folder moved to a different path, its
recorded paths repaired, and `uv run --offline --frozen --no-sync` running from
it afterwards. The *two-machine* case has not been run, because that needs a
second machine. Do Stage 2 with somebody who can read a message on the screen the
first time you do it, not on the day the camera goes up.

---

## macOS and Linux

There is no double-click installer for these; the system is built for the
Windows machine. The commands are in [README.md](README.md#macos).

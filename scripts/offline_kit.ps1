# =============================================================================
#  Prepares the folder that goes to the laptop with no internet.
#
#  Run it on the connected machine, after install.bat has finished. Double-click
#  offline-kit.bat.
#
#  The previous instruction was "copy C:\VMD, .venv included, to the other
#  machine". That could not work, and the reason is worth writing down so it is
#  not reintroduced:
#
#    .venv\pyvenv.cfg records the absolute path of the interpreter it was built
#    against. uv keeps its interpreters in %APPDATA%\uv\python\..., outside the
#    folder being copied and under a user name that does not exist on the other
#    machine. The copied .venv points at nothing, and every launcher exits 103
#    with "No Python at '...'". uv itself did not travel either, and both
#    VMD.bat and vmd\launcher.py require it.
#
#  What fixed it is in install.ps1, not here: the interpreter is installed into
#  bin\python\ and uv is copied into bin\uv.exe, so the folder is genuinely
#  self-contained. This script's job is only to check that it really is, add the
#  one thing that cannot live inside a folder - the VLC installer - and copy the
#  result somewhere.
#
#  VLC is the exception because libVLC is a real installation: a registry entry,
#  a plugin tree, and a DLL the console has to be able to find. It has to be
#  installed on the target, so its installer travels beside the project and
#  scripts\offline_install.ps1 runs it there.
#
#  ---------------------------------------------------------------------------
#  Planned, not built: carrying VLC's folder instead of its installer
#  ---------------------------------------------------------------------------
#
#  The installer is the weakest step in the whole offline story. It asks the
#  person standing at an air-gapped laptop to run a machine-wide install, as
#  Administrator, correctly, on the day the camera goes up - and if winget or
#  the MSI picks the 32-bit build, everything looks fine and the console is
#  blind for ever.
#
#  vmd\desktop\libvlc.py is being given "<the project folder>\VLC" as a search
#  candidate, ahead of the registry and the standard folders, so a VLC placed
#  beside the application wins over whatever happens to be installed on the
#  machine, and falls through to today's behaviour when it is absent or wrong.
#  It checks the architecture and the plugins tree there like anywhere else.
#
#  When that lands, this script should copy VLC's installed folder into the kit.
#  Two decisions, made now so they are not made twice:
#
#    Where.     <project>\VLC\, beside VMD.exe - not bin\vendor\, which is for
#               things that are run once and thrown away. This one is read at
#               every start, has to travel with the folder, and has to be at the
#               path the loader looks at. bin\vendor\vlc-win64.exe stays where it
#               is: it remains the fallback for a machine that wants VLC
#               installed properly, and the online path is unchanged.
#
#    Licence.   VLC is GPL-2.0 (which is what its winget manifest declares) with
#               libVLC under LGPL-2.1+. Shipping a copy of the binaries is
#               conveying them, so the copy has to carry its licence: VLC's own
#               COPYING.txt and AUTHORS.txt live inside the installed folder, so
#               copying the folder whole carries them, and copying only a subset
#               must copy them explicitly. Beside them the kit should carry a
#               short note naming the exact VLC version and the URL its source
#               can be obtained from, because a binary-only distribution needs
#               to say where the source is. Neither of these is optional and
#               neither is expensive.
#
#  Not built here yet, deliberately: whether the whole VideoLAN\VLC folder is
#  needed or only libvlc.dll, libvlccore.dll and plugins\ is a question for the
#  code that loads it, and guessing it would produce a kit that works on the
#  machine it was built on and fails on the laptop.
# =============================================================================
param(
    [string]$To,
    [switch]$VerifyOnly,
    # Print what would be copied and copy nothing. The exclusion list below
    # decides what does and does not leave this machine, which is the kind of
    # claim that should be checkable rather than believed.
    [switch]$ListOnly
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$root   = Get-ProjectRoot
$binDir = Join-Path $root 'bin'
$vendor = Join-Path $binDir 'vendor'

Write-Host ""
Write-Host "  Preparing the offline copy" -ForegroundColor White
Write-Host "  $root" -ForegroundColor DarkGray

Set-StepTotal 3

# =============================================================================
#  1. the VLC installer
# =============================================================================
Write-Step "Fetching the VLC installer, which cannot live inside the folder"
$vlcInstaller = Join-Path $vendor 'vlc-win64.exe'
if ((Test-Path $vlcInstaller) -and ((Get-Item $vlcInstaller).Length -gt 10MB)) {
    Write-Ok "Already here: bin\vendor\vlc-win64.exe"
} else {
    # The published filename carries the version, and the version changes. The
    # directory listing is the only stable thing to ask, so it is asked and the
    # name is read out of it rather than guessed.
    $ok = $false
    try {
        $listingUrl = 'https://get.videolan.org/vlc/last/win64/'
        $listing = Invoke-WebRequest -Uri $listingUrl -UseBasicParsing
        $name = ($listing.Content | Select-String -Pattern 'vlc-[0-9.]+-win64\.exe' -AllMatches).Matches.Value |
            Sort-Object -Unique | Select-Object -First 1
        if ($name) {
            Write-Info "Downloading $name (about 42 MB)."
            $ok = Get-File "$listingUrl$name" $vlcInstaller 'the VLC installer' (10MB)
        }
    } catch {
        Write-Bad "Could not reach get.videolan.org: $($_.Exception.Message)"
    }
    if ($ok) { Write-Ok "VLC installer saved to bin\vendor\vlc-win64.exe" }
    else {
        Write-Bad "No VLC installer in the kit."
        Write-Info "Download the 64-bit Windows installer from https://www.videolan.org/vlc/"
        Write-Info "and save it as:  $vlcInstaller"
    }
}

# =============================================================================
#  2. is this folder actually self-contained?
# =============================================================================
Write-Step "Checking that everything the other laptop needs is inside this folder"

$checks = @(
    @{ Path = (Join-Path $root '.venv\Scripts\python.exe'); What = 'the Python environment (.venv)';         Fix = 'run install.bat' }
    @{ Path = (Join-Path $binDir 'uv.exe');                 What = 'uv, in bin\uv.exe';                      Fix = 'run install.bat' }
    @{ Path = (Join-Path $binDir 'ffmpeg.exe');             What = 'ffmpeg, in bin\ffmpeg.exe';              Fix = 'run install.bat' }
    @{ Path = (Join-Path $binDir 'go2rtc.exe');             What = 'go2rtc, in bin\go2rtc.exe';              Fix = 'run install.bat' }
    @{ Path = (Join-Path $root 'yolo11n.pt');               What = "the detector's weights (yolo11n.pt)";    Fix = 'run install.bat' }
    @{ Path = (Join-Path $root 'VMD.exe');                  What = 'VMD.exe';                                Fix = 'run install.bat' }
    @{ Path = (Join-Path $root 'VERSION');                  What = 'the version number (VERSION)';           Fix = 'run install.bat' }
    # The one sound the console plays. Checked here because the exclusion list
    # below strips *.wav from the project root - stray recordings from
    # commissioning - and a rule that ever grew to cover subfolders would take
    # this with it silently. What the operator would get is an alarm that still
    # works and sounds like a Windows notification, which is the failure this
    # sound was replaced to avoid.
    @{ Path = (Join-Path $root 'vmd\desktop\alarm.wav');    What = 'the alarm sound (vmd\desktop\alarm.wav)'; Fix = 'run scripts\make_alarm_sound.py' }
    @{ Path = $vlcInstaller;                                What = 'the VLC installer';                      Fix = 'see step 1 above' }
)

$missing = @()
foreach ($check in $checks) {
    if (Test-Path $check.Path) { Write-Ok "$($check.What)" }
    else { Write-Bad "MISSING - $($check.What)  ($($check.Fix))"; $missing += $check.What }
}

# Is VMD.exe older than the launcher it was built from?
#
# This one shipped, and it is the reason it is checked. VMD.exe was built ten
# hours before vmd\launcher.py was given `--offline --frozen --no-sync`, so the
# exe in every kit ran a plain `uv run` - which re-resolves the lock and BUILDS
# the project. On the machine that made the kit that succeeds in a second and
# nobody notices. On the offline machine there is no build backend to fetch and
# it stops with "failed to build", which is the first thing the operator sees
# after setting his cameras up.
#
# By timestamp and not by reading the exe: PyInstaller compresses what it
# embeds, so searching the binary for the flags finds nothing whether they are
# there or not - which is a check that passes for the wrong reason.
#
# After the loop above rather than before it, because that loop opens with
# $missing = @(). Written above it, this check ran, printed STALE, added its
# entry - and had it thrown away one line later, so the kit called itself ready
# and shipped the exe it had just objected to.
$exe = Join-Path $root 'VMD.exe'
$launcher = Join-Path $root 'vmd\launcher.py'
if ((Test-Path $exe) -and (Test-Path $launcher)) {
    if ((Get-Item $exe).LastWriteTime -lt (Get-Item $launcher).LastWriteTime) {
        Write-Bad "STALE - VMD.exe is older than vmd\launcher.py"
        Write-Info "The exe is a launcher and it is compiled. One built before the"
        Write-Info "launcher was last changed carries the OLD behaviour to the"
        Write-Info "offline machine, where it fails with 'failed to build'."
        Write-Info "Fix: run scripts\build_exe.ps1, then this again."
        $missing += 'a VMD.exe built from the current launcher'
    } else {
        Write-Ok "VMD.exe is newer than the launcher it was built from"
    }
}

# The one that is easy to get wrong and impossible to see: an interpreter that
# is not inside the folder. Everything looks fine here and fails there.
$python = Find-ProjectPython $root
$venvHome = Get-VenvHome $root
if (-not $python) {
    Write-Bad "MISSING - the project's own Python (bin\python\)"
    Write-Info "Without it the copied .venv points at an interpreter that will not"
    Write-Info "exist on the other machine. Run install.bat again."
    $missing += "bin\python\"
} elseif ($venvHome -and -not $venvHome.ToLower().StartsWith($binDir.ToLower())) {
    Write-Bad "The environment was built against an interpreter OUTSIDE this folder:"
    Write-Info "  $venvHome"
    Write-Info "It will not run on the other machine. Delete the .venv folder and run"
    Write-Info "install.bat again, which rebuilds it against bin\python\."
    $missing += "a .venv built against bin\python\"
} else {
    Write-Ok "the project's own Python (bin\python\), and .venv is built against it"
}

if ($missing.Count -gt 0) {
    Write-Host ""
    Write-Bad "The copy is not ready. Fix the lines marked MISSING and run this again."
    Write-Host ""
    if (-not $VerifyOnly) { exit 1 }
}

# =============================================================================
#  3. copy it
# =============================================================================
Write-Step "Copying the folder to the USB drive"

if ($VerifyOnly) {
    Write-Info "Skipped, because -VerifyOnly was given."
    exit 0
}

if (-not $To) {
    Write-Host ""
    Write-Info "Plug in the USB drive, then type its drive letter - E, for example."
    Write-Info "Press Enter on its own to skip copying and do it in File Explorer."
    $answer = Read-Host "  Drive letter"
    if ([string]::IsNullOrWhiteSpace($answer)) {
        Write-Host ""
        Write-Info "Nothing copied. To do it by hand, copy this whole folder:"
        Write-Info "  $root"
        Write-Info "to the USB drive - including the bin folder and the .venv folder."
        exit 0
    }
    $letter = $answer.Trim().TrimEnd(':').TrimEnd('\')
    $To = "${letter}:\VMD"
}

Write-Info "Copying to $To. This is several gigabytes and takes a while."

# robocopy rather than Copy-Item: it handles a few hundred thousand small files
# without holding them all in memory first, it can be run again to continue
# after a pull-out, and it says what it is doing.
#
# --- what a run leaves beside the settings, and why none of it travels -------
#
# Everything in this list is written by a run and rebuilt by the next one on
# the machine that reads it, so leaving it behind costs nothing. Every item
# also carries something about *this* machine, which is the actual reason:
#
#   settings.json   the camera's address and password. Typed on the laptop that
#                   will use it, in the Settings tab, as everything else is.
#   go2rtc.json     the same camera password again, in the streamer's own
#                   config. The console rewrites this file at every start from
#                   settings.json, so the only thing sending it would achieve is
#                   putting credentials for a camera the target may not even be
#                   pointed at yet onto a USB stick.
#   streaming.json  which ports go2rtc took here.
#   detection.json  what the detector had to say about this machine's streams.
#   *.pid           the claim a recorder writes. A PID from another machine is
#                   at best meaningless and at worst matches something.
#   *.pid.json      the companion identity file beside it - vmd\record_main.py
#                   writes recorder.pid.json holding the interpreter path, the
#                   settings path and a timestamp. Note that /XF *.pid does NOT
#                   match it: robocopy matches the whole name, and this one ends
#                   in .json. In practice it is only read after a pid has been
#                   taken out of recorder.pid, which is excluded, so a stray
#                   companion is never reached - but it is full of C:\...
#                   absolute paths from the build machine, and there is no
#                   reason for those to be on the deployment laptop at all.
#   *.db and friends  the segment and event indexes, which describe recordings
#                   that will not be there.
#   smoke_record.*  what a smoke test left lying around.
#
# Directories, by full path rather than by bare name, because a bare name in
# /XD matches at every level and .venv is full of directories called things
# like __pycache__. The environment is copied byte for byte on purpose: it is
# the thing that has been proved to work, and thinning it out is how a copy
# starts differing from the original in ways nobody can see.
#
#   Ultralytics     the detector library's own config, which records
#                   datasets_dir and weights_dir as absolute paths on this
#                   machine and a per-install uuid. It is recreated locally on
#                   first use; carrying this one over would point the offline
#                   laptop at folders that only exist here.
#   recordings, footage, clips   video of somebody's perimeter. Never travels
#                   by accident.
#   .git            history. The laptop is a deployment, not a working copy.
#   build, bin\logs, and the tool caches   scratch.
$excludeFiles = @(
    'settings.json', 'go2rtc.json', 'streaming.json', 'detection.json',
    '*.pid', '*.pid.json',
    '*.db', '*.db-wal', '*.db-shm',
    '*.log', 'smoke_record.*'
)

$excludeDirs = @(
    'recordings', 'footage', 'clips', '.git', 'build', 'Ultralytics',
    '.pytest_cache', '.mypy_cache', '.ruff_cache', '.superpowers',
    '.playwright-mcp', '.claude'
) | ForEach-Object { Join-Path $root $_ }
$excludeDirs += (Join-Path $binDir 'logs')

# What commissioning a camera leaves lying in the project root: frame grabs,
# saved copies of the camera's own web pages, hex dumps, test clips. .gitignore
# refuses to commit any of it for the same reason it should not travel - the
# next still saved here is of the perimeter this system watches, and
# flir_saved.html is a saved page out of the camera's web interface.
#
# Named one by one, and only in the project root. A *.png pattern would also
# strip the 1,214 icons matplotlib ships inside .venv, and *.bin would strip
# model weights out of torch: an environment that works here has to be the
# environment that arrives there. Nothing the application needs in the root has
# one of these extensions - it is .bat, .exe, .md, .toml, .lock and .pt.
$SCRATCH_EXTENSIONS = @(
    '.jpg', '.jpeg', '.png', '.bmp',    # frame grabs
    '.html',                            # saved camera web pages
    '.bin',                             # hex dumps from probing the camera
    '.ts', '.mp4', '.mkv', '.avi', '.mov', '.wav'   # stray recordings
)
$excludeFiles += @(
    Get-ChildItem -Path $root -File -ErrorAction SilentlyContinue |
        Where-Object { $SCRATCH_EXTENSIONS -contains $_.Extension } |
        ForEach-Object { $_.FullName }
)

$robocopyArgs = @($root, $To, '/E', '/R:1', '/W:1', '/NP')
if ($ListOnly) { $robocopyArgs += '/L' }        # say what would travel, copy nothing
else           { $robocopyArgs += @('/NFL', '/NDL') }
$robocopyArgs += @('/XD') + $excludeDirs + @('/XF') + $excludeFiles
& robocopy @robocopyArgs | Out-Host
$code = $LASTEXITCODE

# robocopy's exit codes are a bit field, not a status. 0-7 is success of some
# kind; 8 and above is a real failure. Treating "files were copied" (1) as an
# error is the classic way to make a working script look broken.
if ($code -ge 8) {
    Write-Bad "The copy did not finish (robocopy code $code). The output above says why."
    exit 1
}

Write-Ok "Copied to $To"
Write-Host ""
Write-Host "  On the computer with no internet:" -ForegroundColor White
Write-Host "    1. Copy the VMD folder to C:\VMD" -ForegroundColor Gray
Write-Host "    2. Open it and double-click offline-install.bat" -ForegroundColor Gray
Write-Host "       (not install.bat - that one needs an internet connection)" -ForegroundColor Gray
Write-Host "    3. Double-click cameras.bat, once for each camera" -ForegroundColor Gray
Write-Host ""
exit 0

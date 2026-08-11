# =============================================================================
#  Installs VMD on the laptop that has no internet.
#
#  Double-click scripts\offline-install.bat. Nothing here touches the network,
#  and nothing here needs to: everything except VLC arrived inside this folder,
#  put there by scripts\offline_kit.ps1 on the machine that had a connection.
#
#  What it actually does, in one sentence each:
#
#    - repairs the two absolute paths a copied environment cannot carry,
#    - puts bin\ on PATH so uv, ffmpeg and the console can find each other,
#    - installs VLC from the installer that travelled in bin\vendor\,
#    - proves the environment runs, without asking the network anything,
#    - makes the recorder and the console start by themselves after a restart.
#
#  It is safe to run twice.
# =============================================================================
param(
    [switch]$NoLaunch,
    [switch]$NoAutostart
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$root   = Get-ProjectRoot
$binDir = Join-Path $root 'bin'

Write-Host ""
Write-Host "  VMD offline installer" -ForegroundColor White
Write-Host "  $root" -ForegroundColor DarkGray

Set-StepTotal 7

# =============================================================================
#  1. is this a prepared copy?
# =============================================================================
# The failure this catches is the one worth catching early: somebody downloads
# the ZIP from GitHub, carries that to the offline laptop, and runs this. A ZIP
# has no .venv, no uv and no interpreter, and there is no way to build them
# here. Better to say so in one sentence than to fail five steps later in a way
# that reads like a bug.
Write-Step "Checking that this folder came from the connected machine"
$fatal = @()
foreach ($item in @(
    @{ Path = (Join-Path $root '.venv\Scripts\python.exe'); What = 'the Python environment (.venv)' }
    @{ Path = (Join-Path $binDir 'uv.exe');                 What = 'uv (bin\uv.exe)' }
)) {
    if (Test-Path $item.Path) { Write-Ok $item.What }
    else { Write-Bad "MISSING - $($item.What)"; $fatal += $item.What }
}
$python = Find-ProjectPython $root
if ($python) { Write-Ok "the project's own Python (bin\python\)" }
else { Write-Bad "MISSING - the project's own Python (bin\python\)"; $fatal += 'bin\python\' }

if ($fatal.Count -gt 0) {
    Write-Host ""
    Write-Bad "This is not a prepared copy - it is the plain project files."
    Write-Info "There is no way to finish the install on a machine with no internet."
    Write-Info "On a machine that has one: run install.bat, then"
    Write-Info "scripts\offline-kit.bat, and bring the folder that produces."
    exit 1
}

# =============================================================================
#  2. the two paths a copy cannot carry
# =============================================================================
# .venv\pyvenv.cfg holds the absolute path of the interpreter, and
# _editable_impl_vmd.pth holds the absolute path of the project. Both were
# written on the other machine. If this folder landed at the same place it was
# built - C:\VMD on both - they are already right and nothing happens here.
Write-Step "Pointing the environment at this machine"
$repaired = Repair-VenvPaths $root
if ($repaired.Count -eq 0) {
    Write-Ok "Nothing to change - the folder is where it was built."
} else {
    foreach ($item in $repaired) { Write-Ok "Corrected $item" }
}

# =============================================================================
#  3. PATH
# =============================================================================
Write-Step "Putting bin\ on PATH"
Write-Info "bin\ holds uv and ffmpeg. VMD.exe looks for uv on PATH and nowhere"
Write-Info "else, and the recorder runs ffmpeg by name, so this is not optional."
if (Add-BinToUserPath $binDir) { Write-Ok "bin\ added to your PATH." }
else { Write-Ok "bin\ is already on your PATH." }

# =============================================================================
#  4. VLC
# =============================================================================
# The only thing that cannot live inside the folder. libVLC is found by
# python-vlc through the registry, so it has to be a real installation.
Write-Step "Installing VLC (draws the live picture in the console)"
$vlcDir = Join-Path $env:ProgramFiles 'VideoLAN\VLC'
$vlcDll = Join-Path $vlcDir 'libvlc.dll'
$haveVlc = Test-Path $vlcDll
if ($haveVlc) {
    Write-Ok "VLC is already installed."
} else {
    $installer = Join-Path $binDir 'vendor\vlc-win64.exe'
    if (Test-Path $installer) {
        Write-Info "Windows will ask for permission. Click Yes."
        Write-Info "The installer runs without showing anything; give it a minute."
        try {
            # /L=1033 /S is NSIS: English, silent. Verb RunAs raises exactly one
            # permission prompt, which is the whole of what this install needs
            # administrator rights for.
            Start-Process -FilePath $installer -ArgumentList '/L=1033', '/S' -Verb RunAs -Wait
        } catch {
            Write-Bad "VLC was not installed: $($_.Exception.Message)"
        }
        $haveVlc = Test-Path $vlcDll
        if ($haveVlc) { Write-Ok "VLC installed." }
        else {
            Write-Bad "VLC still is not installed."
            Write-Info "Double-click $installer yourself and click through it."
            Write-Info "Everything except the live picture works without it."
        }
    } else {
        Write-Bad "No VLC installer in bin\vendor\."
        Write-Info "The console will open but show no live picture. Everything else,"
        Write-Info "recording included, works. Bring the 64-bit VLC installer over"
        Write-Info "on the USB drive and run it."
    }
}

# libVLC rebuilds its plugin index whenever the index is older than the plugins,
# printing a line per plugin and taking about fifteen seconds over it. Doing it
# once here means the operator's first console start is not fifteen seconds of
# blank screen that looks exactly like a hang.
$cacheGen = Join-Path $vlcDir 'vlc-cache-gen.exe'
if ($haveVlc -and (Test-Path $cacheGen)) {
    try {
        Start-Process -FilePath $cacheGen -ArgumentList (Join-Path $vlcDir 'plugins') -Verb RunAs -Wait
        Write-Ok "VLC's plugin index refreshed, so the console starts faster."
    } catch {
        Write-Info "Could not refresh VLC's plugin index. Harmless - the first start is just slow."
    }
}

# =============================================================================
#  5. does it run?
# =============================================================================
# Every command here is offline by construction. `uv run --offline --frozen
# --no-sync` is the same incantation VMD.bat and vmd\launcher.py use, and for
# the same reason: a plain `uv run` re-checks the lock and syncs, and a sync on
# this machine is a hang with no way out.
Write-Step "Checking that it runs, without asking the network anything"
$uv = Join-Path $binDir 'uv.exe'
Push-Location $root
try {
    & $uv run --offline --frozen --no-sync python -c "import cv2, pydantic, ultralytics; print('libraries ok')" | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Bad "The environment does not run on this machine."
        Write-Info "The message above is the whole diagnosis. 'No Python at ...' means"
        Write-Info "the copy is missing bin\python\ - go back to the connected machine"
        Write-Info "and run scripts\offline-kit.bat, which checks for exactly that."
        exit 1
    }
    Write-Ok "The libraries import."
}
finally { Pop-Location }

if (Test-Path (Join-Path $binDir 'ffmpeg.exe')) { Write-Ok "ffmpeg is in bin\, which is on PATH." }
else { Write-Bad "No ffmpeg in bin\. Nothing can be recorded until there is." }

if (Test-Path (Join-Path $binDir 'go2rtc.exe')) { Write-Ok "go2rtc is in bin\." }
else { Write-Warn "No go2rtc in bin\. The console will show no live picture." }

$weights = Join-Path $root 'yolo11n.pt'
if ((Test-Path $weights) -and ((Get-Item $weights).Length -gt 1MB)) {
    Write-Ok "The detector's weights are here (yolo11n.pt)."
} else {
    Write-Warn "No yolo11n.pt. Movement is still detected, but never named -"
    Write-Warn "nothing will be downloaded, because this machine has no internet."
}

if (-not (Test-Path (Join-Path $root 'VMD.exe'))) {
    Write-Warn "VMD.exe did not travel. Use VMD.bat instead - it does the same thing."
}

# =============================================================================
#  6. starting by itself
# =============================================================================
Write-Step "Making the system start by itself after a restart"
if ($NoAutostart) {
    Write-Info "Skipped, because -NoAutostart was given."
} else {
    try {
        & (Join-Path $PSScriptRoot 'autostart.ps1') -Install | Out-Host
    } catch {
        Write-Warn "Could not set up automatic starting: $($_.Exception.Message)"
        Write-Info "Everything else works. Run scripts\autostart-on.bat to try again."
    }
}

# =============================================================================
#  7. done
# =============================================================================
Write-Step "Starting the console"
Write-Host ""
Write-Host "  Installed." -ForegroundColor Green
Write-Host ""
Write-Host "  Now type the camera's address, username, password and stream" -ForegroundColor Gray
Write-Host "  addresses into the Settings tab, and press Save." -ForegroundColor Gray
Write-Host ""
Write-Host "  Recording does not start until that is done, because until then" -ForegroundColor Gray
Write-Host "  there is no camera to record." -ForegroundColor Gray
Write-Host ""

if ($NoLaunch) { exit 0 }

$exe = Join-Path $root 'VMD.exe'
if (Test-Path $exe) { & $exe }
else {
    Push-Location $root
    try { & (Join-Path $root 'VMD.bat') }
    finally { Pop-Location }
}
exit 0

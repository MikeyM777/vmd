# =============================================================================
#  VMD installer. Run it by double-clicking install.bat in the project root.
#
#  What it leaves behind, and why each piece is where it is:
#
#    uv          installed machine-wide by winget, and copied into bin\ as well.
#                The copy is the one that matters: it is what travels to the
#                offline laptop, where there is no winget to install anything.
#    VLC         installed machine-wide. libVLC is a real installation with a
#                registry entry and a plugin tree; it cannot live in bin\.
#    ffmpeg      bin\ffmpeg.exe. Not installed machine-wide any more: the
#                recorder runs the bare name "ffmpeg", bin\ is on PATH, and one
#                ffmpeg that travels with the project beats two that do not.
#    go2rtc      bin\go2rtc.exe.
#    Python      bin\python\. Inside the project on purpose - see step 8.
#    yolo11n.pt  the project root, which is where vmd\detect\classify.py looks.
#    .venv\      the libraries, built against bin\python\.
#    VMD.exe     a launcher for the project it sits in, carrying no code.
#
#  Everything except VLC is inside the project folder, so the folder is the
#  install. That is what makes the offline laptop possible at all.
#
#  Switches, all of them for scripts and tests rather than for people:
#    -NoLaunch      finish without opening the console.
#    -NoAutostart   skip creating the scheduled tasks.
#    -PackagesOnly  only the machine-wide half (winget, VLC). Used when this
#                   script relaunches itself elevated.
#    -SkipPackages  only the project half. The other side of the same coin.
# =============================================================================
param(
    [switch]$NoLaunch,
    [switch]$NoAutostart,
    [switch]$PackagesOnly,
    [switch]$SkipPackages
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$root   = Get-ProjectRoot
$binDir = Join-Path $root 'bin'

$doPackages = -not $SkipPackages
$doProject  = -not $PackagesOnly

Set-StepTotal 12

Write-Host ""
Write-Host "  VMD installer" -ForegroundColor White
Write-Host "  $root" -ForegroundColor DarkGray

# A prepared offline copy carries a VLC installer, which nothing else puts
# there. Running this script on such a folder is the mistake somebody makes on
# the day they are standing at the laptop that has no network, and it fails at
# winget with a message about the Microsoft Store. Say the right thing instead.
# Informative rather than fatal: this file is also what built that copy, and
# re-running it on the connected machine has to keep working.
if ((Test-Path (Join-Path $binDir 'vendor\vlc-win64.exe')) -and
    (Test-Path (Join-Path $root '.venv'))) {
    Write-Host ""
    Write-Warn "This folder looks like a copy prepared for a machine with no internet."
    Write-Warn "If this machine has no internet, close this window and double-click"
    Write-Warn "offline-install.bat instead - it needs no connection."
    Write-Warn "If this machine does have internet, carry on; nothing is wrong."
    Write-Host ""
}

function Update-PathFromRegistry {
    # A winget install writes the new folder into the stored PATH, not into
    # this already-running process. Without this the command stays "missing"
    # until the window is reopened, which is exactly the confusion this script
    # exists to avoid.
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ';'
}

function Install-Package($id, $command, $label) {
    if (Test-Have $command) {
        Write-Ok "$label is already installed."
        return $true
    }
    Write-Info "$label is missing. Installing it with winget - this can take a few minutes."
    winget install --id $id --exact --silent `
        --accept-source-agreements --accept-package-agreements | Out-Host
    Update-PathFromRegistry
    if (Test-Have $command) {
        Write-Ok "$label installed."
        return $true
    }
    Write-Bad "$label still is not runnable after installing it."
    return $false
}

# =============================================================================
#  One permission prompt, not five
# =============================================================================
#
# winget's machine-wide installs each raise their own UAC prompt when this runs
# unelevated, and a beginner who says No to one of five identical prompts ends
# up with a half-installed machine and no idea which half. So the machine-wide
# steps are done once, together, in a single elevated window; the rest stays
# unelevated, which is what we want anyway - the console, the settings file and
# the recordings should belong to the person using the laptop, not to
# Administrator.
if ($doPackages -and $doProject -and -not (Test-Admin)) {
    Write-Host ""
    Write-Info "Windows is about to ask for permission, once."
    Write-Info "A second window will open, install VLC and uv, and close by itself."
    Write-Info "Click Yes when Windows asks. Then this window carries on."
    try {
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', $PSCommandPath, '-PackagesOnly'
        )
        Update-PathFromRegistry
        $doPackages = $false
    } catch {
        # Refusing the prompt is a decision, not a crash. Carry on unelevated:
        # winget will ask again for itself, and if that is refused too, the
        # steps below say plainly what is missing.
        Write-Warn "Permission was not given, so the installer will ask again per item."
    }
}

# =============================================================================
#  1. winget
# =============================================================================
Write-Step "Checking the Windows package manager"
if ($doPackages) {
    if (-not (Test-Have 'winget')) {
        Write-Bad "winget is not available on this machine."
        Write-Info "It ships with 'App Installer'. Install that from the Microsoft Store,"
        Write-Info "reopen this installer, and it will carry on:"
        Write-Info "  https://apps.microsoft.com/detail/9nblggh4nns1"
        exit 1
    }
    Write-Ok "winget is available."
} else {
    Write-Ok "Already checked."
}

# =============================================================================
#  2. uv
# =============================================================================
Write-Step "Checking uv (brings Python and the libraries with it)"
if ($doPackages) {
    $null = Install-Package 'astral-sh.uv' 'uv' 'uv'
} else {
    Update-PathFromRegistry
    if (Test-Have 'uv') { Write-Ok "uv is available." }
}
$haveUv = Test-Have 'uv'

# =============================================================================
#  3. VLC
# =============================================================================
# The console draws its live video with libVLC, which comes with the ordinary
# VLC media player. It is not put on PATH by its installer, so presence is read
# off the file it actually needs rather than off a command name. The 64-bit
# build is the one that matters: a 32-bit VLC cannot be loaded by 64-bit Python,
# and that mismatch is the one failure that looks like VLC being missing when it
# is plainly installed.
Write-Step "Checking VLC (draws the live picture in the console)"
$vlcDir   = Join-Path $env:ProgramFiles 'VideoLAN\VLC'
$vlcDll   = Join-Path $vlcDir 'libvlc.dll'
$vlc32Dll = Join-Path ${env:ProgramFiles(x86)} 'VideoLAN\VLC\libvlc.dll'
$haveVlc  = Test-Path $vlcDll

if ($doPackages) {
    if ($haveVlc) {
        Write-Ok "VLC is already installed."
    } else {
        Write-Info "VLC is missing. Installing it with winget."
        winget install --id VideoLAN.VLC --exact --silent `
            --accept-source-agreements --accept-package-agreements | Out-Host
        Update-PathFromRegistry
        $haveVlc = Test-Path $vlcDll
        if ($haveVlc) { Write-Ok "VLC installed." }
        elseif (Test-Path $vlc32Dll) {
            Write-Bad "Only the 32-bit VLC is installed, which the console cannot use."
            Write-Info "Uninstall it and install the 64-bit VLC from https://www.videolan.org/vlc/"
        }
        else {
            # Not fatal: everything except the live picture works without it, and
            # the console says so in the video pane rather than refusing to open.
            Write-Bad "VLC is still not installed."
            Write-Info "Install it later from https://www.videolan.org/vlc/ - take the 64-bit"
            Write-Info "Windows installer. Everything except the live picture works without it."
        }
    }

    # libVLC checks its plugin index against the plugins on disk at every start.
    # When the index is older than the plugins - which it is after most VLC
    # upgrades - it rebuilds it, prints a line per plugin to stderr while it
    # does so, and the console's first window takes about fifteen seconds to
    # appear. Nothing is wrong, but fifteen seconds of blank screen reads as a
    # hang to somebody who has never seen it before. Regenerating the index once
    # here, while we still have the permission to write into Program Files, is
    # the only chance to fix it rather than explain it.
    $cacheGen = Join-Path $vlcDir 'vlc-cache-gen.exe'
    if ($haveVlc -and (Test-Path $cacheGen) -and (Test-Admin)) {
        try {
            & $cacheGen (Join-Path $vlcDir 'plugins') 2>&1 | Out-Null
            Write-Ok "VLC's plugin index refreshed, so the console starts faster."
        } catch {
            Write-Info "Could not refresh VLC's plugin index. Harmless - the first start is just slow."
        }
    }
} else {
    if ($haveVlc) { Write-Ok "VLC is installed." }
    else { Write-Warn "VLC is not installed, so the console will show no live picture." }
}

if ($PackagesOnly) {
    # The elevated half is done. Everything after this belongs to the person
    # who will actually use the laptop, not to Administrator.
    Write-Host ""
    Write-Ok "Machine-wide components done. This window closes now."
    Start-Sleep -Seconds 2
    exit 0
}

if (-not $haveUv) {
    Write-Bad "Cannot continue without uv."
    Write-Info "Install it by hand with:  winget install --id astral-sh.uv -e"
    exit 1
}

# =============================================================================
#  4. ffmpeg, in bin\
# =============================================================================
# vmd\storage\recorder.py runs the bare name "ffmpeg" and finds it on PATH.
# bin\ is put on PATH in step 7, so an ffmpeg.exe here is found by that name -
# and, unlike a winget install, it is inside the folder that gets copied to the
# offline laptop. This is also the path INSTALL.md has always named, which
# until now nothing actually read.
Write-Step "Checking ffmpeg (records the video)"
$ffmpeg = Join-Path $binDir 'ffmpeg.exe'
if (Test-Path $ffmpeg) {
    Write-Ok "ffmpeg is already here: bin\ffmpeg.exe"
    $haveFfmpeg = $true
} else {
    Write-Info "Downloading ffmpeg. This is about 170 MB and is the second-longest step."
    $zip = Join-Path $env:TEMP 'vmd-ffmpeg.zip'
    $unpack = Join-Path $env:TEMP 'vmd-ffmpeg-unpack'
    $haveFfmpeg = $false
    # BtbN's builds first, gyan.dev second. Both are the usual Windows ffmpeg
    # builds; the order is about the host rather than the build. gyan.dev is a
    # single small server that regularly delivers this file at a few hundred
    # kilobytes a second - slow enough to look like a hang, and slow enough to
    # time out - while BtbN publishes through GitHub's own release CDN.
    $sources = @(
        'https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip',
        'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
    )
    $got = $false
    foreach ($source in $sources) {
        if (Get-File $source $zip 'ffmpeg' (20MB)) { $got = $true; break }
        Write-Info "Trying another source."
    }
    if ($got) {
        try {
            Remove-Item $unpack -Recurse -Force -ErrorAction SilentlyContinue
            Expand-Archive -Path $zip -DestinationPath $unpack -Force
            New-Item -ItemType Directory -Force -Path $binDir | Out-Null
            foreach ($name in @('ffmpeg.exe', 'ffprobe.exe')) {
                $found = Get-ChildItem $unpack -Filter $name -Recurse -ErrorAction SilentlyContinue |
                    Select-Object -First 1
                if ($found) { Copy-Item $found.FullName (Join-Path $binDir $name) -Force }
            }
            $haveFfmpeg = Test-Path $ffmpeg
        } catch {
            Write-Bad "The ffmpeg download unpacked badly: $($_.Exception.Message)"
        } finally {
            Remove-Item $zip -Force -ErrorAction SilentlyContinue
            Remove-Item $unpack -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    if ($haveFfmpeg) { Write-Ok "ffmpeg installed to bin\ffmpeg.exe" }
    else {
        Write-Bad "Could not put ffmpeg in bin\. Nothing can be recorded without it."
        Write-Info "Fetch it by hand from https://www.gyan.dev/ffmpeg/builds/ (release essentials),"
        Write-Info "and copy ffmpeg.exe into:  $binDir"
    }
}
# An ffmpeg already on PATH from an earlier release of this installer still
# works, and saying so avoids a warning that contradicts a working machine.
if (-not $haveFfmpeg -and (Test-Have 'ffmpeg')) {
    Write-Info "There is an ffmpeg on PATH from somewhere else; recording will use that."
    $haveFfmpeg = $true
}

# =============================================================================
#  5. go2rtc
# =============================================================================
# Not in winget, and it is a single self-contained binary, so it is fetched
# straight from the project's own releases and kept inside bin\ rather than
# installed system-wide. Deleting bin\ undoes it completely.
Write-Step "Checking go2rtc (re-serves the camera stream to the console)"
$go2rtc = Join-Path $binDir 'go2rtc.exe'
if (Test-Path $go2rtc) {
    Write-Ok "go2rtc is already here: bin\go2rtc.exe"
} else {
    $asset = switch ($env:PROCESSOR_ARCHITECTURE) {
        'ARM64' { 'go2rtc_win_arm64.zip' }
        'x86'   { 'go2rtc_win32.zip' }
        default { 'go2rtc_win64.zip' }
    }
    $zip = Join-Path $env:TEMP "vmd-$asset"
    Write-Info "Downloading $asset"
    if (Get-File "https://github.com/AlexxIT/go2rtc/releases/latest/download/$asset" $zip 'go2rtc' (1MB)) {
        try {
            New-Item -ItemType Directory -Force -Path $binDir | Out-Null
            Expand-Archive -Path $zip -DestinationPath $binDir -Force
            if (Test-Path $go2rtc) { Write-Ok "go2rtc installed to bin\go2rtc.exe" }
            else { Write-Bad "The download unpacked but go2rtc.exe is not in bin\." }
        } catch {
            Write-Bad "The go2rtc download unpacked badly: $($_.Exception.Message)"
        } finally { Remove-Item $zip -Force -ErrorAction SilentlyContinue }
    } else {
        # A missing streamer does not invalidate the rest of the install: only
        # the live picture needs it. Say so and carry on.
        Write-Info "Everything else still installs. Fetch it later from:"
        Write-Info "  https://github.com/AlexxIT/go2rtc/releases/latest"
    }
}

# =============================================================================
#  6. the detector's weights
# =============================================================================
# yolo11n.pt is not in the repository - it is five and a half megabytes of
# binary that git has no business versioning - so a fresh clone or ZIP arrives
# without it. Handed a name it cannot find, ultralytics recognises yolo11n.pt as
# one of its own published assets and downloads it from github.com. On the
# laptop this runs on there is no network for it to do that over, so the
# download has to happen here, on the machine that has one.
#
# It goes in the project root because that is where vmd\detect\classify.py
# resolves DEFAULT_WEIGHTS to - beside the application, not in whatever
# directory the process was started from.
Write-Step "Checking the detector's weights (what names the thing that moved)"
$weights = Join-Path $root 'yolo11n.pt'
if ((Test-Path $weights) -and ((Get-Item $weights).Length -gt 1MB)) {
    Write-Ok "yolo11n.pt is already here."
} else {
    Write-Info "Downloading yolo11n.pt (5.4 MB)."
    if (Get-File 'https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt' $weights 'the detector weights' (4MB)) {
        Write-Ok "yolo11n.pt downloaded."
    } else {
        Write-Bad "Without this file, movement is still detected but never named."
        Write-Info "Download it by hand to $weights from"
        Write-Info "  https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt"
    }
}

# =============================================================================
#  7. uv beside the project, and bin\ on PATH
# =============================================================================
# uv is a single self-contained executable, so the one winget just installed
# can simply be copied. Copying rather than downloading guarantees the version
# in bin\ is the same version that wrote and validated uv.lock, which is one
# fewer thing to differ between the machine that builds and the machine that
# runs.
#
# PATH is what makes all of this reachable. VMD.exe is vmd\launcher.py frozen,
# and it finds uv with shutil.which(), which reads PATH and nothing else. On the
# offline laptop the copy in bin\ is the only uv there will ever be, so if bin\
# is not on PATH then double-clicking VMD.exe says "uv is not installed" on a
# machine where it plainly is.
Write-Step "Putting uv and bin\ where the console can find them"
$uvSource = (Get-Command uv -ErrorAction SilentlyContinue).Source
$uvLocal  = Join-Path $binDir 'uv.exe'
if ($uvSource -and (Test-Path $uvSource)) {
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null
    Copy-Item $uvSource $uvLocal -Force
    Write-Ok "uv copied to bin\uv.exe"
} elseif (Test-Path $uvLocal) {
    Write-Ok "uv is already in bin\uv.exe"
} else {
    Write-Warn "Could not copy uv into bin\. The offline copy will not work without it."
}

if (Add-BinToUserPath $binDir) { Write-Ok "bin\ added to your PATH." }
else { Write-Ok "bin\ is already on your PATH." }

# =============================================================================
#  8. the project's own Python
# =============================================================================
# uv would normally keep the interpreter in its own store under %APPDATA%, and
# .venv\pyvenv.cfg would record that absolute path. Copying the project folder
# to another machine then produces a .venv pointing at
# C:\Users\<the other person>\AppData\Roaming\uv\... which does not exist, and
# every launcher fails with
#
#     No Python at '...\python.exe'
#
# and exit code 103. Keeping the interpreter under bin\python\ means the whole
# thing travels in one folder; scripts\offline_install.ps1 rewrites the one
# recorded path if the folder lands somewhere other than where it was built.
Write-Step "Installing the project's own Python"
$pythonDir = Get-ProjectPythonDir $root
$projectPython = Find-ProjectPython $root
if ($projectPython) {
    Write-Ok "Already here: $(Split-Path -Leaf (Split-Path -Parent $projectPython))"
} else {
    Write-Info "Downloading CPython 3.12 into bin\python\ (about 20 MB)."
    uv python install --install-dir $pythonDir 3.12 | Out-Host
    $projectPython = Find-ProjectPython $root
    if ($projectPython) { Write-Ok "Python installed into bin\python\" }
    else {
        # Not fatal on this machine: uv will use one of its own interpreters and
        # everything works here. It is fatal for the copy that goes to the
        # offline laptop, and that is worth saying now rather than there.
        Write-Warn "Could not install Python into bin\python\."
        Write-Warn "This machine will still work. A copy of this folder on an offline"
        Write-Warn "machine will not - the interpreter would be left behind."
    }
}

# =============================================================================
#  9. the environment
# =============================================================================
Write-Step "Building the Python environment"
Write-Info "Fetching the libraries at the versions in uv.lock."
Write-Info "This includes the detector stack, which is a large download."
Write-Info "The first run takes several minutes; later runs are seconds."
Push-Location $root
try {
    # --extra detect matters: a plain `uv sync` prunes anything not declared,
    # which would strip the detector out of an environment that had it.
    #
    # --python pins the environment to the interpreter inside the project. Left
    # to itself uv picks whichever interpreter it likes best, and on a machine
    # that already had one that is the one it takes - quietly undoing step 8.
    $syncArgs = @('sync', '--extra', 'detect')
    if ($projectPython) { $syncArgs += @('--python', $projectPython) }
    & uv @syncArgs | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Bad "uv sync failed. The output above says why."
        exit 1
    }

    uv run python -c "import cv2, pydantic, ultralytics" | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Bad "The environment was built but the libraries do not import."
        exit 1
    }
    Write-Ok "Environment ready."
}
finally { Pop-Location }

# =============================================================================
#  10. the single-file launcher
# =============================================================================
Write-Step "Building VMD.exe (the thing you double-click from now on)"
try {
    & (Join-Path $PSScriptRoot 'build_exe.ps1') | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "build failed" }
}
catch {
    # The exe is convenience, not function: VMD.bat starts the same console.
    Write-Bad "Could not build VMD.exe: $($_.Exception.Message)"
    Write-Info "Not a problem - double-click VMD.bat instead. It does the same thing."
}

# =============================================================================
#  11. starting by itself
# =============================================================================
# The laptop this runs on is a dedicated recorder that is never meant to be
# off. Windows reboots anyway - updates, power cuts - and until now every
# reboot left the perimeter unrecorded until somebody walked over and
# double-clicked. Two scheduled tasks fix that; scripts\autostart.ps1 explains
# what they are and how to take them away again.
Write-Step "Making the system start by itself after a restart"
if ($NoAutostart) {
    Write-Info "Skipped, because -NoAutostart was given."
} else {
    try {
        & (Join-Path $PSScriptRoot 'autostart.ps1') -Install -Quiet | Out-Host
    } catch {
        Write-Warn "Could not set up automatic starting: $($_.Exception.Message)"
        Write-Info "Everything else works. Run autostart-on.bat to try again."
    }
}

# =============================================================================
#  12. start the console
# =============================================================================
Write-Step "Starting the console"

Write-Host ""
if (-not $haveFfmpeg) {
    # Not fatal: everything except recording still works, and saying so plainly
    # beats failing the whole install over a component this run may not use.
    Write-Host "  WARNING - ffmpeg is not installed, so nothing can be recorded." -ForegroundColor Yellow
    Write-Host "  Install it and run this again before using the system for real." -ForegroundColor Yellow
    Write-Host ""
}
if (-not $haveVlc) {
    Write-Host "  WARNING - VLC is not installed, so the console shows no live picture." -ForegroundColor Yellow
    Write-Host "  Everything else, recording included, works without it." -ForegroundColor Yellow
    Write-Host ""
}
Write-Host "  Installed." -ForegroundColor Green
Write-Host ""
Write-Host "  From now on, to start the console:  double-click VMD.exe" -ForegroundColor White
Write-Host "  (or VMD.bat - same thing)" -ForegroundColor Gray
Write-Host ""
Write-Host "  Enter the camera address, username, password and stream addresses" -ForegroundColor Gray
Write-Host "  in the Settings tab and press Save. There is no file to edit." -ForegroundColor Gray
Write-Host ""
Write-Host "  The console starts the streaming server and the recorder itself." -ForegroundColor Gray
Write-Host "  Closing its window does not stop the recording." -ForegroundColor Gray
Write-Host ""
Write-Host "  After a restart, recording begins as soon as you sign in and the" -ForegroundColor Gray
Write-Host "  console window opens by itself about 45 seconds later. Do not" -ForegroundColor Gray
Write-Host "  double-click VMD.exe while waiting - you would get two consoles." -ForegroundColor Gray
Write-Host ""
Write-Host "  Recording service:  uv run python -m vmd.record_main" -ForegroundColor Gray
Write-Host "  Tests:              uv run pytest" -ForegroundColor Gray
Write-Host ""
Write-Host "  For a laptop with no internet, run offline-kit.bat next." -ForegroundColor Gray

if ($NoLaunch) { exit 0 }

# Hand over to the console itself. It stays running, so this window becomes the
# console's window rather than a second one.
$exe = Join-Path $root 'VMD.exe'
if (Test-Path $exe) { & $exe }
else {
    Push-Location $root
    try { uv run python -m vmd.desktop }
    finally { Pop-Location }
}
exit 0

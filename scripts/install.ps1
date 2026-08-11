# =============================================================================
#  VMD installer. Run it by double-clicking install.bat in the project root.
#
#  It puts four things on the machine and then builds the environment:
#
#    uv      - installs Python itself and every Python library
#    ffmpeg  - records the video
#    VLC     - draws the live picture inside the console window
#    go2rtc  - takes the camera's RTSP once and re-serves it to the console
#
#  Python is deliberately not installed on its own: uv fetches the exact version
#  this project needs, which avoids the usual mess of several Pythons on one
#  machine and the Microsoft Store stub that only pretends to be one.
#
#  Anything already present is left alone, so running this twice is quick.
#
#  -NoLaunch skips opening the console at the end. It exists so the install can
#  be tested without a console window appearing.
# =============================================================================
param([switch]$NoLaunch)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$STEPS = 8

function Write-Step($n, $text) { Write-Host "`n[$n/$STEPS] $text" -ForegroundColor Cyan }
function Write-Ok($text)       { Write-Host "      $text" -ForegroundColor Green }
function Write-Info($text)     { Write-Host "      $text" -ForegroundColor Gray }
function Write-Bad($text)      { Write-Host "      $text" -ForegroundColor Red }

function Test-Have($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }

# A winget install writes the new folder into the stored PATH, not into this
# already-running process. Without this the command stays "missing" until the
# window is reopened, which is exactly the confusion this script exists to avoid.
function Update-PathFromRegistry {
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

Write-Host ""
Write-Host "  VMD installer" -ForegroundColor White
Write-Host "  $root" -ForegroundColor DarkGray

# --- 1. winget ---------------------------------------------------------------
Write-Step 1 "Checking the Windows package manager"
if (-not (Test-Have 'winget')) {
    Write-Bad "winget is not available on this machine."
    Write-Info "It ships with 'App Installer'. Install that from the Microsoft Store,"
    Write-Info "reopen this installer, and it will carry on:"
    Write-Info "  https://apps.microsoft.com/detail/9nblggh4nns1"
    exit 1
}
Write-Ok "winget is available."

# --- 2 and 3. the packaged dependencies --------------------------------------
Write-Step 2 "Checking uv (brings Python and the libraries with it)"
$haveUv = Install-Package 'astral-sh.uv' 'uv' 'uv'

Write-Step 3 "Checking ffmpeg (records the video)"
$haveFfmpeg = Install-Package 'Gyan.FFmpeg' 'ffmpeg' 'ffmpeg'

if (-not $haveUv) {
    Write-Bad "Cannot continue without uv."
    exit 1
}

# --- 4. VLC ------------------------------------------------------------------
# The console draws its live video with libVLC, which comes with the ordinary
# VLC media player. It is not put on PATH by its installer, so presence is read
# off the file it actually needs rather than off a command name. The 64-bit
# build is the one that matters: a 32-bit VLC cannot be loaded by 64-bit Python,
# and that mismatch is the one failure that looks like VLC being missing when it
# is plainly installed.
Write-Step 4 "Checking VLC (draws the live picture in the console)"
$vlcDll   = Join-Path $env:ProgramFiles 'VideoLAN\VLC\libvlc.dll'
$vlc32Dll = Join-Path ${env:ProgramFiles(x86)} 'VideoLAN\VLC\libvlc.dll'
$haveVlc = Test-Path $vlcDll

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
        # Not fatal: everything except the live picture works without it, and the
        # console says so in the video pane rather than refusing to open.
        Write-Bad "VLC is still not installed."
        Write-Info "Install it later from https://www.videolan.org/vlc/ - take the 64-bit"
        Write-Info "Windows installer. Everything except the live picture works without it."
    }
}

# --- 5. go2rtc ---------------------------------------------------------------
# Not in winget, and it is a single self-contained binary, so it is fetched
# straight from the project's own releases and kept inside bin\ rather than
# installed system-wide. Deleting bin\ undoes it completely.
Write-Step 5 "Checking go2rtc (re-serves the camera stream to the console)"
$binDir = Join-Path $root 'bin'
$go2rtc = Join-Path $binDir 'go2rtc.exe'

if (Test-Path $go2rtc) {
    Write-Ok "go2rtc is already here: bin\go2rtc.exe"
} else {
    $asset = switch ($env:PROCESSOR_ARCHITECTURE) {
        'ARM64' { 'go2rtc_win_arm64.zip' }
        'x86'   { 'go2rtc_win32.zip' }
        default { 'go2rtc_win64.zip' }
    }
    $url = "https://github.com/AlexxIT/go2rtc/releases/latest/download/$asset"
    $zip = Join-Path $env:TEMP "vmd-$asset"
    Write-Info "Downloading $asset"
    try {
        New-Item -ItemType Directory -Force -Path $binDir | Out-Null
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath $binDir -Force
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
        if (Test-Path $go2rtc) { Write-Ok "go2rtc installed to bin\go2rtc.exe" }
        else { Write-Bad "The download unpacked but go2rtc.exe is not in bin\." }
    } catch {
        # A missing streamer does not invalidate the rest of the install: only
        # the live picture needs it. Say so and carry on.
        Write-Bad "Could not download go2rtc: $($_.Exception.Message)"
        Write-Info "Everything else still installs. Fetch it later from:"
        Write-Info "  https://github.com/AlexxIT/go2rtc/releases/latest"
    }
}

# --- 6. the environment ------------------------------------------------------
Write-Step 6 "Building the Python environment"
Write-Info "Fetching Python and the libraries at the versions in uv.lock."
Write-Info "This includes the detector stack, which is a large download."
Write-Info "The first run takes several minutes; later runs are seconds."
Push-Location $root
try {
    # --extra detect matters: a plain `uv sync` prunes anything not declared,
    # which would strip the detector out of an environment that had it.
    uv sync --extra detect | Out-Host
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

# --- 7. the single-file launcher ---------------------------------------------
Write-Step 7 "Building VMD.exe (the thing you double-click from now on)"
try {
    & (Join-Path $PSScriptRoot 'build_exe.ps1') | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "build failed" }
}
catch {
    # The exe is convenience, not function: VMD.bat starts the same console.
    Write-Bad "Could not build VMD.exe: $($_.Exception.Message)"
    Write-Info "Not a problem - double-click VMD.bat instead. It does the same thing."
}

# --- 8. start the console ----------------------------------------------------
Write-Step 8 "Starting the console"

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
Write-Host "  Recording service:  uv run python -m vmd.record_main" -ForegroundColor Gray
Write-Host "  Tests:              uv run pytest" -ForegroundColor Gray

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

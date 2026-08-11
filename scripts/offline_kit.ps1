# =============================================================================
#  Prepares the folder that goes to the laptop with no internet.
#
#  Run it on the connected machine, after install.bat has finished. Double-click
#  scripts\offline-kit.bat.
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
#  a plugin tree, and a DLL that python-vlc finds through the registry. It has
#  to be installed on the target, so its installer travels beside the project
#  and scripts\offline_install.ps1 runs it there.
# =============================================================================
param(
    [string]$To,
    [switch]$VerifyOnly
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
    @{ Path = $vlcInstaller;                                What = 'the VLC installer';                      Fix = 'see step 1 above' }
)

$missing = @()
foreach ($check in $checks) {
    if (Test-Path $check.Path) { Write-Ok "$($check.What)" }
    else { Write-Bad "MISSING - $($check.What)  ($($check.Fix))"; $missing += $check.What }
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
# What is left behind, and why:
#   recordings   footage of somebody's perimeter. Never travels by accident.
#   *.db         the segment and event indexes, which describe recordings that
#                will not be there.
#   settings.json  the camera's address and password. Typed on the laptop that
#                will use it, in the Settings tab, as everything else is.
#   *.pid, logs  what a run on this machine left behind. Meaningless there.
#   .git         history. The laptop is a deployment, not a working copy.
$robocopyArgs = @(
    $root, $To, '/E', '/R:1', '/W:1', '/NFL', '/NDL', '/NP',
    '/XD', (Join-Path $root 'recordings'), (Join-Path $root '.git'),
           (Join-Path $binDir 'logs'), (Join-Path $root 'build'),
    '/XF', 'settings.json', '*.pid', '*.db', '*.db-wal', '*.db-shm', '*.log'
)
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
Write-Host "  On the laptop with no internet:" -ForegroundColor White
Write-Host "    1. Copy the VMD folder from the USB drive to C:\VMD" -ForegroundColor Gray
Write-Host "    2. Open the folder called scripts inside it" -ForegroundColor Gray
Write-Host "    3. Double-click offline-install.bat" -ForegroundColor Gray
Write-Host ""
exit 0

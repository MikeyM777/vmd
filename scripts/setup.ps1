# =============================================================================
#  Setup VMD - the one thing to run. It does everything:
#
#    1. Checks the machine can run the console (uv, the .venv, the tools in bin\)
#       and repairs the .venv paths if the folder was moved or copied.
#    2. Sets up BOTH cameras in one go - address, name, login - and writes their
#       settings files.
#    3. Puts one "VMD" button on the desktop that opens both, side by side.
#    4. Offers to start them automatically when the PC turns on, so a power cut
#       costs nothing - both come back on their own, split-screen, each behind
#       the crash-watchdog.
#    5. Verifies the install and says, in plain words, what works and what is
#       left to do.
#
#  It is built to be run by someone who is not an engineer, on a machine with no
#  terminal, so it never ends on a raw error: anything that goes wrong is caught
#  and said in a sentence, and the window stays open. Every step that can fail on
#  its own is wrapped on its own, so a refused auto-start does not cost the camera
#  setup that already worked.
#
#  Driven by scripts\Setup VMD.bat, which is what the operator double-clicks. A
#  full record of the run, with the camera passwords redacted, is written to
#  bin\logs\setup.log.
# =============================================================================
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$root   = Get-ProjectRoot
$binDir = Join-Path $root 'bin'

# A record of the run, secrets redacted, for after the fact. Best-effort: a setup
# that cannot open a log file must still set the cameras up.
try { Start-VmdTranscript $root 'setup.log' | Out-Null } catch { }

function Pause-AtEnd {
    Write-Host ""
    try { Read-Host "  Press Enter to close this window" | Out-Null } catch { }
}

try {
    Write-Host ""
    Write-Host "  ============================================" -ForegroundColor Cyan
    Write-Host "   VMD setup - both cameras, one button" -ForegroundColor Cyan
    Write-Host "  ============================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Info "This sets up both cameras, puts one VMD button on the desktop, and can"
    Write-Info "make them start by themselves when the PC turns on. It takes a minute."

    Set-StepTotal 5

    # --- 1. Is this even the VMD folder? ------------------------------------
    Write-Step "Checking the VMD folder"
    if (-not (Test-Path (Join-Path $root 'vmd\desktop'))) {
        Write-Bad "This does not look like the VMD folder:"
        Write-Info "  $root"
        Write-Info "Keep this alongside the rest of VMD and run it from there."
        Pause-AtEnd
        Stop-VmdTranscript $root | Out-Null
        exit 1
    }
    Write-Ok "Found VMD in $root"

    # --- 2. Can the console actually run? -----------------------------------
    Write-Step "Checking the machine is ready to run the console"

    # bin\ on PATH: uv, ffmpeg and go2rtc all live there and are found by name.
    if (Test-Path $binDir) {
        try { Add-BinToUserPath $binDir | Out-Null } catch { }
        $env:Path = "$binDir;$env:Path"
    }

    # uv is the only thing that can run the environment.
    $uv = Join-Path $binDir 'uv.exe'
    if (-not (Test-Path $uv)) {
        $onPath = (Get-Command uv -ErrorAction SilentlyContinue).Source
        $uv = if ($onPath) { $onPath } else { $null }
    }

    $venvOk = Test-Path (Join-Path $root '.venv\Scripts\python.exe')
    if ($venvOk) {
        # Fix the .venv's idea of where it lives, in case the folder was copied or
        # moved - harmless when nothing needs fixing.
        try {
            $fixed = @(Repair-VenvPaths $root)
            foreach ($line in $fixed) { Write-Info $line }
        } catch { Write-Warn "Could not check the .venv paths: $($_.Exception.Message)" }
    }

    $canRun = [bool]$uv -and $venvOk
    if ($canRun) {
        Write-Ok "uv and the program environment are here."
    } else {
        Write-Warn "The program is not fully installed yet on this machine:"
        if (-not $uv)     { Write-Info "  - uv (the thing that runs it) was not found." }
        if (-not $venvOk) { Write-Info "  - the .venv environment is missing." }
        Write-Info "You can still set the cameras up now. To finish installing, run"
        Write-Info "install.bat (with internet) or offline-install.bat (from the stick)"
        Write-Info "once, then run this again - the cameras you set up now are kept."
    }

    # The tools a live picture and recording need. Reported, never fatal: a
    # camera can be set up before its tools are in place.
    if (Test-Path (Join-Path $binDir 'go2rtc.exe')) { Write-Ok "go2rtc is here (needed for the live picture)." }
    else { Write-Warn "go2rtc.exe is missing from bin\ - no live picture until the install is finished." }
    if (Test-Path (Join-Path $binDir 'ffmpeg.exe')) { Write-Ok "ffmpeg is here (needed to record)." }
    else { Write-Warn "ffmpeg.exe is missing from bin\ - recording will not run until the install is finished." }
    try {
        $vlc = Get-VlcInstall
        if ($vlc.Found) { Write-Ok "VLC is here (needed for the live picture)." }
        elseif ($vlc.Only32) { Write-Warn "VLC is installed but 32-bit; the console needs the 64-bit one." }
        elseif ($vlc.NoPlugins) { Write-Warn "VLC is here but missing its plugins folder - reinstall VLC." }
        else { Write-Warn "VLC was not found - no live picture until VLC (64-bit) is installed." }
    } catch { Write-Warn "Could not check for VLC: $($_.Exception.Message)" }

    # --- 3. The cameras -----------------------------------------------------
    Write-Step "Setting up the cameras"
    # Reuse the camera functions without running cameras.ps1's own menu.
    . (Join-Path $PSScriptRoot 'cameras.ps1') -AsModule
    $setup = Set-BothCameras
    if ($setup -ne 0) {
        Write-Warn "No cameras were set up. You can run this again any time."
        Pause-AtEnd
        Stop-VmdTranscript $root | Out-Null
        exit 0
    }

    # --- 4. Start on power-up? ----------------------------------------------
    Write-Step "Starting by themselves when the PC turns on"
    Write-Info "Recommended: if the power blinks or the PC restarts, both cameras come"
    Write-Info "back on their own, side by side, without anyone touching them."
    $auto = (Read-Host "  Turn that on now? [Y/n]").Trim().ToLower()
    if ($auto -ne 'n') {
        try {
            # A separate process so its own elevation or exit cannot end this
            # script. -NoElevate keeps it from popping a UAC box: if Windows will
            # not grant scheduled tasks, it falls back to the Startup folder,
            # which needs no permission.
            $autostart = Join-Path $PSScriptRoot 'autostart.ps1'
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $autostart -Install -NoElevate
            Write-Ok "Auto-start is on. Both cameras will open at sign-in."
        } catch {
            Write-Warn "Could not turn on auto-start: $($_.Exception.Message)"
            Write-Info "The VMD button on the desktop still opens both any time."
        }
    } else {
        Write-Info "Left off. Open the cameras with the VMD button on the desktop."
    }

    # --- 5. Check it works --------------------------------------------------
    Write-Step "Checking everything is in place"
    if ($canRun) {
        $allOk = $true
        foreach ($console in @(Get-VmdConsoles $root)) {
            $name = Split-Path (Split-Path $console.Settings -Parent) -Leaf
            try {
                $probe = Invoke-Captured $uv @(
                    'run', '--offline', '--frozen', '--no-sync',
                    'python', '-m', 'vmd.selftest', '--settings', $console.Settings
                )
                if ($probe.Code -eq 0) {
                    Write-Ok "Camera $name - the console starts and its settings are valid."
                    foreach ($line in $probe.Out) {
                        if ($line -match 'VLC|no live picture') { Write-Info "    $line" }
                    }
                } else {
                    $allOk = $false
                    Write-Warn "Camera $name - the self-check reported a problem:"
                    foreach ($line in ($probe.Out + $probe.Err)) {
                        if ($line -and $line -match 'selftest') { Write-Info "    $line" }
                    }
                }
            } catch {
                $allOk = $false
                Write-Warn "Camera $name - the self-check could not run: $($_.Exception.Message)"
            }
        }
        if ($allOk) { Write-Ok "Self-check passed." }
    } else {
        Write-Info "Skipped - the program is not fully installed yet (see step 2)."
    }

    # --- what to do next ----------------------------------------------------
    Write-Host ""
    Write-Host "  Done." -ForegroundColor Green
    Write-Host ""
    Write-Info "To finish, ONCE per camera:"
    Write-Info "  1. Double-click the VMD button on the desktop (opens both)."
    Write-Info "  2. In each console: Settings tab -> type the address of each picture"
    Write-Info "     the camera shows (its stream), and the login if you skipped it."
    Write-Info "     Press Save. The picture starts as soon as it is saved."
    Write-Info "  3. To record too, tick 'Record everything to disk' in Storage, Save."
    Write-Host ""
    Write-Info "That is all. After that, the VMD button - or a restart, if you turned"
    Write-Info "auto-start on - brings both cameras up on their own."

    Pause-AtEnd
    Stop-VmdTranscript $root | Out-Null
    exit 0
}
catch {
    Write-Host ""
    Write-Bad "Something went wrong during setup:"
    Write-Info "  $($_.Exception.Message)"
    Write-Info "Nothing that already succeeded was undone. You can run this again."
    Pause-AtEnd
    try { Stop-VmdTranscript $root | Out-Null } catch { }
    exit 1
}

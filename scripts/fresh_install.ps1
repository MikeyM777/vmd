# =============================================================================
#  Installs VMD on the offline PC from the beginning, as if the PC were new.
#
#  Double-click FRESH-INSTALL.bat in the VMD folder of the offline kit, on the
#  USB stick. The kit is what OfflineSetup.bat makes on the connected machine:
#  the program AND everything it runs on - Python, the .venv, uv, ffmpeg, go2rtc,
#  the VLC installer. A download of the code from GitHub is not enough, and this
#  refuses one in its first step rather than half-way through.
#
#  What it does, one line each:
#
#    1. checks the stick holds a whole kit
#    2. stops the console, the recorder, go2rtc and ffmpeg
#    3. moves the old C:\VMD aside to C:\VMD-old-<date> - moved, not deleted
#    4. copies the kit to C:\VMD
#    5. puts the camera setup back (settings.json, detection.json, cameras\)
#    6. runs the ordinary offline installer (scripts\offline_install.ps1) on it
#
#  Why the old folder is moved and not deleted: a rename on the same drive is
#  instant and cannot half-happen, and if the copy fails the old folder is put
#  straight back - the PC is never left with no VMD at all. It also keeps the
#  recordings and the old logs where somebody can still reach them. Delete
#  C:\VMD-old-* by hand once the new install has been seen working.
#
#  Why the camera setup is put back: the person standing at the PC does not know
#  the camera passwords. -NoKeepSettings starts from an empty setup instead, and
#  then cameras.bat has to be run once per camera.
#
#  Ends on a green DONE or a red FAILED, big enough to photograph.
#
#  Windows PowerShell 5.1 only: nothing newer is certain to be on that PC.
# =============================================================================
param(
    [string]$Target = 'C:\VMD',
    [switch]$NoKeepSettings
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

# The kit's own folder: this script is <kit>\scripts\fresh_install.ps1.
$Kit = Split-Path -Parent $PSScriptRoot

# What is carried from the old install to the new one. The camera setup and
# nothing else: these are in KEEP_OUT in vmd\update\apply.py for the same reason
# - they are this site's, not the program's.
$CARRY = @('settings.json', 'detection.json', 'cameras')

$script:LogLines = New-Object System.Collections.ArrayList

function Write-Log($message, $color) {
    $stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    [void]$script:LogLines.Add("$stamp  $message")
    if ($color) { Write-Host "   $message" -ForegroundColor $color }
    else { Write-Host "   $message" }
}

function Save-Log {
    # Beside the kit on the stick, because the stick is what travels back to
    # whoever can read it. Best-effort: a log is never a reason to fail.
    $text = ($script:LogLines -join "`r`n")
    foreach ($path in @((Join-Path (Split-Path -Parent $Kit) 'fresh-install-log.txt'),
                        (Join-Path $Target 'bin\logs\fresh-install-log.txt'))) {
        try {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $path) | Out-Null
            [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
        } catch { }
    }
}

function Show-Banner($lines, $fg, $bg) {
    Write-Host ""
    Write-Host ("  " + (" " * 68)) -BackgroundColor $bg
    foreach ($line in $lines) {
        Write-Host ("  $line".PadRight(70)) -ForegroundColor $fg -BackgroundColor $bg
    }
    Write-Host ("  " + (" " * 68)) -BackgroundColor $bg
    Write-Host ""
}

function Fail($why) {
    Write-Log "FAILED: $why" Red
    Save-Log
    Show-Banner @('FAILED', '', $why, '', 'Take a photo of this window.') White DarkRed
    exit 1
}

function Step($n, $text) {
    Write-Host ""
    Write-Host "  [$n/6] $text" -ForegroundColor Cyan
    [void]$script:LogLines.Add("--- [$n/6] $text")
}

function Stop-Everything {
    # The project's own stopper knows which processes are this install's and
    # spares everything else; the plain taskkill is for when it will not load.
    $common = Join-Path $Target 'scripts\_common.ps1'
    $stopped = $false
    if (Test-Path $common) {
        try {
            . $common
            $result = Stop-ProjectProcesses $Target
            Write-Log "Stopped: $(@($result.Stopped) -join ', ')"
            $stopped = $true
        } catch {
            Write-Log "The project's stopper would not run ($($_.Exception.Message))."
        }
    }
    # Always, not only as a fallback: a leftover ffmpeg or go2rtc holding a file
    # open is the one thing that stops the folder being moved. Anything whose
    # program lives inside the install - VMD.exe, the python.exe of the console
    # and the recorder, go2rtc, ffmpeg - and nothing else on the PC.
    try {
        Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($Target + '\', 'OrdinalIgnoreCase') } |
            ForEach-Object {
                Write-Log "Stopping $($_.Name) (pid $($_.ProcessId))"
                try { & taskkill /F /T /PID $_.ProcessId 2>$null | Out-Null } catch { }
            }
    } catch { }
    # The scheduled tasks restart the recorder, so they are ended too. They are
    # set up again by the installer in step 6.
    foreach ($task in @(Get-ScheduledTask -ErrorAction SilentlyContinue |
            Where-Object { $_.TaskName -like 'VMD Recorder*' -or $_.TaskName -like 'VMD Console*' })) {
        try { Stop-ScheduledTask -TaskName $task.TaskName -ErrorAction Stop } catch { }
    }
    Start-Sleep -Seconds 2
}

Write-Host ""
Write-Host "  VMD - fresh install" -ForegroundColor White
Write-Host "  from $Kit" -ForegroundColor DarkGray
Write-Host "  to   $Target" -ForegroundColor DarkGray
Write-Log "Kit: $Kit  Target: $Target"

# =============================================================================
Step 1 "Checking the stick holds a whole kit"
# =============================================================================
$need = @('VERSION', 'vmd\desktop\app.py', 'bin\uv.exe', 'bin\ffmpeg.exe', 'bin\go2rtc.exe',
          '.venv\Scripts\python.exe', 'scripts\offline_install.ps1')
$missing = @($need | Where-Object { -not (Test-Path (Join-Path $Kit $_)) })
if ($missing.Count -gt 0) {
    Write-Log "Missing from the kit: $($missing -join ', ')"
    Fail "This is not a whole offline kit (no $($missing[0]))."
}
$kitFull = [IO.Path]::GetFullPath($Kit).TrimEnd('\')
$targetFull = [IO.Path]::GetFullPath($Target).TrimEnd('\')
if ($kitFull -ieq $targetFull -or $kitFull.StartsWith($targetFull + '\', 'OrdinalIgnoreCase')) {
    Fail "Run this from the USB stick, not from $Target."
}
$kitVersion = "$(Get-Content (Join-Path $Kit 'VERSION') -Raw)".Trim()
Write-Log "The kit is VMD $kitVersion." Green

# =============================================================================
Step 2 "Stopping VMD"
# =============================================================================
$hadOld = Test-Path $Target
if ($hadOld) {
    Stop-Everything
    Write-Log "Stopped." Green
} else {
    Write-Log "There is no $Target yet - nothing to stop."
}

# =============================================================================
Step 3 "Moving the old VMD aside"
# =============================================================================
$old = $null
if ($hadOld) {
    $old = "$Target-old-" + (Get-Date).ToString('yyyyMMdd-HHmmss')
    $moved = $false
    for ($try = 1; $try -le 5 -and -not $moved; $try++) {
        try {
            Rename-Item -Path $Target -NewName (Split-Path -Leaf $old) -ErrorAction Stop
            $moved = $true
        } catch {
            Write-Log "Try $try could not move it ($($_.Exception.Message)); stopping again."
            Stop-Everything
            Start-Sleep -Seconds 3
        }
    }
    if (-not $moved) {
        Fail "Could not move $Target - something still has it open. Restart the PC and run this again."
    }
    Write-Log "Moved to $old" Green
} else {
    Write-Log "Nothing to move."
}

# =============================================================================
Step 4 "Copying VMD $kitVersion to $Target (a few minutes)"
# =============================================================================
& robocopy $Kit $Target /E /R:2 /W:2 /MT:8 /NFL /NDL /NJH /NP /XF 'fresh-install-log.txt' | Out-Null
$code = $LASTEXITCODE
# robocopy: 0-7 is success of one kind or another, 8 and up is a failure.
if ($code -ge 8) {
    Write-Log "robocopy exit $code"
    if ($old) {
        try {
            if (Test-Path $Target) { Remove-Item $Target -Recurse -Force -ErrorAction Stop }
            Rename-Item -Path $old -NewName (Split-Path -Leaf $Target) -ErrorAction Stop
            Write-Log "The old VMD was put back as it was." Yellow
        } catch {
            Write-Log "Could not put the old VMD back: $($_.Exception.Message). It is at $old." Red
        }
    }
    Fail "The copy from the stick failed (robocopy $code). The old VMD is back."
}
Write-Log "Copied." Green

# =============================================================================
Step 5 "Putting the camera setup back"
# =============================================================================
if ($NoKeepSettings) {
    Write-Log "Not kept, as asked. Run cameras.bat once for each camera." Yellow
} elseif (-not $old) {
    Write-Log "No old install, so no setup to keep. Run cameras.bat once for each camera." Yellow
} else {
    foreach ($name in $CARRY) {
        $from = Join-Path $old $name
        if (Test-Path $from) {
            try {
                Copy-Item -Path $from -Destination (Join-Path $Target $name) -Recurse -Force -ErrorAction Stop
                Write-Log "Kept $name" Green
            } catch {
                Write-Log "Could not keep ${name}: $($_.Exception.Message)" Red
            }
        }
    }
}

# =============================================================================
Step 6 "Running the offline installer"
# =============================================================================
$installer = Join-Path $Target 'scripts\offline_install.ps1'
& powershell -NoProfile -ExecutionPolicy Bypass -File $installer
$installCode = $LASTEXITCODE
Write-Log "offline_install.ps1 exit $installCode"

$landed = "$(Get-Content (Join-Path $Target 'VERSION') -Raw -ErrorAction SilentlyContinue)".Trim()
Save-Log
if ($installCode -ne 0) {
    Show-Banner @('FAILED', '', "VMD $landed was copied, but the installer reported a problem.",
                  'Scroll up: the red lines say what.', '',
                  "The old VMD is kept at $old", 'Take a photo of this window.') White DarkRed
    exit 1
}
$lines = @('DONE', '', "VMD $landed is installed fresh in $Target.")
if ($old) { $lines += "The old one is kept at $old - delete it once this works." }
Show-Banner $lines Black Green
exit 0

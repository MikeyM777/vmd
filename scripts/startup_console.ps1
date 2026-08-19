# =============================================================================
#  Opens the console 45 seconds after sign-in.
#
#  Only used by the Startup-folder fallback in scripts\autostart.ps1, on a
#  machine where Windows refused to create the scheduled tasks. The task has the
#  delay built in - $consoleTrigger.Delay = 'PT45S' - and a shortcut has nothing
#  of the kind, so the wait lives here instead.
#
#  What the wait is for: the console starts a recorder of its own if it does not
#  find one, and it looks for one by reading recorder.pid. The recorder wrapper
#  starts at the same sign-in and writes that file. Opening the console first
#  means two recorders for one camera, and the second one has to notice and
#  stand down - which it does, but only after both have opened the camera.
#
#  Nothing here decides anything. If the console cannot start, the console says
#  so; this script's only job is to wait and then hand over.
# =============================================================================
param(
    [string]$Settings
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$root = Get-ProjectRoot

# The same log the recorder wrapper writes, for the same reason: after a power
# cut at three in the morning, this file is the only account of what the machine
# tried to do when it came back.
$logDir = Join-Path $root 'bin\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'autostart.log'

function Note($text) {
    $line = "{0}  {1}" -f (Get-Date -Format 's'), $text
    try { Add-Content -Path $log -Value $line -Encoding UTF8 } catch { }
    Write-Host $line
}

$settings = if ($Settings) { $Settings } else { Join-Path $root 'settings.json' }

Note "autostart: console wrapper waiting 45s before opening $settings"
Start-Sleep -Seconds 45

# bin\ carries uv, and VMD.exe finds uv on PATH and nowhere else. The stored
# PATH normally has it already; setting it here as well is what makes the first
# sign-in after an install work, before anything has re-read the environment.
$binDir = Join-Path $root 'bin'
if (Test-Path $binDir) { $env:Path = "$binDir;$env:Path" }

$exe = Join-Path $root 'VMD.exe'
$bat = Join-Path $root 'VMD.bat'

try {
    if (Test-Path $exe) {
        Note "autostart: opening the console ($exe)"
        Start-Process -FilePath $exe -ArgumentList @('--settings', $settings) -WorkingDirectory $root | Out-Null
    } elseif (Test-Path $bat) {
        # VMD.exe is only ever missing on a folder that was never finished
        # building. VMD.bat does the same thing and is in the repository.
        Note "autostart: no VMD.exe, opening the console through VMD.bat"
        Start-Process -FilePath $bat -ArgumentList @('--settings', $settings) -WorkingDirectory $root | Out-Null
    } else {
        Note "autostart: neither VMD.exe nor VMD.bat is in $root - nothing to open."
    }
} catch {
    Note "autostart: the console could not be started: $($_.Exception.Message)"
}

exit 0

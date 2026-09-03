# =============================================================================
#  The VMD button: opens every camera at once, split across the one screen.
#
#  This machine watches two streets with two cameras, and the operator wanted
#  one thing to double-click that brings both up side by side - not two icons to
#  find and two windows to drag into place. This is that one thing: the desktop
#  "VMD" shortcut points here.
#
#  Each camera is opened through scripts\run_console.ps1 - the watchdog - so a
#  console that crashes comes straight back on its own half of the screen, and a
#  console closed on purpose stays closed. Two watchdogs are started, one per
#  camera; this script's own job is done the moment both are launched, so it
#  does not wait for them.
#
#  The order is the folder order, which for these two - 250 and 251 - is the
#  order they read: the first fills the LEFT half, the second the RIGHT. A single
#  camera fills the screen the way it always has. A machine that somehow has more
#  than two opens the first two split and the rest wherever they were left, with
#  a line in the log saying so, because half a screen cannot be cut three ways.
# =============================================================================
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$root = Get-ProjectRoot

$logDir = Join-Path $root 'bin\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'autostart.log'
function Note($text) {
    $line = "{0}  {1}" -f (Get-Date -Format 's'), $text
    try { Add-Content -Path $log -Value $line -Encoding UTF8 } catch { }
    Write-Host $line
}

$watchdog = Join-Path $PSScriptRoot 'run_console.ps1'

# Every camera set up in this folder, in a stable order. Get-VmdConsoles reads
# cameras\*/settings.json and falls back to the plain settings.json beside the
# program when there are no camera folders - which is the single-camera install,
# and it opens exactly as it used to. Sorted by settings path so 250 comes before
# 251 however the filesystem hands them back.
$consoles = @(Get-VmdConsoles $root | Sort-Object -Property Settings)

if ($consoles.Count -eq 0) {
    Note "VMD button: nothing to open - no cameras are set up. Run cameras.bat."
    exit 1
}

# One screen, cut in half: the first camera on the left, the second on the right.
# Only meaningful when there is more than one; a lone console is given no half and
# opens full, the way a single-camera machine always has.
$places = if ($consoles.Count -ge 2) { @('left', 'right') } else { @('') }

for ($i = 0; $i -lt $consoles.Count; $i++) {
    $console = $consoles[$i]
    $place = if ($i -lt $places.Count) { $places[$i] } else { '' }

    # One quoted string, not an array: Start-Process does not quote array parts,
    # so a settings path with a space would reach the watchdog split in two. The
    # -File and -Settings paths are quoted here for that reason.
    $argString = ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Settings "{1}"' -f $watchdog, $console.Settings)
    if ($place) { $argString += " -Place $place" }

    $where = if ($place) { "on the $place" } else { "full screen" }
    Note ("VMD button: opening {0} {1}" -f $console.Settings, $where)
    # No -Wait: each watchdog runs for as long as its console should, and this
    # launcher has no reason to sit behind them. Hidden, so the only windows that
    # appear are the consoles themselves.
    Start-Process -FilePath 'powershell.exe' -ArgumentList $argString -WorkingDirectory $root -WindowStyle Hidden | Out-Null
}

if ($consoles.Count -gt 2) {
    Note ("VMD button: {0} cameras are set up, but a screen only splits in two; " +
          "the extra ones opened where they were last left." -f $consoles.Count)
}

exit 0

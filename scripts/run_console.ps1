# =============================================================================
#  Opens the console once. Does NOT reopen it.
#
#  This was a watchdog: it launched the console in a loop and brought it straight
#  back whenever it exited non-zero, so a libVLC segfault (two hardware decoders
#  in one process was the field cause, since fixed by software decode in
#  vmd\desktop\video.py) did not leave a black screen on a machine watching a
#  perimeter. The loop was removed at the operator's request: a window he closed
#  kept reopening, and a console closed on purpose must stay closed.
#
#  So now it opens the console exactly once and exits with the console's own
#  code. Closed on purpose or fallen over, it stays closed either way - there is
#  no auto-recovery here any more, by choice. The 45-second wait that lets the
#  recorder claim recorder.pid before the console looks for it still lives on the
#  scheduled task's trigger (autostart.ps1) and in startup_console.ps1, not here.
# =============================================================================
param(
    [string]$Settings,
    # Which half of the screen this console fills, for the one-monitor two-camera
    # layout the VMD button opens. Empty means the console places itself the way
    # it always has (remembered geometry, or --screen). Passed straight through to
    # the console as --place, and re-applied on every reopen so a crash-and-return
    # lands back on the same half.
    [ValidateSet('', 'left', 'right')]
    [string]$Place = ''
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$root = Get-ProjectRoot

# The same log the recorder and the startup wrapper write to, so the account of
# what the machine did overnight is in one place: "the console crashed and was
# reopened" belongs beside "recording started" and "adopted an earlier run".
$logDir = Join-Path $root 'bin\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'autostart.log'

function Note($text) {
    $line = "{0}  {1}" -f (Get-Date -Format 's'), $text
    try { Add-Content -Path $log -Value $line -Encoding UTF8 } catch { }
    Write-Host $line
}

$settings = if ($Settings) { $Settings } else { Join-Path $root 'settings.json' }

# bin\ carries uv, which VMD.exe finds on PATH and nowhere else, and ffmpeg,
# which the console's recorder starts by bare name. Set here for the same reason
# startup_console.ps1 sets it: the first sign-in after an install runs before
# anything has re-read the stored environment.
$binDir = Join-Path $root 'bin'
if (Test-Path $binDir) { $env:Path = "$binDir;$env:Path" }

# Tell the launcher (vmd\launcher.py) it is unattended. Without this a start
# failure stops at "Press Enter to close" and waits for a keypress that never
# comes on a machine opened hidden by a scheduled task - an invisible hang
# instead of a window that closes with the reason in the log below. Set in this
# process's environment, which VMD.exe inherits when Start-Process launches it.
$env:VMD_SUPERVISED = '1'

$exe = Join-Path $root 'VMD.exe'
$bat = Join-Path $root 'VMD.bat'

# The arguments every launch is given, as ONE quoted string, not an array.
# Start-Process joins an array with spaces and does not quote the parts, so a
# settings path with a space in it - which happens the moment this folder lives
# under "C:\Program Files\..." or a Hebrew user name with a space - would reach
# the console split in two. The quoted string is the same shape autostart.ps1
# uses for exactly this reason. --place is added only when a half was asked for.
$argString = ('--settings "{0}"' -f $settings)
if ($Place) { $argString += " --place $Place" }

# How the console is launched, chosen once. VMD.exe is the built console; VMD.bat
# is only for a folder that was copied before it was finished building, and it
# does the same thing through uv.
if (Test-Path $exe) {
    $launch = { Start-Process -FilePath $exe -ArgumentList $argString -WorkingDirectory $root -PassThru -Wait }
    $what = $exe
} elseif (Test-Path $bat) {
    $launch = { Start-Process -FilePath $bat -ArgumentList $argString -WorkingDirectory $root -PassThru -Wait }
    $what = $bat
} else {
    Note "watchdog: neither VMD.exe nor VMD.bat is in $root - nothing to open."
    exit 1
}

# One launch, and no reopening. The console used to be opened in a loop here so
# a crash came straight back; that was removed at the operator's request - a
# window he closed kept coming back, and a console closed on purpose must stay
# closed. So it is opened exactly once: closed on purpose or fallen over in a
# heap, it stays closed either way, and its own exit code is passed back for the
# log and for whatever launched this.
Note "launcher: opening the console once ($what), settings $settings"

$startedAt = Get-Date
try {
    $process = & $launch
    $code = $process.ExitCode
} catch {
    # Start-Process itself refused - the file vanished mid-run, a permission
    # changed. Nothing to do but say so; it is not reopened.
    Note "launcher: could not open the console: $($_.Exception.Message)"
    exit 1
}

$ranFor = ((Get-Date) - $startedAt).TotalSeconds
if ($code -eq 0) {
    Note ("launcher: the console was closed (ran {0:N0}s)." -f $ranFor)
} else {
    Note ("launcher: the console stopped after {0:N0}s (exit {1}); it is not reopened." -f $ranFor, $code)
}
exit $code

# =============================================================================
#  The watchdog. Keeps the console open for as long as the machine is on.
#
#  The console draws its picture with libVLC, which is a C library running inside
#  this one process. When libVLC corrupts memory - two hardware decoders sharing
#  one process was the field cause, since fixed by moving to software decode in
#  vmd\desktop\video.py - the whole console dies, and a segfault is not something
#  Python can catch. Before this script the console was launched once and never
#  restarted: the scheduled task's own -RestartCount gave up after three tries a
#  minute apart, and the Startup-folder fallback did not restart at all. Either
#  way a crash left a black screen until a person walked over and reopened it,
#  which on a machine watching a perimeter is the one failure it may not have.
#
#  So the console is launched in a loop instead. The rule is simple and it is the
#  whole of the file:
#
#    * The console exited 0  -> somebody closed it on purpose. Stop, and let it
#      stay closed. Relaunching a window the operator just shut would be its own
#      kind of broken.
#    * The console exited non-zero (a crash, a fast-fail, anything) -> bring it
#      straight back, and log that it happened.
#
#  It never gives up. A console that crashes the instant it opens - a bad build,
#  a settings file it cannot read - would otherwise spin as fast as Windows can
#  spawn a process; the backoff below stops that from becoming a busy loop, but
#  it is a ceiling on how OFTEN it retries, never a limit on WHETHER it does. On
#  this deployment "stop trying" is never the right answer: the camera is still
#  up, and the next attempt may be the one that comes back.
#
#  The first open is immediate. The 45-second wait that lets the recorder claim
#  recorder.pid before the console looks for it lives on the scheduled task's
#  trigger (autostart.ps1) and in startup_console.ps1, not here - a crash at
#  three in the morning must not cost another 45 seconds of black screen on top
#  of the crash.
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

# Tell the launcher (vmd\launcher.py) it is being supervised. Without this a
# crash stops at "Press Enter to close" and waits for a keypress that never
# comes on an unattended machine, freezing this watchdog on the first crash -
# the exact black screen it exists to prevent. Set in this process's
# environment, which VMD.exe inherits when Start-Process launches it below.
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

# The backoff, and what it is a ceiling on. A console that ran for a while and
# then crashed is the ordinary case and comes straight back (SHORT_WAIT). A
# console that dies before it could have drawn anything - under HEALTHY_SECONDS -
# is failing on start, and each such failure widens the wait, up to MAX_WAIT, so
# a broken build does not spawn processes as fast as the machine can. A launch
# that stayed up past HEALTHY_SECONDS resets the widening: it earned it.
$SHORT_WAIT = 3
$MAX_WAIT = 30
$HEALTHY_SECONDS = 30

$wait = $SHORT_WAIT
Note "watchdog: keeping the console open ($what), settings $settings"

while ($true) {
    $startedAt = Get-Date
    $code = $null
    try {
        $process = & $launch
        $code = $process.ExitCode
    } catch {
        # Start-Process itself refused - the file vanished mid-run, a permission
        # changed. Treated exactly like a crash: log it and try again, because
        # the thing that refused may be back on the next pass.
        Note "watchdog: could not launch the console: $($_.Exception.Message)"
        $code = -1
    }

    $ranFor = ((Get-Date) - $startedAt).TotalSeconds

    if ($code -eq 0) {
        Note ("watchdog: the console was closed on purpose (ran {0:N0}s); leaving it closed." -f $ranFor)
        break
    }

    if ($ranFor -ge $HEALTHY_SECONDS) {
        # It was up and working, then fell over. Common, and it comes straight
        # back; the widening from any earlier crash-on-start is forgiven.
        $wait = $SHORT_WAIT
        Note ("watchdog: the console stopped after {0:N0}s (exit {1}); reopening in {2}s." -f $ranFor, $code, $wait)
    } else {
        Note ("watchdog: the console stopped after only {0:N0}s (exit {1}); reopening in {2}s." -f $ranFor, $code, $wait)
    }

    Start-Sleep -Seconds $wait

    # Widen only after a launch that did not stay up, and only up to the cap.
    if ($ranFor -lt $HEALTHY_SECONDS) {
        $wait = [Math]::Min($wait * 2, $MAX_WAIT)
    }
}

exit 0

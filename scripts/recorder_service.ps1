# =============================================================================
#  Starts the recorder at logon and stays with it until it exits.
#
#  Run by the "VMD Recorder" scheduled task, never by hand. Its whole reason to
#  exist is that recording must not wait for somebody to open the console:
#  after a Windows update, a power cut or any other reboot, the perimeter was
#  previously unrecorded until a human logged in and double-clicked.
#
#  ---------------------------------------------------------------------------
#  What this script deliberately does NOT do: touch recorder.pid
#  ---------------------------------------------------------------------------
#
#  It used to write that file, and that was a real defect - the same one that
#  produced the console's respawn storm, arriving at the same place by a
#  different road.
#
#  `.venv\Scripts\python.exe` is not the interpreter. It is a trampoline that
#  launches the real interpreter as a child of itself. Measured here:
#
#      Start-Process reported pid 47196   (.venv\Scripts\python.exe)
#      the process itself reported 9440   (bin\python\...\python.exe)
#      and 9440's parent is 47196
#
#  So `$process.Id` is the launcher's number, never the recorder's. Writing it
#  into recorder.pid told the recorder that a recorder was already running -
#  and the lie passed every check, because the launcher really is alive and its
#  image really is python.exe. The recorder would stand down to its own
#  launcher, exit, take the launcher with it, and at logon this laptop would
#  come up with recording that never starts.
#
#  The fix is not a better identity check here. It is that this script has no
#  business writing that file at all. vmd\record_main.py owns the claim: it
#  writes os.getpid() - the real number - along with a companion .json holding
#  its interpreter, its settings path and a timestamp, and it reads all of that
#  back through `running_recorder`, which already handles a claim left over
#  from before the last boot and a PID Windows has handed out again. Two
#  writers of one claim, neither owning it, is what produced this.
#  vmd\desktop\services.py has been changed the same way and now sets
#  `claims_own_pid = True` rather than writing the file for the recorder.
#
#  So: start it, and let it decide. If another recorder already holds the
#  claim, this one exits ALREADY_RECORDING_EXIT (3), which is a correct
#  outcome and not something to retry.
#
#  This script does not restart the recorder if it dies. The recorder
#  supervises its own ffmpeg processes, and the console supervises the recorder
#  whenever it is open. A third supervisor here would be a third opinion about
#  who owns the recording directory.
# =============================================================================

param(
    # Which console's settings to record for. There is one camera folder per
    # camera now - cameras¨\settings.json, cameras©\settings.json - and
    # one of these tasks per camera. Empty means the single-camera layout, which
    # is settings.json beside VMD.exe and is what every existing installation
    # has.
    [string]$Settings
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$root = Get-ProjectRoot

# Kept in step with vmd\record_main.py's ALREADY_RECORDING_EXIT. Not imported,
# because importing it would mean starting Python to find out how to start
# Python; if it ever changes, the log line below is what will look wrong.
$ALREADY_RECORDING_EXIT = 3

# bin\ carries ffmpeg, which the recorder invokes by bare name. The scheduled
# task inherits the stored PATH, which normally has bin\ in it already, but
# putting it in explicitly means this works on the first boot after an install,
# before anything has re-read the environment.
$binDir = Join-Path $root 'bin'
if (Test-Path $binDir) { $env:Path = "$binDir;$env:Path" }

# bin\ is the one folder .gitignore excludes wholesale, so a log written there
# never turns up as an untracked file in somebody's working tree.
$logDir = Join-Path $binDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'autostart.log'

function Note($text) {
    $line = "{0}  {1}" -f (Get-Date -Format 's'), $text
    try { Add-Content -Path $log -Value $line -Encoding UTF8 } catch { }
    Write-Host $line
}

# Keep the log from growing without limit on a machine that reboots for years.
try {
    if ((Test-Path $log) -and ((Get-Item $log).Length -gt 1MB)) {
        Move-Item $log "$log.old" -Force
    }
} catch { }

Note "autostart: recorder wrapper starting in $root for $(if ($Settings) { $Settings } else { 'settings.json' })"

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Note "autostart: no environment at $python - run install.bat. Nothing started."
    exit 0
}

# The recorder needs a camera and a place to put the video, and both of those
# come from settings.json, which does not exist until somebody has filled in
# the Settings tab once. Before that there is nothing to record and no error to
# report: say so and stop. A pre-flight, not a claim - the recorder decides
# everything that matters about whether it should run.
$settings = if ($Settings) { $Settings } else { Join-Path $root 'settings.json' }
if (-not (Test-Path $settings)) {
    Note "autostart: no settings file at $settings, so there is no camera to record. Nothing started."
    exit 0
}

# One log per camera, because two recorders writing one file interleave their
# lines and the result answers nothing about either of them. The name is the
# camera's folder - 250, 251 - and the plain names are kept for the
# single-camera layout so that nothing anybody has been told to look at moves.
$label = ''
$parent = Split-Path -Parent $settings
if ($parent -and (Split-Path -Leaf (Split-Path -Parent $parent)) -eq 'cameras') {
    $label = '.' + (Split-Path -Leaf $parent)
}
$outLog = Join-Path $logDir "recorder$label.out.log"
$errLog = Join-Path $logDir "recorder$label.err.log"

# -u because Python block-buffers stdout when it is a pipe or a file, and a
# recorder that says one line a minute would otherwise fill eight kilobytes
# before anything reached the log - which is most of an hour of not knowing.
#
# No --pid-file: the default is recorder.pid beside the settings, which is the
# same file the console reads. Naming it here would only be a second place for
# the two to disagree.
$process = Start-Process -FilePath $python `
    -ArgumentList @('-u', '-m', 'vmd.record_main', '--settings', $settings) `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -PassThru

# Touching .Handle keeps .NET's handle to the process open. Without it the
# handle is closed when the process ends and .ExitCode reads back empty, which
# turns the one line that says why recording stopped into a line that says
# nothing. Verified that an exit code does survive the trampoline: 0, 1 and 3
# all came back unchanged.
$null = $process.Handle

# Said as "launcher", and the recorder's own number is not guessed at here.
# Calling this "the recorder (pid N)" is what made the old defect invisible:
# the operator would compare N against recorder.pid, find two different
# numbers, and have no way to know which was lying.
Note "autostart: recorder launched (launcher pid $($process.Id)); it writes its own pid into recorder.pid; logs in bin\logs\"

# Staying alive until the recorder exits is what lets the scheduled task show
# "Running" for as long as recording is happening, which is the one place a
# non-technical operator can look and see the truth. The launcher lives exactly
# as long as the recorder under it, so waiting on it is waiting on the recorder.
$process.WaitForExit()
$code = $process.ExitCode

if ($code -eq $ALREADY_RECORDING_EXIT) {
    # The right outcome, not a failure: the console got there first and its
    # recorder holds the claim. Exit 0 so Task Scheduler's Last Run Result does
    # not show an error for a machine that is recording perfectly well.
    Note "autostart: another recorder already holds the claim, so this one stood down. Recording is running; nothing is wrong."
    exit 0
}

if ($code -eq 0) { Note "autostart: recorder exited normally (code 0)" }
else { Note "autostart: recorder exited with code $code - see recorder.err.log" }
exit $code

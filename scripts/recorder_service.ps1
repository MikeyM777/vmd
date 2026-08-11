# =============================================================================
#  Starts the recorder at logon and stays with it until it exits.
#
#  Run by the "VMD Recorder" scheduled task, never by hand. Its whole reason to
#  exist is that recording must not wait for somebody to open the console:
#  after a Windows update, a power cut or any other reboot, the perimeter was
#  previously unrecorded until a human logged in and double-clicked.
#
#  Two things make it safe to start the recorder from outside the console.
#
#  1. It writes recorder.pid. vmd\desktop\services.py reads that file when the
#     console opens and *adopts* a live recorder rather than starting a second
#     one - and, crucially, leaves an adopted recorder alone when the console
#     closes. Without the PID file the console would start its own recorder,
#     and two recorders would write into the same directory and index it with
#     the same SQLite database. The PID file is what turns "started at logon"
#     into "started once".
#
#  2. It checks that the PID in that file really is this project's recorder,
#     not merely some process with that number. A PID file survives a reboot,
#     and Windows reuses PIDs freely; "there is a process with that id" is not
#     the same claim as "the recorder is running", and getting it wrong here
#     would mean silently not recording.
#
#  This script deliberately does not restart the recorder if it dies. The
#  recorder supervises its own ffmpeg processes, and the console supervises the
#  recorder whenever it is open. A third supervisor here would be a third
#  opinion about who owns the recording directory.
# =============================================================================

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$root = Get-ProjectRoot

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

Note "autostart: recorder wrapper starting in $root"

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Note "autostart: no environment at $python - run install.bat. Nothing started."
    exit 0
}

# The recorder needs a camera and a place to put the video, and both of those
# come from settings.json, which does not exist until somebody has filled in
# the Settings tab once. Before that there is nothing to record and no error to
# report: say so and stop.
$settings = Join-Path $root 'settings.json'
if (-not (Test-Path $settings)) {
    Note "autostart: no settings.json yet, so there is no camera to record. Nothing started."
    exit 0
}

$pidFile = Join-Path $root 'recorder.pid'

function Get-LiveRecorderPid {
    <#
      The PID in recorder.pid, but only if it is really this project's
      recorder. A number alone proves nothing: the file outlives a reboot and
      Windows hands the same numbers out again.
    #>
    if (-not (Test-Path $pidFile)) { return $null }
    $text = (Get-Content $pidFile -Raw -ErrorAction SilentlyContinue)
    $recordedPid = 0
    if (-not [int]::TryParse(($text -replace '\s', ''), [ref]$recordedPid)) { return $null }
    $process = Get-Process -Id $recordedPid -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    $path = $null
    try { $path = $process.Path } catch { }
    if ($path -and ($path -ieq $python)) { return $recordedPid }
    # A live process with our number that is not our interpreter is somebody
    # else's process wearing a recycled PID. Treat the file as stale.
    return $null
}

$existing = Get-LiveRecorderPid
if ($existing) {
    Note "autostart: a recorder is already running (pid $existing). Nothing started."
    exit 0
}

$outLog = Join-Path $logDir 'recorder.out.log'
$errLog = Join-Path $logDir 'recorder.err.log'

# -u because Python block-buffers stdout when it is a pipe or a file, and a
# recorder that says one line a minute would otherwise fill eight kilobytes
# before anything reached the log - which is most of an hour of not knowing.
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
# nothing.
$null = $process.Handle

Set-Content -Path $pidFile -Value $process.Id -Encoding ASCII
Note "autostart: recorder started (pid $($process.Id)); logs in bin\logs\"

# Staying alive until the recorder exits is what lets the scheduled task show
# "Running" for as long as recording is happening, which is the one place a
# non-technical operator can look and see the truth.
$process.WaitForExit()
Note "autostart: recorder exited with code $($process.ExitCode)"

# Only clear the file if it still names us. The console may have started its
# own recorder in the meantime and written its PID here, and deleting that
# would let the next console start a second one.
try {
    $stillOurs = (Get-Content $pidFile -Raw -ErrorAction SilentlyContinue) -replace '\s', ''
    if ($stillOurs -eq [string]$process.Id) { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }
} catch { }
exit 0

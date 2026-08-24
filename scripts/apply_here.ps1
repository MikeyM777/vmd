# =============================================================================
#  The fool-proof applier. Runs ON THE PC, from the update stick, and puts the
#  new version on with as little asked of the person standing there as it is
#  possible to ask: plug the stick in, double-click APPLY-UPDATE.bat, watch for
#  a green DONE or a red FAILED, and if it is red, photograph the window.
#
#  It is deliberately stubborn. Every step it takes has a fallback, and if the
#  first way fails it tries the next rather than stopping - because the person in
#  front of it cannot read a log, cannot open a terminal, and cannot be talked
#  through a repair down a telephone. So:
#
#    finding the install   the running console's own path, then C:\VMD, then the
#                          user's folder, then every drive
#    stopping the console  the project's own stopper, then the stick's copy of
#                          it, then a plain taskkill
#    copying the files     the bundled interpreter running the audited updater in
#                          copy-only mode, then a robocopy of the same whitelist,
#                          then a plain recursive copy
#    starting it again     the scheduled task run now, then the program launched
#                          straight, per console
#
#  Copy-only, and why it is safe here: this stick and the machine carry the same
#  libraries (uv.lock did not change), so there is nothing to install and no
#  reason to run the self-test that a subtly broken .venv could fail. That is the
#  whole difference from the in-console Update button, which runs both and, on
#  this machine, died doing so. The dangerous half - the backup, the marker, the
#  put-the-old-one-back-on-failure - is still the Python updater's; see
#  vmd\update\apply.py and the --copy-only mode in vmd\update\main.py.
#
#  It never touches the machine's own things. The copy, whichever engine does it,
#  is a whitelist: the program's own folders and files, and nothing named in
#  KEEP_OUT below - not settings, not cameras, not recordings, not the .venv, not
#  bin. tests\test_apply_here.py holds those two lists to the ones in
#  vmd\update\apply.py so they cannot drift apart.
#
#  The test seams (-CopyEngineOnly, -WhichRoot) let the pure parts of this be
#  driven from pytest without a real install, a real stick or a real console to
#  kill - the same way scripts\update_stick.ps1 is tested.
# =============================================================================
param(
    # The stick's root - E:\ - which holds update.json, manifest.json and files\.
    # Left empty when double-clicked: it is worked out from this script's own
    # location, which is <stick>\files\scripts\apply_here.ps1, so nothing has to
    # pass a drive letter on a command line (where a trailing "E:\" is misread as
    # E:" - the bug scripts\update_stick.ps1's Quote-ForChild exists for).
    [string]$StickRoot,
    # The install to update. Worked out if not given.
    [string]$Root,
    # Copy the files but do not start the console again - for the operator who
    # would rather restart the whole PC by hand afterwards.
    [switch]$NoRestart,
    # --- test seams. Each does one thing and stops, so pytest can drive the
    #     pure logic without a console to kill. See tests\test_apply_here.py. ---
    # Run ONLY the robocopy/copy fallback engine, from -Files into -Root, and
    # stop. No stop, no restart, no Python. Proves the whitelist protects the
    # machine's own things.
    [switch]$CopyEngineOnly,
    [string]$Files,
    # Print the install that would be chosen from a ';'-joined -Candidates list,
    # or the empty string if none is a VMD install, and stop.
    [switch]$WhichRoot,
    [string]$Candidates
)

# Native commands (robocopy, taskkill, schtasks) return non-zero for benign
# reasons, and on PowerShell 7 that becomes a terminating error under 'Stop'.
# The exit codes are read by hand where they matter, so this is switched off for
# the same reason scripts\_common.ps1 switches it off.
$PSNativeCommandUseErrorActionPreference = $false
$ErrorActionPreference = 'Stop'

# The two lists that make the copy safe. They are the ones in vmd\update\apply.py
# - COPY_IN and KEEP_OUT - and a test keeps them equal. Anything not in COPY_IN
# (or ending .bat) is not copied; anything in KEEP_OUT is never copied even if a
# stick carries it, which is what stands between an update and somebody's camera
# password.
$script:COPY_IN = @('vmd', 'scripts', 'docs', 'VERSION', 'pyproject.toml', 'uv.lock', 'VMD.exe')
$script:KEEP_OUT = @('settings.json', 'go2rtc.json', 'streaming.json', 'detection.json',
    'cameras', 'recordings', 'footage', 'clips', 'bin', '.venv', 'Ultralytics', 'previous')

# --- saying things, and keeping a copy of what was said -----------------------

$script:LogLines = New-Object System.Collections.ArrayList

function Write-Log($message, $color) {
    $stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    [void]$script:LogLines.Add("$stamp  $message")
    if ($color) { Write-Host "   $message" -ForegroundColor $color }
    else { Write-Host "   $message" }
}

function Save-Log($root, $stickRoot) {
    # To the stick first, because it travels back to whoever can read it, and to
    # the machine's own log folder second. Both best-effort: a log that could not
    # be written is never a reason to fail an update that worked.
    $text = ($script:LogLines -join "`r`n")
    $targets = @()
    if ($stickRoot) { $targets += (Join-Path $stickRoot 'apply-here-log.txt') }
    if ($root) { $targets += (Join-Path $root 'bin\logs\apply-here-log.txt') }
    foreach ($path in $targets) {
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
        $padded = "  $line".PadRight(70)
        Write-Host $padded -ForegroundColor $fg -BackgroundColor $bg
    }
    Write-Host ("  " + (" " * 68)) -BackgroundColor $bg
    Write-Host ""
}

# --- reading a version --------------------------------------------------------

function Get-VersionAt($path) {
    # The integer in a folder's VERSION file, or $null if there is not one or it
    # is not a number. Used both to describe the machine and to PROVE the update
    # landed: after the copy, this must read the stick's number.
    $file = Join-Path $path 'VERSION'
    if (-not (Test-Path $file)) { return $null }
    $text = "$(Get-Content $file -Raw -ErrorAction SilentlyContinue)".Trim()
    $value = 0
    if ([int]::TryParse($text, [ref]$value)) { return $value }
    return $null
}

function Test-IsVmdRoot($path) {
    # What makes a folder the thing this updates: a VERSION file and the vmd
    # package beside it. Not bin\python - a folder can be a VMD checkout worth
    # updating without the bundled interpreter, and the robocopy engine needs
    # neither.
    if (-not $path) { return $false }
    return (Test-Path (Join-Path $path 'VERSION')) -and
    (Test-Path (Join-Path $path 'vmd\__init__.py'))
}

# --- finding the install ------------------------------------------------------

function Get-RunningRoots {
    # The surest answer there is: the folder the console that is running right now
    # runs OUT of. Whatever it is called and wherever it sits, updating it is
    # updating the thing the operator is actually looking at. Read from the paths
    # of the processes this project starts - VMD.exe, the bundled interpreter, the
    # .venv interpreter - each of which fixes the root a different, known number
    # of folders up.
    $roots = @()
    foreach ($proc in (Get-Process -ErrorAction SilentlyContinue)) {
        $path = $null
        try { $path = $proc.Path } catch { }
        if (-not $path) { continue }
        if ($path -match '(?i)^(.+)\\VMD\.exe$') { $roots += $Matches[1] }
        elseif ($path -match '(?i)^(.+)\\bin\\python\\') { $roots += $Matches[1] }
        elseif ($path -match '(?i)^(.+)\\\.venv\\Scripts\\python(w)?\.exe$') { $roots += $Matches[1] }
    }
    return @($roots | Where-Object { $_ } | Select-Object -Unique)
}

function Get-CandidateRoots {
    # Every place a VMD install has been known to be, best guess first. The
    # running console wins; then the name it is installed under on this
    # deployment; then the user's own folder; then a sweep of every drive, which
    # is what catches an install on D:\ or a stick-built copy nobody told us
    # about.
    $c = @()
    $c += Get-RunningRoots
    $c += 'C:\VMD'
    if ($env:SystemDrive) { $c += (Join-Path "$($env:SystemDrive)\" 'VMD') }
    if ($env:USERPROFILE) { $c += (Join-Path $env:USERPROFILE 'VMD') }
    if ($env:LOCALAPPDATA) { $c += (Join-Path $env:LOCALAPPDATA 'VMD') }
    foreach ($drive in (Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue)) {
        if ($drive.Root) { $c += (Join-Path $drive.Root 'VMD') }
    }
    return @($c | Where-Object { $_ } | Select-Object -Unique)
}

function Find-InstallRoot($candidates) {
    foreach ($candidate in $candidates) {
        if (Test-IsVmdRoot $candidate) { return (Resolve-Path $candidate).Path }
    }
    return ''
}

# --- stopping the console -----------------------------------------------------

function Import-Common($root, $stickRoot) {
    # Bring in Stop-ProjectProcesses and Get-VmdConsoles from _common.ps1, once,
    # at SCRIPT scope so every function below can see them - the install's copy
    # first, the stick's as the fallback for an install whose script will not
    # load. This must be called at the top level, not from inside a function:
    # dot-sourcing runs in the caller's scope, and dot-sourcing inside a function
    # would leave the definitions trapped there - which is exactly how a
    # two-camera machine would have got only one of its consoles back.
    foreach ($common in @((Join-Path $root 'scripts\_common.ps1'),
            (Join-Path $stickRoot 'files\scripts\_common.ps1'))) {
        if (Test-Path $common) { return $common }
    }
    return $null
}

function Stop-Consoles($root) {
    # Stop-ProjectProcesses knows exactly which processes are this project's and
    # spares everything else. If _common.ps1 was loaded it is used; if it was not
    # (both copies missing), a blunt taskkill by image name is the fallback.
    $did = $false
    if (Get-Command Stop-ProjectProcesses -ErrorAction SilentlyContinue) {
        try {
            $result = Stop-ProjectProcesses $root
            Write-Log "Stopped: $(@($result.Stopped) -join ', ')"
            $did = $true
        }
        catch { Write-Log "The project stopper would not run ($($_.Exception.Message)); trying a plain taskkill." }
    }
    if (-not $did) {
        Write-Log "Falling back to a plain taskkill."
        foreach ($image in @('VMD.exe', 'ffmpeg.exe', 'go2rtc.exe')) {
            try { & taskkill /F /T /IM $image 2>$null | Out-Null } catch { }
        }
    }
    Start-Sleep -Milliseconds 800
    return $did
}

# --- copying the files: engine one, the audited updater -----------------------

function Invoke-PythonCopyEngine($root, $stickRoot) {
    # The bundled interpreter, running vmd.update.main --copy-only out of the
    # STICK'S copy of the code (PYTHONPATH points there), against the install as
    # --root. The stick's code is the new code and is not the code being
    # replaced, so there is no module-replaced-underneath-it problem to design
    # around here. Everything dangerous - the backup, the marker, the rollback if
    # a file will not be overwritten - is that module's, unchanged.
    $pyDir = Join-Path $root 'bin\python'
    if (-not (Test-Path $pyDir)) { throw "no bundled interpreter under $pyDir" }
    $py = Get-ChildItem $pyDir -Filter 'python.exe' -Recurse -Depth 2 -ErrorAction SilentlyContinue |
    Select-Object -First 1
    if (-not $py) { throw "no python.exe under $pyDir" }

    # PYTHONPATH is set only for this one call and then restored, because the
    # console started later inherits this process's environment - and a console
    # that imported vmd from the stick instead of from the install would run the
    # stick's code and stop the moment the stick was pulled out.
    $saved = $env:PYTHONPATH
    $env:PYTHONPATH = (Join-Path $stickRoot 'files')
    try {
        & $py.FullName -m vmd.update.main --root $root --stick $stickRoot --copy-only 2>&1 |
        ForEach-Object { Write-Log "  python: $_" }
        $code = $LASTEXITCODE
    }
    finally {
        if ($null -eq $saved) { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue }
        else { $env:PYTHONPATH = $saved }
    }
    return ($code -eq 0)
}

# --- copying the files: engine two, robocopy the same whitelist ---------------

function Copy-Directory($src, $dst) {
    # robocopy /MIR when it is there, because it prunes a file the new version
    # deleted - the same thing vmd\update\apply.py's _prune_removed does, and for
    # the same reason: a module left behind is imported as happily as a current
    # one. Exit codes 0-7 are success (8 and up are real failures). A plain
    # recursive copy is the last resort when robocopy is somehow absent; it
    # replaces the folder wholesale, which also removes what is no longer there.
    $robo = Get-Command robocopy -ErrorAction SilentlyContinue
    if ($robo) {
        & robocopy $src $dst /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy failed on $src -> $dst (code $LASTEXITCODE)" }
    }
    else {
        if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
        Copy-Item $src $dst -Recurse -Force
    }
}

function Invoke-RobocopyEngine($root, $files) {
    # The fallback for when there is no interpreter to run engine one. Same
    # whitelist, and the machine's own things are held back the same way. Backs up
    # each name it is about to overwrite into previous\<old version>\ first, so a
    # copy that goes wrong here is still one somebody can undo by hand.
    if (-not (Test-IsVmdRoot $root)) { throw "$root is not a VMD install; refusing to copy into it" }

    # The same guard vmd\update\apply.py's ESSENTIAL applies, and for the same
    # reason - this engine mirrors with robocopy /MIR, so a stick that is
    # missing most of VMD does not merely fail to update the machine, it deletes
    # what the machine had. A stick whose write was cut short is internally
    # consistent and its manifest matches, so nothing upstream of here can tell.
    foreach ($needed in @('VERSION', 'vmd\__init__.py', 'vmd\settings.py', 'vmd\desktop\app.py')) {
        if (-not (Test-Path (Join-Path $files $needed) -PathType Leaf)) {
            throw ("the stick is missing $needed, so it is not a whole copy of VMD and " +
                "installing it would break this machine. Nothing was changed. Build the " +
                "stick again on the laptop, and do not unplug it until it has finished.")
        }
    }
    $old = Get-VersionAt $root
    $prev = Join-Path $root ('previous\' + $(if ($null -ne $old) { "$old" } else { 'unknown' }))

    foreach ($entry in (Get-ChildItem $files -Force)) {
        $name = $entry.Name
        if ($script:KEEP_OUT -contains $name) { continue }
        $isProgram = ($script:COPY_IN -contains $name) -or ($entry.Extension.ToLower() -eq '.bat')
        if (-not $isProgram) { continue }

        $target = Join-Path $root $name
        if (Test-Path $target) {
            New-Item -ItemType Directory -Force -Path $prev | Out-Null
            $backup = Join-Path $prev $name
            if (Test-Path $backup) { Remove-Item $backup -Recurse -Force }
            Copy-Item $target $backup -Recurse -Force
        }

        if ($entry.PSIsContainer) { Copy-Directory $entry.FullName $target }
        else { Copy-Item $entry.FullName $target -Force }
        Write-Log "  copied $name"
    }
}

# --- starting the console again -----------------------------------------------

function Start-Consoles($root) {
    # One console per camera on this machine, each pointed at its own settings.
    # Get-VmdConsoles works that out; it comes from _common.ps1, dot-sourced at
    # script scope up top. Each console is started the surest way first - its
    # scheduled task, run NOW in this signed-in session so nothing has to be typed
    # and nothing has to reboot - and, if there is no task, by launching the
    # program straight with that console's settings. The console brings its own
    # recorder up when it does not find one, so starting the console is enough to
    # make the picture come back.
    $consoles = @()
    if (Get-Command Get-VmdConsoles -ErrorAction SilentlyContinue) {
        try { $consoles = @(Get-VmdConsoles $root) } catch { }
    }
    if ($consoles.Count -eq 0) {
        # _common.ps1 did not load, or found nothing. The single-console shape is
        # right for every install but the multi-camera one - the best that can be
        # done without the layout, and the two-camera machine keeps its layout in
        # _common.ps1, which is on the stick.
        $consoles = @([pscustomobject]@{ Suffix = ''; Settings = (Join-Path $root 'settings.json') })
    }

    $started = 0
    foreach ($console in $consoles) {
        $taskName = "VMD Console$($console.Suffix)"
        $ranTask = $false
        try {
            & schtasks /Run /TN $taskName 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-Log "Started `"$taskName`"."
                $ranTask = $true
                $started++
            }
        }
        catch { }
        if ($ranTask) { continue }

        $exe = Join-Path $root 'VMD.exe'
        $starter = if (Test-Path $exe) { $exe } else { Join-Path $root 'VMD.bat' }
        try {
            Start-Process -FilePath $starter -ArgumentList @('--settings', $console.Settings) `
                -WorkingDirectory $root | Out-Null
            Write-Log "Launched $starter for camera$($console.Suffix)."
            $started++
        }
        catch { Write-Log "Could not start the console for camera$($console.Suffix): $($_.Exception.Message)" }
    }
    return $started
}

# =============================================================================
#  Test seams - each answers one question and stops, before anything is touched.
# =============================================================================

if ($CopyEngineOnly) {
    try {
        Invoke-RobocopyEngine $Root $Files
        Write-Output 'OK'
        exit 0
    }
    catch {
        Write-Output "FAIL: $($_.Exception.Message)"
        exit 1
    }
}

if ($WhichRoot) {
    $list = @()
    if ($Candidates) { $list = @($Candidates -split ';') }
    Write-Output (Find-InstallRoot $list)
    exit 0
}

# =============================================================================
#  The real run.
# =============================================================================

# Anything uncaught below turns into a red FAILED the operator can photograph,
# never a raw stack trace in a window they cannot read.
trap {
    Show-Banner @(
        'IT DID NOT WORK.',
        '',
        'Please take a photo of this whole window and send it.',
        "($($_.Exception.Message))"
    ) 'White' 'DarkRed'
    try {
        if ($script:ResolvedStick) {
            $note = Join-Path $script:ResolvedStick 'apply-error.txt'
            [System.IO.File]::WriteAllText($note, ($script:LogLines -join "`r`n") + "`r`n" +
                $_.Exception.Message + "`r`n" + $_.InvocationInfo.PositionMessage,
                (New-Object System.Text.UTF8Encoding($false)))
        }
        Save-Log $script:ResolvedRoot $script:ResolvedStick
    }
    catch { }
    exit 1
}

if (-not $StickRoot) {
    # Double-clicked: this script is <stick>\files\scripts\apply_here.ps1, so the
    # stick is two folders up. Worked out rather than passed on a command line,
    # where a drive root "E:\" is misquoted into "E:".
    $StickRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}
$script:ResolvedStick = $StickRoot

Write-Host ""
Write-Log "VMD updater starting."
Write-Log "Stick: $StickRoot"

$files = Join-Path $StickRoot 'files'
if (-not (Test-Path $files)) {
    throw "This stick has no files\ folder, so there is nothing to install. Build it again on the laptop."
}

# Which version the stick carries, read from its own update.json.
$stickVersion = $null
$updateJson = Join-Path $StickRoot 'update.json'
if (Test-Path $updateJson) {
    try { $stickVersion = (Get-Content $updateJson -Raw | ConvertFrom-Json).version } catch { }
}
if ($null -eq $stickVersion) { $stickVersion = Get-VersionAt $files }

# Find the install.
if (-not $Root) { $Root = Find-InstallRoot (Get-CandidateRoots) }
if (-not (Test-IsVmdRoot $Root)) {
    throw ("Could not find the VMD program on this PC (looked for a folder with a " +
        "VERSION file and a vmd folder). If it is not at C:\VMD, this needs to be " +
        "told where it is.")
}
$Root = (Resolve-Path $Root).Path
$script:ResolvedRoot = $Root
$currentVersion = Get-VersionAt $Root
Write-Log "Program: $Root  (this PC has VMD $currentVersion)"
Write-Log "Stick has VMD $stickVersion."

if (($null -ne $stickVersion) -and ($null -ne $currentVersion) -and ($stickVersion -eq $currentVersion)) {
    Show-Banner @(
        "This PC is already on VMD $currentVersion.",
        'The stick has the same version, so there is nothing to do.'
    ) 'Black' 'Gray'
    Save-Log $Root $StickRoot
    exit 0
}

Show-Banner @("Updating VMD $currentVersion  ->  VMD $stickVersion", '', 'Please wait. Do not unplug the stick.') 'Black' 'Cyan'

# Bring in Stop-ProjectProcesses and Get-VmdConsoles at SCRIPT scope, so both the
# stop and the restart below can see them. Dot-sourced here at the top level on
# purpose - see Import-Common.
$commonPath = Import-Common $Root $StickRoot
if ($commonPath) {
    try { . $commonPath } catch { Write-Log "Could not load $commonPath ($($_.Exception.Message)); using fallbacks." }
}

# 1. Stop the console(s) and the recorder.
Write-Log "Stopping the console..."
Stop-Consoles $Root | Out-Null

# 2. Copy the new files in. Engine one (the audited updater), then engine two
#    (robocopy the same whitelist) if the first could not run.
$copied = $false
try {
    Write-Log "Copying the new version in (using the built-in updater)..."
    $copied = Invoke-PythonCopyEngine $Root $StickRoot
    if (-not $copied) { Write-Log "The built-in updater did not finish; trying a direct copy." }
}
catch {
    Write-Log "The built-in updater could not run ($($_.Exception.Message)); trying a direct copy."
}
if (-not $copied) {
    Write-Log "Copying the new version in (direct copy)..."
    Invoke-RobocopyEngine $Root $files
    $copied = $true
}

# 3. Prove it. VERSION now has to read the stick's number, whichever engine ran.
$after = Get-VersionAt $Root
if ($null -eq $after -or ($null -ne $stickVersion -and $after -ne $stickVersion)) {
    throw "The files were copied but this PC still reads VMD $after, not VMD $stickVersion."
}
Write-Log "This PC is now VMD $after."

# 4. Start the console(s) again, unless asked not to.
if ($NoRestart) {
    Write-Log "Leaving the console stopped, as asked. Restart the PC to bring it back."
}
else {
    Write-Log "Starting the console again..."
    $started = Start-Consoles $Root
    if ($started -eq 0) {
        Write-Log "Could not start the console automatically." 'Yellow'
    }
}

Save-Log $Root $StickRoot
Show-Banner @(
    "DONE.  This PC is now VMD $after.",
    '',
    'The camera picture will come back in a moment.',
    'If it does not within a minute, restart the PC (Start, Power, Restart).',
    'You can now unplug the stick.'
) 'White' 'DarkGreen'
exit 0

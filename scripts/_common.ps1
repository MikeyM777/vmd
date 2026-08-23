# =============================================================================
#  Shared by every script in this folder. Dot-sourced, never run on its own.
#
#  It exists because the installer, the offline installer and the autostart
#  script all have to agree about four things, and disagreeing about any of
#  them is how a deployment quietly breaks:
#
#    - where the project is,
#    - what bin\ is for and how it gets onto PATH,
#    - what "already installed" looks like,
#    - how to say all of that to somebody who has never used a terminal.
# =============================================================================

# PowerShell 7.4 turns a non-zero exit code from a native command into a
# terminating error when $ErrorActionPreference is 'Stop'. winget returns
# non-zero for entirely benign reasons - "no applicable upgrade found" among
# them - so on 7.x the scripts here would die with a red stack trace at the
# first package that was already present. install.bat launches Windows
# PowerShell 5.1, where this does not arise, but a right-click "Run with
# PowerShell" on the .ps1 uses whichever PowerShell is default, and on this
# machine that can be 7.x. Native exit codes are checked explicitly below
# wherever they matter, so switching this off loses nothing.
$PSNativeCommandUseErrorActionPreference = $false

# --- saying things -----------------------------------------------------------

$script:StepTotal = 0
$script:StepNumber = 0

function Set-StepTotal($n) { $script:StepTotal = $n; $script:StepNumber = 0 }

function Write-Step($text) {
    $script:StepNumber++
    Write-Host "`n[$($script:StepNumber)/$($script:StepTotal)] $text" -ForegroundColor Cyan
}

function Write-Ok($text)   { Write-Host "      $text" -ForegroundColor Green }
function Write-Info($text) { Write-Host "      $text" -ForegroundColor Gray }
function Write-Bad($text)  { Write-Host "      $text" -ForegroundColor Red }
function Write-Warn($text) { Write-Host "      $text" -ForegroundColor Yellow }

# --- running something and keeping what it said ------------------------------
#
# Native stderr is merged into the output on purpose: it is where winget, uv and
# PyInstaller put the one sentence that explains a failure, and it is also the
# only stream Start-Transcript cannot see on its own - an unmerged native error
# goes straight to the console window and is missing from the file the operator
# is asked to send.
#
# Merging is what turns those lines into ErrorRecords, and under
# $ErrorActionPreference = 'Stop' the first one terminates the script. That is
# not theoretical: `& $cacheGen ... 2>&1 | Out-Null` in this installer meant
# VLC's plugin index was never actually rebuilt, because vlc-cache-gen reports
# its progress on stderr and the first line of it threw. So the preference is
# lowered for exactly the length of the call and put back afterwards.
#
# The merged lines are flattened back to plain text before they are shown. Left
# as ErrorRecords they print red, and uv reports its ordinary progress on
# stderr - so a completely normal `uv sync` would fill the screen with red on a
# machine where nothing at all is wrong. That is the same cry-wolf failure this
# installer is being fixed for, in a different place.
function Invoke-Logged($file, [string[]]$arguments) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $file @arguments 2>&1 |
            ForEach-Object {
                if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() } else { $_ }
            } | Out-Host
        return $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
}

# The same merge, but the output is handed back instead of printed, split into
# the two streams it came from. Used where the output is evidence rather than
# something to show: `import vlc` prints fifty kilobytes of harmless "stale
# plugins cache" lines to stderr, and putting those on the screen of somebody
# who has never used a terminal is its own kind of false alarm.
# For a program whose output nobody wants and whose failure is expected: only
# its exit code comes back.
#
# The preference is lowered rather than the stream redirected, because
# redirecting is not enough and looks as though it is. `& taskkill ... 2>$null
# | Out-Null` still ends the script under $ErrorActionPreference = 'Stop':
# PowerShell raises NativeCommandError for the stderr of a piped native command
# before the redirection can discard it. Verified by running it - it does not
# reproduce in an interactive session, only in `powershell -File`, which is how
# install.bat runs every one of these scripts.
function Invoke-Quiet($file, [string[]]$arguments) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    try {
        & $file @arguments 2>&1 | Out-Null
        return $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
}

function Invoke-Captured($file, [string[]]$arguments) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $all = & $file @arguments 2>&1
        $code = $LASTEXITCODE
        $out = @($all | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } |
            ForEach-Object { [string]$_ })
        $err = @($all | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } |
            ForEach-Object { $_.ToString() })
        return [pscustomobject]@{ Code = $code; Out = $out; Err = $err }
    } finally { $ErrorActionPreference = $previous }
}

# --- where things are --------------------------------------------------------

function Get-ProjectRoot { Split-Path -Parent $PSScriptRoot }

function Test-Have($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Test-Admin {
    $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# --- PATH --------------------------------------------------------------------
#
# Everything this project downloads lives in bin\, and bin\ has to be on PATH
# for three separate reasons that all end in the same place:
#
#   uv      - VMD.exe is vmd\launcher.py frozen, and it looks for uv with
#             shutil.which(), which reads PATH and nothing else. On the offline
#             laptop there is no winget and no installer, so the copy of uv in
#             bin\ is the only one there will ever be. If bin\ is not on PATH,
#             double-clicking VMD.exe prints "uv is not installed" on a machine
#             where it plainly is.
#   ffmpeg  - vmd\storage\recorder.py runs the bare name "ffmpeg". PATH is how
#             a bare name is found.
#   go2rtc  - started by full path, so it does not need this. Listed so the
#             next person does not wonder why it is missing from the list.
#
# The registry is read and written raw, without expanding %VARIABLES%. The
# obvious [Environment]::GetEnvironmentVariable('Path','User') expands them on
# the way out, so writing the result back replaces every %USERPROFILE% in the
# user's PATH with a literal path - which works today and breaks the day the
# profile moves. Not our variable to damage.

function Get-UserPathRaw {
    $key = Get-Item 'HKCU:\Environment' -ErrorAction SilentlyContinue
    if (-not $key) { return '' }
    $value = $key.GetValue('Path', '', 'DoNotExpandEnvironmentNames')
    if ($null -eq $value) { return '' }
    return [string]$value
}

function Test-PathContains($pathValue, $entry) {
    $wanted = $entry.TrimEnd('\')
    foreach ($part in ($pathValue -split ';')) {
        if ($part.Trim().TrimEnd('\') -ieq $wanted) { return $true }
    }
    return $false
}

# Windows only re-reads the environment when it is told to. Explorer is what
# launches VMD.exe, and Explorer caches its environment from when it started,
# so a PATH written to the registry is invisible to a double-click until the
# user logs out - unless this broadcast is sent, which is exactly what setx
# does and the reason people reach for setx despite its 1024-character limit
# and its habit of mangling long PATHs.
function Publish-EnvironmentChange {
    if (-not ('Vmd.NativeEnv' -as [type])) {
        Add-Type -Namespace Vmd -Name NativeEnv -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true, CharSet = System.Runtime.InteropServices.CharSet.Auto)]
public static extern System.IntPtr SendMessageTimeout(System.IntPtr hWnd, uint Msg, System.IntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out System.UIntPtr lpdwResult);
'@ -ErrorAction SilentlyContinue
    }
    try {
        $HWND_BROADCAST = [IntPtr]0xffff
        $WM_SETTINGCHANGE = 0x1A
        $SMTO_ABORTIFHUNG = 0x0002
        [UIntPtr]$result = [UIntPtr]::Zero
        [void][Vmd.NativeEnv]::SendMessageTimeout(
            $HWND_BROADCAST, $WM_SETTINGCHANGE, [IntPtr]::Zero, 'Environment',
            $SMTO_ABORTIFHUNG, 3000, [ref]$result)
    } catch {
        # Cosmetic only: without it the new PATH is live in the next window
        # rather than immediately. Never worth failing an install over.
    }
}

function Add-BinToUserPath($binDir) {
    $current = Get-UserPathRaw
    if (Test-PathContains $current $binDir) {
        # Still put it into this process, because the registry says nothing
        # about the window we are standing in.
        if (-not (Test-PathContains $env:Path $binDir)) { $env:Path = "$binDir;$env:Path" }
        return $false
    }
    $updated = if ([string]::IsNullOrWhiteSpace($current)) { $binDir } else { "$($current.TrimEnd(';'));$binDir" }
    Set-ItemProperty -Path 'HKCU:\Environment' -Name 'Path' -Value $updated -Type ExpandString
    Publish-EnvironmentChange
    $env:Path = "$binDir;$env:Path"
    return $true
}

function Remove-BinFromUserPath($binDir) {
    $current = Get-UserPathRaw
    if (-not (Test-PathContains $current $binDir)) { return $false }
    $wanted = $binDir.TrimEnd('\')
    $kept = @($current -split ';' | Where-Object { $_.Trim() -and ($_.Trim().TrimEnd('\') -ine $wanted) })
    Set-ItemProperty -Path 'HKCU:\Environment' -Name 'Path' -Value ($kept -join ';') -Type ExpandString
    Publish-EnvironmentChange
    return $true
}

# --- the project's own Python ------------------------------------------------
#
# The interpreter lives inside the project, under bin\python\, rather than in
# the uv-managed store under %APPDATA%. That one decision is what makes the
# offline laptop possible: .venv\pyvenv.cfg records the absolute path of the
# interpreter it was built from, and a folder copied to another machine takes
# .venv with it but cannot take C:\Users\<somebody>\AppData\Roaming\uv\... with
# it. A venv whose `home` does not exist prints
#
#     No Python at '...\python.exe'
#
# and exits 103, which is what the previous offline recipe produced on arrival.
# With the interpreter under bin\python\ the whole thing travels together, and
# Repair-VenvPaths below fixes `home` if the folder lands somewhere new.

function Get-ProjectPythonDir($root) { Join-Path $root 'bin\python' }

function Find-ProjectPython($root) {
    $dir = Get-ProjectPythonDir $root
    if (-not (Test-Path $dir)) { return $null }
    $exe = Get-ChildItem -Path $dir -Filter 'python.exe' -Recurse -Depth 2 -ErrorAction SilentlyContinue |
        Sort-Object FullName | Select-Object -First 1
    if ($exe) { return $exe.FullName }
    return $null
}

function Get-VenvHome($root) {
    $cfg = Join-Path $root '.venv\pyvenv.cfg'
    if (-not (Test-Path $cfg)) { return $null }
    foreach ($line in (Get-Content $cfg -ErrorAction SilentlyContinue)) {
        if ($line -match '^\s*home\s*=\s*(.+?)\s*$') { return $Matches[1] }
    }
    return $null
}

# Two absolute paths are written into a built environment, and both of them are
# wrong the moment the folder is copied somewhere other than where it was
# built:
#
#   .venv\pyvenv.cfg                        home = <the interpreter>
#   .venv\Lib\site-packages\_editable_*.pth <the project directory>
#
# Rewriting them is a two-line repair and it removes the "both machines must
# use exactly C:\VMD" rule from the offline instructions - which was the kind
# of rule nobody reads until it has already been broken.
function Repair-VenvPaths($root) {
    $repaired = @()

    $python = Find-ProjectPython $root
    $cfg = Join-Path $root '.venv\pyvenv.cfg'
    if ($python -and (Test-Path $cfg)) {
        $wanted = Split-Path -Parent $python
        $home_ = Get-VenvHome $root
        if ($home_ -and ($home_.TrimEnd('\') -ine $wanted.TrimEnd('\'))) {
            # UTF-8 without BOM, not ASCII. Set-Content -Encoding ASCII turns
            # every byte above 0x7F into a literal '?', so a folder with a
            # Hebrew or accented character in its path was written back as a
            # path that does not exist - and the failure that follows is an
            # import error naming a module, with nothing pointing at the
            # mangled path. CPython reads both of these files as UTF-8.
            $lines = Get-Content $cfg
            $fixed = ($lines -replace '^\s*home\s*=\s*.*$', "home = $wanted")
            [System.IO.File]::WriteAllLines($cfg, $fixed, (New-Object System.Text.UTF8Encoding($false)))
            $repaired += "the interpreter path in .venv\pyvenv.cfg"
        }
    }

    $sitePackages = Join-Path $root '.venv\Lib\site-packages'
    if (Test-Path $sitePackages) {
        $pthFiles = @(Get-ChildItem $sitePackages -Filter '_editable_impl_*.pth' -ErrorAction SilentlyContinue)
        foreach ($pth in $pthFiles) {
            $content = (Get-Content $pth.FullName -Raw -ErrorAction SilentlyContinue)
            if ($null -eq $content) { continue }
            if ($content.Trim().TrimEnd('\') -ine $root.TrimEnd('\')) {
                # UTF-8 without BOM - see the note on pyvenv.cfg above.
                [System.IO.File]::WriteAllText($pth.FullName, $root, (New-Object System.Text.UTF8Encoding($false)))
                $repaired += "the project path in $($pth.Name)"
            }
        }
        # No .pth at all is the same fault with nothing to rewrite, and it ends
        # the same way: every library imports and the console stops with
        #
        #     Error while finding module specification for 'vmd.desktop'
        #     (ModuleNotFoundError: No module named 'vmd')
        #
        # The file is one line naming the folder the project lives in, so it can
        # simply be written. Only when this folder really is the project - a
        # pyproject.toml and a vmd\ package beside it - because writing a path
        # into site-packages that has no package at the end of it turns a clear
        # import error into a mystery.
        if ($pthFiles.Count -eq 0 -and
            (Test-Path (Join-Path $root 'pyproject.toml')) -and
            (Test-Path (Join-Path $root 'vmd\__init__.py'))) {
            # UTF-8 without BOM - see the note on pyvenv.cfg above.
            [System.IO.File]::WriteAllText((Join-Path $sitePackages '_editable_impl_vmd.pth'), $root,
                (New-Object System.Text.UTF8Encoding($false)))
            $repaired += "the missing _editable_impl_vmd.pth, which is what makes 'import vmd' work"
        }
    }

    return $repaired
}

# --- downloads ---------------------------------------------------------------

# $MinimumBytes is not optional politeness. get.videolan.org answers a request
# for an .exe with a 29 KB HTML page carrying a <meta refresh> to a mirror, so a
# download that "succeeded" can leave a web page named vlc-win64.exe on the USB
# drive - which is only discovered on the offline laptop, where it cannot be
# fixed. Anything short of the expected size is deleted rather than kept.
function Get-File($url, $destination, $label, $MinimumBytes = 0) {
    $previous = $ProgressPreference
    # Invoke-WebRequest's progress bar costs more time than the download on a
    # slow console, and redraws over the step list this installer just printed.
    $ProgressPreference = 'SilentlyContinue'
    try {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
        $response = Invoke-WebRequest -Uri $url -OutFile $destination -UseBasicParsing -PassThru

        # A download-page redirect rather than the file. Follow it once: the
        # mirror it names is the actual download, and picking the mirror is the
        # whole job that page exists to do.
        $type = ($response.Headers['Content-Type'] -join ' ')
        if ($type -match 'text/html') {
            $page = Get-Content $destination -Raw -ErrorAction SilentlyContinue
            if ($page -and ($page -match "http-equiv=`"refresh`"[^>]*URL='([^']+)'")) {
                $mirror = $Matches[1]
                Write-Info "  following the download page to $([Uri]$mirror | ForEach-Object { $_.Host })"
                Invoke-WebRequest -Uri $mirror -OutFile $destination -UseBasicParsing
            }
        }

        $size = (Get-Item $destination -ErrorAction SilentlyContinue).Length
        if ($MinimumBytes -gt 0 -and $size -lt $MinimumBytes) {
            Write-Bad "The download of $label came back too small ($size bytes) to be the real file."
            Remove-Item $destination -Force -ErrorAction SilentlyContinue
            return $false
        }
        return $true
    } catch {
        Write-Bad "Could not download $label"
        Write-Info "  $url"
        Write-Info "  $($_.Exception.Message)"
        Remove-Item $destination -Force -ErrorAction SilentlyContinue
        return $false
    } finally { $ProgressPreference = $previous }
}

# --- getting out of our own way ----------------------------------------------
#
# Found by running the installer on a machine where the recorder was already
# running, which on the deployment laptop is every machine, every time - that is
# what the autostart tasks are for.
#
# `uv sync` decides the environment needs recreating, deletes .venv, and stops
# partway through with "Access is denied" on .venv\Scripts\python.exe because
# the recorder is running out of it. What is left is not a virtual environment
# and not a deletable one either: every later uv command answers
#
#     Project virtual environment directory `...\.venv` cannot be used because
#     it is not a compatible environment but cannot be recreated because it is
#     not a virtual environment
#
# and the only way out is to delete .venv by hand, which is not a thing the
# person installing this can be asked to do. Re-running install.bat, the advice
# the installer itself gives, fails in exactly the same way every time.
#
# So the processes running out of this folder are stopped first. Matched by full
# path, never by name, for the same reason scripts\build_exe.ps1 matches that
# way: "python" and "ffmpeg" are common names and somebody else's are not ours
# to end. The console is started again at the end of the install, and the
# recorder with it.
# Only the programs this project itself starts, named one by one. Not "anything
# running from under the project folder": a developer with the test suite
# running would have it killed underneath them, and on this machine that is
# exactly what happened the first time. Nothing in this list is something a
# person is in the middle of.
#
# $spare is a list of process ids to leave alone, and it exists for exactly one
# caller: the updater, vmd\update\main.py, which stops the console before it
# replaces it. That updater IS a process named python running out of
# bin\python\ - the second rule below matches it as squarely as it matches the
# recorder - so without being told to spare itself it taskkills itself halfway
# through an update, with the console already stopped and nothing left running
# to start it again. That was measured, not imagined.
function Get-ProjectRuntimeProcesses($root, $spare = @()) {
    $wanted = @(
        (Join-Path $root 'VMD.exe')
        (Join-Path $root '.venv\Scripts\python.exe')
        (Join-Path $root '.venv\Scripts\pythonw.exe')
        (Join-Path $root 'bin\ffmpeg.exe')
        (Join-Path $root 'bin\go2rtc.exe')
    )
    # The interpreter under bin\python\ is matched by prefix, because its exact
    # path carries a CPython version that changes.
    $pythonDir = (Join-Path $root 'bin\python').TrimEnd('\') + '\'
    return @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Id -ne $PID -and $spare -notcontains $_.Id -and $_.Path -and (
            ($wanted -contains $_.Path) -or
            ($_.Path.StartsWith($pythonDir, 'OrdinalIgnoreCase') -and $_.ProcessName -eq 'python')
        )
    })
}

function Stop-ProjectProcesses($root, $spare = @()) {
    $stopped = @()
    foreach ($process in (Get-ProjectRuntimeProcesses $root $spare)) {
        # /T as well as /F: VMD.exe starts uv, uv starts python, and python is
        # the one holding the environment open. Ending only the parent leaves
        # the child owning the file that has to be deleted.
        #
        # A process that refuses to die is reported, not thrown. Windows says
        # "could not be terminated" about processes that are already on their
        # way out, and an installer that fell over at that would be a worse
        # false alarm than the one it is here to remove.
        $null = Invoke-Quiet 'taskkill' @('/F', '/T', '/PID', "$($process.Id)")
        $stopped += $process.ProcessName
    }
    if ($stopped.Count -gt 0) { Start-Sleep -Milliseconds 800 }
    # Whatever is still standing after that, said plainly. This is the value the
    # caller should act on, not the list above.
    $left = @(Get-ProjectRuntimeProcesses $root $spare | ForEach-Object { $_.ProcessName })
    return [pscustomobject]@{ Stopped = @($stopped | Sort-Object -Unique); StillRunning = @($left | Sort-Object -Unique) }
}

# --- what has to start by itself ---------------------------------------------
#
# Three files ask the same question - scripts\autostart.ps1, and both installers
# checking that it worked - and they were each answering it their own way. Both
# installers looked for exactly two tasks called "VMD Recorder" and "VMD
# Console", which are the right names for one camera and the wrong names for
# two: a machine with cameras\250 and cameras\251 has four tasks and none of
# them is called either of those, so a correctly set up pair of consoles was
# reported as "nothing will start after a restart".
#
# So the layout is worked out in one place, here, and the two mechanisms are
# both admitted to: scheduled tasks, which is what this does when it can, and
# shortcuts in the Startup folder, which is what it falls back to on a machine
# where registering a task is refused. autostart.ps1 says why that happens.

# One entry per console: the settings file it is pointed at, and the suffix its
# tasks and shortcuts carry. Read off the disk rather than kept anywhere,
# because a list kept anywhere is a second thing that can be wrong - the same
# rule scripts\cameras.ps1 states.
#
# The single-camera layout is not a special case bolted on: it is one entry with
# an empty suffix, which is how the names stay exactly what they always were.
function Get-VmdConsoles($root) {
    $camerasDir = Join-Path $root 'cameras'
    $found = @()
    if (Test-Path $camerasDir) {
        $found = @(
            Get-ChildItem -Path $camerasDir -Directory -ErrorAction SilentlyContinue |
                Where-Object { Test-Path (Join-Path $_.FullName 'settings.json') } |
                ForEach-Object {
                    [pscustomobject]@{
                        Suffix   = " $($_.Name)"
                        Settings = (Join-Path $_.FullName 'settings.json')
                    }
                }
        )
    }
    if ($found.Count -gt 0) { return $found }
    return @([pscustomobject]@{ Suffix = ''; Settings = (Join-Path $root 'settings.json') })
}

function Get-StartupDir { [Environment]::GetFolderPath('Startup') }

# What is actually set up, whichever way it was set up. The caller gets the
# names it expected, the names it found, and one word for how.
function Get-AutostartState($root) {
    # The console entry is only expected when there is a VMD.exe for it to
    # start. autostart.ps1 will not create it otherwise - both the task and the
    # Startup shortcut are behind a Test-Path on the exe - so counting it as
    # expected made the installers print, in red, "the system will not fully
    # come back after a restart", with the fix "run autostart-on.bat as
    # administrator". Running it as administrator creates nothing, because the
    # missing piece is the exe, not permission: the operator was sent to do a
    # thing that could not work, on the same screen that called VMD.exe
    # optional two lines earlier. The recorder - which is what must survive a
    # power cut - is set up in this case and is now reported as such.
    $hasExe = Test-Path (Join-Path $root 'VMD.exe')

    $expected = @()
    foreach ($console in (Get-VmdConsoles $root)) {
        $expected += "VMD Recorder$($console.Suffix)"
        if ($hasExe) { $expected += "VMD Console$($console.Suffix)" }
    }

    $tasks = @()
    foreach ($name in $expected) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { $tasks += $name }
    }

    $startupDir = Get-StartupDir
    $shortcuts = @()
    foreach ($name in $expected) {
        if (Test-Path (Join-Path $startupDir "$name.lnk")) { $shortcuts += $name }
    }

    # A name covered either way is covered. The console task is the one that can
    # legitimately be absent - autostart.ps1 does not create it before VMD.exe
    # exists - so "how" is reported rather than judged here.
    $covered = @($expected | Where-Object { ($tasks -contains $_) -or ($shortcuts -contains $_) })
    $how = 'none'
    if ($tasks.Count -gt 0 -and $shortcuts.Count -gt 0) { $how = 'both' }
    elseif ($tasks.Count -gt 0) { $how = 'tasks' }
    elseif ($shortcuts.Count -gt 0) { $how = 'startup' }

    return [pscustomobject]@{
        Expected  = $expected
        Tasks     = $tasks
        Shortcuts = $shortcuts
        Covered   = $covered
        Missing   = @($expected | Where-Object { $covered -notcontains $_ })
        How       = $how
        Ok        = ($covered.Count -eq $expected.Count)
        HasExe    = $hasExe
    }
}

# The same verdict for both installers, reached once. They print it in their own
# three groups, but what counts as good, as second-best and as broken is one
# decision and belongs in one place.
function Get-AutostartVerdict($root) {
    $state = Get-AutostartState $root

    # Everything that was asked for is set up, but there was no VMD.exe to ask
    # for a console with. Recording - the thing that must survive a power cut -
    # comes back on its own; the window does not, and somebody has to open it.
    # Worth saying plainly, and worth naming the real remedy rather than
    # administrator rights.
    if ($state.Ok -and -not $state.HasExe) {
        return [pscustomobject]@{
            Level = 'optional'
            Say   = "Recording starts by itself. The console window does not - there is no VMD.exe."
            What  = "the console window does not open by itself after a restart (recording does)"
            Fix   = @(
                "Set up: $($state.Covered -join ', ').",
                "Recording restarts by itself after a power cut, which is the part that",
                "matters. The console is only the window onto it.",
                "To open it: double-click VMD.bat, or a camera's shortcut.",
                "To have it open by itself, VMD.exe has to exist: run install.bat on the",
                "connected machine so it is built, then run autostart-on.bat here."
            )
            State = $state
        }
    }

    if ($state.Ok -and $state.How -eq 'tasks') {
        return [pscustomobject]@{
            Level = 'good'
            Say   = "Registered: $($state.Tasks -join ', ')."
            What  = "automatic start after a restart ($($state.Tasks -join ', '))"
            Fix   = @()
            State = $state
        }
    }

    if ($state.Ok) {
        # It works. It is not what was asked for, and the difference only shows
        # up on the day something crashes, so it is said out loud rather than
        # counted as a clean install.
        return [pscustomobject]@{
            Level = 'optional'
            Say   = "It starts from the Startup folder rather than a scheduled task."
            What  = "it starts by itself from the Startup folder, not a scheduled task"
            Fix   = @(
                "Windows refused to create the scheduled tasks on this machine, so",
                "shortcuts in the Startup folder do the job instead: the recorder and",
                "the console both start when this user signs in.",
                "What is lost is the recovery a task gives - restart after a crash, and",
                "catching up a start that was missed while the machine was off.",
                "To get the tasks: right-click autostart-on.bat, 'Run as administrator'."
            )
            State = $state
        }
    }

    if ($state.Covered.Count -gt 0) {
        return [pscustomobject]@{
            Level = 'broken'
            Say   = "Missing: $($state.Missing -join ', ')."
            What  = "the system will not fully come back after a restart"
            Fix   = @(
                "Set up: $($state.Covered -join ', ').",
                "Missing: $($state.Missing -join ', ').",
                "Right-click autostart-on.bat and choose 'Run as administrator'."
            )
            State = $state
        }
    }

    return [pscustomobject]@{
        Level = 'broken'
        Say   = "Nothing is set up to start by itself."
        What  = "nothing will start after a restart, so a power cut stops the recording"
        Fix   = @(
            "Right-click autostart-on.bat and choose 'Run as administrator'.",
            "Until then, recording only runs while somebody has started the console."
        )
        State = $state
    }
}

# --- the transcript ----------------------------------------------------------
#
# "It says VLC can't be installed although it's already on the laptop, and
# other things." That sentence is why this exists. The one named problem could
# be found by reading the script; the other things could not be found at all,
# because nothing kept a record of what the installer saw, and the person
# standing at the laptop cannot be asked to read a console window back.
#
# So every installer writes what it did to bin\logs\, and the summary ends by
# naming the file. bin\logs\ is already excluded from the offline copy by
# scripts\offline_kit.ps1, so a log never travels to the deployment laptop by
# accident.

function Get-LogDir($root) {
    $dir = Join-Path $root 'bin\logs'
    try { New-Item -ItemType Directory -Force -Path $dir | Out-Null } catch { }
    return $dir
}

# Every form of every password that could end up in a log, longest first, the
# same rule vmd\streaming\diagnose.py follows and for the same reason: the
# operator types the password into its own field, and anything that builds an
# RTSP URL percent-encodes it, so `p@ss` also travels as `p%40ss` and a
# redaction that only knew the typed form would match nothing at all.
function Get-SettingsSecrets($root) {
    $file = Join-Path $root 'settings.json'
    if (-not (Test-Path $file)) { return @() }
    try { $settings = ConvertFrom-Json (Get-Content $file -Raw -ErrorAction Stop) }
    catch { return @() }
    $values = @{}
    foreach ($section in @('camera', 'radio')) {
        $secret = $null
        try { $secret = $settings.$section.password } catch { }
        # Two characters or fewer is not a password worth masking, and masking
        # it would replace half the innocent text in the file.
        if (($secret -is [string]) -and $secret.Length -ge 3) {
            $values[$secret] = $true
            $values[[Uri]::EscapeDataString($secret)] = $true
        }
    }
    return @($values.Keys | Sort-Object -Property Length -Descending)
}

# Applied to the finished file rather than to each line on its way out, because
# a transcript records things this script never chose to print - the header, the
# command line it was started with, whatever a tool echoed back at it.
function Protect-LogFile($path, $root) {
    if (-not $path -or -not (Test-Path $path)) { return }
    try { $text = Get-Content $path -Raw -ErrorAction Stop } catch { return }
    if (-not $text) { return }
    $before = $text
    foreach ($secret in (Get-SettingsSecrets $root)) { $text = $text.Replace($secret, '********') }
    # Credentials carried inside a URL are masked whether or not they are in
    # settings.json - a proxy address, an RTSP address a tool echoed back - for
    # the same reason: this file exists to be sent to somebody else.
    $text = [regex]::Replace($text,
        '(?<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)[^\s/@:]+:[^\s/@]+@',
        '${scheme}********:********@')
    if ($text -ne $before) {
        try { Set-Content -Path $path -Value $text -Encoding UTF8 -NoNewline } catch { }
    }
}

function Start-VmdTranscript($root, $name) {
    $path = Join-Path (Get-LogDir $root) $name
    try {
        Start-Transcript -Path $path -Force | Out-Null
        $script:VmdTranscript = $path
        return $path
    } catch {
        # A missing log is a worse install experience, never a failed one.
        $script:VmdTranscript = $null
        return $null
    }
}

# Safe to call twice: the second call does nothing. That matters because the
# installer stops the transcript deliberately before handing the window over to
# the console, and again in the finally that catches a crash.
function Stop-VmdTranscript($root) {
    if (-not $script:VmdTranscript) { return $null }
    $path = $script:VmdTranscript
    $script:VmdTranscript = $null
    try { Stop-Transcript | Out-Null } catch { }
    Protect-LogFile $path $root
    return $path
}

# --- is VLC here, and is it the one 64-bit Python can load? ------------------
#
# The old answer was Test-Path "$env:ProgramFiles\VideoLAN\VLC\libvlc.dll", one
# narrow question that says "not installed" about a VLC in a custom directory, a
# per-user VLC under %LOCALAPPDATA%, or a VLC whose folder the operator chose
# themselves. That is the reported failure: the installer telling somebody VLC
# is missing while they are looking at it in their Start menu.
#
# None of what follows is the authority, and it is important that it is not
# treated as one. The only question that matters is whether the 64-bit Python in
# this project can load libVLC, and the installer asks that directly once the
# environment exists. What this does is narrow it down beforehand and, when the
# answer is no, be able to say why - which of the two very different failures it
# is. python-vlc's own search (site-packages\vlc.py, find_lib) reads
# HKLM\Software\VideoLAN\VLC then HKCU, then falls back to
# %ProgramFiles%\VideoLan\VLC, so those come first here.

# The PE header, rather than which Program Files folder it sits in. A 32-bit VLC
# installed into a 64-bit path is unusual but perfectly possible, and being
# wrong about this produces exactly the advice that does not help.
function Get-PeMachine($file) {
    try { $stream = [IO.File]::OpenRead($file) } catch { return 0 }
    try {
        $buffer = New-Object byte[] 4
        $stream.Position = 0x3C
        if ($stream.Read($buffer, 0, 4) -ne 4) { return 0 }
        $peOffset = [BitConverter]::ToInt32($buffer, 0)
        if ($peOffset -le 0 -or $peOffset -gt ($stream.Length - 6)) { return 0 }
        $stream.Position = $peOffset
        $header = New-Object byte[] 6
        if ($stream.Read($header, 0, 6) -ne 6) { return 0 }
        if ($header[0] -ne 0x50 -or $header[1] -ne 0x45) { return 0 }   # "PE"
        return [BitConverter]::ToUInt16($header, 4)
    } catch { return 0 } finally { $stream.Dispose() }
}

function Get-DllBits($file) {
    switch (Get-PeMachine $file) {
        0x8664  { return 64 }   # x64
        0xAA64  { return 64 }   # ARM64
        0x014C  { return 32 }   # x86
        default { return 0 }
    }
}

# Both hives, both views, and the VALUE rather than the key.
#
# The value, because on the machine this was written on
# HKCU\Software\VideoLAN\VLC exists and holds only Lang=en - no InstallDir at
# all. Code that asks whether the key is there gets a confident yes and a folder
# of empty string.
#
# Both views, because HKLM\Software is redirected: a 64-bit process cannot see a
# 32-bit installer's key and a 32-bit process cannot see a 64-bit one's. The
# HKLM:\SOFTWARE\WOW6432Node\... path works from a 64-bit process and is wrong
# from a 32-bit one, where it would mean WOW6432Node\WOW6432Node. OpenBaseKey
# with an explicit view is right from either, which matters because nothing
# stops somebody starting this from a 32-bit PowerShell.
function Get-RegistryString($hive, $subkey, $name) {
    $found = @()
    foreach ($view in @([Microsoft.Win32.RegistryView]::Registry64,
                        [Microsoft.Win32.RegistryView]::Registry32)) {
        $base = $null
        $key = $null
        try {
            $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($hive, $view)
            $key = $base.OpenSubKey($subkey)
            if ($key) {
                $value = $key.GetValue($name)
                if (($value -is [string]) -and $value.Trim()) { $found += $value.Trim() }
            }
        } catch {
        } finally {
            if ($key) { $key.Dispose() }
            if ($base) { $base.Dispose() }
        }
    }
    return $found
}

function Get-VlcInstall {
    $places = New-Object System.Collections.ArrayList

    # The key python-vlc reads, in both hives and both registry views.
    foreach ($hive in @([Microsoft.Win32.RegistryHive]::LocalMachine,
                        [Microsoft.Win32.RegistryHive]::CurrentUser)) {
        foreach ($dir in (Get-RegistryString $hive 'SOFTWARE\VideoLAN\VLC' 'InstallDir')) {
            [void]$places.Add(@{ Dir = $dir; Source = 'the VideoLAN key in the registry' })
        }
        # What Add or remove programs knows, which is where a custom install
        # directory is recorded.
        foreach ($dir in (Get-RegistryString $hive 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\VLC media player' 'InstallLocation')) {
            [void]$places.Add(@{ Dir = $dir; Source = 'the uninstall entry in the registry' })
        }
    }
    # The ordinary places, including the per-user install winget will choose on
    # a machine where it cannot write to Program Files.
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:ProgramW6432,
                        (Join-Path $env:LOCALAPPDATA 'Programs'), $env:LOCALAPPDATA)) {
        if ($base) { [void]$places.Add(@{ Dir = (Join-Path $base 'VideoLAN\VLC'); Source = 'the usual folder' }) }
    }
    # And anywhere on PATH, which is what a portable copy looks like. Reading
    # PATH is not the same as writing it: nothing in this project ever puts VLC
    # on PATH, because since Python 3.8 ctypes does not search PATH for a
    # dependent DLL - vmd\desktop\libvlc.py uses os.add_dll_directory instead,
    # and PATH is no substitute for it.
    foreach ($part in ($env:Path -split ';')) {
        if ($part.Trim()) { [void]$places.Add(@{ Dir = $part.Trim(); Source = 'a folder on PATH' }) }
    }

    $found = $null
    $only32 = $null
    $noPlugins = $null
    $searched = @()
    $seen = @{}
    foreach ($place in $places) {
        $dir = $place.Dir
        $key = $dir.TrimEnd('\').ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $searched += $dir
        $dll = Join-Path $dir 'libvlc.dll'
        # Whatever the registry says, the file has to actually be there. A key
        # left behind by an uninstall is a false pass, and a false pass is worse
        # than a false failure: it sends the operator looking for a problem
        # somewhere else entirely.
        if (-not (Test-Path $dll)) { continue }
        $bits = Get-DllBits $dll
        if ($bits -ne 64) {
            if (($bits -eq 32) -and (-not $only32)) {
                $only32 = [pscustomobject]@{ Dir = $dir; Dll = $dll; Bits = 32; Source = $place.Source }
            }
            continue
        }
        # A libvlc.dll with no plugins tree beside it loads, reports itself
        # healthy and then shows a black rectangle for ever, with nothing on
        # screen saying why. Counting that as "VLC is installed" would have the
        # installer say everything is fine about the one failure that never
        # explains itself.
        if (-not (Test-Path (Join-Path $dir 'plugins'))) {
            if (-not $noPlugins) {
                $noPlugins = [pscustomobject]@{ Dir = $dir; Dll = $dll; Source = $place.Source }
            }
            continue
        }
        $found = [pscustomobject]@{ Dir = $dir; Dll = $dll; Bits = 64; Source = $place.Source }
        break
    }

    $result = [pscustomobject]@{
        Found  = [bool]$found
        Dir    = $null
        Dll    = $null
        Source = $null
        # A 32-bit VLC and nothing else is its own failure and needs its own
        # sentence: it is the one that looks most like "VLC is missing" to
        # somebody who can see VLC on their own screen.
        Only32 = ((-not $found) -and [bool]$only32)
        Dir32  = $null
        # A VLC with no plugins folder is a third thing again, with a different
        # two-minute fix.
        NoPlugins = ((-not $found) -and (-not $only32) -and [bool]$noPlugins)
        DirNoPlugins = $null
        # Every folder actually looked at, so that a refusal can name where it
        # looked rather than assert an absence.
        Searched = $searched
    }
    if ($found) {
        $result.Dir = $found.Dir
        $result.Dll = $found.Dll
        $result.Source = $found.Source
    }
    if ($only32) { $result.Dir32 = $only32.Dir }
    if ($noPlugins) { $result.DirNoPlugins = $noPlugins.Dir }
    return $result
}

# --- winget ------------------------------------------------------------------
#
# winget returns non-zero for several reasons that are not failures, and the
# installer used to judge the result by re-testing a file path instead of
# reading the code. The one that matters most is 0x8A15002B, which is what
# `winget install` gives back when the package is already there at an equal or
# newer version - a success being reported as a failure, on the machine where
# everything is already fine.
#
# 0x8A15002B, 0x8A150014 and 0x8A150017 were confirmed by running winget on the
# development machine. The rest are from winget's published return codes and are
# here to turn a bare hexadecimal number into a sentence, not to be relied on:
# anything not in the table falls through to "winget said no", and the real
# verdict comes from looking for the thing afterwards.
#
# That last part is not a formality. `winget show --id VideoLAN.VLC
# --architecture x86` and `--architecture x64` both return an installer - a
# win32 MSI and a win64 MSI - so which one an unpinned `winget install` picks is
# a property of the machine, and a completely successful exit code can leave
# behind a VLC that 64-bit Python cannot load. Checked on this machine.
#
# Keyed by the hexadecimal form, which is the form winget's own documentation
# uses and therefore the form worth having in a log somebody will search.
$script:WINGET_MEANING = @{
    '0x00000000' = 'installed'
    '0x8A15002B' = 'already installed, and up to date'
    '0x8A150008' = 'the download failed'
    '0x8A15000B' = "winget's list of packages could not be read"
    '0x8A150010' = 'winget has no installer for this machine'
    '0x8A150011' = 'the download did not match its checksum'
    '0x8A150014' = 'winget does not know that package'
    '0x8A150017' = 'winget has no such version of that package'
    '0x8A150019' = 'winget needs administrator rights for this'
}
# The two codes that mean the machine is already in the state we wanted.
$script:WINGET_BENIGN = @('0x00000000', '0x8A15002B')

function Invoke-Winget([string[]]$arguments) {
    if (-not (Test-Have 'winget')) {
        return [pscustomobject]@{
            Ran = $false; Ok = $false; Code = 'not run'
            Reason = 'winget is not on this machine'
        }
    }
    $code = Invoke-Logged 'winget' $arguments
    # PowerShell hands winget's code back as a negative number and winget writes
    # it as an unsigned hexadecimal. Same value; only one of them is searchable.
    $text = '0x{0:X8}' -f [uint32]($code -band 0xFFFFFFFFL)
    $meaning = $script:WINGET_MEANING[$text]
    if (-not $meaning) { $meaning = "winget stopped with $text" }
    return [pscustomobject]@{
        Ran = $true
        Ok = ($script:WINGET_BENIGN -contains $text)
        Code = $text
        Reason = $meaning
    }
}

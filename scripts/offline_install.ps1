# =============================================================================
#  Installs VMD on the laptop that has no internet.
#
#  Double-click offline-install.bat. Nothing here touches the network,
#  and nothing here needs to: everything except VLC arrived inside this folder,
#  put there by scripts\offline_kit.ps1 on the machine that had a connection.
#
#  What it actually does, in one sentence each:
#
#    - repairs the two absolute paths a copied environment cannot carry,
#    - puts bin\ on PATH so uv, ffmpeg and the console can find each other,
#    - installs VLC from the installer that travelled in bin\vendor\,
#    - proves the environment runs, without asking the network anything,
#    - makes the recorder and the console start by themselves after a restart.
#
#  It is safe to run twice.
# =============================================================================
param(
    [switch]$NoLaunch,
    [switch]$NoAutostart
)

$ErrorActionPreference = 'Stop'
# See the note in scripts\_common.ps1: under PowerShell 7.4 and later, 'Stop'
# also applies to the exit code of an ordinary program. Set there and repeated
# here so that a reader of this file alone can see it.
$PSNativeCommandUseErrorActionPreference = $false
. (Join-Path $PSScriptRoot '_common.ps1')

$root   = Get-ProjectRoot
$binDir = Join-Path $root 'bin'

$logPath = Start-VmdTranscript $root 'offline-install.log'

# Three groups rather than a wall of yellow: what works, what is missing but
# optional, and what must be fixed before this system is relied on. Explained
# in scripts\install.ps1, which reaches the same verdicts the same way.
$findings = New-Object System.Collections.ArrayList
function Add-Good($what)                     { [void]$findings.Add(@{ Level = 'good';     What = $what; Fix = @() }) }
function Add-Optional($what, [string[]]$fix) { [void]$findings.Add(@{ Level = 'optional'; What = $what; Fix = $fix }) }
function Add-Broken($what, [string[]]$fix)   { [void]$findings.Add(@{ Level = 'broken';   What = $what; Fix = $fix }) }

function Write-Summary {
    $good     = @($findings | Where-Object { $_.Level -eq 'good' })
    $optional = @($findings | Where-Object { $_.Level -eq 'optional' })
    $broken   = @($findings | Where-Object { $_.Level -eq 'broken' })

    Write-Host ""
    Write-Host "  ==============================================================" -ForegroundColor DarkGray
    Write-Host "  INSTALLED AND WORKING" -ForegroundColor Green
    Write-Host "  ==============================================================" -ForegroundColor DarkGray
    if ($good.Count -eq 0) { Write-Host "    (nothing yet)" -ForegroundColor DarkGray }
    foreach ($item in $good) { Write-Host "    - $($item.What)" -ForegroundColor Green }

    Write-Host ""
    Write-Host "  ==============================================================" -ForegroundColor DarkGray
    Write-Host "  MISSING, BUT THE SYSTEM STILL DOES ITS JOB WITHOUT IT" -ForegroundColor Yellow
    Write-Host "  ==============================================================" -ForegroundColor DarkGray
    if ($optional.Count -eq 0) {
        Write-Host "    Nothing is missing." -ForegroundColor Green
    }
    foreach ($item in $optional) {
        Write-Host "    - $($item.What)" -ForegroundColor Yellow
        foreach ($line in $item.Fix) { Write-Host "        $line" -ForegroundColor Gray }
    }

    Write-Host ""
    Write-Host "  ==============================================================" -ForegroundColor DarkGray
    # The heading says which of the two states this is, in the words themselves.
    #
    # It read "BROKEN - MUST BE FIXED BEFORE THE SYSTEM IS USED" either way,
    # green over "Nothing." on a good install and red over a list on a bad
    # one. On the screen the colour carries it. In the LOG it does not - and
    # the log is the exact thing this installer asks people to send when
    # something looks wrong, so the one artefact meant for diagnosing a
    # fault was the one where a clean install and a broken one read the
    # same. It was read as an error on a machine where nothing was wrong.
    if ($broken.Count -eq 0) {
        Write-Host "  NOTHING IS BROKEN - THIS SYSTEM CAN BE USED" -ForegroundColor Green
        Write-Host "  ==============================================================" -ForegroundColor DarkGray
        Write-Host "    Every check passed. There is nothing to fix." -ForegroundColor Green
    } else {
        Write-Host "  BROKEN - MUST BE FIXED BEFORE THE SYSTEM IS USED" -ForegroundColor Red
        Write-Host "  ==============================================================" -ForegroundColor DarkGray
        foreach ($item in $broken) {
            Write-Host "    - $($item.What)" -ForegroundColor Red
            foreach ($line in $item.Fix) { Write-Host "        $line" -ForegroundColor Gray }
        }
    }
    Write-Host ""
}

function Write-LogHint {
    if (-not $logPath) {
        Write-Host "  This run could not be written to a file, so there is no log to send." -ForegroundColor DarkGray
        return
    }
    Write-Host "  Everything this installer printed is saved in:" -ForegroundColor Gray
    Write-Host "    $logPath" -ForegroundColor White
    Write-Host "  If anything above looks wrong, that file is the whole story. Copy it" -ForegroundColor Gray
    Write-Host "  onto the USB drive and send it; passwords are taken out of it first." -ForegroundColor Gray
}

function Stop-Installer($code) {
    Write-Host ""
    Write-LogHint
    Write-Host ""
    Stop-VmdTranscript $root | Out-Null
    exit $code
}

try {

Write-Host ""
Write-Host "  VMD offline installer" -ForegroundColor White
Write-Host "  $root" -ForegroundColor DarkGray

Set-StepTotal 7

# =============================================================================
#  1. is this a prepared copy?
# =============================================================================
# The failure this catches is the one worth catching early: somebody downloads
# the ZIP from GitHub, carries that to the offline laptop, and runs this. A ZIP
# has no .venv, no uv and no interpreter, and there is no way to build them
# here. Better to say so in one sentence than to fail five steps later in a way
# that reads like a bug.
Write-Step "Checking that this folder came from the connected machine"
$fatal = @()
foreach ($item in @(
    @{ Path = (Join-Path $root '.venv\Scripts\python.exe'); What = 'the Python environment (.venv)' }
    @{ Path = (Join-Path $binDir 'uv.exe');                 What = 'uv (bin\uv.exe)' }
)) {
    if (Test-Path $item.Path) { Write-Ok $item.What }
    else { Write-Bad "MISSING - $($item.What)"; $fatal += $item.What }
}
$python = Find-ProjectPython $root
if ($python) { Write-Ok "the project's own Python (bin\python\)" }
else { Write-Bad "MISSING - the project's own Python (bin\python\)"; $fatal += 'bin\python\' }

if ($fatal.Count -gt 0) {
    Write-Host ""
    Write-Bad "This is not a prepared copy - it is the plain project files."
    Write-Info "There is no way to finish the install on a machine with no internet."
    Write-Info "On a machine that has one: run install.bat, then"
    Write-Info "offline-kit.bat, and bring the folder that produces."
    Stop-Installer 1
}

# =============================================================================
#  2. the two paths a copy cannot carry
# =============================================================================
# .venv\pyvenv.cfg holds the absolute path of the interpreter, and
# _editable_impl_vmd.pth holds the absolute path of the project. Both were
# written on the other machine. If this folder landed at the same place it was
# built - C:\VMD on both - they are already right and nothing happens here.
Write-Step "Pointing the environment at this machine"
$repaired = Repair-VenvPaths $root
if ($repaired.Count -eq 0) {
    Write-Ok "Nothing to change - the folder is where it was built."
} else {
    foreach ($item in $repaired) { Write-Ok "Corrected $item" }
}

# =============================================================================
#  3. PATH
# =============================================================================
Write-Step "Putting bin\ on PATH"
Write-Info "bin\ holds uv and ffmpeg. VMD.exe looks for uv on PATH and nowhere"
Write-Info "else, and the recorder runs ffmpeg by name, so this is not optional."
if (Add-BinToUserPath $binDir) { Write-Ok "bin\ added to your PATH." }
else { Write-Ok "bin\ is already on your PATH." }

# =============================================================================
#  4. VLC
# =============================================================================
# The only thing that cannot live inside the folder. libVLC is found by
# python-vlc through the registry, so it has to be a real installation.
# Where VLC is looked for is the whole of scripts\_common.ps1's Get-VlcInstall:
# the registry keys python-vlc itself reads, the uninstall entries, the ordinary
# folders and PATH, with libvlc.dll checked to be really there. Asking only
# whether %ProgramFiles%\VideoLAN\VLC\libvlc.dll exists is what made this
# installer tell somebody VLC was missing while they were looking at it in their
# own Start menu.
#
# No verdict is reached here. Step 5 asks Python whether it can load libVLC,
# which is the only question that decides anything, and printing a conclusion
# now that step 5 may contradict is how an operator learns to ignore this.
Write-Step "Installing VLC (draws the live picture in the console)"
$vlc = Get-VlcInstall
if ($vlc.Found) {
    Write-Ok "VLC is already installed: $($vlc.Dir)"
    Write-Info "Found through $($vlc.Source)."
} else {
    if ($vlc.Only32) {
        Write-Info "The VLC on this machine is the 32-bit build, in $($vlc.Dir32)."
        Write-Info "64-bit Python cannot load a 32-bit libVLC, so the 64-bit build"
        Write-Info "in bin\vendor\ is installed alongside it."
    }
    $installer = Join-Path $binDir 'vendor\vlc-win64.exe'
    if (Test-Path $installer) {
        Write-Info "Windows will ask for permission. Click Yes."
        Write-Info "The installer runs without showing anything; give it a minute."
        try {
            # /L=1033 /S is NSIS: English, silent. Verb RunAs raises exactly one
            # permission prompt, which is the whole of what this install needs
            # administrator rights for.
            Start-Process -FilePath $installer -ArgumentList '/L=1033', '/S' -Verb RunAs -Wait
        } catch {
            Write-Info "The VLC installer did not run: $($_.Exception.Message)"
        }
        $vlc = Get-VlcInstall
        if ($vlc.Found) { Write-Ok "VLC installed: $($vlc.Dir)" }
        else {
            Write-Info "No 64-bit libvlc.dll found yet. If the permission prompt was"
            Write-Info "refused, double-click this and click through it yourself:"
            Write-Info "  $installer"
        }
    } else {
        Write-Info "There is no VLC installer in bin\vendor\, so none can be installed here."
        Write-Info "Step 5 says what that costs."
    }
}

# libVLC rebuilds its plugin index whenever the index is older than the plugins,
# printing a line per plugin and taking about fifteen seconds over it. Doing it
# once here means the operator's first console start is not fifteen seconds of
# blank screen that looks exactly like a hang.
#
# Whether it worked is read off the file it is supposed to write, rather than
# from the fact that Start-Process returned. Start-Process returns whether or
# not the program it started did anything at all.
if ($vlc.Found) {
    $cacheGen = Join-Path $vlc.Dir 'vlc-cache-gen.exe'
    $pluginDir = Join-Path $vlc.Dir 'plugins'
    if ((Test-Path $cacheGen) -and (Test-Path $pluginDir)) {
        $cacheFile = Join-Path $pluginDir 'plugins.dat'
        $before = $null
        if (Test-Path $cacheFile) { $before = (Get-Item $cacheFile).LastWriteTimeUtc }
        try { Start-Process -FilePath $cacheGen -ArgumentList $pluginDir -Verb RunAs -Wait } catch { }
        $after = $null
        if (Test-Path $cacheFile) { $after = (Get-Item $cacheFile).LastWriteTimeUtc }
        if ($after -and ($null -eq $before -or $after -gt $before)) {
            Write-Ok "VLC's plugin index was rebuilt, so the console starts faster."
        } else {
            Write-Info "VLC's plugin index was left alone. Harmless - the first start is just slow."
        }
    }
}

# =============================================================================
#  5. does it run?
# =============================================================================
# Every command here is offline by construction. `uv run --offline --frozen
# --no-sync` is the same incantation VMD.bat and vmd\launcher.py use, and for
# the same reason: a plain `uv run` re-checks the lock and syncs, and a sync on
# this machine is a hang with no way out.
Write-Step "Checking that it runs, without asking the network anything"
$uv = Join-Path $binDir 'uv.exe'
Push-Location $root
try {
    # `vmd` is in that list because of what the list without it let through:
    # every third-party library imported, the installer said "The libraries
    # import", and the console then died with
    #
    #     Error while finding module specification for 'vmd.desktop'
    #     (ModuleNotFoundError: No module named 'vmd')
    #
    # The project itself is not installed into .venv as files - it is one line
    # in _editable_impl_vmd.pth naming the folder it lives in - so it is the one
    # part of the environment a copy to another machine can break, and it was
    # the one part nothing checked. Step 2 repairs that line; this is what
    # proves the repair worked.
    $import = Invoke-Captured $uv @('run', '--offline', '--frozen', '--no-sync', 'python', '-c',
                                    'import cv2, pydantic, ultralytics, vmd; print(''libraries ok'')')
    if ($import.Code -ne 0 -or -not ($import.Out -contains 'libraries ok')) {
        foreach ($line in ($import.Err | Select-Object -Last 4)) { Write-Bad "  $line" }
        Write-Bad "The environment does not run on this machine."
        Write-Info "The message above is the whole diagnosis. 'No Python at ...' means"
        Write-Info "the copy is missing bin\python\ - go back to the connected machine"
        Write-Info "and run offline-kit.bat, which checks for exactly that."
        Add-Broken "the Python environment does not run, so nothing works at all" @(
            "Go back to the connected machine, run install.bat then offline-kit.bat,",
            "and bring the folder that produces."
        )
        Write-Summary
        Stop-Installer 1
    }
    Write-Ok "The libraries import."
    Add-Good "the Python environment - the console and the recorder run"

    # The only question about VLC that decides anything, and it is the console's
    # own question. The console no longer lets python-vlc do the searching:
    # vmd\desktop\libvlc.py finds VLC itself, checks the architecture and the
    # plugins folder, and hands the answer over with os.add_dll_directory,
    # because since Python 3.8 ctypes does not look along PATH for a dependent
    # DLL. So this runs that loader, and reuses its refusals verbatim - they are
    # already written for whoever is standing at this laptop, and the video pane
    # shows the same words.
    #
    # Its stderr is captured rather than shown: libVLC prints a line per plugin
    # - about fifty kilobytes of "stale plugins cache" - whenever its index is
    # older than the plugins, and none of it means anything is wrong.
    Write-Info "Asking the console's own loader whether it can draw the live picture."
    $probeFile = Join-Path $env:TEMP 'vmd-vlc-probe.py'
    Set-Content -Path $probeFile -Encoding UTF8 -Value @'
# Written by scripts\offline_install.ps1. Prints one line, and always exits 0 so
# that the installer classifies on the line rather than on an exit code that
# python-vlc sometimes sets by calling sys.exit() from inside an import.
import sys

where = ""
try:
    from vmd.desktop.libvlc import prepare
except Exception:
    prepare = None

try:
    if prepare is not None:
        where = str(prepare().folder)
    import vlc

    vlc.Instance()
except SystemExit as stop:
    print("VLCNO python-vlc stopped the process (exit %s) instead of raising." % stop.code)
    sys.exit(0)
except BaseException as failure:
    print("VLCNO " + " ".join(str(failure).split()))
    sys.exit(0)

print("VLCOK " + where)
'@
    $probe = Invoke-Captured $uv @('run', '--offline', '--frozen', '--no-sync', 'python', $probeFile)
    Remove-Item $probeFile -Force -ErrorAction SilentlyContinue
    $answer = @($probe.Out | Where-Object { $_ -match '^VLC(OK|NO)' } | Select-Object -Last 1)
    if ($answer.Count -eq 1 -and $answer[0].StartsWith('VLCOK')) {
        $where = $answer[0].Substring(5).Trim()
        Write-Ok "Yes - the console can draw the live picture."
        if ($where) { Write-Info "  Using the VLC in $where" }
        Add-Good "VLC - the console loaded libVLC, so it shows the live picture"
    } else {
        $detail = if ($answer.Count -eq 1) { $answer[0].Substring(5).Trim() }
                  else { ($probe.Err | Where-Object { $_ -and ($_ -notmatch 'stale plugins cache') } |
                          Select-Object -Last 1) }
        Write-Info "No - the console will show no live picture. Nothing else is affected."
        if ($detail) { Write-Info "  $detail" }
        # One entry, carrying the loader's own sentence: it already names which
        # failure this is - not installed, the 32-bit one, or one with no
        # plugins folder - and where it looked.
        Add-Optional "the console will show no live picture" @(
            $(if ($detail) { $detail } else { "The check gave no reason." }),
            "Everything else, recording included, works without it.",
            "The 64-bit installer that travelled with this copy is at:",
            "  $(Join-Path $binDir 'vendor\vlc-win64.exe')",
            "Adding VLC to PATH does not help and is not what the console reads."
        )
    }
}
finally { Pop-Location }

if (Test-Path (Join-Path $binDir 'ffmpeg.exe')) {
    Write-Ok "ffmpeg is in bin\, which is on PATH."
    Add-Good "ffmpeg, in bin\ffmpeg.exe - the recorder can record"
} else {
    Write-Bad "No ffmpeg in bin\. Nothing can be recorded until there is."
    Add-Broken "ffmpeg is missing, so nothing can be recorded - and recording is the product" @(
        "It cannot be downloaded here. Go back to the connected machine, run",
        "install.bat then offline-kit.bat, and bring the folder that produces."
    )
}

if (Test-Path (Join-Path $binDir 'go2rtc.exe')) {
    Write-Ok "go2rtc is in bin\."
    Add-Good "go2rtc, in bin\go2rtc.exe - the console can be given a stream to show"
} else {
    Write-Info "No go2rtc in bin\, so the console will show no live picture."
    Add-Optional "go2rtc is missing, so the console will show no live picture" @(
        "Recording is not affected - it reads the camera directly.",
        "It cannot be downloaded here; it has to come over on the USB drive."
    )
}

$weights = Join-Path $root 'yolo11n.pt'
if ((Test-Path $weights) -and ((Get-Item $weights).Length -gt 1MB)) {
    Write-Ok "The detector's weights are here (yolo11n.pt)."
    Add-Good "the detector's weights (yolo11n.pt) - kept for the day naming comes back"
} else {
    Write-Info "No yolo11n.pt. Nothing reads it today - naming what moved is off."
    Add-Optional "yolo11n.pt is missing - nothing reads it today" @(
        "Recording and detection are unaffected: nothing reads this file today.",
        "Nothing will be downloaded, because this machine has no internet."
    )
}

if (Test-Path (Join-Path $root 'VMD.exe')) {
    Add-Good "VMD.exe - the icon to double-click"
} else {
    Write-Info "VMD.exe did not travel. VMD.bat does the same thing."
    Add-Optional "VMD.exe did not travel with the copy" @(
        "Not a problem - double-click VMD.bat instead. It does the same thing."
    )
}

# =============================================================================
#  6. starting by itself
# =============================================================================
Write-Step "Making the system start by itself after a restart"
Write-Info "Windows may ask for permission again here. Creating the two scheduled"
Write-Info "tasks needs it on some machines and not on others; if it asks, click Yes."
Write-Info "Saying no is not fatal - shortcuts in the Startup folder are used instead."
if ($NoAutostart) {
    Write-Info "Skipped, because -NoAutostart was given."
} else {
    try {
        & (Join-Path $PSScriptRoot 'autostart.ps1') -Install | Out-Host
    } catch {
        Write-Info "Could not set up automatic starting: $($_.Exception.Message)"
    }
    # Asked of Windows rather than assumed from the fact that the script
    # returned. On this laptop it is the step whose failure costs the most and
    # shows the least: nothing looks wrong until the day the power comes back.
    #
    # Both mechanisms count. On the first machine this was deployed to, Windows
    # answered "Access is denied" to Register-ScheduledTask and the summary said
    # nothing would start after a restart - which was true, and had no way to
    # become untrue. autostart.ps1 now retries with administrator permission and
    # falls back to the Startup folder, and this reads whichever of the two
    # succeeded.
    $verdict = Get-AutostartVerdict $root
    switch ($verdict.Level) {
        'good'     { Write-Ok $verdict.Say;   Add-Good $verdict.What }
        'optional' { Write-Warn $verdict.Say; Add-Optional $verdict.What $verdict.Fix }
        default    { Write-Bad $verdict.Say;  Add-Broken $verdict.What $verdict.Fix }
    }
}

# =============================================================================
#  7. done
# =============================================================================
Write-Step "Starting the console"
Write-Summary
Write-LogHint
Write-Host ""
Write-Host "  Next, and this machine has two cameras:" -ForegroundColor White
Write-Host ""
Write-Host "    1. Double-click cameras.bat, once for each camera. It asks for" -ForegroundColor Gray
Write-Host "       the camera's address, what it watches, and which screen it" -ForegroundColor Gray
Write-Host "       belongs on, and puts a shortcut on the desktop for it." -ForegroundColor Gray
Write-Host ""
Write-Host "    2. Open each camera's shortcut and type that camera's username," -ForegroundColor Gray
Write-Host "       password and stream addresses into the Settings tab. Save." -ForegroundColor Gray
Write-Host ""
Write-Host "    3. Run autostart-on.bat once, when both cameras are set up." -ForegroundColor Gray
Write-Host ""
Write-Host "  Recording does not start until step 2 is done, because until then" -ForegroundColor Gray
Write-Host "  there is no camera to record." -ForegroundColor Gray
Write-Host ""
Write-Host "  With only one camera, skip step 1: the Settings tab in the console" -ForegroundColor Gray
Write-Host "  that opens next is all there is to fill in." -ForegroundColor Gray
Write-Host ""

# Closed before the console starts. The console is a long-running program with a
# camera address and a password in it, and a transcript left running over the
# top of it would put whatever it prints into a file whose whole purpose is that
# it can be sent to somebody else.
Stop-VmdTranscript $root | Out-Null

if ($NoLaunch) { exit 0 }

$exe = Join-Path $root 'VMD.exe'
if (Test-Path $exe) { & $exe }
else {
    Push-Location $root
    try { & (Join-Path $root 'VMD.bat') }
    finally { Pop-Location }
}
exit 0

}
finally {
    # A crash still leaves a readable file. Does nothing on the ordinary paths,
    # which have already closed it.
    Stop-VmdTranscript $root | Out-Null
}

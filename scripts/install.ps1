# =============================================================================
#  VMD installer. Run it by double-clicking install.bat in the project root.
#
#  What it leaves behind, and why each piece is where it is:
#
#    uv          installed machine-wide by winget, and copied into bin\ as well.
#                The copy is the one that matters: it is what travels to the
#                offline laptop, where there is no winget to install anything.
#    VLC         installed machine-wide. libVLC is a real installation with a
#                registry entry and a plugin tree; it cannot live in bin\.
#    ffmpeg      bin\ffmpeg.exe. Not installed machine-wide any more: the
#                recorder runs the bare name "ffmpeg", bin\ is on PATH, and one
#                ffmpeg that travels with the project beats two that do not.
#    go2rtc      bin\go2rtc.exe.
#    Python      bin\python\. Inside the project on purpose - see step 8.
#    yolo11n.pt  the project root, which is where vmd\detect\classify.py looks.
#    .venv\      the libraries, built against bin\python\.
#    VMD.exe     a launcher for the project it sits in, carrying no code.
#
#  Everything except VLC is inside the project folder, so the folder is the
#  install. That is what makes the offline laptop possible at all.
#
#  ---------------------------------------------------------------------------
#  Two rules this file is held to
#  ---------------------------------------------------------------------------
#
#  1. Never say something is wrong without having checked that it is.
#
#     An installer that cries wolf is worse than one that fails loudly, because
#     the operator learns to ignore it and then misses the message that
#     mattered. Every claim below is either a thing this script just observed,
#     or it is not printed. In particular the verdict on VLC is not reached in
#     step 3 - it cannot be, because the only test that settles it needs the
#     Python environment that step 9 builds - so step 3 reports what it can see
#     and says so, and step 9 decides.
#
#  2. Nothing computed in the elevated window may be read in this one.
#
#     The machine-wide half runs as a second, elevated process. Its variables
#     die with it. Anything this window needs to know afterwards is either
#     looked up again here, or read out of bin\logs\install-packages.json,
#     which that window writes on its way out. That file also carries the
#     absolute path of the uv it installed, because "elevated" can mean a
#     different Windows account, and a uv installed into somebody else's
#     profile is invisible from here.
#
#  Everything printed is also written to bin\logs\install.log.
#
#  Switches, all of them for scripts and tests rather than for people:
#    -NoLaunch      finish without opening the console.
#    -NoAutostart   skip creating the scheduled tasks.
#    -PackagesOnly  only the machine-wide half (winget, VLC). Used when this
#                   script relaunches itself elevated.
#    -SkipPackages  only the project half. The other side of the same coin.
# =============================================================================
param(
    [switch]$NoLaunch,
    [switch]$NoAutostart,
    [switch]$PackagesOnly,
    [switch]$SkipPackages
)

$ErrorActionPreference = 'Stop'

# Set again in _common.ps1, and repeated here so that somebody reading only this
# file can see it. Under PowerShell 7.4 and later, 'Stop' also applies to the
# exit code of an ordinary program, and winget returns non-zero for entirely
# benign reasons - "already installed" among them. install.bat launches Windows
# PowerShell 5.1, where this does not arise, but right-clicking this file and
# choosing "Run with PowerShell" uses whichever PowerShell is default. Native
# exit codes are read explicitly wherever they matter.
$PSNativeCommandUseErrorActionPreference = $false

. (Join-Path $PSScriptRoot '_common.ps1')

$root   = Get-ProjectRoot
$binDir = Join-Path $root 'bin'

$doPackages = -not $SkipPackages
$doProject  = -not $PackagesOnly

# The elevated half and this half must not write to the same transcript: the
# first one to hold it keeps the file open, and the second would silently get
# no log at all - on exactly the run where a log is most wanted.
$logName = if ($PackagesOnly) { 'install-admin.log' } else { 'install.log' }
$logPath = Start-VmdTranscript $root $logName
$handoff = Join-Path (Get-LogDir $root) 'install-packages.json'

# --- what the summary at the end is built out of -----------------------------
#
# Three groups, decided as each step happens rather than guessed at the end:
#
#   good      installed, and checked to be working.
#   optional  missing, and the system still does its job without it. The live
#             picture is the clearest example: the console opens, the recorder
#             records, and the video pane says so in place of the picture.
#   broken    the system must not be relied on until this is fixed.
#
# Write-Bad used to be used for all three, which is how "VLC is not installed"
# ended up in the same colour as an environment that would not build.
$findings = New-Object System.Collections.ArrayList
function Add-Good($what)                     { [void]$findings.Add(@{ Level = 'good';     What = $what; Fix = @() }) }
function Add-Optional($what, [string[]]$fix) { [void]$findings.Add(@{ Level = 'optional'; What = $what; Fix = $fix }) }
function Add-Broken($what, [string[]]$fix)   { [void]$findings.Add(@{ Level = 'broken';   What = $what; Fix = $fix }) }

# Defined here rather than beside the step that prints it, because the steps
# that end the run early print it too, and a function is only callable after
# PowerShell has read it.
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
        Write-Host "    Nothing. Everything that could be installed was." -ForegroundColor Green
    }
    foreach ($item in $optional) {
        Write-Host "    - $($item.What)" -ForegroundColor Yellow
        foreach ($line in $item.Fix) { Write-Host "        $line" -ForegroundColor Gray }
    }

    Write-Host ""
    Write-Host "  ==============================================================" -ForegroundColor DarkGray
    # The heading says which of the two states this is, in the words themselves.
    #
    # It used to read "BROKEN - MUST BE FIXED BEFORE THE SYSTEM IS USED" either
    # way, green over "Nothing." on a good install and red over a list on a bad
    # one. On the screen the colour carries it. In the LOG it does not - and the
    # log is the exact thing this installer asks people to send when something
    # looks wrong, so the one artefact meant for diagnosing a fault was the one
    # where a clean install and a broken one read the same. It was read as an
    # error on a machine where nothing at all was wrong.
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
    $adminLog = Join-Path (Get-LogDir $root) 'install-admin.log'
    if ((-not $PackagesOnly) -and (Test-Path $adminLog)) {
        Write-Host "    $adminLog" -ForegroundColor White
        Write-Host "    (the second file is the window that asked for permission)" -ForegroundColor DarkGray
    }
    Write-Host "  If anything above looks wrong, send that file. It is the whole story," -ForegroundColor Gray
    Write-Host "  and passwords are taken out of it before it is written." -ForegroundColor Gray
}

# Used for the failures that end the run. Prints the log path, because a person
# who has just been told the install stopped is exactly the person who needs to
# know which file to send.
function Stop-Installer($code) {
    Write-Host ""
    Write-LogHint
    Write-Host ""
    Stop-VmdTranscript $root | Out-Null
    exit $code
}

try {

Write-Host ""
Write-Host "  VMD installer" -ForegroundColor White
Write-Host "  $root" -ForegroundColor DarkGray

Set-StepTotal $(if ($PackagesOnly) { 3 } else { 12 })

# A prepared offline copy carries a VLC installer, which nothing else puts
# there. Running this script on such a folder is the mistake somebody makes on
# the day they are standing at the laptop that has no network, and it fails at
# winget with a message about the Microsoft Store. Say the right thing instead.
# Informative rather than fatal: this file is also what built that copy, and
# re-running it on the connected machine has to keep working.
if ((Test-Path (Join-Path $binDir 'vendor\vlc-win64.exe')) -and
    (Test-Path (Join-Path $root '.venv'))) {
    Write-Host ""
    Write-Warn "This folder looks like a copy prepared for a machine with no internet."
    Write-Warn "If this machine has no internet, close this window and double-click"
    Write-Warn "offline-install.bat instead - it needs no connection."
    Write-Warn "If this machine does have internet, carry on; nothing is wrong."
    Write-Host ""
}

function Update-PathFromRegistry {
    # A winget install writes the new folder into the stored PATH, not into
    # this already-running process. Without this the command stays "missing"
    # until the window is reopened, which is exactly the confusion this script
    # exists to avoid.
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ';'
}

# =============================================================================
#  One permission prompt, not five
# =============================================================================
#
# winget's machine-wide installs each raise their own UAC prompt when this runs
# unelevated, and a beginner who says No to one of five identical prompts ends
# up with a half-installed machine and no idea which half. So the machine-wide
# steps are done once, together, in a single elevated window; the rest stays
# unelevated, which is what we want anyway - the console, the settings file and
# the recordings should belong to the person using the laptop, not to
# Administrator.
$packages = $null       # what the elevated window reported, or $null
$elevatedCode = $null   # what it exited with, or $null if it never ran

if ($doPackages -and $doProject -and -not (Test-Admin)) {
    # Any note left by a previous run would be read as this run's answer.
    Remove-Item $handoff -Force -ErrorAction SilentlyContinue
    Write-Host ""
    Write-Info "Windows is about to ask for permission, once."
    Write-Info "A second window will open, install VLC and uv, and close by itself."
    Write-Info "Click Yes when Windows asks. Then this window carries on."
    try {
        # -PassThru, because the exit code of that window is the only thing it
        # can tell us directly, and ignoring it meant this window went on to
        # print "Already checked." about steps that had failed.
        $child = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', $PSCommandPath, '-PackagesOnly'
        )
        $elevatedCode = $child.ExitCode
        Update-PathFromRegistry
        $doPackages = $false
    } catch {
        # Refusing the prompt is a decision, not a crash. Carry on unelevated:
        # winget will ask again for itself, and if that is refused too, the
        # steps below say plainly what is missing.
        Write-Warn "Permission was not given, so the installer will ask again per item."
    }
    if (Test-Path $handoff) {
        try { $packages = ConvertFrom-Json (Get-Content $handoff -Raw -ErrorAction Stop) }
        catch { $packages = $null }
    }
    if ($null -eq $packages -and -not $doPackages) {
        Write-Warn "The window that asked for permission left no record of what it did."
        Write-Info "Nothing is assumed about it. Everything below is checked here instead."
    }
}

# =============================================================================
#  1. winget
# =============================================================================
Write-Step "Checking the Windows package manager"
$haveWinget = Test-Have 'winget'
if ($doPackages) {
    if ($haveWinget) { Write-Ok "winget is available." }
} elseif ($packages) {
    # Asked again here rather than trusted: winget is per-account, and the
    # window that just closed may not have been this account.
    if ($haveWinget) { Write-Ok "winget is available." }
    elseif ($packages.WingetOk) {
        Write-Ok "winget is available to the administrator account, which is where it was needed."
    }
} elseif ($haveWinget) {
    Write-Ok "winget is available."
}
if (-not $haveWinget -and -not ($packages -and $packages.WingetOk)) {
    # Not fatal on its own. winget is only needed for uv and VLC; ffmpeg,
    # go2rtc and the detector weights are ordinary downloads. Whether this
    # matters is decided in step 2, by whether uv can be found at all.
    Write-Warn "winget is not available on this account."
    Write-Info "It ships with 'App Installer'. Install that from the Microsoft Store"
    Write-Info "and run this installer again:"
    Write-Info "  https://apps.microsoft.com/detail/9nblggh4nns1"
}

# =============================================================================
#  2. uv
# =============================================================================
Write-Step "Checking uv (brings Python and the libraries with it)"
$uvWinget = $null
if ($doPackages) {
    if (Test-Have 'uv') {
        Write-Ok "uv is already installed."
    } else {
        Write-Info "uv is missing. Installing it with winget - this can take a few minutes."
        $uvWinget = Invoke-Winget @('install', '--id', 'astral-sh.uv', '--exact', '--silent',
                                    '--accept-source-agreements', '--accept-package-agreements')
        Update-PathFromRegistry
        # The exit code is read, and then the machine is looked at anyway. Both,
        # because either alone is wrong: a benign code with nothing on disk is a
        # false pass, and a non-zero code with a working uv is the false failure
        # this file exists to stop printing.
        if (Test-Have 'uv') {
            if ($uvWinget.Ok) { Write-Ok "uv installed ($($uvWinget.Reason))." }
            else { Write-Ok "uv is installed and runnable, although winget reported: $($uvWinget.Reason)." }
        } elseif ($uvWinget.Ran) {
            Write-Bad "uv is not runnable after installing it - winget said: $($uvWinget.Reason)"
        } else {
            Write-Bad "uv could not be installed, because $($uvWinget.Reason)."
        }
    }
} else {
    Update-PathFromRegistry
    if (Test-Have 'uv') { Write-Ok "uv is available." }
}

# Where uv actually is, asked in every place it could be, in the order that
# gives the most useful answer.
$uvLocal  = Join-Path $binDir 'uv.exe'
$uvSource = (Get-Command uv -ErrorAction SilentlyContinue).Source
if ((-not $uvSource) -and $packages -and $packages.UvPath -and (Test-Path $packages.UvPath)) {
    # winget installs uv into the profile of whoever answered the permission
    # prompt. On a laptop where the operator is a standard user and an
    # administrator's password was typed at that prompt, uv lands in the
    # administrator's profile and is invisible from here - which used to end the
    # install with "Cannot continue without uv" moments after installing uv.
    Write-Info "uv was installed under another Windows account. Using the copy it left."
    $uvSource = $packages.UvPath
}
if ((-not $uvSource) -and (Test-Path $uvLocal)) { $uvSource = $uvLocal }
if (-not $uvSource) {
    # Said here rather than left to silence. Whether it is fatal is decided a
    # few lines below, once the elevated half has had its say.
    Write-Bad "uv could not be found on this account, on PATH or in bin\."
}

if ($PackagesOnly) {
    # The elevated half is done. Its findings have to be written down here,
    # because in a moment this process and everything it knows will be gone.
    $vlcState = Get-VlcInstall
    $report = [ordered]@{
        Finished  = $true
        When      = (Get-Date).ToString('s')
        Account   = $env:USERNAME
        WingetOk  = [bool](Test-Have 'winget')
        UvPath    = $uvSource
        UvWinget  = $(if ($uvWinget) { $uvWinget.Reason } else { 'not run' })
        VlcFound  = [bool]$vlcState.Found
        VlcDir    = $vlcState.Dir
        VlcOnly32 = [bool]$vlcState.Only32
        VlcDir32  = $vlcState.Dir32
        VlcWinget = $null
    }
}

$haveUv = [bool]$uvSource

# =============================================================================
#  3. VLC
# =============================================================================
# The console draws its live video with libVLC, which comes with the ordinary
# VLC media player. It is not put on PATH by its installer, and it is not always
# in Program Files either, so this asks the registry keys python-vlc itself
# reads, the uninstall entries, the ordinary folders and PATH - and then checks
# that libvlc.dll is really there rather than trusting a key.
#
# What it does NOT do is reach a verdict. It cannot: the only test that settles
# the question is whether the 64-bit Python in this project can load libVLC, and
# that Python does not exist until step 9. So this step reports what it can see
# and says who decides. Printing a conclusion here that step 9 may contradict is
# how an installer teaches an operator to ignore it.
Write-Step "Checking VLC (draws the live picture in the console)"
$vlc = Get-VlcInstall
$vlcWinget = $null

if ($doPackages) {
    if ($vlc.Found) {
        Write-Ok "VLC is already here: $($vlc.Dir)"
        Write-Info "Found through $($vlc.Source). Nothing to install."
    } else {
        if ($vlc.Only32) {
            Write-Info "The VLC on this machine is the 32-bit build, in $($vlc.Dir32)."
            Write-Info "64-bit Python cannot load a 32-bit libVLC, so the 64-bit build is"
            Write-Info "needed as well. Installing it with winget."
        } elseif ($vlc.NoPlugins) {
            Write-Info "The VLC in $($vlc.DirNoPlugins) has no plugins folder beside it,"
            Write-Info "which would give a black picture and never say why. Installing it again."
        } else {
            Write-Info "No VLC found yet. Looked in $($vlc.Searched.Count) places, including the"
            Write-Info "registry in both hives and both views. Installing it with winget."
        }
        # --architecture x64 is the whole point of this call.
        #
        # winget's VideoLAN.VLC resolves to the x86 package on some machines,
        # and that is the most likely explanation for the deployment laptop:
        # the installer ran, winget reported success, VLC really is installed -
        # and it is 32-bit, which 64-bit Python physically cannot load. The
        # operator then reinstalls the same VLC and nothing changes, for ever.
        # Unpinned, this installer is what puts them in that loop.
        $vlcArgs = @('install', '--id', 'VideoLAN.VLC', '--exact', '--silent',
                     '--accept-source-agreements', '--accept-package-agreements')
        $vlcWinget = Invoke-Winget ($vlcArgs + @('--architecture', 'x64'))
        Update-PathFromRegistry
        $vlc = Get-VlcInstall
        if ((-not $vlc.Found) -and (-not $vlcWinget.Ok)) {
            # An older winget does not know --architecture, and some machines
            # genuinely have no x64 package for it to pick. Falling back is
            # better than refusing, because what landed is checked afterwards
            # either way - which is the part that actually protects anyone.
            Write-Info "Asking for the 64-bit build did not work ($($vlcWinget.Reason))."
            Write-Info "Trying without pinning the architecture, and checking what lands."
            $vlcWinget = Invoke-Winget $vlcArgs
            Update-PathFromRegistry
            $vlc = Get-VlcInstall
        }
        if ($vlc.Found) {
            Write-Ok "VLC is here now: $($vlc.Dir)"
            Write-Info "Checked: 64-bit libvlc.dll, with its plugins folder beside it."
        } elseif ($vlc.Only32) {
            # winget said yes and what arrived is unusable. This is the sentence
            # the whole exercise is for.
            Write-Bad "winget installed the 32-BIT VLC, in $($vlc.Dir32)."
            Write-Info "64-bit Python cannot load it, and running this installer again"
            Write-Info "will not change that. Uninstall it from Add or remove programs,"
            Write-Info "then install the 64-bit Windows build by hand from"
            Write-Info "  https://www.videolan.org/vlc/"
        } elseif ($vlc.NoPlugins) {
            Write-Bad "The VLC in $($vlc.DirNoPlugins) has no plugins folder beside it."
            Write-Info "It would load and then show a black picture without ever saying why."
            Write-Info "Install VLC again from https://www.videolan.org/vlc/"
        } elseif ($vlcWinget.Ok) {
            # winget is content and the file is not there. Say both halves.
            Write-Info "winget reported: $($vlcWinget.Reason) - but no usable libvlc.dll was found yet."
            Write-Info "Step 9 asks the console's own loader, which is the answer that counts."
        } elseif (-not $vlcWinget.Ran) {
            Write-Info "VLC could not be installed, because $($vlcWinget.Reason)."
            Write-Info "It can be installed by hand later from https://www.videolan.org/vlc/"
        } else {
            Write-Info "winget did not install VLC: $($vlcWinget.Reason)"
            Write-Info "Step 9 asks the console's own loader, which is the answer that counts."
        }
    }

    # libVLC checks its plugin index against the plugins on disk at every start.
    # When the index is older than the plugins - which it is after most VLC
    # upgrades - it rebuilds it, prints a line per plugin to stderr while it
    # does so, and the console's first window takes about fifteen seconds to
    # appear. Nothing is wrong, but fifteen seconds of blank screen reads as a
    # hang to somebody who has never seen it before. Regenerating the index once
    # here, while we still have the permission to write into Program Files, is
    # the only chance to fix it rather than explain it.
    #
    # This used to be `& $cacheGen ... 2>&1 | Out-Null` inside a try, which
    # could never work: vlc-cache-gen writes to stderr, the merge turns that
    # into a terminating error under $ErrorActionPreference = 'Stop', and the
    # catch printed "harmless" about work that had not happened. It is run
    # properly now, and the result is read off the file it is supposed to write.
    if ($vlc.Found -and (Test-Admin)) {
        $cacheGen = Join-Path $vlc.Dir 'vlc-cache-gen.exe'
        $pluginDir = Join-Path $vlc.Dir 'plugins'
        if ((Test-Path $cacheGen) -and (Test-Path $pluginDir)) {
            $cacheFile = Join-Path $pluginDir 'plugins.dat'
            $before = $null
            if (Test-Path $cacheFile) { $before = (Get-Item $cacheFile).LastWriteTimeUtc }
            $null = Invoke-Captured $cacheGen @($pluginDir)
            $after = $null
            if (Test-Path $cacheFile) { $after = (Get-Item $cacheFile).LastWriteTimeUtc }
            if ($after -and ($null -eq $before -or $after -gt $before)) {
                Write-Ok "VLC's plugin index was rebuilt, so the console starts faster."
            } else {
                Write-Info "VLC's plugin index was left alone. Harmless - the first start is just slow."
            }
        }
    }
} else {
    # The parent window, after the elevated one has closed. Its answer died with
    # it, so the question is asked again here rather than carried across.
    if ($vlc.Found) {
        Write-Ok "VLC is installed: $($vlc.Dir)"
    } elseif ($vlc.Only32) {
        Write-Info "The only VLC found is the 32-bit build, in $($vlc.Dir32)."
        Write-Info "Step 9 asks the console's loader about it, which is the answer that counts."
    } elseif ($vlc.NoPlugins) {
        Write-Info "The VLC in $($vlc.DirNoPlugins) has no plugins folder beside it."
        Write-Info "Step 9 asks the console's loader about it, which is the answer that counts."
    } else {
        Write-Info "No VLC found on disk from this account, in $($vlc.Searched.Count) places looked at."
        if ($packages -and $packages.VlcFound) {
            Write-Info "The window that asked for permission did find one, in $($packages.VlcDir)."
        }
        Write-Info "Step 9 asks the console's own loader, which is the answer that counts."
    }
}

if ($PackagesOnly) {
    # Re-read, because the install above may have changed it.
    $vlcState = Get-VlcInstall
    $report.VlcFound  = [bool]$vlcState.Found
    $report.VlcDir    = $vlcState.Dir
    $report.VlcOnly32 = [bool]$vlcState.Only32
    $report.VlcDir32  = $vlcState.Dir32
    $report.VlcWinget = $(if ($vlcWinget) { $vlcWinget.Reason } else { 'not run' })
    try {
        ($report | ConvertTo-Json -Depth 4) | Set-Content $handoff -Encoding UTF8
    } catch {
        Write-Warn "Could not write $handoff - the other window will check for itself."
    }
    Write-Host ""
    Write-Ok "Machine-wide components done. This window closes now."
    Write-Host ""
    Write-LogHint
    Start-Sleep -Seconds 3
    Stop-VmdTranscript $root | Out-Null
    exit 0
}

if (-not $haveUv) {
    Write-Host ""
    Write-Bad "Cannot continue without uv - it is what brings Python and every library."
    Write-Info "Install it by hand with:  winget install --id astral-sh.uv -e"
    Write-Info "then run install.bat again."
    if ($elevatedCode -and $elevatedCode -ne 0) {
        Write-Info "The window that asked for permission stopped with code $elevatedCode."
    }
    Stop-Installer 1
}

# =============================================================================
#  4. ffmpeg, in bin\
# =============================================================================
# vmd\storage\recorder.py runs the bare name "ffmpeg" and finds it on PATH.
# bin\ is put on PATH in step 7, so an ffmpeg.exe here is found by that name -
# and, unlike a winget install, it is inside the folder that gets copied to the
# offline laptop. This is also the path INSTALL.md has always named, which
# until now nothing actually read.
Write-Step "Checking ffmpeg (records the video)"
$ffmpeg = Join-Path $binDir 'ffmpeg.exe'
if (Test-Path $ffmpeg) {
    Write-Ok "ffmpeg is already here: bin\ffmpeg.exe"
    $haveFfmpeg = $true
} else {
    Write-Info "Downloading ffmpeg. This is about 170 MB and is the second-longest step."
    $zip = Join-Path $env:TEMP 'vmd-ffmpeg.zip'
    $unpack = Join-Path $env:TEMP 'vmd-ffmpeg-unpack'
    $haveFfmpeg = $false
    # BtbN's builds first, gyan.dev second. Both are the usual Windows ffmpeg
    # builds; the order is about the host rather than the build. gyan.dev is a
    # single small server that regularly delivers this file at a few hundred
    # kilobytes a second - slow enough to look like a hang, and slow enough to
    # time out - while BtbN publishes through GitHub's own release CDN.
    $sources = @(
        'https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip',
        'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
    )
    $got = $false
    foreach ($source in $sources) {
        if (Get-File $source $zip 'ffmpeg' (20MB)) { $got = $true; break }
        Write-Info "Trying another source."
    }
    if ($got) {
        try {
            Remove-Item $unpack -Recurse -Force -ErrorAction SilentlyContinue
            Expand-Archive -Path $zip -DestinationPath $unpack -Force
            New-Item -ItemType Directory -Force -Path $binDir | Out-Null
            foreach ($name in @('ffmpeg.exe', 'ffprobe.exe')) {
                $found = Get-ChildItem $unpack -Filter $name -Recurse -ErrorAction SilentlyContinue |
                    Select-Object -First 1
                if ($found) { Copy-Item $found.FullName (Join-Path $binDir $name) -Force }
            }
            $haveFfmpeg = Test-Path $ffmpeg
        } catch {
            Write-Bad "The ffmpeg download unpacked badly: $($_.Exception.Message)"
        } finally {
            Remove-Item $zip -Force -ErrorAction SilentlyContinue
            Remove-Item $unpack -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    if ($haveFfmpeg) { Write-Ok "ffmpeg installed to bin\ffmpeg.exe" }
    else { Write-Bad "Could not put ffmpeg in bin\. Nothing can be recorded without it." }
}
# An ffmpeg already on PATH from an earlier release of this installer still
# works, and saying so avoids a warning that contradicts a working machine.
$ffmpegElsewhere = $false
if (-not $haveFfmpeg -and (Test-Have 'ffmpeg')) {
    Write-Info "There is an ffmpeg on PATH from somewhere else; recording will use that."
    $haveFfmpeg = $true
    $ffmpegElsewhere = $true
}
if ($haveFfmpeg -and -not $ffmpegElsewhere) {
    Add-Good "ffmpeg, in bin\ffmpeg.exe - the recorder can record"
} elseif ($haveFfmpeg) {
    Add-Good "ffmpeg, found on PATH outside this folder - the recorder can record"
    Add-Optional "ffmpeg is not inside bin\, so it will not travel to the offline laptop" @(
        "Only matters if this folder is going to a machine with no internet.",
        "Put a copy of ffmpeg.exe in $binDir before running offline-kit.bat."
    )
} else {
    Add-Broken "ffmpeg is missing, so nothing can be recorded - and recording is the product" @(
        "Download the release essentials zip from https://www.gyan.dev/ffmpeg/builds/",
        "Open it and drag ffmpeg.exe into:  $binDir",
        "Then run install.bat again."
    )
}

# =============================================================================
#  5. go2rtc
# =============================================================================
# Not in winget, and it is a single self-contained binary, so it is fetched
# straight from the project's own releases and kept inside bin\ rather than
# installed system-wide. Deleting bin\ undoes it completely.
Write-Step "Checking go2rtc (re-serves the camera stream to the console)"
$go2rtc = Join-Path $binDir 'go2rtc.exe'
if (Test-Path $go2rtc) {
    Write-Ok "go2rtc is already here: bin\go2rtc.exe"
} else {
    $asset = switch ($env:PROCESSOR_ARCHITECTURE) {
        'ARM64' { 'go2rtc_win_arm64.zip' }
        'x86'   { 'go2rtc_win32.zip' }
        default { 'go2rtc_win64.zip' }
    }
    $zip = Join-Path $env:TEMP "vmd-$asset"
    Write-Info "Downloading $asset"
    if (Get-File "https://github.com/AlexxIT/go2rtc/releases/latest/download/$asset" $zip 'go2rtc' (1MB)) {
        try {
            New-Item -ItemType Directory -Force -Path $binDir | Out-Null
            Expand-Archive -Path $zip -DestinationPath $binDir -Force
            if (Test-Path $go2rtc) { Write-Ok "go2rtc installed to bin\go2rtc.exe" }
            else { Write-Info "The download unpacked but go2rtc.exe is not in bin\." }
        } catch {
            Write-Info "The go2rtc download unpacked badly: $($_.Exception.Message)"
        } finally { Remove-Item $zip -Force -ErrorAction SilentlyContinue }
    }
}
if (Test-Path $go2rtc) {
    Add-Good "go2rtc, in bin\go2rtc.exe - the console can be given a stream to show"
} else {
    # A missing streamer does not invalidate the rest of the install: only the
    # live picture needs it, and recording goes straight to the camera.
    Add-Optional "go2rtc is missing, so the console will show no live picture" @(
        "Recording is not affected - it reads the camera directly.",
        "Download go2rtc_win64.zip from https://github.com/AlexxIT/go2rtc/releases/latest",
        "and drag go2rtc.exe into:  $binDir"
    )
}

# =============================================================================
#  6. the detector's weights
# =============================================================================
# yolo11n.pt is not in the repository - it is five and a half megabytes of
# binary that git has no business versioning - so a fresh clone or ZIP arrives
# without it. Handed a name it cannot find, ultralytics recognises yolo11n.pt as
# one of its own published assets and downloads it from github.com. On the
# laptop this runs on there is no network for it to do that over, so the
# download has to happen here, on the machine that has one.
#
# It goes in the project root because that is where vmd\detect\classify.py
# resolves DEFAULT_WEIGHTS to - beside the application, not in whatever
# directory the process was started from.
Write-Step "Checking the detector's weights (what names the thing that moved)"
$weights = Join-Path $root 'yolo11n.pt'
if ((Test-Path $weights) -and ((Get-Item $weights).Length -gt 1MB)) {
    Write-Ok "yolo11n.pt is already here."
} else {
    Write-Info "Downloading yolo11n.pt (5.4 MB)."
    if (Get-File 'https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt' $weights 'the detector weights' (4MB)) {
        Write-Ok "yolo11n.pt downloaded."
    }
}
if ((Test-Path $weights) -and ((Get-Item $weights).Length -gt 1MB)) {
    Add-Good "the detector's weights (yolo11n.pt) - kept for the day naming comes back"
} else {
    Add-Optional "yolo11n.pt is missing - nothing reads it today" @(
        "Recording and detection are unaffected: nothing reads this file today.",
        "Download https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt",
        "and save it as:  $weights"
    )
}

# =============================================================================
#  7. uv beside the project, and bin\ on PATH
# =============================================================================
# uv is a single self-contained executable, so the one winget just installed
# can simply be copied. Copying rather than downloading guarantees the version
# in bin\ is the same version that wrote and validated uv.lock, which is one
# fewer thing to differ between the machine that builds and the machine that
# runs.
#
# PATH is what makes all of this reachable. VMD.exe is vmd\launcher.py frozen,
# and it finds uv with shutil.which(), which reads PATH and nothing else. On the
# offline laptop the copy in bin\ is the only uv there will ever be, so if bin\
# is not on PATH then double-clicking VMD.exe says "uv is not installed" on a
# machine where it plainly is.
Write-Step "Putting uv and bin\ where the console can find them"
$sameFile = $false
if ($uvSource -and (Test-Path $uvSource)) {
    try {
        $sameFile = ([IO.Path]::GetFullPath($uvSource) -ieq [IO.Path]::GetFullPath($uvLocal))
    } catch { $sameFile = $false }
}
if ($sameFile) {
    # Copy-Item refuses to copy a file onto itself, and with
    # $ErrorActionPreference = 'Stop' that refusal ended the whole install. It
    # happens on any re-run where bin\ comes first on PATH, which is every run
    # on a folder prepared for the offline laptop.
    Write-Ok "uv is already in bin\uv.exe"
} elseif ($uvSource -and (Test-Path $uvSource)) {
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null

    # Left behind by the branch below, and deleted here rather than there: the
    # process that held the old uv.exe is usually still holding it at the moment
    # it is renamed, and is gone by the next install.
    foreach ($stale in (Get-ChildItem $binDir -Filter 'uv.exe.old-*' -ErrorAction SilentlyContinue)) {
        Remove-Item $stale.FullName -Force -ErrorAction SilentlyContinue
    }

    # Copying 30 MB over an identical 30 MB is the ordinary case on a re-run,
    # and it is the case that fails: the file it overwrites is the one every
    # running console and recorder was started from. Compare first, and the
    # commonest re-run touches nothing at all.
    $identical = $false
    if (Test-Path $uvLocal) {
        try { $identical = (Get-FileHash $uvLocal).Hash -eq (Get-FileHash $uvSource).Hash }
        catch { $identical = $false }
    }

    if ($identical) {
        Write-Ok "uv is already in bin\uv.exe, and it is this exact version"
    } else {
        try {
            Copy-Item $uvSource $uvLocal -Force
            Write-Ok "uv copied to bin\uv.exe"
        } catch {
            # This is what a second install on a working machine looks like:
            #
            #     Copy-Item : The process cannot access the file
            #     'C:\...\bin\uv.exe' because it is being used by another process.
            #
            # The console, the recorder and both scheduled tasks are all started
            # through bin\uv.exe, so on any machine where VMD is actually
            # running that file is open - and with $ErrorActionPreference =
            # 'Stop' the refusal ended the install at step 7 of 12, before the
            # environment was built. The most complete installation on the
            # machine was the one that could not be re-run.
            #
            # Step 9 does halt everything running out of this folder, which
            # would have released this file - but it does that two steps later
            # and on purpose: it is the price of rebuilding .venv, not of
            # copying one executable. Killing a running recorder here, for a
            # copy that has a way of succeeding without it, is the wrong trade.
            #
            # Windows will not let a running image be overwritten, but it will
            # let it be RENAMED: the running process keeps the file it already
            # opened, under its new name, and the name is free for the new one.
            # So the old uv is moved aside and the copy retried.
            $asideOk = $false
            $aside = "$uvLocal.old-" + (Get-Date -Format 'yyyyMMdd-HHmmss')
            try {
                Move-Item $uvLocal $aside -Force
                Copy-Item $uvSource $uvLocal -Force
                $asideOk = $true
            } catch {
                # Put back what was moved, if the failure was the copy rather
                # than the move. Leaving no uv.exe at all is worse than leaving
                # the old one.
                if ((Test-Path $aside) -and -not (Test-Path $uvLocal)) {
                    Move-Item $aside $uvLocal -Force -ErrorAction SilentlyContinue
                }
            }

            if ($asideOk) {
                Write-Ok "uv copied to bin\uv.exe (the running copy was moved aside)"
                Write-Info "Whatever is running keeps the old one until it is closed."
            } else {
                # Neither route worked. That is not a reason to stop: there is
                # already a working uv in bin\, which is what the rest of this
                # install uses. Say what holds it, because "another process" on
                # its own sends people to Task Manager to guess.
                Write-Warn "bin\uv.exe is in use, so it was left as it is."
                $holders = @()
                try {
                    $holders = @(Get-Process -ErrorAction SilentlyContinue |
                        Where-Object { $_.Path -and ($_.Path -ieq $uvLocal) } |
                        ForEach-Object { "$($_.ProcessName) (pid $($_.Id))" })
                } catch { }
                if ($holders.Count -gt 0) {
                    Write-Info "Held by: $($holders -join ', ')"
                }
                Write-Info "That is VMD itself - the console, the recorder, or the two"
                Write-Info "scheduled tasks. Close the console and run this again if you"
                Write-Info "want the newer uv; nothing here needs it."
            }
        }
    }
} elseif (Test-Path $uvLocal) {
    Write-Ok "uv is already in bin\uv.exe"
} else {
    Write-Warn "Could not copy uv into bin\."
}

if (Add-BinToUserPath $binDir) { Write-Ok "bin\ added to your PATH." }
else { Write-Ok "bin\ is already on your PATH." }

if (Test-Path $uvLocal) {
    Add-Good "uv, in bin\uv.exe, and bin\ is on PATH"
} else {
    Add-Optional "uv is not inside bin\, so this folder cannot be copied to an offline laptop" @(
        "This machine is fine - uv was found at $uvSource.",
        "Run install.bat again before running offline-kit.bat."
    )
}

# Everything from here uses the uv in bin\ by preference: it is the version that
# wrote uv.lock, and naming it by full path removes any question about which uv
# a bare name found.
$uvExe = if (Test-Path $uvLocal) { $uvLocal } else { $uvSource }

# =============================================================================
#  8. the project's own Python
# =============================================================================
# uv would normally keep the interpreter in its own store under %APPDATA%, and
# .venv\pyvenv.cfg would record that absolute path. Copying the project folder
# to another machine then produces a .venv pointing at
# C:\Users\<the other person>\AppData\Roaming\uv\... which does not exist, and
# every launcher fails with
#
#     No Python at '...\python.exe'
#
# and exit code 103. Keeping the interpreter under bin\python\ means the whole
# thing travels in one folder; scripts\offline_install.ps1 rewrites the one
# recorded path if the folder lands somewhere other than where it was built.
Write-Step "Installing the project's own Python"
$pythonDir = Get-ProjectPythonDir $root
$projectPython = Find-ProjectPython $root
if ($projectPython) {
    Write-Ok "Already here: $(Split-Path -Leaf (Split-Path -Parent $projectPython))"
} else {
    Write-Info "Downloading CPython 3.12 into bin\python\ (about 20 MB)."
    $null = Invoke-Logged $uvExe @('python', 'install', '--install-dir', $pythonDir, '3.12')
    $projectPython = Find-ProjectPython $root
    if ($projectPython) { Write-Ok "Python installed into bin\python\" }
    else { Write-Info "Could not install Python into bin\python\." }
}
if ($projectPython) {
    Add-Good "the project's own Python, in bin\python\ - the folder can travel"
} else {
    # Not fatal on this machine: uv will use one of its own interpreters and
    # everything works here. It is fatal for the copy that goes to the offline
    # laptop, and that is worth saying now rather than there.
    Add-Optional "there is no Python inside bin\python\" @(
        "This machine will still work - uv uses an interpreter of its own.",
        "A copy of this folder on an offline machine will not: the interpreter",
        "would be left behind. Run install.bat again before offline-kit.bat."
    )
}

# =============================================================================
#  9. the environment, and the one question about VLC that counts
# =============================================================================
Write-Step "Building the Python environment"

# Before anything touches .venv. On the deployment laptop the recorder is
# running at this moment - that is what the autostart tasks are for - and it is
# running out of .venv\Scripts\python.exe. uv would delete that folder, fail
# partway through with "Access is denied", and leave behind something that is
# neither a usable environment nor a recreatable one, so that every later run of
# install.bat fails in the same way. Explained at length in scripts\_common.ps1.
$halted = Stop-ProjectProcesses $root
if ($halted.Stopped.Count -gt 0) {
    Write-Info "Stopped what was running from this folder ($($halted.Stopped -join ', '))."
    Write-Info "Recording starts again when the console opens at the end."
}
if ($halted.StillRunning.Count -gt 0) {
    Write-Warn "Still running from this folder: $($halted.StillRunning -join ', ')."
    Write-Warn "If the next step fails saying 'Access is denied', restart the laptop"
    Write-Warn "and run install.bat again before anything else has started."
}

Write-Info "Fetching the libraries at the versions in uv.lock."
Write-Info "This includes the detector stack, which is a large download."
Write-Info "The first run takes several minutes; later runs are seconds."
$vlcVerdict = 'unknown'
$vlcDetail = ''
Push-Location $root
try {
    # --extra detect matters: a plain `uv sync` prunes anything not declared,
    # which would strip the detector out of an environment that had it.
    #
    # --python pins the environment to the interpreter inside the project. Left
    # to itself uv picks whichever interpreter it likes best, and on a machine
    # that already had one that is the one it takes - quietly undoing step 8.
    $syncArgs = @('sync', '--extra', 'detect')
    if ($projectPython) { $syncArgs += @('--python', $projectPython) }
    if ((Invoke-Logged $uvExe $syncArgs) -ne 0) {
        Write-Bad "uv sync failed. The output above says why."
        Add-Broken "the Python environment could not be built, so nothing runs at all" @(
            "Check the internet connection and run install.bat again.",
            "It continues from where it stopped."
        )
        Write-Host ""
        Write-Summary
        Stop-Installer 1
    }

    # Exactly what the console imports to open a window, and nothing more - this
    # is the same list scripts\offline_install.ps1 uses on the other machine, and
    # the two must stay identical so a kit that passes here cannot fail there for
    # a reason this check never looked at.
    #
    #   vmd        - imported FIRST. The project is not copied into .venv, it is
    #                one line in _editable_impl_vmd.pth naming the folder it lives
    #                in. Every library can import while `import vmd` fails, and
    #                then the console starts with "No module named 'vmd'" on an
    #                install this step called good. Importing it first also arms
    #                vmd\__init__.py's offline guards before anything heavy loads.
    #   pydantic   - the settings model.  cv2 - frames.  PySide6 - the window.
    #
    # ultralytics is deliberately NOT in this list. It is the object detector, it
    # drags in torch, and naming what moved is OFF - the console never imports it
    # at startup (vmd\detect\classify.py loads it on demand). Making it fatal let
    # a heavy, optional import decide whether the environment was "broken", and on
    # the offline machine it condemned a console that ran fine. The detector's
    # readiness is the yolo11n.pt line, and that is optional. Here, where there is
    # a network to fix things, ultralytics is still checked below - but softly.
    $import = Invoke-Captured $uvExe @('run', '--frozen', '--no-sync', 'python', '-c',
                                       'import vmd, pydantic, cv2, PySide6.QtWidgets; print(''libraries ok'')')
    if ($import.Code -ne 0 -or -not ($import.Out -contains 'libraries ok')) {
        foreach ($line in ($import.Err | Select-Object -Last 3)) { Write-Bad "  $line" }
        Write-Bad "The environment was built but the libraries do not import."
        $missing = $null
        foreach ($line in $import.Err) {
            if ($line -match "No module named '([^']+)'") { $missing = $matches[1]; break }
        }
        $rootModule = if ($missing) { ($missing -split '\.')[0] } else { $null }
        if ($rootModule -eq 'vmd') {
            Add-Broken "the project's own code (vmd) is not on the path, so the console cannot start" @(
                "Delete _editable_impl_vmd.pth in .venv\Lib\site-packages and run",
                "install.bat again - it rewrites that one line with this folder's path."
            )
        } elseif ($rootModule) {
            Add-Broken "a library did not build in ($rootModule is missing), so the console cannot start" @(
                "Delete the .venv folder and run install.bat again."
            )
        } else {
            Add-Broken "the libraries do not import, so the console cannot start" @(
                "Delete the .venv folder and run install.bat again."
            )
        }
        Write-Host ""
        Write-Summary
        Stop-Installer 1
    }
    Write-Ok "Environment ready."
    Add-Good "the Python environment - the console and the recorder run"

    # The detector, checked softly. This machine has the network to fix a broken
    # torch or ultralytics, and this is the last moment before the kit travels to
    # a machine that does not - so a failure here is worth surfacing, but it is
    # not fatal: naming what moved is off, and the console runs without it.
    $detect = Invoke-Captured $uvExe @('run', '--frozen', '--no-sync', 'python', '-c',
                                       'import vmd, ultralytics; print(''detector ok'')')
    if ($detect.Code -eq 0 -and ($detect.Out -contains 'detector ok')) {
        Add-Good "the detector library (ultralytics) - ready for the day naming comes back"
    } else {
        Write-Info "The detector library (ultralytics) does not import. Naming what moved is"
        Write-Info "off, so nothing uses it today; noted here because this is the machine"
        Write-Info "that can still fix it."
        Add-Optional "the detector library (ultralytics) does not import" @(
            "Naming what moved is off today, so the console and recorder are unaffected.",
            "To fix it for later: delete .venv and run install.bat again on this machine."
        )
    }

    # ------------------------------------------------------------------
    #  The verdict on VLC
    # ------------------------------------------------------------------
    # This is the only test that settles it, which is why nothing before this
    # point was allowed to conclude anything.
    #
    # The question is not "can python-vlc find a VLC" - the console stopped
    # asking python-vlc that. vmd\desktop\libvlc.py searches for itself, checks
    # the architecture and the plugins folder, and hands python-vlc the answer
    # through os.add_dll_directory, because since Python 3.8 ctypes no longer
    # looks along PATH for a dependent DLL. So the question the installer must
    # ask is the console's own: run the console's loader.
    #
    # It also means its refusals are reused rather than reinvented. Those
    # sentences are already written for whoever is standing at the laptop - the
    # video pane shows them verbatim - and an installer that said something
    # different about the same machine would be one more thing to reconcile.
    #
    # Written to a file rather than passed with -c: it is several lines, and
    # quoting several lines of Python through PowerShell, cmd and uv is a way
    # to be wrong that is very hard to see.
    Write-Info "Asking the console's own loader whether it can draw the live picture."
    $probeFile = Join-Path $env:TEMP 'vmd-vlc-probe.py'
    Set-Content -Path $probeFile -Encoding UTF8 -Value @'
# Written by scripts\install.ps1. Prints one line, and always exits 0 so that
# the installer classifies on the line rather than on an exit code that
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
    # Its stderr is captured rather than shown. libVLC prints one line per
    # plugin - about fifty kilobytes of "stale plugins cache" - whenever its
    # index is older than the plugins, and none of it means anything is wrong.
    $probe = Invoke-Captured $uvExe @('run', '--offline', '--frozen', '--no-sync', 'python', $probeFile)
    Remove-Item $probeFile -Force -ErrorAction SilentlyContinue
    $answer = @($probe.Out | Where-Object { $_ -match '^VLC(OK|NO)' } | Select-Object -Last 1)
    if ($answer.Count -eq 1 -and $answer[0].StartsWith('VLCOK')) {
        $vlcVerdict = 'ok'
        $vlcDetail = $answer[0].Substring(5).Trim()
        Write-Ok "Yes - the console can draw the live picture."
        if ($vlcDetail) { Write-Info "  Using the VLC in $vlcDetail" }
    } elseif ($answer.Count -eq 1) {
        $vlcVerdict = 'no'
        $vlcDetail = $answer[0].Substring(5).Trim()
        Write-Info "No - the console will show no live picture. Nothing else is affected."
        Write-Info "  $vlcDetail"
    } else {
        $vlcVerdict = 'unknown'
        $vlcDetail = ($probe.Err | Where-Object { $_ -and ($_ -notmatch 'stale plugins cache') } |
                      Select-Object -Last 1)
        Write-Info "The live-picture check gave no answer."
        if ($vlcDetail) { Write-Info "  $vlcDetail" }
    }
}
finally { Pop-Location }

switch ($vlcVerdict) {
    'ok' {
        Add-Good "VLC - the console loaded libVLC, so it shows the live picture"
    }
    'no' {
        # One entry, carrying the console's own sentence. It already names which
        # of the failures this is - not installed, the 32-bit one, or one with
        # no plugins folder - and where it looked, which is the part that turns
        # "VLC is missing" on a machine with VLC installed into something the
        # operator can act on.
        Add-Optional "the console will show no live picture" @(
            $vlcDetail,
            "Everything else, recording included, works without it.",
            "The 64-bit Windows installer is at https://www.videolan.org/vlc/",
            "Adding VLC to PATH does not help and is not what the console reads."
        )
    }
    default {
        Add-Optional "whether the live picture will work was not established" @(
            "Type this in the VMD folder to find out:",
            "  uv run --offline --frozen --no-sync python -c ""from vmd.desktop.libvlc import prepare; print(prepare().dll)"""
        )
    }
}

# =============================================================================
#  10. the single-file launcher
# =============================================================================
Write-Step "Building VMD.exe (the thing you double-click from now on)"
$exePath = Join-Path $root 'VMD.exe'
$builtExe = $false
try {
    & (Join-Path $PSScriptRoot 'build_exe.ps1') | Out-Host
    # Checked by looking for the file rather than by reading $LASTEXITCODE.
    # build_exe.ps1 is a PowerShell script and reports failure by throwing, so
    # $LASTEXITCODE after it is whatever the last program it happened to run
    # left behind - which is not an answer to the question being asked.
    $builtExe = Test-Path $exePath
}
catch {
    Write-Info "Could not build VMD.exe: $($_.Exception.Message)"
    $builtExe = Test-Path $exePath
}
if ($builtExe) {
    Add-Good "VMD.exe - the icon to double-click"
} else {
    # The exe is convenience, not function: VMD.bat starts the same console.
    Add-Optional "VMD.exe was not built" @(
        "Not a problem - double-click VMD.bat instead. It does the same thing."
    )
}

# =============================================================================
#  11. starting by itself
# =============================================================================
# The laptop this runs on is a dedicated recorder that is never meant to be
# off. Windows reboots anyway - updates, power cuts - and until now every
# reboot left the perimeter unrecorded until somebody walked over and
# double-clicked. Two scheduled tasks fix that; scripts\autostart.ps1 explains
# what they are and how to take them away again.
Write-Step "Making the system start by itself after a restart"
if ($NoAutostart) {
    Write-Info "Skipped, because -NoAutostart was given."
} else {
    try {
        & (Join-Path $PSScriptRoot 'autostart.ps1') -Install -Quiet | Out-Host
    } catch {
        Write-Info "Could not set up automatic starting: $($_.Exception.Message)"
    }
    # Asked of Windows rather than assumed from the fact that the script
    # returned. This is the step whose failure costs the most and shows the
    # least: nothing looks wrong until the day the laptop restarts.
    #
    # Both mechanisms count, and the names come from the cameras that are set up
    # rather than from a fixed pair. Looking for exactly "VMD Recorder" and "VMD
    # Console" reported a correctly set up two-camera machine - whose tasks are
    # "VMD Recorder 250" and the rest - as starting nothing at all.
    $verdict = Get-AutostartVerdict $root
    switch ($verdict.Level) {
        'good'     { Write-Ok $verdict.Say;   Add-Good $verdict.What }
        'optional' { Write-Warn $verdict.Say; Add-Optional $verdict.What $verdict.Fix }
        default    { Write-Bad $verdict.Say;  Add-Broken $verdict.What $verdict.Fix }
    }
}

# =============================================================================
#  12. what happened, in three groups
# =============================================================================
Write-Step "Starting the console"
Write-Summary
Write-LogHint

Write-Host ""
Write-Host "  From now on, to start the console:  double-click VMD.exe" -ForegroundColor White
Write-Host "  (or VMD.bat - same thing)" -ForegroundColor Gray
Write-Host ""
Write-Host "  Enter the camera address, username, password and stream addresses" -ForegroundColor Gray
Write-Host "  in the Settings tab and press Save. There is no file to edit." -ForegroundColor Gray
Write-Host ""
Write-Host "  The console starts the streaming server and the recorder itself." -ForegroundColor Gray
Write-Host "  Closing its window does not stop the recording." -ForegroundColor Gray
Write-Host ""
Write-Host "  After a restart, recording begins as soon as you sign in and the" -ForegroundColor Gray
Write-Host "  console window opens by itself about 45 seconds later. Do not" -ForegroundColor Gray
Write-Host "  double-click VMD.exe while waiting - you would get two consoles." -ForegroundColor Gray
Write-Host ""
Write-Host "  Recording service:  uv run python -m vmd.record_main" -ForegroundColor Gray
Write-Host "  Tests:              uv run pytest" -ForegroundColor Gray
Write-Host ""
Write-Host "  For a laptop with no internet, run offline-kit.bat next." -ForegroundColor Gray

# The transcript is closed before the console starts, and deliberately so: the
# console is a long-running program with a camera address and a password in it,
# and leaving a recorder running over the top of it would put whatever it prints
# into a file whose whole purpose is that it can be sent to somebody else.
Stop-VmdTranscript $root | Out-Null

if ($NoLaunch) { exit 0 }

# Hand over to the console itself. It stays running, so this window becomes the
# console's window rather than a second one.
if (Test-Path $exePath) { & $exePath }
else {
    Push-Location $root
    try { & $uvExe run --offline --frozen --no-sync python -m vmd.desktop }
    finally { Pop-Location }
}
exit 0

}
finally {
    # A crash still leaves a readable file. This is the second call on every
    # ordinary path and does nothing then.
    Stop-VmdTranscript $root | Out-Null
}

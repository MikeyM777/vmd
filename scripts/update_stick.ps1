# =============================================================================
#  Fills the VMD update stick, on a laptop with nothing installed on it.
#
#  Double-click VMD-Update-Stick.bat. It downloads the current code from
#  GitHub and writes it onto the stick with a manifest, so that the machine at
#  the other end can prove every byte of it arrived, and it checks the stick
#  against that manifest before anybody carries it anywhere.
#
#  Nothing has to be installed for this to work: no git, no Python. The code
#  comes down as a ZIP over HTTPS.
#
#  What it does NOT do yet: work out which libraries the offline machine is
#  missing and pack the wheels for them. That is Task 11 of
#  docs\superpowers\plans\2026-08-22-offline-updates.md, and until it is built
#  the stick carries code and says so out loud - which is what every update but
#  one is, and a stick that quietly carried no libraries would be one that
#  failed at the far end of a car journey.
#
#  This one script does NOT dot-source scripts\_common.ps1, and that is the one
#  place in this folder where that is deliberate. Every other script here runs
#  inside an installed copy of VMD, where _common.ps1 is certain to be beside
#  it. This one runs on a borrowed laptop that somebody has copied two files
#  onto - this script and the .bat that starts it - and a dot-source of a third
#  file is a script that dies on line 20 with a path nobody can act on. So the
#  four ways of saying things are repeated below, on purpose, and they are the
#  same four.
#
#  -SourceFolder and -NoWheels exist for the tests: they let the whole of this
#  run without a network, against a folder that stands in for the download.
# =============================================================================
param(
    [string]$To,
    [string]$Repository = 'noamsolomon123/vmd',
    [string]$Branch = 'master',
    [string]$SourceFolder,
    [switch]$NoWheels,
    # Says which wheels it WOULD download and then stops, without fetching one.
    # This is what the tests use to exercise the lock-versus-note diff, because
    # tests/conftest.py refuses any socket that is not loopback and a test that
    # actually reached for a wheel could not run at all. It is also useful by
    # hand, to see what a stick is about to carry before committing to the wait.
    [switch]$ListWheelsOnly,
    # Passed by VMD-Update-Stick.bat. Shows a small window instead of doing the
    # work, and the window shells back into this same script without -Gui to do
    # it. Accepted even when unused so that a .bat passing it does not stop with
    # "a parameter cannot be found that matches" before it prints anything.
    [switch]$Gui
)

$ErrorActionPreference = 'Stop'

# Windows PowerShell 5.1 is what a borrowed laptop opens a .ps1 with, so
# nothing below uses anything newer: no ??, no ternary, no && between commands.
# A script that needs PowerShell 7 is a script that needs an install, which is
# the whole thing this half of the update was written to avoid.

# --- saying things -----------------------------------------------------------
#
# The same four as scripts\_common.ps1, for the reason given at the top.

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

# --- what never leaves the developer's machine -------------------------------
#
# The stick goes to a customer's site, so this list is the whole of what stands
# between this laptop and somebody else's camera password on a USB stick in a
# car. It is stated by name rather than by a rule, because a rule ("everything
# except source") is one refactor away from carrying a .venv.
#
#   .git                the entire history of the project, including every
#                       commit message and every branch. The site gets the
#                       program, not the workshop it was built in.
#   settings.json       this machine's camera address and password. go2rtc.json
#                       holds the same password again, and streaming.json and
#                       detection.json describe what ran here.
#   cameras             one folder per camera set up on THIS machine, each with
#                       its own settings.json and its own recordings.
#   recordings, footage, clips
#                       video of somebody's perimeter. Never travels by
#                       accident, and it is the item on this list that would
#                       matter most if it did.
#   .venv, bin          not secret, simply not part of an update: the
#                       environment is rebuilt at the far end from wheels the
#                       stick carries separately, and bin\ holds ffmpeg,
#                       go2rtc, uv and an interpreter - hundreds of megabytes
#                       that the offline machine already has.
#   Ultralytics         the detector library's own config, which records
#                       absolute paths on this machine and a per-install uuid.
#   previous            the copies an earlier update kept, if this checkout has
#                       ever been updated from a stick itself.
#   build, dist         PyInstaller scratch.
#   the cache folders   scratch belonging to pytest, mypy, ruff and the tools
#                       used to write the code.
#   yolo11n.pt          the detector's weights: five and a half megabytes that
#                       the far end will not install even if they arrive, because
#                       vmd\update\apply.py's COPY_IN does not name them. It is
#                       not in the repository either, so a ZIP from GitHub never
#                       carries it - excluding it is what makes a stick built
#                       from a local checkout the same stick as one built from a
#                       download. The installer fetches the weights once, and
#                       they do not change with a version of VMD.
#
# scripts\offline_kit.ps1 keeps its own, longer list for a different job: it
# copies a whole INSTALLED folder, .venv and bin included, because that is the
# thing that has been proved to work. The two lists therefore cannot be one
# list - this one excludes exactly what that one is for. What they must not do
# is disagree about the machine's OWN things, so both name settings.json,
# go2rtc.json, streaming.json, detection.json, cameras, recordings, footage,
# clips and Ultralytics, and a new file of that kind has to be added to both.
# The far end is the second gate: vmd\update\apply.py refuses to copy any of
# those names onto the machine even if a stick arrives carrying one.
$KEEP_BACK = @('.git', '.venv', 'bin', 'recordings', 'footage', 'clips',
               'settings.json', 'go2rtc.json', 'streaming.json', 'detection.json',
               'cameras', 'Ultralytics', 'previous', 'build', 'dist', 'yolo11n.pt',
               '.pytest_cache', '.mypy_cache', '.ruff_cache', '.superpowers',
               '.playwright-mcp', '.claude')

# What commissioning a camera leaves lying in the project root: frame grabs,
# saved copies of the camera's own web pages, hex dumps from probing it, test
# clips, a stray log. A ZIP from GitHub contains none of it - .gitignore refuses
# to commit any of it, for exactly the reason it must not travel - but
# -SourceFolder is pointed at a live checkout, and a live checkout is full of
# it.
#
# By extension, and only in the root, which is where it collects. A recursive
# *.png rule would strip the user guide's screenshots out of docs\, and those
# are drawn by scripts\guide_shots.py against made-up settings and are the only
# thing that makes the guide worth reading.
$ROOT_SCRATCH = @('.jpg', '.jpeg', '.png', '.bmp', '.html', '.bin', '.log',
                  '.ts', '.mp4', '.mkv', '.avi', '.mov', '.wav')

# Written by an interpreter, not by a person, and stale beside code that has
# changed. They also carry the absolute path of the machine that compiled them
# inside every traceback they produce, which is one more thing about this laptop
# that has no business on a customer's site. Pruned at every depth, unlike the
# two lists above, because that is where they are.
$SCRATCH_EVERYWHERE = @('__pycache__')


function Get-Source {
    <#
        Where to copy the program from: a folder that was handed in, or a ZIP
        of the branch fetched from GitHub over HTTPS.

        The unpack folder is emptied first rather than unpacked into. Left in
        place it would hold the previous branch's tree beside this one, and the
        "first directory in it" below would be a coin toss between them - a
        stick built from code nobody chose, which is the kind of fault that is
        discovered at a site. Emptying it also means Expand-Archive -Force
        cannot merge a new tree over an older one and leave a file that this
        version deleted.
    #>
    if ($SourceFolder) {
        if (-not (Test-Path $SourceFolder)) {
            throw "There is no folder at $SourceFolder to build the stick from."
        }
        return (Resolve-Path $SourceFolder).Path
    }

    $zip = Join-Path $env:TEMP 'vmd-update.zip'
    $unpacked = Join-Path $env:TEMP 'vmd-update-src'
    Write-Info "Downloading $Repository ($Branch) from GitHub."

    # TLS 1.2 is not the default on Windows PowerShell 5.1 and codeload.github.com
    # will not speak anything older, so without this line the download fails on
    # a stock laptop with "the request was aborted: could not create SSL/TLS
    # secure channel" - which reads like a proxy problem and is not one.
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    # -UseBasicParsing because Internet Explorer's engine is what the default
    # uses, and a laptop where IE has never been opened stops to ask about its
    # first-run settings with nobody there to answer.
    Invoke-WebRequest -Uri "https://codeload.github.com/$Repository/zip/refs/heads/$Branch" `
        -OutFile $zip -UseBasicParsing

    if (Test-Path $unpacked) { Remove-Item $unpacked -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $unpacked -Force
    Remove-Item $zip -Force

    $inside = @(Get-ChildItem $unpacked -Directory)
    if ($inside.Count -ne 1) {
        throw ("The download did not unpack into one folder as a GitHub ZIP does " +
               "(found $($inside.Count)). Delete $unpacked and try again.")
    }
    return $inside[0].FullName
}


function Copy-Program($source, $target) {
    <#
        Put the program on the stick: everything in the source that is not on
        the two lists above.

        The whole of files\ is deleted first and written again, every time. It
        is worth saying why, because the cost is real: this repository is 248
        files and 17 MB once the lists below have had their say, and all of it
        is copied again when three lines changed - a few seconds on a hard disk,
        and the better part of a minute on a slow stick.

        What it buys is that the stick holds the new version and nothing else.
        A file that this version DELETED, left behind by the previous build,
        would be picked up by the manifest written moments later - the manifest
        describes what is on the stick, not what was in the source - so it would
        verify perfectly at the far end and be copied onto the machine as though
        it belonged to the update. A module deleted because it was replaced,
        reinstalled by the update that replaced it, is a fault that no check on
        either side of the car journey can see.

        A minute against that is not a trade worth thinking about, and it is a
        minute on a build that already involves a drive somewhere. If files\
        ever grows to the size where it is - it should not; wheels\ is where the
        large things go and it is written separately - the answer is robocopy
        /MIR, which copies only what changed and purges what is no longer in the
        source, rather than keeping the speed by trusting that nothing was
        deleted.
    #>
    if (Test-Path $target) { Remove-Item $target -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $target | Out-Null

    foreach ($entry in (Get-ChildItem $source -Force)) {
        if ($KEEP_BACK -contains $entry.Name) { continue }
        if (-not $entry.PSIsContainer -and ($ROOT_SCRATCH -contains $entry.Extension.ToLower())) {
            continue
        }
        $into = Join-Path $target $entry.Name
        if ($entry.PSIsContainer) {
            Copy-Item $entry.FullName $into -Recurse -Force
        } else {
            Copy-Item $entry.FullName $into -Force
        }
    }

    foreach ($name in $SCRATCH_EVERYWHERE) {
        Get-ChildItem $target -Recurse -Force -Directory -Filter $name -ErrorAction SilentlyContinue |
            ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
    }
}


function Get-Sha256($path) {
    <#
        The SHA-256 of one file, as lowercase hex.

        Not Get-FileHash, which is the obvious way to write this and cannot be
        relied on. Get-FileHash is not one of Windows PowerShell 5.1's built-in
        cmdlets: it lives in a module autoloaded off PSModulePath. A PowerShell
        7 session puts its OWN module folders at the front of that variable and
        hands them to whatever it starts, so a 5.1 launched from a 7 prompt -
        or from anything a 7 prompt launched - looks in 7's copy of
        Microsoft.PowerShell.Utility first, cannot load it, and reports that the
        command does not exist.

        That is not a hypothetical and it is not the test's fault: it is how
        this was found. What it produces is a stick that stops three quarters of
        the way through with "the term 'Get-FileHash' is not recognized", which
        tells the person holding it nothing they can act on.

        .NET's SHA256 needs no module and cannot be shadowed.

        It is read as a stream rather than with ReadAllBytes so that a large
        file in the tree does not have to fit in memory.
    #>
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($path)
    try {
        $bytes = $sha.ComputeHash($stream)
    } finally {
        $stream.Dispose()
        $sha.Dispose()
    }
    return [System.BitConverter]::ToString($bytes).Replace('-', '').ToLower()
}


function Get-Manifest($folder) {
    <#
        Every file on the stick with its size and its SHA-256.

        Read back off the stick rather than hashed in the source, which is the
        point of it: what this describes is the bytes that landed, so a file
        that did not fit or was written while somebody pulled the stick out is
        described as it actually is - and then fails the check below.

        The shape has to match vmd\update\manifest.py exactly, because that is
        what reads it: {"files": [{"path", "size", "sha256"}]}, forward slashes,
        lowercase hex.
    #>
    $files = @()
    $prefix = (Resolve-Path $folder).Path.TrimEnd('\') + '\'
    foreach ($file in (Get-ChildItem $folder -Recurse -File -Force | Sort-Object FullName)) {
        $files += [ordered]@{
            # Forward slashes: the machine at the other end compares these as
            # text, and one side writing vmd\app.py while the other looks for
            # vmd/app.py is a stick that reports every file as missing.
            path   = $file.FullName.Substring($prefix.Length).Replace('\', '/')
            size   = $file.Length
            # Lowercase hex, because Python's hexdigest() is lowercase and the
            # two are compared as strings.
            sha256 = Get-Sha256 $file.FullName
        }
    }
    # @( ) around a list that may hold exactly one thing. ConvertTo-Json writes
    # a single-element array as the element itself, and a manifest whose "files"
    # is an object rather than a list is read at the far end as a stick that
    # lists no files at all.
    return [ordered]@{ files = @($files) }
}


function Write-Json($object, $path) {
    $json = $object | ConvertTo-Json -Depth 8
    # WriteAllText with a UTF8Encoding that has been told not to write a byte
    # order mark: Out-File and Set-Content both put one at the front on
    # PowerShell 5.1, and Python's json.loads refuses the file with "Expecting
    # value: line 1 column 1" - a stick that fails at the far end for a reason
    # that is invisible in every editor.
    [System.IO.File]::WriteAllText($path, $json, (New-Object System.Text.UTF8Encoding($false)))
}


function Test-Stick($folder, $manifestPath) {
    <#
        Check the stick against the manifest it was just given, and return every
        disagreement.

        This is the same check vmd\update\manifest.py runs at the far end, run
        here, now, while the stick is still in this laptop. A stick that fails
        its own check is one that nobody has to drive to a site to discover -
        and the failures it catches are real ones: a stick that filled up
        halfway, a file that would not write, and a manifest that serialised
        into a shape the reader cannot use.
    #>
    $problems = @()
    $listed = Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $entries = @()
    # Asked for rather than assumed. @($listed.files) on a manifest with no
    # files in it is an array holding one $null, which then fails on its own
    # missing .path with an error about a null-valued expression - which is a
    # true statement about this script and no help at all about the stick.
    if ($listed -and $listed.files) { $entries = @($listed.files) }
    if ($entries.Count -eq 0) {
        return @('the manifest that was just written lists no files at all')
    }

    $prefix = (Resolve-Path $folder).Path.TrimEnd('\') + '\'
    $seen = @{}
    foreach ($entry in $entries) {
        $path = Join-Path $folder ($entry.path -replace '/', '\')
        $seen[$entry.path] = $true
        if (-not (Test-Path $path -PathType Leaf)) {
            $problems += "$($entry.path) is in the manifest but not on the stick"
            continue
        }
        $file = Get-Item $path -Force
        if ($file.Length -ne $entry.size) {
            $problems += "$($entry.path) is $($file.Length) bytes on the stick and $($entry.size) in the manifest"
            continue
        }
        if ((Get-Sha256 $file.FullName) -ne $entry.sha256) {
            $problems += "$($entry.path) does not match its checksum"
        }
    }

    foreach ($file in (Get-ChildItem $folder -Recurse -File -Force)) {
        $name = $file.FullName.Substring($prefix.Length).Replace('\', '/')
        if (-not $seen.ContainsKey($name)) {
            $problems += "$name is on the stick but not in its manifest"
        }
    }
    return $problems
}


function Test-Nested($inner, $outer) {
    # Is the first folder the same as the second, or somewhere inside it?
    $a = $inner.TrimEnd('\').ToLower()
    $b = $outer.TrimEnd('\').ToLower()
    return ($a -eq $b) -or $a.StartsWith($b + '\')
}


function Normalise-Name($name) {
    <#
        The one spelling of a package name both sides agree on, PEP 503.
        It has to match vmd\update\note.py's `normalise` character for
        character, because that is what the offline machine used when it wrote
        its note: the machine writes pyside6-essentials and uv.lock spells the
        same library PySide6_Essentials, and if the two normalisations disagreed
        the laptop would pack a 90 MB wheel the machine already has, or miss one
        it needs. note.py does re.sub(r"[-_.]+", "-", name).lower(); this is the
        same substitution and the same lowercasing.
    #>
    return ($name -replace '[-_.]+', '-').ToLower()
}


function Test-MarkersApplyToTarget($markerLines) {
    <#
        Do a package occurrence's resolution-markers apply to the machine this
        stick is for - CPython 3.12 on win_amd64?

        A package with no markers applies to everything, so it applies to us. A
        package WITH markers applies if any one of them can be true for our
        target. A marker cannot describe our target if it demands an older
        Python (python_full_version < '3.12') or a different operating system
        (sys_platform != 'win32'), so an occurrence every one of whose markers
        says one of those things is not for this machine and is passed over.
    #>
    if ($markerLines.Count -eq 0) { return $true }
    foreach ($marker in $markerLines) {
        $notForUs = ($marker -match "python_full_version\s*<\s*'3\.12'") -or
                    ($marker -match "sys_platform\s*!=\s*'win32'")
        if (-not $notForUs) { return $true }
    }
    return $false
}


function Get-LockedPackages($lockPath) {
    <#
        The packages a uv.lock pins for THIS machine, as normalised-name ->
        version.

        Read with a regex rather than a TOML parser, because a TOML parser is a
        library and this laptop has nothing installed on it. The shape uv.lock
        uses is stable and simple enough to read line by line: a [[package]]
        table header, then an unindented name = "..." and an unindented
        version = "..." within it.

        The dependency references inside a package - the { name = "..." } items
        in a dependencies or requires-dist list - are deliberately NOT matched,
        because they are inline tables that begin with a brace, so "^\s*name" (no
        brace) skips them. Only the first version after a name is taken, so a
        stray version deeper in the same table cannot overwrite the package's
        own. The format version on line one (version = 1, no quotes) is skipped
        because it is not a quoted string.

        The one thing a plain name -> version read gets wrong: uv lists a package
        once PER set of resolution-markers, so numpy appears twice in this very
        lock - 2.4.6 for python < 3.12 and 2.5.1 for python >= 3.12. Taking
        whichever came last would be a coin toss that packs the < 3.12 wheel for
        a machine that runs 3.12, a wheel the far end will never install. So each
        occurrence is checked against the target with the markers it carries, and
        one that is for another Python or another platform is not recorded.
    #>
    $packages = @{}
    if (-not (Test-Path $lockPath)) { return $packages }

    $name = $null
    $version = $null
    $markers = @()
    $inMarkers = $false

    foreach ($line in (Get-Content $lockPath)) {
        if ($line -match '^\s*\[\[package\]\]') {
            # The previous package ends here. Record it unless its markers say it
            # is not for this machine.
            if ($name -and $version -and (Test-MarkersApplyToTarget $markers)) {
                $packages[(Normalise-Name $name)] = $version
            }
            $name = $null; $version = $null; $markers = @(); $inMarkers = $false
            continue
        }
        if ($inMarkers) {
            if ($line -match '"([^"]+)"') { $markers += $Matches[1] }
            if ($line -match '\]') { $inMarkers = $false }
            continue
        }
        if ($line -match '^\s*resolution-markers\s*=\s*\[') {
            $inMarkers = $true
            # A single-line resolution-markers = [ ... ] closes on the same line.
            if ($line -match '\]') { $inMarkers = $false }
            continue
        }
        if ($line -match '^\s*name\s*=\s*"([^"]+)"') { $name = $Matches[1]; continue }
        if ($line -match '^\s*version\s*=\s*"([^"]+)"' -and $name -and -not $version) {
            $version = $Matches[1]
        }
    }
    # The last package in the file has no [[package]] after it to flush it.
    if ($name -and $version -and (Test-MarkersApplyToTarget $markers)) {
        $packages[(Normalise-Name $name)] = $version
    }
    return $packages
}


function Get-MissingPackages($locked, $notes) {
    <#
        The pins no machine on this stick has yet, as normalised-name -> version.

        A package counts as needed when ANY machine's note lacks it at the locked
        version - the union over every note - because one stick may serve two
        sites and a wheel packed for one costs the other nothing but a little
        room. A machine that already has the exact version the lock pins is asked
        for nothing, which is the whole point: torch is over 2 GB and does not
        change every release.
    #>
    $missing = @{}
    foreach ($note in $notes) {
        $have = @{}
        try {
            $parsed = Get-Content $note.FullName -Raw | ConvertFrom-Json
            if ($parsed.libraries) {
                foreach ($property in $parsed.libraries.PSObject.Properties) {
                    $have[(Normalise-Name $property.Name)] = [string]$property.Value
                }
            }
        } catch {
            # A note that will not parse tells us nothing about that machine, so
            # it is skipped rather than allowed to stop the build. The far end
            # checks every wheel it installs regardless.
            continue
        }
        foreach ($name in $locked.Keys) {
            if ($have[$name] -ne $locked[$name]) { $missing[$name] = $locked[$name] }
        }
    }
    return $missing
}


function Get-StickPython($stick) {
    <#
        A CPython that can fetch wheels, put on the stick and kept there.

        This is here rather than "uv pip download" because the bundled uv, and
        the current release of uv, have no pip download subcommand at all - uv
        pip covers compile, sync, install and so on, but not download. So the
        wheels are fetched the one way that needs nothing installed on the
        laptop: uv is dropped onto the stick, uv installs a Python onto the
        stick, and that Python's pip does the download. The laptop keeps nothing;
        the stick keeps both for next time, so only the first build waits for
        them.

        The target platform is stated at the download, not here: this Python is
        merely the tool that fetches, and it fetches win_amd64 CPython 3.12
        wheels no matter what the laptop itself is.
    #>
    $uv = Join-Path $stick 'tools\uv.exe'
    if (-not (Test-Path $uv)) {
        New-Item -ItemType Directory -Force -Path (Split-Path $uv) | Out-Null
        Write-Info "Fetching uv onto the stick (14 MB, once)."
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $zip = Join-Path $env:TEMP 'uv.zip'
        Invoke-WebRequest -UseBasicParsing -OutFile $zip `
            -Uri 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip'
        Expand-Archive $zip (Split-Path $uv) -Force
        Remove-Item $zip -Force
    }
    $pythonDir = Join-Path $stick 'tools\python'
    $found = Get-ChildItem $pythonDir -Filter 'python.exe' -Recurse -Depth 2 -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $found) {
        Write-Info "Fetching a Python onto the stick (20 MB, once)."
        & $uv python install --install-dir $pythonDir 3.12 | Out-Host
        $found = Get-ChildItem $pythonDir -Filter 'python.exe' -Recurse -Depth 2 -ErrorAction SilentlyContinue |
            Select-Object -First 1
    }
    if (-not $found) { throw "Could not put a Python on the stick to fetch wheels with." }
    return $found.FullName
}


# =============================================================================
#  the window
# =============================================================================
#
# One button and a drive to point it at, for whoever fills the stick and does
# not open a terminal. It does none of the work itself: it shells back into this
# same script WITHOUT -Gui and shows what comes back, so there is one code path
# that builds a stick and the window is only a way to start it. That is why the
# tests never touch this block - they call the same script the button does.
if ($Gui) {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'VMD update stick'
    $form.Size = New-Object System.Drawing.Size(560, 260)
    $form.StartPosition = 'CenterScreen'

    $label = New-Object System.Windows.Forms.Label
    $label.Text = 'USB drive:'
    $label.Location = New-Object System.Drawing.Point(16, 20)
    $label.AutoSize = $true
    $form.Controls.Add($label)

    $drives = New-Object System.Windows.Forms.ComboBox
    $drives.Location = New-Object System.Drawing.Point(100, 16)
    $drives.Width = 420
    $drives.DropDownStyle = 'DropDownList'
    # Removable drives only (DriveType 2), each labelled with the version it
    # already carries if it is a VMD stick, so the person choosing can tell a
    # stick that has been built before from a blank one.
    foreach ($drive in (Get-WmiObject Win32_LogicalDisk -Filter 'DriveType=2')) {
        $version = ''
        $updateJson = Join-Path $drive.DeviceID '\update.json'
        if (Test-Path $updateJson) {
            try { $version = " (VMD $((Get-Content $updateJson -Raw | ConvertFrom-Json).version))" } catch { }
        }
        [void]$drives.Items.Add("$($drive.DeviceID)\$version")
    }
    if ($drives.Items.Count -gt 0) { $drives.SelectedIndex = 0 }
    $form.Controls.Add($drives)

    $status = New-Object System.Windows.Forms.TextBox
    $status.Multiline = $true
    $status.ReadOnly = $true
    $status.ScrollBars = 'Vertical'
    $status.Location = New-Object System.Drawing.Point(16, 60)
    $status.Size = New-Object System.Drawing.Size(504, 110)
    $form.Controls.Add($status)

    $go = New-Object System.Windows.Forms.Button
    $go.Text = 'Build the stick'
    $go.Location = New-Object System.Drawing.Point(400, 180)
    $go.Size = New-Object System.Drawing.Size(120, 30)
    $go.Add_Click({
        # The item reads like "E:\ (VMD 8)"; the drive is the part before the
        # first space, and the rest is only there to be read.
        $chosen = ($drives.SelectedItem -split ' ')[0]
        if (-not $chosen) { $status.AppendText("Plug the stick in and open this again.`r`n"); return }
        $go.Enabled = $false
        $status.AppendText("Building on $chosen ...`r`n")
        $form.Refresh()
        # The same script, without -Gui, is what does the work. 2>&1 folds its
        # error stream into the output so a failure is shown in the box rather
        # than lost to a console nobody opened.
        $output = & powershell -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath -To $chosen 2>&1
        foreach ($line in $output) { $status.AppendText("$line`r`n") }
        $go.Enabled = $true
    })
    $form.Controls.Add($go)

    [void]$form.ShowDialog()
    exit 0
}


# =============================================================================
#  the build
# =============================================================================
try {
    if (-not $To) { throw "Say which drive to write to, like this:  -To E:\" }

    Write-Host ""
    Write-Host "  Filling the VMD update stick" -ForegroundColor White

    # Made absolute before anything is written, and made absolute WITHOUT
    # requiring the folder to exist yet. [System.IO.File]::WriteAllText resolves
    # a relative path against the .NET process's own current directory, which is
    # not PowerShell's - so a relative -To would put manifest.json somewhere
    # neither the operator nor this script can find. Resolve-Path is the usual
    # way to fix that and it refuses a path that is not there, which -To on a
    # fresh stick is.
    $To = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($To)

    Set-StepTotal 4

    Write-Step "Getting the code"
    $source = Get-Source
    Write-Ok "Building from $source"

    # The stick and the source are told apart before either is created or
    # anything is deleted: Copy-Program's first act is to remove the whole of
    # files\, and a -To pointed inside the checkout would delete part of the
    # thing being copied.
    $files = Join-Path $To 'files'
    if ((Test-Nested $files $source) -or (Test-Nested $source $To)) {
        throw ("The stick folder ($To) is inside the source folder ($source), or " +
               "the other way round. Point -To at the USB drive.")
    }
    New-Item -ItemType Directory -Force -Path $To | Out-Null

    $versionFile = Join-Path $source 'VERSION'
    if (-not (Test-Path $versionFile)) {
        throw ("There is no VERSION file in $source, so this is not a copy of VMD. " +
               "Check the folder, or the branch name.")
    }
    $version = 0
    # "$( )" around the read, because an empty VERSION file makes Get-Content
    # -Raw return nothing at all, and .Trim() on nothing is a red stack trace
    # instead of the sentence below.
    $versionText = "$(Get-Content $versionFile -Raw)".Trim()
    if (-not [int]::TryParse($versionText, [ref]$version)) {
        throw "The VERSION file in $source says '$versionText', which is not a version number."
    }
    Write-Ok "This is VMD $version."

    Write-Step "Copying the program onto the stick"
    # The stick stops calling itself an update stick for as long as the build
    # is running. vmd\update\stick.py treats a drive as a stick only when both
    # update.json and manifest.json are on it, so removing them here means a
    # build that fails halfway leaves a drive the console ignores rather than
    # one that offers yesterday's version number over today's half-written
    # files. They are written again at the end, after the check has passed.
    foreach ($name in @('update.json', 'manifest.json')) {
        $stale = Join-Path $To $name
        if (Test-Path $stale) { Remove-Item $stale -Force }
    }
    Copy-Program $source $files
    $count = @(Get-ChildItem $files -Recurse -File -Force).Count
    Write-Ok "$count files in files\ - the program, and none of this laptop's own."

    Write-Step "Working out what the machine at the other end needs"
    # What the machines on this stick already have. One file per machine,
    # written by the machine itself - see vmd\update\note.py. Read before the
    # wheels are packed and never written to: the note is the only thing on the
    # stick this side does not own.
    $notes = @()
    $machinesDir = Join-Path $To 'machines'
    if (Test-Path $machinesDir) {
        $notes = @(Get-ChildItem $machinesDir -Filter '*.json' -ErrorAction SilentlyContinue)
    }
    if ($notes.Count -eq 0) {
        Write-Info "This stick has never been to a VMD machine, so it carries code only."
        Write-Info "If the update needs a new library the console will say so and change nothing."
    } else {
        foreach ($note in $notes) {
            Write-Ok "This stick has been to $($note.BaseName)."
        }
    }

    # Diff the new lock against every machine note and work out which wheels no
    # machine on this stick has yet. Only when there is at least one note: with
    # no note there is nothing to diff against, and the lines above have already
    # said the stick will carry code only.
    $missing = @{}
    if ($notes.Count -gt 0) {
        $locked = Get-LockedPackages (Join-Path $source 'uv.lock')
        $missing = Get-MissingPackages $locked $notes
        foreach ($name in ($missing.Keys | Sort-Object)) {
            Write-Info "needs $name==$($missing[$name])"
        }
        if ($missing.Count -eq 0) {
            Write-Ok "Every library this update needs is already on the machine this stick has visited."
        }
    }

    # -ListWheelsOnly has now said what it would fetch, and stops before touching
    # the network or the stick. This is the seam the tests use: they can prove
    # the diff picked the right packages without a wheel ever being downloaded.
    if ($ListWheelsOnly) {
        Write-Host ""
        Write-Ok "Listed what would be downloaded, and downloaded nothing (-ListWheelsOnly)."
        exit 0
    }

    if ($NoWheels) {
        Write-Info "Not downloading any libraries, because -NoWheels was given."
    } elseif ($missing.Count -gt 0) {
        $wheels = Join-Path $To 'wheels'
        New-Item -ItemType Directory -Force -Path $wheels | Out-Null
        $python = Get-StickPython $To
        foreach ($name in ($missing.Keys | Sort-Object)) {
            $pin = "$name==$($missing[$name])"
            Write-Info "Downloading $pin"
            # --no-deps because the pins come from the lock, which already
            # resolved the whole graph: letting pip resolve the dependencies
            # again would pull versions this machine is not going to install.
            # --only-binary and the three platform flags pin the wheel to the
            # machine at the far end - win_amd64, CPython 3.12 - rather than to
            # this laptop, whose own Python is not what the offline machine runs.
            & $python -m pip download $pin --no-deps --only-binary=:all: `
                --platform win_amd64 --python-version 3.12 --implementation cp `
                --dest $wheels | Out-Host
            if ($LASTEXITCODE -ne 0) { Write-Bad "could not download $pin" }
        }
    }

    Write-Step "Writing the manifest and checking the stick against it"
    $manifestPath = Join-Path $To 'manifest.json'
    Write-Json (Get-Manifest $files) $manifestPath

    $problems = Test-Stick $files $manifestPath
    if ($problems.Count -gt 0) {
        Write-Bad "The stick does not match the manifest it was just given:"
        foreach ($problem in $problems) { Write-Info "  $problem" }
        Write-Bad "Do not take this stick anywhere. Try another one, or build it again."
        exit 1
    }
    Write-Ok "Every one of the $count files was read back and matched."

    Write-Json ([ordered]@{
        version = $version
        built   = (Get-Date).ToString('s')
        branch  = $Branch
        source  = $Repository
    }) (Join-Path $To 'update.json')

    # For whoever picks the stick up, which may not be whoever filled it.
    $readme = @"
VMD update stick - VMD $version, built $((Get-Date).ToString('dd MMM yyyy'))

Take this stick to the VMD computer, open the console, go to the Settings tab
and press "Update now" at the bottom.

Do not put anything else on this stick. Everything on it is checked against
manifest.json before it is installed, and anything unexpected stops the update.
"@
    # Carriage returns, because this is read by double-clicking it into Notepad
    # on the machine it is carried to, and Notepad before Windows 10 1809 shows
    # a file with bare newlines as one long line.
    [System.IO.File]::WriteAllText((Join-Path $To 'README.txt'),
        ($readme -replace "`r?`n", "`r`n"), (New-Object System.Text.UTF8Encoding($false)))

    Write-Host ""
    Write-Ok "Stick ready: VMD $version at $To"
    Write-Host ""
    exit 0
} catch {
    Write-Host ""
    Write-Bad "The stick was not finished: $($_.Exception.Message)"
    Write-Info "(line $($_.InvocationInfo.ScriptLineNumber) of update_stick.ps1)"
    Write-Host ""
    exit 1
}

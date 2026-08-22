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
    # Passed by VMD-Update-Stick.bat and ignored until Task 11 puts a window on
    # this. Accepted now rather than then, because a .bat that passes a
    # parameter the .ps1 has never heard of stops with "a parameter cannot be
    # found that matches" before it prints anything at all.
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

        Not Get-FileHash, which is the obvious way to write this and does not
        work. Get-FileHash is not one of Windows PowerShell 5.1's built-in
        cmdlets: it lives in a module that is autoloaded off PSModulePath, and
        on any machine where PowerShell 7 is installed PSModulePath leads with
        7's own copy of that module - which 5.1 cannot load, so the command is
        simply not found. It is not rare and it is not the test's fault: it is
        every laptop that has 7 on it and starts this from a 7 prompt, and what
        it produces is a stick that stops with "the term 'Get-FileHash' is not
        recognized" three quarters of the way through.

        .NET's SHA256 needs no module and cannot be shadowed. It is read as a
        stream rather than with ReadAllBytes so that a large file in the tree
        does not have to fit in memory.
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

    if ($NoWheels) {
        Write-Info "Not packing any libraries, because -NoWheels was given."
    } elseif ($notes.Count -gt 0) {
        # Filled in by Task 11 of docs\superpowers\plans\2026-08-22-offline-updates.md.
        # Until then the stick carries code, which is what every update but one
        # is. Said out loud rather than done silently, so that nobody reads the
        # line above and believes wheels were packed.
        Write-Info "Working out which libraries are missing is not built yet; carrying code only."
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

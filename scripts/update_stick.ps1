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
    # A test seam, the sibling of -ListWheelsOnly. Given the text of one error, it
    # prints NETWORK or OTHER and stops, so tests\test_update_stick_builder.py can
    # prove Test-NetworkError names a no-internet failure - the case that turns a
    # raw web-exception stack into one sentence for the operator - without a test
    # ever reaching the network conftest.py forbids. Nothing in the build reads it.
    [string]$ClassifyError,
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


function Test-NetworkError($text) {
    <#
        Was this failure the laptop having no way to reach GitHub, rather than a
        fault in the code, the branch name or the stick?

        Kept as its own function that takes a plain string so it can be tested
        without a network: a test hands it the exact words Windows writes and
        checks the verdict. The build's catch block feeds it the exception's
        message joined with a WebException's Status name ("NameResolutionFailure"
        and its kind), because a machine with no route to the internet fails the
        DNS lookup first and reports exactly that - "The remote name could not be
        resolved" - before anything else is tried.

        Matched by the words rather than by exception type on purpose: the same
        no-internet condition arrives as a WebException from Invoke-WebRequest, as
        that same exception re-wrapped when it crosses the child process boundary,
        and as an IOException from Expand-Archive if the download half-arrived. The
        type is not stable across those; the words Windows uses for "there is no
        network" are, and it is those words the operator needs turned into the one
        sentence they can act on.

        Deliberately narrow. A 404 for a branch that does not exist, or a stick
        that filled up, is NOT a network failure and must keep its own specific
        message - so those words are not on this list. A wrong verdict here would
        send somebody to check their internet while the real fault sat elsewhere.
    #>
    if (-not $text) { return $false }
    $lower = ([string]$text).ToLower()
    # Each phrase is one Windows writes when the machine cannot reach the far host
    # at all: DNS could not be looked up, no route, refused, timed out, or the
    # WebExceptionStatus name for the same. None of them can be produced by a
    # server that answered - which is what keeps a 404 or a 500 off this list.
    $signals = @(
        'could not be resolved',
        'no such host is known',
        'unable to connect',
        'connection attempt failed',
        'operation has timed out',
        'network is unreachable',
        'the underlying connection was closed',
        'connection with the server could not be established',
        'nameresolutionfailure',
        'proxynameresolutionfailure',
        'connectfailure'
    )
    foreach ($signal in $signals) {
        if ($lower.Contains($signal)) { return $true }
    }
    return $false
}


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


# The one machine every wheel on this stick is for: the offline console, which
# is CPython 3.12 on 64-bit Windows and nothing else. These are the environment
# markers PEP 508 evaluates against, filled in for that machine. They are stated
# here rather than read from this laptop on purpose - the laptop is not the
# machine the stick is for, and a marker judged against the laptop's own OS would
# pack the laptop's wheels.
$script:TargetEnv = @{
    'os_name'                        = 'nt'
    'sys_platform'                   = 'win32'
    'platform_system'                = 'Windows'
    'platform_machine'               = 'AMD64'
    'platform_python_implementation' = 'CPython'
    'implementation_name'            = 'cpython'
    'python_version'                 = '3.12'
    # All three components, and that is not cosmetic. Compare-MarkerValues casts
    # both sides to [version], and [version]'3.12' has Build -1 while
    # [version]'3.12.0' has Build 0, so a two-component '3.12' here would make
    # python_full_version >= '3.12.0' come out FALSE and any dependency gated on
    # a three-component python_full_version >= '3.12.x' marker be silently
    # dropped - fail-closed, a library missing at the far end after the trip.
    # This is the real bundled interpreter, bin\python\cpython-3.12.9-...; it
    # must track that folder if the interpreter is ever bumped. python_version
    # above stays two components: PEP 508 defines it as major.minor.
    'python_full_version'            = '3.12.9'
}

# One atomic comparison in a marker: a variable or quoted string, an operator,
# and another variable or quoted string. Everything between the atoms is 'and',
# 'or' and parentheses, handled by the evaluator below.
$script:MarkerAtom = [regex]@'
(?<a>[A-Za-z_][A-Za-z0-9_]*|'[^']*'|"[^"]*")\s*(?<op>==|!=|<=|>=|~=|not in|in|<|>)\s*(?<b>[A-Za-z_][A-Za-z0-9_]*|'[^']*'|"[^"]*")
'@


function Resolve-MarkerOperand($token) {
    # A quoted literal is itself; a bare word is an environment marker, looked up
    # for the target. A bare word that is not one of the known markers (an extra
    # name, say) returns $null, and the caller treats an unknown atom as true so
    # that an unfamiliar marker never causes a needed package to be dropped.
    if ($token -match "^'(.*)'$") { return $Matches[1] }
    if ($token -match '^"(.*)"$') { return $Matches[1] }
    if ($script:TargetEnv.ContainsKey($token)) { return $script:TargetEnv[$token] }
    return $null
}


function Compare-MarkerValues($a, $op, $b) {
    # Version-aware for the ordering operators when both sides look like version
    # numbers, so python_full_version < '3.15' compares as versions and not as
    # text - "3.9" is greater than "3.15" as strings, which would get the answer
    # exactly backwards.
    $looksVersion = ($a -match '^\d+(\.\d+)*$') -and ($b -match '^\d+(\.\d+)*$')
    if ($looksVersion -and ($op -eq '<' -or $op -eq '<=' -or $op -eq '>' -or $op -eq '>=')) {
        $va = [version]$a
        $vb = [version]$b
        switch ($op) {
            '<'  { return $va -lt $vb }
            '<=' { return $va -le $vb }
            '>'  { return $va -gt $vb }
            '>=' { return $va -ge $vb }
        }
    }
    switch ($op) {
        '==' { return $a -eq $b }
        '!=' { return $a -ne $b }
        '<'  { return ([string]$a) -lt ([string]$b) }
        '<=' { return ([string]$a) -le ([string]$b) }
        '>'  { return ([string]$a) -gt ([string]$b) }
        '>=' { return ([string]$a) -ge ([string]$b) }
        '~=' { return $a -eq $b }
        'in' { return ([string]$b).Contains([string]$a) }
        'not in' { return -not ([string]$b).Contains([string]$a) }
    }
    return $true
}


function Test-MarkerAtom($a, $op, $b) {
    $va = Resolve-MarkerOperand $a
    $vb = Resolve-MarkerOperand $b
    # An atom naming a marker this script does not model is treated as true, the
    # safe direction: it can only keep a package in, never wrongly drop one.
    if ($null -eq $va -or $null -eq $vb) { return $true }
    return (Compare-MarkerValues $va $op $vb)
}


function Test-MarkerHoldsForTarget($marker) {
    <#
        Does one PEP 508 marker string hold for the target machine?

        A real evaluator rather than a pattern match, because the markers in this
        lock are not flat: torch reaches its CUDA libraries through markers like
        "(platform_machine == 'aarch64' and sys_platform == 'linux') or
        (platform_machine == 'x86_64' and sys_platform == 'linux')", and deciding
        that against win32 needs the 'and', the 'or' and the parentheses actually
        evaluated. Each atomic comparison is resolved to true or false first, then
        what remains - T, F, and, or, and brackets - is parsed with 'and' binding
        tighter than 'or', as PEP 508 says.
    #>
    if (-not $marker) { return $true }
    # Resolve every atomic comparison to a bare T or F, then isolate the brackets
    # as their own tokens so the parser can see them.
    $reduced = $script:MarkerAtom.Replace($marker, {
        param($m)
        if (Test-MarkerAtom $m.Groups['a'].Value $m.Groups['op'].Value $m.Groups['b'].Value) { ' T ' } else { ' F ' }
    })
    $reduced = $reduced -replace '\(', ' ( ' -replace '\)', ' ) '
    $script:MarkerTokens = @($reduced -split '\s+' | Where-Object { $_ -ne '' })
    $script:MarkerPos = 0
    return (Read-MarkerOr)
}

function Read-MarkerOr {
    $value = Read-MarkerAnd
    while ($script:MarkerPos -lt $script:MarkerTokens.Count -and $script:MarkerTokens[$script:MarkerPos] -eq 'or') {
        $script:MarkerPos++
        $right = Read-MarkerAnd
        $value = $value -or $right
    }
    return $value
}

function Read-MarkerAnd {
    $value = Read-MarkerTerm
    while ($script:MarkerPos -lt $script:MarkerTokens.Count -and $script:MarkerTokens[$script:MarkerPos] -eq 'and') {
        $script:MarkerPos++
        $right = Read-MarkerTerm
        $value = $value -and $right
    }
    return $value
}

function Read-MarkerTerm {
    $token = $script:MarkerTokens[$script:MarkerPos]
    if ($token -eq '(') {
        $script:MarkerPos++
        $value = Read-MarkerOr
        if ($script:MarkerPos -lt $script:MarkerTokens.Count -and $script:MarkerTokens[$script:MarkerPos] -eq ')') {
            $script:MarkerPos++
        }
        return $value
    }
    $script:MarkerPos++
    if ($token -eq 'T') { return $true }
    if ($token -eq 'F') { return $false }
    # An unexpected token cannot make a package be dropped: treated as true.
    return $true
}


function Test-MarkerListHolds($markerLines) {
    # A package's resolution-markers hold for the target if ANY one of them does;
    # a package with none applies unconditionally.
    if ($markerLines.Count -eq 0) { return $true }
    foreach ($marker in $markerLines) {
        if (Test-MarkerHoldsForTarget $marker) { return $true }
    }
    return $false
}


function Get-LockedPackages($lockPath) {
    <#
        The packages a uv.lock pins for THIS machine, as normalised-name ->
        version - and only the ones that machine actually installs.

        Read with a regex rather than a TOML parser, because a TOML parser is a
        library and this laptop has nothing installed on it. The shape uv.lock
        uses is stable enough to read line by line: a [[package]] header, an
        unindented name = "..." and version = "..." within it, an optional
        resolution-markers block, an optional source, and dependency lists whose
        entries are inline { name = "..." } tables.

        Two things a plain name -> version read gets wrong, and both are handled
        here:

        1. uv lists a package once PER set of resolution-markers, so numpy is in
           this lock twice - 2.4.6 for python < 3.12 and 2.5.1 for python >= 3.12.
           The occurrence whose own resolution-markers do not hold for a 3.12
           machine is not recorded, so the 3.12 pin wins whatever the order.

        2. A package can be in the lock as its own table with NO markers of its
           own, yet be reached only through a dependency EDGE that is gated to
           another platform - torch's whole CUDA stack (cuda-bindings, triton,
           the nvidia-* libraries) is pulled in only under sys_platform != 'win32'
           or == 'linux'. Read by its own table alone it looks unconditionally
           needed, and the laptop would try to fetch nineteen Linux-only wheels
           every build, most of which do not exist for Windows. So the edges are
           read too: a package every one of whose inbound edges is gated to a
           platform this machine is not is dropped. nvidia-ml-py survives that,
           correctly - ultralytics depends on it with no marker, so a Windows
           machine really does have it.
    #>
    $packages = @{}
    if (-not (Test-Path $lockPath)) { return $packages }

    $name = $null
    $version = $null
    $markers = @()
    $isEditable = $false
    $inMarkers = $false
    $inDeps = $false
    $inDepSection = $false

    # Candidate name -> version for occurrences whose own markers hold, and the
    # inbound-edge tallies used to drop the off-platform packages afterwards.
    $candidates = @{}
    $hasEdge = @{}
    $hasTargetEdge = @{}

    $flush = {
        # The editable root package (source = { editable = "." }) is the project
        # itself, not a wheel to fetch, so it is never a candidate.
        if ($name -and $version -and (-not $isEditable) -and (Test-MarkerListHolds $markers)) {
            $candidates[(Normalise-Name $name)] = $version
        }
    }

    foreach ($line in (Get-Content $lockPath)) {
        if ($line -match '^\s*\[\[package\]\]') {
            & $flush
            $name = $null; $version = $null; $markers = @(); $isEditable = $false
            $inMarkers = $false; $inDeps = $false; $inDepSection = $false
            continue
        }

        if ($inMarkers) {
            if ($line -match '"([^"]+)"') { $markers += $Matches[1] }
            if ($line -match '\]') { $inMarkers = $false }
            continue
        }

        # A dependency edge inside a resolved list: { name = "X", marker = "..." }.
        # Collected only while inside a dependencies / optional-dependencies /
        # dev-dependencies list, never inside [package.metadata], whose
        # requires-dist entries are abstract specifiers and not the resolved graph.
        if ($inDeps) {
            if ($line -match '^\s*\]') { $inDeps = $false; continue }
            if ($line -match '^\s*\{\s*name\s*=\s*"([^"]+)"') {
                $edgeName = Normalise-Name $Matches[1]
                $edgeMarker = $null
                if ($line -match 'marker\s*=\s*"([^"]+)"') { $edgeMarker = $Matches[1] }
                $hasEdge[$edgeName] = $true
                if (Test-MarkerHoldsForTarget $edgeMarker) { $hasTargetEdge[$edgeName] = $true }
            }
            continue
        }

        if ($line -match '^\s*resolution-markers\s*=\s*\[') {
            $inMarkers = $true
            if ($line -match '\]') { $inMarkers = $false }
            continue
        }

        # The start of a resolved dependency list. A bare "dependencies = [" is
        # one; a group like "detect = [" or "dev = [" is one only while we are
        # inside a [package.optional-dependencies] or [package.dev-dependencies]
        # section, which is what $inDepSection tracks.
        if ($line -match '^\s*dependencies\s*=\s*\[') {
            $inDeps = $true
            if ($line -match '^\s*dependencies\s*=\s*\[\s*\]') { $inDeps = $false }
            continue
        }
        if ($line -match '^\s*\[package\.optional-dependencies\]' -or
            $line -match '^\s*\[package\.dev-dependencies\]') {
            $inDepSection = $true
            continue
        }
        if ($line -match '^\s*\[') {
            # Any other table header - [package.metadata], [package.metadata.*] -
            # ends the section where group lists count as resolved edges.
            $inDepSection = $false
            continue
        }
        if ($inDepSection -and $line -match '^\s*[A-Za-z0-9_.-]+\s*=\s*\[') {
            $inDeps = $true
            if ($line -match '\]\s*$') { $inDeps = $false }
            continue
        }

        if ($line -match '^\s*source\s*=\s*\{.*editable') { $isEditable = $true; continue }
        if ($line -match '^\s*name\s*=\s*"([^"]+)"') { $name = $Matches[1]; continue }
        if ($line -match '^\s*version\s*=\s*"([^"]+)"' -and $name -and -not $version) {
            $version = $Matches[1]
        }
    }
    & $flush

    # Drop a candidate that is in the lock only for another platform: it has
    # inbound edges, and not one of them is gated to this target.
    foreach ($candidate in $candidates.Keys) {
        if ($hasEdge[$candidate] -and -not $hasTargetEdge[$candidate]) { continue }
        $packages[$candidate] = $candidates[$candidate]
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

# The classifier seam runs before the window, so a test invoking it with a bare
# -ClassifyError (and no -Gui) gets its answer and stops. Write-Output, not
# Write-Host, so the verdict is a value on stdout the test reads back cleanly.
if ($PSBoundParameters.ContainsKey('ClassifyError')) {
    if (Test-NetworkError $ClassifyError) { Write-Output 'NETWORK' } else { Write-Output 'OTHER' }
    exit 0
}

if ($Gui) {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    # --- reading the drives, in one place both the picker and the watcher use ---

    function Get-RemovableDriveIds {
        # The DeviceIDs ("E:") of every removable drive Windows sees right now,
        # sorted so the set can be compared against the last tick without caring
        # about order. Get-WmiObject Win32_LogicalDisk -Filter 'DriveType=2' is
        # the same enumeration the picker was built from, kept identical on
        # purpose so the watcher and the list can never disagree about what a
        # removable drive is.
        return @(Get-WmiObject Win32_LogicalDisk -Filter 'DriveType=2' |
            ForEach-Object { $_.DeviceID } | Sort-Object)
    }

    function Get-StickLabel($deviceId) {
        # The version suffix shown in the list for a drive: " (VMD 7)" for a
        # built stick, "" for a blank one, " (unreadable)" for one whose
        # update.json will not parse. Wrapped so a drive that is still mounting
        # cannot throw its way out of rebuilding the whole list.
        $updateJson = Join-Path "$deviceId\" 'update.json'
        if (-not (Test-Path $updateJson)) { return '' }
        try { return " (VMD $((Get-Content $updateJson -Raw | ConvertFrom-Json).version))" }
        catch { return ' (unreadable)' }
    }

    function Get-StickDescription($deviceId) {
        # The sentence announced when a drive appears. A blank drive and an
        # unreadable one are both fine outcomes the operator should see named,
        # not errors - a blank stick gets set up, an unreadable one gets rebuilt.
        $root = "$deviceId\"
        $updateJson = Join-Path $root 'update.json'
        if (-not (Test-Path $updateJson)) {
            return "Stick detected on $root - blank, will be set up as a VMD stick."
        }
        try {
            $version = (Get-Content $updateJson -Raw | ConvertFrom-Json).version
            return "Stick detected on $root - it has VMD $version."
        } catch {
            return "Stick detected on $root - unreadable, it will be rebuilt."
        }
    }

    function Set-DriveItems($ids) {
        # Rebuild the list from the given DeviceIDs. Called only when the set of
        # drives actually changed, so a selection the operator made by hand is
        # not cleared and re-made once a second.
        $drives.Items.Clear()
        foreach ($id in $ids) {
            [void]$drives.Items.Add("$id\$(Get-StickLabel $id)")
        }
    }

    function Select-DriveId($deviceId) {
        # Point the list at the item for $deviceId ("E:"), if it is there. The
        # item reads like "E:\ (VMD 7)", so its first space-separated token is the
        # drive root "E:\".
        for ($i = 0; $i -lt $drives.Items.Count; $i++) {
            if ((($drives.Items[$i]) -split ' ')[0] -eq "$deviceId\") {
                $drives.SelectedIndex = $i
                return
            }
        }
    }

    function Get-SelectedDriveId {
        # The DeviceID ("E:") of the current selection, or '' if nothing is
        # chosen. Strips the trailing backslash so it compares with the DeviceIDs
        # from Get-RemovableDriveIds.
        if (-not $drives.SelectedItem) { return '' }
        return (($drives.SelectedItem -split ' ')[0]).TrimEnd('\')
    }

    # --- the window ---------------------------------------------------------

    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'VMD update stick'
    $form.Size = New-Object System.Drawing.Size(560, 320)
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
    $form.Controls.Add($drives)

    $status = New-Object System.Windows.Forms.TextBox
    $status.Multiline = $true
    $status.ReadOnly = $true
    $status.ScrollBars = 'Vertical'
    $status.Location = New-Object System.Drawing.Point(16, 60)
    $status.Size = New-Object System.Drawing.Size(504, 110)
    $form.Controls.Add($status)

    # The one line the operator cannot miss: gold while it works, green when the
    # stick is ready, red when it is not. A non-technical person watching a box of
    # log lines scroll needs a single coloured verdict at the end, not to read the
    # last line and judge it. Hidden until the first build, so an idle window is
    # not a wall of colour that means nothing yet.
    $banner = New-Object System.Windows.Forms.Label
    $banner.Location = New-Object System.Drawing.Point(16, 178)
    $banner.Size = New-Object System.Drawing.Size(504, 44)
    $banner.TextAlign = 'MiddleCenter'
    $banner.Font = New-Object System.Drawing.Font($banner.Font.FontFamily, 10, [System.Drawing.FontStyle]::Bold)
    $banner.Visible = $false
    $form.Controls.Add($banner)

    $go = New-Object System.Windows.Forms.Button
    $go.Text = 'Build the stick'
    $go.Location = New-Object System.Drawing.Point(400, 236)
    $go.Size = New-Object System.Drawing.Size(120, 30)
    $go.Add_Click({
        # A build already running is left to finish. The button is disabled below
        # for the same reason, but a click queued in the instant before that takes
        # effect would otherwise start a SECOND child writing the same stick - two
        # processes each deleting and rewriting files\ at once, which is a corrupt
        # stick and a manifest that matches neither of them.
        if ($script:Building) { return }

        # The item reads like "E:\ (VMD 8)"; the drive is the part before the
        # first space, and the rest is only there to be read.
        $chosen = ($drives.SelectedItem -split ' ')[0]
        if (-not $chosen) { $status.AppendText("Plug the stick in and open this again.`r`n"); return }

        $script:Building = $true
        $go.Enabled = $false
        $go.Text = 'Working...'
        $script:FailReason = ''
        $banner.Visible = $true
        $banner.Text = 'Working - do not remove the stick.'
        $banner.BackColor = [System.Drawing.Color]::Goldenrod
        $banner.ForeColor = [System.Drawing.Color]::Black
        $status.AppendText("Building on $chosen ...`r`n")

        # The work runs in a child powershell - this same script without -Gui -
        # read LIVE, so the operator watches the four steps arrive one by one
        # instead of watching a frozen window. It is deliberately NOT the old
        # inline "& powershell ... 2>&1": that blocked this UI thread for the
        # whole minute the download and copy take, and a window that paints
        # nothing for a minute is one a non-technical person reads as crashed and
        # unplugs mid-write - the exact accident this rewrite exists to stop.
        #
        # A dedicated reader runspace does the blocking ReadLine on the child's
        # output and pushes each line into a thread-safe queue; the WinForms.Timer
        # below, on THIS thread, drains that queue into the box. The reader has to
        # be its own runspace and not an OutputDataReceived handler: that event
        # fires on a threadpool thread that has no runspace attached, and running
        # any PowerShell there dies with "there is no Runspace available to run
        # scripts in this thread" - which killed the whole window when tried.
        $script:BuildQueue = New-Object 'System.Collections.Concurrent.ConcurrentQueue[string]'
        $script:BuildState = [hashtable]::Synchronized(@{ Done = $false; ExitCode = $null })

        $reader = {
            param($scriptPath, $drive, $queue, $state)
            try {
                $psi = New-Object System.Diagnostics.ProcessStartInfo
                $psi.FileName = 'powershell.exe'
                # Each argument quoted on its own: a drive is "E:\" with no space,
                # but the script path can sit under "C:\Program Files\..." and an
                # unquoted path there splits into two arguments and the child never
                # starts. CreateNoWindow with UseShellExecute false means the child
                # flashes no console of its own.
                $psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -To `"$drive`""
                $psi.UseShellExecute = $false
                $psi.RedirectStandardOutput = $true
                $psi.RedirectStandardError = $true
                $psi.CreateNoWindow = $true
                $proc = New-Object System.Diagnostics.Process
                $proc.StartInfo = $psi
                [void]$proc.Start()
                # Blocking line reads, which is the whole reason this is on its own
                # thread. The child's Write-Host reaches this redirected stdout -
                # the full console host writes it there - so the [1/4]..[4/4] steps
                # arrive here as the child prints them, not in one lump at the end.
                while ($null -ne ($line = $proc.StandardOutput.ReadLine())) {
                    $queue.Enqueue($line)
                }
                # Anything on stderr is read only after stdout has closed. The
                # script routes every message it means to show through Write-Host,
                # so stderr carries only an unhandled crash and is near always
                # empty; reading it after avoids interleaving two blocking reads.
                $errText = $proc.StandardError.ReadToEnd()
                if ($errText) {
                    foreach ($errLine in ($errText -split "`r?`n")) {
                        if ($errLine) { $queue.Enqueue($errLine) }
                    }
                }
                $proc.WaitForExit()
                $state.ExitCode = $proc.ExitCode
            } catch {
                # The reader itself failing - the child could not even be started -
                # is still shown as a finished build that did not work, never a
                # silent hang.
                $queue.Enqueue("The stick was not finished: $($_.Exception.Message)")
                $state.ExitCode = 1
            } finally {
                # Set last of all, so the drain timer can never see Done before the
                # final line is safely in the queue.
                $state.Done = $true
            }
        }

        $script:BuildRunspace = [runspacefactory]::CreateRunspace()
        $script:BuildRunspace.Open()
        $script:BuildPowerShell = [powershell]::Create()
        $script:BuildPowerShell.Runspace = $script:BuildRunspace
        [void]$script:BuildPowerShell.AddScript($reader).
            AddArgument($PSCommandPath).AddArgument($chosen).
            AddArgument($script:BuildQueue).AddArgument($script:BuildState)
        $script:BuildHandle = $script:BuildPowerShell.BeginInvoke()

        $buildTimer.Start()
    })
    $form.Controls.Add($go)

    # Whether a build is running right now. The button reads it to refuse a
    # second start, and the drive-watcher reads it to stand off entirely while a
    # stick is being written, so a stick brushed in its socket mid-build is not
    # read as "removed" and acted on. Cleared only when the child has exited.
    $script:Building = $false
    $script:FailReason = ''

    # The set of drives as of the last look. The watcher compares against it and
    # only rebuilds the list when it actually differs.
    $script:LastDrives = @(Get-RemovableDriveIds)
    Set-DriveItems $script:LastDrives
    if ($drives.Items.Count -gt 0) {
        $drives.SelectedIndex = 0
        try { $status.AppendText((Get-StickDescription (Get-SelectedDriveId)) + "`r`n") } catch { }
    } else {
        $go.Enabled = $false
        $status.AppendText("Plug a stick in - it will be picked up automatically.`r`n")
    }

    # --- watching for a stick being plugged in or pulled out ----------------
    #
    # A System.Windows.Forms.Timer, NOT System.Timers.Timer: this one raises Tick
    # on the UI thread, so the callback may touch the combo box directly. A
    # System.Timers.Timer fires on a threadpool thread, and touching a control
    # from another thread is the classic cross-thread InvalidOperationException -
    # a crash a second or two after the window opens.
    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 1000
    $timer.Add_Tick({
        # Hands off entirely while a build is running. A stick being written to is
        # one the operator must not touch, and a momentary wobble in the socket
        # that dropped it from the drive list for one tick would otherwise clear
        # the selection, disable the button and print "The stick was removed."
        # over the top of a build that is still going fine. The build owns the
        # window until it finishes; the watcher resumes the moment it does.
        if ($script:Building) { return }

        try {
            $now = @(Get-RemovableDriveIds)
        } catch {
            # Enumeration itself can throw while a drive is mid-mount. Skip this
            # tick and let the next one see the drive once it has settled, rather
            # than showing an error for a stick that is fine a moment later.
            return
        }

        # Do nothing unless the set of drives changed, so a selection the
        # operator made by hand is not stamped over every single second.
        $before = @($script:LastDrives)
        $changed = ($before.Count -ne $now.Count)
        if (-not $changed) {
            foreach ($d in $now) { if ($before -notcontains $d) { $changed = $true; break } }
        }
        if (-not $changed) { return }

        $added = @($now | Where-Object { $before -notcontains $_ })
        $removed = @($before | Where-Object { $now -notcontains $_ })
        $selected = Get-SelectedDriveId

        try {
            Set-DriveItems $now
        } catch {
            # A drive that vanished between the enumerate above and the read here.
            # Leave LastDrives untouched so the next tick redoes this cleanly
            # rather than crashing on a drive that is no longer there.
            return
        }

        if ($added.Count -gt 0) {
            # A new stick: choose it for the operator and say what it is. If it is
            # not ready to read yet the describe throws; LastDrives is left as it
            # was so the next tick announces it once it has mounted.
            $new = $added[0]
            Select-DriveId $new
            $go.Enabled = $true
            try {
                $status.AppendText((Get-StickDescription $new) + "`r`n")
            } catch {
                return
            }
        } elseif ($selected -ne '' -and ($removed -contains $selected)) {
            # The stick that was selected was pulled out.
            $drives.SelectedIndex = -1
            $go.Enabled = $false
            $status.AppendText("The stick was removed.`r`n")
        } else {
            # A change that did not involve the selected drive - another drive
            # came or went. Keep the operator's choice rather than jumping it.
            if ($selected -ne '') { Select-DriveId $selected }
        }

        $script:LastDrives = $now
    })
    # --- draining the live build output -------------------------------------
    #
    # A second System.Windows.Forms.Timer, for the same reason the watcher is one:
    # Tick fires on the UI thread, so this may touch $status and $banner directly.
    # It runs faster than the watcher because it is showing progress a person is
    # reading, and it runs ONLY between a build starting and finishing - started
    # in the click handler, stopped here the moment the child has exited.
    $buildTimer = New-Object System.Windows.Forms.Timer
    $buildTimer.Interval = 150
    $buildTimer.Add_Tick({
        # Empty whatever the reader has queued since the last tick into the box.
        # The reader thread only ever touches the thread-safe queue, never a
        # control; this thread only ever reads the queue and writes the controls.
        # That split is what keeps the child's output off the classic cross-thread
        # InvalidOperationException the watcher's own comment warns about.
        $line = $null
        while ($script:BuildQueue.TryDequeue([ref]$line)) {
            $status.AppendText("$line`r`n")
            # Keep the reason the build failed, to name it on the red banner. The
            # no-internet sentence is preferred whenever it appears, because it is
            # the one an operator can act on without help.
            if ($line -match 'No internet') {
                $script:FailReason = 'No internet. Connect this laptop to the internet and press Build again.'
            } elseif ($line -match 'not finished' -and -not $script:FailReason) {
                # The child's line already begins "The stick was not finished:",
                # and the banner adds "The stick was NOT finished." itself, so the
                # prefix is stripped here to keep the banner from saying it twice.
                $script:FailReason = ($line.Trim() -replace '^(?i)the stick was not finished:\s*', '')
            }
        }

        # Nothing more to do until the child has actually exited. Done is set by
        # the reader only after its last line is queued, so the drain above has
        # already shown everything by the time this fires true.
        if (-not $script:BuildState.Done) { return }

        $buildTimer.Stop()
        # Tidy the reader runspace now the work is over. EndInvoke is wrapped
        # because a reader that threw has already reported it through the queue,
        # and a failure to reap it must not itself crash the window.
        try { [void]$script:BuildPowerShell.EndInvoke($script:BuildHandle) } catch { }
        if ($script:BuildPowerShell) { $script:BuildPowerShell.Dispose(); $script:BuildPowerShell = $null }
        if ($script:BuildRunspace) { $script:BuildRunspace.Close(); $script:BuildRunspace.Dispose(); $script:BuildRunspace = $null }

        if ($script:BuildState.ExitCode -eq 0) {
            $banner.Text = 'READY - take the stick to the VMD computer. You can unplug it now.'
            $banner.BackColor = [System.Drawing.Color]::ForestGreen
            $banner.ForeColor = [System.Drawing.Color]::White
        } else {
            $reason = $script:FailReason
            if (-not $reason) { $reason = 'See the messages above for what went wrong.' }
            $banner.Text = "The stick was NOT finished. $reason"
            $banner.BackColor = [System.Drawing.Color]::Firebrick
            $banner.ForeColor = [System.Drawing.Color]::White
        }

        $go.Text = 'Build the stick'
        # Offer a retry only if the stick is still there to write to. On a clean
        # or a failed build alike, a retry must be able to run - the build removes
        # update.json and manifest.json before it copies, so a half-written stick
        # is one the console ignores until a later build completes it.
        $go.Enabled = ((Get-SelectedDriveId) -ne '')
        # Cleared last, which lets the watcher take the window back. Until this
        # line it has been standing off, so the built stick was never read as
        # removed while it was being written.
        $script:Building = $false
    })

    $form.Add_FormClosed({
        # Stop and dispose both timers as the window closes, so a tick already
        # queued cannot fire into a disposed form and raise an ObjectDisposed
        # error the operator would see as a crash on the way out. The reader
        # runspace is torn down too if the window is closed mid-build, so no
        # orphaned child powershell is left holding the stick.
        $timer.Stop()
        $timer.Dispose()
        $buildTimer.Stop()
        $buildTimer.Dispose()
        if ($script:BuildPowerShell) {
            try { [void]$script:BuildPowerShell.EndInvoke($script:BuildHandle) } catch { }
            try { $script:BuildPowerShell.Dispose() } catch { }
        }
        if ($script:BuildRunspace) {
            try { $script:BuildRunspace.Close(); $script:BuildRunspace.Dispose() } catch { }
        }
    })
    $timer.Start()

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
    # One place classifies every failure that reaches here, so both downloads -
    # the code ZIP and the uv that fetches wheels - turn a no-internet fault into
    # the same one sentence. The WebException's Status name is joined to the
    # message because "The remote name could not be resolved" and
    # "NameResolutionFailure" each name the condition, and either is enough.
    $errText = "$($_.Exception.Message)"
    if ($_.Exception -is [System.Net.WebException]) {
        $errText = "$errText $($_.Exception.Status)"
    }
    if (Test-NetworkError $errText) {
        # The wording the GUI keys on for its red banner, and plain enough on its
        # own for whoever ran the script from a terminal.
        Write-Bad "The stick was not finished: No internet. Connect this laptop to the internet and press Build again."
    } else {
        Write-Bad "The stick was not finished: $($_.Exception.Message)"
        Write-Info "(line $($_.InvocationInfo.ScriptLineNumber) of update_stick.ps1)"
    }
    Write-Host ""
    exit 1
}

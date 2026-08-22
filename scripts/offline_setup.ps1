# =============================================================================
#  Makes the folder that goes to the offline machine, in one step.
#
#  Run this ON THE COMPUTER THAT HAS INTERNET. Double-click OfflineSetup.bat.
#
#  It downloads everything VMD needs, checks that what it has built can actually
#  run somewhere else, copies it into one folder, and zips that folder. What
#  comes out is a single file to put on a USB stick.
#
#  ---------------------------------------------------------------------------
#  Why this exists when install.bat and offline-kit.bat already do
#  ---------------------------------------------------------------------------
#
#  They do, and this runs both of them. What it adds is that it is one thing
#  rather than two, in the order they have to happen, with the "did that work?"
#  question answered in between - and it ends with a zip rather than a folder,
#  because a folder of two hundred thousand small files copied to a USB stick
#  by hand is how half a Python environment arrives at the other end.
#
#  A half-copied .venv is the worst possible failure here: it looks complete,
#  the offline installer's checks pass, and the console dies on an import three
#  days later on a machine with no internet and nobody who can read a
#  traceback. One file either copies or it does not.
#
#  ---------------------------------------------------------------------------
#  What ends up in the zip
#  ---------------------------------------------------------------------------
#
#    VMD\               the whole program folder - the Python environment, the
#                       interpreter it was built against, uv, ffmpeg, go2rtc,
#                       the detector's weights, VMD.exe, and the VLC installer
#                       in bin\vendor\.
#    START HERE.txt     the three steps, for whoever is standing at the other
#                       machine with no internet to look anything up on.
#
#  What does NOT travel is decided by scripts\offline_kit.ps1 and written out in
#  full there: no recordings, no settings.json, no camera passwords, no frame
#  grabs, no git history. Nothing about the perimeter this system watches leaves
#  on that USB stick.
# =============================================================================
param(
    # Where to build it. The desktop by default, because that is the one place
    # on a Windows machine everybody can find.
    [string]$Out,
    # Skip the download half, when everything is already here and only the
    # folder has to be made again. The verification still runs.
    [switch]$SkipInstall,
    # Leave it as a folder. For a copy going somewhere over a network, or a
    # machine without room for both the folder and the zip.
    [switch]$NoZip
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
. (Join-Path $PSScriptRoot '_common.ps1')

$root = Get-ProjectRoot

if (-not $Out) {
    $Out = Join-Path ([Environment]::GetFolderPath('Desktop')) 'VMD-offline'
}
$folder = Join-Path $Out 'VMD'

Write-Host ""
Write-Host "  Preparing VMD for the offline machine" -ForegroundColor White
Write-Host "  from $root" -ForegroundColor DarkGray
Write-Host "  to   $Out" -ForegroundColor DarkGray
Write-Host ""
Write-Info "This needs an internet connection and about 6 GB of free space."
Write-Info "It takes fifteen minutes or so the first time, most of it downloading."
Write-Host ""

Set-StepTotal 4

# =============================================================================
#  1. everything VMD needs, downloaded into this folder
# =============================================================================
Write-Step "Downloading and building everything VMD needs"
if ($SkipInstall) {
    Write-Info "Skipped, because -SkipInstall was given."
} else {
    # -NoLaunch, because a console window opening in the middle of building a
    # kit is a console pointed at no camera on the wrong machine. -NoAutostart,
    # because this machine is not the one that has to come back after a power
    # cut - and registering scheduled tasks on a developer's laptop is a
    # surprise nobody asked for.
    & (Join-Path $PSScriptRoot 'install.ps1') -NoLaunch -NoAutostart | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Bad "The install did not finish, so there is nothing complete to copy."
        Write-Info "Read the lines above: the reason is one of them. Fix it and run"
        Write-Info "this again - it does not start from the beginning."
        exit 1
    }
}

# =============================================================================
#  2. is it really self-contained, and copy it
# =============================================================================
# offline_kit.ps1 is what knows the answer to both questions: which files would
# make this work on a machine that has never seen the internet, and which files
# must never leave this one. Called rather than repeated - two lists of
# exclusions that have to agree is one list too many.
Write-Step "Checking it will run elsewhere, and copying it"
if (Test-Path $folder) {
    Write-Info "There is already a copy at $folder."
    Write-Info "It is written over rather than deleted, so a copy that was"
    Write-Info "interrupted carries on from where it stopped."
}
New-Item -ItemType Directory -Force -Path $Out | Out-Null
& (Join-Path $PSScriptRoot 'offline_kit.ps1') -To $folder | Out-Host
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Bad "The copy is not complete, so it is not worth carrying anywhere."
    Write-Info "The lines above say what is missing."
    exit 1
}

# =============================================================================
#  3. the note that travels with it
# =============================================================================
Write-Step "Writing the instructions that travel with it"

# Written into the folder rather than only into the zip, so that it is the first
# thing visible when the zip is opened - and so that it is still there on the
# machine it was extracted onto, which is where somebody will look for it in six
# months. Plain text, CRLF, because it will be opened in Notepad on a machine
# with nothing else on it.
$startHere = @"
VMD - installing it on this computer
====================================

This computer has no internet, and does not need one. Everything is here.

Three steps.

  1. Copy the folder called VMD, which is beside this file, to:

         C:\VMD

     Copy the whole thing. It is a few gigabytes and takes a few minutes.

  2. Open C:\VMD and double-click:

         offline-install.bat

     Windows will ask for permission once, to install VLC. Click Yes.
     It prints a list at the end. Everything under
     "BROKEN - MUST BE FIXED" has to be dealt with; everything else is fine.

  3. Double-click:

         cameras.bat

     It asks three questions for each camera - its address, what it watches,
     and which screen it belongs on - and puts a shortcut on the desktop for
     each one. Run it once per camera.

Then open each camera's shortcut and fill in the Settings tab: the camera's
username, password, and the address of each picture. Press Save. Recording
starts as soon as that is saved.

Updating it later
-----------------

This computer stays offline for good, but it can still be given new versions.
A new version travels on one USB stick dedicated to VMD:

  - On a computer WITH internet, double-click VMD-Update-Stick.bat (it is in
    the VMD folder) and press Build the stick.
  - Bring the stick here, open the console, and press Update now in the
    Software box at the bottom of the Settings tab. The stick is recognised on
    its own when it is plugged in.

If the new version does not start, the console puts the old one back by itself.
Part 4 of docs\OFFLINE-SETUP.md is the whole of it.

If step 2 does not work
-----------------------

Read INSTALL.md in the VMD folder. It has the same steps written out one at a
time, including how to do each of them by hand.

Do not run install.bat on this machine. That one downloads things, and this
machine has no internet - it will sit and wait for a connection that is not
coming.
"@
$notePath = Join-Path $Out 'START HERE.txt'
[System.IO.File]::WriteAllText($notePath, ($startHere -replace "`r?`n", "`r`n"), (New-Object System.Text.UTF8Encoding($false)))
Write-Ok "Wrote $notePath"

# =============================================================================
#  4. one file
# =============================================================================
Write-Step "Zipping it"
$zipPath = "$Out.zip"
if ($NoZip) {
    Write-Info "Skipped, because -NoZip was given."
    $zipPath = $null
} else {
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Write-Info "About 2 GB of files. This takes several minutes and looks like nothing"
    Write-Info "is happening; it is."
    try {
        # Written entry by entry rather than with CreateFromDirectory, and the
        # reason is not style.
        #
        # `CreateFromDirectory` under Windows PowerShell writes the paths with
        # BACKSLASHES. The zip format says they must be forward slashes
        # (APPNOTE 4.4.17.1). Windows Explorer and 7-Zip both cope, so the
        # archive looks perfect on the machine that made it - and anything that
        # follows the specification extracts one flat directory full of files
        # whose names have the separators in them as literal characters. On a
        # kit whose whole job is to be opened on a machine nobody can fix
        # remotely, that is not a risk worth carrying for the sake of one call.
        #
        # Fastest rather than Optimal because the bulk of this is
        # already-compressed wheels and binaries: Optimal spends twenty minutes
        # to save very little on them.
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        Add-Type -AssemblyName System.IO.Compression
        $fastest = [System.IO.Compression.CompressionLevel]::Fastest
        $archive = [System.IO.Compression.ZipFile]::Open(
            $zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
        try {
            $sep = [System.IO.Path]::DirectorySeparatorChar
            $prefix = (Resolve-Path $Out).Path.TrimEnd($sep) + $sep
            $files = Get-ChildItem -Path $Out -File -Recurse -Force
            $done = 0
            foreach ($file in $files) {
                $name = $file.FullName.Substring($prefix.Length).Replace($sep, '/')
                [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                    $archive, $file.FullName, $name, $fastest)
                $done++
                if (($done % 4000) -eq 0) {
                    Write-Info "  $done of $($files.Count) files"
                }
            }
        } finally {
            $archive.Dispose()
        }
        $size = [math]::Round((Get-Item $zipPath).Length / 1GB, 1)
        Write-Ok "Made $zipPath  ($size GB)"
    } catch {
        Write-Bad "The zip could not be made: $($_.Exception.Message)"
        Write-Info "The folder itself is finished and can be copied as it is:"
        Write-Info "  $Out"
        $zipPath = $null
    }
}

Write-Host ""
Write-Host "  ==============================================================" -ForegroundColor DarkGray
Write-Host "  READY" -ForegroundColor Green
Write-Host "  ==============================================================" -ForegroundColor DarkGray
Write-Host ""
if ($zipPath) {
    Write-Host "  Put this one file on the USB stick:" -ForegroundColor White
    Write-Host "    $zipPath" -ForegroundColor White
} else {
    Write-Host "  Put this whole folder on the USB stick:" -ForegroundColor White
    Write-Host "    $Out" -ForegroundColor White
}
Write-Host ""
Write-Host "  On the offline computer: unzip it, open the folder, and read" -ForegroundColor Gray
Write-Host "  START HERE.txt. It is three steps." -ForegroundColor Gray
Write-Host ""
exit 0

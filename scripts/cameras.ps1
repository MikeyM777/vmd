# =============================================================================
#  One VMD, two cameras: sets up a console for each of them.
#
#  Double-click cameras.bat.
#
#  ---------------------------------------------------------------------------
#  Why two consoles and not one window with two cameras in it
#  ---------------------------------------------------------------------------
#
#  There are two cameras now, one watching each street, and they are separate
#  installations of the same thing: a camera address, a stream, a recorder, a
#  detector, a disk budget, a set of areas to ignore, and a picture on a screen.
#  The console was built for one of those and does it well. Teaching it to hold
#  two would touch every part of it - the wall, the steering, the alarm strip,
#  the storage figures - and every one of those changes is a chance to break the
#  thing that already works, on a system whose whole job is not stopping.
#
#  So: the same program, run twice, each copy pointed at its own settings file.
#  Everything the console writes it writes beside that file - go2rtc.json,
#  streaming.json, the recorder's pid, the segment index, the events, the
#  remembered window - so two of them share nothing but the program folder and
#  never disagree about anything. The ports do not collide either: go2rtc is
#  given a preferred port and takes a free one when that is busy, which is
#  `free_port` in vmd\streaming\go2rtc.py, and it was written for this.
#
#  ---------------------------------------------------------------------------
#  Why the folder is named after the address
#  ---------------------------------------------------------------------------
#
#  Because that is what he calls them: "one software for 251 and one for 250".
#  The last part of the camera's address is the name he already uses, it is
#  short, and it is ASCII - which a folder name that is typed into a .bat file
#  on a Hebrew Windows had better be.
#
#  The name that appears on the screen is a different thing and is Hebrew:
#  "ירושלים", "השיטה". It is written into settings.json as `title`, drawn above
#  the pictures on the Live tab - including in fullscreen - and put on the
#  window itself so the taskbar can tell the two apart. It is also the name of
#  the desktop shortcut, which is the only one of these an operator ever reads.
#
#  Nothing here deletes anything. There is no -Remove: a camera folder holds
#  that camera's recordings, and this file is not going to be the reason a
#  night's footage is gone. Taking one out of use is deleting its shortcut,
#  which is said at the end of a listing.
# =============================================================================
param(
    # Set one up, asking for what it needs. The default is to list what is there.
    [switch]$Add,
    # Everything -Add would ask for, for a second machine or a scripted setup.
    [string]$Address,
    [string]$Name,
    [int]$Screen = 0,
    [switch]$NoShortcut
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$root       = Get-ProjectRoot
$camerasDir = Join-Path $root 'cameras'

# What a camera folder is: a folder under cameras\ with a settings.json in it.
# Read from the disk every time rather than from a list kept somewhere, because
# a list kept somewhere is a second thing that can be wrong.
function Get-Cameras {
    if (-not (Test-Path $camerasDir)) { return @() }
    Get-ChildItem -Path $camerasDir -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path (Join-Path $_.FullName 'settings.json') } |
        ForEach-Object {
            $settingsPath = Join-Path $_.FullName 'settings.json'
            $title = ''
            $host_ = ''
            try {
                $json = Get-Content $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
                if ($json.title)  { $title = [string]$json.title }
                if ($json.camera) { $host_ = [string]$json.camera.host }
            } catch {
                # A settings file that will not parse is still a camera folder,
                # and saying "one camera, and its settings file is broken" is
                # more use than leaving it out of the list entirely.
                $title = '(its settings file could not be read)'
            }
            [pscustomobject]@{
                Label    = $_.Name
                Folder   = $_.FullName
                Settings = $settingsPath
                Title    = $title
                Address  = $host_
            }
        }
}

# The last part of an address, which is what these two cameras are called in
# every conversation about them. Anything that is not an address is used as it
# stands, with whatever cannot be in a folder name taken out.
function Get-Label($address) {
    $text = ([string]$address).Trim()
    if ($text -match '^\d{1,3}\.\d{1,3}\.\d{1,3}\.(\d{1,3})$') { return $Matches[1] }
    $text = $text -replace '^\w+://', ''
    $text = $text -split '[/:]' | Select-Object -First 1
    if ($text -match '^\d{1,3}\.\d{1,3}\.\d{1,3}\.(\d{1,3})$') { return $Matches[1] }
    $clean = ($text -replace '[^A-Za-z0-9._-]', '-').Trim('-')
    if (-not $clean) { return 'camera' }
    return $clean
}

# Windows refuses these in a file name, and a shortcut that cannot be saved is
# the one step of this whose failure the operator would not understand.
function Get-SafeFileName($name) {
    $clean = $name
    foreach ($bad in [System.IO.Path]::GetInvalidFileNameChars()) {
        $clean = $clean.Replace([string]$bad, '')
    }
    return $clean.Trim()
}

function Show-Cameras {
    $cameras = @(Get-Cameras)
    Write-Host ""
    Write-Host "  Cameras set up in this folder" -ForegroundColor White
    Write-Host "  $camerasDir" -ForegroundColor DarkGray
    Write-Host ""
    if ($cameras.Count -eq 0) {
        Write-Info "None yet."
        Write-Host ""
        Write-Info "This machine watches two streets with two cameras, so it needs two"
        Write-Info "consoles. Run cameras.bat and answer three questions to set one up,"
        Write-Info "then run it again for the second camera."
        Write-Host ""
        Write-Info "A single camera does not need any of this: the plain settings.json"
        Write-Info "beside VMD.exe still works exactly as it always has."
        Write-Host ""
        return
    }
    foreach ($camera in $cameras) {
        $shown = if ($camera.Title) { $camera.Title } else { '(no name yet - set it in the Settings tab)' }
        Write-Host ("    {0,-8} {1}" -f $camera.Label, $shown) -ForegroundColor White
        Write-Host ("    {0,-8} {1}" -f '', $(if ($camera.Address) { $camera.Address } else { 'no address yet' })) -ForegroundColor DarkGray
        Write-Host ("    {0,-8} {1}" -f '', $camera.Folder) -ForegroundColor DarkGray
        Write-Host ""
    }
    Write-Info "Each of these opens from its own shortcut on the desktop, and records"
    Write-Info "into its own folder. They share nothing but the program itself."
    Write-Host ""
    Write-Info "To stop using one, delete its desktop shortcut. Its folder above is"
    Write-Info "left alone on purpose - that is where its recordings are."
    Write-Host ""
}

function Add-Camera {
    Write-Host ""
    Write-Host "  Setting up a camera" -ForegroundColor White
    Write-Host ""

    $address = $Address
    if (-not $address) {
        Write-Info "The camera's address on the network - 192.168.1.250, for example."
        $address = Read-Host "  Camera address"
    }
    $address = ([string]$address).Trim()
    if (-not $address) {
        Write-Bad "No address, so there is nothing to set up. Nothing was changed."
        return 1
    }

    $title = $Name
    if (-not $title) {
        Write-Host ""
        Write-Info "What this camera watches, in your own words - the street it looks at."
        Write-Info "Hebrew is fine. It is written above the pictures and on the window,"
        Write-Info "so that you can see at a glance which console is which."
        $title = Read-Host "  What it watches"
    }
    $title = ([string]$title).Trim()

    $screen = $Screen
    if ($screen -le 0) {
        Write-Host ""
        Write-Info "This desktop has a screen for each camera. Which one is this camera's?"
        Write-Info "Press Enter if there is only one screen, or you would rather drag the"
        Write-Info "window there yourself - it remembers where it was left."
        $answer = Read-Host "  Screen number (1 or 2)"
        if ($answer -match '^\d+$') { $screen = [int]$answer }
    }

    $label  = Get-Label $address
    $folder = Join-Path $camerasDir $label
    $settingsPath = Join-Path $folder 'settings.json'

    if (Test-Path $settingsPath) {
        Write-Host ""
        Write-Warn "There is already a camera $label here:"
        Write-Info "  $settingsPath"
        Write-Info "Its settings and its recordings are left exactly as they are. Only"
        Write-Info "the way it is started - the shortcut - is written again."
    } else {
        New-Item -ItemType Directory -Force -Path $folder | Out-Null
        # Four fields and no more. Everything else in the file has a default the
        # program is happy with, and the rest of the camera - the username, the
        # password, the stream addresses - is typed into the Settings tab, which
        # is where every instruction anybody has been given says to type it.
        $settings = [ordered]@{
            title  = $title
            camera = [ordered]@{ host = $address; username = ''; password = ''; streams = @() }
        }
        # Which monitor this console belongs on lives in the settings file and
        # not in the shortcut, so that it is one fact in one place: the Settings
        # tab shows it, the operator can change it there, and anything that
        # starts this console - the shortcut, the .bat, the scheduled task -
        # gets it right without being told.
        if ($screen -gt 0) { $settings['screen'] = $screen }
        # UTF8 without a byte order mark: Python's json.loads chokes on a BOM,
        # and PowerShell 5's Out-File would put one there. This project's shell
        # is PowerShell 7, whose default is already without - written explicitly
        # so that it stays true if this is ever run by the other one.
        $json = $settings | ConvertTo-Json -Depth 6
        [System.IO.File]::WriteAllText($settingsPath, $json, (New-Object System.Text.UTF8Encoding($false)))
        Write-Ok "Made $settingsPath"
    }

    # --- the way to start it -------------------------------------------------
    #
    # A .bat in the program folder as well as the desktop shortcut, because the
    # shortcut is the thing that gets deleted by accident and this is how it is
    # made again. Both point at the same command.
    $batName = "Camera $label.bat"
    $batPath = Join-Path $root $batName
    $bat = @"
@echo off
REM ============================================================
REM  Opens the VMD console for camera $label.
REM
REM  Written by scripts\cameras.ps1. Everything this console
REM  records, and everything it remembers, lives in:
REM    cameras\$label\
REM
REM  Delete this file and nothing is lost but the way to start it -
REM  run cameras.bat to write it again.
REM ============================================================
cd /d "%~dp0"
start "" "%~dp0VMD.exe" --settings "%~dp0cameras\$label\settings.json"
"@
    [System.IO.File]::WriteAllText($batPath, $bat, (New-Object System.Text.ASCIIEncoding))
    Write-Ok "Made $batName"

    if (-not $NoShortcut) {
        $shortcutName = Get-SafeFileName $(if ($title) { $title } else { "VMD $label" })
        $desktop = [Environment]::GetFolderPath('Desktop')
        $linkPath = Join-Path $desktop "$shortcutName.lnk"
        try {
            $shell = New-Object -ComObject WScript.Shell
            $link = $shell.CreateShortcut($linkPath)
            $link.TargetPath = Join-Path $root 'VMD.exe'
            $link.Arguments = ('--settings "{0}"' -f $settingsPath)
            $link.WorkingDirectory = $root
            $link.Description = $(if ($title) { "VMD - $title" } else { "VMD - camera $label" })
            $link.Save()
            Write-Ok "Put `"$shortcutName`" on the desktop."
        } catch {
            Write-Warn "Could not put a shortcut on the desktop: $($_.Exception.Message)"
            Write-Info "Start it with $batName in the VMD folder instead - it does the same."
        }
    }

    Write-Host ""
    Write-Ok "Camera $label is set up."
    Write-Host ""
    Write-Info "Next, on this camera's own console:"
    Write-Info "  1. Open it - the shortcut on the desktop, or $batName"
    Write-Info "  2. Go to Settings and type the camera's username, password and the"
    Write-Info "     address of each picture it shows. Press Save."
    Write-Info "  3. Recording starts as soon as that is saved."
    Write-Host ""
    Write-Info "Then run cameras.bat again for the other camera."
    Write-Host ""
    Write-Info "To have both start by themselves after a restart, run autostart-on.bat"
    Write-Info "once when both are set up. It finds every camera in cameras\."
    Write-Host ""
    return 0
}

if ($Add -or $Address -or $Name) { exit (Add-Camera) }

Show-Cameras

# Double-clicked, which is the only way this is ever run. A listing and a dead
# end would leave the operator with the right screen in front of him and no way
# on from it, so the next step is offered here rather than described.
$existing = @(Get-Cameras).Count
$question = if ($existing -eq 0) { "  Set one up now? [Y/n]" } else { "  Set up another camera? [y/N]" }
$answer = (Read-Host $question).Trim().ToLower()
$yes = if ($existing -eq 0) { $answer -ne 'n' } else { $answer -eq 'y' }
if ($yes) { exit (Add-Camera) }
exit 0

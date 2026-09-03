# =============================================================================
#  One VMD, two cameras: sets BOTH of them up in a single run, and puts one
#  "VMD" button on the desktop that opens both side by side.
#
#  Double-click cameras.bat and answer a few questions. That is the whole of it.
#
#  ---------------------------------------------------------------------------
#  Why two consoles and not one window with two cameras in it
#  ---------------------------------------------------------------------------
#
#  There are two cameras, one watching each street, and they are separate
#  installations of the same thing: a camera address, a stream, a recorder, a
#  detector, a disk budget, a set of areas to ignore, and a picture on a screen.
#  The console was built for one of those and does it well. Teaching it to hold
#  two would touch every part of it and every one of those changes is a chance to
#  break the thing that already works, on a system whose whole job is not
#  stopping.
#
#  So: the same program, run twice, each copy pointed at its own settings file.
#  Everything the console writes it writes beside that file - go2rtc.json,
#  streaming.json, the recorder's pid, the segment index, the events, the
#  remembered window - so two of them share nothing but the program folder and
#  never disagree. The ports do not collide either: go2rtc takes a free one when
#  its preferred port is busy (`free_port` in vmd\streaming\go2rtc.py).
#
#  What changed from the older version of this file: it set up ONE camera per run
#  and you ran it twice; now it sets up BOTH in one go, and instead of a shortcut
#  per camera it makes a single desktop button, "VMD", that opens the two of them
#  split across the one screen - 250 on the left, 251 on the right - each behind
#  the crash-watchdog (scripts\run_console.ps1), so a console that falls over
#  comes straight back on its own half.
#
#  ---------------------------------------------------------------------------
#  Why the folder is named after the address
#  ---------------------------------------------------------------------------
#
#  Because that is what he calls them: "one software for 251 and one for 250".
#  The last part of the camera's address is the name he already uses, it is
#  short, and it is ASCII - which a folder name typed into a .bat file on a
#  Hebrew Windows had better be. The name that appears on the screen is a
#  different thing and is Hebrew ("ירושלים", "השיטה"): it is written into
#  settings.json as `title`, drawn above the pictures, and put on the window so
#  the taskbar can tell the two apart.
#
#  Nothing here deletes anything. A camera folder holds that camera's
#  recordings, and this file is not going to be the reason a night's footage is
#  gone. Taking one out of use is deleting the VMD button, or the camera's own
#  repair .bat; the folder is left alone on purpose.
# =============================================================================
param(
    # Set up the two cameras, asking for what they need. The default when
    # double-clicked. -Add is kept for a one-camera or scripted setup.
    [switch]$Add,
    # Everything a single -Add would ask for, for a second machine or a script.
    [string]$Address,
    [string]$Name,
    [string]$Username,
    [string]$Password,
    [int]$Screen = 0,
    [switch]$NoShortcut,
    # Dot-sourced by scripts\setup.ps1 to reuse the functions below (Set-BothCameras,
    # Set-VmdButton, Get-Cameras) without running the interactive flow. Nothing
    # here happens on load when this is set - the caller drives it.
    [switch]$AsModule
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$root       = Get-ProjectRoot
$camerasDir = Join-Path $root 'cameras'

# The two this machine watches, and the side each one takes on the screen. The
# addresses are only defaults - Enter accepts them, anything else replaces them -
# so a different pair of cameras is a matter of typing over them, not editing
# this file.
$DEFAULT_CAMERAS = @(
    [pscustomobject]@{ Address = '192.168.1.250'; Side = 'left' },
    [pscustomobject]@{ Address = '192.168.1.251'; Side = 'right' }
)

# What a camera folder is: a folder under cameras\ with a settings.json in it.
# Read from disk every time rather than from a list kept somewhere, because a
# list kept somewhere is a second thing that can be wrong.
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

# Write one camera's folder, settings file and repair .bat. Returns the camera,
# or $null when there was no address to write. The desktop button is made once,
# afterwards, over all the cameras - not here.
function Write-Camera($address, $title, $username, $password, $screen) {
    $address = ([string]$address).Trim()
    if (-not $address) { return $null }

    $label  = Get-Label $address
    $folder = Join-Path $camerasDir $label
    $settingsPath = Join-Path $folder 'settings.json'

    if (Test-Path $settingsPath) {
        Write-Warn "There is already a camera $label here - its settings and its"
        Write-Info "recordings are left exactly as they are; only the way to start it"
        Write-Info "is written again."
    } else {
        New-Item -ItemType Directory -Force -Path $folder | Out-Null
        # title, host, and - when they were given - the login. streams stays
        # empty: the Settings tab draws blank stream cards for a new install and
        # that is where the picture addresses are typed, which is what every
        # instruction the operator has says to do. Everything else in the file
        # has a default the program is happy with.
        $settings = [ordered]@{
            title  = ([string]$title).Trim()
            camera = [ordered]@{
                host     = $address
                username = ([string]$username)
                password = ([string]$password)
                streams  = @()
            }
        }
        if ($screen -gt 0) { $settings['screen'] = $screen }
        # UTF8 without a byte order mark: Python's json.loads chokes on a BOM.
        $json = $settings | ConvertTo-Json -Depth 6
        [System.IO.File]::WriteAllText($settingsPath, $json, (New-Object System.Text.UTF8Encoding($false)))
        Write-Ok "Made $settingsPath"
    }

    # A repair .bat in the program folder: the way to open this ONE camera on its
    # own if the VMD button is ever deleted, and the record of what starts it. It
    # goes through the watchdog too, so even a single camera reopens after a
    # crash. No half is passed, so on its own it opens where it was last left.
    $batName = "Camera $label.bat"
    $batPath = Join-Path $root $batName
    $bat = @"
@echo off
REM ============================================================
REM  Opens the VMD console for camera $label on its own.
REM
REM  The usual way in is the "VMD" button on the desktop, which
REM  opens both cameras side by side. This file is the fallback:
REM  it opens just this one, through scripts\run_console.ps1 (the
REM  watchdog) so it reopens itself if it ever crashes.
REM
REM  Everything this console records lives in cameras\$label\.
REM  Delete this file and nothing is lost but this fallback - run
REM  cameras.bat to write it again.
REM ============================================================
cd /d "%~dp0"
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0scripts\run_console.ps1" -Settings "%~dp0cameras\$label\settings.json"
"@
    [System.IO.File]::WriteAllText($batPath, $bat, (New-Object System.Text.ASCIIEncoding))
    Write-Ok "Made $batName"

    return [pscustomobject]@{
        Label = $label; Folder = $folder; Settings = $settingsPath; Title = ([string]$title).Trim()
    }
}

# The one button the operator uses: opens every camera at once, split-screen,
# through scripts\open_all_cameras.ps1. Made once, over all the cameras, and
# rewritten on every setup so it always reflects what is installed.
function Set-VmdButton {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $linkPath = Join-Path $desktop 'VMD.lnk'
    $opener = Join-Path $root 'scripts\open_all_cameras.ps1'
    $exePath = Join-Path $root 'VMD.exe'
    try {
        $shell = New-Object -ComObject WScript.Shell
        $link = $shell.CreateShortcut($linkPath)
        $link.TargetPath = 'powershell.exe'
        $link.Arguments = ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $opener)
        $link.WorkingDirectory = $root
        if (Test-Path $exePath) { $link.IconLocation = $exePath }
        $link.Description = 'VMD - opens every camera, side by side'
        $link.Save()
        Write-Ok "Put the `"VMD`" button on the desktop."
        return $true
    } catch {
        Write-Warn "Could not put the VMD button on the desktop: $($_.Exception.Message)"
        Write-Info "Open the cameras with scripts\open_all_cameras.ps1, or each Camera <n>.bat."
        return $false
    }
}

function Show-Cameras {
    $cameras = @(Get-Cameras)
    Write-Host ""
    Write-Host "  Cameras set up in this folder" -ForegroundColor White
    Write-Host "  $camerasDir" -ForegroundColor DarkGray
    Write-Host ""
    if ($cameras.Count -eq 0) {
        Write-Info "None yet. Answer the questions below and both cameras are set up at"
        Write-Info "once, with one `"VMD`" button on the desktop that opens them side by side."
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
    Write-Info "The `"VMD`" button on the desktop opens all of these side by side. Each"
    Write-Info "records into its own folder above and they share nothing but the program."
    Write-Host ""
}

# Ask for one camera's details, filling in the address default and the login from
# the one before it - the two cameras on this machine almost always share a login,
# so the second is offered the first one's and Enter accepts it.
function Read-Camera($number, $defaultAddress, $side, $lastUser, $lastPass) {
    Write-Host ""
    Write-Host ("  Camera $number - the $side of the screen") -ForegroundColor White
    Write-Host ""

    Write-Info "Its address on the network. Press Enter for $defaultAddress."
    $address = (Read-Host "  Camera $number address").Trim()
    if (-not $address) { $address = $defaultAddress }

    Write-Host ""
    Write-Info "What this camera watches, in your own words - the street it looks at."
    Write-Info "Hebrew is fine. It is written above the pictures and on the window."
    $title = (Read-Host "  What camera $number watches").Trim()

    Write-Host ""
    Write-Info "The camera's login. Press Enter to leave it and type it later in the"
    Write-Info "Settings tab. These two cameras usually share one login."
    $userPrompt = if ($lastUser) { "  Username [$lastUser]" } else { "  Username" }
    $username = (Read-Host $userPrompt).Trim()
    if (-not $username -and $lastUser) { $username = $lastUser }

    $password = $lastPass
    if ($username) {
        $secure = Read-Host "  Password (leave blank to keep/skip)" -AsSecureString
        $typed = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
        if ($typed) { $password = $typed }
    }

    return [pscustomobject]@{
        Address = $address; Title = $title; Username = $username; Password = $password
    }
}

# The guided setup: both cameras, then the button. This is what a double-click
# runs into.
function Set-BothCameras {
    Write-Host ""
    Write-Host "  Setting up both cameras" -ForegroundColor White
    Write-Host "  ----------------------" -ForegroundColor DarkGray
    Write-Info "This machine watches two streets. We will set up both now - it takes"
    Write-Info "a minute - and put one button on the desktop that opens them together."

    $made = @()
    $lastUser = ''
    $lastPass = ''
    $number = 0
    foreach ($default in $DEFAULT_CAMERAS) {
        $number += 1
        $answer = Read-Camera $number $default.Address $default.Side $lastUser $lastPass
        $camera = Write-Camera $answer.Address $answer.Title $answer.Username $answer.Password 0
        if ($camera) { $made += $camera }
        $lastUser = $answer.Username
        $lastPass = $answer.Password
    }

    if ($made.Count -eq 0) {
        Write-Bad "No cameras were set up. Nothing was changed."
        return 1
    }

    Write-Host ""
    if (-not $NoShortcut) { Set-VmdButton | Out-Null }

    Write-Host ""
    Write-Ok ("Set up {0} camera(s)." -f $made.Count)
    Write-Host ""
    Write-Info "Next, ONCE per camera, to get a picture:"
    Write-Info "  1. Double-click the `"VMD`" button on the desktop."
    Write-Info "  2. In each console: Settings tab, type the address of each picture"
    Write-Info "     the camera shows (its stream) and, if you skipped it, the login."
    Write-Info "     Press Save. The live picture starts as soon as it is saved."
    Write-Host ""
    Write-Info "  To record as well, tick 'Record everything to disk' in the Storage"
    Write-Info "  box on the same Settings tab."
    Write-Host ""
    Write-Info "To have both start by themselves after a restart, run autostart-on.bat"
    Write-Info "once. It finds every camera in cameras\."
    Write-Host ""
    return 0
}

# The single-camera / scripted path, kept for a second machine or a script. It
# writes one camera and gives it its own desktop shortcut through the watchdog,
# and refreshes the VMD button so it covers the new one too.
function Add-Camera {
    Write-Host ""
    Write-Host "  Setting up a camera" -ForegroundColor White
    Write-Host ""

    $address = $Address
    if (-not $address) {
        Write-Info "The camera's address on the network - 192.168.1.250, for example."
        $address = Read-Host "  Camera address"
    }
    $title = $Name
    if (-not $title -and -not $Address) {
        Write-Host ""
        Write-Info "What this camera watches, in your own words. Hebrew is fine."
        $title = Read-Host "  What it watches"
    }
    $screen = $Screen

    $camera = Write-Camera $address $title $Username $Password $screen
    if (-not $camera) {
        Write-Bad "No address, so there is nothing to set up. Nothing was changed."
        return 1
    }
    if (-not $NoShortcut) { Set-VmdButton | Out-Null }
    Write-Host ""
    Write-Ok "Camera $($camera.Label) is set up."
    Write-Info "Open it with the VMD button, then add its stream and login in Settings."
    Write-Host ""
    return 0
}

# When dot-sourced as a module (by scripts\setup.ps1) nothing below runs: the
# caller has the functions and drives them itself.
if (-not $AsModule) {
    # -Add or any scripted field means the single-camera path. A bare double-click
    # means the guided both-cameras path.
    if ($Add -or $Address -or $Name) { exit (Add-Camera) }

    Show-Cameras

    $existing = @(Get-Cameras).Count
    $question = if ($existing -eq 0) { "  Set up both cameras now? [Y/n]" } else { "  Set the cameras up again? [y/N]" }
    $answer = (Read-Host $question).Trim().ToLower()
    $yes = if ($existing -eq 0) { $answer -ne 'n' } else { $answer -eq 'y' }
    if ($yes) { exit (Set-BothCameras) }

    # Nothing to set up, but there may be cameras already there whose button is
    # missing - deleted by accident. Offer to put it back rather than ending on a
    # listing with no way forward.
    if ($existing -gt 0 -and -not $NoShortcut) {
        $fix = (Read-Host "  Put the VMD button back on the desktop? [Y/n]").Trim().ToLower()
        if ($fix -ne 'n') { Set-VmdButton | Out-Null }
    }
    exit 0
}

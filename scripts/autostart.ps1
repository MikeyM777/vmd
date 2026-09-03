# =============================================================================
#  Makes the system come back by itself after a restart, and takes it away
#  again.
#
#  Double-click autostart-on.bat to switch it on, autostart-off.bat
#  to switch it off. Everything below is what those two files do.
#
#  ---------------------------------------------------------------------------
#  What gets created
#  ---------------------------------------------------------------------------
#
#  Two scheduled tasks, both running as the person who is logged in, both
#  triggered when that person logs in:
#
#    VMD Recorder   runs scripts\recorder_service.ps1, which starts the
#                   recording service and writes recorder.pid.
#    VMD Console    runs VMD.exe, 45 seconds later.
#
#  Two per camera, once there is more than one camera. This machine watches two
#  streets with two cameras, and each has its own console pointed at its own
#  settings file under cameras\ - so the tasks are named after the camera:
#
#    VMD Recorder 250, VMD Console 250, VMD Recorder 251, VMD Console 251
#
#  A folder with no cameras\ in it - which is every installation before today,
#  and every single-camera one after it - gets exactly the two tasks it always
#  got, with the names it always had. Nothing anybody has been told to look for
#  in Task Scheduler moves.
#
#  Two tasks rather than one because the recording is the product and the
#  console is the window onto it. A console that fails to open - a broken VLC,
#  a bad settings file, a Qt that will not start - must not be able to stop the
#  disk filling. Starting the recorder first and separately means the worst a
#  broken console can do is be a broken console.
#
#  The 45-second delay is what keeps them from racing. The console starts a
#  recorder of its own if it does not find one, and it looks for one by reading
#  recorder.pid; the delay is there so that file has been written before the
#  console looks. Both sides check, so the race is narrow either way, but a
#  narrow race that runs at every boot for years is a race that eventually
#  happens.
#
#  Also set, because they are the difference between a laptop that records for
#  a year and one that records until somebody shuts the lid:
#
#    - never sleep, never hibernate, never spin the disks down, on mains
#    - closing the lid does nothing
#
#  ---------------------------------------------------------------------------
#  The part that is a real decision: logging in
#  ---------------------------------------------------------------------------
#
#  A logon task fires when somebody logs in. Nothing fires before that. After a
#  power cut the laptop reaches the Windows sign-in screen and stops there,
#  recording nothing, until a person types a password.
#
#  -EnableAutoLogon removes that step: Windows signs the account in by itself
#  at boot. It is not switched on by this installer and never happens by
#  accident, because it costs something real:
#
#    - anyone who can open the lid gets a signed-in desktop, with no password
#    - the password is stored in the registry in clear text, readable by any
#      administrator on the machine
#
#  For this deployment - one laptop, no network of any kind, physically inside
#  the perimeter it is watching, doing nothing but recording - that is usually
#  the right trade, because the alternative is that every power cut costs the
#  recording until somebody notices. It is still a decision for whoever owns
#  the site, not for an installer, so it is asked for explicitly.
#
#  Running the recorder as SYSTEM at boot would avoid the question entirely,
#  and was deliberately not done: SYSTEM-owned recordings and a SYSTEM-owned
#  SQLite index are then read and deleted by a console running as an ordinary
#  user, which works right up until it does not, and the failure would show up
#  as retention quietly not deleting anything on a full disk.
#
#  ---------------------------------------------------------------------------
#  When Windows will not have a scheduled task at all
#  ---------------------------------------------------------------------------
#
#  It happened on the first machine this was deployed to:
#
#      [6/7] Making the system start by itself after a restart
#      TerminatingError(Register-ScheduledTask): "... Access is denied."
#            Neither scheduled task was registered.
#
#  Creating a task in the root folder of Task Scheduler is not something every
#  account may do. On a machine whose C:\Windows\System32\Tasks is locked down,
#  or for an account that is not an administrator, Windows answers "Access is
#  denied" and there is nothing this script can say to it that changes that.
#
#  So there are two mechanisms now, and this tries them in order:
#
#    1. The scheduled tasks above. Everything they can do - restart after a
#       failure, run a task that was missed while the machine was off, ignore
#       the battery - only exists here, so it is what is asked for first.
#    2. If Windows refuses, and this is not already running as administrator,
#       the same registration is retried once with administrator permission.
#       That is one UAC prompt, on the machine where somebody is standing.
#    3. If that is refused too - or there is no administrator to be had, which
#       is the case that has no way out - shortcuts are put in the Startup
#       folder instead. They need no permission from anybody: they run when
#       this user signs in, exactly like the tasks, and they are what the
#       installer's summary then reports.
#
#  What the fallback costs, stated rather than glossed over: no restart after a
#  crash, no catch-up for a start that was missed, and no protection from Task
#  Scheduler's battery rules - because none of those are Startup-folder
#  features. The recording still comes back after a power cut, which is the
#  thing the site is paying for.
# =============================================================================
param(
    [switch]$Install,
    [switch]$Remove,
    [switch]$Status,
    [switch]$Quiet,
    [switch]$EnableAutoLogon,
    [switch]$DisableAutoLogon,
    # Both set by the elevated retry this script starts of itself, and by
    # nothing else. -ForUser carries the account the tasks are FOR, because the
    # elevated copy may be running as a different administrator entirely, and
    # $env:USERNAME there would silently make the tasks for the wrong person.
    # -NoElevate is what stops that copy trying to elevate again.
    [string]$ForUser,
    [switch]$NoElevate
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$root = Get-ProjectRoot

$RECORDER_TASK = 'VMD Recorder'
$CONSOLE_TASK  = 'VMD Console'
$WINLOGON      = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'

# What has to start, one entry per console. Lives in scripts\_common.ps1 now,
# because both installers have to reach the same answer when they check that
# this worked - and they used to do it by looking for two fixed names, which is
# the right answer for one camera and the wrong one for two.
function Get-Consoles { Get-VmdConsoles $root }

# Which half of the one screen a console takes when the cameras auto-start
# together: the first camera on the left, the second on the right, any beyond
# that none - a screen only splits in two. The same rule as the VMD button
# (scripts\open_all_cameras.ps1), so a reboot lands the pictures exactly where a
# double-click of the button would. Returns '' for "no half", which the
# launchers treat as "open where it was left".
function Get-ConsolePlace([int]$index) {
    switch ($index) { 0 { 'left' } 1 { 'right' } default { '' } }
}

# Every task this script has ever created, so that switching it off takes away
# the ones a previous layout left behind. Asked of Windows by name pattern
# rather than worked out from what is on the disk: a camera folder deleted by
# hand would otherwise leave a task running for ever with nothing to find.
function Get-OurTasks {
    Get-ScheduledTask -ErrorAction SilentlyContinue |
        Where-Object { $_.TaskName -like 'VMD Recorder*' -or $_.TaskName -like 'VMD Console*' }
}

if (-not ($Install -or $Remove -or $Status -or $EnableAutoLogon -or $DisableAutoLogon)) {
    $Status = $true
}

function Get-Task($name) {
    Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
}

# -----------------------------------------------------------------------------
#  The Startup folder, which is the fallback and nothing else
# -----------------------------------------------------------------------------
# Only reached when Windows has refused to create the tasks, elevated and
# otherwise. A shortcut in this folder needs no permission from anybody: it
# belongs to the account it is in, and Windows runs it when that account signs
# in - which is the same moment the -AtLogOn trigger would have fired.

function Get-OurShortcuts {
    $dir = Get-StartupDir
    if (-not (Test-Path $dir)) { return @() }
    @(Get-ChildItem -Path $dir -Filter '*.lnk' -ErrorAction SilentlyContinue |
        Where-Object { $_.BaseName -like 'VMD Recorder*' -or $_.BaseName -like 'VMD Console*' })
}

function New-StartupShortcut($name, $target, $argumentString, $workingDir, $description) {
    $dir = Get-StartupDir
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $path = Join-Path $dir "$name.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath       = $target
    $shortcut.Arguments        = $argumentString
    $shortcut.WorkingDirectory = $workingDir
    # 7 is minimised. The recorder wrapper hides its own window; this is for the
    # second or two before it does, so that a sign-in does not flash a console
    # at whoever is standing there.
    $shortcut.WindowStyle      = 7
    $shortcut.Description      = $description
    $shortcut.Save()
    return (Test-Path $path)
}

function Install-StartupShortcuts {
    $exe = Join-Path $root 'VMD.exe'
    $recorderScript = Join-Path $PSScriptRoot 'recorder_service.ps1'
    $consoleScript  = Join-Path $PSScriptRoot 'startup_console.ps1'
    $made = @()

    $consoles = @(Get-Consoles)
    for ($ci = 0; $ci -lt $consoles.Count; $ci++) {
        $console = $consoles[$ci]
        $suffix = $console.Suffix
        $forWhat = if ($suffix) { " for camera$suffix" } else { "" }
        # Only split the screen when there is more than one camera - a lone
        # console opens full, exactly as the VMD button does (open_all_cameras.ps1).
        $place = if ($consoles.Count -ge 2) { Get-ConsolePlace $ci } else { '' }

        $ok = New-StartupShortcut "$RECORDER_TASK$suffix" 'powershell.exe' `
            ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Settings "{1}"' -f $recorderScript, $console.Settings) `
            $root "Starts VMD recording$forWhat at sign-in."
        if ($ok) { $made += "$RECORDER_TASK$suffix"; Write-Ok "`"$RECORDER_TASK$suffix`" put in the Startup folder - recording starts at sign-in." }

        if (Test-Path $exe) {
            # Through startup_console.ps1, which waits 45 seconds - a shortcut
            # cannot wait, and a console that opens before the recorder has
            # written recorder.pid starts a second recorder of its own - and
            # then hands off to the watchdog (run_console.ps1) with the half of
            # the screen this camera takes, so a power cut brings both back side
            # by side and each reopens itself if it crashes.
            $placeArg = if ($place) { (' -Place {0}' -f $place) } else { '' }
            $ok = New-StartupShortcut "$CONSOLE_TASK$suffix" 'powershell.exe' `
                ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Settings "{1}"{2}' -f $consoleScript, $console.Settings, $placeArg) `
                $root "Opens the VMD console$forWhat 45 seconds after sign-in, and reopens it if it crashes."
            if ($ok) { $made += "$CONSOLE_TASK$suffix"; Write-Ok "`"$CONSOLE_TASK$suffix`" put in the Startup folder - the console opens 45 seconds later." }
        }
    }
    return $made
}

function Remove-StartupShortcuts {
    $removed = @()
    foreach ($shortcut in (Get-OurShortcuts)) {
        Remove-Item $shortcut.FullName -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path $shortcut.FullName)) { $removed += $shortcut.BaseName }
    }
    return $removed
}

# -----------------------------------------------------------------------------
#  Status
# -----------------------------------------------------------------------------
function Show-Status {
    Write-Host ""
    Write-Host "  Starting by itself after a restart" -ForegroundColor White
    Write-Host ""
    $startupNames = @(Get-OurShortcuts | ForEach-Object { $_.BaseName })
    foreach ($console in Get-Consoles) {
        foreach ($base in @($RECORDER_TASK, $CONSOLE_TASK)) {
            $name = "$base$($console.Suffix)"
            $task = Get-Task $name
            if ($task) {
                $state = $task.State
                Write-Host ("    {0,-22} on   ({1})" -f $name, $state) -ForegroundColor Green
            } elseif ($startupNames -contains $name) {
                # Named as what it is. "on" over a Startup shortcut and "on"
                # over a scheduled task are not the same promise, and the
                # difference is only visible after the failure it does not
                # cover.
                Write-Host ("    {0,-22} on   (Startup folder, not a scheduled task)" -f $name) -ForegroundColor Green
            } else {
                Write-Host ("    {0,-22} off" -f $name) -ForegroundColor Yellow
            }
        }
    }
    # Anything left over from a layout that has changed - a camera folder that
    # was renamed, or a second camera that has been taken away. Named, because a
    # scheduled task nobody knows about is a console that opens by itself every
    # morning for a reason nobody can find.
    $expected = @()
    foreach ($console in Get-Consoles) {
        $expected += "$RECORDER_TASK$($console.Suffix)"
        $expected += "$CONSOLE_TASK$($console.Suffix)"
    }
    $strays = @(Get-OurTasks | Where-Object { $expected -notcontains $_.TaskName })
    foreach ($stray in $strays) {
        Write-Host ("    {0,-22} on, for a camera that is not set up here" -f $stray.TaskName) -ForegroundColor Yellow
    }
    $strayShortcuts = @(Get-OurShortcuts | Where-Object { $expected -notcontains $_.BaseName })
    foreach ($stray in $strayShortcuts) {
        Write-Host ("    {0,-22} on in the Startup folder, for a camera that is not set up here" -f $stray.BaseName) -ForegroundColor Yellow
    }
    $strays += $strayShortcuts
    if ($strays.Count -gt 0) {
        Write-Host ""
        Write-Info "Run autostart-off.bat then autostart-on.bat to tidy those up."
    }
    $auto = (Get-ItemProperty -Path $WINLOGON -Name 'AutoAdminLogon' -ErrorAction SilentlyContinue).AutoAdminLogon
    if ($auto -eq '1') {
        $user = (Get-ItemProperty -Path $WINLOGON -Name 'DefaultUserName' -ErrorAction SilentlyContinue).DefaultUserName
        Write-Host ("    {0,-18} on   (signs in as {1})" -f 'Automatic sign-in', $user) -ForegroundColor Green
    } else {
        Write-Host ("    {0,-18} off  (somebody must sign in after a restart)" -f 'Automatic sign-in') -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "    Switch on:   autostart-on.bat" -ForegroundColor Gray
    Write-Host "    Switch off:  autostart-off.bat" -ForegroundColor Gray
    Write-Host ""
}

# -----------------------------------------------------------------------------
#  Power settings
# -----------------------------------------------------------------------------
# The lid setting, by GUID rather than by the SUB_BUTTONS/LIDACTION aliases.
# The aliases are only published on machines where Windows considers the
# setting visible, and a desktop - or a laptop with the setting hidden by
# policy - answers "invalid parameter" to the alias while accepting the GUID.
$LID_SUBGROUP = '4f971e89-eebd-4455-a8de-9e59040e7347'
$LID_ACTION   = '5ca83367-6e45-459f-a27b-476b1d01c936'

function Set-NeverSleep {
    <#
      Best effort, and it reports what it actually managed. A laptop that
      sleeps records nothing, but failing an install because powercfg refused
      would be worse - and claiming the lid is handled on a machine where the
      setting does not exist would be worse still.
    #>
    # Invoke-Quiet, not a redirection: under $ErrorActionPreference = 'Stop' the
    # stderr of a piped native command is a terminating error whatever it is
    # redirected to, so one powercfg that had something to say used to abandon
    # the two calls after it and the catch reported all three as not done. Each
    # exit code is read separately, which is what makes "it managed two of
    # three" sayable. See the note on Invoke-Quiet in scripts\_common.ps1.
    $sleepOff = $true
    foreach ($setting in @('standby-timeout-ac', 'hibernate-timeout-ac', 'disk-timeout-ac')) {
        if ((Invoke-Quiet 'powercfg' @('/change', $setting, '0')) -ne 0) { $sleepOff = $false }
    }
    # Closing the lid is the one that catches people out: it suspends the
    # machine, ffmpeg stops, and the perimeter is unwatched while the laptop
    # looks switched on. 0 is "do nothing".
    $lidOff = $false
    if ((Invoke-Quiet 'powercfg' @('/setacvalueindex', 'SCHEME_CURRENT', $LID_SUBGROUP, $LID_ACTION, '0')) -eq 0) {
        $lidOff = ((Invoke-Quiet 'powercfg' @('/setactive', 'SCHEME_CURRENT')) -eq 0)
    }
    return @{ Sleep = $sleepOff; Lid = $lidOff }
}

# -----------------------------------------------------------------------------
#  Install
# -----------------------------------------------------------------------------
function Install-Tasks {
    $exe = Join-Path $root 'VMD.exe'
    $recorderScript = Join-Path $PSScriptRoot 'recorder_service.ps1'

    # -ForUser is set only by the elevated retry below, and it is the whole
    # reason that retry can be trusted: the elevated copy may be running as a
    # different administrator, and tasks made for that account would trigger at
    # a sign-in that never happens on this laptop.
    $user = if ($ForUser) { $ForUser } else { "$env:USERDOMAIN\$env:USERNAME" }
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $refused = @()
    $refusal = ''

    # StartWhenAvailable so a task missed because the machine was off is run as
    # soon as it can be. No time limit, because the recorder is meant to run for
    # months and Task Scheduler's default is to kill a task after three days.
    # The battery settings matter on a laptop that will spend the occasional
    # hour on its own battery during a power cut: the default is to stop.
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew

    # Anything from a previous layout goes first. Registering the new set on top
    # of the old one would leave a "VMD Console" opening a console for a camera
    # that is now "VMD Console 250" - two windows for one camera, one of them
    # pointed at a settings file nobody uses.
    $consoles = @(Get-Consoles)
    $keeping = @()
    foreach ($console in $consoles) {
        $keeping += "$RECORDER_TASK$($console.Suffix)"
        $keeping += "$CONSOLE_TASK$($console.Suffix)"
    }
    foreach ($stray in @(Get-OurTasks | Where-Object { $keeping -notcontains $_.TaskName })) {
        # Not fatal. A task this account may not delete is somebody else's to
        # deal with, and stopping here would take the working half of the setup
        # with it.
        try {
            Unregister-ScheduledTask -TaskName $stray.TaskName -Confirm:$false
            Write-Info "Removed `"$($stray.TaskName)`", left over from an earlier setup."
        } catch {
            Write-Warn "Could not remove the leftover task `"$($stray.TaskName)`": $($_.Exception.Message)"
        }
    }

    $consoleScript = Join-Path $PSScriptRoot 'run_console.ps1'
    for ($ci = 0; $ci -lt $consoles.Count; $ci++) {
        $console = $consoles[$ci]
        $suffix = $console.Suffix
        $forWhat = if ($suffix) { " for camera$suffix" } else { "" }
        # Only split the screen when there is more than one camera - a lone
        # console opens full, exactly as the VMD button does (open_all_cameras.ps1).
        $place = if ($consoles.Count -ge 2) { Get-ConsolePlace $ci } else { '' }

        # --- the recorder -----------------------------------------------------
        # The settings file is passed even in the single-camera case, where it is
        # the same path the script would have worked out for itself. One code
        # path, and the task's own arguments say which camera it belongs to -
        # which is what somebody looking at Task Scheduler needs to read.
        $recorderAction = New-ScheduledTaskAction `
            -Execute 'powershell.exe' `
            -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Settings "{1}"' -f $recorderScript, $console.Settings) `
            -WorkingDirectory $root
        $recorderTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user

        # Caught rather than thrown, here and below. "Access is denied" from
        # Register-ScheduledTask is not a broken installation, it is a machine
        # that will not have tasks - and there is a second way to do this. What
        # is refused is collected and dealt with after the loop, so that a
        # refusal on the first camera does not skip the second.
        try {
            Register-ScheduledTask -TaskName "$RECORDER_TASK$suffix" -Force `
                -Description "Starts VMD recording$forWhat when this user signs in. Recording is the product; it must not wait for the console." `
                -Action $recorderAction -Trigger $recorderTrigger `
                -Principal $principal -Settings $settings | Out-Null
            Write-Ok "`"$RECORDER_TASK$suffix`" created - recording starts at sign-in."
        } catch {
            $refused += "$RECORDER_TASK$suffix"
            if (-not $refusal) { $refusal = $_.Exception.Message }
        }

        # --- the console ------------------------------------------------------
        # Through scripts\run_console.ps1 - the watchdog - not straight at
        # VMD.exe. A crash used to leave a black screen until the next sign-in;
        # the watchdog reopens the console on its own. And --place puts it on its
        # half of the screen, so after a reboot or a power cut both cameras come
        # back side by side, exactly as the VMD button opens them. The task keeps
        # its own 45-second delay for the recorder.pid race; the watchdog opens
        # at once, so nothing waits twice.
        if (Test-Path $exe) {
            $placeArg = if ($place) { (' -Place {0}' -f $place) } else { '' }
            $consoleAction = New-ScheduledTaskAction -Execute 'powershell.exe' `
                -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Settings "{1}"{2}' -f $consoleScript, $console.Settings, $placeArg) `
                -WorkingDirectory $root
            $consoleTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
            # 45 seconds after sign-in, so the recorder has written recorder.pid
            # and the console adopts it instead of starting a second one.
            $consoleTrigger.Delay = 'PT45S'
            try {
                Register-ScheduledTask -TaskName "$CONSOLE_TASK$suffix" -Force `
                    -Description "Opens the VMD console$forWhat 45 seconds after sign-in, once the recorder has claimed recorder.pid. Reopens itself if it crashes." `
                    -Action $consoleAction -Trigger $consoleTrigger `
                    -Principal $principal -Settings $settings | Out-Null
                Write-Ok "`"$CONSOLE_TASK$suffix`" created - the console opens 45 seconds later, and reopens itself if it crashes."
            } catch {
                $refused += "$CONSOLE_TASK$suffix"
                if (-not $refusal) { $refusal = $_.Exception.Message }
            }
        } else {
            Write-Warn "VMD.exe is not built yet, so only the recorder task was created."
            Write-Info "Run install.bat again once VMD.exe exists to add the console task."
        }
    }
    # --- what to do about anything Windows refused ---------------------------
    if ($refused.Count -gt 0) {
        Write-Host ""
        Write-Warn "Windows would not create $($refused -join ', ')."
        Write-Warn "  $refusal"

        # Step 2: the same registration, with administrator permission. Not
        # attempted when already elevated (it would change nothing) and not
        # attempted by the elevated copy itself (-NoElevate), which is what
        # stops this asking for a password in a loop.
        if (-not (Test-Admin) -and -not $NoElevate) {
            Write-Info "Trying again with administrator permission."
            Write-Info "Windows will ask once. Click Yes - it creates the two tasks and"
            Write-Info "does nothing else. The window it opens closes by itself."
            $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Install -Quiet -NoElevate -ForUser "{1}"' -f `
                $PSCommandPath, $user
            try {
                Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Verb RunAs -Wait
            } catch {
                Write-Info "The permission prompt was refused or could not be shown."
            }
            # Asked of Windows rather than assumed from the fact that
            # Start-Process returned: it returns whether or not the copy it
            # started managed anything at all.
            $still = @($refused | Where-Object { -not (Get-Task $_) })
            if ($still.Count -eq 0) {
                Write-Ok "Created with administrator permission: $($refused -join ', ')."
                $refused = @()
            } else {
                $refused = $still
            }
        }
    }

    # Step 3: the Startup folder, which asks nobody's permission.
    if ($refused.Count -gt 0) {
        Write-Host ""
        Write-Info "Falling back to the Startup folder, which needs no permission."
        $made = @(Install-StartupShortcuts)
        if ($made.Count -gt 0) {
            Write-Host ""
            Write-Info "This works - the recorder and the console start when $user signs"
            Write-Info "in - but it is the weaker of the two. A scheduled task restarts"
            Write-Info "after a crash, catches up a start it missed, and ignores the"
            Write-Info "battery rules; a shortcut does none of those."
            Write-Info "To get the tasks instead: right-click autostart-on.bat and choose"
            Write-Info "'Run as administrator'."
        } else {
            Write-Bad "Nothing could be set up to start by itself."
            Write-Bad "Recording will only run while somebody has opened the console."
        }
    } else {
        # Both mechanisms at once would start two recorders, and the second one
        # stands down noisily rather than silently. Whichever ran last wins, and
        # the tasks are what ran last here.
        $stale = @(Remove-StartupShortcuts)
        if ($stale.Count -gt 0) {
            Write-Info "Took the Startup-folder shortcuts away - the scheduled tasks do this now."
        }
    }

    if ($consoles.Count -gt 1) {
        Write-Host ""
        Write-Info "$($consoles.Count) consoles will open after a restart, one per camera."
        Write-Info "Which monitor each opens on is set in its own Settings tab."
    }

    $power = Set-NeverSleep
    if ($power.Sleep) { Write-Ok "This machine will not sleep, hibernate, or spin its disks down." }
    else { Write-Warn "Could not switch sleep off. Do it in Windows Settings, System, Power." }
    if ($power.Lid) { Write-Ok "Closing the lid now does nothing, so recording survives it." }
    else { Write-Warn "Could not change what closing the lid does - set it to 'Do nothing' in" }
    if (-not $power.Lid) { Write-Warn "Windows Settings, System, Power, if this machine has a lid." }

    if (-not $Quiet) {
        Write-Host ""
        Write-Info "After a restart, somebody still has to sign in to Windows before any"
        Write-Info "of this happens. To have Windows sign in by itself as well, run:"
        Write-Info "  autostart-on.bat -EnableAutoLogon"
        Write-Info "Read the top of scripts\autostart.ps1 first - it costs something."
    }
}

# -----------------------------------------------------------------------------
#  Remove
# -----------------------------------------------------------------------------
function Remove-Tasks {
    # Every task this script has ever made, whatever camera it was for. Asked of
    # Windows rather than worked out from the cameras that are set up now: off
    # has to mean off, including for a camera folder somebody has deleted.
    $ours = @(Get-OurTasks)
    $shortcuts = @(Get-OurShortcuts)
    if ($ours.Count -eq 0 -and $shortcuts.Count -eq 0) {
        Write-Info "Nothing was starting by itself, so there was nothing to take away."
    }
    foreach ($task in $ours) {
        try {
            Unregister-ScheduledTask -TaskName $task.TaskName -Confirm:$false
            Write-Ok "`"$($task.TaskName)`" removed."
        } catch {
            Write-Warn "Could not remove `"$($task.TaskName)`": $($_.Exception.Message)"
            Write-Warn "Right-click autostart-off.bat and choose 'Run as administrator'."
        }
    }
    # The other mechanism, taken away by the same switch. Off has to mean off
    # whichever way it was switched on.
    foreach ($name in (Remove-StartupShortcuts)) {
        Write-Ok "`"$name`" removed from the Startup folder."
    }
    Write-Info "The power settings were left as they are - change them in Windows"
    Write-Info "Settings under System, Power, if you want this laptop to sleep again."
}

# -----------------------------------------------------------------------------
#  Automatic sign-in
# -----------------------------------------------------------------------------
function Enable-AutoLogon {
    if (-not (Test-Admin)) {
        Write-Bad "Automatic sign-in changes a machine-wide setting, so this needs"
        Write-Bad "administrator permission. Right-click autostart-on.bat and"
        Write-Bad "choose 'Run as administrator'."
        return
    }
    Write-Host ""
    Write-Warn "Read this before answering."
    Write-Warn ""
    Write-Warn "  Windows will sign in as $env:USERNAME by itself at every start, with"
    Write-Warn "  no password. Anyone who opens this laptop gets a signed-in desktop."
    Write-Warn "  The password is stored in the registry in clear text, where any"
    Write-Warn "  administrator on this machine can read it."
    Write-Warn ""
    Write-Warn "  In exchange, a power cut costs nothing: the laptop comes back and"
    Write-Warn "  starts recording on its own, with nobody present."
    Write-Host ""
    $answer = Read-Host "  Type YES in capitals to switch automatic sign-in on"
    if ($answer -cne 'YES') {
        Write-Info "Left off. Nothing was changed."
        return
    }
    $secure = Read-Host "  Windows password for $env:USERNAME" -AsSecureString
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    if ([string]::IsNullOrEmpty($plain)) {
        Write-Bad "No password given. Nothing was changed."
        return
    }
    Set-ItemProperty -Path $WINLOGON -Name 'AutoAdminLogon'   -Value '1'            -Type String
    Set-ItemProperty -Path $WINLOGON -Name 'DefaultUserName'  -Value $env:USERNAME  -Type String
    Set-ItemProperty -Path $WINLOGON -Name 'DefaultDomainName' -Value $env:USERDOMAIN -Type String
    Set-ItemProperty -Path $WINLOGON -Name 'DefaultPassword'  -Value $plain         -Type String
    Write-Ok "Automatic sign-in is on. Restart to check it, before you rely on it."
    Write-Info "To undo it: autostart-off.bat, as administrator."
}

function Disable-AutoLogon {
    if (-not (Test-Admin)) {
        Write-Warn "Automatic sign-in was left alone: turning it off needs administrator"
        Write-Warn "permission. Right-click autostart-off.bat, 'Run as administrator'."
        return
    }
    Set-ItemProperty -Path $WINLOGON -Name 'AutoAdminLogon' -Value '0' -Type String
    Remove-ItemProperty -Path $WINLOGON -Name 'DefaultPassword' -ErrorAction SilentlyContinue
    Write-Ok "Automatic sign-in is off, and the stored password was deleted."
}

# -----------------------------------------------------------------------------

if ($Install)         { Install-Tasks }
if ($EnableAutoLogon) { Enable-AutoLogon }
if ($Remove)          { Remove-Tasks; Disable-AutoLogon }
if ($DisableAutoLogon -and -not $Remove) { Disable-AutoLogon }
if ($Status)          { Show-Status }
exit 0

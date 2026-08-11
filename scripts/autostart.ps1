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
# =============================================================================
param(
    [switch]$Install,
    [switch]$Remove,
    [switch]$Status,
    [switch]$Quiet,
    [switch]$EnableAutoLogon,
    [switch]$DisableAutoLogon
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$root = Get-ProjectRoot

$RECORDER_TASK = 'VMD Recorder'
$CONSOLE_TASK  = 'VMD Console'
$WINLOGON      = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'

if (-not ($Install -or $Remove -or $Status -or $EnableAutoLogon -or $DisableAutoLogon)) {
    $Status = $true
}

function Get-Task($name) {
    Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
}

# -----------------------------------------------------------------------------
#  Status
# -----------------------------------------------------------------------------
function Show-Status {
    Write-Host ""
    Write-Host "  Starting by itself after a restart" -ForegroundColor White
    Write-Host ""
    foreach ($name in @($RECORDER_TASK, $CONSOLE_TASK)) {
        $task = Get-Task $name
        if ($task) {
            $state = $task.State
            Write-Host ("    {0,-18} on   ({1})" -f $name, $state) -ForegroundColor Green
        } else {
            Write-Host ("    {0,-18} off" -f $name) -ForegroundColor Yellow
        }
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
    $sleepOff = $false
    $lidOff = $false
    try {
        powercfg /change standby-timeout-ac 0   2>&1 | Out-Null
        powercfg /change hibernate-timeout-ac 0 2>&1 | Out-Null
        powercfg /change disk-timeout-ac 0      2>&1 | Out-Null
        $sleepOff = $true
    } catch { }
    try {
        # Closing the lid is the one that catches people out: it suspends the
        # machine, ffmpeg stops, and the perimeter is unwatched while the laptop
        # looks switched on. 0 is "do nothing".
        powercfg /setacvalueindex SCHEME_CURRENT $LID_SUBGROUP $LID_ACTION 0 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            powercfg /setactive SCHEME_CURRENT 2>&1 | Out-Null
            $lidOff = $true
        }
    } catch { }
    return @{ Sleep = $sleepOff; Lid = $lidOff }
}

# -----------------------------------------------------------------------------
#  Install
# -----------------------------------------------------------------------------
function Install-Tasks {
    $exe = Join-Path $root 'VMD.exe'
    $recorderScript = Join-Path $PSScriptRoot 'recorder_service.ps1'

    $user = "$env:USERDOMAIN\$env:USERNAME"
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

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

    # --- the recorder ---------------------------------------------------------
    $recorderAction = New-ScheduledTaskAction `
        -Execute 'powershell.exe' `
        -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $recorderScript) `
        -WorkingDirectory $root
    $recorderTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user

    Register-ScheduledTask -TaskName $RECORDER_TASK -Force `
        -Description 'Starts VMD recording when this user signs in. Recording is the product; it must not wait for the console.' `
        -Action $recorderAction -Trigger $recorderTrigger `
        -Principal $principal -Settings $settings | Out-Null
    Write-Ok "`"$RECORDER_TASK`" created - recording starts at sign-in."

    # --- the console ----------------------------------------------------------
    if (Test-Path $exe) {
        $consoleAction = New-ScheduledTaskAction -Execute $exe -WorkingDirectory $root
        $consoleTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
        # 45 seconds after sign-in, so the recorder has written recorder.pid and
        # the console adopts it instead of starting a second one.
        $consoleTrigger.Delay = 'PT45S'
        Register-ScheduledTask -TaskName $CONSOLE_TASK -Force `
            -Description 'Opens the VMD console 45 seconds after sign-in, once the recorder has claimed recorder.pid.' `
            -Action $consoleAction -Trigger $consoleTrigger `
            -Principal $principal -Settings $settings | Out-Null
        Write-Ok "`"$CONSOLE_TASK`" created - the console opens 45 seconds later."
    } else {
        Write-Warn "VMD.exe is not built yet, so only the recorder task was created."
        Write-Info "Run install.bat again once VMD.exe exists to add the console task."
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
    foreach ($name in @($RECORDER_TASK, $CONSOLE_TASK)) {
        if (Get-Task $name) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Ok "`"$name`" removed."
        } else {
            Write-Info "`"$name`" was not there."
        }
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

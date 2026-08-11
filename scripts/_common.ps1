# =============================================================================
#  Shared by every script in this folder. Dot-sourced, never run on its own.
#
#  It exists because the installer, the offline installer and the autostart
#  script all have to agree about four things, and disagreeing about any of
#  them is how a deployment quietly breaks:
#
#    - where the project is,
#    - what bin\ is for and how it gets onto PATH,
#    - what "already installed" looks like,
#    - how to say all of that to somebody who has never used a terminal.
# =============================================================================

# PowerShell 7.4 turns a non-zero exit code from a native command into a
# terminating error when $ErrorActionPreference is 'Stop'. winget returns
# non-zero for entirely benign reasons - "no applicable upgrade found" among
# them - so on 7.x the scripts here would die with a red stack trace at the
# first package that was already present. install.bat launches Windows
# PowerShell 5.1, where this does not arise, but a right-click "Run with
# PowerShell" on the .ps1 uses whichever PowerShell is default, and on this
# machine that can be 7.x. Native exit codes are checked explicitly below
# wherever they matter, so switching this off loses nothing.
$PSNativeCommandUseErrorActionPreference = $false

# --- saying things -----------------------------------------------------------

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
function Write-Warn($text) { Write-Host "      $text" -ForegroundColor Yellow }

# --- where things are --------------------------------------------------------

function Get-ProjectRoot { Split-Path -Parent $PSScriptRoot }

function Test-Have($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Test-Admin {
    $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# --- PATH --------------------------------------------------------------------
#
# Everything this project downloads lives in bin\, and bin\ has to be on PATH
# for three separate reasons that all end in the same place:
#
#   uv      - VMD.exe is vmd\launcher.py frozen, and it looks for uv with
#             shutil.which(), which reads PATH and nothing else. On the offline
#             laptop there is no winget and no installer, so the copy of uv in
#             bin\ is the only one there will ever be. If bin\ is not on PATH,
#             double-clicking VMD.exe prints "uv is not installed" on a machine
#             where it plainly is.
#   ffmpeg  - vmd\storage\recorder.py runs the bare name "ffmpeg". PATH is how
#             a bare name is found.
#   go2rtc  - started by full path, so it does not need this. Listed so the
#             next person does not wonder why it is missing from the list.
#
# The registry is read and written raw, without expanding %VARIABLES%. The
# obvious [Environment]::GetEnvironmentVariable('Path','User') expands them on
# the way out, so writing the result back replaces every %USERPROFILE% in the
# user's PATH with a literal path - which works today and breaks the day the
# profile moves. Not our variable to damage.

function Get-UserPathRaw {
    $key = Get-Item 'HKCU:\Environment' -ErrorAction SilentlyContinue
    if (-not $key) { return '' }
    $value = $key.GetValue('Path', '', 'DoNotExpandEnvironmentNames')
    if ($null -eq $value) { return '' }
    return [string]$value
}

function Test-PathContains($pathValue, $entry) {
    $wanted = $entry.TrimEnd('\')
    foreach ($part in ($pathValue -split ';')) {
        if ($part.Trim().TrimEnd('\') -ieq $wanted) { return $true }
    }
    return $false
}

# Windows only re-reads the environment when it is told to. Explorer is what
# launches VMD.exe, and Explorer caches its environment from when it started,
# so a PATH written to the registry is invisible to a double-click until the
# user logs out - unless this broadcast is sent, which is exactly what setx
# does and the reason people reach for setx despite its 1024-character limit
# and its habit of mangling long PATHs.
function Publish-EnvironmentChange {
    if (-not ('Vmd.NativeEnv' -as [type])) {
        Add-Type -Namespace Vmd -Name NativeEnv -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true, CharSet = System.Runtime.InteropServices.CharSet.Auto)]
public static extern System.IntPtr SendMessageTimeout(System.IntPtr hWnd, uint Msg, System.IntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out System.UIntPtr lpdwResult);
'@ -ErrorAction SilentlyContinue
    }
    try {
        $HWND_BROADCAST = [IntPtr]0xffff
        $WM_SETTINGCHANGE = 0x1A
        $SMTO_ABORTIFHUNG = 0x0002
        [UIntPtr]$result = [UIntPtr]::Zero
        [void][Vmd.NativeEnv]::SendMessageTimeout(
            $HWND_BROADCAST, $WM_SETTINGCHANGE, [IntPtr]::Zero, 'Environment',
            $SMTO_ABORTIFHUNG, 3000, [ref]$result)
    } catch {
        # Cosmetic only: without it the new PATH is live in the next window
        # rather than immediately. Never worth failing an install over.
    }
}

function Add-BinToUserPath($binDir) {
    $current = Get-UserPathRaw
    if (Test-PathContains $current $binDir) {
        # Still put it into this process, because the registry says nothing
        # about the window we are standing in.
        if (-not (Test-PathContains $env:Path $binDir)) { $env:Path = "$binDir;$env:Path" }
        return $false
    }
    $updated = if ([string]::IsNullOrWhiteSpace($current)) { $binDir } else { "$($current.TrimEnd(';'));$binDir" }
    Set-ItemProperty -Path 'HKCU:\Environment' -Name 'Path' -Value $updated -Type ExpandString
    Publish-EnvironmentChange
    $env:Path = "$binDir;$env:Path"
    return $true
}

function Remove-BinFromUserPath($binDir) {
    $current = Get-UserPathRaw
    if (-not (Test-PathContains $current $binDir)) { return $false }
    $wanted = $binDir.TrimEnd('\')
    $kept = @($current -split ';' | Where-Object { $_.Trim() -and ($_.Trim().TrimEnd('\') -ine $wanted) })
    Set-ItemProperty -Path 'HKCU:\Environment' -Name 'Path' -Value ($kept -join ';') -Type ExpandString
    Publish-EnvironmentChange
    return $true
}

# --- the project's own Python ------------------------------------------------
#
# The interpreter lives inside the project, under bin\python\, rather than in
# the uv-managed store under %APPDATA%. That one decision is what makes the
# offline laptop possible: .venv\pyvenv.cfg records the absolute path of the
# interpreter it was built from, and a folder copied to another machine takes
# .venv with it but cannot take C:\Users\<somebody>\AppData\Roaming\uv\... with
# it. A venv whose `home` does not exist prints
#
#     No Python at '...\python.exe'
#
# and exits 103, which is what the previous offline recipe produced on arrival.
# With the interpreter under bin\python\ the whole thing travels together, and
# Repair-VenvPaths below fixes `home` if the folder lands somewhere new.

function Get-ProjectPythonDir($root) { Join-Path $root 'bin\python' }

function Find-ProjectPython($root) {
    $dir = Get-ProjectPythonDir $root
    if (-not (Test-Path $dir)) { return $null }
    $exe = Get-ChildItem -Path $dir -Filter 'python.exe' -Recurse -Depth 2 -ErrorAction SilentlyContinue |
        Sort-Object FullName | Select-Object -First 1
    if ($exe) { return $exe.FullName }
    return $null
}

function Get-VenvHome($root) {
    $cfg = Join-Path $root '.venv\pyvenv.cfg'
    if (-not (Test-Path $cfg)) { return $null }
    foreach ($line in (Get-Content $cfg -ErrorAction SilentlyContinue)) {
        if ($line -match '^\s*home\s*=\s*(.+?)\s*$') { return $Matches[1] }
    }
    return $null
}

# Two absolute paths are written into a built environment, and both of them are
# wrong the moment the folder is copied somewhere other than where it was
# built:
#
#   .venv\pyvenv.cfg                        home = <the interpreter>
#   .venv\Lib\site-packages\_editable_*.pth <the project directory>
#
# Rewriting them is a two-line repair and it removes the "both machines must
# use exactly C:\VMD" rule from the offline instructions - which was the kind
# of rule nobody reads until it has already been broken.
function Repair-VenvPaths($root) {
    $repaired = @()

    $python = Find-ProjectPython $root
    $cfg = Join-Path $root '.venv\pyvenv.cfg'
    if ($python -and (Test-Path $cfg)) {
        $wanted = Split-Path -Parent $python
        $home_ = Get-VenvHome $root
        if ($home_ -and ($home_.TrimEnd('\') -ine $wanted.TrimEnd('\'))) {
            $lines = Get-Content $cfg
            ($lines -replace '^\s*home\s*=\s*.*$', "home = $wanted") |
                Set-Content $cfg -Encoding ASCII
            $repaired += "the interpreter path in .venv\pyvenv.cfg"
        }
    }

    $sitePackages = Join-Path $root '.venv\Lib\site-packages'
    if (Test-Path $sitePackages) {
        foreach ($pth in (Get-ChildItem $sitePackages -Filter '_editable_impl_*.pth' -ErrorAction SilentlyContinue)) {
            $content = (Get-Content $pth.FullName -Raw -ErrorAction SilentlyContinue)
            if ($null -eq $content) { continue }
            if ($content.Trim().TrimEnd('\') -ine $root.TrimEnd('\')) {
                Set-Content -Path $pth.FullName -Value $root -Encoding ASCII -NoNewline
                $repaired += "the project path in $($pth.Name)"
            }
        }
    }

    return $repaired
}

# --- downloads ---------------------------------------------------------------

# $MinimumBytes is not optional politeness. get.videolan.org answers a request
# for an .exe with a 29 KB HTML page carrying a <meta refresh> to a mirror, so a
# download that "succeeded" can leave a web page named vlc-win64.exe on the USB
# drive - which is only discovered on the offline laptop, where it cannot be
# fixed. Anything short of the expected size is deleted rather than kept.
function Get-File($url, $destination, $label, $MinimumBytes = 0) {
    $previous = $ProgressPreference
    # Invoke-WebRequest's progress bar costs more time than the download on a
    # slow console, and redraws over the step list this installer just printed.
    $ProgressPreference = 'SilentlyContinue'
    try {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
        $response = Invoke-WebRequest -Uri $url -OutFile $destination -UseBasicParsing -PassThru

        # A download-page redirect rather than the file. Follow it once: the
        # mirror it names is the actual download, and picking the mirror is the
        # whole job that page exists to do.
        $type = ($response.Headers['Content-Type'] -join ' ')
        if ($type -match 'text/html') {
            $page = Get-Content $destination -Raw -ErrorAction SilentlyContinue
            if ($page -and ($page -match "http-equiv=`"refresh`"[^>]*URL='([^']+)'")) {
                $mirror = $Matches[1]
                Write-Info "  following the download page to $([Uri]$mirror | ForEach-Object { $_.Host })"
                Invoke-WebRequest -Uri $mirror -OutFile $destination -UseBasicParsing
            }
        }

        $size = (Get-Item $destination -ErrorAction SilentlyContinue).Length
        if ($MinimumBytes -gt 0 -and $size -lt $MinimumBytes) {
            Write-Bad "The download of $label came back too small ($size bytes) to be the real file."
            Remove-Item $destination -Force -ErrorAction SilentlyContinue
            return $false
        }
        return $true
    } catch {
        Write-Bad "Could not download $label"
        Write-Info "  $url"
        Write-Info "  $($_.Exception.Message)"
        Remove-Item $destination -Force -ErrorAction SilentlyContinue
        return $false
    } finally { $ProgressPreference = $previous }
}

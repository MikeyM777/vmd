# =============================================================================
#  Builds VMD.exe in the project root - one file, double-clicked, starts
#  the console.
#
#  The exe carries no application code at all - it is a launcher that runs the
#  project it sits in. That is what makes the Update button work: pulling new
#  code changes what the exe runs, with nothing to rebuild.
# =============================================================================
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
. (Join-Path $PSScriptRoot '_common.ps1')
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    Write-Host "Building VMD.exe - this takes a minute." -ForegroundColor Cyan

    # A running console holds its own exe open, and Windows refuses to overwrite
    # it. Closing it first turns an obscure "Access is denied" into nothing at all.
    #
    # Matched by full path, not by name. "VMD" is also the process name of
    # Visual Molecular Dynamics and of anything else a user happens to have
    # called VMD.exe; killing those by name would be someone else's bad day.
    #
    # taskkill /T rather than Stop-Process, because VMD.exe is a launcher: it
    # starts uv, uv starts python, and python is the window the operator is
    # looking at. Ending only the launcher used to leave that window open with
    # nothing owning it, so "just run it again" produced a second console on the
    # same directory - two consoles, two supervisors, one settings file and one
    # recording index. Killing the tree ends the whole chain.
    $target = Join-Path $root 'VMD.exe'
    $running = Get-Process VMD -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $target }
    if ($running) {
        Write-Host "Closing the console that is already running." -ForegroundColor Gray
        foreach ($process in $running) {
            # Invoke-Quiet rather than a redirection. Under
            # $ErrorActionPreference = 'Stop', the stderr of a piped native
            # command becomes a terminating NativeCommandError, and neither
            # 2>&1 nor 2>$null prevents it - so a taskkill against a process
            # that was already on its way out failed the whole build. See the
            # note on Invoke-Quiet in scripts\_common.ps1.
            $null = Invoke-Quiet 'taskkill' @('/F', '/T', '/PID', "$($process.Id)")
        }
        Start-Sleep -Milliseconds 400
        # taskkill walks the tree Windows knows about. If anything survived it -
        # a grandchild whose parent had already exited, which Windows no longer
        # relates to anyone - end it directly rather than leaving it to fight
        # the next console for the recording directory.
        Get-Process VMD -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -eq $target } |
            Stop-Process -Force -ErrorAction SilentlyContinue
    }

    # The project's own uv when it is here, PATH's only as a fallback. This
    # script is what offline_kit.ps1 tells the operator to run when VMD.exe is
    # stale or missing ("run scripts\build_exe.ps1, then this again"), and a
    # bare `uv` answers "not recognized" in any shell that has not picked up
    # bin\ on PATH yet - which is every shell opened before the installer ran.
    $uvExe = Join-Path $root 'bin\uv.exe'
    if (-not (Test-Path $uvExe)) { $uvExe = 'uv' }

    # Nothing is bundled: no --add-data, no application imports. The launcher is
    # stdlib only, which is also why this builds in seconds and stays small.
    #
    # --with pyinstaller fetches PyInstaller from the network, so this is a
    # connected-machine script by construction. It is never run on the offline
    # laptop; the exe travels there already built, inside the kit.
    & $uvExe run --with pyinstaller pyinstaller `
        --onefile `
        --name VMD `
        --distpath $root `
        --workpath build\pyinstaller `
        --specpath build `
        --console `
        (Join-Path $root 'vmd\launcher.py')

    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

    # It lands in the project root rather than dist\ on purpose: the exe keeps
    # settings.json beside itself, and the recording service reads settings.json
    # from the project directory. Same folder, same file, one set of settings.
    $exe = Join-Path $root 'VMD.exe'
    if (-not (Test-Path $exe)) { throw "PyInstaller finished but VMD.exe is not there." }
    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "Built VMD.exe ($mb MB)" -ForegroundColor Green
}
finally { Pop-Location }

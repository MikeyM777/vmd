# =============================================================================
#  Builds VMD.exe in the project root - one file, double-clicked, starts
#  the console.
#
#  The exe carries no application code at all - it is a launcher that runs the
#  project it sits in. That is what makes the Update button work: pulling new
#  code changes what the exe runs, with nothing to rebuild.
# =============================================================================
$ErrorActionPreference = 'Stop'
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
    $target = Join-Path $root 'VMD.exe'
    $running = Get-Process VMD -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $target }
    if ($running) {
        Write-Host "Closing the console that is already running." -ForegroundColor Gray
        $running | Stop-Process -Force
        Start-Sleep -Milliseconds 400
    }

    # Nothing is bundled: no --add-data, no application imports. The launcher is
    # stdlib only, which is also why this builds in seconds and stays small.
    uv run --with pyinstaller pyinstaller `
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

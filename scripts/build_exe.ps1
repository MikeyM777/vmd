# =============================================================================
#  Builds VMD.exe in the project root - one file, double-clicked, starts
#  the console.
#
#  Only the console goes in it: the web server, the settings model and the page.
#  The detector is deliberately left out. Bundling torch would turn a 15 MB file
#  into a 2 GB one to launch a web page, and the recorder and detector are run
#  from the project directory anyway.
# =============================================================================
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    Write-Host "Building VMD.exe - this takes a minute." -ForegroundColor Cyan

    # A running console holds its own exe open, and Windows refuses to overwrite
    # it. Closing it first turns an obscure "Access is denied" into nothing at all.
    $running = Get-Process VMD -ErrorAction SilentlyContinue
    if ($running) {
        Write-Host "Closing the console that is already running." -ForegroundColor Gray
        $running | Stop-Process -Force
        Start-Sleep -Milliseconds 400
    }

    # --add-data puts the console page inside the exe; PyInstaller unpacks it to
    # a temporary folder at run time, which is why server.py locates it relative
    # to its own file rather than the working directory.
    #
    # The source path must be absolute: --specpath makes relative paths resolve
    # against the spec directory, not against here, which silently looks for the
    # page inside build\ and fails.
    $static = Join-Path $root 'vmd\webui\static'

    uv run --with pyinstaller pyinstaller `
        --onefile `
        --name VMD `
        --distpath $root `
        --workpath build\pyinstaller `
        --specpath build `
        --add-data "$static;vmd/webui/static" `
        --console `
        (Join-Path $root 'vmd\webui\__main__.py')

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

@echo off
REM ============================================================
REM  VMD - make the offline kit ZIP, on a computer WITH internet.
REM
REM  This one file is all that is needed. Download it on its own
REM  from GitHub, double-click it, and it:
REM    1. downloads the latest VMD from GitHub (MikeyM777/vmd)
REM       into %USERPROFILE%\VMD-build\vmd
REM    2. runs OfflineSetup (scripts\offline_setup.ps1) there, which
REM       downloads Python, the libraries, uv, ffmpeg, go2rtc and VLC,
REM       checks the result, and zips it.
REM  The ZIP lands on the Desktop as VMD-offline.zip.
REM
REM  It does NOT turn on autostart or open the console on this
REM  computer. Run it again any time: bin\ and .venv are kept
REM  between runs, so the second time downloads far less.
REM ============================================================

set "VMD_BUILD=%USERPROFILE%\VMD-build\vmd"

echo.
echo   [1/2] Downloading the latest VMD from GitHub...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $dest=$env:VMD_BUILD; $zip=Join-Path $env:TEMP 'vmd-src.zip'; $tmp=Join-Path $env:TEMP 'vmd-src'; Invoke-WebRequest -UseBasicParsing 'https://codeload.github.com/MikeyM777/vmd/zip/refs/heads/master' -OutFile $zip; if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }; Expand-Archive $zip $tmp -Force; Remove-Item $zip -Force; $src=(Get-ChildItem $tmp -Directory | Select-Object -First 1).FullName; New-Item -ItemType Directory -Force $dest | Out-Null; & robocopy $src $dest /MIR /XD bin .venv /NFL /NDL /NJH /NJS /NP | Out-Null; if ($LASTEXITCODE -ge 8) { throw ('robocopy failed: ' + $LASTEXITCODE) }; Remove-Item $tmp -Recurse -Force; Write-Host ('        VMD ' + (Get-Content (Join-Path $dest 'VERSION') -Raw).Trim() + ' is in ' + $dest) -ForegroundColor Green"
if errorlevel 1 (
  echo.
  echo   FAILED: could not download VMD from GitHub. Is the internet connected?
  echo.
  pause
  exit /b 1
)

echo.
echo   [2/2] Building the offline kit. This takes a while the first time.
powershell -NoProfile -ExecutionPolicy Bypass -File "%VMD_BUILD%\scripts\offline_setup.ps1"
set RESULT=%ERRORLEVEL%

echo.
if %RESULT% NEQ 0 (
  echo   FAILED: the kit was not made. The red lines above say why.
) else (
  echo   DONE. The kit is VMD-offline.zip on the Desktop.
)
echo.
echo Press any key to close this window.
pause >nul
exit /b %RESULT%

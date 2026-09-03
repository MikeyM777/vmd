@echo off
REM ============================================================
REM  VMD - double-click this to start the console.
REM
REM  It opens the console window. Leave the small black window that appears
REM  open; closing it closes the console.
REM ============================================================

cd /d "%~dp0"

REM  bin\ first, always. It holds ffmpeg, which the recorder starts by bare
REM  name, and uv, which is the only uv on the offline laptop - there is no
REM  winget there and nothing was installed machine-wide. The installer also
REM  puts bin\ into the stored PATH so that double-clicking VMD.exe works, but
REM  a freshly copied folder may not have been through the installer yet, and
REM  Explorer caches the environment from when it started. Setting it here
REM  costs nothing and removes a whole class of "it works from the terminal
REM  but not when I double-click it".
set "PATH=%~dp0bin;%PATH%"

REM  Prefer the uv that travels with the project over any other on the machine:
REM  it is the version that wrote uv.lock, and on the offline laptop it is the
REM  only one.
set "UV=%~dp0bin\uv.exe"
if exist "%UV%" goto :run

set "UV=uv"
where uv >nul 2>&1
if errorlevel 1 (
  echo.
  echo   uv is not installed, so nothing can run yet.
  echo   Double-click install.bat first. It sets everything up.
  echo.
  echo   If this folder was copied from another machine, double-click
  echo   offline-install.bat instead. It needs no internet connection.
  echo.
  pause
  exit /b 1
)

:run

REM  --no-sync --frozen --offline: starting the console must never be a network
REM  operation. Plain "uv run" re-checks the lock and syncs, so any drift sends
REM  it to PyPI - and this laptop has no network, so that is a hang at the one
REM  moment nobody here can recover from. install.bat is where dependencies are
REM  allowed to change.
"%UV%" run --offline --frozen --no-sync python -m vmd.desktop %*
set RESULT=%ERRORLEVEL%

REM  Only wait for a keypress when a person is here to press it. Under the
REM  watchdog (scripts\run_console.ps1) VMD_SUPERVISED=1 is set, and a "pause"
REM  then would block Start-Process -Wait for ever - freezing the reopen loop on
REM  the first crash, which is the black screen the watchdog exists to prevent.
REM  Either way the real exit code is handed back so the watchdog can relaunch.
if not "%VMD_SUPERVISED%"=="1" (
  if %RESULT% NEQ 0 (
    echo.
    echo   The console stopped with an error. The message above says why.
    echo.
    pause
  )
)
exit /b %RESULT%

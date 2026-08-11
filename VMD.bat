@echo off
REM ============================================================
REM  VMD - double-click this to start the console.
REM
REM  It opens the console window. Leave the small black window that appears
REM  open; closing it closes the console.
REM ============================================================

cd /d "%~dp0"

where uv >nul 2>&1
if errorlevel 1 (
  echo.
  echo   uv is not installed, so nothing can run yet.
  echo   Double-click install.bat first. It sets everything up.
  echo.
  pause
  exit /b 1
)

REM  --no-sync --frozen --offline: starting the console must never be a network
REM  operation. Plain "uv run" re-checks the lock and syncs, so any drift sends
REM  it to PyPI - and this laptop has no network, so that is a hang at the one
REM  moment nobody here can recover from. install.bat is where dependencies are
REM  allowed to change.
uv run --offline --frozen --no-sync python -m vmd.desktop %*
set RESULT=%ERRORLEVEL%

if %RESULT% NEQ 0 (
  echo.
  echo   The console stopped with an error. The message above says why.
  echo.
  pause
)
exit /b %RESULT%

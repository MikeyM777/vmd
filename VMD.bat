@echo off
REM ============================================================
REM  VMD - double-click this to start the console.
REM
REM  It starts the local server and opens the browser at it.
REM  Leave the window that appears open; closing it stops the console.
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

uv run python -m vmd.webui %*
set RESULT=%ERRORLEVEL%

if %RESULT% NEQ 0 (
  echo.
  echo   The console stopped with an error. The message above says why.
  echo.
  pause
)
exit /b %RESULT%

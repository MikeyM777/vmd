@echo off
REM ============================================================
REM  Prepares the copy that goes to the laptop with no internet.
REM
REM  Run this on the machine that HAS internet, after install.bat has
REM  finished. It checks that everything the other laptop needs is inside
REM  this folder, fetches the one thing that cannot be (the VLC
REM  installer), and copies the whole folder to a USB drive.
REM
REM  This file is only the door. The work is in scripts\offline_kit.ps1.
REM ============================================================

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\offline_kit.ps1" %*
set RESULT=%ERRORLEVEL%

echo.
if %RESULT% NEQ 0 (
  echo Not ready yet. The messages above say what is missing.
)
echo Press any key to close this window.
pause >nul
exit /b %RESULT%

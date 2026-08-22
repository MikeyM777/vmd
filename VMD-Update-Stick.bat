@echo off
REM ============================================================
REM  Fills the VMD update stick from GitHub.
REM
REM  Copy this file and the scripts folder onto any Windows laptop
REM  that has internet. Nothing needs to be installed.
REM
REM  This file is only the door. The work is in scripts\update_stick.ps1.
REM ============================================================

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update_stick.ps1" -Gui %*
set RESULT=%ERRORLEVEL%

echo.
if %RESULT% NEQ 0 (
  echo The stick was not finished. The messages above say why.
)
echo Press any key to close this window.
pause >nul
exit /b %RESULT%

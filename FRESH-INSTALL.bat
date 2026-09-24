@echo off
REM ============================================================
REM  VMD - install from the beginning, on the OFFLINE PC.
REM
REM  Double-click this from the offline kit's VMD folder ON THE
REM  USB STICK. It stops VMD, moves the old C:\VMD aside (it is
REM  kept, not deleted), copies this kit to C:\VMD, keeps the
REM  camera setup, and runs the offline installer.
REM
REM  This file is only the door. The work is in
REM  scripts\fresh_install.ps1.
REM ============================================================

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\fresh_install.ps1" %*
set RESULT=%ERRORLEVEL%

echo.
echo Press any key to close this window.
pause >nul
exit /b %RESULT%

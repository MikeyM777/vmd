@echo off
REM ============================================================
REM  VMD - double-click installer.
REM
REM  This file is only the door. The work is in scripts\install.ps1,
REM  because batch cannot do error handling worth relying on.
REM ============================================================

REM  Start in the project folder rather than wherever this was launched from.
REM  The scripts resolve their own paths from their own location, so this is
REM  belt and braces - but a shortcut, a scheduled task or a right-click "Run
REM  as administrator" can all hand this file a working directory of
REM  C:\Windows\System32, and nothing downstream should have to survive that.
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*
set RESULT=%ERRORLEVEL%

echo.
if %RESULT% NEQ 0 (
  echo Install did not finish. The messages above say why.
)
echo Press any key to close this window.
pause >nul
exit /b %RESULT%

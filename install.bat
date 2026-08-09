@echo off
REM ============================================================
REM  VMD - double-click installer.
REM
REM  This file is only the door. The work is in scripts\install.ps1,
REM  because batch cannot do error handling worth relying on.
REM ============================================================

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*
set RESULT=%ERRORLEVEL%

echo.
if %RESULT% NEQ 0 (
  echo Install did not finish. The messages above say why.
)
echo Press any key to close this window.
pause >nul
exit /b %RESULT%

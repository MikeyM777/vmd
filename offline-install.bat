@echo off
REM ============================================================
REM  Installs VMD on the laptop that has no internet.
REM
REM  Run this on the OFFLINE laptop, after copying the VMD folder from
REM  the USB drive to C:\VMD. Do not run install.bat on that machine -
REM  it needs a connection and this one does not.
REM ============================================================

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0offline_install.ps1" %*
set RESULT=%ERRORLEVEL%

echo.
if %RESULT% NEQ 0 (
  echo Install did not finish. The messages above say why.
)
echo Press any key to close this window.
pause >nul
exit /b %RESULT%

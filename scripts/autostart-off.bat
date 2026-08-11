@echo off
REM ============================================================
REM  Switch off: nothing starts by itself any more. From then on the
REM  console and the recording only run when somebody double-clicks
REM  VMD.exe.
REM
REM  Double-click this file. It does not stop anything that is running
REM  now, and it does not delete a single recording.
REM
REM  If automatic Windows sign-in was switched on, right-click this file
REM  and choose "Run as administrator" so it can switch that off too and
REM  delete the stored password.
REM ============================================================

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0autostart.ps1" -Remove %*

echo.
echo Press any key to close this window.
pause >nul

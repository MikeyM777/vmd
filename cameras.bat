@echo off
REM ============================================================
REM  VMD - set up a console for each camera.
REM
REM  There are two cameras, one per street, and each one gets its
REM  own console: its own settings, its own recordings, its own
REM  window on its own screen.
REM
REM  Double-click this, answer three questions, and run it again
REM  for the second camera. Run it with nothing set up and it
REM  lists what is there.
REM ============================================================

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\cameras.ps1" %*

echo.
pause

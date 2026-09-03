@echo off
REM ============================================================
REM  SET UP VMD - double-click this one file.
REM
REM  It does everything: sets up both cameras, puts a "VMD"
REM  button on the desktop that opens them side by side, offers
REM  to start them by themselves when the PC turns on, and
REM  checks it all works.
REM
REM  Answer the few questions it asks. That is the whole of it.
REM
REM  The work is in scripts\setup.ps1. This file only opens it.
REM ============================================================

cd /d "%~dp0"

REM  bin\ first so uv and the tools are found even on a folder that
REM  was just copied and has not been through the installer yet.
set "PATH=%~dp0bin;%PATH%"

REM  Run in THIS window (not hidden) so the questions can be answered.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"

REM  A safety net only: setup.ps1 keeps the window open itself, but if it
REM  could not start at all this is what lets the message be read.
if errorlevel 1 (
  echo.
  echo   Setup did not finish. The message above says why.
  echo.
  pause
)

@echo off
REM ============================================================
REM  VMD - prepare the copy that goes to the offline computer.
REM
REM  RUN THIS ON A COMPUTER THAT HAS INTERNET.
REM
REM  It downloads everything VMD needs, checks that what it has
REM  built will actually run somewhere else, and puts it all in
REM  one zip file on your desktop.
REM
REM  Take that one file to the offline computer on a USB stick,
REM  unzip it there, and read START HERE.txt inside.
REM
REM  It needs about 6 GB of free space and takes fifteen minutes
REM  or so the first time. It is safe to run again.
REM ============================================================

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\offline_setup.ps1" %*

echo.
pause

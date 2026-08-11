@echo off
REM ============================================================
REM  Switch on: the recorder and the console come back by themselves
REM  after the laptop restarts.
REM
REM  Double-click this file. It is safe to run twice.
REM
REM  To also have Windows sign itself in after a restart - which is what
REM  makes a power cut cost nothing - right-click this file, choose
REM  "Run as administrator", and it will ask you about it. Read what it
REM  says before answering; it gives something up in exchange.
REM ============================================================

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0autostart.ps1" -Install -Status %*

echo.
echo Press any key to close this window.
pause >nul

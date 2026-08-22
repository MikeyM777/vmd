@echo off
REM ============================================================
REM  Fills the VMD update stick from GitHub.
REM
REM  Copy this file and the scripts folder onto any Windows laptop
REM  that has internet. Nothing needs to be installed.
REM
REM  This file is only the door. The work is in scripts\update_stick.ps1.
REM
REM  It launches the window and gets out of the way. The old version ran
REM  PowerShell in this console and then "pause"d, which left a black window
REM  sitting behind the GUI saying "Press any key to close" - and the operator
REM  was told "no cmd". So instead:
REM
REM    start ""      - hand the PowerShell off to Windows and do not wait for it,
REM                    so this .bat returns at once and its console closes. The
REM                    "" is start's title argument, which must come first or the
REM                    quoted path after it would be read as the title instead.
REM    -WindowStyle Hidden
REM                  - the launched PowerShell keeps no console of its own; the
REM                    only window that appears is the GUI it shows.
REM    (no pause)    - nothing to press, nothing left behind.
REM
REM  A brief flash of THIS console as Windows starts the .bat is unavoidable - it
REM  is how a double-clicked .bat runs at all - but it closes on its own the
REM  instant the line below has launched the window. A .vbs launcher could remove
REM  even that flash, but it would be a third file to copy to the laptop, and the
REM  whole design is "copy two things: this .bat and the scripts folder". One
REM  flash is a fair price for keeping it to two.
REM ============================================================

cd /d "%~dp0"

start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0scripts\update_stick.ps1" -Gui

exit /b 0

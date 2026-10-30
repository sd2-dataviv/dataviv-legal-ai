@echo off
REM Starts the API and opens the test console at http://127.0.0.1:8010/ui (next free port if 8010 is busy). Close the window or press Ctrl+C to stop.
cd /d "%~dp0..\.."
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\console.ps1
pause

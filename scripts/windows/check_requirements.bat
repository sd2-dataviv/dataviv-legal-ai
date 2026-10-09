@echo off
REM Launches the read-only requirements check (check_requirements.ps1) and keeps the window open.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0check_requirements.ps1"
echo.
echo You can close this window.
pause

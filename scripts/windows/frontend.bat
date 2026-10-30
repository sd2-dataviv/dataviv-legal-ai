@echo off
REM Installs and builds the React test console. Logs go to logs\.
cd /d "%~dp0..\.."
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\frontend.ps1
pause

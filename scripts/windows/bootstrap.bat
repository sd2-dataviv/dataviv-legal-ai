@echo off
REM First run: installs Python packages, prepares PostgreSQL, runs lint, type checks and tests. Logs go to logs\.
cd /d "%~dp0..\.."
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\bootstrap.ps1
pause

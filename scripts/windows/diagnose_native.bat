@echo off
cd /d "%~dp0..\.."
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\diagnose_native.ps1
timeout /t 10

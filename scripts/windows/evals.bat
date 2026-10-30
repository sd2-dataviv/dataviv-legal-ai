@echo off
REM Runs the benchmark with the model in backend\.env (Groq). Takes 5-10 minutes. Report goes to logs\evals\.
cd /d "%~dp0..\.."
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\evals.ps1 %*
pause

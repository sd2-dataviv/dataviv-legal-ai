@echo off
REM Writes the latest agent runs, with every step and error, to logs\40_agents.log
cd /d "%~dp0..\.."
.venv\Scripts\python.exe scripts\agent_report.py %1 > logs\40_agents.log 2>&1
type logs\40_agents.log
echo.
echo Saved to logs\40_agents.log
pause

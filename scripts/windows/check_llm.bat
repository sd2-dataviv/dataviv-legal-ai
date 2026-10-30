@echo off
REM Makes one tiny call to the model provider (Groq) to check the key and model. Never prints the key.
cd /d "%~dp0..\.."
if not exist logs mkdir logs
".venv\Scripts\python.exe" scripts\check_llm.py > logs\20_check_llm.log 2>&1
type logs\20_check_llm.log
pause

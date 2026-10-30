@echo off
REM Extracts dataviv-ai-os.zip into this folder (local only, no downloads).
cd /d "%~dp0"
echo Extracting project files...
tar -xf dataviv-ai-os.zip
if errorlevel 1 (echo Extraction failed. & pause & exit /b 1)
del dataviv-ai-os.zip
echo Done. Project extracted to %~dp0
timeout /t 5

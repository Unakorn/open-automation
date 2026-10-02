@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "outputs\FLP Automation Connector.py"
) else (
  python "outputs\FLP Automation Connector.py"
)
if errorlevel 1 pause

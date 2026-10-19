@echo off
cd /d "%~dp0"
if not exist "backend\.venv\Scripts\pythonw.exe" (
  echo Install backend/requirements-desktop.txt and build the frontend first.
  pause
  exit /b 1
)
start "" "backend\.venv\Scripts\pythonw.exe" "backend\desktop.py"

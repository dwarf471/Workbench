@echo off
cd /d "%~dp0backend"
if not exist ".venv\Scripts\python.exe" (
  echo Run start.cmd first to initialize the workbench.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m app.backup
pause

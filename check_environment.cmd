@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "taskPython=.venv\Scripts\python.exe"
if not exist "%taskPython%" set "taskPython=.venv-pytorch\Scripts\python.exe"
if not exist "%taskPython%" (
  echo Project Python environment is missing. Read docs\SETTINGS_AND_TRANSFER_GUIDE.md.
  pause
  exit /b 1
)
"%taskPython%" -X utf8 check_desktop.py
echo.
pause


@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
if errorlevel 1 exit /b 1
set "taskPython=%~dp0.venv\Scripts\python.exe"
if not exist "%taskPython%" set "taskPython=%~dp0.venv-pytorch\Scripts\python.exe"
if not exist "%taskPython%" (
  echo Project Python environment is missing. Read docs\SETTINGS_AND_TRANSFER_GUIDE.md and run setup_desktop.ps1.
  pause
  exit /b 1
)
if exist "%~dp0.cache\qwen-local-runtime.json" (
  echo Preparing local AI. First load may take a moment...
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_local_ai.ps1"
  if errorlevel 1 echo Local AI is not ready. You can retry in AI settings.
)
"%taskPython%" -X utf8 "%~dp0desktop.py" %*
set "taskExit=%ERRORLEVEL%"
if not "%taskExit%"=="0" pause
exit /b %taskExit%

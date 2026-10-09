@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv-pytorch\Scripts\python.exe" (
  echo Project Python environment is missing.
  pause
  exit /b 1
)
".venv-pytorch\Scripts\python.exe" desktop.py
if errorlevel 1 pause
